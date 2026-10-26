"""Microsoft account login (device code flow) for Minecraft: Java Edition.

The flow: Microsoft OAuth device code -> Xbox Live -> XSTS -> Minecraft
services. Only the refresh token and the Minecraft profile are persisted.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .net import fetch_json, post_form, post_json

DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
SCOPE = "XboxLive.signin offline_access"

XSTS_MESSAGES = {
    2148916233: "该 Microsoft 账户还没有 Xbox 账户，请先到 xbox.com 创建。",
    2148916235: "该账户所在地区不支持 Xbox Live。",
    2148916236: "该账户需要完成成人验证（Xbox 网页登录一次即可）。",
    2148916237: "该账户需要完成成人验证（Xbox 网页登录一次即可）。",
    2148916238: "未成年账户需要先加入 Microsoft 家庭组。",
}


class AuthError(Exception):
    def __init__(self, message: str, code: str | int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    message: str


@dataclass
class AuthSession:
    username: str
    uuid: str
    access_token: str
    refresh_token: str
    expires_at: float
    client_id: str
    xuid: str = ""
    user_type: str = "msa"

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "AuthSession":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


def offline_uuid(name: str) -> str:
    """Deterministic offline UUID (the ``OfflinePlayer:<name>`` convention)."""
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    value = digest.hex()
    return f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:]}"


def _jwt_xuid(token: str) -> str:
    """Read the ``xuid`` claim from an XSTS JWT without verifying it (display only)."""
    try:
        import base64

        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return str(data.get("xuid", ""))
    except Exception:  # noqa: BLE001
        return ""


class MicrosoftAuth:
    def __init__(self, client_id: str, accounts_file: Path | None = None) -> None:
        self.client_id = client_id
        self.accounts_file = Path(accounts_file) if accounts_file else None

    # -- device code -----------------------------------------------------
    def start_device_code(self) -> DeviceCode:
        status, data = post_form(
            DEVICE_CODE_URL,
            {"client_id": self.client_id, "scope": SCOPE},
        )
        if status != 200 or "device_code" not in data:
            raise AuthError(
                f"无法开始微软登录：{data.get('error_description') or data.get('error') or status}",
                data.get("error"),
            )
        return DeviceCode(
            device_code=data["device_code"],
            user_code=data.get("user_code", ""),
            verification_uri=data.get("verification_uri", "https://microsoft.com/link"),
            expires_in=int(data.get("expires_in", 900)),
            interval=int(data.get("interval", 5)),
            message=data.get("message", ""),
        )

    def wait_for_login(
        self,
        device: DeviceCode,
        on_status: Callable[[str], None] | None = None,
    ) -> AuthSession:
        deadline = time.time() + device.expires_in
        interval = max(1, device.interval)
        while time.time() < deadline:
            time.sleep(interval)
            status, data = post_form(
                TOKEN_URL,
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self.client_id,
                    "device_code": device.device_code,
                },
            )
            if status == 200 and "access_token" in data:
                return self._finish_login(
                    data["access_token"],
                    data.get("refresh_token", ""),
                    int(data.get("expires_in", 3600)),
                )
            error = data.get("error", "")
            if error == "authorization_pending":
                if on_status:
                    on_status("等待在浏览器中完成登录…")
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error in ("expired_token", "authorization_declined", "bad_verification_code"):
                raise AuthError("登录已取消或设备码过期，请重试。", error)
            raise AuthError(f"微软登录失败：{data.get('error_description') or error or status}", error)
        raise AuthError("登录超时，请重试。", "expired_token")

    # -- token exchange --------------------------------------------------
    def _finish_login(self, ms_access: str, refresh_token: str, expires_in: int) -> AuthSession:
        xbl_token, uhs = self._xbox_live(ms_access)
        xsts_token, xsts_uhs, xuid = self._xsts(xbl_token)
        mc = self._minecraft_login(xsts_uhs or uhs, xsts_token)
        profile = self._profile(mc["access_token"])
        session = AuthSession(
            username=profile.get("name", "Player"),
            uuid=profile.get("id", ""),
            access_token=mc["access_token"],
            refresh_token=refresh_token,
            expires_at=time.time() + int(mc.get("expires_in", expires_in)) - 60,
            client_id=self.client_id,
            xuid=xuid,
        )
        self._save(session)
        return session

    def _xbox_live(self, ms_access: str) -> tuple[str, str]:
        status, data = post_json(
            XBL_URL,
            {
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": f"d={ms_access}",
                },
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
            },
        )
        if status != 200 or "Token" not in data:
            raise AuthError(f"Xbox Live 认证失败：{data.get('error') or status}", status)
        uhs = ((data.get("DisplayClaims") or {}).get("xui") or [{}])[0].get("uhs", "")
        return data["Token"], uhs

    def _xsts(self, xbl_token: str) -> tuple[str, str, str]:
        status, data = post_json(
            XSTS_URL,
            {
                "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
                "RelyingParty": "rp://api.minecraftservices.com/",
                "TokenType": "JWT",
            },
        )
        if status != 200 or "Token" not in data:
            xerr = data.get("XErr")
            message = XSTS_MESSAGES.get(int(xerr), f"XSTS 授权失败（{xerr or status}）") if xerr else f"XSTS 授权失败（{status}）"
            raise AuthError(message, xerr)
        uhs = ((data.get("DisplayClaims") or {}).get("xui") or [{}])[0].get("uhs", "")
        return data["Token"], uhs, _jwt_xuid(data["Token"])

    def _minecraft_login(self, uhs: str, xsts_token: str) -> dict:
        status, data = post_json(
            MC_LOGIN_URL,
            {"identityToken": f"XBL3.0 x={uhs};{xsts_token}"},
        )
        if status != 200 or "access_token" not in data:
            raise AuthError(f"Minecraft 登录失败：{data.get('errorMessage') or data.get('error') or status}", status)
        return data

    def _profile(self, mc_access: str) -> dict:
        try:
            return fetch_json(MC_PROFILE_URL, headers={"Authorization": f"Bearer {mc_access}"})
        except Exception as exc:  # noqa: BLE001
            raise AuthError("该账户没有 Minecraft: Java Edition（未购买或未创建角色）。") from exc

    # -- refresh ---------------------------------------------------------
    def refresh(self, session: AuthSession) -> AuthSession:
        status, data = post_form(
            TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": session.refresh_token,
                "scope": SCOPE,
            },
        )
        if status != 200 or "access_token" not in data:
            raise AuthError("登录已过期，请重新登录 Microsoft 账户。", data.get("error"))
        return self._finish_login(
            data["access_token"],
            data.get("refresh_token", session.refresh_token),
            int(data.get("expires_in", 3600)),
        )

    # -- persistence -----------------------------------------------------
    def _save(self, session: AuthSession) -> None:
        if self.accounts_file is None:
            return
        self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
        accounts = self.load_accounts()
        accounts = [a for a in accounts if a.uuid != session.uuid]
        accounts.insert(0, session)
        self.accounts_file.write_text(
            json.dumps([a.to_dict() for a in accounts], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_accounts(self) -> list[AuthSession]:
        if self.accounts_file is None or not self.accounts_file.is_file():
            return []
        try:
            raw = json.loads(self.accounts_file.read_text(encoding="utf-8"))
            return [AuthSession.from_dict(item) for item in raw]
        except (OSError, ValueError, TypeError):
            return []
