"""系统托盘主动提醒：快用完 / 已重置恢复 / 订阅临期 / 检测到未保存新登录。

所有提醒都按周期去重并持久化到 state.json，重启后也不会重复刷屏。
"""
import time

from . import paths, settings
from .util import atomic_write_json, load_json


class Notifier:
    def __init__(self):
        st = load_json(paths.NOTIFIED_JSON, [])
        self._sent = set(st) if isinstance(st, list) else set()
        self._prev_levels = {}
        self._initialized = False

    def _save(self):
        atomic_write_json(paths.NOTIFIED_JSON, sorted(self._sent)[-200:])

    def _once(self, token, msg, send):
        if token in self._sent:
            return
        self._sent.add(token)
        self._save()
        send(msg)

    def check(self, state, send):
        if not state:
            return
        now = state.get("now") or time.time()
        enabled = bool(settings.load().get("notify_enabled", True))

        # 第一次加载只记录基线（避免刚启动就对历史上已重置的旧号误发通知）
        accounts = state.get("accounts") or []
        if not self._initialized:
            for a in accounts:
                st = a.get("status") or {}
                self._prev_levels[a["id"]] = (st.get("level"), st.get("recover_at"))
            self._initialized = True
            if not enabled:
                return
            # 刚启动时仅检查：订阅临期、当前号极低额度
            self._check_ongoing(state, now, send)
            return

        if not enabled:
            for a in accounts:
                st = a.get("status") or {}
                self._prev_levels[a["id"]] = (st.get("level"), st.get("recover_at"))
            return

        # 1. 检测“已用尽 → 已重置恢复”
        for a in accounts:
            st = a.get("status") or {}
            prev_level, prev_rec = self._prev_levels.get(a["id"], (None, None))
            cur_level = st.get("level")
            if prev_level == "out" and (st.get("freshness") == "reset" or (cur_level in ("ok", "low") and (st.get("remaining") or 0) >= 50)):
                bucket = int((prev_rec or now) // 300)
                self._once("reset:%s:%d" % (a["id"], bucket),
                           "「%s」的额度已重置恢复，可以正常使用了。" % a["name"], send)
            self._prev_levels[a["id"]] = (cur_level, st.get("recover_at"))

        self._check_ongoing(state, now, send)

    def _check_ongoing(self, state, now, send):
        # 2. 当前账号剩余额度 <= 15%（仅实时数据）
        for a in state.get("accounts") or []:
            st = a.get("status") or {}
            if a.get("is_current") and st.get("freshness") == "live":
                rem = st.get("remaining")
                if rem is not None and rem <= 15:
                    bucket = int((st.get("next_reset") or st.get("recover_at") or now) // 300)
                    label = st.get("bottleneck") or ""
                    if rem <= 0:
                        msg = "当前账号「%s」%s额度已用尽。" % (a["name"], label)
                    else:
                        msg = "当前账号「%s」%s额度仅剩 %d%%。" % (a["name"], label, rem)
                    self._once("low:%s:%d:%s" % (a["id"], bucket, "0" if rem <= 0 else "15"), msg, send)

            # 3. 订阅 3 天内到期
            sub = a.get("sub_until")
            if sub and 0 < sub - now <= 3 * 86400:
                days = max(1, int((sub - now + 43200) // 86400))
                self._once("sub:%s:%d" % (a["id"], int(sub // 86400)),
                           "「%s」订阅将在 %d 天内到期，记得检查续费。" % (a["name"], days), send)

        # 4. 后台检测到未保存的新登录
        c = state.get("current") or {}
        if c.get("valid") and not c.get("saved_id") and c.get("email"):
            self._once("unsaved:%s:%d" % (c["email"].lower(), int(c.get("last_refresh") or 0)),
                       "检测到新登录账号 %s，点开托盘图标即可保存。" % c["email"], send)

        # 5. 检测到代码仓库新版本
        u = state.get("update") or {}
        if u.get("supported") and u.get("has_update") and u.get("remote_sha"):
            commits = u.get("commits") or []
            raw_latest = commits[0] if commits else ""
            latest = raw_latest.split(" ", 1)[1] if " " in raw_latest else (raw_latest or u.get("remote_short") or "新提交")
            self._once("update:%s" % u["remote_sha"],
                       "发现新版本（%d 项更新）：%s。打开窗口即可一键更新。" % (u.get("behind") or 1, latest), send)
