"""用户设置（settings.json）+ 开机启动（Windows 注册表 / macOS LaunchAgents）。"""
import os
import sys

if sys.platform == "win32":
    import winreg
elif sys.platform == "darwin":
    import plistlib

from . import paths
from .util import atomic_write_json, load_json

DEFAULTS = {
    "close_to_tray": sys.platform == "win32",  # 点 × 时缩到托盘（Windows）
    "autostart": False,                        # 开机启动
    "launch_after_switch": True,               # 切换后自动启动 Codex
    "confirm_before_switch": True,             # 切换前弹窗确认（关闭后一键直切）
    "notify_enabled": True,                    # 额度快用尽 / 已重置 / 订阅临期 / 新登录 / 新版本系统提醒
    "auto_check_update": True,                 # 自动检查 GitHub 仓库更新
    "sort_mode": "custom",                     # 总览排序模式：custom | remaining
    "privacy_mode": False,                     # 隐私模式：对邮箱进行脱敏打码
    "theme": "auto",                           # 主题外观：auto | light | dark
    "tray_hint_shown": False,                  # 是否提示过“已缩到托盘”
    # ---- 放空小站 ----
    "break_enabled": True,                     # 顶栏显示「去放空」
    "break_minutes": 10,                       # 单次建议时长（超过提醒回来）
    "break_goal": 8,                           # 每日放空目标次数
}

INT_LIMITS = {"break_minutes": (3, 60), "break_goal": (1, 30)}
TIME_KEYS = ()


def _norm_time(v):
    try:
        h, m = str(v).strip().split(":")[:2]
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return "%02d:%02d" % (h, m)
    return None


def _norm_int(key, v):
    lo, hi = INT_LIMITS[key]
    try:
        return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError):
        return None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "CodexAccountManager"
PLIST_LABEL = "com.codexaccountmanager"
PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_LABEL}.plist")


def load():
    s = load_json(paths.SETTINGS_JSON, {})
    if not isinstance(s, dict):
        s = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in s.items() if k in DEFAULTS})
    out["sort_mode"] = "remaining" if out.get("sort_mode") == "remaining" else "custom"
    out["theme"] = out.get("theme") if out.get("theme") in ("auto", "light", "dark") else "auto"
    out["privacy_mode"] = bool(out.get("privacy_mode", False))
    for k in INT_LIMITS:
        v = _norm_int(k, out.get(k))
        out[k] = v if v is not None else DEFAULTS[k]
    for k in TIME_KEYS:
        out[k] = _norm_time(out.get(k)) or DEFAULTS[k]
    out["autostart"] = autostart_enabled()
    return out


def save(s):
    atomic_write_json(paths.SETTINGS_JSON, {k: s.get(k, v) for k, v in DEFAULTS.items()})


def set_value(key, value):
    if key not in DEFAULTS:
        raise KeyError(key)
    s = load()
    if key == "sort_mode":
        s[key] = "remaining" if str(value) == "remaining" else "custom"
    elif key == "theme":
        s[key] = str(value) if str(value) in ("auto", "light", "dark") else "auto"
    elif key in INT_LIMITS:
        v = _norm_int(key, value)
        if v is None:
            raise ValueError("请输入有效的分钟数")
        s[key] = v
    elif key in TIME_KEYS:
        v = _norm_time(value)
        if v is None:
            raise ValueError("时间格式不正确，应为 HH:MM")
        s[key] = v
    else:
        s[key] = bool(value)
        if key == "autostart":
            set_autostart(bool(value))
    save(s)
    return s


def _app_dir_and_entry():
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return app_dir, os.path.join(app_dir, "app.py")


def launch_command(minimized=True):
    if getattr(sys, "frozen", False):
        cmd = '"%s"' % sys.executable
    else:
        exe = sys.executable
        if sys.platform == "win32" and exe.lower().endswith("python.exe"):
            cand = exe[:-10] + "pythonw.exe"
            if os.path.exists(cand):
                exe = cand
        _, app = _app_dir_and_entry()
        cmd = '"%s" "%s"' % (exe, app)
    return cmd + (" --minimized" if minimized else "")


def autostart_enabled():
    if sys.platform == "win32":
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, RUN_NAME)
                return True
        except OSError:
            return False
    elif sys.platform == "darwin":
        return os.path.isfile(PLIST_PATH)
    return False


def _mac_plist_payload():
    app_dir, app_py = _app_dir_and_entry()
    if getattr(sys, "frozen", False):
        prog_args = [sys.executable]
    else:
        prog_args = [sys.executable, app_py]
    return {
        "Label": PLIST_LABEL,
        "ProgramArguments": prog_args,
        "RunAtLoad": True,
        "WorkingDirectory": app_dir,
    }


def set_autostart(on):
    if sys.platform == "win32":
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, launch_command(True))
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        if on:
            os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
            with open(PLIST_PATH, "wb") as f:
                plistlib.dump(_mac_plist_payload(), f)
        else:
            try:
                os.remove(PLIST_PATH)
            except FileNotFoundError:
                pass


def refresh_autostart_path():
    """exe / Python 位置变了（比如重新打包/移动目录）时，更新开机启动路径。"""
    if autostart_enabled():
        set_autostart(True)
