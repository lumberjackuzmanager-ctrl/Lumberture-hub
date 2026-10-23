# -*- coding: utf-8 -*-
"""SMS-провайдеры Узбекистана: Eskiz.uz (по умолчанию), Play Mobile, а также режим «тест — только журнал».
Каждый провайдер: send(phone, text) -> (status, response_text); status: 'sent' | 'test'. Ошибка -> SmsError."""
import re, json, time, uuid, logging
import requests

log = logging.getLogger("telegram")
PROVIDERS = {"test": "Тест — только журнал (SMS не отправляются)", "eskiz": "Eskiz.uz", "playmobile": "Play Mobile"}


class SmsError(Exception):
    def __init__(self, msg, response=""):
        super().__init__(msg)
        self.response = response


def normalize_phone(s):
    """-> 998XXXXXXXXX или '' если номер не узбекский мобильный."""
    d = re.sub(r"\D", "", s or "")
    if len(d) == 9:
        d = "998" + d
    return d if len(d) == 12 and d.startswith("998") else ""


def sms_parts(text):
    """Сколько SMS-частей займёт текст (латиница 160/153, кириллица 70/67)."""
    uni = any(ord(ch) > 127 for ch in text)
    one, multi = (70, 67) if uni else (160, 153)
    n = len(text)
    return 1 if n <= one else -(-n // multi)


class TestProvider:
    name = "test"

    def __init__(self, cfg=None):
        self.cfg = cfg or {}

    def send(self, phone, text):
        return "test", "Тестовый режим: SMS не отправлено, только записано в журнал"


class EskizProvider:
    """https://notify.eskiz.uz — вход по email+паролю -> Bearer-токен (действует ~30 дней, обновляется сам)."""
    name = "eskiz"
    BASE = "https://notify.eskiz.uz/api"

    def __init__(self, cfg, token_store=None):
        self.cfg = cfg
        self.store = token_store   # (get(), set(token)) — кэш токена в настройках
        self.base = (cfg.get("eskiz_base") or self.BASE).rstrip("/")

    def _login(self):
        if not self.cfg.get("eskiz_email") or not self.cfg.get("eskiz_password"):
            raise SmsError("Eskiz: не указан email или пароль")
        try:
            r = requests.post(f"{self.base}/auth/login", data={"email": self.cfg["eskiz_email"],
                                                                "password": self.cfg["eskiz_password"]}, timeout=20)
        except requests.RequestException as e:
            raise SmsError(f"Eskiz: нет связи ({e.__class__.__name__})")
        try:
            tok = r.json()["data"]["token"]
        except (ValueError, KeyError, TypeError):
            raise SmsError("Eskiz: неверный email или пароль", r.text[:300])
        if self.store:
            self.store[1](tok)
        return tok

    def _token(self, fresh=False):
        tok = None if fresh or not self.store else self.store[0]()
        return tok or self._login()

    def send(self, phone, text):
        data = {"mobile_phone": phone, "message": text, "from": self.cfg.get("eskiz_from") or "4546"}
        for attempt in (0, 1):
            tok = self._token(fresh=attempt == 1)
            try:
                r = requests.post(f"{self.base}/message/sms/send", data=data,
                                  headers={"Authorization": f"Bearer {tok}"}, timeout=20)
            except requests.RequestException as e:
                raise SmsError(f"Eskiz: нет связи ({e.__class__.__name__})")
            if r.status_code == 401 and attempt == 0:
                continue   # токен истёк — войти заново
            body = r.text[:500]
            try:
                js = r.json()
            except ValueError:
                js = {}
            if r.status_code == 200 and (js.get("status") in ("waiting", "success", "ok", "accepted") or js.get("id")):
                return "sent", body
            msg = js.get("message") if isinstance(js.get("message"), str) else body
            if any(k in (msg or "").lower() for k in ("template", "shablon", "moderat", "шаблон", "модерац")):
                msg = f"{msg} — текст не совпадает с одобренным шаблоном Eskiz (Кабинет → Шаблоны SMS)"
            raise SmsError(f"Eskiz: ошибка {r.status_code}: {msg}", body)
        raise SmsError("Eskiz: не удалось авторизоваться")


class PlaymobileProvider:
    """Play Mobile SMS-Broker: POST JSON, HTTP Basic авторизация (логин/пароль выдаёт Play Mobile по договору)."""
    name = "playmobile"
    URL = "https://send.smsxabar.uz/broker-api/send"

    def __init__(self, cfg, token_store=None):
        self.cfg = cfg

    def send(self, phone, text):
        if not self.cfg.get("pm_login") or not self.cfg.get("pm_password"):
            raise SmsError("Play Mobile: не указан логин или пароль")
        payload = {"messages": [{"recipient": phone, "message-id": "r" + uuid.uuid4().hex[:18],
                                 "sms": {"originator": self.cfg.get("pm_originator") or "3700", "content": {"text": text}}}]}
        try:
            r = requests.post(self.cfg.get("pm_url") or self.URL, data=json.dumps(payload).encode("utf-8"),
                              headers={"Content-Type": "application/json; charset=UTF-8"},
                              auth=(self.cfg["pm_login"], self.cfg["pm_password"]), timeout=20)
        except requests.RequestException as e:
            raise SmsError(f"Play Mobile: нет связи ({e.__class__.__name__})")
        body = r.text[:500]
        if r.status_code == 200:
            return "sent", body or "Request is received"
        if r.status_code == 401:
            raise SmsError("Play Mobile: неверный логин или пароль", body)
        raise SmsError(f"Play Mobile: ошибка {r.status_code}", body)


def get_provider(name, cfg, token_store=None):
    if name == "eskiz":
        return EskizProvider(cfg, token_store)
    if name == "playmobile":
        return PlaymobileProvider(cfg, token_store)
    return TestProvider(cfg)
