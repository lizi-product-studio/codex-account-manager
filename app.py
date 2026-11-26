"""Codex 账号管家 - 入口（支持 Windows & macOS）。

  --minimized   启动时只显示托盘图标（Windows 开机启动用）
  --debug       打开 WebView 开发者工具
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import traceback

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

BASE = os.path.dirname(os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", BASE)          # 打包后资源目录
sys.path.insert(0, BASE)

from backend import paths, settings  # noqa: E402
from backend.notifier import Notifier  # noqa: E402
from backend.service import Service  # noqa: E402
from backend.store import StoreError  # noqa: E402
from backend.tray import Tray  # noqa: E402

TITLE = "Codex 账号管家"
APP_VERSION = paths.APP_VERSION

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    MUTEX_NAME = "Local\\CodexAccountManager"
    SHOW_EVENT_NAME = "Local\\CodexAccountManagerShow"

    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.OpenEventW.restype = wintypes.HANDLE
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.SetEvent.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
else:
    import fcntl


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            r = fn(*args, **kwargs)
            return r if r is not None else {"ok": True}
        except StoreError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    return wrapper


# ============================================================================ 前端接口
class Api:
    """暴露给前端 JS 的接口（下划线开头的属性不会暴露）。"""

    def __init__(self, app):
        self._app = app
        self._svc = app.svc

    def get_state(self):
        def run():
            st = self._svc.get_state()
            self._app.tray.update(st)
            return {"ok": True, "state": st}
        return _safe(run)()

    def refresh_live(self, force=True):
        return _safe(lambda: self._svc.refresh_live(force=bool(force)))()

    def save_current(self, name, note=""):
        return _safe(self._svc.save_current)(name, note)

    def switch_account(self, acc_id, launch=True, force=False):
        return _safe(self._svc.switch)(acc_id, bool(launch), bool(force))

    def stop_codex(self):
        return _safe(self._svc.stop_codex)()

    def apply_switch(self, acc_id, force=False):
        return _safe(self._svc.apply_switch)(acc_id, bool(force))

    def start_codex(self):
        return _safe(self._svc.start_codex)()

    def add_new(self, launch=True, force=False):
        return _safe(self._svc.add_new)(bool(launch), bool(force))

    def rename(self, acc_id, name):
        return _safe(self._svc.rename)(acc_id, name)

    def set_note(self, acc_id, note):
        return _safe(self._svc.set_note)(acc_id, note)

    def move(self, acc_id, delta):
        return _safe(self._svc.move)(acc_id, delta)

    def delete(self, acc_id):
        return _safe(self._svc.delete)(acc_id)

    def open_store(self):
        return _safe(self._svc.open_store)()

    def open_usage_page(self):
        return _safe(self._svc.open_usage_page)()

    def get_settings(self):
        return _safe(self._svc.get_settings)()

    def set_setting(self, key, value):
        return _safe(self._svc.set_setting)(key, value)

    def check_update(self, force=True):
        return _safe(self._svc.check_update)(bool(force))

    def apply_update(self, force_reset=False):
        def run():
            r = self._svc.apply_update(force_reset=bool(force_reset))
            if r.get("ok") and r.get("updated"):
                threading.Timer(0.45, self._app.restart).start()
            return r
        return _safe(run)()

    def break_action(self, action, minutes=5):
        def run():
            b = self._svc.breaks
            if action == "start":
                b.start()
            elif action == "end":
                b.end()
            elif action == "skip":
                b.skip()
            elif action == "snooze":
                b.snooze(int(minutes or 5))
            else:
                raise StoreError("未知的放空操作")
            threading.Thread(target=self._app.refresh_tray, daemon=True).start()
            return {"ok": True, "breaks": b.status()}
        return _safe(run)()

    def quit_app(self):
        threading.Thread(target=self._app.quit, daemon=True).start()
        return {"ok": True}

    def bring_to_front(self):
        threading.Thread(target=self._app.show, daemon=True).start()
        return {"ok": True}


# ============================================================================ 应用
class App:
    def __init__(self):
        self.svc = Service()
        self.window = None
        self.quitting = False
        self.restart_after_exit = False
        self._stop = threading.Event()
        self.notifier = Notifier()
        self.tray = Tray(self.show, self.switch_from_tray, self.quit)
        self.tray.on_break = self.break_from_tray

    # ---------------------------------------------------------------- 窗口
    def show(self):
        if not self.window:
            return
        try:
            self.window.show()
            if IS_WIN:
                hwnd = u32.FindWindowW(None, TITLE)
                if hwnd:
                    if u32.IsIconic(hwnd):
                        u32.ShowWindow(hwnd, 9)   # SW_RESTORE
                    u32.SetForegroundWindow(hwnd)
            else:
                try:
                    self.window.restore()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def on_closing(self):
        if self.quitting or not IS_WIN or not settings.load()["close_to_tray"]:
            return True
        threading.Thread(target=self._hide_to_tray, daemon=True).start()
        return False                          # 取消关闭，改为缩到托盘

    def _hide_to_tray(self):
        self.window.hide()
        s = settings.load()
        if not s["tray_hint_shown"]:
            self.tray.notify("已缩到右下角托盘，会继续在后台记录额度。\n右键托盘图标可以一键切换账号或退出。")
            s["tray_hint_shown"] = True
            settings.save(s)

    def on_closed(self):
        self.quitting = True
        self._stop.set()
        self.tray.stop()

    def quit(self):
        self.quitting = True
        self._stop.set()
        self.tray.stop()
        if self.window:
            try:
                self.window.destroy()
            except Exception:  # noqa: BLE001
                pass

    def restart(self):
        self.restart_after_exit = True
        self.quit()

    # ---------------------------------------------------------------- 放空提醒
    def refresh_tray(self):
        try:
            self.tray.update(self.svc.get_state())
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _push_ui(self):
        if not self.window:
            return
        try:
            self.window.evaluate_js("window.onBreakEvent && window.onBreakEvent(); true")
        except Exception:  # noqa: BLE001
            pass

    def break_from_tray(self, action):
        def run():
            try:
                if action == "start":
                    self.svc.breaks.start()
                    self.tray.notify("开始放空 ☕ 回来后记得点“我回来了”", "放空提醒")
                else:
                    m = self.svc.breaks.end()
                    self.tray.notify("欢迎回来！本次放空 %d 分钟" % max(1, round(m)), "放空提醒")
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            self.refresh_tray()
            self._push_ui()
        threading.Thread(target=run, daemon=True).start()

    def on_break_event(self, kind, msg):
        self.tray.notify(msg, "Codex 账号管家 · 放空提醒")
        if kind == "due" and settings.load().get("break_bring_front", True):
            self.show()
        self.refresh_tray()
        self._push_ui()

    def switch_from_tray(self, acc_id):
        def run():
            self.show()
            js = "setTimeout(function(){ window.switchById && window.switchById(%s); }, 50); true" % json.dumps(acc_id)
            try:
                self.window.evaluate_js(js)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        threading.Thread(target=run, daemon=True).start()

    # ---------------------------------------------------------------- 后台
    def monitor(self):
        """窗口隐藏时也持续记录：额度扫描、当前号定时实时同步、账号时间线、托盘图标、代码更新检查与系统提醒。"""
        while not self._stop.is_set():
            try:
                self.svc.refresh_live_async(force=False)
                if settings.load().get("auto_check_update", True):
                    self.svc.updater.check_async(force=False)
                st = self.svc.get_state()
                self.tray.update(st)
                self.notifier.check(st, self.tray.notify)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            self._stop.wait(30)

    def listen_show(self, handle):
        """第二次启动时，由新进程发信号，这里把已有窗口拉到前台。"""
        if IS_WIN:
            while not self._stop.is_set():
                if k32.WaitForSingleObject(handle, 1000) == 0:
                    self.show()
        else:
            if not handle:
                return
            handle.settimeout(1.0)
            try:
                while not self._stop.is_set():
                    try:
                        conn, _ = handle.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    try:
                        data = conn.recv(16)
                        if b"SHOW" in data:
                            self.show()
                    except OSError:
                        pass
                    finally:
                        try:
                            conn.close()
                        except OSError:
                            pass
            finally:
                try:
                    handle.close()
                except OSError:
                    pass


# ============================================================================ 启动
def _single_instance():
    if IS_WIN:
        mutex = k32.CreateMutexW(None, False, MUTEX_NAME)
        if k32.GetLastError() == 183:             # 已在运行：通知它显示窗口，然后退出
            h = k32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)   # EVENT_MODIFY_STATE
            if h:
                k32.SetEvent(h)
                k32.CloseHandle(h)
            sys.exit(0)
        event = k32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
        return mutex, event

    lock_path = os.path.join(paths.STORE_DIR, ".app.lock")
    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        try:
            lock_file.seek(0)
            port_s = lock_file.read().strip()
            if port_s.isdigit():
                with socket.create_connection(("127.0.0.1", int(port_s)), timeout=1.5) as s:
                    s.sendall(b"SHOW")
        except OSError:
            pass
        try:
            lock_file.close()
        except OSError:
            pass
        sys.exit(0)

    wake_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    wake_sock.bind(("127.0.0.1", 0))
    wake_sock.listen(5)
    port = wake_sock.getsockname()[1]
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(port))
    lock_file.flush()
    return lock_file, wake_sock


def _release_single_instance(guard, handle):
    if IS_WIN:
        try:
            if handle:
                k32.SetEvent(handle)
                k32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass
        try:
            if guard:
                k32.CloseHandle(guard)
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            if handle:
                handle.close()
        except OSError:
            pass
        try:
            if guard:
                fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
                guard.close()
        except OSError:
            pass


def _relaunch():
    os.chdir(BASE)
    cmd = [sys.executable, os.path.abspath(__file__)] + sys.argv[1:]
    if IS_WIN:
        subprocess.Popen(cmd, cwd=BASE)
    else:
        os.execv(sys.executable, cmd)


def _pick_port(preferred=42817):
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return None


def main():
    import webview

    minimized = IS_WIN and ("--minimized" in sys.argv)
    paths.ensure_dirs()
    _instance_guard, show_handle = _single_instance()
    try:
        settings.refresh_autostart_path()
    except OSError:
        pass

    app = App()
    win = webview.create_window(
        TITLE,
        url=os.path.join(RES, "ui", "index.html"),
        js_api=Api(app),
        width=1080, height=780, min_size=(780, 540),
        background_color="#f4f6fa",
        hidden=minimized,
    )
    win.events.closing += app.on_closing
    win.events.closed += app.on_closed
    app.window = win

    app.tray.start()
    app.svc.breaks.start_thread(app.on_break_event)
    threading.Thread(target=app.monitor, daemon=True).start()
    threading.Thread(target=app.listen_show, args=(show_handle,), daemon=True).start()

    storage_dir = os.path.join(paths.STORE_DIR, "webview_data")
    os.makedirs(storage_dir, exist_ok=True)
    # 仅在版本变化时清理 Windows WebView2 静态缓存（保留 Local Storage），平时直接复用加速启动
    ver_file = os.path.join(storage_dir, ".app_version")
    try:
        prev_ver = open(ver_file, "r", encoding="utf-8").read().strip()
    except OSError:
        prev_ver = ""
    if prev_ver != APP_VERSION:
        if IS_WIN:
            for sub in ("Cache", "Code Cache"):
                shutil.rmtree(os.path.join(storage_dir, "EBWebView", "Default", sub), ignore_errors=True)
        try:
            with open(ver_file, "w", encoding="utf-8") as f:
                f.write(APP_VERSION)
        except OSError:
            pass
    if IS_WIN:
        icon_path = os.path.join(RES, "assets", "icon.ico")
    else:
        icon_path = os.path.join(RES, "assets", "icon_mac.png")
        if not os.path.isfile(icon_path):
            icon_path = os.path.join(RES, "assets", "icon.png")
    try:
        webview.start(
            debug="--debug" in sys.argv,
            private_mode=IS_MAC,
            storage_path=storage_dir,
            http_port=_pick_port(0 if IS_MAC else 42817),
            icon=icon_path if os.path.isfile(icon_path) else None,
        )
    finally:
        app.quitting = True
        app._stop.set()
        app.tray.stop()
        app.svc.close()
        _release_single_instance(_instance_guard, show_handle)

    if app.restart_after_exit:
        _relaunch()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        paths.ensure_dirs()
        log_path = os.path.join(paths.STORE_DIR, "app.log")
        err = traceback.format_exc()
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(err + "\n")
        except OSError:
            pass
        msg = "启动失败，错误详情已写入：\n%s\n\n%s" % (log_path, err.splitlines()[-1])
        if IS_WIN:
            u32.MessageBoxW(None, msg, TITLE, 0x10)
        else:
            sys.stderr.write(err + "\n")
            if IS_MAC:
                try:
                    esc_msg = msg.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                    subprocess.run(
                        ["osascript", "-e", 'display alert "%s" message "%s" as critical' % (TITLE, esc_msg)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception:  # noqa: BLE001
                    pass
        sys.exit(1)
