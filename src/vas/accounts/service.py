"""Аккаунты: вход через Telegram-бота и по коду на почту, устройства, токены."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vas.accounts.models import Device, LoginCode, RefreshToken, User
from vas.billing.ledger import add_period
from vas.core import secrets
from vas.core.cache import allow
from vas.core.clock import Clock
from vas.core.db import transaction
from vas.core.problems import Conflict, NotFound, TooManyRequests, Unauthorized
from vas.events.outbox import emit
from vas.notify.channels import Notifier
from vas.settings import Settings

log = structlog.get_logger()

BOT_CODE_TTL = timedelta(minutes=5)
EMAIL_CODE_TTL = timedelta(minutes=10)
EMAIL_CODE_TRIES = 5


class SlotsFull(Conflict):
    """Клиент по этому коду показывает список устройств и предлагает освободить слот."""

    code = "device_slots_full"


@dataclass(frozen=True)
class DeviceInfo:
    install_id: str
    platform: str
    title: str
    push_token: str | None = None
    replace: uuid.UUID | None = None  # какой слот освободить, если все заняты


@dataclass(frozen=True)
class Tokens:
    access: str
    refresh: str
    expires_in: int
    user_id: int
    device_id: uuid.UUID


class Accounts:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        notifier: Notifier,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self.sessions = sessions
        self.redis = redis
        self.notifier = notifier
        self.clock = clock
        self.settings = settings
        self._pepper = settings.code_pepper.get_secret_value()

    # ------------------------------------------------------------------ Telegram

    async def start_from_bot(
        self, telegram_id: int, invite: str | None = None
    ) -> tuple[User, bool]:
        """/start в боте. Повторный /start (или два параллельных) вернёт того же пользователя."""
        inviter = _parse_invite(invite)
        async with transaction(self.sessions) as s:
            if inviter is not None and await s.get(User, inviter) is None:
                inviter = None
            new_id = await s.scalar(
                insert(User)
                .values(telegram_id=telegram_id, invited_by=inviter)
                .on_conflict_do_nothing(index_elements=["telegram_id"])
                .returning(User.id)
            )
            user = await s.scalar(select(User).where(User.telegram_id == telegram_id))
            assert user is not None
            if new_id is not None:
                await self._welcome(s, user)
        return user, new_id is not None

    async def bot_login_code(self, telegram_id: int) -> str:
        """/login в боте: одноразовый код, который человек вводит в приложении."""
        if not await allow(self.redis, f"botcode:{telegram_id}", limit=5, per_seconds=600):
            raise TooManyRequests("too many codes, wait a few minutes")
        user, _ = await self.start_from_bot(telegram_id)
        for _ in range(5):
            code = secrets.random_code(8)
            try:
                async with transaction(self.sessions) as s:
                    s.add(
                        LoginCode(
                            channel="bot",
                            target=str(user.id),
                            code_hash=secrets.digest(code, self._pepper),
                            expires_at=self.clock.now() + BOT_CODE_TTL,
                        )
                    )
            except IntegrityError:
                continue  # такой живой код уже есть у кого-то, генерируем другой
            return f"{code[:4]}-{code[4:]}"
        raise RuntimeError("could not generate a unique login code")

    async def login_with_bot_code(self, code: str, device: DeviceInfo) -> Tokens:
        normalized = code.replace("-", "").replace(" ", "").upper()
        now = self.clock.now()
        async with transaction(self.sessions) as s:
            row = await s.scalar(
                select(LoginCode)
                .where(
                    LoginCode.channel == "bot",
                    LoginCode.code_hash == secrets.digest(normalized, self._pepper),
                    LoginCode.used_at.is_(None),
                )
                .with_for_update()
            )
            if row is None or row.expires_at <= now:
                raise Unauthorized("code is invalid or expired")
            row.used_at = now
            user_id = int(row.target)
            return await self._sign_in(s, user_id, device)

    # ------------------------------------------------------------------ почта

    async def send_email_code(self, email: str) -> None:
        email = email.strip().lower()
        if not await allow(self.redis, f"mail1m:{email}", limit=1, per_seconds=60):
            raise TooManyRequests("wait a minute before asking for a new code")
        if not await allow(self.redis, f"mail1h:{email}", limit=5, per_seconds=3600):
            raise TooManyRequests("too many codes for this address")
        code = secrets.random_digits(6)
        async with transaction(self.sessions) as s:
            # Новый код отменяет старые: в почте может лежать несколько писем.
            await s.execute(
                update(LoginCode)
                .where(
                    LoginCode.channel == "email",
                    LoginCode.target == email,
                    LoginCode.used_at.is_(None),
                )
                .values(used_at=self.clock.now())
            )
            s.add(
                LoginCode(
                    channel="email",
                    target=email,
                    code_hash=secrets.digest(code, self._pepper),
                    expires_at=self.clock.now() + EMAIL_CODE_TTL,
                )
            )
        # Отправляем после коммита и не через outbox: код не должен лежать в БД открытым текстом.
        await self.notifier.email(
            email, "Код для входа", f"Ваш код: {code}. Он действует 10 минут."
        )

    async def login_with_email(self, email: str, code: str, device: DeviceInfo) -> Tokens:
        email = email.strip().lower()
        now = self.clock.now()
        wrong = False
        async with transaction(self.sessions) as s:
            row = await s.scalar(
                select(LoginCode)
                .where(
                    LoginCode.channel == "email",
                    LoginCode.target == email,
                    LoginCode.used_at.is_(None),
                )
                .order_by(LoginCode.id.desc())
                .limit(1)
                .with_for_update()
            )
            if row is None or row.expires_at <= now:
                raise Unauthorized("code is invalid or expired")
            if row.tries >= EMAIL_CODE_TRIES:
                raise TooManyRequests("too many wrong codes, request a new one")
            if not secrets.same(secrets.digest(code.strip(), self._pepper), row.code_hash):
                row.tries += 1
                wrong = True
            else:
                row.used_at = now
                new_id = await s.scalar(
                    insert(User)
                    .values(email=email)
                    .on_conflict_do_nothing(index_elements=["email"])
                    .returning(User.id)
                )
                user = await s.scalar(select(User).where(User.email == email))
                assert user is not None
                if new_id is not None:
                    await self._welcome(s, user)
                tokens = await self._sign_in(s, user.id, device)
        # Ошибку бросаем после коммита, иначе счётчик попыток откатится вместе с транзакцией.
        if wrong:
            raise Unauthorized("code is invalid or expired")
        return tokens

    # ------------------------------------------------------------------ токены

    async def refresh(self, token: str) -> Tokens:
        now = self.clock.now()
        reused = False
        async with transaction(self.sessions) as s:
            row = await s.scalar(
                select(RefreshToken)
                .where(RefreshToken.token_hash == secrets.digest(token, self._pepper))
                .with_for_update()
            )
            if row is None or row.revoked_at is not None or row.expires_at <= now:
                raise Unauthorized("refresh token is not valid")
            if row.rotated_at is not None:
                # Этот токен уже обменяли. Пришёл второй раз — значит, его кто-то украл.
                await s.execute(
                    update(RefreshToken)
                    .where(RefreshToken.chain_id == row.chain_id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
                reused = True
            else:
                row.rotated_at = now
                tokens = await self._issue(s, row.user_id, row.device_id, chain_id=row.chain_id)
        if reused:
            log.warning("refresh_reuse_detected", chain=str(row.chain_id), user_id=row.user_id)
            raise Unauthorized("refresh token reuse, please sign in again")
        return tokens

    async def logout(self, device_id: uuid.UUID) -> None:
        async with transaction(self.sessions) as s:
            await s.execute(
                update(RefreshToken)
                .where(RefreshToken.device_id == device_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=self.clock.now())
            )

    # ------------------------------------------------------------------ устройства

    async def devices(self, user_id: int) -> list[Device]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(Device).where(Device.user_id == user_id).order_by(Device.created_at)
            )
            return list(rows)

    async def remove_device(self, user_id: int, device_id: uuid.UUID) -> None:
        async with transaction(self.sessions) as s:
            # user_id в условии обязателен: иначе можно удалить чужое устройство по id.
            gone = await s.scalar(
                delete(Device)
                .where(Device.id == device_id, Device.user_id == user_id)
                .returning(Device.id)
            )
        if gone is None:
            raise NotFound("device not found")

    async def check_session(self, user_id: int, device_id: uuid.UUID) -> None:
        """Вызывается на каждый запрос: удалённое устройство и заблокированный пользователь
        теряют доступ сразу, а не когда истечёт access-токен."""
        async with self.sessions() as s:
            ok = await s.scalar(
                select(func.count())
                .select_from(Device)
                .join(User, User.id == Device.user_id)
                .where(Device.id == device_id, User.id == user_id, User.status == "active")
            )
        if not ok:
            raise Unauthorized("session is no longer valid")

    async def user(self, user_id: int) -> User:
        async with self.sessions() as s:
            user = await s.get(User, user_id)
        if user is None:
            raise NotFound("user not found")
        return user

    # ------------------------------------------------------------------ внутреннее

    async def _welcome(self, s: AsyncSession, user: User) -> None:
        await add_period(
            s,
            user_id=user.id,
            source_type="trial",
            source_id=str(user.id),  # один триал на пользователя гарантирует уникальный ключ
            days=self.settings.trial_days,
            now=self.clock.now(),
        )
        emit(s, "analytics", name="signed_up", user_id=user.id, invited=user.invited_by is not None)

    async def _sign_in(self, s: AsyncSession, user_id: int, info: DeviceInfo) -> Tokens:
        device = await self._take_slot(s, user_id, info)
        return await self._issue(s, user_id, device.id, chain_id=uuid.uuid4())

    async def _take_slot(self, s: AsyncSession, user_id: int, info: DeviceInfo) -> Device:
        """Занять слот устройства. Лок на строке пользователя сериализует параллельные входы:
        без него десять одновременных логинов прочитают «4 из 5» и все добавятся."""
        user = await s.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or user.status != "active":
            raise Unauthorized("account is not active")
        now = self.clock.now()
        known = await s.scalar(
            select(Device).where(Device.user_id == user_id, Device.install_id == info.install_id)
        )
        if known is not None:
            known.title = info.title
            known.push_token = info.push_token or known.push_token
            known.last_seen_at = now
            return known
        taken = list((await s.scalars(select(Device).where(Device.user_id == user_id))).all())
        victim = next((d for d in taken if d.id == info.replace), None)
        if len(taken) >= self.settings.device_slots and victim is not None:
            await s.delete(victim)  # refresh-токены этого устройства удалятся каскадом
            taken.remove(victim)
        if len(taken) >= self.settings.device_slots:
            raise SlotsFull(
                "all device slots are taken",
                slots=self.settings.device_slots,
                devices=[{"id": str(d.id), "title": d.title} for d in taken],
            )
        device = Device(
            id=uuid.uuid4(),
            user_id=user_id,
            install_id=info.install_id,
            platform=info.platform,
            title=info.title,
            push_token=info.push_token,
            last_seen_at=now,
        )
        s.add(device)
        await s.flush()
        return device

    async def _issue(
        self, s: AsyncSession, user_id: int, device_id: uuid.UUID, *, chain_id: uuid.UUID
    ) -> Tokens:
        now = self.clock.now()
        refresh = secrets.opaque_token()
        s.add(
            RefreshToken(
                user_id=user_id,
                device_id=device_id,
                chain_id=chain_id,
                token_hash=secrets.digest(refresh, self._pepper),
                expires_at=now + timedelta(days=self.settings.refresh_token_days),
            )
        )
        minutes = self.settings.access_token_minutes
        access = secrets.issue_access(
            self.settings.jwt_key.get_secret_value(),
            user_id=user_id,
            device_id=device_id,
            now=now,
            minutes=minutes,
        )
        return Tokens(access, refresh, minutes * 60, user_id, device_id)

    async def purge_codes(self, *, before: datetime) -> None:
        async with transaction(self.sessions) as s:
            await s.execute(delete(LoginCode).where(LoginCode.expires_at < before))


def _parse_invite(invite: str | None) -> int | None:
    if invite and invite.startswith("inv") and invite[3:].isdigit():
        return int(invite[3:])
    return None


def invite_code(user_id: int) -> str:
    return f"inv{user_id}"
