# -*- coding: utf-8 -*-
"""Минимальный клиент Telegram Bot API (через requests, без вебхуков — long polling,
поэтому работает на обычном ПК за роутером без белого IP)."""
import os, json, logging, socket, threading
import requests

# Провайдерда IPv6 ишламаса, requests аввал IPv6'га уланиб 20+ сония кутиб қолади (ConnectTimeout) — фақат IPv4.
try:
    import urllib3.util.connection as _uc
    if os.environ.get("TG_ALLOW_IPV6") != "1":
        _uc.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

API_BASE = os.environ.get("TG_API_BASE", "https://api.telegram.org").rstrip("/")
log = logging.getLogger("telegram")


class TgError(Exception):
    def __init__(self, code, description, retry_after=None):
        super().__init__(f"{code}: {description}")
        self.code, self.description, self.retry_after = code, description, retry_after

    @property
    def retryable(self):
        return self.code in (0, 429) or (isinstance(self.code, int) and self.code >= 500)

    def human(self):
        d = (self.description or "").lower()
        if self.code == 0:
            return "Нет связи с Telegram (интернет?)"
        if self.code == 401 or self.code == 404:
            return "Неверный токен бота (проверьте токен из @BotFather)"
        if self.code == 403:
            return "Арендатор заблокировал бота или удалил чат"
        if self.code == 409:
            return "Бот уже используется другой программой (второй экземпляр или вебхук). Используйте отдельного бота"
        if self.code == 429:
            return f"Слишком много сообщений, Telegram просит подождать {self.retry_after or ''} с"
        if self.code == 400 and "chat not found" in d:
            return "Чат не найден"
        return f"Ошибка Telegram {self.code}: {self.description}"


_tls = threading.local()


def _session():
    """Ҳар бир оқим учун битта уланиш (keep-alive): ҳар хабарда янги TLS уланиш очилмайди — секин интернетда тезроқ."""
    s = getattr(_tls, "s", None)
    if s is None:
        s = _tls.s = requests.Session()
    return s


CONNECT_TIMEOUT = 7


def _flush_db():
    """Интернетни кутишдан олдин шу оқимнинг commit қилинмаган ёзувларини сақлаш (база қулфи узоқ турмасин)."""
    try:
        from flask import g, has_app_context
        if has_app_context() and "db" in g and g.db.in_transaction:
            g.db.commit()
    except Exception:
        pass


def call(token, method, params=None, timeout=15):
    """Вызов метода Bot API. Бросает TgError. Уланиш ўрнатилмаса (сўров ҳали кетмаган) — бир марта қайта уринади."""
    if not token:
        raise TgError(401, "token is empty")
    _flush_db()
    r = None
    for attempt in (1, 2):
        try:
            r = _session().post(f"{API_BASE}/bot{token}/{method}", json=params or {}, timeout=(CONNECT_TIMEOUT, timeout))
            break
        except (requests.ConnectionError, requests.Timeout) as e:
            not_sent = isinstance(e, requests.ConnectTimeout) or any(k in str(e) for k in ("NewConnectionError", "NameResolution", "ConnectTimeout", "RemoteDisconnected", "Connection aborted"))
            _tls.s = None                                  # бузилган уланишни ташлаб, янгисини очиш
            if attempt == 2 or not not_sent:
                raise TgError(0, f"network: {e.__class__.__name__}")
        except requests.RequestException as e:
            raise TgError(0, f"network: {e.__class__.__name__}")
    try:
        data = r.json()
    except ValueError:
        raise TgError(r.status_code if r.status_code != 200 else 0, f"HTTP {r.status_code}")
    if not data.get("ok"):
        raise TgError(data.get("error_code", r.status_code), data.get("description", ""),
                      (data.get("parameters") or {}).get("retry_after"))
    return data.get("result")


def send_message(token, chat_id, text, reply_markup=None):
    p = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if reply_markup:
        p["reply_markup"] = json.loads(reply_markup) if isinstance(reply_markup, str) else reply_markup
    return call(token, "sendMessage", p)


def get_me(token):
    return call(token, "getMe", timeout=10)


def get_updates(token, offset, timeout=25):
    return call(token, "getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
                timeout=timeout + 15)


def prepare(token):
    """Убрать вебхук (иначе getUpdates не работает) и задать меню команд."""
    call(token, "deleteWebhook", {"drop_pending_updates": False})
    try:
        call(token, "setMyCommands", {"commands": [
            {"command": "start", "description": "Lumberture"},
            {"command": "lang", "description": "Тил / Язык / Language"}]})
    except TgError as e:
        log.warning("setMyCommands: %s", e)


def mask(token):
    if not token:
        return ""
    return token[:6] + "…" + token[-4:] if len(token) > 12 else "…"
