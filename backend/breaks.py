"""放空提醒：按上下班 / 午休时间表定时提醒起身放空，并记录每次放空时长。

默认：早 9 晚 6、12:00-13:00 午休；上午每 60 分钟提醒一次，下午每 30 分钟提醒一次，每次放空 5-10 分钟。
状态持久化在 ~/.codex-accounts/breaks.json，重启程序不丢当天记录。
"""
import datetime
import threading
import time
import traceback

from . import paths, settings
from .util import atomic_write_json, load_json

GRACE_SECONDS = 30 * 60        # 到点后最长保持“待放空”多久，超过算错过
EARLY_MERGE_SECONDS = 15 * 60  # 提前去放空：下一个提醒点在 15 分钟内则一并算完成
AUTO_END_MINUTES = 60          # 放空超过 60 分钟视为忘了点“我回来了”，自动结束
INFO_WINDOW = 10 * 60          # 午休 / 下班提醒只在到点后 10 分钟内发送（避免晚上打开程序时乱发）


def _hm(s):
    try:
        h, m = str(s).strip().split(":")[:2]
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h * 60 + m
    return None


def _fmt(minutes):
    return "%02d:%02d" % divmod(int(minutes), 60)


def config(s=None):
    s = s or settings.load()
    d = settings.DEFAULTS

    def t(k):
        v = _hm(s.get(k))
        return v if v is not None else _hm(d[k])

    return dict(
        enabled=bool(s.get("break_enabled", True)),
        workdays_only=bool(s.get("break_workdays_only", True)),
        bring_front=bool(s.get("break_bring_front", True)),
        start=t("break_work_start"), end=t("break_work_end"),
        lunch_start=t("break_lunch_start"), lunch_end=t("break_lunch_end"),
        am=max(15, int(s.get("break_am_interval") or 60)),
        pm=max(15, int(s.get("break_pm_interval") or 30)),
        minutes=max(3, int(s.get("break_minutes") or 10)),
    )


def _bounds(cfg):
    st, ls, le, en = cfg["start"], cfg["lunch_start"], cfg["lunch_end"], cfg["end"]
    lunch = st < ls < le < en
    if not lunch:
        ls = le = en
    return st, ls, le, en, lunch


def _day_ts(day, minutes):
    return (datetime.datetime.combine(day, datetime.time(0, 0)) + datetime.timedelta(minutes=minutes)).timestamp()


def is_workday(day, cfg):
    return not (cfg["workdays_only"] and day.weekday() >= 5)


def slots_for(day, cfg):
    """某天的全部提醒点：上午从上班起每 am 分钟一次（午休前），下午从午休结束起每 pm 分钟一次（下班前）。"""
    if not is_workday(day, cfg):
        return []
    st, ls, le, en, lunch = _bounds(cfg)
    if st >= en:
        return []
    out = []
    t = st + cfg["am"]
    while t < ls:
        out.append(t)
        t += cfg["am"]
    if lunch:
        t = le + cfg["pm"]
        while t < en:
            out.append(t)
            t += cfg["pm"]
    return [dict(ts=_day_ts(day, m), hm=_fmt(m)) for m in out]


def _phase(day, now, cfg):
    st, ls, le, en, lunch = _bounds(cfg)
    nm = (now - _day_ts(day, 0)) / 60.0
    if nm < st:
        return "before"
    if nm >= en:
        return "after"
    if lunch and ls <= nm < le:
        return "lunch"
    return "work"


def _range_text(minutes):
    return "5–%d" % minutes if minutes > 5 else "%d" % minutes


class BreakTimer:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._on_event = None
        raw = load_json(paths.BREAKS_JSON, {})
        self._st = raw if isinstance(raw, dict) else {}
        with self._lock:
            self._ensure_day(time.time())

    # ---------------------------------------------------------------- 持久化 / 跨天
    def _save(self):
        try:
            atomic_write_json(paths.BREAKS_JSON, self._st)
        except OSError:
            pass

    def _ensure_day(self, now):
        today = datetime.date.fromtimestamp(now).isoformat()
        if self._st.get("date") == today:
            return
        hist = self._st.get("history") if isinstance(self._st.get("history"), dict) else {}
        if self._st.get("date"):
            log = self._st.get("log") or []
            hist[self._st["date"]] = dict(
                count=len(log), minutes=round(sum(e["end"] - e["start"] for e in log) / 60))
        hist = {k: hist[k] for k in sorted(hist)[-30:]}
        self._st = dict(date=today, handled={}, notified={}, log=[], snooze_until=None,
                        active=None, over_notified=False, history=hist)
        self._save()

    # ---------------------------------------------------------------- 计算
    @staticmethod
    def _key(ts):
        return str(int(ts))

    def _deadline(self, i, slots):
        dl = slots[i]["ts"] + GRACE_SECONDS
        if i + 1 < len(slots):
            dl = min(dl, slots[i + 1]["ts"])
        sn = self._st.get("snooze_until")
        if sn and sn >= slots[i]["ts"]:
            dl = max(dl, sn + 120)
        return dl

    def _candidate(self, now, slots):
        """最近一个已到点、还没处理、也还没过期的提醒点下标（忽略稍后提醒）。"""
        idx = None
        for i, s in enumerate(slots):
            if s["ts"] <= now:
                idx = i
            else:
                break
        if idx is None:
            return None
        if self._key(slots[idx]["ts"]) in (self._st.get("handled") or {}):
            return None
        if now >= self._deadline(idx, slots):
            return None
        return idx

    def _context(self, now):
        cfg = config()
        day = datetime.date.fromtimestamp(now)
        workday = is_workday(day, cfg)
        slots = slots_for(day, cfg) if cfg["enabled"] else []
        return cfg, day, workday, slots

    def _log_stats(self):
        log = self._st.get("log") or []
        return len(log), round(sum(e["end"] - e["start"] for e in log) / 60)

    def status(self):
        with self._lock:
            now = time.time()
            self._ensure_day(now)
            cfg, day, workday, slots = self._context(now)
            handled = self._st.get("handled") or {}
            active = self._st.get("active")
            sn = self._st.get("snooze_until")
            cand = self._candidate(now, slots)
            snoozed = bool(cand is not None and sn and now < sn)
            due = slots[cand] if (cand is not None and not active and not snoozed) else None
            items = []
            for i, s in enumerate(slots):
                k = self._key(s["ts"])
                if k in handled:
                    state = handled[k]
                elif i == cand:
                    state = "snoozed" if snoozed else ("due" if not active else "pending")
                elif s["ts"] > now:
                    state = "pending"
                else:
                    state = "missed"
                items.append(dict(ts=s["ts"], hm=s["hm"], state=state))
            nxt = next((s for s in slots if s["ts"] > now and self._key(s["ts"]) not in handled), None)
            count, mins = self._log_stats()
            st, ls, le, en, _ = _bounds(cfg)
            return dict(
                enabled=cfg["enabled"], workday=workday,
                phase=_phase(day, now, cfg) if workday else "rest",
                target_minutes=cfg["minutes"],
                work_start=_fmt(st), work_end=_fmt(en), lunch_start=_fmt(ls), lunch_end=_fmt(le),
                active_start=active,
                due=dict(ts=due["ts"], hm=due["hm"]) if due else None,
                next=dict(ts=nxt["ts"], hm=nxt["hm"]) if nxt else None,
                snooze_until=sn if (sn and now < sn) else None,
                count=count, minutes_total=mins,
                done=sum(1 for v in handled.values() if v == "done"), total=len(slots),
                slots=items,
            )

    # ---------------------------------------------------------------- 操作
    def start(self):
        with self._lock:
            now = time.time()
            self._ensure_day(now)
            if self._st.get("active"):
                return
            _, _, _, slots = self._context(now)
            handled = self._st.setdefault("handled", {})
            cand = self._candidate(now, slots)
            if cand is not None:
                handled[self._key(slots[cand]["ts"])] = "done"
            for s in slots:
                if s["ts"] > now:
                    if s["ts"] - now <= EARLY_MERGE_SECONDS and self._key(s["ts"]) not in handled:
                        handled[self._key(s["ts"])] = "done"
                    break
            self._st["active"] = now
            self._st["over_notified"] = False
            self._st["snooze_until"] = None
            self._save()

    def _end_locked(self, now, cap=None):
        act = self._st.get("active")
        if not act:
            return 0.0
        dur = max(0.0, now - act)
        if cap:
            dur = min(dur, cap)
        if dur >= 30:
            self._st.setdefault("log", []).append(dict(start=round(act), end=round(act + dur)))
        self._st["active"] = None
        self._st["over_notified"] = False
        self._save()
        return dur / 60.0

    def end(self):
        with self._lock:
            return self._end_locked(time.time())

    def skip(self):
        with self._lock:
            now = time.time()
            self._ensure_day(now)
            _, _, _, slots = self._context(now)
            cand = self._candidate(now, slots)
            if cand is not None:
                self._st.setdefault("handled", {})[self._key(slots[cand]["ts"])] = "skipped"
            self._st["snooze_until"] = None
            self._save()

    def snooze(self, minutes=5):
        with self._lock:
            self._st["snooze_until"] = time.time() + max(1, min(60, int(minutes))) * 60
            self._save()

    # ---------------------------------------------------------------- 后台定时
    def start_thread(self, on_event):
        self._on_event = on_event
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(5):
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    def _tick(self):
        events = []
        with self._lock:
            now = time.time()
            self._ensure_day(now)
            cfg, day, workday, slots = self._context(now)
            notified = self._st.setdefault("notified", {})
            act = self._st.get("active")
            if act:
                el = now - act
                if el >= AUTO_END_MINUTES * 60:
                    self._end_locked(now, cap=cfg["minutes"] * 60)
                    events.append(("auto_end", "放空已超过 %d 分钟，已自动结束计时（按 %d 分钟记录）"
                                   % (AUTO_END_MINUTES, cfg["minutes"])))
                elif not self._st.get("over_notified") and el >= cfg["minutes"] * 60:
                    self._st["over_notified"] = True
                    self._save()
                    events.append(("over", "已经放空 %d 分钟啦，该回来继续干活了 💪" % int(el // 60)))
            elif slots:
                cand = self._candidate(now, slots)
                sn = self._st.get("snooze_until")
                if cand is not None and not (sn and now < sn):
                    s = slots[cand]
                    k = self._key(s["ts"])
                    last = notified.get(k)
                    if last is None or (sn and last < sn <= now):
                        notified[k] = now
                        self._st["snooze_until"] = None
                        self._save()
                        count, _ = self._log_stats()
                        events.append(("due", "☕ %s 到点放空啦！起身走走、看看远处、喝口水，放空 %s 分钟（今日第 %d 次）"
                                       % (s["hm"], _range_text(cfg["minutes"]), count + 1)))

            if slots:
                st, ls, le, en, lunch = _bounds(cfg)
                count, mins = self._log_stats()
                infos = [("end", _day_ts(day, en), "🎉 下班啦！今天共放空 %d 次、%d 分钟，辛苦了～" % (count, mins))]
                if lunch:
                    infos.insert(0, ("lunch", _day_ts(day, ls), "🍚 午休时间到啦！好好吃饭休息，%s 再回来～" % _fmt(le)))
                for tag, ts, msg in infos:
                    if ts <= now < ts + INFO_WINDOW and tag not in notified:
                        notified[tag] = now
                        self._save()
                        events.append(("info", msg))

        if self._on_event:
            for kind, msg in events:
                try:
                    self._on_event(kind, msg)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
