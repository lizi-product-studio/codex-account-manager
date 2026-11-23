"""额度数据：从 Codex 本地会话日志里读取 rate_limits 快照，并按时间线归属到账号。

- 不联网：数据全部来自 %CODEX_HOME%\\sessions\\**\\*.jsonl 中 token_count 事件。
- 会话日志里的 creator_user_id 是“线程创建者”，并不等于发请求的账号，所以不能用它归属；
  这里改用“账号时间线”：记录每个时刻登录的是哪个账号，快照按时间戳归到当时的账号。
"""
import bisect
import glob
import json
import os
import time

from . import paths
from .util import atomic_write_json, iso_to_epoch, load_json

KEEP_DAYS = 30
CACHE_VERSION = 3

# 快照字段下标（列表存储，省空间）
TS, LIMIT, PLAN, P_USED, P_WIN, P_RESET, S_USED, S_WIN, S_RESET, CR_HAS, CR_UNL, CR_BAL, REACHED = range(13)


def _window(w, ts):
    if not isinstance(w, dict):
        return None, None, None
    used = w.get("used_percent")
    minutes = w.get("window_minutes")
    reset = w.get("resets_at")
    if reset is None and isinstance(w.get("resets_in_seconds"), (int, float)):
        reset = ts + w["resets_in_seconds"]          # 兼容旧版本日志
    return used, minutes, reset


def snap_from_event(o):
    pl = o.get("payload")
    if not isinstance(pl, dict) or pl.get("type") != "token_count":
        return None
    rl = pl.get("rate_limits")
    if not isinstance(rl, dict):
        return None
    ts = iso_to_epoch(o.get("timestamp"))
    if ts is None:
        return None
    pu, pw, pr = _window(rl.get("primary"), ts)
    su, sw, sr = _window(rl.get("secondary"), ts)
    if pu is None and su is None:
        return None
    cr = rl.get("credits") if isinstance(rl.get("credits"), dict) else {}
    return [round(ts, 3), rl.get("limit_id") or "codex", rl.get("plan_type") or "",
            pu, pw, pr, su, sw, sr,
            cr.get("has_credits"), cr.get("unlimited"), cr.get("balance"),
            rl.get("rate_limit_reached_type")]


def snap_from_rpc(rl, ts):
    """把官方 codex app-server 的 account/rateLimits/read（camelCase）转成统一快照格式。"""
    if not isinstance(rl, dict):
        return None

    def _w(w):
        if not isinstance(w, dict):
            return None, None, None
        return w.get("usedPercent"), w.get("windowDurationMins"), w.get("resetsAt")

    pu, pw, pr = _w(rl.get("primary"))
    su, sw, sr = _w(rl.get("secondary"))
    if pu is None and su is None:
        return None
    cr = rl.get("credits") if isinstance(rl.get("credits"), dict) else {}
    return [round(ts, 3), rl.get("limitId") or "codex", rl.get("planType") or "",
            pu, pw, pr, su, sw, sr,
            cr.get("hasCredits"), cr.get("unlimited"), cr.get("balance"),
            rl.get("rateLimitReachedType")]


def _long_reset(s):
    """取窗口更长的那个重置时间作为账号“指纹”（每周窗口的重置时刻对每个账号基本固定）。"""
    if s[S_WIN] and (not s[P_WIN] or s[S_WIN] > s[P_WIN]):
        return s[S_WIN], s[S_RESET]
    return s[P_WIN], s[P_RESET]


def same_fingerprint(a, b, tol=180):
    if a[LIMIT] != b[LIMIT] or a[PLAN] != b[PLAN]:
        return False
    wa, ra = _long_reset(a)
    wb, rb = _long_reset(b)
    if wa != wb:
        return False
    if ra is None or rb is None:
        return ra is None and rb is None
    return abs(ra - rb) <= tol


# ======================================================================== 扫描
class UsageScanner:
    def __init__(self):
        c = load_json(paths.USAGE_CACHE, {})
        if not isinstance(c, dict) or c.get("v") != CACHE_VERSION:
            c = {"v": CACHE_VERSION, "files": {}, "snaps": []}
        self.files = c["files"]
        self.snaps = c["snaps"]
        self._keys = {(s[TS], s[LIMIT]) for s in self.snaps}
        self.last_scan = 0.0

    def _candidates(self):
        roots = [os.path.join(paths.CODEX_HOME, "sessions")]
        roots += glob.glob(os.path.join(paths.CODEX_HOME, "archived_sessions*"))
        cutoff = time.time() - KEEP_DAYS * 86400
        seen, out = set(), []
        for r in roots:
            if not os.path.isdir(r):
                continue
            for f in glob.iglob(os.path.join(r, "**", "*.jsonl"), recursive=True):
                try:
                    st = os.stat(f)
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    continue
                ident = (st.st_dev, st.st_ino) if st.st_ino else os.path.realpath(f).lower()
                if ident in seen:              # 硬链接/重复文件只读一次
                    continue
                seen.add(ident)
                out.append((f, st.st_size))
        return out

    def scan(self):
        added = 0
        live = set()
        for path, size in self._candidates():
            live.add(path)
            ent = dict(self.files.get(path) or {})
            off = ent.get("off", 0)
            if size < off:                     # 文件被重写，从头读
                off = 0
                ent = {}
            if size == off:
                continue
            is_root = "_" not in os.path.basename(path)
            try:
                with open(path, "rb") as fh:
                    fh.seek(off)
                    for line in fh:
                        if not line.endswith(b"\n"):
                            break              # 半行，等下次
                        off += len(line)
                        if is_root and "acct" not in ent and b'"session_meta"' in line:
                            try:
                                meta = json.loads(line)
                                if meta.get("type") == "session_meta":
                                    pl = meta.get("payload") or {}
                                    ent["acct"] = pl.get("creator_account_id") or ""
                                    ent["created"] = iso_to_epoch(pl.get("timestamp") or meta.get("timestamp"))
                            except ValueError:
                                pass
                            continue
                        if b'"rate_limits"' not in line:
                            continue
                        try:
                            s = snap_from_event(json.loads(line))
                        except ValueError:
                            continue
                        if s:
                            if is_root and ent.get("acct") and "first_snap" not in ent:
                                cr = ent.get("created")
                                if cr is None or abs(s[TS] - cr) <= 3600:
                                    ent["first_snap"] = s
                            if (s[TS], s[LIMIT]) not in self._keys:
                                self._keys.add((s[TS], s[LIMIT]))
                                self.snaps.append(s)
                                added += 1
            except OSError:
                continue
            ent["off"] = off
            self.files[path] = ent

        cutoff = time.time() - KEEP_DAYS * 86400
        dirty = added > 0 or any(p not in live for p in self.files)
        self.files = {p: v for p, v in self.files.items() if p in live}
        if added:
            self.snaps.sort(key=lambda s: s[TS])
        if self.snaps and self.snaps[0][TS] < cutoff:
            self.snaps = [s for s in self.snaps if s[TS] >= cutoff]
            self._keys = {(s[TS], s[LIMIT]) for s in self.snaps}
            dirty = True
        if dirty or added or not os.path.exists(paths.USAGE_CACHE):
            atomic_write_json(paths.USAGE_CACHE, {"v": CACHE_VERSION, "files": self.files, "snaps": self.snaps})
        self.last_scan = time.time()
        return added


# ======================================================================== 时间线
class Timeline:
    """记录“从某时刻起，登录的是哪个账号（key）”。key 为空串表示未登录。"""

    def __init__(self):
        self.entries = []
        try:
            with open(paths.TIMELINE_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        o = json.loads(line)
                        self.entries.append([float(o["ts"]), o.get("key") or ""])
                    except (ValueError, KeyError, TypeError):
                        pass
        except OSError:
            pass
        self.entries.sort(key=lambda e: e[0])
        self._ts = [e[0] for e in self.entries]
        st = load_json(paths.STATE_JSON, {})
        self.last_seen = st.get("last_seen") if isinstance(st, dict) else None
        self._last_state_save = 0.0

    def current_key(self):
        return self.entries[-1][1] if self.entries else None

    def append(self, ts, key, how):
        if self.entries and ts <= self.entries[-1][0]:
            ts = self.entries[-1][0] + 0.001
        self.entries.append([ts, key])
        self._ts.append(ts)
        with open(paths.TIMELINE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(ts, 3), "key": key, "how": how,
                                "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))},
                               ensure_ascii=False) + "\n")

    def touch(self, now):
        self.last_seen = now
        if now - self._last_state_save > 30:
            atomic_write_json(paths.STATE_JSON, {"last_seen": now})
            self._last_state_save = now

    def key_at(self, ts):
        i = bisect.bisect_right(self._ts, ts) - 1
        return self.entries[i][1] if i >= 0 else None


# ======================================================================== 归属
def seed_time(snaps, cur_plan, auth_mtime, now):
    """首次运行：估算当前账号是从什么时候开始用的，用于把之前的快照归给它。"""
    if not snaps:
        return now
    latest = snaps[-1]
    if auth_mtime and latest[TS] < auth_mtime - 120 and latest[PLAN] != cur_plan:
        return auth_mtime          # 最近的快照很可能不是当前账号的
    t0 = latest[TS]
    for s in reversed(snaps):
        if s[LIMIT] != latest[LIMIT]:
            continue
        if not same_fingerprint(s, latest):
            break
        t0 = s[TS]
    return t0 - 0.001


def estimate_switch_time(snaps, timeline, prev_key, t_seen, auth_mtime, now):
    """在工具没运行时发生了账号切换：估算切换时刻。"""
    if not prev_key:
        return t_seen or now
    t_seen = t_seen or (timeline.entries[-1][0] if timeline.entries else now)
    tss = [s[TS] for s in snaps]
    i = bisect.bisect_right(tss, t_seen)
    ref = None
    for s in reversed(snaps[:i]):
        if timeline.key_at(s[TS]) == prev_key:
            ref = s
            break
    window = snaps[i:]
    if ref and window:
        for s in window:
            if s[LIMIT] == ref[LIMIT] and not same_fingerprint(s, ref):
                return s[TS] - 0.001
        return now
    if auth_mtime and t_seen < auth_mtime <= now:
        return auth_mtime
    return now


def window_label(minutes):
    if not minutes:
        return "额度"
    m = int(minutes)
    if m == 300:
        return "5 小时"
    if m == 10080:
        return "每周"
    if m == 1440:
        return "每日"
    if 40000 <= m <= 45000:
        return "每月"
    if m % 1440 == 0:
        return "%d 天" % (m // 1440)
    if m % 60 == 0:
        return "%d 小时" % (m // 60)
    return "%d 分钟" % m


def summarize(snaps, timeline, file_metas=None, known_accounts=None, live_snaps=None):
    """-> {key: {"updated": ts, "plan": str, "limits": [...]}}"""
    latest = {}
    for s in snaps:
        key = timeline.key_at(s[TS])
        if not key:
            continue
        latest.setdefault(key, {})[s[LIMIT]] = s

    # 合并通过官方 codex.exe 读取到的实时额度快照（时间戳更新则覆盖）
    if isinstance(live_snaps, dict):
        for key, items in live_snaps.items():
            if not key or not isinstance(items, list):
                continue
            for s in items:
                if not isinstance(s, list) or len(s) < 13:
                    continue
                cur = latest.setdefault(key, {}).get(s[LIMIT])
                if cur is None or s[TS] >= cur[TS]:
                    latest[key][s[LIMIT]] = s

    # 历史回溯：对于加入时间线前就在本机用过、但之后还没发过消息的账号，
    # 用它新建会话(root session)里的首条额度快照指纹，从历史日志找回最后一次已知额度与重置时间。
    if file_metas and known_accounts:
        tl_start = timeline.entries[0][0] if timeline.entries else float("inf")
        claimed = [s for by_lim in latest.values() for s in by_lim.values()]
        for key, account_id in known_accounts:
            if not key or not account_id or key in latest:
                continue
            anchors = [
                ent["first_snap"]
                for ent in file_metas.values()
                if ent.get("acct") == account_id and ent.get("first_snap")
                and (ent.get("created") or ent["first_snap"][TS]) < tl_start
            ]
            if not anchors:
                continue
            anchor = max(anchors, key=lambda s: s[TS])
            if any(same_fingerprint(c, anchor) for c in claimed):
                continue
            best = anchor
            for s in snaps:
                if s[TS] >= tl_start:
                    break
                if same_fingerprint(s, anchor) and s[TS] >= best[TS]:
                    best = s
            latest.setdefault(key, {})[best[LIMIT]] = best
            claimed.append(best)

    out = {}
    for key, by_limit in latest.items():
        limits = []
        for lid, s in by_limit.items():
            wins = []
            for used, minutes, reset in ((s[P_USED], s[P_WIN], s[P_RESET]), (s[S_USED], s[S_WIN], s[S_RESET])):
                if used is None:
                    continue
                wins.append(dict(minutes=minutes, label=window_label(minutes), used=float(used), resets_at=reset))
            wins.sort(key=lambda w: w["minutes"] or 0)
            limits.append(dict(
                id=lid, plan=s[PLAN], updated=s[TS], reached=s[REACHED], windows=wins,
                credits=dict(has=s[CR_HAS], unlimited=s[CR_UNL], balance=s[CR_BAL]),
            ))
        limits.sort(key=lambda l: (l["id"] != "codex", l["id"]))
        out[key] = dict(updated=max(l["updated"] for l in limits),
                        plan=limits[0]["plan"] if limits else "", limits=limits)
    return out


LIVE_SECONDS = 15 * 60


def account_status(usage, now):
    """把一个账号的额度汇总成一眼能看懂的状态。

    remaining : 主额度里“最紧”的那个窗口还剩多少（%），已过重置时间的窗口按 100% 算
    level     : ok / low / out / none
    freshness : live（15 分钟内）/ stale（上次已知）/ reset（所有窗口都已过重置时间，预计已恢复）/ none
    """
    st = dict(remaining=None, level="none", freshness="none", recover_at=None,
              next_reset=None, reset_at=None, bottleneck=None, updated=None)
    if not usage or not usage.get("limits"):
        return st
    lim = usage["limits"][0]
    wins = lim["windows"]
    if not wins:
        return st
    rem, bottleneck, all_reset, resets, past_resets = 100.0, None, True, [], []
    for w in wins:
        r = w.get("resets_at")
        if r and r <= now:
            past_resets.append(r)
            continue
        all_reset = False
        if r:
            resets.append(r)
        left = max(0.0, 100.0 - float(w["used"]))
        if bottleneck is None or left < rem:
            rem, bottleneck = left, w
    st["remaining"] = round(rem)
    st["bottleneck"] = bottleneck["label"] if bottleneck else None
    st["next_reset"] = min(resets) if resets else None
    st["reset_at"] = max(past_resets) if past_resets else None
    st["updated"] = usage["updated"]
    if (not all_reset and lim.get("reached")) or rem <= 0.5:
        st["remaining"] = 0
        st["level"] = "out"
        st["recover_at"] = (bottleneck.get("resets_at") if bottleneck else None) or st["next_reset"]
    elif rem < 30:
        st["level"] = "low"
    else:
        st["level"] = "ok"
    if all_reset:
        st["freshness"] = "reset"
    elif now - usage["updated"] <= LIVE_SECONDS:
        st["freshness"] = "live"
    else:
        st["freshness"] = "stale"
    return st


def _match_window(snap, minutes, reset, tol=180):
    """从一条快照里找到与 (minutes, reset) 对应的窗口 used_percent。"""
    for used, win_m, win_r in ((snap[P_USED], snap[P_WIN], snap[P_RESET]),
                               (snap[S_USED], snap[S_WIN], snap[S_RESET])):
        if used is None or win_m != minutes:
            continue
        if reset is None or win_r is None or abs(win_r - reset) <= tol:
            return float(used)
    return None


def burn_forecasts(snaps, timeline, usage_by_key, now):
    """基于最近 24h 本地快照，保守估算每个账号按当前消耗速度是否能撑到重置。"""
    since = now - 86400
    by_key = {}
    for s in snaps:
        if s[TS] < since:
            continue
        k = timeline.key_at(s[TS])
        if k:
            by_key.setdefault(k, []).append(s)

    out = {}
    for key, usage in usage_by_key.items():
        lims = usage.get("limits") or []
        if not lims:
            continue
        lim = lims[0]
        # 找当前未重置且最紧的窗口
        target = None
        for w in lim["windows"]:
            r = w.get("resets_at")
            if r and r <= now:
                continue
            if target is None or w["used"] > target["used"]:
                target = w
        if not target or target["used"] >= 99.5:
            continue

        pts = []
        for s in by_key.get(key, ()):
            if s[LIMIT] != lim["id"]:
                continue
            u = _match_window(s, target["minutes"], target.get("resets_at"))
            if u is not None:
                pts.append((s[TS], u))
        if len(pts) < 2:
            continue
        t0, u0 = pts[0]
        t1, u1 = pts[-1]
        span = t1 - t0
        delta = u1 - u0
        # 样本太短（< 1 小时）或消耗太少（< 3%）不做预测，避免误导
        if span < 3600 or delta < 3.0:
            continue
        rate_per_sec = delta / span
        rem = max(0.0, 100.0 - float(target["used"]))
        empty_at = now + rem / rate_per_sec
        reset = target.get("resets_at")
        will_last = bool(reset and empty_at >= reset)
        out[key] = dict(
            window=target["label"],
            rate_per_day=round(rate_per_sec * 86400, 1),
            empty_at=round(empty_at),
            will_last=will_last,
        )
    return out
