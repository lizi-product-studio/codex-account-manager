"""命令行自检：打印当前状态（不显示任何 token），不打开窗口、不修改 Codex。
用法： python selftest.py
"""
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend import codex, paths  # noqa: E402
from backend.service import Service  # noqa: E402


def fmt(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def main():
    t = time.time()
    svc = Service()
    st = svc.get_state()
    print("CODEX_HOME :", paths.CODEX_HOME)
    print("存档目录   :", paths.STORE_DIR)
    print("快照数量   : %d   (耗时 %.1fs)" % (st["snapshots"], time.time() - t))
    c = st["current"]
    print("当前登录   :", c["email"] or "(未登录)", c["plan"], "已保存为:", c["saved_name"])
    print("订阅至     :", fmt(c["sub_until"]), "  凭证刷新:", fmt(c["last_refresh"]))
    u = c["usage"]
    if u:
        print("额度更新于 :", fmt(u["updated"]))
        for lim in u["limits"]:
            for w in lim["windows"]:
                print("   [%s] %s 已用 %.0f%%  重置于 %s" % (lim["id"], w["label"], w["used"], fmt(w["resets_at"])))
    print("已保存账号 :", len(st["accounts"]))
    for a in st["accounts"]:
        s = a["status"]
        print("   -", a["name"], a["email"], a["plan"], "当前" if a["is_current"] else "",
              "剩余=%s%% 状态=%s 新鲜度=%s 重置=%s 更新=%s" % (
                  s["remaining"], s["level"], s["freshness"], fmt(s["next_reset"]), fmt(s["updated"])))
    print("Codex 进程 :", codex.list_processes())
    print("启动入口   :", codex.package_family())
    print("时间线     :", [(fmt(e[0]), e[1].split("|")[0]) for e in svc.timeline.entries[-5:]])
    svc.close()


if __name__ == "__main__":
    main()
