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
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x00100000

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
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.TerminateProcess.restype = wintypes.BOOL
    _k32.WaitForMultipleObjects.argtypes = [
        wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD,
    ]
    _k32.WaitForMultipleObjects.restype = wintypes.DWORD
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


def _win_terminate_and_wait(wait_pids, bg_pids=(), timeout_ms=280):
    """终止全部相关进程，但只阻塞等待关键进程（ChatGPT / codex）句柄退出，辅助进程异步收尾。"""
    handles = []
    try:
        for pid in bg_pids:
            h = _k32.OpenProcess(_PROCESS_TERMINATE, False, pid)
            if h:
                _k32.TerminateProcess(h, 0)
                _k32.CloseHandle(h)
        for pid in wait_pids:
            h = _k32.OpenProcess(_PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
            if h:
                _k32.TerminateProcess(h, 0)
                handles.append(h)
            else:
                h2 = _k32.OpenProcess(_PROCESS_TERMINATE, False, pid)
                if h2:
                    _k32.TerminateProcess(h2, 0)
                    _k32.CloseHandle(h2)
        if handles:
            # WaitForMultipleObjects 单次最多等待 64 个内核句柄，关键进程退出的那 1ms 立即唤醒返回
            batch = handles[:64]
            arr = (wintypes.HANDLE * len(batch))(*batch)
            _k32.WaitForMultipleObjects(len(batch), arr, True, timeout_ms)
    finally:
        for h in handles:
            _k32.CloseHandle(h)


def stop():
    """关闭 Codex 桌面版及其全部子进程。返回 (ok: bool, remaining_names: str)。"""
    if IS_WIN:
        rem = list_processes()
        if not rem:
            return True, ""
        # 终止全部相关进程，但只等待核心进程（ChatGPT / codex）退出即立刻放行，不卡在 node_repl 等辅助进程回收
        start_t = time.monotonic()
        deadline = start_t + 4.0
        taskkill_tried = False
        while True:
            core = [p["Id"] for p in rem if p.get("Name") in ("ChatGPT", "codex")]
            helpers = [p["Id"] for p in rem if p.get("Name") not in ("ChatGPT", "codex")]
            if not core:
                # 关键进程已全部退出，给残余辅助进程发一次异步终止即可返回
                if helpers:
                    _win_terminate_and_wait([], bg_pids=helpers, timeout_ms=0)
                return True, ""
            _win_terminate_and_wait(core, bg_pids=helpers, timeout_ms=260)
            rem = list_processes()
            core_rem = [p for p in rem if p.get("Name") in ("ChatGPT", "codex")]
            if not core_rem:
                if rem:
                    _win_terminate_and_wait([], bg_pids=[p["Id"] for p in rem], timeout_ms=0)
                return True, ""
            if time.monotonic() >= deadline:
                break
            if not taskkill_tried and any(p.get("Name") == "ChatGPT" for p in core_rem) and (time.monotonic() - start_t) >= 0.6:
                taskkill_tried = True
                try:
                    subprocess.run(
                        ["taskkill.exe", "/F", "/T", "/IM", "ChatGPT.exe"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=4, creationflags=CREATE_NO_WINDOW,
                    )
                except Exception:
                    pass
                rem = list_processes()
                if not any(p.get("Name") in ("ChatGPT", "codex") for p in rem):
                    return True, ""
            time.sleep(0.01)

        core_rem = [p for p in rem if p.get("Name") in ("ChatGPT", "codex")]
        if not core_rem:
            return True, ""
        return False, ",".join(sorted({p.get("Name") or "Codex" for p in core_rem}))
    elif IS_MAC:
        rem = list_processes()
        if not rem:
            return True, ""
        # 先发 SIGTERM（主进程优先，25ms 极速轮询），120ms 后对未退出的子进程立即升级 SIGKILL
        retry_start = time.monotonic()
        retry_deadline = retry_start + 4.0
        sent_term = set()
        while True:
            rem.sort(key=lambda p: (0 if p.get("Main") else 1))
            elapsed = time.monotonic() - retry_start
            if elapsed < 0.12:
                for p in rem:
                    pid = p["Id"]
                    if pid not in sent_term:
                        sent_term.add(pid)
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
            else:
                for p in rem:
                    try:
                        os.kill(p["Id"], signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
            time.sleep(0.025)
            rem = list_processes()
            if not rem or time.monotonic() >= retry_deadline:
                break

        if not rem:
            return True, ""
        return False, ",".join(sorted({p.get("Name") or "Codex" for p in rem}))
    return True, ""


_pfn_cache = None
_alias_cache = None


def package_family():
    if not IS_WIN:
        return ""
    global _pfn_cache
    if _pfn_cache is None:
        # 优先直接读 %LOCALAPPDATA%\Packages\OpenAI.Codex_*（<1ms），找不到再回退 PowerShell
        pkg_pat = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "OpenAI.Codex_*")
        hits = sorted(glob.glob(pkg_pat), key=os.path.getmtime, reverse=True)
        if hits:
            _pfn_cache = os.path.basename(hits[0])
        else:
            _pfn_cache = _ps("(Get-AppxPackage -Name 'OpenAI.Codex' | Select-Object -First 1).PackageFamilyName",
                             timeout=20) or ""
    return _pfn_cache


def _win_app_alias():
    """查找 MSIX 原生 AppExecutionAlias（chatgpt-desktop.exe），比 shell:AppsFolder 唤起快得多。"""
    global _alias_cache
    if _alias_cache is not None:
        return _alias_cache
    winapps = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps")
    pfn = package_family()
    cands = []
    if pfn:
        cands.append(os.path.join(winapps, pfn, "chatgpt-desktop.exe"))
    cands.append(os.path.join(winapps, "chatgpt-desktop.exe"))
    for c in cands:
        if c and os.path.exists(c):
            _alias_cache = c
            return c
    _alias_cache = ""
    return ""


def start():
    if IS_WIN:
        # 1. 优先直接非阻塞启动 MSIX 原生别名 chatgpt-desktop.exe（<5ms 返回，绕开 Explorer/ShellExecute 排队）
        alias = _win_app_alias()
        if alias:
            try:
                flags = CREATE_NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                subprocess.Popen(
                    [alias],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                    close_fds=True, creationflags=flags,
                )
                return True
            except OSError:
                pass
        # 2. 回退：异步通过 shell:AppsFolder 唤起，避免同步阻塞切号返回
        pfn = package_family()
        if not pfn:
            return False
        target = "shell:AppsFolder\\%s!App" % pfn

        def _bg_launch():
            try:
                os.startfile(target)
            except OSError:
                try:
                    subprocess.Popen(["explorer.exe", target], creationflags=CREATE_NO_WINDOW)
                except OSError:
                    pass

        threading.Thread(target=_bg_launch, daemon=True).start()
        return True
    elif IS_MAC:
        for app_path in ("/Applications/Codex.app", os.path.expanduser("~/Applications/Codex.app")):
            if os.path.isdir(app_path):
                subprocess.Popen(["open", app_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
        subprocess.Popen(["open", "-a", "Codex"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    return False


def _mac_running_codex_bins():
    """从当前运行中的进程列表里提取真实 codex 可执行文件路径。"""
    out = []
    try:
        r = subprocess.run(
            ["ps", "-axo", "command="],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=4,
        )
        for raw in (r.stdout or "").splitlines():
            line = raw.strip()
            if not line or "codex-account-manager" in line:
                continue
            try:
                tok = shlex.split(line)[0]
            except ValueError:
                tok = line.split()[0]
            if os.path.basename(tok) == "codex" and os.path.isabs(tok) and os.path.isfile(tok) and os.access(tok, os.X_OK):
                if tok not in out:
                    out.append(tok)
    except Exception:
        pass
    return out


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
        cands = _mac_running_codex_bins() + [
            "/Applications/Codex.app/Contents/Resources/codex",
            "/Applications/Codex.app/Contents/Resources/bin/codex",
            "/Applications/Codex.app/Contents/MacOS/codex",
            os.path.join(home, "Applications", "Codex.app", "Contents", "Resources", "codex"),
            os.path.join(home, "Applications", "Codex.app", "Contents", "Resources", "bin", "codex"),
        ]
        for sub in (
            os.path.join(home, "Library", "Application Support", "OpenAI", "Codex", "bin", "*", "codex"),
            os.path.join(home, "Library", "Application Support", "Codex", "bin", "*", "codex"),
            os.path.join(home, ".nvm", "versions", "node", "*", "bin", "codex"),
            os.path.join(home, ".fnm", "node-versions", "*", "installation", "bin", "codex"),
        ):
            try:
                cands.extend(sorted(glob.glob(sub), key=os.path.getmtime, reverse=True))
            except OSError:
                pass
        cands.extend([
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
            os.path.join(home, ".codex", "bin", "codex"),
            os.path.join(home, ".local", "bin", "codex"),
            os.path.join(home, ".npm-global", "bin", "codex"),
            os.path.join(home, ".volta", "bin", "codex"),
            os.path.join(home, ".bun", "bin", "codex"),
        ])
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
    elif IS_MAC:
        # 从 .app 启动时 macOS PATH 极简，补齐常见目录以免 npm/Homebrew 版 codex 找不到 node
        env = os.environ.copy()
        extra = [
            os.path.dirname(exe),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            os.path.expanduser("~/.volta/bin"),
            os.path.expanduser("~/.local/bin"),
        ]
        cur_path = env.get("PATH", "")
        env["PATH"] = ":".join([p for p in extra if p] + ([cur_path] if cur_path else []))
        popen_kwargs["env"] = env
    proc = subprocess.Popen([exe, "app-server"], **popen_kwargs)
    timer = threading.Timer(timeout, lambda: proc.kill() if proc.poll() is None else None)
    timer.start()
    try:
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"clientInfo": {"name": "codex-account-manager", "version": "1.3.3"}}},
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
