"""放空小站：想走就走的放空计时器。

点「去放空」开始计时，回来点「已回来」结束并记录；自动统计每次时长、每日次数、总时长，
附带最近 7 天柱状图、连续天数、放空等级与成就。数据保存在 ~/.codex-accounts/breaks.json。
"""
import datetime
import threading
import time
import traceback

from . import paths, settings
from .util import atomic_write_json, load_json

KEEP_DAYS = 365
AUTO_END_SECONDS = 3 * 3600   # 放空超过 3 小时视为忘了点「已回来」，自动结束
MIN_RECORD_SECONDS = 20       # 少于 20 秒视为误点，不记录

TAGS = [
    ("water", "☕ 喝水"), ("walk", "🚶 走走"), ("wc", "🚻 厕所"), ("air", "🌿 透气"),
    ("daze", "💭 发呆"), ("phone", "📱 刷手机"), ("chat", "💬 聊天"), ("snack", "🍪 零食"),
]
TAG_IDS = {t[0] for t in TAGS}
LEVELS = [(0, "放空新手"), (5, "放空学徒"), (20, "摸鱼达人"), (50, "放空高手"),
          (100, "放空大师"), (200, "禅定宗师"), (500, "放空之神")]
WEEKDAYS = "一二三四五六日"
ONE_DAY = datetime.timedelta(days=1)


def _day(ts):
    return datetime.date.fromtimestamp(ts)


def _cfg():
    s = settings.load()
    return dict(
        enabled=bool(s.get("break_enabled", True)),
        minutes=int(s.get("break_minutes") or 10),
        goal=int(s.get("break_goal") or 8),
    )


def _streaks(days, today):
    """返回 (当前连续天数, 历史最长连续天数)。今天还没放空时，从昨天往前数。"""
    cur = 0
    d = today if today in days else today - ONE_DAY
    while d in days:
        cur += 1
        d -= ONE_DAY
    best, run, prev = 0, 0, None
    for d in sorted(days):
        run = run + 1 if (prev is not None and d - prev == ONE_DAY) else 1
        best = max(best, run)
        prev = d
    return cur, best


class BreakTimer:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._on_event = None
        raw = load_json(paths.BREAKS_JSON, {})
        self._st = self._migrate(raw if isinstance(raw, dict) else {})

    # ---------------------------------------------------------------- 持久化
    @staticmethod
    def _valid(r):
        return isinstance(r, dict) and isinstance(r.get("start"), (int, float)) \
            and isinstance(r.get("end"), (int, float)) and r["end"] > r["start"]

    @classmethod
    def _migrate(cls, raw):
        if raw.get("v") == 2:
            recs = [r for r in raw.get("records") or [] if cls._valid(r)]
            act = raw.get("active") if isinstance(raw.get("active"), dict) else None
            return dict(v=2, records=recs, active=act, over_notified=bool(raw.get("over_notified")))
        # v1（定点提醒版）→ v2：保留已有放空记录
        recs = [dict(start=e["start"], end=e["end"], tag="") for e in raw.get("log") or [] if cls._valid(e)]
        act = raw.get("active")
        act = dict(start=float(act), tag="") if isinstance(act, (int, float)) else None
        return dict(v=2, records=recs, active=act, over_notified=False)

    def _save(self):
        cutoff = time.time() - KEEP_DAYS * 86400
        self._st["records"] = [r for r in self._st["records"] if r["start"] >= cutoff]
        try:
            atomic_write_json(paths.BREAKS_JSON, self._st)
        except OSError:
            pass

    # ---------------------------------------------------------------- 统计
    def status(self):
        with self._lock:
            now = time.time()
            cfg = _cfg()
            today = _day(now)
            recs = sorted(self._st["records"], key=lambda r: r["start"])
            by_day = {}
            for r in recs:
                e = by_day.setdefault(_day(r["start"]), [0, 0.0])
                e[0] += 1
                e[1] += r["end"] - r["start"]

            t_recs = [r for r in recs if _day(r["start"]) == today]
            durs = [r["end"] - r["start"] for r in t_recs]
            tag_counts = {}
            for r in t_recs:
                if r.get("tag"):
                    tag_counts[r["tag"]] = tag_counts.get(r["tag"], 0) + 1

            week = []
            for i in range(6, -1, -1):
                d = today - datetime.timedelta(days=i)
                c, sec = by_day.get(d, (0, 0.0))
                week.append(dict(date=d.isoformat(), label="今天" if i == 0 else "周" + WEEKDAYS[d.weekday()],
                                 count=c, total_sec=round(sec), today=(i == 0)))

            streak, best_streak = _streaks(set(by_day), today)
            total_cnt = len(recs)
            total_sec = sum(r["end"] - r["start"] for r in recs)
            idx = max(i for i, (th, _) in enumerate(LEVELS) if total_cnt >= th)
            level = dict(lv=idx + 1, title=LEVELS[idx][1],
                         next_at=LEVELS[idx + 1][0] if idx + 1 < len(LEVELS) else None)

            act = self._st.get("active")
            return dict(
                enabled=cfg["enabled"], target_minutes=cfg["minutes"], goal=cfg["goal"],
                active=dict(start=act["start"], tag=act.get("tag") or "") if act else None,
                today=dict(
                    count=len(t_recs), total_sec=round(sum(durs)),
                    avg_sec=round(sum(durs) / len(durs)) if durs else 0,
                    longest_sec=round(max(durs)) if durs else 0,
                    records=[dict(start=r["start"], end=r["end"], tag=r.get("tag") or "", auto=bool(r.get("auto")))
                             for r in reversed(t_recs)],
                    tags=tag_counts,
                ),
                since_last=t_recs[-1]["end"] if t_recs else None,
                week=week, streak=streak, best_streak=best_streak,
                all=dict(count=total_cnt, total_sec=round(total_sec), days=len(by_day)),
                level=level,
                badges=self._badges(recs, by_day, cfg, best_streak, total_cnt),
                tags=[dict(id=i, label=l) for i, l in TAGS],
            )

    @staticmethod
    def _badges(recs, by_day, cfg, best_streak, total_cnt):
        durs = [r["end"] - r["start"] for r in recs]
        tags = {r.get("tag") for r in recs if r.get("tag")}
        hours = [datetime.datetime.fromtimestamp(r["start"]).hour for r in recs]
        day_counts = [c for c, _ in by_day.values()]
        items = [
            ("first", "🌱", "初次放空", "完成第一次放空", total_cnt >= 1),
            ("goal", "🎯", "今日达标", "单日放空次数达到每日目标", any(c >= cfg["goal"] for c in day_counts)),
            ("flash", "⚡", "闪电充电", "单次放空不超过 3 分钟", any(d <= 180 for d in durs)),
            ("deep", "🧘", "深度放空", "单次放空超过 15 分钟", any(d >= 900 for d in durs)),
            ("early", "🌅", "早安放空", "上午 10 点前去放空一次", any(5 <= h < 10 for h in hours)),
            ("rainbow", "🌈", "花样放空", "用过 5 种不同的放空理由", len(tags) >= 5),
            ("streak3", "🔥", "三天不断", "连续 3 天都有放空", best_streak >= 3),
            ("streak7", "🏆", "一周坚持", "连续 7 天都有放空", best_streak >= 7),
            ("crazy", "🌀", "放空狂人", "单日放空 12 次以上", any(c >= 12 for c in day_counts)),
            ("hundred", "💯", "百次放空", "累计放空 100 次", total_cnt >= 100),
        ]
        return [dict(id=i, icon=ic, name=n, desc=d, got=bool(g)) for i, ic, n, d, g in items]

    # ---------------------------------------------------------------- 操作
    def start(self, tag=""):
        with self._lock:
            if self._st.get("active"):
                return
            self._st["active"] = dict(start=round(time.time(), 1), tag=tag if tag in TAG_IDS else "")
            self._st["over_notified"] = False
            self._save()

    def _finish(self, end_ts, auto=False):
        act = self._st.get("active")
        if not act:
            return 0.0
        dur = max(0.0, end_ts - act["start"])
        if dur >= MIN_RECORD_SECONDS:
            rec = dict(start=act["start"], end=round(end_ts, 1), tag=act.get("tag") or "")
            if auto:
                rec["auto"] = True
            self._st["records"].append(rec)
        self._st["active"] = None
        self._st["over_notified"] = False
        self._save()
        return dur

    def end(self):
        """结束放空并记录，返回本次时长（秒）。"""
        with self._lock:
            return self._finish(time.time())

    def cancel(self):
        with self._lock:
            self._st["active"] = None
            self._st["over_notified"] = False
            self._save()

    def delete(self, start):
        with self._lock:
            start = float(start)
            self._st["records"] = [r for r in self._st["records"] if abs(r["start"] - start) >= 1]
            self._save()

    # ---------------------------------------------------------------- 后台：超时提醒
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
            act = self._st.get("active")
            if act:
                now = time.time()
                cfg = _cfg()
                el = now - act["start"]
                if el >= AUTO_END_SECONDS:
                    self._finish(act["start"] + cfg["minutes"] * 60, auto=True)
                    events.append(("auto_end", "放空计时超过 3 小时，已自动结束（按 %d 分钟记录）。下次回来记得点「已回来」～"
                                   % cfg["minutes"]))
                elif not self._st.get("over_notified") and el >= cfg["minutes"] * 60:
                    self._st["over_notified"] = True
                    self._save()
                    events.append(("over", "已经放空 %d 分钟啦～ 回来后记得点「已回来」记录一下" % int(el // 60)))
        if self._on_event:
            for kind, msg in events:
                try:
                    self._on_event(kind, msg)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
