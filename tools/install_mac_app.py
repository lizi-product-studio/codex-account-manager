"""生成或更新轻量级 macOS .app 启动器（直接运行 Git 仓库源码，无冷冻副本，支持应用内自动更新）。"""
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.paths import APP_VERSION  # noqa: E402

APP_NAME = "Codex账号管家.app"


def _can_import_webview(py_path):
    try:
        p = subprocess.run(
            [py_path, "-c", "import webview"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=6,
        )
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_repo_python(repo_dir):
    repo_dir = os.path.abspath(repo_dir)
    raw_cands = [
        os.path.join(repo_dir, ".venv", "bin", "python"),
        os.path.join(repo_dir, ".venv", "bin", "python3"),
        os.path.join(repo_dir, "venv", "bin", "python"),
        os.path.join(repo_dir, "venv", "bin", "python3"),
        sys.executable,
        shutil.which("python3") or "",
    ]
    cands = []
    seen = set()
    for c in raw_cands:
        if not c:
            continue
        ap = os.path.abspath(c)
        if ap in seen:
            continue
        seen.add(ap)
        if os.path.isfile(ap) and os.access(ap, os.X_OK):
            cands.append(ap)

    for c in cands:
        if _can_import_webview(c):
            return c
    for c in cands:
        if c.startswith(os.path.join(repo_dir, ".venv")) or c.startswith(os.path.join(repo_dir, "venv")):
            return c
    return cands[0] if cands else sys.executable


def _generate_icns(icon_png, out_icns):
    if sys.platform != "darwin" or not os.path.isfile(icon_png):
        return False
    if not shutil.which("sips") or not shutil.which("iconutil"):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "AppIcon.iconset")
        os.makedirs(iconset, exist_ok=True)
        try:
            for sz in (16, 32, 128, 256, 512):
                subprocess.run(
                    ["sips", "-z", str(sz), str(sz), icon_png, "--out",
                     os.path.join(iconset, "icon_%dx%d.png" % (sz, sz))],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                )
                dbl = sz * 2
                subprocess.run(
                    ["sips", "-z", str(dbl), str(dbl), icon_png, "--out",
                     os.path.join(iconset, "icon_%dx%d@2x.png" % (sz, sz))],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                )
            subprocess.run(
                ["iconutil", "-c", "icns", iconset, "-o", out_icns],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
            )
            return os.path.isfile(out_icns)
        except (OSError, subprocess.CalledProcessError):
            return False


def build_mac_app(repo_dir, target_app, python_bin=None, make_icns=True):
    repo_dir = os.path.abspath(repo_dir)
    target_app = os.path.abspath(target_app)
    py_bin = os.path.abspath(python_bin) if python_bin else find_repo_python(repo_dir)

    if os.path.exists(target_app):
        shutil.rmtree(target_app)

    contents = os.path.join(target_app, "Contents")
    macos_dir = os.path.join(contents, "MacOS")
    res_dir = os.path.join(contents, "Resources")
    os.makedirs(macos_dir, exist_ok=True)
    os.makedirs(res_dir, exist_ok=True)

    plist_data = {
        "CFBundleName": "Codex账号管家",
        "CFBundleDisplayName": "Codex 账号管家",
        "CFBundleIdentifier": "com.codexaccountmanager.app",
        "CFBundleVersion": APP_VERSION,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleExecutable": "CodexAccountManager",
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "AppIcon",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    }
    with open(os.path.join(contents, "Info.plist"), "wb") as f:
        plistlib.dump(plist_data, f)

    if make_icns:
        icon_png = os.path.join(repo_dir, "assets", "icon_mac.png")
        if not os.path.isfile(icon_png):
            icon_png = os.path.join(repo_dir, "assets", "icon.png")
        out_icns = os.path.join(res_dir, "AppIcon.icns")
        if not _generate_icns(icon_png, out_icns) and os.path.isfile(icon_png):
            shutil.copy2(icon_png, os.path.join(res_dir, "AppIcon.png"))

    launcher_script = "\n".join([
        "#!/bin/bash",
        "REPO_DIR=" + shlex.quote(repo_dir),
        "PY_CFG=" + shlex.quote(py_bin),
        'PY_BIN=""',
        'for cand in "$PY_CFG" "$REPO_DIR/.venv/bin/python" "$REPO_DIR/venv/bin/python" "$(command -v python3 2>/dev/null || true)"; do',
        '  if [ -n "$cand" ] && [ -x "$cand" ] && "$cand" -c "import webview" 2>/dev/null; then',
        '    PY_BIN="$cand"',
        "    break",
        "  fi",
        "done",
        'if [ -z "$PY_BIN" ]; then',
        '  for cand in "$PY_CFG" "$REPO_DIR/.venv/bin/python" "$REPO_DIR/venv/bin/python" "$(command -v python3 2>/dev/null || true)"; do',
        '    if [ -n "$cand" ] && [ -x "$cand" ]; then',
        '      PY_BIN="$cand"',
        "      break",
        "    fi",
        "  done",
        "fi",
        'if [ -z "$PY_BIN" ]; then',
        '  osascript -e \'display alert "Codex 账号管家" message "未找到可用的 Python 环境，请检查工具目录。" as critical\' >/dev/null 2>&1 || true',
        "  exit 1",
        "fi",
        'cd "$REPO_DIR" || exit 1',
        'exec "$PY_BIN" "$REPO_DIR/app.py" "$@"',
        "",
    ])
    launcher_path = os.path.join(macos_dir, "CodexAccountManager")
    with open(launcher_path, "wb") as f:
        f.write(launcher_script.encode("utf-8"))
    try:
        os.chmod(launcher_path, 0o755)
    except OSError:
        pass
    return target_app, py_bin


def main():
    if sys.platform != "darwin":
        sys.stderr.write("仅支持在 macOS 上安装 .app 启动器。\n")
        return 1

    repo_dir = REPO_ROOT
    py_bin = find_repo_python(repo_dir)
    if not _can_import_webview(py_bin):
        sys.stderr.write("警告：所选 Python（%s）暂未安装 pywebview，请先确认已安装依赖。\n" % py_bin)

    target = os.path.join("/Applications", APP_NAME)
    try:
        installed, used_py = build_mac_app(repo_dir, target, python_bin=py_bin, make_icns=True)
    except OSError:
        user_apps = os.path.expanduser("~/Applications")
        os.makedirs(user_apps, exist_ok=True)
        target = os.path.join(user_apps, APP_NAME)
        installed, used_py = build_mac_app(repo_dir, target, python_bin=py_bin, make_icns=True)

    local_app = os.path.join(repo_dir, APP_NAME)
    if os.path.exists(local_app):
        build_mac_app(repo_dir, local_app, python_bin=used_py, make_icns=True)
        print("已同步更新目录内启动器：", local_app)

    try:
        subprocess.run(["touch", installed], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass

    print("已生成轻量版 .app（直连 Git 仓库源码，支持自动更新）：", installed)
    print("关联目录：", repo_dir)
    print("Python  ：", used_py)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
