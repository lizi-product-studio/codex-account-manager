"""系统托盘与原生通知：Windows 使用 pystray 托盘图标；macOS 安全降级为系统通知（避免与 pywebview 冲突）。"""
import datetime
import subprocess
import sys
import threading

IS_WIN = sys.platform == "win32"

if IS_WIN:
    import pystray

    from .icons import tray_icon

LEVEL_TEXT = {"ok": "充足", "low": "偏紧", "out": "已用尽", "none": "无数据"}


def _fmt_time(ts):
    d = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    hm = d.strftime("%H:%M")
    if d.date() == today:
        return "今天 " + hm
    if (d.date() - today).days == 1:
        return "明天 " + hm
    return "%d/%d %s" % (d.month, d.day, hm)


def status_text(st):
    if not st or st.get("level") == "none":
        return "无额度数据"
    if st["level"] == "out":
        return "已用尽" + ("，%s 恢复" % _fmt_time(st["recover_at"]) if st.get("recover_at") else "")
    txt = "剩余 %d%%" % st["remaining"]
    if st.get("freshness") == "reset":
        txt = "应已重置"
    elif st.get("freshness") == "stale":
        txt += "（旧数据）"
    return txt


def _mac_osascript_notify(message, title="Codex 账号管家"):
    def _esc(s):
        return str(s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

    script = 'display notification "%s" with title "%s"' % (_esc(message), _esc(title))
    subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


if IS_WIN:
    class Tray:
        def __init__(self, on_show, on_switch, on_quit):
            self._on_show = on_show
            self._on_switch = on_switch
            self._on_quit = on_quit
            self.on_break = None
            self._state = None
            self._last_sig = None
            self.icon = pystray.Icon(
                "CodexAccountManager", tray_icon(None, "none"), "Codex 账号管家",
                menu=pystray.Menu(self._menu_items),
            )

        # ---------------------------------------------------------------- 菜单
        def _menu_items(self):
            yield pystray.MenuItem("打开主窗口", lambda icon, item: self._on_show(), default=True)
            yield pystray.Menu.SEPARATOR
            accs = (self._state or {}).get("accounts") or []
            if not accs:
                yield pystray.MenuItem("（还没有保存账号）", None, enabled=False)
            for a in accs:
                label = "%s   ·   %s" % (a["name"], status_text(a.get("status")))
                yield pystray.MenuItem(
                    label,
                    self._make_switch(a["id"], a["is_current"]),
                    checked=(lambda item, cur=a["is_current"]: cur),
                    radio=True,
                    enabled=a["valid"],
                )
            yield pystray.Menu.SEPARATOR
            br = (self._state or {}).get("breaks") or {}
            if br.get("enabled") and self.on_break:
                if br.get("active"):
                    yield pystray.MenuItem("☕ 已回来", lambda icon, item: self.on_break("end"))
                else:
                    yield pystray.MenuItem("☕ 去放空", lambda icon, item: self.on_break("start"))
                yield pystray.Menu.SEPARATOR
            yield pystray.MenuItem("退出", lambda icon, item: self._on_quit())

        def _make_switch(self, acc_id, is_current):
            def action(icon, item):
                if is_current:
                    self._on_show()
                else:
                    self._on_switch(acc_id)
            return action

        # ---------------------------------------------------------------- 更新
        def update(self, state):
            self._state = state
            cur = next((a for a in state.get("accounts", []) if a["is_current"]), None)
            st = (cur or {}).get("status") or state.get("current", {}).get("status") or {}
            level = st.get("level", "none")
            remaining = st.get("remaining") if level != "none" else None
            if st.get("freshness") == "stale" and level != "out":
                level = "none"   # 旧数据不着色，避免误导
            name = cur["name"] if cur else (state.get("current", {}).get("email") or "未登录")
            title = "Codex 账号管家\n当前：%s · %s" % (name, status_text(st))
            br = state.get("breaks") or {}
            sig = (remaining, level, title, bool(br.get("enabled")), bool(br.get("active")), tuple((a["id"], a["name"], a["is_current"], status_text(a.get("status")))
                                                 for a in state.get("accounts", [])))
            if sig == self._last_sig:
                return
            self._last_sig = sig
            try:
                self.icon.icon = tray_icon(remaining, level)
                self.icon.title = title[:127]
                self.icon.update_menu()
            except Exception:  # noqa: BLE001  托盘还没就绪时忽略
                pass

        def notify(self, message, title="Codex 账号管家"):
            try:
                self.icon.notify(message, title)
            except Exception:  # noqa: BLE001
                pass

        def start(self):
            threading.Thread(target=self.icon.run, daemon=True).start()

        def stop(self):
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass
else:
    class Tray:
        """非 Windows 平台占位：pystray 与 pywebview 同进程运行在 macOS 会触发 Cocoa 线程崩溃，改用原生通知。"""

        def __init__(self, on_show, on_switch, on_quit):
            self._on_show = on_show
            self._on_switch = on_switch
            self._on_quit = on_quit
            self.on_break = None

        def update(self, state):
            pass

        def notify(self, message, title="Codex 账号管家"):
            if sys.platform == "darwin":
                try:
                    _mac_osascript_notify(message, title)
                except Exception:  # noqa: BLE001
                    pass

        def start(self):
            pass

        def stop(self):
            pass
