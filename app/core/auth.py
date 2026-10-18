"""登录认证：密码校验 + httpOnly Cookie 签名会话令牌。

设计（匹配私有化内网部署 + 前端 React SPA）：
- 会话令牌为自包含签名 token：`payload.b64.signature_b64`
    - payload = {username, exp(epoch秒)}
    - signature = HMAC-SHA256(auth_secret, payload_b64)，恒定时间比较防时序攻击
- 存于 httpOnly Cookie（JS 不可读，防 XSS 窃取）；前端请求自动携带
- 密码用 secrets.compare_digest 恒定时间比较，避免时序旁路
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import json
import secrets
import time

from starlette.responses import Response


class AuthService:
    """依赖 Settings：负责签发/校验/登出 httpOnly 会话 Cookie。"""

    def __init__(self, username: str, password: str, secret: str, cookie_name: str, session_hours: float) -> None:
        self._username = username
        self._password = password
        self._secret = secret.encode("utf-8")
        self.cookie_name = cookie_name
        self.session_hours = session_hours

    # ---------- 校验 ----------

    def check_credentials(self, username: str, password: str) -> bool:
        """恒定时间比较账号密码。"""
        user_ok = secrets.compare_digest(username or "", self._username)
        pwd_ok = secrets.compare_digest(password or "", self._password)
        return user_ok and pwd_ok

    # ---------- 令牌 ----------

    def _sign(self, payload_b64: str) -> str:
        return hmac.new(self._secret, payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()

    def _urlsafe_b64(self, raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _urlsafe_unb64(self, data: str) -> bytes:
        pad = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + pad)

    def issue_token(self, username: str) -> str:
        """签发签名令牌 payload.signature。"""
        payload = {"u": username, "exp": int(time.time()) + int(self.session_hours * 3600)}
        payload_b64 = self._urlsafe_b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = self._sign(payload_b64)
        return f"{payload_b64}.{sig}"

    def verify_token(self, token: str) -> str | None:
        """校验令牌；合法且未过期返回用户名，否则 None。"""
        try:
            payload_b64, sig = token.rsplit(".", 1)
            expected = self._sign(payload_b64)
            if not hmac.compare_digest(expected, sig):
                return None
            payload = json.loads(self._urlsafe_unb64(payload_b64))
            username = payload.get("u")
            exp = int(payload.get("exp", 0))
            if not username or time.time() > exp:
                return None
            return str(username)
        except Exception:
            return None

    # ---------- Cookie ----------

    def set_session_cookie(self, response: Response) -> None:
        """登录成功：签入 httpOnly 会话 Cookie。"""
        token = self.issue_token(self._username)
        response.set_cookie(
            key=self.cookie_name,
            value=token,
            max_age=int(self.session_hours * 3600),
            httponly=True,
            secure=False,  # 内网 http 部署；若上 https 应改 True
            samesite="lax",
            path="/",
        )

    def clear_session_cookie(self, response: Response) -> None:
        """登出：清除会话 Cookie。"""
        response.delete_cookie(key=self.cookie_name, path="/")

    def session_from_cookie(self, cookie_value: str | None) -> str | None:
        """从请求 Cookie 值解析当前登录用户名；未登录/无效返回 None。"""
        if not cookie_value:
            return None
        return self.verify_token(cookie_value)