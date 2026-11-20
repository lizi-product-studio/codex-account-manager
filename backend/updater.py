"""基于 Git 的跨平台代码更新检测与一键同步重启。

仅操作程序自身 Git 仓库（支持 Windows/macOS 源码运行模式），
绝不触碰 ~/.codex-accounts/ 或 ~/.codex/ 中的账号凭证。
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from . import paths

_CRED_URL_RE = re.compile(r"://[^/@]+@")
_CHECK_INTERVAL = 600.0


def _mask_url(url):
    if not url:
        return ""
    return _CRED_URL_RE.sub("://***@", str(url).strip())


class Updater:
    def __init__(self, app_dir=None, app_version=paths.APP_VERSION):
        self.app_dir = app_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._frozen = getattr(sys, "frozen", False) if app_dir is None else False
        self.app_version = app_version
        self._lock = threading.Lock()
        self._check_lock = threading.Lock()
        self._last_check = 0.0
        self._stop = threading.Event()
        self._status = self._initial_status()

    # ---------------------------------------------------------------- 环境与命令
    def _check_env(self):
        if self._frozen:
            return False, "当前为独立打包版（非 Git 源码目录）"
        if not os.path.exists(os.path.join(self.app_dir, ".git")):
            return False, "当前目录未关联 Git 仓库"
        if not shutil.which("git"):
            return False, "本机未检测到 git 命令"
        return True, ""

    def _run_git(self, args, timeout=15):
        git = shutil.which("git")
        if not git:
            return 127, "", "git not found"
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "never"
        env["LC_ALL"] = "C"
        kw = {
            "cwd": self.app_dir,
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout,
        }
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            p = subprocess.run([git] + list(args), **kw)
            return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timed out"
        except OSError as e:
            return -1, "", str(e)

    def _format_git_error(self, err):
        raw = _mask_url(err or "")
        low = raw.lower()
        if "timed out" in low or "timeout" in low:
            return "连接远程仓库超时，请检查网络或代理后重试"
        if any(k in low for k in (
            "authentication failed",
            "could not read username",
            "terminal prompts disabled",
            "permission denied",
            "access denied",
            "403",
            "401",
        )):
            return "Git 仓库认证失败，请在本机配置好 GitHub 访问权限"
        if any(k in low for k in (
            "could not resolve host",
            "failed to connect",
            "unable to access",
            "connection refused",
            "connection reset",
            "network is unreachable",
        )):
            return "无法连接远程仓库，请检查网络或代理设置"
        first = raw.splitlines()[0] if raw else "未知错误"
        return "操作失败：%s" % first[:160]

    # ---------------------------------------------------------------- 状态读取
    def _empty_status(self, supported=False, reason=""):
        return {
            "app_version": self.app_version,
            "supported": supported,
            "reason": reason,
            "branch": "",
            "local_sha": "",
            "local_short": "",
            "remote_url": "",
            "upstream": "",
            "has_update": False,
            "behind": 0,
            "ahead": 0,
            "remote_sha": "",
            "remote_short": "",
            "commits": [],
            "dirty": False,
            "checked_at": None,
            "error": "",
            "checking": False,
        }

    def _initial_status(self):
        ok, reason = self._check_env()
        st = self._empty_status(supported=ok, reason=reason)
        if ok:
            st.update(self._read_state_snapshot())
        return st

    def _resolve_upstream(self, branch):
        rc, out, _ = self._run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], timeout=5)
        if rc == 0 and out:
            rc_v, _, _ = self._run_git(["rev-parse", "--verify", "--quiet", out], timeout=5)
            if rc_v == 0:
                return out
        cands = []
        if branch and branch != "HEAD":
            cands.append("origin/" + branch)
        cands.extend(["origin/main", "origin/master"])
        for ref in cands:
            rc_v, _, _ = self._run_git(["rev-parse", "--verify", "--quiet", "refs/remotes/" + ref], timeout=5)
            if rc_v == 0:
                return ref
        return ""

    def _read_state_snapshot(self):
        snap = {}
        rc, branch, _ = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], timeout=5)
        branch = branch if rc == 0 else ""
        snap["branch"] = "" if branch == "HEAD" else branch

        rc, lsha, _ = self._run_git(["rev-parse", "HEAD"], timeout=5)
        snap["local_sha"] = lsha if rc == 0 else ""
        rc, lshort, _ = self._run_git(["rev-parse", "--short", "HEAD"], timeout=5)
        snap["local_short"] = lshort if rc == 0 else ""

        rc, rurl, _ = self._run_git(["remote", "get-url", "origin"], timeout=5)
        snap["remote_url"] = _mask_url(rurl) if rc == 0 else ""

        rc, dirty_out, _ = self._run_git(["status", "--porcelain", "--untracked-files=no"], timeout=5)
        snap["dirty"] = bool(rc == 0 and dirty_out)

        upstream = self._resolve_upstream(snap["branch"])
        snap["upstream"] = upstream
        snap["has_update"] = False
        snap["behind"] = 0
        snap["ahead"] = 0
        snap["remote_sha"] = ""
        snap["remote_short"] = ""
        snap["commits"] = []

        if upstream and snap["local_sha"]:
            rc, rsha, _ = self._run_git(["rev-parse", upstream], timeout=5)
            if rc == 0:
                snap["remote_sha"] = rsha
            rc, rshort, _ = self._run_git(["rev-parse", "--short", upstream], timeout=5)
            if rc == 0:
                snap["remote_short"] = rshort
            rc, b_out, _ = self._run_git(["rev-list", "--count", "HEAD.." + upstream], timeout=5)
            if rc == 0 and b_out.isdigit():
                snap["behind"] = int(b_out)
            rc, a_out, _ = self._run_git(["rev-list", "--count", upstream + "..HEAD"], timeout=5)
            if rc == 0 and a_out.isdigit():
                snap["ahead"] = int(a_out)
            if snap["behind"] > 0:
                snap["has_update"] = True
                rc, log_out, _ = self._run_git(
                    ["-c", "i18n.logOutputEncoding=utf-8", "log", "HEAD.." + upstream, "--pretty=format:%h %s", "-n", "5"],
                    timeout=5,
                )
                if rc == 0 and log_out:
                    snap["commits"] = [line.strip() for line in log_out.splitlines() if line.strip()]
        return snap

    def get_status(self):
        with self._lock:
            st = dict(self._status)
            st["commits"] = list(self._status.get("commits") or [])
            return st

    def _set_checking(self, val):
        with self._lock:
            self._status["checking"] = bool(val)

    def _update_status(self, **kwargs):
        with self._lock:
            self._status.update(kwargs)

    def _commit_snap(self, snap=None, error="", touch_time=True):
        data = dict(snap) if snap is not None else self._read_state_snapshot()
        data["supported"] = True
        data["reason"] = ""
        data["error"] = error
        data["checking"] = False
        if touch_time:
            data["checked_at"] = time.time()
        with self._lock:
            self._status.update(data)
            st = dict(self._status)
            st["commits"] = list(self._status.get("commits") or [])
            return st

    # ---------------------------------------------------------------- 公开操作
    def check_update(self, force=False):
        ok, reason = self._check_env()
        if not ok:
            with self._lock:
                self._status.update(self._empty_status(supported=False, reason=reason))
            return {"ok": False, "error": reason, "status": self.get_status()}

        now_mono = time.monotonic()
        if not force and self._last_check > 0 and (now_mono - self._last_check) < _CHECK_INTERVAL:
            return {"ok": True, "cached": True, "status": self.get_status()}

        if force:
            self._check_lock.acquire()
        elif not self._check_lock.acquire(blocking=False):
            return {"ok": True, "busy": True, "status": self.get_status()}

        try:
            now_mono = time.monotonic()
            if not force and self._last_check > 0 and (now_mono - self._last_check) < _CHECK_INTERVAL:
                return {"ok": True, "cached": True, "status": self.get_status()}

            self._set_checking(True)
            rc, rurl, _ = self._run_git(["remote", "get-url", "origin"], timeout=5)
            if rc != 0 or not rurl:
                self._last_check = time.monotonic()
                st = self._commit_snap(error="尚未配置 origin 远程仓库")
                return {"ok": False, "error": st["error"], "status": st}

            rc, _, err = self._run_git(["fetch", "--quiet", "origin"], timeout=15)
            self._last_check = time.monotonic()
            if rc != 0:
                msg = self._format_git_error(err)
                return {"ok": False, "error": msg, "status": self._commit_snap(error=msg)}

            snap = self._read_state_snapshot()
            if not snap.get("upstream"):
                msg = "未找到匹配的远端分支（如 origin/main）"
                return {"ok": False, "error": msg, "status": self._commit_snap(snap, error=msg)}

            return {"ok": True, "status": self._commit_snap(snap)}
        finally:
            self._set_checking(False)
            self._check_lock.release()

    def check_async(self, force=False, delay=0.0):
        def _run():
            if delay > 0 and self._stop.wait(delay):
                return
            if self._stop.is_set():
                return
            try:
                self.check_update(force=force)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_run, daemon=True).start()

    def apply_update(self, force_reset=False):
        ok, reason = self._check_env()
        if not ok:
            return {"ok": False, "error": reason, "status": self.get_status()}

        with self._check_lock:
            self._set_checking(True)
            try:
                rc, _, err = self._run_git(["fetch", "--quiet", "origin"], timeout=20)
                if rc != 0:
                    msg = self._format_git_error(err)
                    return {"ok": False, "error": msg, "status": self._commit_snap(error=msg)}

                snap = self._read_state_snapshot()
                upstream = snap.get("upstream")
                if not upstream:
                    msg = "未找到可跟踪的远端分支（如 origin/main）"
                    return {"ok": False, "error": msg, "status": self._commit_snap(snap, error=msg)}

                if snap.get("behind", 0) <= 0:
                    self._last_check = time.monotonic()
                    return {
                        "ok": True,
                        "updated": False,
                        "message": "当前已是最新版本",
                        "status": self._commit_snap(snap),
                    }

                dirty = snap.get("dirty", False)
                if not force_reset:
                    if dirty:
                        return {
                            "ok": False,
                            "need_force": True,
                            "error": "检测到本地已修改跟踪文件，无法直接快进更新。你可以选择先自动暂存本地改动，再同步到最新版本。",
                            "status": self._commit_snap(snap),
                        }
                    rc_m, _, err_m = self._run_git(["merge", "--ff-only", upstream], timeout=20)
                    if rc_m != 0:
                        return {
                            "ok": False,
                            "need_force": True,
                            "error": "本地代码与远端存在分歧，无法直接合并。你可以选择先自动暂存本地改动，再同步到最新版本。",
                            "detail": _mask_url(err_m),
                            "status": self._commit_snap(),
                        }
                else:
                    if dirty:
                        rc_s, _, err_s = self._run_git(
                            ["stash", "push", "-m", "codex-account-manager-auto-backup"],
                            timeout=20,
                        )
                        if rc_s != 0:
                            msg = "暂存本地改动失败：" + self._format_git_error(err_s)
                            return {"ok": False, "error": msg, "status": self._commit_snap(error=msg)}
                    rc_r, _, err_r = self._run_git(["reset", "--hard", upstream], timeout=20)
                    if rc_r != 0:
                        msg = "同步代码失败：" + self._format_git_error(err_r)
                        return {"ok": False, "error": msg, "status": self._commit_snap(error=msg)}

                self._last_check = time.monotonic()
                st = self._commit_snap()
                new_short = st.get("local_short") or ""
                return {
                    "ok": True,
                    "updated": True,
                    "commit": new_short,
                    "message": "已更新到最新代码（%s），正在重启…" % new_short,
                    "status": st,
                }
            finally:
                self._set_checking(False)

    def close(self):
        self._stop.set()
