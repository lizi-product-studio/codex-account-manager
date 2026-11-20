import datetime
import json
import os
import re
import tempfile
import time

_FRAC = re.compile(r"(:\d\d)\.(\d+)")


def iso_to_epoch(v):
    """ISO 时间字符串 / 数字 -> epoch 秒（float），失败返回 None。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        s = _FRAC.sub(lambda m: m.group(1) + "." + m.group(2)[:6].ljust(6, "0"), s)
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def atomic_write_bytes(path, data: bytes):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-cam-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for _ in range(15):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def atomic_write_json(path, obj):
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None
