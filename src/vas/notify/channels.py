"""Каналы уведомлений: Telegram, почта, push через FCM. Без ключей пишут в лог."""

from typing import Protocol

import httpx
import structlog

from vas.core import upstream
from vas.settings import Settings

log = structlog.get_logger()


class Notifier(Protocol):
    async def telegram(self, chat_id: int, text: str) -> None: ...

    async def email(self, to: str, subject: str, text: str) -> None: ...

    async def push(self, device_token: str, title: str, body: str) -> bool:
        """False — токен устройства больше не действует, его надо забыть."""
        ...


class HttpNotifier:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._http = httpx.AsyncClient(timeout=upstream.TIMEOUTS)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def telegram(self, chat_id: int, text: str) -> None:
        if self.s.bot_token is None:
            log.info("notify_skipped", channel="telegram", chat_id=chat_id)
            return
        url = f"{self.s.telegram_api}/bot{self.s.bot_token.get_secret_value()}/sendMessage"
        resp = await upstream.send(
            "telegram",
            "send",
            lambda: self._http.post(url, json={"chat_id": chat_id, "text": text}),
        )
        if resp.status_code == 403:
            log.info("telegram_blocked_by_user", chat_id=chat_id)  # пользователь остановил бота
            return
        resp.raise_for_status()

    async def email(self, to: str, subject: str, text: str) -> None:
        if self.s.email_api is None or self.s.email_key is None:
            log.info("notify_skipped", channel="email")
            return
        body = {"to": to, "subject": subject, "text": text}
        headers = {"Authorization": f"Bearer {self.s.email_key.get_secret_value()}"}
        resp = await upstream.send(
            "email",
            "send",
            lambda: self._http.post(self.s.email_api or "", json=body, headers=headers),
        )
        resp.raise_for_status()

    async def push(self, device_token: str, title: str, body: str) -> bool:
        if self.s.fcm_project is None or self.s.fcm_token is None:
            log.info("notify_skipped", channel="push")
            return True
        # FCM HTTP v1. Access-токен в проде выдаёт google-auth по сервисному аккаунту.
        url = f"https://fcm.googleapis.com/v1/projects/{self.s.fcm_project}/messages:send"
        message = {
            "message": {"token": device_token, "notification": {"title": title, "body": body}}
        }
        headers = {"Authorization": f"Bearer {self.s.fcm_token.get_secret_value()}"}
        resp = await upstream.send(
            "fcm", "send", lambda: self._http.post(url, json=message, headers=headers)
        )
        if resp.status_code == 404:
            return False  # UNREGISTERED: приложение удалили
        resp.raise_for_status()
        return True
