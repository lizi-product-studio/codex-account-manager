"""账号存档：accounts.json（名称/备注/顺序） + accounts/<id>.auth.json（凭证）。"""
import os
import secrets
import shutil
import time

from . import paths
from .auth import read_auth, parse_auth
from .util import atomic_write_bytes, atomic_write_json, load_json


class StoreError(Exception):
    pass


class AccountStore:
    def __init__(self):
        meta = load_json(paths.ACCOUNTS_JSON, None)
        if not isinstance(meta, dict) or not isinstance(meta.get("accounts"), list):
            meta = {"version": 1, "accounts": []}
        self.meta = meta

    # ---------------------------------------------------------------- 基础
    def _save(self):
        atomic_write_json(paths.ACCOUNTS_JSON, self.meta)

    @staticmethod
    def file_of(acc_id):
        return os.path.join(paths.ACCOUNTS_DIR, "%s.auth.json" % acc_id)

    def _meta(self, acc_id):
        for a in self.meta["accounts"]:
            if a["id"] == acc_id:
                return a
        raise StoreError("账号不存在")

    def list(self):
        out = []
        for a in self.meta["accounts"]:
            info, data = read_auth(self.file_of(a["id"]))
            out.append(dict(a, info=info, data=data))
        return out

    def get(self, acc_id):
        a = self._meta(acc_id)
        info, data = read_auth(self.file_of(acc_id))
        return dict(a, info=info, data=data)

    def find_by_key(self, key):
        if not key:
            return None
        for a in self.list():
            if a["info"]["valid"] and a["info"]["key"] == key:
                return a
        return None

    def _check_name(self, name, exclude_id=None):
        name = (name or "").strip()
        if not name:
            raise StoreError("名称不能为空")
        if len(name) > 40:
            raise StoreError("名称太长了（最多 40 个字）")
        for a in self.meta["accounts"]:
            if a["id"] != exclude_id and a["name"] == name:
                raise StoreError("已经有叫“%s”的账号了" % name)
        return name

    # ---------------------------------------------------------------- 修改
    def add(self, name, data, note=""):
        name = self._check_name(name)
        info = parse_auth(data)
        if not info["valid"]:
            raise StoreError("凭证无效，无法保存")
        if self.find_by_key(info["key"]):
            raise StoreError("这个账号已经保存过了")
        acc_id = secrets.token_hex(4)
        atomic_write_bytes(self.file_of(acc_id), data)
        self.meta["accounts"].append(dict(id=acc_id, name=name, note=note or "", created=time.time()))
        self._save()
        return acc_id

    def update_auth(self, acc_id, data):
        atomic_write_bytes(self.file_of(acc_id), data)

    def rename(self, acc_id, name):
        a = self._meta(acc_id)
        a["name"] = self._check_name(name, exclude_id=acc_id)
        self._save()

    def set_note(self, acc_id, note):
        a = self._meta(acc_id)
        a["note"] = (note or "").strip()[:200]
        self._save()

    def move(self, acc_id, delta):
        accs = self.meta["accounts"]
        i = next((k for k, a in enumerate(accs) if a["id"] == acc_id), None)
        if i is None:
            raise StoreError("账号不存在")
        j = max(0, min(len(accs) - 1, i + delta))
        accs.insert(j, accs.pop(i))
        self._save()

    def delete(self, acc_id):
        a = self._meta(acc_id)
        src = self.file_of(acc_id)
        if os.path.exists(src):
            safe = "".join(c if c.isalnum() else "_" for c in a["name"])[:30]
            dst = os.path.join(paths.BACKUP_DIR, "deleted-%s-%s-%s.auth.json"
                               % (safe, acc_id, time.strftime("%Y%m%d-%H%M%S")))
            shutil.move(src, dst)
        self.meta["accounts"] = [x for x in self.meta["accounts"] if x["id"] != acc_id]
        self._save()
