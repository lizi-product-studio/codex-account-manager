"""Codex 桌面版进程控制（支持 Windows & macOS：检测 / 关闭 / 启动 / 读取实时额度）。"""
import glob
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    CREATE_NO_WINDOW = 0x08000000
    _TH32CS_SNAPPROCESS = 0x00000002
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    _k32 = ctypes.windll.kernel32

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    _FILTER = r"""
function Get-CodexProcs {
  $r = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex'
  Get-Process | Where-Object {
    $p = $null; try { $p = $_.Path } catch {}
    $p -and ($p -like '*\WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe' -or $p -like "$r\*")
  }
}
"""

    def _ps(script, timeout=40):
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
        return r.stdout.decode("utf-8", "replace").strip()


def _parse_mac_ps_output(output, self_pid=None):
    """解析 macOS `ps -axo pid=,command=` 输出，返回 Codex 相关进程列表。"""
    if self_pid is None:
        self_pid = os.getpid()
    home = os.path.expanduser("~")
    support_openai = os.path.join(home, "Library", "Application Support", "OpenAI", "Codex")
    support_codex = os.path.join(home, "Library", "Application Support", "Codex")
    out = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == self_pid:
            continue
        cmd = parts[1]
        if "codex-account-manager" in cmd:
            continue
        try:
            first_token = shlex.split(cmd)[0]
        except ValueError:
            first_token = cmd.split()[0]
        base = os.path.basename(first_token)
        matched = (
            "/Codex.app/" in cmd
            or "/Library/Application Support/OpenAI/Codex/" in cmd
            or "/Library/Application Support/Codex/" in cmd
            or support_openai in cmd
            or support_codex in cmd
            or base == "codex"
        )
        if matched:
            out.append({
                "Id": pid,
                "Name": base or "Codex",
                "Main": "/Codex.app/Contents/MacOS/" in cmd,
            })
    return out


def list_processes():
    """枚举本机 Codex 桌面版及其本地运行时子进程。"""
    if IS_WIN:
        local_root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex").lower() + "\\"
        out = []
        snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == wintypes.HANDLE(-1).value:
            return out
        try:
            pe = _PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(pe)
            buf = ctypes.create_unicode_buffer(1024)
            if _k32.Process32FirstW(snap, ctypes.byref(pe)):
                while True:
                    pid = pe.th32ProcessID
                    if pid > 4:
                        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                        if h:
                            sz = wintypes.DWORD(1024)
                            ok = _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz))
                            _k32.CloseHandle(h)
                            if ok:
                                pl = buf.value.lower()
                                if (("\\windowsapps\\openai.codex_" in pl and pl.endswith("\\app\\chatgpt.exe"))
                                        or pl.startswith(local_root)):
                                    out.append({"Id": pid, "Name": os.path.splitext(pe.szExeFile)[0]})
                    if not _k32.Process32NextW(snap, ctypes.byref(pe)):
                        break
        finally:
            _k32.CloseHandle(snap)
        return out
    elif IS_MAC:
        try:
            r = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            return _parse_mac_ps_output(r.stdout)
        except Exception:
            return []
    return []


def is_running():
    try:
        if IS_WIN:
            return any(p.get("Name") == "ChatGPT" for p in list_processes())
        elif IS_MAC:
            return any(p.get("Main") or p.get("Name") in ("Codex", "ChatGPT") for p in list_processes())
        return False
    except Exception:
        return False


def stop():
    """关闭 Codex 桌面版及其全部子进程。返回 (ok: bool, remaining_names: str)。"""
    if IS_WIN:
        if not list_processes():
            return True, ""
        script = _FILTER + r"""
$ps = @(Get-CodexProcs)
if ($ps.Count -eq 0) { 'OK'; exit }

# 1. 先尝试优雅关闭主窗口（等 2.5 秒，若只是缩到托盘则进入强关）
$ps | Where-Object { $_.Name -eq 'ChatGPT' -and $_.MainWindowHandle -ne [IntPtr]::Zero } |
  ForEach-Object { try { [void]$_.CloseMainWindow() } catch {} }
$d = (Get-Date).AddMilliseconds(2500)
while ((Get-Date) -lt $d -and @(Get-CodexProcs).Count) { Start-Sleep -Milliseconds 250 }

# 2. 循环强关（先杀父进程 ChatGPT.exe，防止 Electron 自动拉起子进程）
$d = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $d) {
  $left = @(Get-CodexProcs)
  if ($left.Count -eq 0) { 'OK'; exit }
  $parents = @($left | Where-Object { $_.Name -eq 'ChatGPT' })
  $others  = @($left | Where-Object { $_.Name -ne 'ChatGPT' })
  foreach ($p in ($parents + $others)) {
    try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
  if ($parents.Count -gt 0) {
    try { & taskkill.exe /F /T /IM ChatGPT.exe 2>$null | Out-Null } catch {}
  }
  Start-Sleep -Milliseconds 400
}
$rem = @(Get-CodexProcs)
if ($rem.Count -eq 0) { 'OK' } else { 'FAIL:' + (($rem | Select-Object -ExpandProperty Name -Unique) -join ',') }
"""
        out = _ps(script, timeout=40)
        if out.endswith("OK"):
            return True, ""
        rem = out.split("FAIL:", 1)[1].strip() if "FAIL:" in out else ""
        return False, rem
    elif IS_MAC:
        if not list_processes():
            return True, ""
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Codex" to quit'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
        except Exception:
            pass
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not list_processes():
                return True, ""
            time.sleep(0.25)

        retry_start = time.monotonic()
        retry_deadline = retry_start + 8.0
        while time.monotonic() < retry_deadline:
            rem = list_processes()
            if not rem:
                return True, ""
            rem.sort(key=lambda p: (0 if p.get("Main") else 1))
            sig = signal.SIGTERM if (time.monotonic() - retry_start) < 2.5 else signal.SIGKILL
            for p in rem:
                try:
                    os.kill(p["Id"], sig)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            time.sleep(0.35)

        rem = list_processes()
        if not rem:
            return True, ""
        return False, ",".join(sorted({p.get("Name") or "Codex" for p in rem}))
    return True, ""


_pfn_cache = None


def package_family():
    if not IS_WIN:
        return ""
    global _pfn_cache
    if _pfn_cache is None:
        _pfn_cache = _ps("(Get-AppxPackage -Name 'OpenAI.Codex' | Select-Object -First 1).PackageFamilyName",
                         timeout=20) or ""
    return _pfn_cache


def start():
    if IS_WIN:
        pfn = package_family()
        if not pfn:
            return False
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\%s!App" % pfn], creationflags=CREATE_NO_WINDOW)
        return True
    elif IS_MAC:
        for app_path in ("/Applications/Codex.app", os.path.expanduser("~/Applications/Codex.app")):
            if os.path.isdir(app_path):
                r = subprocess.run(["open", app_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return r.returncode == 0
        r = subprocess.run(["open", "-a", "Codex"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    return False


def find_codex_exe():
    """定位本机安装的官方 codex 可执行文件（优先 Codex 桌面版自带版本）。"""
    if IS_WIN:
        pat = os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex", "bin", "*", "codex.exe")
        cands = sorted(glob.glob(pat), key=os.path.getmtime, reverse=True)
        if cands:
            return cands[0]
        return shutil.which("codex")
    elif IS_MAC:
        home = os.path.expanduser("~")
        cands = [
            "/Applications/Codex.app/Contents/Resources/codex",
            os.path.join(home, "Applications", "Codex.app", "Contents", "Resources", "codex"),
        ]
        for sub in (
            os.path.join(home, "Library", "Application Support", "OpenAI", "Codex", "bin", "*", "codex"),
            os.path.join(home, "Library", "Application Support", "Codex", "bin", "*", "codex"),
        ):
            try:
                cands.extend(sorted(glob.glob(sub), key=os.path.getmtime, reverse=True))
            except OSError:
                pass
        cands.extend(["/opt/homebrew/bin/codex", "/usr/local/bin/codex"])
        for p in cands:
            if p and os.path.isfile(p) and os.access(p, os.X_OK):
                return p
        return shutil.which("codex")
    return shutil.which("codex")


def read_live_rate_limits(timeout=12):
    """通过官方 codex app-server 的标准 RPC（account/rateLimits/read）读取当前登录账号的实时额度。

    只查询当前 ~/.codex/auth.json 对应的唯一账号，绝不碰其他账号凭证。
    返回 (account_id: str, rate_limits_list: list[dict])，失败返回 (None, None)。
    """
    exe = find_codex_exe()
    if not exe:
        return None, None
    popen_kwargs = dict(
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace",
    )
    if IS_WIN:
        popen_kwargs["creationflags"] = CREATE_NO_WINDOW
    proc = subprocess.Popen([exe, "app-server"], **popen_kwargs)
    timer = threading.Timer(timeout, lambda: proc.kill() if proc.poll() is None else None)
    timer.start()
    try:
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"clientInfo": {"name": "codex-account-manager", "version": "1.2.0"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}},
        ]
        for r in reqs:
            proc.stdin.write(json.dumps(r) + "\n")
        proc.stdin.flush()

        for _ in range(40):
            line = proc.stdout.readline()
            if not line:
                break
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("id") != 2:
                continue
            res = obj.get("result")
            if not isinstance(res, dict):
                return None, None
            by_id = res.get("rateLimitsByLimitId")
            if isinstance(by_id, dict) and by_id:
                items = [v for v in by_id.values() if isinstance(v, dict)]
            elif isinstance(res.get("rateLimits"), dict):
                items = [res["rateLimits"]]
            else:
                items = []
            return res.get("accountId") or "", items
        return None, None
    except Exception:
        return None, None
    finally:
        timer.cancel()
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
