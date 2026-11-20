r"""使用 PyInstaller 将 Codex 账号管家打包成单个 exe。

用法：python build.py
产物：d:\工具\codex-account-manager\Codex账号管家.exe
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
EXE_NAME = "Codex账号管家"


def main():
    os.chdir(ROOT)
    # 1. 确保图标存在
    from backend.icons import write_app_icons
    write_app_icons(os.path.join(ROOT, "assets"))

    # 2. 清理旧的 build/dist
    for d in ("build", "dist"):
        p = os.path.join(ROOT, d)
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", EXE_NAME,
        "--icon", os.path.join(ROOT, "assets", "icon.ico"),
        "--add-data", "ui;ui",
        "--add-data", "assets;assets",
        "--collect-all", "webview",
        "--collect-all", "clr_loader",
        "--collect-all", "pythonnet",
        "--hidden-import", "pystray._win32",
        "--hidden-import", "PIL._tkinter_finder",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "app.py",
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)

    src = os.path.join(ROOT, "dist", EXE_NAME + ".exe")
    dst = os.path.join(ROOT, EXE_NAME + ".exe")
    shutil.copy2(src, dst)
    print("Built:", dst, "(%.1f MB)" % (os.path.getsize(dst) / (1024 * 1024)))
    desktop_copy = os.path.join(os.path.expanduser("~"), "Desktop", EXE_NAME + ".exe")
    shutil.copy2(src, desktop_copy)
    print("Synced to Desktop:", desktop_copy)


if __name__ == "__main__":
    main()
