"""业务编排：状态汇总、保存、切换、登录新账号。所有公开方法都是线程安全的。"""
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

from . import codex, paths, settings
from .auth import read_auth
from .store import AccountStore, StoreError
from .updater import Updater
from .usage import (Timeline, UsageScanner, account_status, burn_forecasts, estimate_switch_time,
                    seed_time, snap_from_rpc, summarize)
from .util import atomic_write_bytes, atomic_write_json, load_json


class Service:
    def __init__(self):
        paths.ensure_dirs()
        self.lock = threading.RLock()
        self._live_lock = threading.Lock()
        self.store = AccountStore()
        self.scanner = UsageScanner()
        self.timeline = Timeline()
        raw_live = load_json(paths.LIVE_USAGE, {})
        self.live_usage = raw_live if isinstance(raw_live, dict) else {}
        self._last_live_at = {}
        self.codex_running = codex.is_running()
        self.updater = Updater(app_version=paths.APP_VERSION)
        self._stop = threading.Event()
        threading.Thread(target=self._watch_codex, daemon=True).start()
        self.refresh_live_async()
        if settings.load().get("auto_check_update", True):
            self.updater.check_async(force=True, delay=4.0)

    # ---------------------------------------------------------------- 后台
    def _watch_codex(self):
        while not self._stop.wait(10):
            self.codex_running = codex.is_running()

    def close(self):
        self._stop.set()
        self.updater.close()

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _current():
        info, data = read_auth(paths.AUTH_FILE)
        try:
            mtime = os.path.getmtime(paths.AUTH_FILE)
        except OSError:
            mtime = None
        return info, data, mtime

    def _observe(self, cur, mtime, now):
        key = cur["key"] if cur["valid"] else ""
        tl = self.timeline
        if not tl.entries:
            ts = seed_time(self.scanner.snaps, cur["plan"], mtime, now) if key else now
            tl.append(ts, key, "seed")
        elif tl.current_key() != key:
            ts = estimate_switch_time(self.scanner.snaps, tl, tl.current_key(), tl.last_seen, mtime, now)
            tl.append(ts, key, "detected")
            if key:
                self.refresh_live_async(force=True, delay=2.0)
        tl.touch(now)

    def _current_low_remaining(self, key):
        snaps = self.live_usage.get(key)
        if not isinstance(snaps, list) or not snaps:
            return False
        for s in snaps:
            if not isinstance(s, list) or len(s) < 13:
                continue
            for u in (s[3], s[6]):
                if isinstance(u, (int, float)) and u >= 90:
                    return True
        return False

    def refresh_live(self, force=False):
        """通过官方 codex 读取当前登录账号的实时额度（只查当前这一个号，不碰其他号）。"""
        cur, _, _ = self._current()
        if not cur["valid"] or cur["mode"] != "chatgpt":
            return dict(ok=False, error="当前没有登录 ChatGPT 账号")
        key = cur["key"]
        now = time.time()
        if force:
            min_gap = 5
        elif self._current_low_remaining(key):
            min_gap = 60
        else:
            min_gap = 180
        if now - self._last_live_at.get(key, 0) < min_gap:
            return dict(ok=True, cached=True)
        if not self._live_lock.acquire(blocking=False):
            return dict(ok=True, busy=True)
        try:
            self._last_live_at[key] = now
            if not codex.find_codex_exe():
                return dict(ok=False, error="未找到本机 codex 可执行文件")
            acct_id, items = codex.read_live_rate_limits()
            if not items:
                return dict(ok=False, error="暂时无法从 Codex 读取实时额度")
            # 确认返回的账号确实是刚才的当前账号（防止中途切号串数据）
            cur2, _, _ = self._current()
            if not cur2["valid"] or cur2["key"] != key:
                return dict(ok=False, error="账号已切换")
            if acct_id and cur2["account_id"] and acct_id != cur2["account_id"]:
                return dict(ok=False, error="返回的账号不匹配")
            ts = time.time()
            snaps = [s for s in (snap_from_rpc(it, ts) for it in items) if s]
            if not snaps:
                return dict(ok=False, error="未返回额度数据")
            with self.lock:
                self.live_usage[key] = snaps
                atomic_write_json(paths.LIVE_USAGE, self.live_usage)
            return dict(ok=True, updated=ts)
        finally:
            self._live_lock.release()

    def refresh_live_async(self, force=False, delay=0.0):
        def _run():
            if delay > 0 and self._stop.wait(delay):
                return
            try:
                self.refresh_live(force=force)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _sync_current(self):
        """Codex 会自动刷新 token：把最新的 auth.json 写回对应存档，避免存档过期。"""
        cur, data, _ = self._current()
        if not cur["valid"] or not data:
            return None
        acc = self.store.find_by_key(cur["key"])
        if acc and acc["data"] != data:
            if (cur.get("last_refresh") or 0) >= (acc["info"].get("last_refresh") or 0):
                self.store.update_auth(acc["id"], data)
        return acc

    @staticmethod
    def _backup_auth():
        if not os.path.exists(paths.AUTH_FILE):
            return
        dst = os.path.join(paths.BACKUP_DIR, "auth-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copyfile(paths.AUTH_FILE, dst)
        olds = sorted((f for f in os.listdir(paths.BACKUP_DIR) if f.startswith("auth-")), reverse=True)
        for f in olds[20:]:
            try:
                os.remove(os.path.join(paths.BACKUP_DIR, f))
            except OSError:
                pass

    def _need_save(self):
        cur, _, _ = self._current()
        return cur["valid"] and not self.store.find_by_key(cur["key"])

    # ---------------------------------------------------------------- 查询
    def get_state(self):
        with self.lock:
            now = time.time()
            self.scanner.scan()
            cur, _, mtime = self._current()
            self._observe(cur, mtime, now)
            saved = self._sync_current()
            stored = self.store.list()
            known_accounts = [(a["info"]["key"], a["info"]["account_id"]) for a in stored if a["info"]["valid"]]
            if cur["valid"]:
                known_accounts.append((cur["key"], cur["account_id"]))
            usage = summarize(self.scanner.snaps, self.timeline, self.scanner.files, known_accounts, self.live_usage)
            forecasts = burn_forecasts(self.scanner.snaps, self.timeline, usage, now)

            accounts = []
            for a in stored:
                info = a["info"]
                u = usage.get(info["key"]) if info["valid"] else None
                accounts.append(dict(
                    id=a["id"], name=a["name"], note=a.get("note", ""),
                    valid=info["valid"], email=info["email"], plan=info["plan"],
                    sub_until=info["sub_until"], last_refresh=info["last_refresh"],
                    access_exp=info["access_exp"],
                    is_current=bool(cur["valid"] and info["valid"] and info["key"] == cur["key"]),
                    usage=u, status=account_status(u, now),
                ))
            cu = usage.get(cur["key"]) if cur["valid"] else None
            return dict(
                now=now,
                version=paths.APP_VERSION,
                platform=sys.platform,
                codex_running=self.codex_running,
                store_dir=paths.STORE_DIR,
                update=self.updater.get_status(),
                current=dict(
                    valid=cur["valid"], email=cur["email"], plan=cur["plan"],
                    sub_until=cur["sub_until"], last_refresh=cur["last_refresh"],
                    saved_id=saved["id"] if saved else None,
                    saved_name=saved["name"] if saved else None,
                    usage=cu, status=account_status(cu, now),
                    forecast=forecasts.get(cur["key"]) if cur["valid"] else None,
                ),
                accounts=accounts,
                snapshots=len(self.scanner.snaps),
            )

    # ---------------------------------------------------------------- 设置
    @staticmethod
    def get_settings():
        return dict(ok=True, settings=settings.load())

    @staticmethod
    def set_setting(key, value):
        return dict(ok=True, settings=settings.set_value(key, value))

    # ---------------------------------------------------------------- 更新
    def check_update(self, force=True):
        return self.updater.check_update(force=bool(force))

    def apply_update(self, force_reset=False):
        return self.updater.apply_update(force_reset=bool(force_reset))

    # ---------------------------------------------------------------- 操作
    def save_current(self, name, note=""):
        with self.lock:
            cur, data, _ = self._current()
            if not cur["valid"]:
                raise StoreError("当前 Codex 没有登录任何账号")
            acc = self.store.find_by_key(cur["key"])
            if acc:
                self._sync_current()
                return dict(ok=True, id=acc["id"], message="已更新“%s”的存档" % acc["name"])
            acc_id = self.store.add(name, data, note)
            return dict(ok=True, id=acc_id, message="已保存为“%s”" % name.strip())

    @staticmethod
    def _stop_hint():
        return "顶部菜单栏或程序坞" if sys.platform == "darwin" else "右下角托盘图标"

    def stop_codex(self):
        """分步切换 第 1 步：关闭 Codex。"""
        ok, rem = codex.stop()
        if not ok:
            extra = "（残留进程：%s）" % rem if rem else ""
            raise StoreError("Codex 没能完全关闭%s，请手动退出（包括%s）后再试" % (extra, self._stop_hint()))
        self.codex_running = False
        return dict(ok=True)

    def apply_switch(self, acc_id, force=False):
        """分步切换 第 2 步：同步当前凭证 → 备份 → 写入目标凭证（要求 Codex 已关闭）。"""
        with self.lock:
            target = self.store.get(acc_id)
            tinfo = target["info"]
            if not tinfo["valid"] or not target["data"]:
                raise StoreError("这个账号的存档无效，无法切换")
            if not force and self._need_save():
                return dict(ok=False, need_save=True, message="当前登录还没保存")
            if codex.list_processes():
                raise StoreError("检测到 Codex 仍有进程在运行，请先关闭再切换")
            self._sync_current()
            self._backup_auth()
            atomic_write_bytes(paths.AUTH_FILE, target["data"])
            self.timeline.append(time.time(), tinfo["key"], "switch")
            self.refresh_live_async(force=True, delay=0.2)
            return dict(ok=True, message="已切换到“%s”" % target["name"])

    def start_codex(self):
        """分步切换 第 3 步：启动 Codex。"""
        if codex.start():
            self.codex_running = True
            return dict(ok=True)
        return dict(ok=False, error="没找到 Codex 桌面版，请手动打开")

    def switch(self, acc_id, launch=True, force=False):
        with self.lock:
            target = self.store.get(acc_id)
            tinfo = target["info"]
            if not tinfo["valid"] or not target["data"]:
                raise StoreError("这个账号的存档无效，无法切换")
            cur, _, _ = self._current()
            if cur["valid"] and cur["key"] == tinfo["key"]:
                return dict(ok=True, message="现在登录的就是“%s”" % target["name"])
            if not force and self._need_save():
                return dict(ok=False, need_save=True, message="当前登录还没保存")

            ok, rem = codex.stop()
            if not ok:
                extra = "（残留进程：%s）" % rem if rem else ""
                raise StoreError("Codex 没能完全关闭%s，请手动退出（包括%s）后再试" % (extra, self._stop_hint()))
            self.codex_running = False
            self._sync_current()            # 关闭后再同步一次，拿到 Codex 最后刷新的 token
            self._backup_auth()
            atomic_write_bytes(paths.AUTH_FILE, target["data"])
            self.timeline.append(time.time(), tinfo["key"], "switch")
            msg = "已切换到“%s”" % target["name"]
            if launch:
                if codex.start():
                    self.codex_running = True
                    msg += "，Codex 正在启动"
                else:
                    msg += "，但没找到 Codex 桌面版，请手动打开"
            return dict(ok=True, message=msg)

    def add_new(self, launch=True, force=False):
        with self.lock:
            if not force and self._need_save():
                return dict(ok=False, need_save=True, message="当前登录还没保存")
            ok, rem = codex.stop()
            if not ok:
                extra = "（残留进程：%s）" % rem if rem else ""
                raise StoreError("Codex 没能完全关闭%s，请手动退出（包括%s）后再试" % (extra, self._stop_hint()))
            self.codex_running = False
            self._sync_current()
            self._backup_auth()
            if os.path.exists(paths.AUTH_FILE):
                os.remove(paths.AUTH_FILE)
            self.timeline.append(time.time(), "", "logout")
            if launch:
                codex.start()
            return dict(ok=True, message="已退出登录。请在 Codex 中登录新账号，登录后回到这里保存")

    def rename(self, acc_id, name):
        with self.lock:
            self.store.rename(acc_id, name)
            return dict(ok=True, message="已重命名")

    def set_note(self, acc_id, note):
        with self.lock:
            self.store.set_note(acc_id, note)
            return dict(ok=True, message="备注已更新")

    def move(self, acc_id, delta):
        with self.lock:
            self.store.move(acc_id, int(delta))
            return dict(ok=True)

    def delete(self, acc_id):
        with self.lock:
            self.store.delete(acc_id)
            return dict(ok=True, message="已删除（文件已移到 _backup 目录）")

    @staticmethod
    def open_store():
        if sys.platform == "win32":
            os.startfile(paths.STORE_DIR)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", paths.STORE_DIR], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", paths.STORE_DIR], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return dict(ok=True)

    @staticmethod
    def open_usage_page():
        webbrowser.open(paths.OFFICIAL_USAGE_URL)
        return dict(ok=True)
