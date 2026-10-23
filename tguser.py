# -*- coding: utf-8 -*-
"""Отправка уведомлений от обычного Telegram-аккаунта (MTProto, библиотека Telethon).

Получателям НЕ нужно ничего нажимать: номер телефона импортируется в контакты аккаунта-отправителя
(contacts.importContacts), затем отправляется сообщение. Все вызовы синхронные: внутри — отдельный
поток со своим asyncio-циклом, в котором живёт один TelegramClient.

Результат send(): dict(status=..., user_id=..., wait=..., error=...), где status:
  sent        — доставлено в Telegram
  no_tg       — у номера нет Telegram или поиск по номеру закрыт настройками приватности
  privacy     — пользователь запретил сообщения от незнакомых / заблокировал аккаунт / удалён
  flood       — FloodWait: Telegram просит подождать wait секунд (сообщение остаётся в очереди)
  peer_flood  — аккаунт ограничен (спам-жалобы): писать незнакомым нельзя, проверьте @SpamBot
  auth        — сессия недействительна, нужно войти заново
  retry       — сеть/временная ошибка, повторить позже
  error       — прочая ошибка Telegram
"""
import re
import os, asyncio, threading, random, logging

log = logging.getLogger("telegram")

class TgUserError(Exception):
    """Ошибка входа/настройки с понятным русским текстом."""

def _err_text(e):
    from telethon import errors
    m = {
        errors.ApiIdInvalidError: "Неверные api_id / api_hash. Скопируйте их ещё раз с my.telegram.org → API development tools",
        errors.PhoneNumberInvalidError: "Неверный номер телефона. Формат: +998XXXXXXXXX",
        errors.PhoneNumberBannedError: "Этот номер заблокирован в Telegram",
        errors.PhoneNumberFloodError: "Слишком много попыток входа. Подождите несколько часов",
        errors.PhoneCodeInvalidError: "Неверный код. Введите код из Telegram (приходит в приложение Telegram, чат «Telegram»)",
        errors.PhoneCodeExpiredError: "Код устарел. Нажмите «Получить код» ещё раз",
        errors.PasswordHashInvalidError: "Неверный пароль двухэтапной проверки (облачный пароль Telegram)",
    }
    for cls, txt in m.items():
        if isinstance(e, cls):
            return txt
    if isinstance(e, errors.FloodWaitError):
        return f"Telegram просит подождать {e.seconds} сек. перед следующей попыткой"
    return f"Ошибка Telegram: {e.__class__.__name__}: {e}"


class Sender:
    def __init__(self, session_path):
        self.session_path = session_path
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="tguser-loop", daemon=True)
        self.thread.start()
        self.client = None
        self.key = None
        self.phone = None
        self.code_hash = None

    def _run(self, coro, timeout=90):
        # Telegram'ни кутишдан олдин шу оқимнинг ёзилган (commit қилинмаган) ўзгаришларини сақлаш — акс ҳолда база
        # кутиш давомида қулфланиб қолади ва Telegram оқимидаги бошқа ишлар (Эман, «тайёр» хабарлари) «database is locked» олади.
        try:
            from flask import g, has_app_context
            if has_app_context() and "db" in g and g.db.in_transaction:
                g.db.commit()
        except Exception:
            pass
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _ensure(self, api_id, api_hash):
        from telethon import TelegramClient
        key = (int(api_id), api_hash)
        if self.client is None or self.key != key:
            if self.client is not None:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            self.client = TelegramClient(self.session_path, key[0], key[1], device_model="Lumberture Hub",
                                         system_version="Windows", app_version="1.0", lang_code="ru", system_lang_code="ru")
            self.key = key
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    # ----------------------------------------------------------- вход
    def status(self, api_id, api_hash):
        async def go():
            c = await self._ensure(api_id, api_hash)
            if not await c.is_user_authorized():
                return {"authorized": False, "me": None}
            me = await c.get_me()
            return {"authorized": True, "me": _me(me)}
        try:
            return self._run(go(), 40)
        except Exception as e:
            raise TgUserError(_err_text(e) if _is_rpc(e) else f"Нет связи с Telegram: {e.__class__.__name__}") from e

    def send_code(self, api_id, api_hash, phone):
        async def go():
            c = await self._ensure(api_id, api_hash)
            sent = await c.send_code_request(phone)
            self.phone, self.code_hash = phone, sent.phone_code_hash
        try:
            self._run(go(), 60)
        except Exception as e:
            raise TgUserError(_err_text(e) if _is_rpc(e) else f"Нет связи с Telegram: {e.__class__.__name__}: {e}") from e

    def sign_in(self, api_id, api_hash, code):
        from telethon import errors
        if not self.code_hash:
            raise TgUserError("Сначала нажмите «Получить код» (после перезапуска программы код нужно запросить заново)")
        async def go():
            c = await self._ensure(api_id, api_hash)
            try:
                me = await c.sign_in(phone=self.phone, code=code.strip(), phone_code_hash=self.code_hash)
            except errors.SessionPasswordNeededError:
                return "password", None
            return "ok", _me(me)
        try:
            return self._run(go(), 60)
        except TgUserError:
            raise
        except Exception as e:
            raise TgUserError(_err_text(e)) from e

    def sign_in_password(self, api_id, api_hash, password):
        async def go():
            c = await self._ensure(api_id, api_hash)
            me = await c.sign_in(password=password)
            return "ok", _me(me)
        try:
            return self._run(go(), 60)
        except Exception as e:
            raise TgUserError(_err_text(e)) from e

    def logout(self, api_id, api_hash):
        async def go():
            c = await self._ensure(api_id, api_hash)
            try:
                await c.log_out()
            finally:
                self.client = None
        try:
            self._run(go(), 40)
        except Exception as e:
            log.warning("logout: %s", e)
        for suffix in ("", "-journal"):
            p = self.session_path + ".session" + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # ----------------------------------------------------------- отправка
    def send(self, api_id, api_hash, phone, name, text, user_id=None, file=None):
        try:
            return self._run(self._send(api_id, api_hash, phone, name, text, user_id, file), 180)
        except Exception as e:  # таймаут/сеть
            return {"status": "retry", "error": f"Нет связи с Telegram: {e.__class__.__name__}"}

    async def _send(self, api_id, api_hash, phone, name, text, user_id, file=None):
        return await send_via(await self._ensure(api_id, api_hash), phone, name, text, user_id, file)

    def send_chat(self, api_id, api_hash, chat, text, file=None):
        """Сообщение в группу/канал/чат: название группы (как в Telegram), @username, ссылка t.me/… или числовой id."""
        try:
            return self._run(self._send_chat(api_id, api_hash, chat, text, file), 180)
        except Exception as e:
            return {"status": "retry", "error": f"Нет связи с Telegram: {e.__class__.__name__}"}

    async def _send_chat(self, api_id, api_hash, chat, text, file=None):
        return await send_chat_via(await self._ensure(api_id, api_hash), chat, text, file)

    def call(self, api_id, api_hash, fn, timeout=240):
        """Произвольная async-функция fn(client) в потоке Telegram (входящие сообщения и т.п.)."""
        async def go():
            return await fn(await self._ensure(api_id, api_hash))
        return self._run(go(), timeout)

    # ----------------------------------------------------------- вопрос боту (курс доллара)
    def ask_bot(self, api_id, api_hash, bot, text, timeout=25):
        """Отправить команду боту (напр. @em_mat_info_bot «/rate_usd») и вернуть текст его ответа. TgUserError при ошибке."""
        async def go():
            c = await self._ensure(api_id, api_hash)
            if not await c.is_user_authorized():
                raise TgUserError("Telegram-аккаунт не авторизован")
            return await ask_bot_via(c, bot, text, timeout)
        try:
            return self._run(go(), timeout + 30)
        except TgUserError:
            raise
        except Exception as e:
            raise TgUserError(_err_text(e) if _is_rpc(e) else f"Нет связи с Telegram: {e.__class__.__name__}: {e}") from e


async def ask_bot_via(c, bot, text, timeout=25, poll=1.0):
    """Написать боту и дождаться ответа (новые входящие сообщения после нашего). Вынесено для тестов с поддельным клиентом."""
    entity = await c.get_input_entity(bot.lstrip("@"))
    sent = await c.send_message(entity, text)
    loop = asyncio.get_running_loop()
    t0, got = loop.time(), []
    while loop.time() - t0 < timeout:
        await asyncio.sleep(poll)
        msgs = await c.get_messages(entity, min_id=sent.id, limit=10)
        got = [m for m in msgs if not m.out and (m.raw_text or "").strip()]
        if got:
            await asyncio.sleep(poll)                       # бот может прислать ответ несколькими сообщениями
            msgs = await c.get_messages(entity, min_id=sent.id, limit=10)
            got = [m for m in msgs if not m.out and (m.raw_text or "").strip()] or got
            return "\n".join(m.raw_text for m in sorted(got, key=lambda m: m.id))
    raise TgUserError(f"Бот @{bot.lstrip('@')} не ответил за {timeout} сек.")


async def send_via(c, phone, name, text, user_id=None, file=None):
    """Импорт номера в контакты + отправка. Вынесено отдельно, чтобы тестировать с поддельным клиентом."""
    from telethon import errors, functions, types
    try:
        if not await c.is_user_authorized():
            return {"status": "auth", "error": "Аккаунт не авторизован — войдите заново в «Настройки → Telegram»"}
        entity = None
        if user_id:
            try:
                entity = await c.get_input_entity(int(user_id))     # уже известен (кэш сессии) — без повторного импорта
            except (ValueError, TypeError, KeyError):
                entity = None
        if entity is None:
            contact = types.InputPhoneContact(client_id=random.randrange(1, 2 ** 62), phone="+" + phone.lstrip("+"),
                                              first_name=(name or phone)[:60], last_name="")
            res = await c(functions.contacts.ImportContactsRequest([contact]))
            if not res.users:
                if getattr(res, "retry_contacts", None):
                    return {"status": "flood", "wait": 3600, "error": "Достигнут лимит импорта контактов Telegram — пауза 1 час"}
                return {"status": "no_tg", "error": "Номер не зарегистрирован в Telegram или скрыт настройками приватности"}
            entity = res.users[0]
            user_id = entity.id
        m = await _out(c, entity, text, file)
        return {"status": "sent", "user_id": user_id, "msg_id": getattr(m, "id", None), "peer": str(user_id or "")}
    except errors.FloodWaitError as e:
        return {"status": "flood", "wait": int(e.seconds), "error": f"FloodWait: Telegram просит подождать {e.seconds} сек."}
    except errors.PeerFloodError:
        return {"status": "peer_flood", "error": "Telegram ограничил аккаунт (PEER_FLOOD): нельзя писать незнакомым. Проверьте @SpamBot"}
    except (errors.UserPrivacyRestrictedError, errors.UserIsBlockedError, errors.YouBlockedUserError,
            errors.InputUserDeactivatedError) as e:
        return {"status": "privacy", "user_id": user_id, "error": f"Получатель недоступен ({e.__class__.__name__.replace('Error', '')})"}
    except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.UserDeactivatedBanError, errors.UnauthorizedError) as e:
        return {"status": "auth", "error": f"Сессия Telegram недействительна ({e.__class__.__name__}) — войдите заново"}
    except errors.RPCError as e:
        msg = str(e)
        if any(k in msg.upper() for k in ("PRIVACY", "PREMIUM_REQUIRED", "ALLOW_PAYMENT")):
            return {"status": "privacy", "user_id": user_id, "error": "Получатель принимает сообщения только от контактов / Premium"}
        return {"status": "error", "error": f"Telegram: {msg[:200]}"}
    except (ConnectionError, OSError, asyncio.TimeoutError) as e:
        return {"status": "retry", "error": f"Нет связи с Telegram: {e.__class__.__name__}"}


async def _out(c, entity, text, file=None):
    if file and os.path.exists(file):
        if len(text) <= 1000:
            return await c.send_file(entity, file, caption=text)
        await c.send_file(entity, file)
        return await c.send_message(entity, text)
    return await c.send_message(entity, text)


async def send_chat_via(c, chat, text, file=None):
    from telethon import errors
    try:
        if not await c.is_user_authorized():
            return {"status": "auth", "error": "Аккаунт не авторизован — войдите заново в «Настройки → Telegram»"}
        chat = (chat or "").strip()
        entity = None
        if re.fullmatch(r"-?\d+", chat):
            entity = await c.get_entity(int(chat))
        elif chat.startswith("@") or "t.me/" in chat:
            entity = await c.get_entity(chat)
        else:
            want = chat.casefold()
            async for d in c.iter_dialogs(limit=500):
                if (d.name or "").strip().casefold() == want:
                    entity = d.entity
                    break
        if entity is None:
            return {"status": "error", "error": f"Группа «{chat}» не найдена: аккаунт должен быть в ней участником"}
        m = await _out(c, entity, text, file)
        try:
            from telethon import utils
            peer = str(utils.get_peer_id(entity))
        except Exception:
            peer = ""
        return {"status": "sent", "msg_id": getattr(m, "id", None), "peer": peer}
    except errors.FloodWaitError as e:
        return {"status": "flood", "wait": int(e.seconds), "error": f"FloodWait: Telegram просит подождать {e.seconds} сек."}
    except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.UnauthorizedError) as e:
        return {"status": "auth", "error": f"Сессия Telegram недействительна ({e.__class__.__name__}) — войдите заново"}
    except errors.RPCError as e:
        return {"status": "error", "error": f"Telegram: {str(e)[:200]}"}
    except (ValueError, TypeError) as e:
        return {"status": "error", "error": f"Группа «{chat}» не найдена ({e})"}
    except (ConnectionError, OSError, asyncio.TimeoutError) as e:
        return {"status": "retry", "error": f"Нет связи с Telegram: {e.__class__.__name__}"}


def _me(u):
    if not u:
        return None
    return {"id": u.id, "name": " ".join(x for x in [u.first_name, u.last_name] if x), "username": u.username or "",
            "phone": u.phone or ""}

def _is_rpc(e):
    from telethon import errors
    return isinstance(e, errors.RPCError)


class SimulatedSender:
    """Имитация Telegram для демонстрации/скриншотов (RENT_TG_SIMULATE=1). Ничего никуда не отправляет.
    Номера, оканчивающиеся на 7 — «нет Telegram», на 3 — «закрыта приватность». Код входа: 12345, пароль 2FA: demo."""
    def __init__(self, logfile):
        self.logfile, self.auth, self.need_pw = logfile, False, False

    def status(self, api_id, api_hash):
        return {"authorized": self.auth, "me": self._me() if self.auth else None}

    def _me(self):
        return {"id": 1, "name": "Бухгалтерия (имитация)", "username": "", "phone": "998900000000"}

    def send_code(self, api_id, api_hash, phone):
        self.phone = phone

    def sign_in(self, api_id, api_hash, code):
        if code.strip() != "12345":
            raise TgUserError("Неверный код. Введите код из Telegram")
        self.need_pw = True
        return "password", None

    def sign_in_password(self, api_id, api_hash, pw):
        if pw != "demo":
            raise TgUserError("Неверный пароль двухэтапной проверки")
        self.auth = True
        return "ok", self._me()

    def logout(self, api_id, api_hash):
        self.auth = False

    def ask_bot(self, api_id, api_hash, bot, text, timeout=25):
        return os.environ.get("RENT_TG_SIM_RATE", "1$ = 11 840 UZS")

    def send_chat(self, api_id, api_hash, chat, text, file=None):
        with open(self.logfile, "a", encoding="utf-8") as f:
            f.write(f"--- to chat «{chat}»{' [photo]' if file else ''}\n{text}\n\n")
        return {"status": "sent"}

    def call(self, api_id, api_hash, fn, timeout=240):
        return None

    def send(self, api_id, api_hash, phone, name, text, user_id=None, file=None):
        if phone.endswith("7"):
            return {"status": "no_tg", "error": "Номер не зарегистрирован в Telegram или скрыт настройками приватности"}
        if phone.endswith("3"):
            return {"status": "privacy", "error": "Получатель принимает сообщения только от контактов"}
        with open(self.logfile, "a", encoding="utf-8") as f:
            f.write(f"--- to +{phone} ({name}){' [photo]' if file else ''}\n{text}\n\n")
        return {"status": "sent", "user_id": int(phone[-9:])}


_sender = None
_lock = threading.Lock()

def get_sender(data_dir):
    global _sender
    with _lock:
        if _sender is None:
            if os.environ.get("RENT_TG_SIMULATE") == "1":
                _sender = SimulatedSender(os.path.join(data_dir, "tg_simulated.log"))
            else:
                _sender = Sender(os.path.join(data_dir, "tg_user"))
        return _sender

def set_sender(s):
    """Для тестов: подменить отправителя."""
    global _sender
    _sender = s
