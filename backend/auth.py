"""解析 Codex 的 auth.json（只在本地解码 JWT 的 payload，不验证、不联网）。"""
import base64
import json

from .util import iso_to_epoch, read_bytes


def _jwt_payload(token):
    if not isinstance(token, str) or token.count(".") < 2:
        return {}
    seg = token.split(".")[1]
    seg += "=" * (-len(seg) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(seg.encode("ascii")))
    except Exception:
        return {}


def empty_info():
    return dict(valid=False, email="", name="", plan="", account_id="", user_id="", key="",
                last_refresh=None, sub_until=None, sub_checked=None, access_exp=None, mode="")


def parse_auth(data):
    info = empty_info()
    if not data:
        return info
    try:
        j = json.loads(data.decode("utf-8"))
    except Exception:
        return info
    if not isinstance(j, dict):
        return info
    tokens = j.get("tokens") or {}
    claims = _jwt_payload(tokens.get("id_token"))
    if claims:
        a = claims.get("https://api.openai.com/auth") or {}
        ac = _jwt_payload(tokens.get("access_token"))
        ac_a = ac.get("https://api.openai.com/auth") or {}
        plan = a.get("chatgpt_plan_type") or ""
        ac_plan = ac_a.get("chatgpt_plan_type") or ""
        if ac_plan and (not plan or (ac.get("iat") or 0) > (claims.get("iat") or 0)):
            plan = ac_plan
        info.update(
            email=claims.get("email") or "",
            name=claims.get("name") or "",
            plan=plan,
            account_id=tokens.get("account_id") or a.get("chatgpt_account_id") or "",
            user_id=a.get("chatgpt_user_id") or a.get("user_id") or claims.get("sub") or "",
            sub_until=iso_to_epoch(a.get("chatgpt_subscription_active_until")),
            sub_checked=iso_to_epoch(a.get("chatgpt_subscription_last_checked")),
            last_refresh=iso_to_epoch(j.get("last_refresh")),
            mode="chatgpt",
        )
        exp = ac.get("exp")
        if isinstance(exp, (int, float)):
            info["access_exp"] = float(exp)
        if info["email"]:
            info["key"] = "%s|%s" % (info["email"].lower(), info["account_id"])
            info["valid"] = True
    elif j.get("OPENAI_API_KEY"):
        tail = str(j["OPENAI_API_KEY"])[-4:]
        info.update(email="API Key …" + tail, plan="api-key", key="apikey|" + tail,
                    valid=True, mode="apikey")
    return info


def read_auth(path):
    """返回 (info, 原始字节或 None)。"""
    data = read_bytes(path)
    return parse_auth(data), data
