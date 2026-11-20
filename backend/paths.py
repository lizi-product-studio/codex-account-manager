"""所有路径集中在这里。"""
import os

APP_VERSION = "1.3.3"

HOME = os.path.expanduser("~")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
AUTH_FILE = os.path.join(CODEX_HOME, "auth.json")

# 账号存档放在用户目录下（不放进工具目录，避免被误分享/同步）
STORE_DIR = os.environ.get("CODEX_ACCOUNTS_DIR") or os.path.join(HOME, ".codex-accounts")
ACCOUNTS_DIR = os.path.join(STORE_DIR, "accounts")
BACKUP_DIR = os.path.join(STORE_DIR, "_backup")
ACCOUNTS_JSON = os.path.join(STORE_DIR, "accounts.json")
TIMELINE_FILE = os.path.join(STORE_DIR, "timeline.jsonl")
STATE_JSON = os.path.join(STORE_DIR, "state.json")
USAGE_CACHE = os.path.join(STORE_DIR, "usage_cache.json")
LIVE_USAGE = os.path.join(STORE_DIR, "live_usage.json")
SETTINGS_JSON = os.path.join(STORE_DIR, "settings.json")
NOTIFIED_JSON = os.path.join(STORE_DIR, "notified.json")

OFFICIAL_USAGE_URL = "https://chatgpt.com/codex/settings/usage"


def ensure_dirs():
    for d in (STORE_DIR, ACCOUNTS_DIR, BACKUP_DIR):
        os.makedirs(d, exist_ok=True)
