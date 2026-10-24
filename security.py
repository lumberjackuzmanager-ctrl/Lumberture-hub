# -*- coding: utf-8 -*-
"""Защита веб-интерфейса: доступ по IP, политика паролей, заголовки безопасности.
Без внешних зависимостей; состояние (журнал входов, блокировки) хранится в БД приложения (app.py)."""
import ipaddress
import socket
import time

DEFAULT_PASSWORDS = ("admin123", "master123", "manager123")
LOCK_FAILS = 5                 # неудачных попыток подряд для пары логин + IP ...
LOCK_WINDOW = 15 * 60          # ... -> блокировка на 15 минут
IP_FAILS = 20                  # неудачных попыток с одного IP (любые логины) за 15 мин -> блок IP
IDLE_ADMIN = 30 * 60           # автовыход администратора после 30 мин бездействия
IDLE_OTHER = 30 * 60           # остальные роли — тоже 30 мин (эгаси, 24.10; аввал 8 соат)
LOG_KEEP_DAYS = 90
DEFAULT_IP_ALLOW = "127.0.0.1\n::1\n192.168.1.22"     # по умолчанию — только этот ПК
ALWAYS = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": CSP,
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

_own = {"ts": 0.0, "ips": set()}


def own_ips():
    """IP-адреса самого сервера (запрос с этого ПК по адресу в сети тоже считается «с этого ПК»)."""
    if time.time() - _own["ts"] > 600:
        ips = set()
        try:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                ips.add(ip)
        except OSError:
            pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1)); ips.add(s.getsockname()[0]); s.close()
        except OSError:
            pass
        _own.update(ts=time.time(), ips=ips)
    return _own["ips"]


def _addr(ip):
    try:
        a = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return None
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a


def parse_list(text):
    """Строки «IP» или «подсеть/маска» (можно через запятую, # — комментарий). -> (сети, ошибки)."""
    nets, errs = [], []
    for raw in (text or "").replace(",", "\n").replace(";", "\n").splitlines():
        s = raw.split("#")[0].strip()
        if not s:
            continue
        try:
            nets.append(ipaddress.ip_network(s, strict=False))
        except ValueError:
            errs.append(s)
    return nets, errs


def is_local(ip):
    a = _addr(ip)
    return bool(a and (any(a in n for n in ALWAYS) or str(a) in own_ips()))


def ip_allowed(ip, text):
    """Этот ПК (127.0.0.1, ::1 и собственные адреса сервера) разрешён всегда — владелец не может закрыть доступ себе."""
    a = _addr(ip)
    if a is None:
        return False
    if is_local(ip):
        return True
    nets, _ = parse_list(text)
    return any(a.version == n.version and a in n for n in nets)


def password_problem(pw, username=""):
    """None — пароль подходит; иначе текст ошибки."""
    pw = pw or ""
    if len(pw) < 8:
        return "пароль — минимум 8 символов"
    if not any(ch.isalpha() for ch in pw) or not any(ch.isdigit() for ch in pw):
        return "пароль должен содержать и буквы, и цифры"
    if pw.lower() in DEFAULT_PASSWORDS or pw.lower() in ("password1", "qwerty123", "12345678a", "admin1234"):
        return "этот пароль слишком известный — придумайте другой"
    if username and username.lower() in pw.lower():
        return "пароль не должен содержать логин"
    return None


def hash_method(h):
    return (h or "").split(":", 1)[0] if ":" in (h or "") else "неизвестно"


def hash_ok(h):
    """Хэш werkzeug с медленной функцией: scrypt или pbkdf2 (не меньше 100 000 итераций) — иначе пере-хэшируем при входе."""
    m = (h or "").split("$", 1)[0]
    if m.startswith("scrypt:"):
        return True
    if m.startswith("pbkdf2:"):
        try:
            return int(m.split(":")[2]) >= 100_000
        except (IndexError, ValueError):
            return False
    return False


def apply_headers(resp, logged_in=False, static=False):
    for k, v in HEADERS.items():
        resp.headers.setdefault(k, v)
    if logged_in and not static:
        resp.headers["Cache-Control"] = "no-store"      # после выхода «Назад» в браузере не покажет данные
    return resp
