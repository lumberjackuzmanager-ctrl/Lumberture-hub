# -*- coding: utf-8 -*-
"""Платформа учёта арендаторов: аренда и компрессор в долларах, свет и мусор в сумах (валюта — у каждого вида сбора),
свет по счётчику, прочие начисления, оплаты в $ или сумах, долги/переплаты (итог в $ по текущему курсу), клиенты-должники.
Курс доллара: Telegram-бот @em_mat_info_bot (/rate_usd) → резерв ЦБ РУз → вручную. Уведомления — в Telegram от отдельного аккаунта по номеру телефона
(MTProto/Telethon), резерв — SMS, дополнительно — Telegram-бот."""
import ipaddress, os, io, re, json, time, random, sqlite3, secrets, string, calendar, tempfile, threading, logging, hmac, traceback
from logging.handlers import RotatingFileHandler
from datetime import date, datetime, timedelta
from functools import wraps
from flask import (send_from_directory, Flask, g, render_template, request, redirect, url_for, session, flash, abort,
                   send_file, Response, has_app_context)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
import tg, tguser, rates, onec
import security as sec
import sms as smsmod

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("RENT_DB", os.path.join(BASE, "data", "rent.db"))
DATA_DIR = os.path.dirname(DB_PATH)
os.makedirs(DATA_DIR, exist_ok=True)

log = logging.getLogger("telegram")
if not log.handlers:
    _h = RotatingFileHandler(os.path.join(DATA_DIR, "telegram.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(_h); log.setLevel(logging.INFO)

def _secret():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    p = os.path.join(DATA_DIR, ".secret_key")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
    return open(p).read().strip()

errlog = logging.getLogger("rent.errors")
if not errlog.handlers:
    _eh = RotatingFileHandler(os.path.join(DATA_DIR, "errors.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    class _MaskFmt(logging.Formatter):
        """Логда бот токени очиқ қолмасин: bot123:ABC… → bot123:***"""
        def format(self, rec):
            return re.sub(r"(bot\d{6,}):[A-Za-z0-9_-]{20,}", r"\1:***", super().format(rec))
    _eh.setFormatter(_MaskFmt("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    errlog.addHandler(_eh); errlog.setLevel(logging.INFO); errlog.propagate = False

app = Flask(__name__)
app.config["SECRET_KEY"] = _secret()
app.config["DEBUG"] = False                            # никакого отладочного режима и трассировок в браузере
app.config["PROPAGATE_EXCEPTIONS"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)   # не дольше рабочего дня (+ автовыход по бездействию)
app.config["SESSION_COOKIE_NAME"] = "rent_session"   # не пересекается с зарплатой (порт 8080) на том же ПК
app.config["SESSION_COOKIE_HTTPONLY"] = True           # cookie недоступна скриптам
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"          # не отправляется с чужих сайтов (защита от CSRF)
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("RENT_COOKIE_SECURE") == "1"   # только при работе через HTTPS
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024     # загрузка файлов (Excel) — не больше 5 МБ

ROLES = {"admin": "Администратор", "cashier": "Кассир / бухгалтер", "kassa": "Касса (фақат касса ҳисоботи)"}
# «Касса» фойдаланувчиси фақат шу саҳифаларни очади (ой итоги, фойда, шериклар, аренда, 1С — йўқ)
KASSA_ONLY = {"static", "favicon", "ui_theme_css", "ui_logo", "login", "logout", "kassa_day", "kassa_day_save", "kassa_lock", "kassa_month", "kassa_month_pdf", "kassa_meals", "set_lang", "setup_shortcut", "erp_docs", "erp_doc", "erp_doc_print", "erp_doc2_print",
              # кассир: накладной ва поступление денег ёзиш (товар танлаш, мижоз, курс)
              "erp_sale_edit", "erp_doc2_edit", "erp_doc_status", "erp_doc_lock", "erp_api_rate", "erp_api_goods", "erp_api_clients",
              "erp_api_credit", "erp_goods", "erp_api_gtree", "erp_api_glist", "erp_client_form"}   # табел/KPI/цех — кассирга йўқ (автомат)
# кассирга қўшимча кўриш бўлимлари (фақат кўриш) — админ «Пользователи» саҳифасида ёқади/ўчиради
KASSA_OPT = {"kassa_v_clients": ("Мижозлар рўйхати", {"erp_clients", "erp_history"}),
             "kassa_v_receipts": ("Поступление товара (приход: кўриш ва ёзиш)", {"rep_receipts", "erp_suppliers"}),
             "kassa_v_mutual": ("Взаиморасчеты", {"rep_mutual"}),
             "kassa_v_stock": ("Остаток товаров", {"rep_stock"})}

def kassa_can(key):
    """Аввалги «Касса» қўшимча бўлимлари (kassa_v_*) — энди роль бўлимлари (roles.py)."""
    return _roles_mod().has_sec(_roles_mod().OLD_KASSA_OPT.get(key, key))

def kassa_allowed(ep):
    return _roles_mod().ep_allowed(ep)

def _roles_mod():
    import roles
    return roles

def is_limited():
    return _roles_mod().is_limited()

# оформление интерфейса («Настройки → Оформление»)
UI_THEMES = {"light": "Светлая", "dark": "Тёмная", "auto": "Авто (как в системе)", "v16": "Современный (дашборд)"}   # v16 — светлая + замонавий стиль
UI_ACCENTS = {"lumberture": ("Lumberture", "#3E643C"), "green": ("Зелёный", "#2f5d50"), "blue": ("Синий", "#1f5fae"), "purple": ("Фиолетовый", "#6b3fa0"),
              "orange": ("Оранжевый", "#c2570c"), "red": ("Красный", "#b3261e"), "graphite": ("Графит", "#3d4451")}
UI_FONTS = {"normal": "Обычный", "large": "Крупный", "xlarge": "Очень крупный"}
UI_DENSITY = {"normal": "Обычно", "compact": "Компактно"}
UI_RADIUS = {"sharp": "Строгие", "normal": "Стандарт", "soft": "Мягкие"}
UI_HEADER = {"light": "Светлая", "brand": "Цвет бренда"}
UI_FF = {"inter": "Inter", "system": "Системный (Segoe UI)"}
UI_SKIN = {"classic": "Классический", "v16": "Современный (дашборд)"}
UI_LOGO_MAX = 1024 * 1024
UI_LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg"}
METHODS = {"cash": "Наличные", "card": "Карта", "transfer": "Перечисление / перевод"}
KINDS = {"rent": "Аренда: площадь × ставка за м² (или фикс. сумма из карточки)", "fixed": "Фиксированная сумма в месяц (своя у каждого)",
         "area": "За м² площади в месяц (площадь × ставка)", "meter": "По счётчику (расход × тариф)",
         "oneoff": "Разовое начисление (вручную)"}
MONTHLY_KINDS = ("fixed", "area")
OPENING_NAME = "Долг на начало (перенос)"
OPENING_ADV = "Аванс на начало (перенос)"
MONTHS = ["", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
NOTIF_KINDS = {"payment": "Оплата", "invoice": "Счёт", "meter": "Показания", "reminder": "Напоминание",
               "balance": "Баланс", "reply": "Ответ бота", "welcome": "Подключение"}
NOTIF_STATUS = {"pending": "в очереди", "sent": "отправлено", "failed": "ошибка", "skipped": "не отправлено"}
CHANNELS = {"tg": "Telegram", "sms": "SMS", "bot": "Бот"}
MSG_STATUS = {"pending": "в очереди", "sent": "отправлено", "no_tg": "нет Telegram", "privacy": "закрыто приватностью",
              "failed": "ошибка", "skipped": "не отправлено", "expired": "устарело", "test": "тест (не отправлено)"}
MSG_KINDS = {"invoice": "Счёт", "payment": "Оплата", "reminder": "Напоминание", "meter": "Показания", "balance": "Баланс",
             "manual": "Вручную", "test": "Проверка", "reply": "Ответ бота", "welcome": "Подключение", "ready": "Буюртма тайёр", "report": "Ҳисобот (PDF)"}
TG_STATUS = {"": "не проверено", "ok": "Telegram ✓", "none": "нет Telegram", "privacy": "закрыто приватностью"}
NO_TG = ("none", "privacy")
CURRENCIES = {"USD": "Доллары ($)", "UZS": "Сумы (сум)"}
FX_SOURCES = {"bot": "Telegram-бот", "cbu": "ЦБ Узбекистана", "manual": "вручную"}
EPS = 0.01          # долг/переплата меньше 1 цента считается нулём

DEFAULT_SETTINGS = {
    "ip_allow": sec.DEFAULT_IP_ALLOW, "ip_admin_allow": "",
    "ui_theme": "light", "ui_accent": "green", "ui_accent_custom": "#2f5d50", "ui_font": "normal", "ui_density": "normal",
    "ui_title": "", "ui_logo": "", "ui_ver": "1", "ui_radius": "normal", "ui_header": "light", "ui_ff": "inter", "ui_zebra": "0", "ui_skin": "classic",
    "tg_token": "", "tg_bot_username": "", "tg_bot_name": "", "tg_offset": "0",
    "notify_payment": "1", "notify_invoice": "1", "notify_meter": "0",
    "auto_billing": "1", "billing_day": "1", "notify_hour": "9",
    "reminder_day": "0", "reminder_min_debt": "10000", "reminder_min_usd": "10", "client_min_usd": "10", "remind_tenants": "1", "remind_clients": "1",
    # Курс доллара (USD → UZS)
    "fx_bot": "em_mat_info_bot", "fx_cmd": "/rate_usd", "fx_pick": "sell", "fx_auto": "1", "fx_hour": "9", "fx_cbu": "0", "fx_every": "30", "fx_until": "19", "edit_lock_days": "4", "kassa_v_clients": "1", "kassa_v_receipts": "1", "kassa_v_mutual": "1", "kassa_v_stock": "1", "edit_open_from": "", "edit_open_until": "",
    "fx_fallback_rate": "12000",
    # Telegram-аккаунт (MTProto) — основной канал
    "tgu_enabled": "1", "tgu_api_id": "", "tgu_api_hash": "", "tgu_phone": "", "tgu_authorized": "0", "tgu_me": "",
    "tgu_delay_min": "20", "tgu_delay_max": "60", "tgu_daily_cap": "50", "tgu_hour_from": "8", "tgu_hour_to": "21",
    "tgu_expire_days": "7",
    "bot_enabled": "0",
    "company_name": "Аренда помещений", "contact_phone": "",
    "client_company_name": "Lumberture, +998 33 409-60-70",
    "tenant_categories": "Мебельщик\nМаляр\nДругое",
    # SMS / клиенты-должники
    "sms_provider": "test", "eskiz_email": "", "eskiz_password": "", "eskiz_from": "4546", "eskiz_token": "",
    "pm_login": "", "pm_password": "", "pm_originator": "3700", "pm_url": "",
    "sms_auto": "1", "sms_days": "0,3", "sms_time": "10:00", "sms_min_debt": "10000", "sms_on_payment": "1",
    "sms_channel": "0", "client_grace_days": "0",
    "default_lang": "ru",
    # 1С (COM, только чтение): долги контрагентов -> клиенты-должники
    "onec_mode": "file", "onec_path": "", "onec_server": "", "onec_ref": "", "onec_user": "", "onec_password": "",
    "onec_preset": "buh_account", "onec_query": "", "onec_account": "4010", "onec_auto_minutes": "0",
    "onec_last_at": "", "onec_last_result": "", "onec_last_error": "", "onec_info": "", "onec_new_remind": "0",
}
# ---- Уведомления: КОРОТКО — приветствие + итог в $ (без строк счёта, сумов, курса). Два языка: русский и узбекский (латиница).
LANGS = {"ru": "Русский", "uz": "O'zbekcha"}
TPL_KINDS = {  # шаблоны уведомлений — вид: (название, подстановки)
    "invoice": ("Счёт за месяц (и начисление по счётчику)", "{name} {balance_text} {date} {period} {unit} {company} {phone}"),
    "payment": ("Получена оплата", "{name} {amount} {balance_text} {date} {unit} {company} {phone}"),
    "reminder": ("Напоминание о долге (арендаторам)", "{name} {balance_text} {date} {unit} {company} {phone}"),
    "c_reminder": ("Напоминание клиентам (материалы и услуги)", "{name} {balance_text} {date} {company} {phone}"),
    "balance": ("Баланс («Отправить баланс», ответ бота)", "{name} {balance_text} {date} {unit} {company} {phone}"),
    "welcome": ("Подключение к боту (только бот)", "{name} {company}"),
    "debt": ("Фраза «долг» → {balance_text}", "{sum} {date}"),
    "rest": ("Фраза «остаток долга» после оплаты → {balance_text}", "{sum} {date}"),
    "advance": ("Фраза «аванс» (переплата) → {balance_text}", "{sum} {date}"),
    "zero": ("Фраза «нет долга» → {balance_text}", "{date}"),
}
DEFAULT_MSGS = {
    "ru": {"invoice": "Здравствуйте, {name}! {balance_text} {company}",
           "payment": "Здравствуйте, {name}! Получена оплата: {amount}. {balance_text} {company}",
           "reminder": "Здравствуйте, {name}! {balance_text} {company}",
           "balance": "Здравствуйте, {name}! {balance_text} {company}",
           "welcome": "Вы подключены к уведомлениям {company}.",
           "debt": "Ваша задолженность на {date}: {sum}.",
           "rest": "Остаток задолженности: {sum}.",
           "advance": "На вашем счёте: {sum}.",
           "zero": "Задолженности нет. Спасибо!",
           "c_reminder": "Здравствуйте, {name}! Напоминаем об оплате за материалы и выполненные услуги. {balance_text} {company}"},
    "uz": {"invoice": "Assalomu alaykum, {name}! {balance_text} {company}",
           "payment": "Assalomu alaykum, {name}! To'lov qabul qilindi: {amount}. {balance_text} {company}",
           "reminder": "Assalomu alaykum, {name}! {balance_text} {company}",
           "balance": "Assalomu alaykum, {name}! {balance_text} {company}",
           "welcome": "Siz {company} xabarnomalariga ulandingiz.",
           "debt": "{date} holatiga qarzdorligingiz: {sum}.",
           "rest": "Qolgan qarz: {sum}.",
           "advance": "Hisobingizda: {sum}.",
           "zero": "Qarzdorlik yo'q. Rahmat!",
           "c_reminder": "Assalomu alaykum, {name}! Materiallar va bajarilgan xizmatlar uchun to'lovni eslatamiz. {balance_text} {company}"},
}
DEFAULT_TEMPLATES = {f"msg_{k}_{l}": v for l, d in DEFAULT_MSGS.items() for k, v in d.items()}
MONTHS_UZ = ["", "yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  full_name TEXT DEFAULT '', role TEXT NOT NULL DEFAULT 'cashier', active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tenants(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, unit TEXT DEFAULT '', area REAL NOT NULL DEFAULT 0,
  phone TEXT DEFAULT '', category TEXT DEFAULT '', rent_amount REAL NOT NULL DEFAULT 0, rent_rate REAL, contract_start TEXT, contract_end TEXT,
  notes TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1, tg_code TEXT UNIQUE,
  is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS charge_types(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT NOT NULL, kind TEXT NOT NULL,
  default_amount REAL NOT NULL DEFAULT 0, apply_all INTEGER NOT NULL DEFAULT 0, unit TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 100, is_demo INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'UZS');
-- История тарифов (для счётчиков): действует тариф с максимальной valid_from <= последний день месяца показаний
CREATE TABLE IF NOT EXISTS tariffs(
  id INTEGER PRIMARY KEY, charge_type_id INTEGER NOT NULL REFERENCES charge_types(id) ON DELETE CASCADE,
  valid_from TEXT NOT NULL, price REAL NOT NULL, created_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0,
  UNIQUE(charge_type_id, valid_from));
-- Индивидуальные настройки фиксированных начислений арендатора (вкл/выкл, своя сумма)
CREATE TABLE IF NOT EXISTS tenant_fixed(
  tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  charge_type_id INTEGER NOT NULL REFERENCES charge_types(id) ON DELETE CASCADE,
  enabled INTEGER NOT NULL DEFAULT 1, amount REAL, PRIMARY KEY(tenant_id, charge_type_id));
CREATE TABLE IF NOT EXISTS tenant_meters(
  tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  charge_type_id INTEGER NOT NULL REFERENCES charge_types(id) ON DELETE CASCADE,
  meter_no TEXT DEFAULT '', initial_reading REAL NOT NULL DEFAULT 0, tariff_override REAL,
  enabled INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(tenant_id, charge_type_id));
CREATE TABLE IF NOT EXISTS invoices(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  period TEXT NOT NULL, created_at TEXT, created_by INTEGER, notified_at TEXT,
  is_demo INTEGER NOT NULL DEFAULT 0, UNIQUE(tenant_id, period));
CREATE TABLE IF NOT EXISTS charges(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  charge_type_id INTEGER REFERENCES charge_types(id), period TEXT NOT NULL, charge_date TEXT NOT NULL,
  description TEXT NOT NULL, quantity REAL NOT NULL DEFAULT 1, unit_price REAL NOT NULL DEFAULT 0,
  amount REAL NOT NULL, source TEXT NOT NULL DEFAULT 'manual', invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
  created_by INTEGER, created_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'UZS', fx_rate REAL);
CREATE TABLE IF NOT EXISTS meter_readings(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  charge_type_id INTEGER NOT NULL REFERENCES charge_types(id), period TEXT NOT NULL, reading_date TEXT NOT NULL,
  prev_value REAL NOT NULL, cur_value REAL NOT NULL, consumption REAL NOT NULL, tariff REAL NOT NULL,
  amount REAL NOT NULL, charge_id INTEGER REFERENCES charges(id) ON DELETE SET NULL,
  created_by INTEGER, created_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0,
  UNIQUE(tenant_id, charge_type_id, period));
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  pay_date TEXT NOT NULL, amount REAL NOT NULL, method TEXT NOT NULL DEFAULT 'cash', note TEXT DEFAULT '',
  created_by INTEGER, created_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'UZS', fx_rate REAL, usd_part REAL, uzs_part REAL);
-- Курс доллара: история (текущий = последняя запись)
CREATE TABLE IF NOT EXISTS fx_rates(
  id INTEGER PRIMARY KEY, rate REAL NOT NULL, source TEXT NOT NULL, rate_date TEXT, fetched_at TEXT NOT NULL,
  raw TEXT DEFAULT '', created_by INTEGER, is_demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tenant_chats(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  chat_id INTEGER NOT NULL, tg_name TEXT DEFAULT '', tg_username TEXT DEFAULT '', linked_at TEXT,
  active INTEGER NOT NULL DEFAULT 1, UNIQUE(tenant_id, chat_id));
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE, chat_id INTEGER,
  kind TEXT NOT NULL, text TEXT NOT NULL, markup TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0, created_at TEXT, sent_at TEXT);
CREATE INDEX IF NOT EXISTS ix_ch_tenant ON charges(tenant_id, charge_date);
CREATE INDEX IF NOT EXISTS ix_ch_period ON charges(period);
CREATE INDEX IF NOT EXISTS ix_pay_tenant ON payments(tenant_id, pay_date);
CREATE INDEX IF NOT EXISTS ix_notif_status ON notifications(status, next_try);
"""

CLIENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT DEFAULT '', company TEXT DEFAULT '', notes TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, sms_enabled INTEGER NOT NULL DEFAULT 1, sms_paused_until TEXT,
  tg_code TEXT UNIQUE, is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS client_debts(
  id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  doc_date TEXT NOT NULL, due_date TEXT, order_no TEXT DEFAULT '', description TEXT DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'services', amount REAL NOT NULL, created_by INTEGER, created_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS client_payments(
  id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  pay_date TEXT NOT NULL, amount REAL NOT NULL, method TEXT NOT NULL DEFAULT 'cash', note TEXT DEFAULT '',
  created_by INTEGER, created_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS client_chats(
  id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  chat_id INTEGER NOT NULL, tg_name TEXT DEFAULT '', tg_username TEXT DEFAULT '', linked_at TEXT,
  active INTEGER NOT NULL DEFAULT 1, UNIQUE(client_id, chat_id));
CREATE TABLE IF NOT EXISTS sms_log(
  id INTEGER PRIMARY KEY, client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL, phone TEXT, text TEXT,
  kind TEXT NOT NULL DEFAULT 'reminder', provider TEXT, status TEXT NOT NULL DEFAULT 'pending', response TEXT DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0, created_at TEXT, sent_at TEXT);
CREATE INDEX IF NOT EXISTS ix_cd_client ON client_debts(client_id, doc_date);
CREATE INDEX IF NOT EXISTS ix_cp_client ON client_payments(client_id, pay_date);
CREATE INDEX IF NOT EXISTS ix_sms_status ON sms_log(status, next_try);
-- Очередь и журнал сообщений от Telegram-аккаунта (по номеру телефона)
CREATE TABLE IF NOT EXISTS tg_out(
  id INTEGER PRIMARY KEY, tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
  client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE, kind TEXT NOT NULL, phone TEXT, name TEXT DEFAULT '',
  text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', error TEXT DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
  next_try REAL NOT NULL DEFAULT 0, created_at TEXT, tried_at TEXT, sent_at TEXT);
CREATE INDEX IF NOT EXISTS ix_tgout_status ON tg_out(status, next_try);
"""

# ---------------------------------------------------------------- DB
def connect():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=15000")
    return c

def db():
    if "db" not in g:
        g.db = connect()
    return g.db

@app.teardown_appcontext
def _close(exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()

def q(sql, args=(), one=False):
    rows = db().execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows

def scalar(sql, args=()):
    r = db().execute(sql, args).fetchone()
    return (r[0] if r else None) or 0

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def new_code():
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

def init_db(admin_password=None):
    c = connect()
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    c.executescript(CLIENT_SCHEMA)
    if "client_id" not in {r[1] for r in c.execute("PRAGMA table_info(notifications)")}:
        c.execute("ALTER TABLE notifications ADD COLUMN client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE")
    for table, col, ddl in [("tenants", "category", "TEXT DEFAULT ''"), ("tenants", "rent_rate", "REAL"),
                            ("tenants", "tg_status", "TEXT NOT NULL DEFAULT ''"), ("tenants", "tg_user_id", "INTEGER"),
                            ("tenants", "tg_checked_at", "TEXT"), ("tenants", "remind_enabled", "INTEGER NOT NULL DEFAULT 1"),
                            ("clients", "tg_status", "TEXT NOT NULL DEFAULT ''"), ("clients", "tg_user_id", "INTEGER"),
                            ("clients", "tg_checked_at", "TEXT"),
                            ("sms_log", "tenant_id", "INTEGER REFERENCES tenants(id) ON DELETE SET NULL"),
                            # валюты (миграция v2: $ и сумы) — добавление колонок, данные не теряются
                            ("charge_types", "currency", "TEXT NOT NULL DEFAULT 'UZS'"),
                            ("charges", "currency", "TEXT NOT NULL DEFAULT 'UZS'"), ("charges", "fx_rate", "REAL"),
                            ("payments", "currency", "TEXT NOT NULL DEFAULT 'UZS'"), ("payments", "fx_rate", "REAL"),
                            ("payments", "usd_part", "REAL"), ("payments", "uzs_part", "REAL"),
                            # клиенты: валюта долга/оплаты (v3, начальные долги) — по умолчанию сумы, как было
                            ("client_debts", "currency", "TEXT NOT NULL DEFAULT 'UZS'"), ("client_debts", "fx_rate", "REAL"),
                            ("client_payments", "currency", "TEXT NOT NULL DEFAULT 'UZS'"), ("client_payments", "fx_rate", "REAL"),
                            ("client_payments", "usd_part", "REAL"), ("client_payments", "uzs_part", "REAL"),
                            # язык уведомлений (пусто — по умолчанию из «Настройки»)
                            ("tenants", "lang", "TEXT NOT NULL DEFAULT ''"), ("clients", "lang", "TEXT NOT NULL DEFAULT ''"),
                            # 1С: код контрагента и пометка записей сверки с 1С
                            ("clients", "onec_code", "TEXT"), ("client_debts", "onec", "INTEGER NOT NULL DEFAULT 0"),
                            ("client_payments", "onec", "INTEGER NOT NULL DEFAULT 0")]:
        if col not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    # безопасность: журнал входов (блокировка подбора пароля) и признак стандартного пароля
    c.execute("""CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY, ts REAL NOT NULL, at TEXT NOT NULL,
                 username TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT '', ok INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '')""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_login_attempts ON login_attempts(ip, username, ts)")
    if "weak_pw" not in {r[1] for r in c.execute("PRAGMA table_info(users)")}:
        c.execute("ALTER TABLE users ADD COLUMN weak_pw INTEGER")
    ucols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    if "p_add" not in ucols:      # рухсатлар: товар/мижоз қўшиш (админ беради), супер — ўзгартириш ва ўчириш
        c.execute("ALTER TABLE users ADD COLUMN p_add INTEGER NOT NULL DEFAULT 1")
    if "p_super" not in ucols:
        c.execute("ALTER TABLE users ADD COLUMN p_super INTEGER NOT NULL DEFAULT 0")
    if "p_limit" not in ucols:      # мижоз лимити ва запретини ўзгартириш
        c.execute("ALTER TABLE users ADD COLUMN p_limit INTEGER NOT NULL DEFAULT 0")
    if "p_nal" not in ucols:        # накладнойда «Суммани ҳисобга олиш» белгисини олиш (БезНал)
        c.execute("ALTER TABLE users ADD COLUMN p_nal INTEGER NOT NULL DEFAULT 0")
    # старые оплаты были в сумах: целиком в «сумовый карман»
    c.execute("UPDATE payments SET usd_part=0, uzs_part=amount WHERE usd_part IS NULL OR uzs_part IS NULL")
    c.execute("UPDATE client_payments SET usd_part=0, uzs_part=amount WHERE (usd_part IS NULL OR uzs_part IS NULL) AND currency<>'USD'")
    if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        pw = admin_password or os.environ.get("ADMIN_PASSWORD", "admin123")
        c.execute("INSERT INTO users(username,password_hash,full_name,role) VALUES(?,?,?,?)",
                  ("admin", generate_password_hash(pw), "Администратор", "admin"))
    if not c.execute("SELECT 1 FROM charge_types LIMIT 1").fetchone():
        for code, name, kind, amt, allc, unit, sort in [
                ("rent", "Аренда", "rent", 0, 1, "м²", 0),
                ("electricity", "Свет", "meter", 0, 1, "кВт·ч", 1),
                ("trash", "Мусор", "fixed", 0, 1, "мес", 2),
                ("compressor", "Компрессор", "fixed", 0, 0, "мес", 3),
                ("oneoff", "Прочее (разовое)", "oneoff", 0, 0, "", 90),
                ("adjust", "Корректировка / скидка", "oneoff", 0, 0, "", 99)]:
            c.execute("INSERT INTO charge_types(code,name,kind,default_amount,apply_all,unit,sort) VALUES(?,?,?,?,?,?,?)",
                      (code, name, kind, amt, allc, unit, sort))
    if not c.execute("SELECT 1 FROM charge_types WHERE code='compressor'").fetchone():
        c.execute("INSERT INTO charge_types(code,name,kind,default_amount,apply_all,unit,sort) VALUES('compressor','Компрессор','fixed',0,0,'мес',3)")
    if not c.execute("SELECT 1 FROM charge_types WHERE code='opening'").fetchone():
        # служебный вид «Долг на начало (перенос)»: не входит в счета, не выбирается в «Начислить вручную»
        c.execute("INSERT INTO charge_types(code,name,kind,default_amount,apply_all,unit,sort,currency) "
                  "VALUES('opening',?,'opening',0,0,'',98,'UZS')", (OPENING_NAME,))
    if not c.execute("SELECT 1 FROM settings WHERE key='mig_currency_v2'").fetchone():
        # Аренда и компрессор — в долларах (если по ним ещё нет начислений в сумах; иначе валюту меняет админ)
        for code in ("rent", "compressor"):
            row = c.execute("SELECT id FROM charge_types WHERE code=?", (code,)).fetchone()
            if row and not c.execute("SELECT 1 FROM charges WHERE charge_type_id=?", (row[0],)).fetchone():
                c.execute("UPDATE charge_types SET currency='USD' WHERE id=?", (row[0],))
        # сохранённые шаблоны: «{amount} сум» -> «{amount}» (валюта теперь внутри подстановки)
        for key, val in c.execute("SELECT key, value FROM settings WHERE key LIKE 'tpl_%' OR key LIKE 'txt_%'").fetchall():
            nv = re.sub(r"\{(amount|total|tariff|balance|sum)\} сум", r"{\1}", val or "")
            if nv != val:
                c.execute("UPDATE settings SET value=? WHERE key=?", (nv, key))
        c.execute("INSERT INTO settings(key,value) VALUES('mig_currency_v2', ?)", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    c.commit(); c.close()

# ---------------------------------------------------------------- helpers
def money(v):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return v
    s = f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v - round(v)) > 0.004 else f"{round(v):,}"
    return s.replace(",", " ").replace("-", "−") if v < 0 else s.replace(",", " ")

def usd(v):
    """$1 234.50 / $500 / −$12.30"""
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return v
    s = f"{abs(v):,.2f}"
    s = "$" + (s[:-3] if s.endswith(".00") else s).replace(",", " ")
    return ("−" + s) if v <= -0.005 else s

def som(v):
    """Сумма в сумах для показа: целое число без копеек (826 643.3 -> 826 643)."""
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return v
    return money(round(v))

def cur(v, c="UZS"):
    """Сумма в своей валюте: $500 или 300 000 сум."""
    return usd(v) if c == "USD" else f"{som(v)} сум"

def num(v):
    v = float(v or 0)
    return str(int(v)) if v == int(v) else f"{v:g}"

def parse_float(v, default=0.0):
    try:
        s = str(v).replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".").replace("\u2212", "-")
        return float(s) if s else default
    except (TypeError, ValueError):
        return default

def parse_date(v, default=None):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default

def period_of(d=None):
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"

def valid_period(s):
    return bool(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", s or ""))

def get_period(arg="period", default=None):
    p = request.args.get(arg) or request.form.get(arg) or ""
    return p if valid_period(p) else (default or period_of())

def period_bounds(p):
    y, m = int(p[:4]), int(p[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])

def prev_period(p):
    return period_of(period_bounds(p)[0] - timedelta(days=1))

def next_period(p):
    return period_of(period_bounds(p)[1] + timedelta(days=1))

def period_name(p):
    return f"{MONTHS[int(p[5:7])]} {p[:4]}" if valid_period(p) else p

def ddmm(s):
    d = parse_date(s) if isinstance(s, str) else s
    return d.strftime("%d.%m.%Y") if d else (s or "")

def inum(v):
    """Значение для поля ввода: «1 500 000», «-50», «12.5» (parse_float читает обратно)."""
    return money(v).replace("−", "-") if v not in (None, "") else ""

app.jinja_env.filters.update(money=money, som=som, num=num, pname=period_name, ddmm=ddmm, usd=usd, cur=cur, inum=inum)

def get_setting(key, default=None):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    if r and r["value"] is not None:
        return r["value"]
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(key, DEFAULT_TEMPLATES.get(key, ""))

def set_settings(commit=True, **kw):
    for k, v in kw.items():
        db().execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    if commit:
        db().commit()

def setting_int(key):
    try:
        return int(get_setting(key))
    except (TypeError, ValueError):
        return int(DEFAULT_SETTINGS.get(key, 0))

@app.context_processor
def ctx():
    fxr = fx_current() if has_app_context() else None
    u_ = current_user() if has_app_context() else None
    ui_ = ui_settings() if has_app_context() else {}
    return dict(user=u_, csrf_token=csrf_token, kassa_can=kassa_can, LIMITED=bool(u_) and _roles_mod().is_limited(u_), has_sec=_roles_mod().has_sec, today=date.today().isoformat(), ROLES=ROLES, LANGS=LANGS, can=can, DENY_TEXT=DENY_TEXT, is_closed=is_closed, closed_edge=closed_edge,
                UI=ui_, UI_THEMES=UI_THEMES, UI_ACCENTS=UI_ACCENTS, UI_FONTS=UI_FONTS, UI_DENSITY=UI_DENSITY, UI_RADIUS=UI_RADIUS, UI_HEADER=UI_HEADER, UI_FF=UI_FF, UI_SKIN=UI_SKIN,
                WEAK=(weak_users() if u_ and u_["role"] == "admin" else ([u_["username"]] if u_ and u_["weak_pw"] else [])),
                DEFAULT_LANG=lang_of() if has_app_context() else "ru",
                METHODS=METHODS, KINDS=KINDS, CATEGORIES=categories() if has_app_context() else [], NOTIF_KINDS=NOTIF_KINDS, NOTIF_STATUS=NOTIF_STATUS,
                cur_period=period_of(), CURRENCIES=CURRENCIES, FX_SOURCES=FX_SOURCES, FXROW=fxr, FX=(fxr["rate"] if fxr else 0))

def firm_info():
    """Чоп этиш шапкаси учун: логотип, фирма номи, телефон, манзил (UI тилида) — накладнойдагидек."""
    import c1 as _c1x, i18n as _i18x
    ui_ = ui_settings()
    lg = _i18x.lang() if has_app_context() else "ru"
    lg = "uz" if lg == "uz" else "ru"
    return dict(logo=(url_for("ui_logo") + "?v=" + str(ui_.get("ver", "1"))) if ui_.get("logo") else "",
                company=get_setting("nakl_company") or "Lumberture", phone=get_setting("contact_phone") or "+998 33 409-60-70",
                address=get_setting(f"nakl_address_{lg}") or get_setting("nakl_address_ru") or _c1x.NAKL_ADDR.get(lg, ""))

@app.context_processor
def ctx_firm():
    return dict(firm_info=firm_info)


# ---------------------------------------------------------------- auth
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    if "user" not in g:
        g.user = q("SELECT * FROM users WHERE id=? AND active=1", (uid,), one=True)
    return g.user

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]

# ---------------------------------------------------------------- безопасность: доступ по IP, бездействие, CSRF
BLOCK_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Доступ запрещён</title></head>
<body style="font-family:sans-serif;max-width:560px;margin:60px auto;line-height:1.5"><h2>⛔ Доступ с этого компьютера запрещён</h2>
<p>Программа «Аренда» открывается только с разрешённых компьютеров. Ваш адрес: <b>{ip}</b>.</p>
<p>Если доступ нужен — администратор добавляет адрес на компьютере с программой: «Безопасность» → «Доступ по IP».</p></body></html>"""
_ip_noted = {}

def client_ip():
    return request.remote_addr or ""

def _note_blocked(ip, why="ip"):
    """В журнал входов — не чаще раза в 10 мин. на адрес (чтобы сканирование не раздувало журнал)."""
    t = time.time()
    if t - _ip_noted.get((ip, why), 0) < 600:
        return
    _ip_noted[(ip, why)] = t
    try:
        log_attempt("", ip, 0, why)
    except sqlite3.Error:
        pass

@app.before_request
def _role_gate():
    """Роль «kassa»: фақат кунлик касса, табель, KPI, овқат."""
    if request.endpoint in (None, "static", "favicon"):
        return None
    u = current_user()
    if u and _roles_mod().is_limited(u) and not _roles_mod().ep_allowed(request.endpoint, u):
        if request.method == "GET":
            if request.endpoint == "dashboard":
                return redirect(_roles_mod().home(u))
            flash("Этот раздел вашей роли недоступен — доступ даёт администратор", "warn")
            return redirect(_roles_mod().home(u))
        abort(403)
    return None

@app.before_request
def _ip_gate():
    ip = client_ip()
    if sec.is_local(ip):
        return None
    if not sec.ip_allowed(ip, get_setting("ip_allow")):
        _note_blocked(ip)
        return Response(BLOCK_HTML.replace("{ip}", ip.replace("<", "")), 403, mimetype="text/html")
    adm = (get_setting("ip_admin_allow") or "").strip()
    if adm and not sec.ip_allowed(ip, adm):
        u = current_user()
        if request.path.startswith("/admin") or (u and u["role"] == "admin"):
            _note_blocked(ip, "ip-admin")
            if u and u["role"] == "admin":
                session.pop("uid", None)
            return Response(BLOCK_HTML.replace("{ip}", ip.replace("<", "")), 403, mimetype="text/html")
    return None

@app.before_request
def _idle_logout():
    if request.endpoint == "static" or not session.get("uid"):
        return None
    u = current_user()
    if not u:
        session.clear()
        return None
    limit = sec.IDLE_ADMIN if u["role"] == "admin" else sec.IDLE_OTHER
    t, last = time.time(), session.get("seen", 0)
    if last and t - last > limit:
        session.clear(); g.pop("user", None)
        flash(f"Сеанс завершён после {limit // 60} мин. бездействия — войдите снова", "warn")
        return redirect(url_for("login", next=request.path if request.method == "GET" else None))
    if t - last > 15:
        session["seen"] = t
    return None

@app.before_request
def _csrf():
    if request.method == "POST" and request.path == "/api/basis/import":   # Базис скрипти — ўз калити билан (X-LB-Token)
        return None
    if request.path.startswith("/api/online/"):    # сайт API: кукисиз, «Authorization: Bearer» токени билан (online.py)
        return None
    if request.method == "POST":
        tok, ref = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or "", session.get("csrf") or ""
        if not tok or not ref or not hmac.compare_digest(str(tok), str(ref)):
            abort(400, "CSRF token invalid")

@app.after_request
def _security_headers(resp):
    try:
        return sec.apply_headers(resp, logged_in=bool(session.get("uid")), static=request.endpoint in ("static", "ui_theme_css", "ui_logo"))
    except Exception:
        return resp

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        u = current_user()
        if not u:
            return redirect(url_for("login", next=request.path))
        if u["role"] != "admin":
            abort(403)
        return f(*a, **k)
    return w

def can(perm):
    """Рухсат: 'add' — товар/хизмат ва мижоз қўшиш; 'super' — ўзгартириш ва ўчириш. Админга ҳаммаси мумкин."""
    u = current_user()
    if not u:
        return False
    if u["role"] == "admin":
        return True
    keys = u.keys()
    if perm == "add":
        return bool(u["p_add"]) if "p_add" in keys else True
    if perm == "super":
        return bool(u["p_super"]) if "p_super" in keys else False
    if perm == "limit":
        return bool(u["p_limit"]) if "p_limit" in keys else False
    if perm == "nal":
        return bool(u["p_nal"]) if "p_nal" in keys else False
    if perm == "post":
        return _roles_mod().can_post(u)
    return False

DENY_TEXT = "Бу ҳаракатга доступ йўқ. Рухсатни админ беради."

# ---------------------------------------------------------------- кунларни ёпиш (1С: «дата запрета редактирования»)
def closed_edge():
    """Шу санагача (шу сана ҳам) кунлар ёпиқ: накладной ва касса ўзгармайди. None — ҳеч нарса ёпиқ эмас.
    Автомат: бугун ва олдинги (edit_lock_days-1) кун очиқ, ундан эскиси ёпиқ. Админ вақтинча эрта санадан очиши мумкин."""
    days = setting_int("edit_lock_days")
    if days <= 0:
        return None
    edge = date.today() - timedelta(days=days)
    of, ou = get_setting("edit_open_from", ""), get_setting("edit_open_until", "")
    if of and ou and now() < ou:
        try:
            edge = min(edge, date.fromisoformat(of) - timedelta(days=1))
        except ValueError:
            pass
    return edge

def is_closed(d):
    if isinstance(d, str):
        d = parse_date(d)
    e = closed_edge()
    return bool(d and e and d <= e)

def closed_text(d):
    d = parse_date(d) if isinstance(d, str) else d
    return (f"{d.strftime('%d.%m.%Y') if d else ''} куни ёпилган — накладной ва кассани ўзгартириб бўлмайди. "
            "Админ «Настройки → Кунларни ёпиш» бўлимида очиши мумкин.")

def is_admin():
    u = current_user()
    return bool(u and u["role"] == "admin")

@app.errorhandler(403)
def _403(e):
    return render_template("message.html", title="Нет доступа", text="Этот раздел доступен только администратору."), 403

@app.errorhandler(404)
def _404(e):
    return render_template("message.html", title="Не найдено", text="Страница или запись не найдена."), 404

HTTP_TEXTS = {400: ("Запрос не принят", "Форма устарела или была открыта в другом окне. Обновите страницу и повторите."),
              405: ("Действие недоступно", "Такой запрос здесь не поддерживается."),
              413: ("Файл слишком большой", "Можно загрузить файл не больше 5 МБ."),
              429: ("Слишком много попыток", "Подождите несколько минут и повторите.")}

@app.errorhandler(Exception)
def _any_error(e):
    """Пользователь не видит трассировку: подробности — только в data/errors.log."""
    if isinstance(e, HTTPException):
        title, text = HTTP_TEXTS.get(e.code, (e.name, "Не удалось выполнить запрос."))
        code = e.code or 500
    else:
        if app.testing and not app.config.get("SHOW_ERROR_PAGE"):
            raise e
        ref = secrets.token_hex(3)
        errlog.error("[%s] %s %s from %s\n%s", ref, request.method, request.path, client_ip(), traceback.format_exc())
        title, text, code = "Ошибка программы", f"Что-то пошло не так. Повторите действие; если ошибка повторяется — сообщите код {ref}.", 500
    try:
        return render_template("message.html", title=title, text=text), code
    except Exception:
        return Response(f"<h2>{title}</h2><p>{text}</p>", code, mimetype="text/html")

# ---------------------------------------------------------------- вход: журнал, блокировка подбора, проверка паролей
_DUMMY_HASH = generate_password_hash("not-a-real-password-0")

def log_attempt(username, ip, ok, reason):
    t = time.time()
    db().execute("INSERT INTO login_attempts(ts,at,username,ip,ok,reason) VALUES(?,?,?,?,?,?)",
                 (t, datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S"), (username or "")[:64], (ip or "")[:64], ok, reason))
    if random.random() < 0.05:
        db().execute("DELETE FROM login_attempts WHERE ts<?", (t - sec.LOG_KEEP_DAYS * 86400,))
    db().commit()

FAIL_REASONS = ("bad", "nouser", "locked")

def login_locked_until(username, ip, t=None):
    """0 — можно входить; иначе время (epoch), до которого вход для пары логин+IP (или всего IP) заблокирован."""
    t = t or time.time()
    since = t - sec.LOCK_WINDOW
    r = q("SELECT MAX(ts) m FROM login_attempts WHERE username=? AND ip=? AND (ok=1 OR reason='unlock')", (username, ip), one=True)
    rows = q(f"SELECT ts FROM login_attempts WHERE username=? AND ip=? AND ok=0 AND reason IN ({','.join('?' * len(FAIL_REASONS))}) "
             "AND ts>? ORDER BY ts DESC", (username, ip, *FAIL_REASONS, max(since, (r["m"] if r and r["m"] else 0))))
    if len(rows) >= sec.LOCK_FAILS:
        return rows[0]["ts"] + sec.LOCK_WINDOW
    if not sec.is_local(ip):            # подбор разных логинов с одного адреса
        r = q("SELECT MAX(ts) m FROM login_attempts WHERE username='*' AND ip=? AND reason='unlock'", (ip,), one=True)
        rows = q(f"SELECT ts FROM login_attempts WHERE ip=? AND ok=0 AND reason IN ({','.join('?' * len(FAIL_REASONS))}) "
                 "AND ts>? ORDER BY ts DESC", (ip, *FAIL_REASONS, max(since, (r["m"] if r and r["m"] else 0))))
        if len(rows) >= sec.IP_FAILS:
            return rows[0]["ts"] + sec.LOCK_WINDOW
    return 0

def locked_pairs():
    t = time.time()
    out = []
    for r in q("SELECT DISTINCT username, ip FROM login_attempts WHERE ok=0 AND ts>?", (t - sec.LOCK_WINDOW,)):
        until = login_locked_until(r["username"], r["ip"], t)
        if until:
            out.append(dict(username=r["username"], ip=r["ip"], until=datetime.fromtimestamp(until).strftime("%H:%M")))
    return out

def check_weak_passwords(force=False):
    """Есть ли у пользователей стандартный пароль (admin123 и т.п.). Медленная проверка — в фоне при запуске."""
    c = connect()
    try:
        rows = c.execute("SELECT id, password_hash FROM users WHERE active=1" + ("" if force else " AND weak_pw IS NULL")).fetchall()
        for uid, h in rows:
            weak = any(check_password_hash(h, p) for p in sec.DEFAULT_PASSWORDS)
            c.execute("UPDATE users SET weak_pw=? WHERE id=?", (1 if weak else 0, uid))
        c.commit()
        return len(rows)
    finally:
        c.close()

def weak_users():
    return [r["username"] for r in q("SELECT username FROM users WHERE active=1 AND weak_pw=1 ORDER BY id")]

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        uname, pw, ip = request.form.get("username", "").strip()[:64], request.form.get("password", ""), client_ip()
        until = login_locked_until(uname, ip)
        if until:
            log_attempt(uname, ip, 0, "locked")
            mins = max(1, int((until - time.time()) // 60) + 1)
            flash(f"Слишком много неудачных попыток входа. Вход временно заблокирован — повторите через {mins} мин.", "error")
            return render_template("login.html", **_login_ctx()), 429
        u = q("SELECT * FROM users WHERE username=? AND active=1", (uname,), one=True)
        ok = check_password_hash(u["password_hash"] if u else _DUMMY_HASH, pw)    # одинаковое время для любого логина
        if not (u and ok):
            log_attempt(uname, ip, 0, "bad" if u else "nouser")
            flash("Неверный логин или пароль", "error")
            return render_template("login.html", **_login_ctx())
        adm = (get_setting("ip_admin_allow") or "").strip()
        if u["role"] == "admin" and adm and not sec.ip_allowed(ip, adm):
            log_attempt(uname, ip, 0, "ip-admin")
            flash("Вход администратора с этого компьютера запрещён («Безопасность» → «Доступ по IP»)", "error")
            return render_template("login.html", **_login_ctx()), 403
        weak = 1 if pw.lower() in sec.DEFAULT_PASSWORDS else 0
        if not sec.hash_ok(u["password_hash"]):
            db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), u["id"]))
        db().execute("UPDATE users SET weak_pw=? WHERE id=?", (weak, u["id"]))
        log_attempt(uname, ip, 1, "ok")
        session.clear(); session.permanent = False     # браузер/дастур ёпилса — сессия тугайди
        session["uid"], session["seen"] = u["id"], time.time()
        if weak:
            flash("У вас стандартный пароль. Смените его: «Пользователи» → «Изменить» (минимум 8 символов, буквы и цифры).", "warn")
        nxt = request.args.get("next") or ""
        resp = redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        resp.set_cookie("lu", uname, max_age=365 * 86400, httponly=True, samesite="Lax")     # шу компьютерда охирги кирган (1С'дагидек)
        return resp
    return render_template("login.html", **_login_ctx())

def lan_ip(ip):
    """Офис тармоғи (192.168.*, 10.*, 172.16-31.*) ёки шу ПК."""
    try:
        a = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return False
    return a.is_private or a.is_loopback

def _login_ctx():
    """1С'дагидек: офис ичида — фойдаланувчилар рўйхатдан танланади; ташқаридан — логин ёзилади."""
    if not lan_ip(client_ip()):
        return dict(login_users=None, last_user="")
    R = _roles_mod()
    us = []
    for r in q("SELECT username, full_name, role FROM users WHERE active=1"):
        nm = (r["full_name"] or "").strip() or r["username"]
        us.append(dict(username=r["username"], name=nm, role=R.role(r["role"]).get("name") or ""))
    # Админ — охирида; қолганлари исм бўйича
    us.sort(key=lambda x: (x["username"] == "admin", x["name"].lower()))
    return dict(login_users=us, last_user=request.cookies.get("lu", ""))

@app.route("/favicon.ico")
def favicon():
    """Браузер ўзи сўрайдиган /favicon.ico — логинсиз, расмий белги."""
    r = send_from_directory(app.static_folder, "app.ico", mimetype="image/x-icon", max_age=86400)
    return r

@app.route("/setup/shortcut.bat")
def setup_shortcut():
    """Иш столига «Lumberture» белгиси: Chrome/Edge'да манзил сатрисиз, алоҳида ойна (1С каби). Манзил — шу компьютер очган манзил."""
    if not lan_ip(client_ip()):
        abort(404)
    # Манзил — шу компьютер очган манзил (IP:порт): бу компьютерда ишлаши аниқ. PowerShell ўрнига VBScript — тез ва блокланмайди.
    host = request.host if re.fullmatch(r"[A-Za-z0-9.\-]+(:\d+)?", request.host or "") else "localhost:8081"
    url = f"http://{host}/"
    vbs = [
        "On Error Resume Next",
        f'u = "{url}"',
        'Set sh = CreateObject("WScript.Shell")',
        'Set fs = CreateObject("Scripting.FileSystemObject")',
        'la = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")',
        'pf = sh.ExpandEnvironmentStrings("%ProgramFiles%")',
        'p86 = sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%")',
        'd = la & "\\Lumberture"',
        "If Not fs.FolderExists(d) Then fs.CreateFolder d",
        'ico = d & "\\app.ico"',
        'Set x = CreateObject("MSXML2.ServerXMLHTTP.6.0")',
        "x.setTimeouts 3000, 3000, 3000, 5000",
        'x.open "GET", u & "static/app.ico", False',
        "x.send",
        "If Err.Number = 0 And x.Status = 200 Then",
        '  Set st = CreateObject("ADODB.Stream")',
        "  st.Type = 1",
        "  st.Open",
        "  st.Write x.responseBody",
        "  st.SaveToFile ico, 2",
        "  st.Close",
        "End If",
        "Err.Clear",
        'c = ""',
        'For Each p In Array(pf & "\\Google\\Chrome\\Application\\chrome.exe", p86 & "\\Google\\Chrome\\Application\\chrome.exe", '
        'la & "\\Google\\Chrome\\Application\\chrome.exe", p86 & "\\Microsoft\\Edge\\Application\\msedge.exe", pf & "\\Microsoft\\Edge\\Application\\msedge.exe")',
        '  If c = "" And fs.FileExists(p) Then c = p',
        "Next",
        'If c = "" Then',
        '  WScript.Echo "XATO: Chrome yoki Edge topilmadi"',
        "  WScript.Quit 1",
        "End If",
        'Set s = sh.CreateShortcut(sh.SpecialFolders("Desktop") & "\\Lumberture Hub.lnk")',
        "s.TargetPath = c",
        'pd = d & "\\chrome2"',
        # Алоҳида профил: платформа ойнаси оддий Chrome'дан мустақил. Профил янги бўлганда «Каждый раз спрашивать, где сохранить файл»
        # билан бошланади (мавжуд профил файлига тегилмайди — Chrome уни бузилган деб ҳисобламасин).
        "If Not fs.FolderExists(pd) Then fs.CreateFolder pd",
        'If Not fs.FolderExists(pd & "\\Default") Then fs.CreateFolder pd & "\\Default"',
        'pp = pd & "\\Default\\Preferences"',
        "If Not fs.FileExists(pp) Then",
        '  Set tf = fs.CreateTextFile(pp, True, False)',
        '  tf.Write "{""download"":{""prompt_for_download"":true}}"',
        "  tf.Close",
        "End If",
        "Err.Clear",
        's.Arguments = "--app=" & u & " --start-maximized --user-data-dir=" & Chr(34) & pd & Chr(34) & " --no-first-run --no-default-browser-check"',
        "If fs.FileExists(ico) Then s.IconLocation = ico",
        's.Description = "Lumberture Hub"',
        "s.Save",
        "If Err.Number <> 0 Then",
        '  WScript.Echo "XATO: yorliq yaratilmadi (" & Err.Description & ")"',
        "Else",
        '  WScript.Echo "TAYYOR: ish stolida Lumberture Hub yorlig\'i paydo bo\'ldi"',
        "End If",
    ]
    def esc(line):
        for ch in "^&<>|":
            line = line.replace(ch, "^" + ch)
        return line.replace("%", "%%")
    bat = ["@echo off", f"rem Lumberture: {url}", "echo Lumberture yorlig'i yaratilmoqda...", 'set "V=%TEMP%\\lumberture_yarliq.vbs"']
    bat.append('> "%V%" echo ' + esc(vbs[0]))
    bat += ['>> "%V%" echo ' + esc(l) for l in vbs[1:]]
    bat += ['cscript //nologo "%V%"', 'del "%V%" >nul 2>&1', "timeout /t 6 >nul"]
    bat = "\r\n".join(bat) + "\r\n"
    return Response(bat, mimetype="application/octet-stream", headers={"Content-Disposition": "attachment; filename=Lumberture_Hub_yarlik.bat"})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ================================================================ КУРС ДОЛЛАРА
def fx_current():
    """Текущий курс = последняя запись в истории (бот / ЦБ / вручную). -> Row или None."""
    if "fx" not in g:
        g.fx = q("SELECT * FROM fx_rates ORDER BY id DESC LIMIT 1", one=True)
    return g.fx

def fx_rate():
    r = fx_current()
    return r["rate"] if r else None

def fx_date(r=None):
    r = r or fx_current()
    return ddmm(r["rate_date"] or r["fetched_at"][:10]) if r else ""

def fx_label(r=None):
    """«курс 11 840 сум/$ на 08.10.2026»"""
    r = r or fx_current()
    return f"курс {money(r['rate'])} сум/$ на {fx_date(r)}" if r else "курс не задан"

def fx_store(rate, source, raw="", rate_date=None, uid=None, is_demo=0):
    db().execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,created_by,is_demo) VALUES(?,?,?,?,?,?,?)",
                 (round(float(rate), 2), source, rate_date or date.today().isoformat(), now(), (raw or "")[:500], uid, is_demo))
    db().commit()
    g.pop("fx", None)
    log.info("fx rate %s (%s)", rate, source)
    return fx_current()

def fx_fetch(uid=None):
    """Обновить курс: бот @em_mat_info_bot -> (резерв) ЦБ РУз. -> (новая запись | None, [ошибки/замечания])."""
    errors, row = [], None
    bot, cmd = (get_setting("fx_bot") or "em_mat_info_bot").lstrip("@"), get_setting("fx_cmd") or "/rate_usd"
    if bot and tgu_ready():
        try:
            api_id, api_hash = tgu_cfg()
            txt = tgu_sender().ask_bot(api_id, api_hash, bot, cmd, timeout=20)
            set_settings(fx_bot_raw=(txt or "")[:300], fx_bot_at=now())
            rate = rates.parse_rate(txt, get_setting("fx_pick"))
            if rate:
                prev = q("SELECT * FROM fx_rates WHERE source IN ('bot','manual') AND is_demo=0 ORDER BY id DESC LIMIT 1", one=True)
                today = date.today().isoformat()
                if prev and abs(prev["rate"] - round(float(rate), 2)) < 0.005 and (prev["rate_date"] or prev["fetched_at"][:10]) == today:
                    row = prev                                   # курс ўзгармаган — янги ёзув керак эмас
                else:
                    row = fx_store(rate, "bot", raw=f"@{bot} {cmd}: {txt}", uid=uid)
                    if prev and abs(prev["rate"] - row["rate"]) >= 0.005:
                        fx_notify(prev, row)
            else:
                errors.append(f"бот @{bot} ответил, но курс не распознан: «{(txt or '')[:80]}»")
        except tguser.TgUserError as e:
            errors.append(f"бот @{bot}: {e}")
        except Exception as e:  # noqa — курс не должен ронять планировщик/страницу
            log.exception("fx bot")
            errors.append(f"бот @{bot}: {e.__class__.__name__}")
    elif bot:
        errors.append("бот: Telegram-аккаунт не подключён")
    if row is None and get_setting("fx_cbu") == "1":
        try:
            rate, d = rates.fetch_cbu()
            row = fx_store(rate, "cbu", raw=f"cbu.uz: USD {rate} на {ddmm(d) if d else '?'}", rate_date=d, uid=uid)
        except rates.RateError as e:
            errors.append(str(e))
    set_settings(fx_last_try_at=now(), fx_last_error="; ".join(errors))
    if row is None:
        log.warning("fx fetch failed: %s", errors)
    return row, errors

def fx_notify(prev, row):
    """Курс кун давомида ўзгарса — шерикларга Telegram'да хабар (раҳбар ботидаги «📈 Ҳисобот» эгалари)."""
    try:
        import owner as _own
        same_day = (prev["rate_date"] or prev["fetched_at"][:10]) == (row["rate_date"] or row["fetched_at"][:10])
        txt = (f"💱 Эман курси {'ўзгарди' if same_day else 'янгиланди'}: {money(prev['rate'])} → {money(row['rate'])} сўм/$ "
               f"({row['fetched_at'][11:16]})\nЯнги накладной ва касса шу курс билан ҳисобланади.")
        _own._send_all(txt)
    except Exception:
        log.exception("fx notify")

def fx_due(dt):
    """Курсни ботдан қачон сўраш: ҳар куни fx_hour'дан (9:00) бошлаб, кун давомида ҳар fx_every дақиқада (30)
    fx_until соатгача (19:00) — курс кун ўртасида ўзгариши мумкин. Бугунги курс ҳали олинмаган бўлса — ҳар 15 дақиқада қайта."""
    if get_setting("fx_auto") != "1":
        return False
    lt = get_setting("fx_last_try_at", "")
    try:
        mins = (dt - datetime.strptime(lt, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60 if lt else 1e9
    except ValueError:
        mins = 1e9
    if mins < 0:
        mins = 1e9
    if fx_current() is None:
        return mins >= 60
    if dt.hour < setting_int("fx_hour"):
        return False
    if get_setting("fx_auto_date", "") < dt.date().isoformat():
        return mins >= 15
    if dt.hour >= (setting_int("fx_until") or 19):
        return False
    return mins >= max(setting_int("fx_every") or 30, 10)

def to_usd(u, z, rate=None):
    """$ + сумы/курс. Если курс ещё ни разу не загружался — условный курс fx_fallback_rate (на главной — предупреждение),
    чтобы сумовые долги не «пропадали» из итогов и напоминаний."""
    rate = rate or fx_rate() or parse_float(get_setting("fx_fallback_rate"), 12000) or 12000
    return round((u or 0) + (z or 0) / rate, 2)

def usd_uzs(v, rate_row=None):
    """«$125.34 (≈ 1 484 000 сум по курсу 11 840 на 08.10.2026)»"""
    r = rate_row or fx_current()
    if not r:
        return usd(v)
    return f"{usd(v)} (≈ {money(round(v * r['rate'], -2) if abs(v * r['rate']) >= 100 else v * r['rate'])} сум по курсу {money(r['rate'])} на {fx_date(r)})"

def totals_text(u, z, approx=True):
    """«$500 + 300 000 сум ≈ $525.34 (курс 11 840 сум/$ на 08.10.2026)»"""
    parts = ([usd(u)] if abs(u) >= EPS else []) + ([f"{money(z)} сум"] if abs(z) >= 0.5 else [])
    if not parts:
        return "$0"
    s_ = " + ".join(parts)
    if approx and abs(z) >= 0.5 and fx_rate():
        s_ += f" ≈ {usd(to_usd(u, z))} ({fx_label()})"
    return s_

def pockets_text(u, z):
    """Состав долга по валютам (если есть сумовая часть): «в т.ч. долг $100 + долг 300 000 сум»."""
    if abs(z) < 0.5:
        return ""
    if z > 0 and u > -EPS:
        return "в т.ч.: " + (f"{usd(u)} + " if u >= EPS else "") + f"{money(z)} сум"
    items = ([("долг " if u > 0 else "переплата ") + usd(abs(u))] if abs(u) >= EPS else []) + \
            [("долг " if z > 0 else "переплата ") + f"{money(abs(z))} сум"]
    return "в т.ч.: " + ", ".join(items)

# ================================================================ ДОМЕН: начисления, баланс
def ct_by_code(code):
    return q("SELECT * FROM charge_types WHERE code=?", (code,), one=True)

def get_tenant(tid):
    t = q("SELECT * FROM tenants WHERE id=?", (tid,), one=True)
    if not t:
        abort(404)
    return t

def tenant_in_period(t, p):
    f, l = period_bounds(p)
    if not t["active"]:
        return False
    cs, ce = parse_date(t["contract_start"]), parse_date(t["contract_end"])
    return not (cs and cs > l) and not (ce and ce < f)

def rent_rate(t, p=None):
    """Ставка аренды за м²: своя у арендатора или общая (с историей), действующая на 1-е число месяца."""
    if t["rent_rate"] is not None and t["rent_rate"] > 0:
        return t["rent_rate"]
    rent = ct_by_code("rent")
    return (tariff_for(rent["id"], period_bounds(p or period_of())[0]) or 0) if rent else 0

def rent_amount(t, p=None):
    if (t["rent_amount"] or 0) > 0:          # фиксированная сумма вместо м²
        return float(t["rent_amount"])
    return round((t["area"] or 0) * rent_rate(t, p), 2)

def fixed_lines(t, p=None):
    """Ежемесячные фиксированные начисления арендатора: [(charge_type, сумма)]."""
    lines = []
    rent = ct_by_code("rent")
    if rent and rent["active"]:
        amt = rent_amount(t, p)
        if amt > 0:
            lines.append((rent, amt))
    for ct in q("SELECT * FROM charge_types WHERE kind IN ('fixed','area') AND active=1 ORDER BY sort, id"):
        r = q("SELECT * FROM tenant_fixed WHERE tenant_id=? AND charge_type_id=?", (t["id"], ct["id"]), one=True)
        enabled = r["enabled"] if r else ct["apply_all"]
        val = r["amount"] if r and r["amount"] is not None else ct["default_amount"]   # сумма или ставка за м²
        amt = round((t["area"] or 0) * (val or 0), 2) if ct["kind"] == "area" else (val or 0)
        if enabled and amt > 0:
            lines.append((ct, float(amt)))
    return lines

def line_desc(t, ct, p):
    if ct["kind"] == "rent":
        if (t["rent_amount"] or 0) > 0:
            return f"{ct['name']} за {period_name(p)}"
        return f"{ct['name']} за {period_name(p)}: {num(t['area'])} м² × {cur(rent_rate(t, p), ct['currency'])}"
    if ct["kind"] == "area":
        r = q("SELECT amount FROM tenant_fixed WHERE tenant_id=? AND charge_type_id=?", (t["id"], ct["id"]), one=True)
        rate = r["amount"] if r and r["amount"] is not None else ct["default_amount"]
        return f"{ct['name']} за {period_name(p)}: {num(t['area'])} м² × {cur(rate, ct['currency'])}"
    return f"{ct['name']} за {period_name(p)}"

def categories():
    return [x.strip() for x in (get_setting("tenant_categories") or "").splitlines() if x.strip()]

def monthly_totals(t, p=None):
    """Ежемесячные фиксированные начисления по валютам -> ($, сум)."""
    u = sum(a for ct, a in fixed_lines(t, p) if ct["currency"] == "USD")
    return round(u, 2), round(sum(a for ct, a in fixed_lines(t, p) if ct["currency"] != "USD"), 2)

def monthly_fixed_total(t, p=None):
    """Ежемесячные фиксированные начисления, в $ по текущему курсу."""
    return to_usd(*monthly_totals(t, p))

def ct_currency(ct_id):
    r = q("SELECT currency FROM charge_types WHERE id=?", (ct_id,), one=True) if ct_id else None
    return r["currency"] if r and r["currency"] in CURRENCIES else "UZS"

def add_charge(tid, ct_id, p, cdate, desc, amount, qty=1, price=None, source="manual", uid=None, is_demo=0, invoice_id=None,
               currency=None):
    """Начисление хранится в своей валюте (по умолчанию — валюта вида сбора) + курс на момент начисления (справочно)."""
    if invoice_id is None:
        inv = q("SELECT id FROM invoices WHERE tenant_id=? AND period=?", (tid, p), one=True)
        invoice_id = inv["id"] if inv else None
    currency = currency if currency in CURRENCIES else ct_currency(ct_id)
    c_ = db().execute(
        "INSERT INTO charges(tenant_id,charge_type_id,period,charge_date,description,quantity,unit_price,amount,source,"
        "invoice_id,created_by,created_at,is_demo,currency,fx_rate) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, ct_id, p, cdate, desc, qty, amount if price is None else price, round(amount, 2), source, invoice_id, uid, now(), is_demo,
         currency, fx_rate()))
    return c_.lastrowid

def generate_invoice(t, p, uid=None):
    """Счёт за месяц: аренда + мусор + прочие ежемесячные. Повторно не создаётся. -> id счёта или None."""
    if q("SELECT 1 FROM invoices WHERE tenant_id=? AND period=?", (t["id"], p), one=True):
        return None
    inv = db().execute("INSERT INTO invoices(tenant_id,period,created_at,created_by,is_demo) VALUES(?,?,?,?,?)",
                       (t["id"], p, now(), uid, t["is_demo"])).lastrowid
    first = period_bounds(p)[0].isoformat()
    for ct, amt in fixed_lines(t, p):
        add_charge(t["id"], ct["id"], p, first, line_desc(t, ct, p), amt, source="invoice",
                   uid=uid, is_demo=t["is_demo"], invoice_id=inv)
    db().execute("UPDATE charges SET invoice_id=? WHERE tenant_id=? AND period=? AND invoice_id IS NULL AND source<>'opening'", (inv, t["id"], p))
    return inv

def generate_invoices(p, uid=None, notify=False):
    created = []
    for t in q("SELECT * FROM tenants WHERE active=1 ORDER BY id"):
        if tenant_in_period(t, p) and generate_invoice(t, p, uid):
            created.append(t["id"])
    db().commit()
    if notify:
        for tid in created:
            notify_invoice(tid, p)
    return created

# ---------------------------------------------------------------- баланс и выписка
# Учёт по двум «карманам»: долг в $ и долг в сумах (каждое начисление — в своей валюте).
# Оплата в сумах сначала гасит сумовый долг, остаток пересчитывается в $ по курсу ДНЯ ОПЛАТЫ и гасит долларовый долг
# (и наоборот для оплаты в $). Разбивка фиксируется в payments.usd_part / uzs_part.
# Итог/баланс в $ = долг в $ + долг в сумах / ТЕКУЩИЙ курс — поэтому при изменении курса меняется только сумовая часть.
def tenant_pockets(tid, before=None):
    """-> (долг в $, долг в сумах); < 0 — переплата. before='YYYY-MM-DD' — на начало этого дня."""
    cc, cp, a = ("AND charge_date<?", "AND pay_date<?", (tid, before)) if before else ("", "", (tid,))
    u = scalar(f"SELECT SUM(amount) FROM charges WHERE tenant_id=? AND currency='USD' {cc}", a) \
        - scalar(f"SELECT SUM(usd_part) FROM payments WHERE tenant_id=? {cp}", a)
    z = scalar(f"SELECT SUM(amount) FROM charges WHERE tenant_id=? AND currency<>'USD' {cc}", a) \
        - scalar(f"SELECT SUM(uzs_part) FROM payments WHERE tenant_id=? {cp}", a)
    return round(u, 4), round(z, 2)

def tenant_balance(tid, before=None):
    """Долг (>0) или переплата (<0) в $ по текущему курсу."""
    return to_usd(*tenant_pockets(tid, before))

def all_pockets(before=None):
    cond, args = (" WHERE charge_date<?", (before,)) if before else ("", ())
    res = {r["id"]: [0.0, 0.0] for r in q("SELECT id FROM tenants")}
    for r in q(f"SELECT tenant_id, currency, SUM(amount) s FROM charges{cond} GROUP BY tenant_id, currency", args):
        res.setdefault(r["tenant_id"], [0.0, 0.0])[0 if r["currency"] == "USD" else 1] += r["s"]
    cond = cond.replace("charge_date", "pay_date")
    for r in q(f"SELECT tenant_id, SUM(usd_part) u, SUM(uzs_part) z FROM payments{cond} GROUP BY tenant_id", args):
        x = res.setdefault(r["tenant_id"], [0.0, 0.0])
        x[0] -= r["u"] or 0; x[1] -= r["z"] or 0
    return {k: (round(v[0], 4), round(v[1], 2)) for k, v in res.items()}

def all_balances(before=None):
    rate = fx_rate()
    return {k: to_usd(u, z, rate) for k, (u, z) in all_pockets(before).items()}

def allocate_payment(tid, amount, currency, rate):
    """Разнести оплату арендатора по карманам -> (usd_part, uzs_part)."""
    return alloc_split(*tenant_pockets(tid), amount, currency, rate)

def alloc_split(u, z, amount, currency, rate):
    """Разнести оплату по карманам (u — долг в $, z — долг в сумах) -> (usd_part, uzs_part). Сначала своя валюта,
    остаток — в другую по курсу дня оплаты; то, что больше всего долга, остаётся переплатой в валюте оплаты."""
    if currency == "USD":
        to_u = min(amount, max(u, 0))
        rest = amount - to_u
        to_z = min(rest * rate, max(z, 0)) if rate else 0
        return round(amount - (to_z / rate if rate else 0), 4), round(to_z, 2)
    to_z = min(amount, max(z, 0))
    rest = amount - to_z
    to_u = min(rest / rate, max(u, 0)) if rate else 0
    return round(to_u, 4), round(amount - to_u * rate, 2)

def pay_text(p):
    """«1 184 000 сум (= $100 по курсу 11 840)» — с пересчётом, если оплата гасила долг в другой валюте."""
    s_ = cur(p["amount"], p["currency"])
    if p["currency"] == "USD" and (p["uzs_part"] or 0) >= 0.5:
        s_ += f" (из них {money(p['uzs_part'])} сум по курсу {money(p['fx_rate'])})"
    elif p["currency"] != "USD" and (p["usd_part"] or 0) >= EPS:
        s_ += f" (из них {usd(p['usd_part'])} по курсу {money(p['fx_rate'])})"
    return s_

def ledger(tid, d1=None, d2=None):
    """Выписка: сальдо на начало, движения в своей валюте, баланс в $ (по текущему курсу) нарастающим итогом, сальдо на конец."""
    d1s, d2s = (d1.isoformat() if d1 else "0000-00-00"), (d2.isoformat() if d2 else "9999-12-31")
    rate = fx_rate()
    ou, oz = tenant_pockets(tid, d1s) if d1 else (0.0, 0.0)
    rows = []
    for c in q("SELECT c.*, ct.name ct_name, ct.kind ct_kind FROM charges c LEFT JOIN charge_types ct ON ct.id=c.charge_type_id "
               "WHERE c.tenant_id=? AND c.charge_date BETWEEN ? AND ?", (tid, d1s, d2s)):
        rows.append(dict(kind="charge", id=c["id"], date=c["charge_date"], period=c["period"], text=c["description"], cur=c["currency"],
                         charge=c["amount"], payment=0, source=c["source"], ct_name=c["ct_name"], sort=(c["charge_date"], 0, c["id"])))
    for p in q("SELECT * FROM payments WHERE tenant_id=? AND pay_date BETWEEN ? AND ?", (tid, d1s, d2s)):
        conv = pay_text(p)[len(cur(p["amount"], p["currency"])):].strip()
        rows.append(dict(kind="payment", id=p["id"], date=p["pay_date"], period="", charge=0, payment=p["amount"], cur=p["currency"],
                         usd_part=p["usd_part"] or 0, uzs_part=p["uzs_part"] or 0,
                         text="Оплата — " + METHODS.get(p["method"], p["method"]) + (f" ({p['note']})" if p["note"] else "") + (f" {conv}" if conv else ""),
                         method=p["method"], sort=(p["pay_date"], 1, p["id"])))
    rows.sort(key=lambda r: r["sort"])
    u, z = ou, oz
    charged, paid = {"USD": 0.0, "UZS": 0.0}, {"USD": 0.0, "UZS": 0.0}
    for r in rows:
        k = "USD" if r["cur"] == "USD" else "UZS"
        if r["kind"] == "charge":
            charged[k] += r["charge"]
            if k == "USD":
                u += r["charge"]
            else:
                z += r["charge"]
        else:
            paid[k] += r["payment"]
            u -= r["usd_part"]; z -= r["uzs_part"]
        r["pockets"] = (round(u, 4), round(z, 2))
        r["balance"] = to_usd(u, z, rate)
    return dict(opening=to_usd(ou, oz, rate), opening_p=(ou, oz), rows=rows, closing=to_usd(u, z, rate), closing_p=(round(u, 4), round(z, 2)),
                charged={k: round(v, 2) for k, v in charged.items()}, paid={k: round(v, 2) for k, v in paid.items()}, rate=rate)

# ---------------------------------------------------------------- начальный долг (перенос из прошлого учёта)
# Хранится как начисление source='opening' вида «Долг на начало (перенос)» — по одному на валюту ($ / сумы).
# Сумма < 0 — аванс (переплата). Входит в баланс и напоминания; в счета не входит; при вводе сообщений не отправляет.
def opening_ct():
    return ct_by_code("opening")

def opening_note(desc):
    """Комментарий из описания «Долг на начало (перенос): <комментарий>»."""
    for pre in (OPENING_NAME, OPENING_ADV):
        if (desc or "").startswith(pre):
            return desc[len(pre):].lstrip(": ").strip()
    return desc or ""

def opening_desc(amount, note=""):
    return (OPENING_NAME if amount > 0 else OPENING_ADV) + (f": {note.strip()}" if (note or "").strip() else "")

def tenant_openings(tid):
    """-> {'USD': row|None, 'UZS': row|None, 'note': комментарий, 'date': дата}"""
    res = {"USD": None, "UZS": None}
    for r in q("SELECT * FROM charges WHERE tenant_id=? AND source='opening' ORDER BY id", (tid,)):
        k = "USD" if r["currency"] == "USD" else "UZS"
        res[k] = res[k] or r
    any_ = res["USD"] or res["UZS"]
    res["note"] = opening_note(any_["description"]) if any_ else ""
    res["date"] = any_["charge_date"] if any_ else ""
    return res

def all_openings():
    res = {}
    for r in q("SELECT * FROM charges WHERE source='opening' ORDER BY id"):
        x = res.setdefault(r["tenant_id"], {"USD": None, "UZS": None, "note": "", "date": ""})
        k = "USD" if r["currency"] == "USD" else "UZS"
        x[k] = x[k] or r
        x["note"] = x["note"] or opening_note(r["description"]); x["date"] = x["date"] or r["charge_date"]
    return res

def opening_default_date(tid=None, contract_start=None):
    """По умолчанию — день перед первым счётом (или перед первой оплатой). Нет ни того, ни другого —
    день перед 1-м числом месяца, с которого пойдут счета (текущий месяц или месяц начала договора, если он позже)."""
    first = None
    if tid:
        a = q("SELECT MIN(charge_date) d FROM charges WHERE tenant_id=? AND source<>'opening'", (tid,), one=True)["d"]
        b = q("SELECT MIN(pay_date) d FROM payments WHERE tenant_id=?", (tid,), one=True)["d"]
        first = min([x for x in (a, b) if x] or [None]) if (a or b) else None
    if first:
        return parse_date(first) - timedelta(days=1)
    p0 = period_bounds(period_of())[0]
    cs = parse_date(contract_start) if contract_start else None
    if cs and date(cs.year, cs.month, 1) > p0:
        p0 = date(cs.year, cs.month, 1)
    return p0 - timedelta(days=1)

def set_tenant_opening(tid, currency, amount, d=None, note=None, uid=None, is_demo=None):
    """Установить начальный долг в валюте: amount=None — не менять, 0 — удалить, < 0 — аванс.
    Сообщения арендатору НЕ отправляются. -> 'added' | 'updated' | 'deleted' | None (без изменений)."""
    currency = "USD" if currency == "USD" else "UZS"
    rows = q("SELECT * FROM charges WHERE tenant_id=? AND source='opening' AND " + ("currency='USD'" if currency == "USD" else "currency<>'USD'")
             + " ORDER BY id", (tid,))
    if amount is None:
        return None
    amount = round(float(amount), 2)
    if abs(amount) < 0.005:
        if rows:
            db().execute("DELETE FROM charges WHERE tenant_id=? AND source='opening' AND " + ("currency='USD'" if currency == "USD" else "currency<>'USD'"), (tid,))
            return "deleted"
        return None
    if note is None:
        note = opening_note(rows[0]["description"]) if rows else ""
    d = d or (parse_date(rows[0]["charge_date"]) if rows else opening_default_date(tid, get_tenant(tid)["contract_start"]))
    ds, desc = d.isoformat(), opening_desc(amount, note)
    if rows:
        r = rows[0]
        for extra in rows[1:]:
            db().execute("DELETE FROM charges WHERE id=?", (extra["id"],))
        if abs(r["amount"] - amount) < 0.005 and r["charge_date"] == ds and r["description"] == desc and len(rows) == 1:
            return None
        db().execute("UPDATE charges SET amount=?, unit_price=?, charge_date=?, period=?, description=?, currency=?, invoice_id=NULL WHERE id=?",
                     (amount, amount, ds, period_of(d), desc, currency, r["id"]))
        return "updated"
    ct = opening_ct()
    if is_demo is None:
        is_demo = get_tenant(tid)["is_demo"]
    db().execute("INSERT INTO charges(tenant_id,charge_type_id,period,charge_date,description,quantity,unit_price,amount,source,"
                 "invoice_id,created_by,created_at,is_demo,currency,fx_rate) VALUES(?,?,?,?,?,1,?,?,'opening',NULL,?,?,?,?,?)",
                 (tid, ct["id"] if ct else None, period_of(d), ds, desc, amount, amount, uid, now(), is_demo, currency, fx_rate()))
    return "added"

def opening_from_form(f, prefix=""):
    """Поля формы «Начальный долг» -> (usd|None, uzs|None, date|None, note|None). Пустое поле = 0 (удалить), если поле есть в форме."""
    def val(k):
        if prefix + k not in f:
            return None
        raw = (f.get(prefix + k) or "").strip()
        return parse_float(raw) if raw else 0.0
    note = f.get(prefix + "note")
    return val("usd"), val("uzs"), parse_date(f.get(prefix + "date")) if (f.get(prefix + "date") or "").strip() else None, \
        (note.strip() if note is not None else None)

def apply_tenant_opening(tid, usd_amt, uzs_amt, d, note, uid=None):
    """Обе валюты сразу; дата/комментарий общие. -> список изменений (пусто — ничего не изменилось)."""
    return [x for x in (set_tenant_opening(tid, "USD", usd_amt, d, note, uid), set_tenant_opening(tid, "UZS", uzs_amt, d, note, uid)) if x]

# ---------------------------------------------------------------- счётчики (свет и др.)
def tariff_for(ct_id, d):
    r = q("SELECT price FROM tariffs WHERE charge_type_id=? AND valid_from<=? ORDER BY valid_from DESC LIMIT 1",
          (ct_id, d.isoformat() if isinstance(d, date) else d), one=True)
    return r["price"] if r else None

def tenant_meter(tid, ct_id):
    return q("SELECT * FROM tenant_meters WHERE tenant_id=? AND charge_type_id=?", (tid, ct_id), one=True)

def meter_tariff(tid, ct_id, p):
    tm = tenant_meter(tid, ct_id)
    if tm and tm["tariff_override"] is not None and tm["tariff_override"] > 0:
        return tm["tariff_override"]
    return tariff_for(ct_id, period_bounds(p)[1])

def meter_prev(tid, ct_id, p):
    r = q("SELECT cur_value, period, reading_date FROM meter_readings WHERE tenant_id=? AND charge_type_id=? AND period<? "
          "ORDER BY period DESC LIMIT 1", (tid, ct_id, p), one=True)
    if r:
        return r["cur_value"], r["period"]
    tm = tenant_meter(tid, ct_id)
    return (tm["initial_reading"] if tm else 0.0), None

def _meter_desc(ct, p, prev, cur_v, cons, tariff):
    return f"{ct['name']} за {period_name(p)}: {num(prev)} → {num(cur_v)} = {num(cons)} {ct['unit']} × {cur(tariff, ct['currency'])}"

def _write_reading(t, ct, p, prev, cur, rdate, tariff, existing, uid):
    cons = round(cur - prev, 4)
    amount = round(cons * tariff, 2)
    desc = _meter_desc(ct, p, prev, cur, cons, tariff)
    cid = existing["charge_id"] if existing else None
    if cid and q("SELECT 1 FROM charges WHERE id=?", (cid,), one=True):
        db().execute("UPDATE charges SET description=?, quantity=?, unit_price=?, amount=?, charge_date=? WHERE id=?",
                     (desc, cons, tariff, amount, rdate, cid))
    else:
        cid = add_charge(t["id"], ct["id"], p, rdate, desc, amount, qty=cons, price=tariff, source="meter", uid=uid, is_demo=t["is_demo"])
    if existing:
        db().execute("UPDATE meter_readings SET reading_date=?, prev_value=?, cur_value=?, consumption=?, tariff=?, amount=?, charge_id=? WHERE id=?",
                     (rdate, prev, cur, cons, tariff, amount, cid, existing["id"]))
        return existing["id"]
    return db().execute(
        "INSERT INTO meter_readings(tenant_id,charge_type_id,period,reading_date,prev_value,cur_value,consumption,tariff,amount,"
        "charge_id,created_by,created_at,is_demo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (t["id"], ct["id"], p, rdate, prev, cur, cons, tariff, amount, cid, uid, now(), t["is_demo"])).lastrowid

def save_reading(t, ct, p, cur, rdate, uid=None):
    """Записать показание за месяц p. Возвращает (id показания, изменилось ли). ValueError — ошибка ввода."""
    prev, _ = meter_prev(t["id"], ct["id"], p)
    if cur < prev:
        raise ValueError(f"{t['name']}: текущее показание {num(cur)} меньше предыдущего {num(prev)}")
    tariff = meter_tariff(t["id"], ct["id"], p)
    if not tariff:
        raise ValueError(f"Не задан тариф «{ct['name']}» на {period_name(p)} — укажите его в «Виды начислений»")
    nxt = q("SELECT * FROM meter_readings WHERE tenant_id=? AND charge_type_id=? AND period>? ORDER BY period LIMIT 1",
            (t["id"], ct["id"], p), one=True)
    if nxt and nxt["cur_value"] < cur:
        raise ValueError(f"{t['name']}: показание {num(cur)} больше, чем уже внесённое за {period_name(nxt['period'])} ({num(nxt['cur_value'])})")
    ex = q("SELECT * FROM meter_readings WHERE tenant_id=? AND charge_type_id=? AND period=?", (t["id"], ct["id"], p), one=True)
    if ex and abs(ex["cur_value"] - cur) < 1e-9 and abs(ex["prev_value"] - prev) < 1e-9 and abs(ex["tariff"] - tariff) < 1e-9:
        return ex["id"], False
    rid = _write_reading(t, ct, p, prev, cur, rdate or date.today().isoformat(), tariff, ex, uid)
    if nxt and abs(nxt["prev_value"] - cur) > 1e-9:   # следующий месяц уже внесён — пересчитать его расход
        _write_reading(t, ct, nxt["period"], cur, nxt["cur_value"], nxt["reading_date"], nxt["tariff"], nxt, uid)
    return rid, True

def delete_reading(rid):
    r = q("SELECT * FROM meter_readings WHERE id=?", (rid,), one=True)
    if not r:
        return
    if r["charge_id"]:
        db().execute("DELETE FROM charges WHERE id=?", (r["charge_id"],))
    db().execute("DELETE FROM meter_readings WHERE id=?", (rid,))
    nxt = q("SELECT * FROM meter_readings WHERE tenant_id=? AND charge_type_id=? AND period>? ORDER BY period LIMIT 1",
            (r["tenant_id"], r["charge_type_id"], r["period"]), one=True)
    if nxt:
        prev, _ = meter_prev(r["tenant_id"], r["charge_type_id"], nxt["period"])
        if prev <= nxt["cur_value"]:
            _write_reading(get_tenant(r["tenant_id"]), q("SELECT * FROM charge_types WHERE id=?", (r["charge_type_id"],), one=True),
                           nxt["period"], prev, nxt["cur_value"], nxt["reading_date"], nxt["tariff"], nxt, None)

# ================================================================ TELEGRAM: тексты, очередь, бот
def lang_of(r=None):
    """Язык уведомлений получателя: свой (ru/uz) или общий по умолчанию."""
    l = (r["lang"] if r is not None and "lang" in r.keys() else "") or ""
    if l in LANGS:
        return l
    d = get_setting("default_lang")
    return d if d in LANGS else "ru"

def msg_tpl(kind, lang):
    return get_setting(f"msg_{kind}_{lang}") or DEFAULT_MSGS[lang][kind]

def fill(tpl, v):
    for k, val in v.items():
        tpl = tpl.replace("{" + k + "}", str(val))
    tpl = re.sub(r"[ \t]{2,}", " ", tpl)
    tpl = re.sub(r"[ \t]+([.,!?])", r"\1", tpl)
    return re.sub(r"[ \t]+\n", "\n", tpl).strip()

def period_name_lang(p, lang):
    if lang == "uz" and valid_period(p):
        return f"{MONTHS_UZ[int(p[5:7])]} {p[:4]}"
    return period_name(p)

def bal_phrase(b, lang, after_payment=False):
    """Итог в $: «Ваша задолженность на 08.10.2026: $204.22.» / «Остаток задолженности: $Y.» / «Аванс: $Y.» / «Задолженности нет»."""
    d = ddmm(date.today())
    if b >= EPS:
        return fill(msg_tpl("rest" if after_payment else "debt", lang), {"sum": usd(b), "date": d})
    if b <= -EPS:
        return fill(msg_tpl("advance", lang), {"sum": usd(-b), "date": d})
    return fill(msg_tpl("zero", lang), {"date": d})

def notif_text(kind, r, b, amount_usd=None, period=None, lang=None):
    """Короткое уведомление арендатору/клиенту: приветствие + итог в $ (b — долг > 0 / аванс < 0, в $ по текущему курсу)."""
    lang = lang if lang in LANGS else lang_of(r)
    is_client = "area" not in r.keys()  # клиент (материалы/услуги), а не арендатор
    if is_client and kind in ("reminder", "balance"):
        kind = "c_reminder"
    v = {"name": r["name"], "unit": (r["unit"] if "unit" in r.keys() else "") or "", "date": ddmm(date.today()),
         "company": get_setting("client_company_name" if is_client else "company_name"), "phone": get_setting("contact_phone"),
         "period": period_name_lang(period, lang) if period else "", "amount": usd(amount_usd) if amount_usd is not None else "",
         "balance_text": bal_phrase(b, lang, after_payment=(kind == "payment"))}
    return fill(msg_tpl(kind, lang), v)

def pay_usd(p):
    """Сумма оплаты в $ (оплата в сумах — по курсу дня оплаты)."""
    if p["currency"] == "USD":
        return round(p["amount"], 2)
    rate = p["fx_rate"] or fx_rate() or parse_float(get_setting("fx_fallback_rate"), 12000) or 12000
    return round(p["amount"] / rate, 2)

BALANCE_KB = json.dumps({"keyboard": [[{"text": "💰 Баланс"}]], "resize_keyboard": True})
CONTACT_KB = json.dumps({"keyboard": [[{"text": "📱 Отправить мой номер", "request_contact": True}]],
                         "resize_keyboard": True, "one_time_keyboard": True})
outbox_event = threading.Event()

def enqueue(tid, kind, text, markup=None, chat_ids=None, commit=True, client_id=None):
    """Очередь Telegram-БОТА (дополнительный канал). Отправка — в фоне, не блокирует действия администратора."""
    if chat_ids is None:
        chat_ids = [r["chat_id"] for r in q("SELECT chat_id FROM tenant_chats WHERE tenant_id=? AND active=1", (tid,))]
    if not chat_ids:
        db().execute("INSERT INTO notifications(tenant_id,client_id,chat_id,kind,text,status,error,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (tid, client_id, None, kind, text, "skipped", "Не подключён к боту", now()))
    for cid in chat_ids:
        db().execute("INSERT INTO notifications(tenant_id,client_id,chat_id,kind,text,markup,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (tid, client_id, cid, kind, text, markup, "pending", now()))
    if commit:
        db().commit()
    if chat_ids:
        outbox_event.set()
    return len(chat_ids)

def safe_notify(fn, *a):
    """Ошибка уведомления не должна ломать запись оплаты/показаний. -> (канал|None, причина)."""
    try:
        return fn(*a)
    except Exception:
        log.exception("notify failed: %s", fn.__name__)
        return (None, "внутренняя ошибка (см. data/telegram.log)")

def queued(res):
    return 1 if res and res[0] else 0

def notify_payment(pid):
    p = q("SELECT * FROM payments WHERE id=?", (pid,), one=True)
    t = get_tenant(p["tenant_id"])
    return deliver("payment", notif_text("payment", t, tenant_balance(t["id"]), pay_usd(p)), tenant=t)

def invoice_text(t, p):
    return notif_text("invoice", t, tenant_balance(t["id"]), period=p)

def notify_invoice(tid, p):
    t = get_tenant(tid)
    res = deliver("invoice", invoice_text(t, p), tenant=t, commit=False)
    if res[0]:
        db().execute("UPDATE invoices SET notified_at=? WHERE tenant_id=? AND period=?", (now(), tid, p))
    db().commit()
    return res

def notify_meter(rid):
    r = q("SELECT * FROM meter_readings WHERE id=?", (rid,), one=True)
    t = get_tenant(r["tenant_id"])
    return deliver("meter", notif_text("invoice", t, tenant_balance(t["id"]), period=r["period"]), tenant=t)

def balance_message(t):
    return notif_text("balance", t, tenant_balance(t["id"]))

def tenant_welcome(t):
    return notif_text("welcome", t, 0) + "\n\n" + balance_message(t)

def debtors(min_debt=1):
    bal = all_balances()
    rows = [(t, bal.get(t["id"], 0)) for t in q("SELECT * FROM tenants WHERE active=1")]
    return sorted([(t, b) for t, b in rows if b >= max(min_debt, EPS)], key=lambda x: -x[1])

def send_reminders(today=None):
    """Напоминания арендаторам-должникам (вручную или по расписанию). -> сколько поставлено в очередь."""
    n = 0
    for x in tenant_reminder_targets(today):
        if not x["skip"]:
            ch, _ = deliver("reminder", x["text"], tenant=x["r"], commit=False)
            n += 1 if ch else 0
    db().commit()
    return n

# ================================================================ ДОСТАВКА: Telegram-аккаунт → (бот) → SMS
tg_event = threading.Event()
TG_FAIL_HOOKS = []   # fn(row, status, error) — хабар мижозга етмаганда (ready.py ишчига хабар беради)

def tgu_enabled():
    return get_setting("tgu_enabled") == "1"

def tgu_ready():
    return (tgu_enabled() and get_setting("tgu_authorized") == "1" and bool(get_setting("tgu_api_id"))
            and bool(get_setting("tgu_api_hash")))

def bot_on():
    return get_setting("bot_enabled") == "1" and bool(get_setting("tg_token"))

def rent_bot_on():
    """Бот ижарага ҳеч қачон боғланмайди (эгаси айтди, 22.10): бот — фақат Lumberture мижозлари учун (сайтга кириш, буюртма ҳолати),
    ижара ва қарздорларга хабар — фақат Telegram-аккаунтдан (009-60-70)."""
    return False

def sms_on():
    return get_setting("sms_channel") == "1"

def _bot_chat_ids(r, is_tenant):
    if is_tenant:
        return [x["chat_id"] for x in q("SELECT chat_id FROM tenant_chats WHERE tenant_id=? AND active=1", (r["id"],))]
    return [x["chat_id"] for x in q("SELECT chat_id FROM client_chats WHERE client_id=? AND active=1", (r["id"],))]

def predict_channel(r, is_tenant):
    """Каким каналом уйдёт сообщение получателю сейчас. -> (tg|bot|sms|None, почему не Telegram)."""
    phone = smsmod.normalize_phone(r["phone"])
    if r["is_demo"] and os.environ.get("RENT_TG_SIMULATE") != "1":
        return None, "демо-запись (не отправляется)"
    if rent_bot_on() and _bot_chat_ids(r, is_tenant):
        return "bot", ""
    if not tgu_enabled():
        reason = "Telegram-канал выключен"
    elif not phone:
        reason = "неверный номер телефона" if r["phone"] else "нет номера телефона"
    elif r["tg_status"] in NO_TG:
        reason = TG_STATUS[r["tg_status"]]
    elif not tgu_ready():
        reason = "Telegram-аккаунт не подключён (Настройки)"
    else:
        return "tg", ""
    if sms_on() and phone:
        return "sms", reason
    return None, reason

def queue_sms(phone, text, kind, tenant_id=None, client_id=None, note=""):
    if phone:
        db().execute("INSERT INTO sms_log(tenant_id,client_id,phone,text,kind,provider,status,response,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (tenant_id, client_id, phone, text, kind, get_setting("sms_provider"), "pending", note, now()))
    else:
        db().execute("INSERT INTO sms_log(tenant_id,client_id,phone,text,kind,provider,status,response,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (tenant_id, client_id, "", text, kind, get_setting("sms_provider"), "skipped", "Неверный или пустой номер телефона", now()))
    outbox_event.set()

def deliver(kind, text, tenant=None, client=None, via="auto", commit=True, file=""):
    """Единая точка отправки уведомления арендатору/клиенту. Никогда не ждёт сеть — только ставит в очередь.
    via: auto (Telegram-аккаунт, если нет Telegram — SMS, если включено), tg, sms. -> (канал|None, причина)."""
    r, is_t = (tenant, True) if tenant is not None else (client, False)
    tid, cid = (r["id"], None) if is_t else (None, r["id"])
    phone = smsmod.normalize_phone(r["phone"])
    if r["is_demo"] and os.environ.get("RENT_TG_SIMULATE") != "1":
        ch, why = None, "демо-запись: сообщения не отправляются (номер вымышленный)"
    elif not is_t and "tg_chat" in r.keys() and (r["tg_chat"] or "").strip() and via in ("auto", "tg") and tgu_ready():
        # клиенту задана группа Telegram — сообщение идёт в группу, а не в личку
        db().execute("INSERT INTO tg_out(tenant_id,client_id,kind,phone,name,text,status,created_at,chat,file) VALUES(?,?,?,?,?,?,'pending',?,?,?)",
                     (None, cid, kind, phone or "", r["name"], text, now(), r["tg_chat"].strip(), file or ""))
        tg_event.set()
        if commit:
            db().commit()
        return "tg", ""
    elif via == "tg":
        ch, why = ("tg", "") if phone and tgu_ready() else (None, "нет номера телефона" if not phone else "Telegram-аккаунт не подключён (Настройки)")
    elif via == "sms":
        ch, why = ("sms", "") if phone else (None, "неверный номер телефона")
    else:
        ch, why = predict_channel(r, is_t)
    if ch == "bot":
        enqueue(tid, kind, text, BALANCE_KB, chat_ids=_bot_chat_ids(r, is_t), commit=False, client_id=cid)
    elif ch == "tg":
        db().execute("INSERT INTO tg_out(tenant_id,client_id,kind,phone,name,text,status,created_at,file) VALUES(?,?,?,?,?,?,'pending',?,?)",
                     (tid, cid, kind, phone, r["name"], text, now(), file or ""))
        tg_event.set()
    elif ch == "sms":
        queue_sms(phone, text, kind, tid, cid, note=("вместо Telegram: " + why) if why else "")
    elif tgu_enabled() or via != "auto":
        st = {"none": "no_tg", "privacy": "privacy"}.get(r["tg_status"], "skipped") if not r["is_demo"] else "skipped"
        db().execute("INSERT INTO tg_out(tenant_id,client_id,kind,phone,name,text,status,error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (tid, cid, kind, phone or r["phone"], r["name"], text, st, why + ("" if sms_on() or r["is_demo"] else ". SMS выключены"), now()))
    if commit:
        db().commit()
    return ch, why

def channel_note(res):
    ch, why = res if res else (None, "ошибка постановки в очередь")
    if ch == "tg":
        return "сообщение в Telegram поставлено в очередь (уходит с паузами 20–60 сек.)"
    if ch == "bot":
        return "сообщение через Telegram-бота отправляется"
    if ch == "sms":
        return f"SMS в очереди ({why})" + (" — тестовый режим, не уходит" if get_setting("sms_provider") == "test" else "") if why else "SMS в очереди"
    return "уведомление не отправлено: " + (why or "нет канала")

def set_tg_status(tenant_id, client_id, status, user_id=None):
    table, rid = ("tenants", tenant_id) if tenant_id else ("clients", client_id)
    if not rid:
        return
    if user_id:
        db().execute(f"UPDATE {table} SET tg_status=?, tg_user_id=?, tg_checked_at=? WHERE id=?", (status, user_id, now(), rid))
    else:
        db().execute(f"UPDATE {table} SET tg_status=?, tg_checked_at=? WHERE id=?", (status, now(), rid))

def tgu_cfg():
    return get_setting("tgu_api_id"), get_setting("tgu_api_hash")

def tgu_sender():
    return tguser.get_sender(DATA_DIR)

def tgu_delays():
    if os.environ.get("RENT_TG_FAST") == "1":          # только для демонстрации/тестов
        return 0.5, 1.5
    a = max(parse_float(get_setting("tgu_delay_min"), 20), 5)
    return a, max(parse_float(get_setting("tgu_delay_max"), 60), a)

# «Таниш» қабул қилувчи: аввал унга хабар ҳақиқатан кетган (ёки гуруҳ/@username). Унга ёзиш спам ҳисобланмайди —
# кунлик чеклов фақат биринчи марта ёзиладиган рақамларга қўлланади.
KNOWN_SQL = ("(COALESCE(t.chat,'')<>'' OR EXISTS (SELECT 1 FROM tg_out p WHERE p.phone=t.phone AND t.phone<>'' "
             "AND p.status='sent' AND p.id<t.id))")

def tgu_tried_today(dt=None, new_only=True):
    """Бугун уриниб кўрилган хабарлар (new_only — фақат янги рақамларга, чеклов шулар учун)."""
    d = (dt or datetime.now()).date().isoformat()
    if not new_only:
        return scalar("SELECT COUNT(*) FROM tg_out WHERE tried_at>=?", (d,))
    return scalar(f"SELECT COUNT(*) FROM tg_out t WHERE tried_at>=? AND NOT {KNOWN_SQL}", (d,))

URGENT_KINDS = ("ready",)   # «буюртма тайёр» — кунлик чекловга қарамай юборилади ва навбатда биринчи

def tgu_profile_once():
    """Разовая смена профиля Telegram-аккаунта: data/tg_profile.json {"first_name","last_name","photo"} → tg_profile_done.json."""
    p = os.path.join(DATA_DIR, "tg_profile.json")
    if not os.path.isfile(p) or not tgu_ready():
        return
    try:
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
        os.replace(p, p + ".work")
    except Exception:
        errlog.exception("tg profile read"); return
    photo = os.path.join(DATA_DIR, cfg.get("photo") or "") if cfg.get("photo") else ""

    async def fn(c):
        from telethon import functions
        out = {}
        if cfg.get("first_name"):
            u = await c(functions.account.UpdateProfileRequest(first_name=cfg["first_name"][:64], last_name=(cfg.get("last_name") or "")[:64]))
            out["name"] = " ".join(x for x in (u.first_name, u.last_name) if x)
        if photo and os.path.isfile(photo):
            await c(functions.photos.UploadProfilePhotoRequest(file=await c.upload_file(photo)))
            out["photo"] = "ok"
        return out
    res = {}
    try:
        api_id, api_hash = tgu_cfg()
        res = tgu_sender().call(api_id, api_hash, fn, timeout=120) or {}
        st = tgu_sender().status(api_id, api_hash)
        if st.get("me"):
            set_settings(tgu_me=json.dumps(st["me"], ensure_ascii=False))
        errlog.warning("tg profile: %s", res)
    except Exception as e:
        res = {"error": f"{e.__class__.__name__}: {e}"[:300]}
        errlog.exception("tg profile")
    try:
        with open(os.path.join(DATA_DIR, "tg_profile_done.json"), "w", encoding="utf-8") as fh:
            json.dump(dict(cfg=cfg, result=res, at=now()), fh, ensure_ascii=False)
        os.remove(p + ".work")
    except OSError:
        pass

def tgu_state(dt=None, urgent=False):
    """Можно ли отправлять прямо сейчас. -> (ok, причина, секунд подождать). urgent — без дневного лимита."""
    dt = dt or datetime.now()
    ts = dt.timestamp()
    if not tgu_ready():
        return False, "Telegram-аккаунт не подключён", 30
    fu = parse_float(get_setting("tgu_flood_until"), 0)
    if fu > ts:
        return False, "пауза по требованию Telegram до " + datetime.fromtimestamp(fu).strftime("%d.%m %H:%M"), min(fu - ts, 300)
    h1, h2 = setting_int("tgu_hour_from"), setting_int("tgu_hour_to") or 24
    if not (h1 <= dt.hour < h2):
        return False, f"вне часов отправки ({h1}:00–{h2}:00)", 120
    cap = setting_int("tgu_daily_cap")
    if cap and not urgent and tgu_tried_today(dt) >= cap:
        return False, f"дневной лимит {cap} сообщений исчерпан — продолжим завтра", 300
    na = parse_float(get_setting("tgu_next_at"), 0)
    if na > ts:
        return False, "пауза между сообщениями", na - ts
    return True, "", 0

def process_tg_one(dt=None):
    """Отправить ОДНО сообщение из очереди Telegram-аккаунта с соблюдением лимитов. -> секунд до следующей попытки."""
    dt = dt or datetime.now()
    exp = setting_int("tgu_expire_days")
    if exp > 0:
        n = db().execute("UPDATE tg_out SET status='expired', error=? WHERE status='pending' AND created_at<?",
                         (f"Не отправлено за {exp} дн. (лимит / аккаунт не подключён) — отменено, чтобы не слать устаревшее",
                          (dt - timedelta(days=exp)).strftime("%Y-%m-%d %H:%M:%S"))).rowcount
        if n:
            db().commit()
    ok, why, wait = tgu_state(dt)
    only_urgent = False
    ph = ",".join("?" * len(URGENT_KINDS))
    exempt = f"(t.kind IN ({ph}) OR {KNOWN_SQL})"      # чекловсиз: «тайёр» ва таниш мижозлар
    if not ok and "лимит" in why:
        if scalar(f"SELECT COUNT(*) FROM tg_out t WHERE status='pending' AND next_try<=? AND {exempt}", (time.time(),) + URGENT_KINDS):
            ok2, why2, wait2 = tgu_state(dt, urgent=True)
            if ok2:
                ok, only_urgent = True, True
            else:
                why, wait = why2, wait2
    if not ok:
        if get_setting("tgu_wait_reason", "") != why:
            set_settings(tgu_wait_reason=why)
        return wait
    r = q(f"SELECT * FROM tg_out t WHERE status='pending' AND next_try<=?{(' AND ' + exempt) if only_urgent else ''} "
          f"ORDER BY (t.kind IN ({ph})) DESC, (t.kind='invoice') DESC, t.id LIMIT 1",     # накладной/тўлов хабари эслатмалардан олдин
          (time.time(),) + (URGENT_KINDS if only_urgent else ()) + URGENT_KINDS, one=True)
    if not r:
        if get_setting("tgu_wait_reason", ""):
            set_settings(tgu_wait_reason="")
        return 15
    target = get_tenant(r["tenant_id"]) if r["tenant_id"] else (q("SELECT * FROM clients WHERE id=?", (r["client_id"],), one=True) if r["client_id"] else None)
    api_id, api_hash = tgu_cfg()
    try:
        fl = r["file"] if "file" in r.keys() and r["file"] else None
        kw = {"file": fl} if fl else {}
        if "chat" in r.keys() and r["chat"]:
            res = tgu_sender().send_chat(api_id, api_hash, r["chat"], r["text"], **kw)
        else:
            res = tgu_sender().send(api_id, api_hash, r["phone"], r["name"] or (target["name"] if target else ""), r["text"],
                                    user_id=target["tg_user_id"] if target else None, **kw)
    except Exception as e:  # noqa — отправитель никогда не должен ронять поток
        log.exception("tg user send")
        res = {"status": "retry", "error": str(e)[:200]}
    st, err, ts = res.get("status"), res.get("error", ""), time.time()
    lo, hi = tgu_delays()
    nxt = ts + random.uniform(lo, hi)
    tried = st in ("sent", "no_tg", "privacy", "error")
    db().execute("UPDATE tg_out SET attempts=attempts+1, tried_at=COALESCE(?, tried_at) WHERE id=?", (now() if tried else None, r["id"]))
    if st == "sent":
        db().execute("UPDATE tg_out SET status='sent', sent_at=?, error='' WHERE id=?", (now(), r["id"]))
        if res.get("msg_id") and "out_msg_id" in r.keys():         # кейин «ҳамма учун ўчириш» мумкин бўлсин
            db().execute("UPDATE tg_out SET out_msg_id=?, out_peer=? WHERE id=?", (res["msg_id"], res.get("peer") or "", r["id"]))
        if not ("chat" in r.keys() and r["chat"]):
            set_tg_status(r["tenant_id"], r["client_id"], "ok", res.get("user_id"))
    elif st in ("no_tg", "privacy"):
        set_tg_status(r["tenant_id"], r["client_id"], "none" if st == "no_tg" else "privacy")
        if sms_on() and r["phone"] and r["kind"] != "test":
            queue_sms(r["phone"], r["text"], r["kind"], r["tenant_id"], r["client_id"], note="вместо Telegram: " + MSG_STATUS[st])
            err += " → отправлено SMS"
        db().execute("UPDATE tg_out SET status=?, error=? WHERE id=?", (st, err, r["id"]))
    elif st == "flood":
        wait = int(res.get("wait") or 60)
        set_settings(commit=False, tgu_flood_until=ts + wait + 5, tgu_flood_at=now(), tgu_flood_seconds=wait)
        db().execute("UPDATE tg_out SET error=? WHERE id=?", (err, r["id"]))
        nxt = ts + wait + 5
        log.warning("FloodWait %s s", wait)
    elif st == "peer_flood":
        set_settings(commit=False, tgu_flood_until=ts + 24 * 3600, tgu_peer_flood_at=now())
        db().execute("UPDATE tg_out SET error=? WHERE id=?", (err, r["id"]))
        nxt = ts + 24 * 3600
        log.error("PEER_FLOOD: account limited")
    elif st == "auth":
        set_settings(commit=False, tgu_authorized="0", tgu_auth_error=err, tgu_auth_error_at=now())
        db().execute("UPDATE tg_out SET error=? WHERE id=?", (err, r["id"]))
        log.error("tg user auth: %s", err)
    elif st == "retry":
        # интернет/Telegram йўқ — хабар йўқолмайди: tgu_expire_days ичида қайта-қайта уриниб кўрамиз (энг кўпи 10 дақиқада бир)
        db().execute("UPDATE tg_out SET next_try=?, error=? WHERE id=?", (ts + min(600, 120 * (r["attempts"] + 1)), err, r["id"]))
        nxt = ts + 30
    else:
        db().execute("UPDATE tg_out SET status='failed', error=? WHERE id=?", (err or "ошибка", r["id"]))
    set_settings(commit=False, tgu_next_at=nxt, tgu_last_at=now())
    db().commit()
    if st not in ("sent", "flood", "peer_flood", "auth", "retry"):
        for fn in TG_FAIL_HOOKS:
            try:
                fn(r, st, err)
            except Exception:
                errlog.exception("tg fail hook")
    log.info("tg user -> %s: %s %s", r["phone"], st, err)
    return max(nxt - time.time(), 1)

# ---------------------------------------------------------------- отправка очереди
def process_outbox(limit=30):
    rows = q("SELECT * FROM notifications WHERE status='pending' AND next_try<=? ORDER BY id LIMIT ?", (time.time(), limit))
    if not rows:
        return 0
    token = get_setting("tg_token")
    sent = 0
    for r in rows:
        if not token:
            db().execute("UPDATE notifications SET status='skipped', error=? WHERE id=?", ("Бот не настроен (нет токена)", r["id"]))
            continue
        try:
            import botlang
            lang = botlang.get("client", r["chat_id"])             # мижоз танлаган тил (Lumberture боти)
            tg.send_message(token, r["chat_id"], botlang.tr(r["text"], lang), botlang.tr_markup(r["markup"], lang))
            db().execute("UPDATE notifications SET status='sent', sent_at=?, attempts=attempts+1, error='' WHERE id=?", (now(), r["id"]))
            sent += 1
        except tg.TgError as e:
            att = r["attempts"] + 1
            if e.retryable and att < 6:
                db().execute("UPDATE notifications SET attempts=?, next_try=?, error=? WHERE id=?",
                             (att, time.time() + (e.retry_after or 60 * att), e.human(), r["id"]))
            else:
                db().execute("UPDATE notifications SET status='failed', attempts=?, error=? WHERE id=?", (att, e.human(), r["id"]))
                if e.code == 403 or (e.code == 400 and "chat not found" in (e.description or "").lower()):
                    db().execute("UPDATE tenant_chats SET active=0 WHERE chat_id=?", (r["chat_id"],))
                    db().execute("UPDATE client_chats SET active=0 WHERE chat_id=?", (r["chat_id"],))
            log.warning("send to %s failed: %s", r["chat_id"], e)
        except Exception as e:  # noqa
            log.exception("send failed")
            db().execute("UPDATE notifications SET status='failed', attempts=attempts+1, error=? WHERE id=?", (str(e)[:200], r["id"]))
        db().commit()
    return sent

# ---------------------------------------------------------------- входящие сообщения бота
def phone_key(s):
    d = re.sub(r"\D", "", s or "")
    return d[-9:] if len(d) >= 9 else ""

def link_chat(t, chat, frm):
    name = " ".join(x for x in [frm.get("first_name"), frm.get("last_name")] if x)
    db().execute("INSERT INTO tenant_chats(tenant_id,chat_id,tg_name,tg_username,linked_at,active) VALUES(?,?,?,?,?,1) "
                 "ON CONFLICT(tenant_id,chat_id) DO UPDATE SET active=1, tg_name=excluded.tg_name, tg_username=excluded.tg_username",
                 (t["id"], chat["id"], name, frm.get("username") or "", now()))
    db().commit()
    log.info("chat %s linked to tenant %s (%s)", chat["id"], t["id"], t["name"])

def linked_tenants(chat_id):
    return q("SELECT t.* FROM tenants t JOIN tenant_chats c ON c.tenant_id=t.id WHERE c.chat_id=? AND c.active=1 ORDER BY t.id", (chat_id,))

def reply(chat_id, text, markup=None, tid=None):
    db().execute("INSERT INTO notifications(tenant_id,chat_id,kind,text,markup,status,created_at) VALUES(?,?,?,?,?,?,?)",
                 (tid, chat_id, "reply" if tid is None else "balance", text, markup, "pending", now()))
    db().commit()
    outbox_event.set()

NOT_LINKED = ("Здравствуйте! Это бот уведомлений об аренде.\nЧтобы подключиться, откройте персональную ссылку, "
              "которую дал арендодатель, или нажмите кнопку ниже, чтобы отправить свой номер телефона.")

def handle_update(u):
    try:
        import online as _ol                      # Lumberture мижозлари: сайтга кириш коди, буюртма ҳолати
        if _ol.on_update(u) or not rent_bot_on():
            return                                # бот ижарага боғланмайди: ҳавола, баланс, рақам — ижара оқими ишламайди
    except Exception:
        log.exception("online bot update")
        if not rent_bot_on():
            return
    msg = u.get("message") or {}
    chat = msg.get("chat") or {}
    if not chat or chat.get("type") != "private":
        return
    cid, frm = chat["id"], msg.get("from") or {}
    text = (msg.get("text") or "").strip()
    contact = msg.get("contact")
    if contact:
        if contact.get("user_id") != frm.get("id"):
            return reply(cid, "Отправьте, пожалуйста, свой собственный номер кнопкой «📱 Отправить мой номер».", CONTACT_KB)
        key = phone_key(contact.get("phone_number"))
        found = [t for t in q("SELECT * FROM tenants WHERE active=1") if key and phone_key(t["phone"]) == key]
        cfound = [c for c in q("SELECT * FROM clients WHERE active=1") if key and phone_key(c["phone"]) == key]
        for c in cfound:
            link_client_chat(c, chat, frm)
            reply(cid, client_tg_welcome(c), BALANCE_KB)
        if not found and not cfound:
            return reply(cid, "Номер не найден среди арендаторов. Попросите у арендодателя персональную ссылку для подключения.")
        for t in found:
            link_chat(t, chat, frm)
            reply(cid, tenant_welcome(t), BALANCE_KB, t["id"])
        return
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        if code:
            t = q("SELECT * FROM tenants WHERE tg_code=? AND active=1", (code,), one=True)
            c = None if t else q("SELECT * FROM clients WHERE tg_code=? AND active=1", (code,), one=True)
            if c:
                link_client_chat(c, chat, frm)
                return reply(cid, client_tg_welcome(c), BALANCE_KB)
            if not t:
                return reply(cid, "Ссылка недействительна или устарела. Попросите у арендодателя новую ссылку.")
            link_chat(t, chat, frm)
            return reply(cid, tenant_welcome(t), BALANCE_KB, t["id"])
        ts, cs = linked_tenants(cid), linked_clients(cid)
        if not ts and not cs:
            return reply(cid, NOT_LINKED, CONTACT_KB)
        for t in ts:
            reply(cid, balance_message(t), BALANCE_KB, t["id"])
        for c in cs:
            reply(cid, client_balance_message(c), BALANCE_KB)
        return
    if text.startswith("/balance") or text in ("💰 Баланс", "Баланс", "баланс"):
        ts, cs = linked_tenants(cid), linked_clients(cid)
        if not ts and not cs:
            return reply(cid, NOT_LINKED, CONTACT_KB)
        for t in ts:
            reply(cid, balance_message(t), BALANCE_KB, t["id"])
        for c in cs:
            reply(cid, client_balance_message(c), BALANCE_KB)
        return
    if not linked_tenants(cid) and not linked_clients(cid):
        return reply(cid, NOT_LINKED, CONTACT_KB)
    phone = get_setting("contact_phone")
    reply(cid, "Команды:\n/balance — текущий баланс и последние начисления" +
          (f"\nПо вопросам: {phone}" if phone else ""), BALANCE_KB)

def poll_once(token, timeout=25):
    offset = int(get_setting("tg_offset") or 0)
    updates = tg.get_updates(token, offset, timeout) or []
    for upd in updates:
        set_settings(tg_offset=upd["update_id"] + 1)
        try:
            handle_update(upd)
        except Exception:
            log.exception("handle_update failed: %s", upd.get("update_id"))
    return len(updates)

# ---------------------------------------------------------------- фоновые задачи
_bg = {"started": False, "threads": {}}
_stop = threading.Event()

def _outbox_loop():
    while not _stop.is_set():
        try:
            with app.app_context():
                process_outbox()
                process_sms_outbox()
        except Exception:
            log.exception("outbox loop")
        outbox_event.wait(5)
        outbox_event.clear()

def _poll_loop():
    prepared = None
    while not _stop.is_set():
        pause = 0
        try:
            with app.app_context():
                token = get_setting("tg_token") if get_setting("bot_enabled") == "1" else ""
                if not token:
                    prepared, pause = None, 10
                else:
                    if prepared != token:
                        me = tg.get_me(token)
                        set_settings(tg_bot_username=me.get("username", ""), tg_bot_name=me.get("first_name", ""))
                        tg.prepare(token)
                        prepared = token
                        log.info("bot @%s started (long polling)", me.get("username"))
                    poll_once(token)
                    set_settings(tg_poll_ok_at=now(), tg_poll_error="")
        except tg.TgError as e:
            log.warning("polling: %s", e)
            pause = 60 if e.code in (401, 404, 409) else 15
            try:
                with app.app_context():
                    set_settings(tg_poll_error=e.human(), tg_poll_error_at=now())
            except Exception:
                pass
        except Exception:
            log.exception("polling loop")
            pause = 15
        if pause:
            _stop.wait(pause)

def scheduler_tick(dt=None):
    """Автосчета в день выставления и напоминания должникам (не раньше «часа уведомлений»)."""
    dt = dt or datetime.now()
    done = []
    if fx_due(dt):
        row, errs = fx_fetch()
        set_settings(fx_last_try_at=dt.strftime("%Y-%m-%d %H:%M:%S"))
        if row:
            set_settings(fx_auto_date=dt.date().isoformat())
        done.append("rate")
    if client_sms_due(dt):
        n = run_reminders(dt.date())
        set_settings(sms_last_run=dt.date().isoformat(), sms_last_run_at=now(), sms_last_run_count=n)
        log.info("reminders (tenants+clients): %s", n); done.append("reminders")
    if onec_due(dt):
        try:
            st_ = onec_sync()
            done.append("1c")
            log.info("1C sync: %s", st_)
        except onec.OneCError as e:
            set_settings(onec_last_at=now(), onec_last_error=str(e))
            errlog.error("1C sync: %s", e)
    if dt.hour < setting_int("notify_hour"):
        return done
    p = period_of(dt.date())
    bday = min(max(setting_int("billing_day"), 1), 28)
    if get_setting("auto_billing") == "1" and dt.day >= bday and get_setting("auto_billed_period", "") < p:
        created = generate_invoices(p, None, notify=get_setting("notify_invoice") == "1")
        set_settings(auto_billed_period=p, auto_billed_at=now(), auto_billed_count=len(created))
        log.info("auto billing %s: %s invoices", p, len(created)); done.append("billing")
    return done

def note_downtime():
    """Свет/компьютер ўчиб қолган бўлса: охирги «юрак уриши»дан бошлаб 1С ҳужжатларини қайта текшириш учун белгилаймиз."""
    prev = parse_float(get_setting("hb_at", "0"), 0)
    t = time.time()
    if prev and t - prev > 600:
        set_settings(c1_catchup_from=datetime.fromtimestamp(prev).date().isoformat(), c1_catchup_at=str(t),
                     downtime_from=datetime.fromtimestamp(prev).strftime("%Y-%m-%d %H:%M"), downtime_to=now()[:16])
        errlog.warning("downtime: %s → %s, катч-ап ёқилди", datetime.fromtimestamp(prev).strftime("%Y-%m-%d %H:%M"), now()[:16])
    set_settings(hb_at=str(t))

def _scheduler_loop():
    _stop.wait(10)
    try:
        with app.app_context():
            note_downtime()
    except Exception:
        errlog.exception("downtime check")
    while not _stop.is_set():
        try:
            with app.app_context():
                set_settings(hb_at=str(time.time()))
                scheduler_tick()
        except Exception:
            log.exception("scheduler")
        _stop.wait(60)

def _tgu_loop():
    _stop.wait(3)
    while not _stop.is_set():
        wait = 30
        try:
            with app.app_context():
                wait = process_tg_one()
        except Exception:
            log.exception("tg user loop")
        tg_event.wait(max(1.0, min(wait, 300)))
        tg_event.clear()

def _pwcheck_once():
    try:
        check_weak_passwords()
    except Exception as e:
        errlog.error("password check: %r", e)

def start_background_jobs():
    if _bg["started"]:
        return
    _bg["started"] = True
    for name, fn in [("outbox", _outbox_loop), ("telegram", _poll_loop), ("tguser", _tgu_loop), ("scheduler", _scheduler_loop),
                     ("pwcheck", _pwcheck_once)]:
        th = threading.Thread(target=fn, name=name, daemon=True)
        th.start()
        _bg["threads"][name] = th

def bg_alive(name):
    th = _bg["threads"].get(name)
    return bool(th and th.is_alive())

# ================================================================ СТРАНИЦЫ
def monthly_types(active_only=True):
    return q("SELECT * FROM charge_types WHERE kind IN ('fixed','area')" + (" AND active=1" if active_only else "") + " ORDER BY sort, id")

def meter_types(active_only=True):
    return q("SELECT * FROM charge_types WHERE kind='meter'" + (" AND active=1" if active_only else "") + " ORDER BY sort, id")

def meter_enabled(t, ct):
    tm = tenant_meter(t["id"], ct["id"])
    return bool(tm["enabled"]) if tm else bool(ct["apply_all"])

def tg_chats(tid):
    return q("SELECT * FROM tenant_chats WHERE tenant_id=? ORDER BY active DESC, id", (tid,))

def deep_link(t):
    """Ижара учун бот ҳаволаси йўқ — бот фақат Lumberture мижозлари учун (rent_bot_on)."""
    if not rent_bot_on():
        return ""
    bot = get_setting("tg_bot_username")
    return f"https://t.me/{bot}?start={t['tg_code']}" if bot and t["tg_code"] else ""

def setup_warnings():
    w = []
    fxr = fx_current()
    if not fxr:
        w.append((f"Курс доллара не загружен — суммы в сумах временно пересчитываются по условному курсу {money(parse_float(get_setting('fx_fallback_rate'), 12000))}. "
                  "Нажмите «Обновить курс» или введите курс вручную.", url_for("rates_page")))
    elif (date.today() - (parse_date(fxr["fetched_at"][:10]) or date.today())).days >= 3:
        w.append((f"Курс доллара давно не обновлялся (последний: {money(fxr['rate'])} от {fx_date(fxr)}).", url_for("rates_page")))
    if tgu_enabled() and not tgu_ready():
        w.append(("Telegram-аккаунт для уведомлений не подключён — сообщения арендаторам и клиентам не отправляются"
                  + (" (" + get_setting("tgu_auth_error") + ")" if get_setting("tgu_auth_error") else "") + ".", url_for("settings_page") + "#tgu"))
    if get_setting("tgu_peer_flood_at", "") and parse_float(get_setting("tgu_flood_until"), 0) > time.time():
        w.append(("Telegram ограничил аккаунт-отправитель (PEER_FLOOD, спам-жалобы). Отправка приостановлена на сутки — проверьте @SpamBot.", url_for("settings_page") + "#tgu"))
    if bot_on() and get_setting("tg_poll_error"):
        w.append(("Telegram-бот: " + get_setting("tg_poll_error"), url_for("settings_page") + "#bot"))
    for ct in meter_types():
        if not tariff_for(ct["id"], date.today()):
            w.append((f"Не задан тариф «{ct['name']}».", url_for("charge_types", edit=ct["id"])))
    rent = ct_by_code("rent")
    if rent and not tariff_for(rent["id"], date.today()) and q("SELECT 1 FROM tenants WHERE active=1 AND rent_amount<=0 AND (rent_rate IS NULL OR rent_rate<=0)", one=True):
        w.append(("Не задана общая ставка аренды за м² — у части арендаторов аренда не начислится.", url_for("charge_types", edit=rent["id"])))
    return w

@app.route("/")
@login_required
def dashboard():
    p = get_period()
    f, l = period_bounds(p)
    bal = all_balances()
    tenants = q("SELECT * FROM tenants WHERE active=1 ORDER BY name")
    fl = (f.isoformat(), l.isoformat())
    charged = {c_: scalar("SELECT SUM(amount) FROM charges WHERE charge_date BETWEEN ? AND ? AND source<>'opening' AND " + ("currency='USD'" if c_ == "USD" else "currency<>'USD'"), fl)
               for c_ in CURRENCIES}
    paid = {c_: scalar("SELECT SUM(amount) FROM payments WHERE pay_date BETWEEN ? AND ? AND " + ("currency='USD'" if c_ == "USD" else "currency<>'USD'"), fl)
            for c_ in CURRENCIES}
    paid_usd = to_usd(scalar("SELECT SUM(usd_part) FROM payments WHERE pay_date BETWEEN ? AND ?", fl),
                      scalar("SELECT SUM(uzs_part) FROM payments WHERE pay_date BETWEEN ? AND ?", fl))
    debt = round(sum(b for b in bal.values() if b > 0), 2)
    over = round(-sum(b for b in bal.values() if b < 0), 2)
    need_inv = [t for t in tenants if tenant_in_period(t, p) and not q("SELECT 1 FROM invoices WHERE tenant_id=? AND period=?", (t["id"], p), one=True)]
    rp = prev_period(p)
    missing_readings = []
    for ct in meter_types():
        n = sum(1 for t in tenants if tenant_in_period(t, rp) and meter_enabled(t, ct) and
                not q("SELECT 1 FROM meter_readings WHERE tenant_id=? AND charge_type_id=? AND period=?", (t["id"], ct["id"], rp), one=True))
        if n:
            missing_readings.append((ct, n))
    tgs = {r["tg_status"]: r["n"] for r in q("SELECT tg_status, COUNT(*) n FROM tenants WHERE active=1 GROUP BY tg_status")}
    linked = tgs.get("ok", 0)
    last_pay = {r["tenant_id"]: r["d"] for r in q("SELECT tenant_id, MAX(pay_date) d FROM payments GROUP BY tenant_id")}
    pks = all_pockets()
    deb = [dict(t=t, b=b, pk=pks.get(t["id"], (0, 0)), months=(b / monthly_fixed_total(t)) if monthly_fixed_total(t) else None,
                last=last_pay.get(t["id"]), tg=t["tg_status"]) for t, b in debtors()]
    by_cat = {}
    for t in tenants:
        c = by_cat.setdefault(t["category"] or "Без категории", [0, 0.0])
        c[0] += 1; c[1] += max(bal.get(t["id"], 0), 0)
    cbal = client_balances()
    client_debt = sum(v for v in cbal.values() if v > 0)
    client_debtors = sum(1 for v in cbal.values() if v > 0.5)
    pays = q("SELECT p.*, t.name, t.unit FROM payments p JOIN tenants t ON t.id=p.tenant_id ORDER BY p.pay_date DESC, p.id DESC LIMIT 6")
    # клиенты 1С: крупные должники (без собственных счетов — у них уведомления выключены)
    own = {r["id"] for r in q("SELECT id FROM clients WHERE sms_enabled=0")}
    cnames = {r["id"]: r for r in q("SELECT id, name, active FROM clients")}
    top_c = []
    for cid_, b in sorted(cbal.items(), key=lambda x: -x[1]):
        if b <= 0.5 or len(top_c) >= 8:
            break
        c = cnames.get(cid_)
        if not c or cid_ in own or not c["active"]:
            continue
        top_c.append(dict(c=c, b=b, days=client_overdue(cid_)[0]))
    t0 = date.today().isoformat()
    tgd = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM tg_out WHERE created_at>=? GROUP BY status", (t0,))}
    kas = None
    try:
        import kassa as _K
        kym = _K.ym_today()
        if _K.has_data(kym):
            mc = _K.month_calc(kym)
            o = mc["days"][date.today().day - 1]
            today_ok = o["filled"]
            if not today_ok:
                o = mc["last"]
            kas = dict(o=o, today=today_ok, mc=mc, title=_K.ym_title(kym), cash=o["fact"] or o["end"],
                       chart=[dict(day=x["day"], inc=x["inc"][0] / (x["rate"] or 1) + x["inc"][1], out=x["out"][0] / (x["rate"] or 1) + x["out"][1])
                              for x in mc["days"] if x["filled"]])
    except Exception:           # касса не должна ломать главную
        errlog.exception("dashboard kassa")
    return render_template("dashboard.html", p=p, top_c=top_c, tgd=tgd, kas=kas, client_debt_usd=client_debt / (fx_rate() or 1), charged=charged, paid=paid, paid_usd=paid_usd, charged_usd=to_usd(charged["USD"], charged["UZS"]),
                           debt=debt, over=over, n_tenants=len(tenants), fx_err=get_setting("fx_last_error", ""),
                           linked=linked, tgs=tgs, need_inv=need_inv, tgu=tgu_ready(), queue=scalar("SELECT COUNT(*) FROM tg_out WHERE status='pending'"), missing_readings=missing_readings, rp=rp, debtors=deb,
                           pays=pays, warnings=setup_warnings(), by_cat=by_cat, client_debt=client_debt,
                           client_debtors=client_debtors, nxt_sms=next_sms_run(), sms_mode=get_setting("sms_provider"),
                           prevp=prev_period(p), nextp=next_period(p))

# ---------------------------------------------------------------- арендаторы
@app.route("/tenants")
@login_required
def tenants():
    cat = request.args.get("category", "")
    show_all = request.args.get("all") == "1"
    sql, args = "SELECT * FROM tenants WHERE 1=1", []
    if not show_all:
        sql += " AND active=1"
    if cat:
        sql += " AND category=?"; args.append(cat)
    rows = q(sql + " ORDER BY active DESC, name", args)
    bal = all_balances()
    return render_template("tenants.html", rows=rows, bal=bal, cat=cat, show_all=show_all,
                           total_debt=sum(max(bal.get(r["id"], 0), 0) for r in rows),
                           monthly={r["id"]: monthly_totals(r) for r in rows})

@app.route("/tenants/new", methods=["GET", "POST"])
@app.route("/tenants/<int:tid>/edit", methods=["GET", "POST"])
@admin_required
def tenant_form(tid=None):
    t = get_tenant(tid) if tid else None
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        if not name:
            flash("Укажите название / ФИО арендатора", "error")
            return redirect(request.url)
        vals = (name, f.get("unit", "").strip(), parse_float(f.get("area")), f.get("phone", "").strip(), f.get("category", "").strip(),
                parse_float(f.get("rent_amount")), (parse_float(f.get("rent_rate")) or None) if f.get("rent_rate", "").strip() else None,
                f.get("contract_start") or None, f.get("contract_end") or None,
                f.get("notes", "").strip(), 1 if f.get("active") else 0, 1 if f.get("remind_enabled") else 0)
        if t:
            db().execute("UPDATE tenants SET name=?, unit=?, area=?, phone=?, category=?, rent_amount=?, rent_rate=?, contract_start=?, contract_end=?, "
                         "notes=?, active=?, remind_enabled=? WHERE id=?", vals + (tid,))
            if smsmod.normalize_phone(t["phone"]) != smsmod.normalize_phone(vals[3]):
                db().execute("UPDATE tenants SET tg_status='', tg_user_id=NULL WHERE id=?", (tid,))
        else:
            tid = db().execute("INSERT INTO tenants(name,unit,area,phone,category,rent_amount,rent_rate,contract_start,contract_end,notes,active,"
                               "remind_enabled,tg_code,is_demo,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               vals + (new_code(), 1 if f.get("is_demo") else 0, now())).lastrowid
        for ct in monthly_types(False):
            if f"fx_present_{ct['id']}" not in f:
                continue
            raw = f.get(f"fx_amt_{ct['id']}", "").strip()
            db().execute("INSERT INTO tenant_fixed(tenant_id,charge_type_id,enabled,amount) VALUES(?,?,?,?) "
                         "ON CONFLICT(tenant_id,charge_type_id) DO UPDATE SET enabled=excluded.enabled, amount=excluded.amount",
                         (tid, ct["id"], 1 if f.get(f"fx_en_{ct['id']}") else 0, parse_float(raw) if raw else None))
        for ct in meter_types(False):
            if f"m_present_{ct['id']}" not in f:
                continue
            raw = f.get(f"m_tariff_{ct['id']}", "").strip()
            db().execute("INSERT INTO tenant_meters(tenant_id,charge_type_id,meter_no,initial_reading,tariff_override,enabled) VALUES(?,?,?,?,?,?) "
                         "ON CONFLICT(tenant_id,charge_type_id) DO UPDATE SET meter_no=excluded.meter_no, initial_reading=excluded.initial_reading, "
                         "tariff_override=excluded.tariff_override, enabled=excluded.enabled",
                         (tid, ct["id"], f.get(f"m_no_{ct['id']}", "").strip(), parse_float(f.get(f"m_init_{ct['id']}")),
                          parse_float(raw) if raw else None, 1 if f.get(f"m_en_{ct['id']}") else 0))
        if "lang" in f:
            db().execute("UPDATE tenants SET lang=? WHERE id=?", (f.get("lang") if f.get("lang") in LANGS else "", tid))
        ou, oz, od, on_ = opening_from_form(f, "op_")
        opres = apply_tenant_opening(tid, ou, oz, od, on_, current_user()["id"])
        db().commit()
        if vals[3] and not smsmod.normalize_phone(vals[3]):
            flash("Внимание: номер не похож на узбекский мобильный (+998 XX XXX XX XX) — уведомления по нему не уйдут", "warn")
        flash("Арендатор сохранён" + ("; начальный долг записан (сообщение арендатору не отправлялось)" if opres else ""), "ok")
        return redirect(url_for("tenant_card", tid=tid))
    fx = {r["charge_type_id"]: r for r in q("SELECT * FROM tenant_fixed WHERE tenant_id=?", (tid,))} if t else {}
    ms = {r["charge_type_id"]: r for r in q("SELECT * FROM tenant_meters WHERE tenant_id=?", (tid,))} if t else {}
    has_readings = {r["charge_type_id"] for r in q("SELECT DISTINCT charge_type_id FROM meter_readings WHERE tenant_id=?", (tid,))} if t else set()
    rent = ct_by_code("rent")
    return render_template("tenant_form.html", t=t, op=tenant_openings(tid) if t else {"USD": None, "UZS": None, "note": "", "date": ""},
                           op_default=opening_default_date(tid if t else None, t["contract_start"] if t else None),
                           monthly=monthly_types(), global_rate=tariff_for(rent["id"], date.today()) if rent else 0,
                           rent_cur=rent["currency"] if rent else "USD", meters=meter_types(), fx=fx, ms=ms, has_readings=has_readings)

@app.route("/tenants/<int:tid>")
@login_required
def tenant_card(tid):
    t = get_tenant(tid)
    led = ledger(tid)
    led["rows"].reverse()
    link = deep_link(t)
    qr = ""
    if link:
        import segno
        qr = segno.make(link, error="m").svg_inline(scale=4, dark="#1f2a27")
    p = period_of()
    readings = q("SELECT r.*, ct.name ct_name, ct.unit ct_unit, COALESCE(c.currency, ct.currency) cur FROM meter_readings r "
                 "JOIN charge_types ct ON ct.id=r.charge_type_id LEFT JOIN charges c ON c.id=r.charge_id "
                 "WHERE r.tenant_id=? ORDER BY r.period DESC, ct.sort LIMIT 24", (tid,))
    mu, mz = monthly_totals(t)
    return render_template("tenant_card.html", t=t, led=led, balance=led["closing"], pk=led["closing_p"], link=link if rent_bot_on() else "", qr=qr, chats=tg_chats(tid),
                           msgs=messages_for(tenant_id=tid), chan=predict_channel(t, True), phone_ok=bool(smsmod.normalize_phone(t["phone"])),
                           lines=fixed_lines(t), monthly_text=totals_text(mu, mz), readings=readings, rate=rent_rate(t),
                           balance_uzs=usd_uzs(abs(led["closing"])), detail=pockets_text(*led["closing_p"]),
                           need_invoice=tenant_in_period(t, p) and not q("SELECT 1 FROM invoices WHERE tenant_id=? AND period=?", (tid, p), one=True),
                           ctypes=q("SELECT * FROM charge_types WHERE active=1 AND kind NOT IN ('meter','opening') ORDER BY sort, id"),
                           op=tenant_openings(tid), op_default=opening_default_date(tid, t["contract_start"]),
                           bot=get_setting("tg_bot_username"), bot_enabled=rent_bot_on())

def insert_payment(t, d, amount, currency, method, note="", uid=None, is_demo=None):
    """Оплата хранится в валюте оплаты + курс дня + разбивка по карманам ($ / сумы)."""
    currency = currency if currency in CURRENCIES else "UZS"
    rate = fx_rate()
    if not rate and currency == "UZS" and tenant_pockets(t["id"])[0] > EPS:
        log.warning("payment in UZS without fx rate: tenant %s — goes to UZS pocket", t["id"])
    up, zp = allocate_payment(t["id"], amount, currency, rate)
    return db().execute("INSERT INTO payments(tenant_id,pay_date,amount,method,note,created_by,created_at,is_demo,currency,fx_rate,usd_part,uzs_part) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (t["id"], d, amount, method, note, uid, now(), t["is_demo"] if is_demo is None else is_demo,
                         currency, rate, up, zp)).lastrowid

def record_payment(tid, f):
    amount = parse_float(f.get("amount"))
    if amount <= 0:
        raise ValueError("Сумма оплаты должна быть больше 0")
    d = parse_date(f.get("pay_date"), date.today())
    method = f.get("method") if f.get("method") in METHODS else "cash"
    currency = f.get("currency") if f.get("currency") in CURRENCIES else "USD"
    t = get_tenant(tid)
    pid = insert_payment(t, d.isoformat(), amount, currency, method, f.get("note", "").strip(), current_user()["id"])
    db().commit()
    b = tenant_balance(tid)
    pr = q("SELECT * FROM payments WHERE id=?", (pid,), one=True)
    msg = f"Оплата {pay_text(pr)} записана: {t['name']}. " + ("Долг: " + usd_uzs(b) if b >= EPS else "Переплата: " + usd_uzs(-b) if b <= -EPS else "Долга нет")
    if f.get("notify") and get_setting("notify_payment") == "1":
        msg += ". " + channel_note(safe_notify(notify_payment, pid)).capitalize()
    flash(msg, "ok")
    return pid

@app.route("/tenants/<int:tid>/payment", methods=["POST"])
@login_required
def tenant_payment(tid):
    try:
        record_payment(tid, request.form)
    except ValueError as e:
        flash(f"Ошибка: {e}", "error")
    return redirect(request.form.get("back") or url_for("tenant_card", tid=tid))

@app.route("/tenants/<int:tid>/charge", methods=["POST"])
@login_required
def tenant_charge(tid):
    t, f = get_tenant(tid), request.form
    ct = q("SELECT * FROM charge_types WHERE id=?", (f.get("charge_type_id"),), one=True)
    amount = parse_float(f.get("amount"))
    try:
        if not ct:
            raise ValueError("Выберите вид начисления")
        if ct["kind"] == "opening":
            raise ValueError("Начальный долг вносится в блоке «Начальный долг (перенос)»")
        if not amount:
            raise ValueError("Укажите сумму (для скидки — со знаком минус)")
        if amount < 0 and not is_admin():
            raise ValueError("Скидку/корректировку может внести только администратор")
        d = parse_date(f.get("charge_date"), date.today())
        p = f.get("period") if valid_period(f.get("period")) else period_of(d)
        desc = f.get("description", "").strip() or f"{ct['name']} за {period_name(p)}"
        currency = f.get("currency") if f.get("currency") in CURRENCIES else ct["currency"]
        add_charge(tid, ct["id"], p, d.isoformat(), desc, amount, uid=current_user()["id"], is_demo=t["is_demo"], currency=currency)
        db().commit()
        flash(f"Начислено {cur(amount, currency)}: {desc}", "ok")
    except ValueError as e:
        flash(f"Ошибка: {e}", "error")
    return redirect(url_for("tenant_card", tid=tid))

@app.route("/tenants/<int:tid>/invoice", methods=["POST"])
@login_required
def tenant_invoice(tid):
    t = get_tenant(tid)
    p = get_period()
    if request.form.get("action") == "resend":
        res = safe_notify(notify_invoice, tid, p)
        flash("Счёт: " + channel_note(res), "ok" if res[0] else "warn")
    elif generate_invoice(t, p, current_user()["id"]):
        db().commit()
        flash(f"Счёт за {period_name(p)} сформирован", "ok")
        if request.form.get("notify") and get_setting("notify_invoice") == "1":
            safe_notify(notify_invoice, tid, p)
    else:
        flash(f"Счёт за {period_name(p)} уже был сформирован", "warn")
    return redirect(request.form.get("back") or url_for("tenant_card", tid=tid))

@app.route("/tenants/<int:tid>/send-balance", methods=["POST"])
@login_required
def tenant_send_balance(tid):
    t = get_tenant(tid)
    via = request.form.get("via", "auto")
    res = safe_notify(deliver, "balance", balance_message(t), t, None, via if via in ("auto", "tg", "sms") else "auto")
    flash("Баланс: " + channel_note(res), "ok" if res[0] else "warn")
    return redirect(url_for("tenant_card", tid=tid))

@app.route("/tenants/<int:tid>/tg", methods=["POST"])
@login_required
def tenant_tg(tid):
    get_tenant(tid)
    act = request.form.get("action")
    if act in ("regen", "unlink") and not is_admin():
        abort(403)
    if act == "regen":
        db().execute("UPDATE tenants SET tg_code=? WHERE id=?", (new_code(), tid))
        flash("Создана новая ссылка. Старая ссылка больше не работает (уже подключённые чаты остаются)", "ok")
    elif act == "unlink":
        db().execute("DELETE FROM tenant_chats WHERE id=? AND tenant_id=?", (request.form.get("chat"), tid))
        flash("Чат отключён от уведомлений", "ok")
    elif act == "recheck":
        db().execute("UPDATE tenants SET tg_status='', tg_user_id=NULL WHERE id=?", (tid,))
        flash("Статус Telegram сброшен — номер будет проверен при следующей отправке", "ok")
    db().commit()
    return redirect(url_for("tenant_card", tid=tid))

@app.route("/tenants/<int:tid>/qr.png")
@login_required
def tenant_qr(tid):
    link = deep_link(get_tenant(tid))
    if not link:
        abort(404)
    import segno
    buf = io.BytesIO()
    segno.make(link, error="m").save(buf, kind="png", scale=10, border=2)
    return Response(buf.getvalue(), mimetype="image/png")

@app.route("/tenants/<int:tid>/tg-card")
@login_required
def tenant_tg_card(tid):
    t = get_tenant(tid)
    link = deep_link(t)
    if not link:
        flash("Аренда не подключается к боту: сообщения арендаторам идут с Telegram-аккаунта", "warn")
        return redirect(url_for("tenant_card", tid=tid))
    import segno
    return render_template("tg_card.html", t=t, link=link, bot=get_setting("tg_bot_username"),
                           qr=segno.make(link, error="m").svg_inline(scale=7))

@app.route("/tenants/<int:tid>/opening", methods=["POST"])
@admin_required
def tenant_opening(tid):
    get_tenant(tid)
    ou, oz, od, on_ = opening_from_form(request.form, "op_")
    if request.form.get("action") == "delete":
        ou, oz = 0.0, 0.0
    res = apply_tenant_opening(tid, ou, oz, od, on_, current_user()["id"])
    db().commit()
    op = tenant_openings(tid)
    parts = [cur(op[k]["amount"], k) for k in ("USD", "UZS") if op[k]]
    flash(("Начальный долг сохранён: " + " + ".join(parts) if parts else "Начальный долг удалён") +
          f". Баланс: {usd_uzs(tenant_balance(tid))}. Сообщение арендатору не отправлялось" if res else "Начальный долг не изменился", "ok")
    return redirect(request.form.get("back") or url_for("tenant_card", tid=tid))

@app.route("/charges/<int:cid>/delete", methods=["POST"])
@admin_required
def charge_delete(cid):
    c = q("SELECT * FROM charges WHERE id=?", (cid,), one=True) or abort(404)
    r = q("SELECT id FROM meter_readings WHERE charge_id=?", (cid,), one=True)
    if r:
        delete_reading(r["id"])
    else:
        db().execute("DELETE FROM charges WHERE id=?", (cid,))
    db().commit()
    flash("Начисление удалено" + (" (вместе с показанием счётчика)" if r else ""), "ok")
    return redirect(request.form.get("back") or url_for("tenant_card", tid=c["tenant_id"]))

@app.route("/payments/<int:pid>/delete", methods=["POST"])
@admin_required
def payment_delete(pid):
    p = q("SELECT * FROM payments WHERE id=?", (pid,), one=True) or abort(404)
    db().execute("DELETE FROM payments WHERE id=?", (pid,))
    db().commit()
    flash(f"Оплата {cur(p['amount'], p['currency'])} удалена", "ok")
    return redirect(request.form.get("back") or url_for("tenant_card", tid=p["tenant_id"]))

# ---------------------------------------------------------------- оплаты
@app.route("/payments", methods=["GET", "POST"])
@login_required
def payments():
    if request.method == "POST":
        try:
            tid = int(request.form.get("tenant_id") or 0)
            if not q("SELECT 1 FROM tenants WHERE id=?", (tid,), one=True):
                raise ValueError("Выберите арендатора")
            record_payment(tid, request.form)
        except ValueError as e:
            flash(f"Ошибка: {e}", "error")
        return redirect(url_for("payments", period=get_period()))
    p = get_period()
    f, l = period_bounds(p)
    rows = q("SELECT p.*, t.name, t.unit, t.category FROM payments p JOIN tenants t ON t.id=p.tenant_id WHERE p.pay_date BETWEEN ? AND ? "
             "ORDER BY p.pay_date DESC, p.id DESC", (f.isoformat(), l.isoformat()))
    by_method, total = {}, {"USD": 0.0, "UZS": 0.0}
    for r in rows:
        k = "USD" if r["currency"] == "USD" else "UZS"
        by_method.setdefault(r["method"], {"USD": 0.0, "UZS": 0.0})[k] += r["amount"]
        total[k] += r["amount"]
    bal = all_balances()
    return render_template("payments.html", p=p, rows=rows, by_method=by_method, total=total, pay_text=pay_text,
                           total_usd=to_usd(sum((r["usd_part"] or 0) for r in rows), sum((r["uzs_part"] or 0) for r in rows)),
                           tenants=q("SELECT * FROM tenants WHERE active=1 ORDER BY name"), bal=bal, pks=all_pockets(),
                           sel=request.args.get("tenant", type=int), prevp=prev_period(p), nextp=next_period(p))

# ---------------------------------------------------------------- показания счётчиков
@app.route("/readings", methods=["GET", "POST"])
@login_required
def readings():
    mts = meter_types()
    if not mts:
        return render_template("message.html", title="Показания", text="Нет видов начислений «по счётчику». Добавьте их в «Виды начислений».")
    default_p = prev_period(period_of()) if date.today().day <= 15 else period_of()
    p = get_period(default=default_p)
    ct = next((m for m in mts if str(m["id"]) == (request.values.get("ct") or "")), mts[0])
    rows = [t for t in q("SELECT * FROM tenants WHERE active=1 ORDER BY unit, name") if tenant_in_period(t, p) and meter_enabled(t, ct)]
    if request.method == "POST":
        rdate = (parse_date(request.form.get("reading_date"), date.today())).isoformat()
        errors, changed = [], []
        for t in rows:
            raw = request.form.get(f"cur_{t['id']}", "").strip()
            if raw == "":
                continue
            val = parse_float(raw, None)
            if val is None or val < 0:
                errors.append(f"{t['name']}: неверное число «{raw}»")
                continue
            try:
                rid, ch = save_reading(t, ct, p, val, rdate, current_user()["id"])
                db().commit()
                if ch:
                    changed.append(rid)
            except ValueError as e:
                db().rollback()
                errors.append(str(e))
        for e in errors:
            flash(e, "error")
        if changed:
            sent = 0
            if request.form.get("notify") and get_setting("notify_meter") == "1":
                sent = sum(queued(safe_notify(notify_meter, rid)) for rid in changed)
            flash(f"Сохранено показаний: {len(changed)}. Начисления добавлены в баланс арендаторов" +
                  (f"; уведомлений в очереди: {sent}" if sent else ""), "ok")
        elif not errors:
            flash("Изменений нет", "warn")
        return redirect(url_for("readings", period=p, ct=ct["id"]))
    data = []
    for t in rows:
        prev, prev_p = meter_prev(t["id"], ct["id"], p)
        ex = q("SELECT * FROM meter_readings WHERE tenant_id=? AND charge_type_id=? AND period=?", (t["id"], ct["id"], p), one=True)
        tm = tenant_meter(t["id"], ct["id"])
        data.append(dict(t=t, prev=prev, prev_p=prev_p, ex=ex, meter_no=tm["meter_no"] if tm else "",
                         tariff=meter_tariff(t["id"], ct["id"], p),
                         tg=bool(q("SELECT 1 FROM tenant_chats WHERE tenant_id=? AND active=1", (t["id"],), one=True))))
    return render_template("readings.html", p=p, ct=ct, mts=mts, data=data, done=sum(1 for d in data if d["ex"]), rc=ct["currency"],
                           total=sum(d["ex"]["amount"] for d in data if d["ex"]), prevp=prev_period(p), nextp=next_period(p))

@app.route("/readings/<int:rid>/delete", methods=["POST"])
@admin_required
def reading_delete(rid):
    r = q("SELECT * FROM meter_readings WHERE id=?", (rid,), one=True) or abort(404)
    delete_reading(rid)
    db().commit()
    flash("Показание и его начисление удалены", "ok")
    return redirect(url_for("readings", period=r["period"], ct=r["charge_type_id"]))

# ---------------------------------------------------------------- ежемесячные счета
@app.route("/billing", methods=["GET", "POST"])
@login_required
def billing():
    p = get_period()
    if request.method == "POST":
        act = request.form.get("action")
        if act == "generate":
            notify = bool(request.form.get("notify")) and get_setting("notify_invoice") == "1"
            created = generate_invoices(p, current_user()["id"], notify=False)
            sent = sum(queued(safe_notify(notify_invoice, tid, p)) for tid in created) if notify else 0
            flash(f"Сформировано счетов за {period_name(p)}: {len(created)}" + (f"; уведомлений в очереди: {sent}" if notify else ""),
                  "ok" if created else "warn")
        elif act == "remind":
            n = send_reminders()
            flash(f"Напоминания поставлены в очередь: {n}. Ход отправки — «Журнал сообщений»" if n else
                  "Некому отправить: нет должников выше порога, у них нет Telegram или аккаунт не подключён (см. «Напоминания»)", "ok" if n else "warn")
        return redirect(url_for("billing", period=p))
    rows = []
    for t in q("SELECT * FROM tenants WHERE active=1 OR id IN (SELECT tenant_id FROM invoices WHERE period=?) ORDER BY unit, name", (p,)):
        inv = q("SELECT * FROM invoices WHERE tenant_id=? AND period=?", (t["id"], p), one=True)
        lines = q("SELECT * FROM charges WHERE tenant_id=? AND period=? AND source<>'opening' ORDER BY charge_date, id", (t["id"], p))
        if not inv and not lines and not tenant_in_period(t, p):
            continue
        u = sum(c["amount"] for c in lines if c["currency"] == "USD")
        z = sum(c["amount"] for c in lines if c["currency"] != "USD")
        rows.append(dict(t=t, inv=inv, lines=lines, total=(u, z), total_usd=to_usd(u, z),
                         preview=None if inv else monthly_totals(t, p)))
    return render_template("billing.html", p=p, rows=rows, prevp=prev_period(p), nextp=next_period(p),
                           missing=sum(1 for r in rows if not r["inv"] and tenant_in_period(r["t"], p)),
                           auto=get_setting("auto_billing") == "1", bday=setting_int("billing_day"))

# ---------------------------------------------------------------- отчёты
def build_report(p, cat=""):
    """Сводка за месяц. Начисления — по видам сборов в своей валюте; оплаты — в валюте оплаты;
    сальдо — в $ по ТЕКУЩЕМУ курсу (долг в $ + долг в сумах / курс)."""
    f, l = period_bounds(p)
    f1, l1, nxt = f.isoformat(), l.isoformat(), (l + timedelta(days=1)).isoformat()
    open_p, close_p = all_pockets(f1), all_pockets(nxt)
    rate = fx_rate()
    by = {}
    for r in q("SELECT tenant_id, charge_type_id, currency, SUM(amount) s FROM charges WHERE charge_date BETWEEN ? AND ? "
               "GROUP BY tenant_id, charge_type_id, currency", (f1, l1)):
        by.setdefault(r["tenant_id"], {})[(r["charge_type_id"], r["currency"] if r["currency"] == "USD" else "UZS")] = r["s"]
    paid = {}
    for r in q("SELECT tenant_id, currency, SUM(amount) s FROM payments WHERE pay_date BETWEEN ? AND ? GROUP BY tenant_id, currency", (f1, l1)):
        paid.setdefault(r["tenant_id"], {"USD": 0.0, "UZS": 0.0})["USD" if r["currency"] == "USD" else "UZS"] += r["s"]
    used = {k for d in by.values() for k in d}
    cols = [dict(id=(ct["id"], c_), name=ct["name"], cur=c_) for ct in q("SELECT * FROM charge_types ORDER BY sort, id")
            for c_ in ("USD", "UZS") if (ct["id"], c_) in used]
    rows = []
    for t in q("SELECT * FROM tenants ORDER BY category, unit, name"):
        if cat and (t["category"] or "") != cat:
            continue
        op, cp = open_p.get(t["id"], (0.0, 0.0)), close_p.get(t["id"], (0.0, 0.0))
        o, c = to_usd(*op, rate), to_usd(*cp, rate)
        ch = by.get(t["id"], {})
        pd = paid.get(t["id"], {"USD": 0.0, "UZS": 0.0})
        if not t["active"] and not ch and not (pd["USD"] or pd["UZS"]) and abs(o) < EPS and abs(c) < EPS:
            continue
        chg = {"USD": round(sum(v for (i, c_), v in ch.items() if c_ == "USD"), 2), "UZS": round(sum(v for (i, c_), v in ch.items() if c_ == "UZS"), 2)}
        rows.append(dict(t=t, opening=o, opening_p=op, by=ch, charged=chg, charged_usd=to_usd(chg["USD"], chg["UZS"], rate),
                         paid=pd, closing=c, closing_p=cp))
    tot = dict(opening=round(sum(r["opening"] for r in rows), 2), closing=round(sum(r["closing"] for r in rows), 2),
               charged={k: round(sum(r["charged"][k] for r in rows), 2) for k in ("USD", "UZS")},
               paid={k: round(sum(r["paid"][k] for r in rows), 2) for k in ("USD", "UZS")},
               debt=round(sum(max(r["closing"], 0) for r in rows), 2),
               by={c_["id"]: sum(r["by"].get(c_["id"], 0) for r in rows) for c_ in cols})
    tot["charged_usd"] = to_usd(tot["charged"]["USD"], tot["charged"]["UZS"], rate)
    return cols, rows, tot

@app.route("/reports")
@login_required
def reports():
    p, cat = get_period(), request.args.get("category", "")
    cols, rows, tot = build_report(p, cat)
    return render_template("reports.html", p=p, cat=cat, cols=cols, rows=rows, tot=tot, prevp=prev_period(p), nextp=next_period(p))

def statement_range():
    d1, d2 = parse_date(request.args.get("start")), parse_date(request.args.get("end"))
    if d1 and d2 and d1 <= d2:
        return d1, d2, f"{ddmm(d1)} – {ddmm(d2)}"
    p = get_period()
    f, l = period_bounds(p)
    return f, l, period_name(p)

@app.route("/tenants/<int:tid>/statement")
@login_required
def statement(tid):
    t = get_tenant(tid)
    d1, d2, label = statement_range()
    return render_template("statement.html", t=t, led=ledger(tid, d1, d2), d1=d1, d2=d2, label=label, p=period_of(d1),
                           company=get_setting("company_name"))

# ---- Excel
def _xl():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    return Workbook, Font, PatternFill, Alignment, Border, Side

def _xl_header(ws, cols, row):
    import xlbrand
    for i, h in enumerate(cols, 1):
        ws.cell(row=row, column=i, value=h)
    xlbrand.style_header(ws, row, len(cols))

def _xl_statement(ws, t, led, label):
    _, Font, *_ = _xl()
    ws["A1"] = f"Выписка: {t['name']} — {t['unit'] or ''}"; ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"Период: {label}. Баланс — в $ по текущему курсу ({fx_label()}); долг в сумах пересчитывается по курсу на дату выписки"
    ws["A3"] = "Сальдо на начало, $ (+ долг / − переплата)"; ws["G3"] = led["opening"]
    _xl_header(ws, ["Дата", "Период", "Операция", "Валюта", "Начислено", "Оплачено", "Баланс, $"], 5)
    r = 6
    for x in led["rows"]:
        ws.append([parse_date(x["date"]), period_name(x["period"]) if x["period"] else "", x["text"], "$" if x["cur"] == "USD" else "сум",
                   x["charge"] or None, x["payment"] or None, x["balance"]])
        ws.cell(row=r, column=1).number_format = "DD.MM.YYYY"
        for col in (5, 6):
            ws.cell(row=r, column=col).number_format = "# ##0.00" if x["cur"] == "USD" else "# ##0"
        r += 1
    for k, lbl in (("USD", "$"), ("UZS", "сум")):
        ws.append(["", "", f"Итого в {lbl}", lbl, led["charged"][k], led["paid"][k], None])
        ws.cell(row=r, column=5).number_format = ws.cell(row=r, column=6).number_format = "# ##0.00" if k == "USD" else "# ##0"
        for c in ws[r]:
            c.font = Font(bold=True)
        r += 1
    ws.append(["", "", "Сальдо на конец, $", "$", None, None, led["closing"]])
    for c in ws[r]:
        c.font = Font(bold=True)
    u, z = led["closing_p"]
    ws.cell(row=r + 1, column=3, value=f"в т.ч. по валютам: {usd(u)} и {money(z)} сум")
    for col, w in zip("ABCDEFG", [12, 16, 60, 8, 15, 15, 15]):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=3, min_col=7, max_col=7):
        for c in row:
            c.number_format = "# ##0.00"

def _xlsx_response(wb, name):
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/reports.xlsx")
@login_required
def reports_xlsx():
    Workbook, Font, *_ = _xl()
    p, cat = get_period(), request.args.get("category", "")
    cols, rows, tot = build_report(p, cat)
    wb = Workbook(); ws = wb.active; ws.title = "Сводка"
    ws["A1"] = f"Арендаторы — сводка за {period_name(p)}" + (f" ({cat})" if cat else ""); ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"Сальдо в $: + долг арендатора, − переплата. Долг в сумах пересчитан по текущему курсу ({fx_label()})"
    head = (["Арендатор", "Категория", "Помещение", "Сальдо на начало, $"] + [f"{c['name']}, {'$' if c['cur'] == 'USD' else 'сум'}" for c in cols]
            + ["Начислено, $", "Начислено, сум", "Оплачено, $", "Оплачено, сум", "Сальдо на конец, $"])
    _xl_header(ws, head, 4)
    for r in rows:
        ws.append([r["t"]["name"], r["t"]["category"], r["t"]["unit"], r["opening"]] + [r["by"].get(c["id"], 0) for c in cols]
                  + [r["charged"]["USD"], r["charged"]["UZS"], r["paid"]["USD"], r["paid"]["UZS"], r["closing"]])
    ws.append(["ИТОГО", "", "", tot["opening"]] + [tot["by"][c["id"]] for c in cols]
              + [tot["charged"]["USD"], tot["charged"]["UZS"], tot["paid"]["USD"], tot["paid"]["UZS"], tot["closing"]])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for row in ws.iter_rows(min_row=5, min_col=4):
        for c in row:
            h = ws.cell(row=4, column=c.column).value or ""
            c.number_format = "# ##0" if h.endswith("сум") else "# ##0.00"
    ws.column_dimensions["A"].width = 32; ws.column_dimensions["B"].width = 14; ws.column_dimensions["C"].width = 16
    for i in range(4, len(head) + 1):
        ws.column_dimensions[ws.cell(row=4, column=i).column_letter].width = 15
    ws.freeze_panes = "B5"
    f, l = period_bounds(p)
    used = set()
    for r in rows:
        t = r["t"]
        title = re.sub(r"[\[\]\:\*\?\/\\]", "", f"{t['unit'] or ''} {t['name']}".strip())[:28] or f"id{t['id']}"
        while title in used:
            title = title[:25] + f"_{t['id']}"
        used.add(title)
        _xl_statement(wb.create_sheet(title), t, ledger(t["id"], f, l), period_name(p))
    return _xlsx_response(wb, f"arenda_{p}.xlsx")

@app.route("/tenants/<int:tid>/statement.xlsx")
@login_required
def statement_xlsx(tid):
    Workbook, *_ = _xl()
    t = get_tenant(tid)
    d1, d2, label = statement_range()
    wb = Workbook(); ws = wb.active; ws.title = "Выписка"
    _xl_statement(ws, t, ledger(tid, d1, d2), label)
    return _xlsx_response(wb, f"vypiska_{tid}_{d1.isoformat()}_{d2.isoformat()}.xlsx")

# ---------------------------------------------------------------- виды начислений (сборов)
@app.route("/admin/charge-types", methods=["GET", "POST"])
@admin_required
def charge_types():
    if request.method == "POST":
        f, act = request.form, request.form.get("action")
        try:
            if act == "save":
                name = f.get("name", "").strip()
                if not name:
                    raise ValueError("Укажите название")
                ct = q("SELECT * FROM charge_types WHERE id=?", (f.get("id"),), one=True) if f.get("id") else None
                if ct and ct["kind"] == "opening":
                    raise ValueError("Служебный вид «Долг на начало» не редактируется")
                kind = f.get("kind") if f.get("kind") in KINDS and f.get("kind") != "rent" else "fixed"
                if ct and ct["kind"] == "rent":
                    kind = "rent"
                elif ct and ct["kind"] != kind and q("SELECT 1 FROM charges WHERE charge_type_id=?", (ct["id"],), one=True) \
                        and "meter" in (ct["kind"], kind):
                    raise ValueError("Нельзя менять способ расчёта «по счётчику» у вида, по которому уже есть начисления. Создайте новый вид")
                currency = f.get("currency") if f.get("currency") in CURRENCIES else (ct["currency"] if ct else "UZS")
                vals = (name, kind, parse_float(f.get("default_amount")), 1 if f.get("apply_all") else 0,
                        f.get("unit", "").strip() or ("кВт·ч" if kind == "meter" else ""), int(parse_float(f.get("sort"), 100)),
                        1 if f.get("active") else 0, currency)
                if ct:
                    db().execute("UPDATE charge_types SET name=?, kind=?, default_amount=?, apply_all=?, unit=?, sort=?, active=?, currency=? WHERE id=?", vals + (ct["id"],))
                    cid = ct["id"]
                    if ct["currency"] != currency:
                        flash(f"Валюта «{name}» изменена на {CURRENCIES[currency]}. Уже сделанные начисления остаются в прежней валюте. "
                              "Проверьте сумму по умолчанию, тарифы/ставки и индивидуальные суммы арендаторов — они теперь считаются в новой валюте", "warn")
                else:
                    cid = db().execute("INSERT INTO charge_types(name,kind,default_amount,apply_all,unit,sort,active,currency) VALUES(?,?,?,?,?,?,?,?)", vals).lastrowid
                if kind in ("meter", "rent") and parse_float(f.get("tariff")) > 0:
                    db().execute("INSERT INTO tariffs(charge_type_id,valid_from,price,created_at) VALUES(?,?,?,?) "
                                 "ON CONFLICT(charge_type_id,valid_from) DO UPDATE SET price=excluded.price",
                                 (cid, parse_date(f.get("valid_from"), date(2020, 1, 1)).isoformat(), parse_float(f.get("tariff")), now()))
                db().commit()
                flash(f"Вид начисления «{name}» сохранён", "ok")
                return redirect(url_for("charge_types", edit=cid))
            if act == "tariff":
                cid, price = int(f.get("id")), parse_float(f.get("tariff"))
                d = parse_date(f.get("valid_from"))
                if price <= 0 or not d:
                    raise ValueError("Укажите тариф и дату, с которой он действует")
                db().execute("INSERT INTO tariffs(charge_type_id,valid_from,price,created_at) VALUES(?,?,?,?) "
                             "ON CONFLICT(charge_type_id,valid_from) DO UPDATE SET price=excluded.price", (cid, d.isoformat(), price, now()))
                db().commit()
                flash(f"Тариф/ставка {cur(price, ct_currency(cid))} действует с {ddmm(d)}. Уже выставленные начисления не пересчитываются", "ok")
                return redirect(url_for("charge_types", edit=cid))
            if act == "tariff_delete":
                db().execute("DELETE FROM tariffs WHERE id=?", (f.get("tariff_id"),)); db().commit()
                flash("Тариф удалён", "ok")
                return redirect(url_for("charge_types", edit=f.get("id")))
            if act == "assign":
                cid = int(f.get("id"))
                ct = q("SELECT * FROM charge_types WHERE id=?", (cid,), one=True)
                for t in q("SELECT * FROM tenants WHERE active=1"):
                    if f"present_{t['id']}" not in f:
                        continue
                    en = 1 if f.get(f"en_{t['id']}") else 0
                    if ct["kind"] == "meter":
                        db().execute("INSERT INTO tenant_meters(tenant_id,charge_type_id,enabled,initial_reading,meter_no) VALUES(?,?,?,?,?) "
                                     "ON CONFLICT(tenant_id,charge_type_id) DO UPDATE SET enabled=excluded.enabled",
                                     (t["id"], cid, en, 0, ""))
                    else:
                        raw = f.get(f"amt_{t['id']}", "").strip()
                        db().execute("INSERT INTO tenant_fixed(tenant_id,charge_type_id,enabled,amount) VALUES(?,?,?,?) "
                                     "ON CONFLICT(tenant_id,charge_type_id) DO UPDATE SET enabled=excluded.enabled, amount=excluded.amount",
                                     (t["id"], cid, en, parse_float(raw) if raw else None))
                db().commit()
                flash("Назначение арендаторам сохранено. Применяется к следующим счетам", "ok")
                return redirect(url_for("charge_types", edit=cid))
            if act == "delete":
                cid = int(f.get("id"))
                ct = q("SELECT * FROM charge_types WHERE id=?", (cid,), one=True)
                if ct["code"]:
                    raise ValueError("Базовый вид нельзя удалить — его можно отключить")
                if q("SELECT 1 FROM charges WHERE charge_type_id=?", (cid,), one=True):
                    raise ValueError("По этому виду уже есть начисления — его можно только отключить (снять «Активен»)")
                db().execute("DELETE FROM charge_types WHERE id=?", (cid,)); db().commit()
                flash("Вид начисления удалён", "ok")
        except ValueError as e:
            flash(f"Ошибка: {e}", "error")
        return redirect(url_for("charge_types"))
    edit = q("SELECT * FROM charge_types WHERE id=?", (request.args.get("edit"),), one=True) if request.args.get("edit") else None
    tariffs = q("SELECT * FROM tariffs WHERE charge_type_id=? ORDER BY valid_from DESC", (edit["id"],)) if edit else []
    assign = []
    if edit and edit["kind"] in ("fixed", "area", "meter"):
        for t in q("SELECT * FROM tenants WHERE active=1 ORDER BY category, unit, name"):
            if edit["kind"] == "meter":
                tm = tenant_meter(t["id"], edit["id"])
                assign.append(dict(t=t, en=tm["enabled"] if tm else edit["apply_all"], amt=None))
            else:
                r = q("SELECT * FROM tenant_fixed WHERE tenant_id=? AND charge_type_id=?", (t["id"], edit["id"]), one=True)
                assign.append(dict(t=t, en=r["enabled"] if r else edit["apply_all"], amt=r["amount"] if r else None))
    rows = q("SELECT ct.*, (SELECT COUNT(*) FROM charges c WHERE c.charge_type_id=ct.id) n FROM charge_types ct WHERE ct.kind<>'opening' ORDER BY active DESC, sort, id")
    cur_t = {r["id"]: tariff_for(r["id"], date.today()) for r in rows if r["kind"] in ("meter", "rent")}
    return render_template("charge_types.html", rows=rows, edit=edit, tariffs=tariffs, assign=assign, cur_t=cur_t,
                           new=request.args.get("new") == "1")

# ---------------------------------------------------------------- настройки и Telegram
@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def settings_page():
    if request.method == "POST":
        f, act = request.form, request.form.get("action")
        if act and act.startswith("tgu_"):
            tgu_action(act, f)
            return redirect(url_for("settings_page") + "#tgu")
        if act == "bot_toggle":
            set_settings(bot_enabled="1" if f.get("bot_enabled") else "0")
            flash("Бот для клиентов " + ("включён" if f.get("bot_enabled") else "выключен"), "ok")
            return redirect(url_for("settings_page") + "#bot")
        if act == "token":
            token = f.get("token", "").strip()
            if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", token):
                flash("Токен выглядит неверно. Скопируйте его целиком из сообщения @BotFather (вида 1234567890:AAH...)", "error")
            else:
                try:
                    me = tg.get_me(token)
                    set_settings(tg_token=token, tg_bot_username=me.get("username", ""), tg_bot_name=me.get("first_name", ""),
                                 tg_poll_error="", tg_offset="0")
                    flash(f"Бот @{me.get('username')} подключён — для клиентов Lumberture (вход на сайт, статусы заказов)", "ok")
                except tg.TgError as e:
                    if e.code == 0:
                        set_settings(tg_token=token, tg_offset="0")
                        flash("Токен сохранён, но нет связи с Telegram — проверим автоматически, когда появится интернет", "warn")
                    else:
                        flash("Telegram не принял токен: " + e.human(), "error")
        elif act == "token_delete":
            set_settings(tg_token="", tg_bot_username="", tg_bot_name="", tg_poll_error="")
            flash("Бот отключён. Уведомления не отправляются", "ok")
        elif act == "options":
            kw = {k: ("1" if f.get(k) else "0") for k in ("notify_payment", "notify_invoice", "notify_meter", "auto_billing")}
            kw.update(billing_day=min(max(int(parse_float(f.get("billing_day"), 1)), 1), 28),
                      notify_hour=min(max(int(parse_float(f.get("notify_hour"), 9)), 0), 23),
                      company_name=f.get("company_name", "").strip(), client_company_name=f.get("client_company_name", "").strip(), contact_phone=f.get("contact_phone", "").strip(),
                      default_lang=f.get("default_lang") if f.get("default_lang") in LANGS else "ru",
                      tenant_categories="\n".join(x.strip() for x in f.get("tenant_categories", "").splitlines() if x.strip()))
            set_settings(**kw)
            flash("Настройки сохранены", "ok")
        elif act == "templates":
            kw = {}
            for k in DEFAULT_TEMPLATES:
                v = f.get(k, "").replace("\r\n", "\n").strip()
                kw[k] = v if v else DEFAULT_TEMPLATES[k]
            set_settings(**kw)
            flash("Шаблоны сообщений сохранены", "ok")
        elif act == "template_reset":
            k = f.get("key")
            if k in DEFAULT_TEMPLATES:
                set_settings(**{k: DEFAULT_TEMPLATES[k]})
                flash("Шаблон восстановлен по умолчанию", "ok")
            elif not k:
                set_settings(**DEFAULT_TEMPLATES)
                flash("Все шаблоны восстановлены по умолчанию", "ok")
        elif act == "test" and rent_bot_on():
            t = q("SELECT * FROM tenants WHERE id=?", (f.get("tenant_id"),), one=True)
            if t:
                n = safe_notify(enqueue, t["id"], "balance", balance_message(t), BALANCE_KB)
                flash(f"Тестовое сообщение через бота поставлено в очередь: {n} чат(ов)" if n else "Арендатор не подключён к боту", "ok" if n else "warn")
        elif act == "retry":
            db().execute("UPDATE notifications SET status='pending', next_try=0, attempts=0 WHERE status='failed' AND chat_id IS NOT NULL")
            db().commit(); outbox_event.set()
            flash("Неотправленные сообщения поставлены в очередь повторно", "ok")
        return redirect(url_for("settings_page") + (f"#{f.get('anchor')}" if f.get("anchor") else ""))
    st = {k: get_setting(k) if k in DEFAULT_SETTINGS else get_setting(k, "") for k in list(DEFAULT_SETTINGS) + ["tg_poll_ok_at", "tg_poll_error", "tg_poll_error_at", "auto_billed_period",
                                                              "auto_billed_at", "auto_billed_count", "tgu_auth_error", "tgu_auth_error_at",
                                                              "tgu_flood_at", "tgu_flood_seconds", "tgu_peer_flood_at", "tgu_last_at",
                                                              "tgu_wait_reason", "tgu_code_sent", "tgu_need_password"]}
    tpls = {k: get_setting(k) for k in DEFAULT_TEMPLATES}
    notif = q("SELECT n.*, COALESCE(t.name, cl.name || ' (клиент)') name FROM notifications n LEFT JOIN tenants t ON t.id=n.tenant_id "
              "LEFT JOIN clients cl ON cl.id=n.client_id ORDER BY n.id DESC LIMIT 40")
    linked = q("SELECT DISTINCT t.id, t.name, t.unit FROM tenants t JOIN tenant_chats c ON c.tenant_id=t.id WHERE c.active=1 ORDER BY t.name")
    counts = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM notifications GROUP BY status")}
    me = json.loads(get_setting("tgu_me") or "{}") if get_setting("tgu_me") else {}
    return render_template("settings.html", st=st, tpls=tpls, kinds=TPL_KINDS, samples={l: fill(msg_tpl("reminder", l), {"name": "Rasul", "balance_text": bal_phrase(204.22, l), "company": get_setting("company_name")}) for l in LANGS}, notif=notif, linked=linked, counts=counts,
                           me=me, tgu=tgu_ready(), tq=tgu_queue_info(), simulate=os.environ.get("RENT_TG_SIMULATE") == "1",
                           bot_enabled=get_setting("bot_enabled") == "1",
                           masked=tg.mask(st["tg_token"]), poll_alive=bg_alive("telegram"), outbox_alive=bg_alive("outbox"),
                           n_tenants=scalar("SELECT COUNT(*) FROM tenants WHERE active=1"),
                           demo_n=scalar("SELECT COUNT(*) FROM tenants WHERE is_demo=1") + scalar("SELECT COUNT(*) FROM clients WHERE is_demo=1"))

# ---------------------------------------------------------------- Telegram-аккаунт: вход и лимиты
def tgu_queue_info(dt=None):
    dt = dt or datetime.now()
    ok, why, wait = tgu_state(dt)
    cnt = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM tg_out GROUP BY status")}
    pending = cnt.get("pending", 0)
    nxt = None
    if pending and tgu_ready():
        nxt = dt + timedelta(seconds=wait) if not ok else dt
    return dict(ok=ok, why=why, pending=pending, tried_today=tgu_tried_today(dt), cap=setting_int("tgu_daily_cap"),
                sent_today=scalar("SELECT COUNT(*) FROM tg_out WHERE status='sent' AND sent_at>=?", (dt.date().isoformat(),)),
                counts=cnt, next=nxt, alive=bg_alive("tguser"),
                flood_until=datetime.fromtimestamp(parse_float(get_setting("tgu_flood_until"), 0)) if parse_float(get_setting("tgu_flood_until"), 0) > time.time() else None)

def tgu_action(act, f):
    api_id, api_hash = tgu_cfg()
    try:
        if act == "tgu_save":
            new_id, new_hash = f.get("api_id", "").strip(), f.get("api_hash", "").strip()
            phone = smsmod.normalize_phone(f.get("phone", "")) or re.sub(r"[^\d]", "", f.get("phone", ""))
            if new_id and not new_id.isdigit():
                raise tguser.TgUserError("api_id — это число (например 1234567)")
            if new_hash and not re.fullmatch(r"[0-9a-f]{32}", new_hash):
                raise tguser.TgUserError("api_hash — 32 символа 0-9 и a-f, скопируйте целиком с my.telegram.org")
            kw = dict(tgu_enabled="1" if f.get("tgu_enabled") else "0", tgu_phone=phone,
                      tgu_delay_min=max(int(parse_float(f.get("tgu_delay_min"), 20)), 5),
                      tgu_daily_cap=max(int(parse_float(f.get("tgu_daily_cap"), 50)), 0),
                      tgu_hour_from=min(max(int(parse_float(f.get("tgu_hour_from"), 8)), 0), 23),
                      tgu_hour_to=min(max(int(parse_float(f.get("tgu_hour_to"), 21)), 1), 24),
                      tgu_expire_days=max(int(parse_float(f.get("tgu_expire_days"), 3)), 0))
            kw["tgu_delay_max"] = max(int(parse_float(f.get("tgu_delay_max"), 60)), kw["tgu_delay_min"])
            if new_id:
                kw["tgu_api_id"] = new_id
            if new_hash:
                kw["tgu_api_hash"] = new_hash
            if (new_id and new_id != api_id) or (new_hash and new_hash != api_hash):
                kw.update(tgu_authorized="0", tgu_code_sent="", tgu_need_password="")
            set_settings(**kw)
            flash("Настройки Telegram-аккаунта сохранены", "ok")
        elif act == "tgu_code":
            phone = get_setting("tgu_phone")
            if not (api_id and api_hash and phone):
                raise tguser.TgUserError("Сначала заполните api_id, api_hash и номер телефона аккаунта-отправителя и нажмите «Сохранить»")
            tgu_sender().send_code(api_id, api_hash, "+" + phone)
            set_settings(tgu_code_sent=now(), tgu_need_password="")
            flash("Код отправлен в Telegram на номер +" + phone + " (сообщение от «Telegram» в приложении). Введите его ниже", "ok")
        elif act == "tgu_signin":
            res, me = tgu_sender().sign_in(api_id, api_hash, f.get("code", ""))
            if res == "password":
                set_settings(tgu_need_password="1")
                flash("На аккаунте включена двухэтапная проверка — введите облачный пароль Telegram", "warn")
            else:
                _tgu_logged_in(me)
        elif act == "tgu_password":
            res, me = tgu_sender().sign_in_password(api_id, api_hash, f.get("password", ""))
            _tgu_logged_in(me)
        elif act == "tgu_check":
            st = tgu_sender().status(api_id, api_hash)
            if st["authorized"]:
                _tgu_logged_in(st["me"], quiet=True)
                flash("Аккаунт подключён: " + (st["me"] or {}).get("name", ""), "ok")
            else:
                set_settings(tgu_authorized="0")
                flash("Аккаунт не авторизован — получите код и войдите", "warn")
        elif act == "tgu_logout":
            tgu_sender().logout(api_id, api_hash)
            set_settings(tgu_authorized="0", tgu_me="", tgu_code_sent="", tgu_need_password="")
            flash("Вы вышли из Telegram-аккаунта. Файл сессии удалён", "ok")
        elif act == "tgu_test":
            phone = smsmod.normalize_phone(f.get("phone"))
            if not phone:
                raise tguser.TgUserError("Укажите номер получателя в формате +998 XX XXX XX XX")
            db().execute("INSERT INTO tg_out(kind,phone,name,text,status,created_at) VALUES('test',?,?,?,'pending',?)",
                         (phone, "Проверка", f.get("text", "").strip() or "Проверка уведомлений", now()))
            db().commit(); tg_event.set()
            flash("Тестовое сообщение поставлено в очередь — результат в «Журнал сообщений» через несколько секунд", "ok")
        elif act == "tgu_retry":
            n = db().execute("UPDATE tg_out SET status='pending', next_try=0, created_at=? WHERE status IN ('failed','expired')", (now(),)).rowcount
            db().commit(); tg_event.set()
            flash(f"Повторно в очереди: {n}", "ok")
        elif act == "tgu_resume":
            set_settings(tgu_flood_until="0", tgu_next_at="0", tgu_peer_flood_at="")
            tg_event.set()
            flash("Пауза снята. Если Telegram снова вернёт ограничение — отправка опять остановится", "warn")
        elif act == "tgu_clear":
            n = db().execute("UPDATE tg_out SET status='skipped', error='Очередь очищена вручную' WHERE status='pending'").rowcount
            db().commit()
            flash(f"Очередь очищена ({n})", "ok")
    except tguser.TgUserError as e:
        flash(str(e), "error")

def _tgu_logged_in(me, quiet=False):
    set_settings(tgu_authorized="1", tgu_me=json.dumps(me or {}, ensure_ascii=False), tgu_code_sent="", tgu_need_password="",
                 tgu_auth_error="")
    tg_event.set()
    if not quiet:
        flash("Готово: вход выполнен" + (f" как {me.get('name')}" if me else "") + ". Сессия сохранена в data/, уведомления будут уходить с этого аккаунта", "ok")

def messages_for(tenant_id=None, client_id=None, limit=15, channel="", status="", search=""):
    """Единый журнал: Telegram-аккаунт + SMS + бот."""
    parts, args = [], []
    for sql, ch in [("SELECT 'tg' ch, id, created_at, COALESCE(sent_at, tried_at) done_at, tenant_id, client_id, kind, phone, text, status, error FROM tg_out", "tg"),
                    ("SELECT 'sms' ch, id, created_at, sent_at, tenant_id, client_id, kind, phone, text, status, (provider || ': ' || COALESCE(response,'')) FROM sms_log", "sms"),
                    ("SELECT 'bot' ch, id, created_at, sent_at, tenant_id, client_id, kind, CAST(chat_id AS TEXT), text, status, error FROM notifications", "bot")]:
        if channel and channel != ch:
            continue
        parts.append(sql)
    sql = "SELECT m.*, COALESCE(t.name, c.name, '') rname FROM (" + " UNION ALL ".join(parts) + ") m LEFT JOIN tenants t ON t.id=m.tenant_id LEFT JOIN clients c ON c.id=m.client_id WHERE 1=1"
    if tenant_id:
        sql += " AND m.tenant_id=?"; args.append(tenant_id)
    if client_id:
        sql += " AND m.client_id=?"; args.append(client_id)
    if status:
        sql += " AND m.status=?"; args.append(status)
    if search:
        sql += " AND (m.phone LIKE ? OR rname LIKE ? OR m.text LIKE ?)"; args += [f"%{search}%"] * 3
    return q(sql + " ORDER BY m.created_at DESC, m.id DESC LIMIT ?", args + [limit])

@app.route("/messages", methods=["GET", "POST"])
@login_required
def messages():
    if request.method == "POST":
        if not is_admin():
            abort(403)
        tgu_action(request.form.get("action", ""), request.form)
        return redirect(url_for("messages"))
    a = request.args
    rows = messages_for(limit=300, channel=a.get("channel", ""), status=a.get("status", ""), search=a.get("q", "").strip())
    return render_template("messages.html", rows=rows, tq=tgu_queue_info(), tgu=tgu_ready(), a=a)

# ---------------------------------------------------------------- курс доллара: страница, обновление, ручной ввод
@app.route("/rates", methods=["GET", "POST"])
@login_required
def rates_page():
    if request.method == "POST":
        f, act = request.form, request.form.get("action")
        back = request.form.get("back") or url_for("rates_page")
        if act == "refresh":
            row, errs = fx_fetch(current_user()["id"])
            if row:
                flash(f"Курс обновлён: 1 $ = {money(row['rate'])} сум (источник: {FX_SOURCES.get(row['source'], row['source'])})"
                      + (f". Замечания: {'; '.join(errs)}" if errs else ""), "ok" if row["source"] == "bot" else "warn")
            else:
                flash("Курс не получен: " + "; ".join(errs) + ". Введите курс вручную", "error")
            return redirect(back if back.startswith("/") else url_for("rates_page"))
        if not is_admin():
            abort(403)
        if act == "manual":
            rate = parse_float(f.get("rate"))
            if not (rates.RATE_MIN <= rate <= rates.RATE_MAX):
                flash(f"Курс должен быть от {money(rates.RATE_MIN)} до {money(rates.RATE_MAX)} сум за 1 $", "error")
            else:
                fx_store(rate, "manual", raw=f.get("note", "").strip(), rate_date=(parse_date(f.get("rate_date")) or date.today()).isoformat(),
                         uid=current_user()["id"])
                flash(f"Курс {money(rate)} сум/$ сохранён (вручную). Он действует, пока не будет получен новый", "ok")
        elif act == "settings":
            set_settings(fx_bot=(f.get("fx_bot", "").strip().lstrip("@") or "em_mat_info_bot"), fx_cmd=f.get("fx_cmd", "").strip() or "/rate_usd",
                         fx_pick=f.get("fx_pick") if f.get("fx_pick") in rates.PICKS else "sell",
                         fx_auto="1" if f.get("fx_auto") else "0", fx_cbu="1" if f.get("fx_cbu") else "0",
                         fx_hour=min(max(int(parse_float(f.get("fx_hour"), 9)), 0), 23),
                         fx_every=min(max(int(parse_float(f.get("fx_every"), 30)), 10), 240),
                         fx_until=min(max(int(parse_float(f.get("fx_until"), 19)), 1), 24))
            flash("Настройки курса сохранены", "ok")
        elif act == "delete":
            db().execute("DELETE FROM fx_rates WHERE id=?", (f.get("id"),)); db().commit(); g.pop("fx", None)
            flash("Запись курса удалена", "ok")
        return redirect(url_for("rates_page"))
    st = {k: get_setting(k) for k in ("fx_bot", "fx_cmd", "fx_pick", "fx_auto", "fx_hour", "fx_cbu", "fx_every", "fx_until")}
    st.update({k: get_setting(k, "") for k in ("fx_bot_raw", "fx_bot_at", "fx_last_error", "fx_last_try_at", "fx_auto_date")})
    return render_template("rates.html", st=st, rows=q("SELECT * FROM fx_rates ORDER BY id DESC LIMIT 100"), tgu=tgu_ready(), PICKS=rates.PICKS)

@app.route("/admin/period-lock", methods=["GET", "POST"])
@admin_required
def period_lock():
    if request.method == "POST":
        f, act = request.form, request.form.get("action")
        if act == "days":
            set_settings(edit_lock_days=min(max(int(parse_float(f.get("days"), 4)), 0), 365))
            flash("Сақланди", "ok")
        elif act == "open":
            d = parse_date(f.get("from"))
            hrs = min(max(int(parse_float(f.get("hours"), 24)), 1), 24 * 7)
            if not d:
                flash("Санани танланг", "error")
            else:
                until = (datetime.now() + timedelta(hours=hrs)).strftime("%Y-%m-%d %H:%M:%S")
                set_settings(edit_open_from=d.isoformat(), edit_open_until=until)
                log.info("period opened from %s until %s by %s", d, until, current_user()["username"])
                flash(f"{d.strftime('%d.%m.%Y')} дан бошлаб кунлар {until[:16]} гача очиқ", "ok")
        elif act == "close":
            set_settings(edit_open_from="", edit_open_until="")
            flash("Кунлар яна автомат ёпилди", "ok")
        return redirect(url_for("period_lock"))
    return render_template("period_lock.html", days=setting_int("edit_lock_days"), edge=closed_edge(),
                           of=get_setting("edit_open_from", ""), ou=get_setting("edit_open_until", ""), now_s=now())

# ---------------------------------------------------------------- пользователи, резервная копия, демо
# ---------------------------------------------------------------- оформление: тема, акцент, шрифт, плотность, логотип
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")

def ui_settings():
    """Текущее оформление (для каждой страницы, в т.ч. страницы входа)."""
    if "ui" not in g:
        st = {k: get_setting(k) for k in ("ui_theme", "ui_accent", "ui_accent_custom", "ui_font", "ui_density", "ui_title", "ui_logo", "ui_ver",
                                          "ui_radius", "ui_header", "ui_ff", "ui_zebra", "ui_skin")}
        acc = st["ui_accent"]
        color = UI_ACCENTS[acc][1] if acc in UI_ACCENTS else (st["ui_accent_custom"] if HEX_RE.fullmatch(st["ui_accent_custom"] or "") else UI_ACCENTS["green"][1])
        logo = st["ui_logo"] if st["ui_logo"] in UI_LOGO_TYPES and os.path.exists(ui_logo_path(st["ui_logo"])) else ""
        th = st["ui_theme"] if st["ui_theme"] in UI_THEMES else "light"
        if th == "light" and st["ui_skin"] == "v16":       # олдинги «Стиль» созламаси (fix414) — энди «Тема»да
            th = "v16"
        g.ui = dict(theme="light" if th == "v16" else th, theme_sel=th, accent=acc if acc in UI_ACCENTS or acc == "custom" else "green",
                    color=color, custom=st["ui_accent_custom"] if HEX_RE.fullmatch(st["ui_accent_custom"] or "") else color,
                    font=st["ui_font"] if st["ui_font"] in UI_FONTS else "normal",
                    density=st["ui_density"] if st["ui_density"] in UI_DENSITY else "normal",
                    title=(st["ui_title"] or "").strip(), logo=logo, ver=st["ui_ver"] or "1",
                    radius=st["ui_radius"] if st["ui_radius"] in UI_RADIUS else "normal",
                    header=st["ui_header"] if st["ui_header"] in UI_HEADER else "light",
                    ff=st["ui_ff"] if st["ui_ff"] in UI_FF else "inter", zebra="1" if st["ui_zebra"] == "1" else "0",
                    skin="v16" if th == "v16" else "classic")
    return g.ui

def ui_logo_path(ext):
    return os.path.join(DATA_DIR, f"ui_logo.{ext}")

def logo_type(raw):
    """Только PNG и JPEG (по содержимому файла, не по имени); SVG и прочее — нет."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    return None

@app.route("/ui/theme.css")
def ui_theme_css():
    ui = ui_settings()
    css = f":root{{--g:{ui['color']}}}\n"
    resp = Response(css, mimetype="text/css")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

@app.route("/ui/logo")
def ui_logo():
    ui = ui_settings()
    if not ui["logo"]:
        abort(404)
    resp = send_file(ui_logo_path(ui["logo"]), mimetype=UI_LOGO_TYPES[ui["logo"]], max_age=86400)
    resp.headers["Content-Disposition"] = "inline"
    return resp

@app.route("/admin/appearance", methods=["GET", "POST"])
@admin_required
def appearance():
    if request.method == "POST":
        f, act = request.form, request.form.get("action")
        if act == "reset":
            for ext in UI_LOGO_TYPES:
                if os.path.exists(ui_logo_path(ext)):
                    os.remove(ui_logo_path(ext))
            set_settings(**{k: DEFAULT_SETTINGS[k] for k in ("ui_theme", "ui_accent", "ui_accent_custom", "ui_font", "ui_density", "ui_title", "ui_logo",
                                                             "ui_radius", "ui_header", "ui_ff", "ui_zebra", "ui_skin")},
                         ui_ver=str(int(time.time())))
            flash("Оформление сброшено по умолчанию", "ok")
            return redirect(url_for("appearance"))
        kw = dict(ui_theme=f.get("ui_theme") if f.get("ui_theme") in UI_THEMES else "light",
                  ui_font=f.get("ui_font") if f.get("ui_font") in UI_FONTS else "normal",
                  ui_density=f.get("ui_density") if f.get("ui_density") in UI_DENSITY else "normal",
                  ui_title=f.get("ui_title", "").strip()[:60],
                  ui_radius=f.get("ui_radius") if f.get("ui_radius") in UI_RADIUS else "normal",
                  ui_header=f.get("ui_header") if f.get("ui_header") in UI_HEADER else "light",
                  ui_skin="classic",                       # стиль энди «Тема»да (ui_theme=v16)
                  ui_ff=f.get("ui_ff") if f.get("ui_ff") in UI_FF else "inter", ui_zebra="1" if f.get("ui_zebra") else "0")
        acc, custom = f.get("ui_accent", "green"), (f.get("ui_accent_custom") or "").strip()
        if acc == "custom":
            if not HEX_RE.fullmatch(custom):
                flash("Свой цвет — в формате #RRGGBB", "error")
                return redirect(url_for("appearance"))
            kw.update(ui_accent="custom", ui_accent_custom=custom.lower())
        else:
            kw["ui_accent"] = acc if acc in UI_ACCENTS else "green"
            if HEX_RE.fullmatch(custom):
                kw["ui_accent_custom"] = custom.lower()
        up = request.files.get("logo")
        if up and up.filename:
            raw = up.read(UI_LOGO_MAX + 1)
            kind = logo_type(raw)
            if len(raw) > UI_LOGO_MAX:
                flash("Логотип — не больше 1 МБ", "error")
                return redirect(url_for("appearance"))
            if not kind:
                flash("Логотип — только PNG или JPG (SVG и другие форматы не принимаются)", "error")
                return redirect(url_for("appearance"))
            for ext in UI_LOGO_TYPES:
                if ext != kind and os.path.exists(ui_logo_path(ext)):
                    os.remove(ui_logo_path(ext))
            with open(ui_logo_path(kind), "wb") as fh:
                fh.write(raw)
            kw["ui_logo"] = kind
        elif f.get("logo_delete"):
            for ext in UI_LOGO_TYPES:
                if os.path.exists(ui_logo_path(ext)):
                    os.remove(ui_logo_path(ext))
            kw["ui_logo"] = ""
        set_settings(**kw, ui_ver=str(int(time.time())))
        flash("Оформление сохранено — для всех пользователей", "ok")
        return redirect(url_for("appearance"))
    return render_template("appearance.html")

# ---------------------------------------------------------------- «Безопасность»: доступ по IP, журнал входов, пароли, копии
def backup_status():
    try:
        with open(os.path.join(DATA_DIR, "backup_status.json"), encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    bdir = os.path.join(DATA_DIR, "backups")
    files = sorted(f for f in os.listdir(bdir) if f.startswith("rent_") and f.endswith(".db")) if os.path.isdir(bdir) else []
    st["local_count"], st["local_last"] = len(files), (files[-1] if files else "")
    return st

@app.route("/admin/security", methods=["GET", "POST"])
@admin_required
def security_page():
    ip = client_ip()
    if request.method == "POST":
        f, act = request.form, request.form.get("action")
        if act == "ip":
            allow, adm = f.get("ip_allow", "").strip(), f.get("ip_admin_allow", "").strip()
            errs = sec.parse_list(allow)[1] + sec.parse_list(adm)[1]
            if errs:
                flash("Не распознаны адреса: " + ", ".join(errs[:5]) + " (пример: 192.168.1.22 или 192.168.1.0/24)", "error")
            elif not sec.ip_allowed(ip, allow) or (adm and not sec.ip_allowed(ip, adm)):
                flash(f"Не сохранено: ваш компьютер ({ip}) потерял бы доступ. Добавьте его адрес в список.", "error")
            else:
                set_settings(ip_allow=allow, ip_admin_allow=adm)
                flash("Доступ по IP сохранён. Этот ПК (127.0.0.1, ::1, свои адреса сервера) разрешён всегда", "ok")
        elif act == "ip_default":
            set_settings(ip_allow=sec.DEFAULT_IP_ALLOW, ip_admin_allow="")
            flash("Восстановлено: доступ только с этого ПК", "ok")
        elif act == "unlock":
            log_attempt(f.get("username", ""), f.get("ip", ""), 2, "unlock")
            if f.get("ip"):
                log_attempt("*", f.get("ip", ""), 2, "unlock")
            flash(f"Разблокировано: {f.get('username') or '—'} / {f.get('ip')}", "ok")
        elif act == "pwcheck":
            check_weak_passwords(force=True)
            flash("Пароли проверены", "ok")
        return redirect(url_for("security_page"))
    only_fail = request.args.get("fail") == "1"
    attempts = q("SELECT * FROM login_attempts" + (" WHERE ok=0" if only_fail else "") + " ORDER BY id DESC LIMIT 300")
    last_ok = {r["username"]: r for r in q("SELECT username, MAX(at) at, ip FROM login_attempts WHERE ok=1 GROUP BY username")}
    users_ = [dict(u=r, method=sec.hash_method(r["password_hash"]), strong=sec.hash_ok(r["password_hash"]), last=last_ok.get(r["username"]))
              for r in q("SELECT * FROM users ORDER BY active DESC, id")]
    day = time.time() - 86400
    stats = q("SELECT SUM(ok=1) ok, SUM(ok=0 AND reason IN ('bad','nouser','locked')) bad, SUM(ok=0 AND reason LIKE 'ip%') ip "
              "FROM login_attempts WHERE ts>?", (day,), one=True)
    return render_template("security.html", ip=ip, local=sec.is_local(ip), own=sorted(sec.own_ips()), st={k: get_setting(k) for k in ("ip_allow", "ip_admin_allow")},
                           default_allow=sec.DEFAULT_IP_ALLOW, attempts=attempts, only_fail=only_fail, users_=users_, locked=locked_pairs(),
                           weak=weak_users(), stats=stats, sec=sec, bk=backup_status(), REASONS=LOGIN_REASONS)

LOGIN_REASONS = {"ok": "вход", "bad": "неверный пароль", "nouser": "нет такого логина", "locked": "заблокирован (подбор)",
                 "ip": "IP не разрешён", "ip-admin": "IP не разрешён для админа", "unlock": "разблокировано админом"}

@app.route("/admin/backup")
@admin_required
def backup():
    fd, tmp = tempfile.mkstemp(suffix=".db"); os.close(fd)
    src, dst = connect(), sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    with open(tmp, "rb") as fh:
        data = fh.read()
    os.remove(tmp)
    return send_file(io.BytesIO(data), as_attachment=True, download_name=f"rent_{date.today().isoformat()}.db",
                     mimetype="application/octet-stream")

@app.route("/admin/demo/create", methods=["POST"])
@admin_required
def demo_create():
    import demo
    n = demo.create(db())
    flash(f"Созданы демо-данные: {n} арендаторов и клиентов-должников с пометкой «демо». Удалить — кнопкой в «Настройки»", "ok")
    return redirect(url_for("dashboard"))

@app.route("/admin/demo/delete", methods=["POST"])
@admin_required
def demo_delete():
    ids = [r["id"] for r in q("SELECT id FROM tenants WHERE is_demo=1")]
    for tid in ids:
        db().execute("DELETE FROM notifications WHERE tenant_id=?", (tid,))
        db().execute("DELETE FROM tg_out WHERE tenant_id=?", (tid,))
        db().execute("DELETE FROM sms_log WHERE tenant_id=?", (tid,))
        db().execute("DELETE FROM meter_readings WHERE tenant_id=?", (tid,))
        db().execute("DELETE FROM tenants WHERE id=?", (tid,))
    cids = [r["id"] for r in q("SELECT id FROM clients WHERE is_demo=1")]
    for cid in cids:
        db().execute("DELETE FROM sms_log WHERE client_id=?", (cid,))
        db().execute("DELETE FROM tg_out WHERE client_id=?", (cid,))
        db().execute("DELETE FROM notifications WHERE client_id=?", (cid,))
        db().execute("DELETE FROM clients WHERE id=?", (cid,))
    db().execute("DELETE FROM tariffs WHERE is_demo=1")
    db().execute("DELETE FROM fx_rates WHERE is_demo=1")
    for ct in q("SELECT id FROM charge_types WHERE is_demo=1"):
        if not q("SELECT 1 FROM charges WHERE charge_type_id=?", (ct["id"],), one=True):
            db().execute("DELETE FROM charge_types WHERE id=?", (ct["id"],))
    db().commit()
    flash(f"Демо-данные удалены ({len(ids)} арендаторов, {len(cids)} клиентов)", "ok")
    return redirect(url_for("dashboard"))

# ================================================================ КЛИЕНТЫ-ДОЛЖНИКИ (услуги распила/кромки, материалы)
DEBT_KINDS = {"services": "Услуги (распил, кромка…)", "materials": "Материалы (ЛДСП, МДФ…)"}
OPENING_KIND = "opening"          # «Долг на начало (перенос)» — без заказа; < 0 — аванс
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
SMS_STATUS = {"pending": "в очереди", "sent": "отправлено", "test": "тест (не отправлено)", "failed": "ошибка", "skipped": "пропущено"}
SMS_KINDS = {"reminder": "Напоминание", "payment": "Оплата", "manual": "Вручную", "test": "Проверка"}

@app.context_processor
def ctx_clients():
    return dict(DEBT_KINDS=DEBT_KINDS, WEEKDAYS=WEEKDAYS, SMS_STATUS=SMS_STATUS, SMS_KINDS=SMS_KINDS,
                SMS_PROVIDERS=smsmod.PROVIDERS, CHANNELS=CHANNELS, MSG_STATUS=MSG_STATUS, MSG_KINDS=MSG_KINDS, TG_STATUS=TG_STATUS,
                SMS_ON=sms_on() if has_app_context() else False)

def get_client(cid):
    c = q("SELECT * FROM clients WHERE id=?", (cid,), one=True)
    if not c:
        abort(404)
    return c

# Клиенты: долги и оплаты — в сумах или в $ (каждая запись в своей валюте). Итог по клиенту — в СУМАХ:
# долг в сумах + долг в $ × текущий курс. Оплата гасит сначала долг в своей валюте, остаток — по курсу дня оплаты.
_CP_U = "COALESCE(usd_part, CASE WHEN currency='USD' THEN amount ELSE 0 END)"
_CP_Z = "COALESCE(uzs_part, CASE WHEN currency='USD' THEN 0 ELSE amount END)"

def to_uzs(u, z, rate=None):
    rate = rate or fx_rate() or parse_float(get_setting("fx_fallback_rate"), 12000) or 12000
    return round((z or 0) + (u or 0) * rate, 2)

def client_pockets(cid):
    u = scalar("SELECT SUM(amount) FROM client_debts WHERE client_id=? AND currency='USD'", (cid,)) \
        - scalar(f"SELECT SUM({_CP_U}) FROM client_payments WHERE client_id=?", (cid,))
    z = scalar("SELECT SUM(amount) FROM client_debts WHERE client_id=? AND currency<>'USD'", (cid,)) \
        - scalar(f"SELECT SUM({_CP_Z}) FROM client_payments WHERE client_id=?", (cid,))
    return round(u, 4), round(z, 2)

def client_balance(cid):
    """Долг клиента (>0) или аванс (<0) в сумах (долг в $ — по текущему курсу)."""
    return to_uzs(*client_pockets(cid))

def all_client_pockets():
    res = {r["id"]: [0.0, 0.0] for r in q("SELECT id FROM clients")}
    for r in q("SELECT client_id, currency, SUM(amount) s FROM client_debts GROUP BY client_id, currency"):
        res.setdefault(r["client_id"], [0.0, 0.0])[0 if r["currency"] == "USD" else 1] += r["s"]
    for r in q(f"SELECT client_id, SUM({_CP_U}) u, SUM({_CP_Z}) z FROM client_payments GROUP BY client_id"):
        x = res.setdefault(r["client_id"], [0.0, 0.0])
        x[0] -= r["u"] or 0; x[1] -= r["z"] or 0
    return {k: (round(v[0], 4), round(v[1], 2)) for k, v in res.items()}

def client_balances():
    rate = fx_rate()
    return {k: to_uzs(u, z, rate) for k, (u, z) in all_client_pockets().items()}

def client_pockets_text(u, z):
    """«в т.ч. $120 + 500 000 сум; $ по курсу 11 840» — если есть долларовая часть."""
    if abs(u) < EPS:
        return ""
    parts = [("долг " if u > 0 else "аванс ") + usd(abs(u))] + ([("долг " if z > 0 else "аванс ") + f"{money(abs(z))} сум"] if abs(z) >= 0.5 else [])
    return "в т.ч.: " + ", ".join(parts) + (f"; $ пересчитаны по курсу {money(fx_rate())}" if fx_rate() else "")

def client_pay_text(p):
    s_ = cur(p["amount"], p["currency"])
    if p["currency"] == "USD" and (p["uzs_part"] or 0) >= 0.5:
        s_ += f" (из них {money(p['uzs_part'])} сум по курсу {money(p['fx_rate'])})"
    elif p["currency"] != "USD" and (p["usd_part"] or 0) >= EPS:
        s_ += f" (из них {usd(p['usd_part'])} по курсу {money(p['fx_rate'])})"
    return s_

def client_overdue(cid, today=None):
    """Оплаты (и авансы на начало) гасят долги по порядку (FIFO), всё в сумах по текущему курсу.
    -> (дней просрочки самого старого непогашенного долга, этот долг)."""
    today = today or date.today()
    rate = fx_rate() or parse_float(get_setting("fx_fallback_rate"), 12000) or 12000
    eq = lambda amt, c_: amt * rate if c_ == "USD" else amt
    paid = sum(to_uzs(r["u"] or 0, r["z"] or 0, rate) for r in
               q(f"SELECT {_CP_U} u, {_CP_Z} z FROM client_payments WHERE client_id=?", (cid,)))
    debts = q("SELECT * FROM client_debts WHERE client_id=? ORDER BY doc_date, id", (cid,))
    paid += sum(-eq(d["amount"], d["currency"]) for d in debts if d["amount"] < 0)
    for d in debts:
        if d["amount"] <= 0:
            continue
        a = eq(d["amount"], d["currency"])
        if paid >= a - 0.5:
            paid -= a
            continue
        due = parse_date(d["due_date"]) or parse_date(d["doc_date"])
        return max((today - due).days, 0), d
    return 0, None

# ---------------------------------------------------------------- начальный долг клиента (без заказа)
def client_openings(cid):
    res = {"USD": None, "UZS": None}
    for r in q("SELECT * FROM client_debts WHERE client_id=? AND kind=? ORDER BY id", (cid, OPENING_KIND)):
        k = "USD" if r["currency"] == "USD" else "UZS"
        res[k] = res[k] or r
    any_ = res["USD"] or res["UZS"]
    res["note"] = opening_note(any_["description"]) if any_ else ""
    res["date"] = any_["doc_date"] if any_ else ""
    return res

def all_client_openings():
    res = {}
    for r in q("SELECT * FROM client_debts WHERE kind=? ORDER BY id", (OPENING_KIND,)):
        x = res.setdefault(r["client_id"], {"USD": None, "UZS": None, "note": "", "date": ""})
        k = "USD" if r["currency"] == "USD" else "UZS"
        x[k] = x[k] or r
        x["note"] = x["note"] or opening_note(r["description"]); x["date"] = x["date"] or r["doc_date"]
    return res

def client_opening_default_date(cid=None):
    """День перед первым заказом/оплатой клиента; если их нет — сегодня."""
    if cid:
        a = q("SELECT MIN(doc_date) d FROM client_debts WHERE client_id=? AND kind<>?", (cid, OPENING_KIND), one=True)["d"]
        b = q("SELECT MIN(pay_date) d FROM client_payments WHERE client_id=?", (cid,), one=True)["d"]
        first = min(x for x in (a, b) if x) if (a or b) else None
        if first:
            return parse_date(first) - timedelta(days=1)
    return date.today()

def set_client_opening(cid, currency, amount, d=None, note=None, uid=None, is_demo=None):
    """Как set_tenant_opening, но для клиента (запись client_debts kind='opening', срок = дата). Сообщений не шлёт."""
    currency = "USD" if currency == "USD" else "UZS"
    cond = "currency='USD'" if currency == "USD" else "currency<>'USD'"
    rows = q(f"SELECT * FROM client_debts WHERE client_id=? AND kind=? AND {cond} ORDER BY id", (cid, OPENING_KIND))
    if amount is None:
        return None
    amount = round(float(amount), 2)
    if abs(amount) < 0.005:
        if rows:
            db().execute(f"DELETE FROM client_debts WHERE client_id=? AND kind=? AND {cond}", (cid, OPENING_KIND))
            return "deleted"
        return None
    if note is None:
        note = opening_note(rows[0]["description"]) if rows else ""
    d = d or (parse_date(rows[0]["doc_date"]) if rows else client_opening_default_date(cid))
    ds, desc = d.isoformat(), opening_desc(amount, note)
    if rows:
        r = rows[0]
        for extra in rows[1:]:
            db().execute("DELETE FROM client_debts WHERE id=?", (extra["id"],))
        if abs(r["amount"] - amount) < 0.005 and r["doc_date"] == ds and r["description"] == desc and len(rows) == 1:
            return None
        db().execute("UPDATE client_debts SET amount=?, doc_date=?, due_date=?, description=?, currency=? WHERE id=?",
                     (amount, ds, ds, desc, currency, r["id"]))
        return "updated"
    if is_demo is None:
        is_demo = get_client(cid)["is_demo"]
    db().execute("INSERT INTO client_debts(client_id,doc_date,due_date,order_no,description,kind,amount,created_by,created_at,is_demo,currency,fx_rate) "
                 "VALUES(?,?,?,'',?,?,?,?,?,?,?,?)", (cid, ds, ds, desc, OPENING_KIND, amount, uid, now(), is_demo, currency, fx_rate()))
    return "added"

def apply_client_opening(cid, usd_amt, uzs_amt, d, note, uid=None):
    return [x for x in (set_client_opening(cid, "USD", usd_amt, d, note, uid), set_client_opening(cid, "UZS", uzs_amt, d, note, uid)) if x]

def client_paused(c, today=None):
    pu = parse_date(c["sms_paused_until"])
    return bool(pu and pu >= (today or date.today()))

def client_chat_ids(cid):
    return [r["chat_id"] for r in q("SELECT chat_id FROM client_chats WHERE client_id=? AND active=1", (cid,))]

def linked_clients(chat_id):
    return q("SELECT c.* FROM clients c JOIN client_chats x ON x.client_id=c.id WHERE x.chat_id=? AND x.active=1 ORDER BY c.id", (chat_id,))

def link_client_chat(c, chat, frm):
    name = " ".join(x for x in [frm.get("first_name"), frm.get("last_name")] if x)
    db().execute("INSERT INTO client_chats(client_id,chat_id,tg_name,tg_username,linked_at,active) VALUES(?,?,?,?,?,1) "
                 "ON CONFLICT(client_id,chat_id) DO UPDATE SET active=1, tg_name=excluded.tg_name, tg_username=excluded.tg_username",
                 (c["id"], chat["id"], name, frm.get("username") or "", now()))
    db().commit()
    log.info("chat %s linked to client %s", chat["id"], c["id"])

def client_usd_balance(cid):
    """Долг клиента в $ (сумы — по текущему курсу) — для уведомлений."""
    return to_usd(*client_pockets(cid))

def client_tg_welcome(c):
    return notif_text("welcome", c, 0) + "\n\n" + client_balance_message(c)

def client_balance_message(c):
    return notif_text("balance", c, client_usd_balance(c["id"]))

def client_reminder_text(c):
    return notif_text("reminder", c, client_usd_balance(c["id"]))

def client_channels():
    """Активные каналы: (Telegram-аккаунт, SMS-резерв)."""
    return tgu_enabled(), sms_on()

def sms_provider():
    cfg = {k: get_setting(k) for k in ("eskiz_email", "eskiz_password", "eskiz_from", "pm_login", "pm_password", "pm_originator", "pm_url")}
    cfg["eskiz_base"] = os.environ.get("ESKIZ_BASE", "")
    store = (lambda: get_setting("eskiz_token"), lambda tok: set_settings(eskiz_token=tok))
    return smsmod.get_provider(get_setting("sms_provider"), cfg, store)

def process_sms_outbox(limit=20):
    rows = q("SELECT * FROM sms_log WHERE status='pending' AND next_try<=? ORDER BY id LIMIT ?", (time.time(), limit))
    if not rows:
        return 0
    prov, done = sms_provider(), 0
    for r in rows:
        try:
            st, resp = prov.send(r["phone"], r["text"])
            db().execute("UPDATE sms_log SET status=?, response=?, provider=?, sent_at=?, attempts=attempts+1 WHERE id=?",
                         (st, resp[:500], prov.name, now(), r["id"]))
            done += 1
        except smsmod.SmsError as e:
            att = r["attempts"] + 1
            retry = "нет связи" in str(e) and att < 4
            db().execute("UPDATE sms_log SET status=?, response=?, provider=?, attempts=?, next_try=? WHERE id=?",
                         ("pending" if retry else "failed", (str(e) + (" | " + e.response if e.response else ""))[:500],
                          prov.name, att, time.time() + 300 * att, r["id"]))
            log.warning("sms to %s failed: %s", r["phone"], e)
        except Exception as e:  # noqa
            log.exception("sms send")
            db().execute("UPDATE sms_log SET status='failed', response=? WHERE id=?", (str(e)[:300], r["id"]))
        db().commit()
    return done

def sms_days():
    return sorted({int(x) for x in (get_setting("sms_days") or "").split(",") if x.strip().isdigit() and 0 <= int(x) <= 6})

def sms_time():
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", get_setting("sms_time") or "")
    return (int(m.group(1)), int(m.group(2))) if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else (10, 0)

def client_sms_due(dt):
    if get_setting("sms_auto") != "1" or dt.weekday() not in sms_days():
        return False
    h, mi = sms_time()
    return (dt.hour, dt.minute) >= (h, mi) and get_setting("sms_last_run", "") < dt.date().isoformat()

def next_sms_run(dt=None):
    dt = dt or datetime.now()
    days = sms_days()
    if get_setting("sms_auto") != "1" or not days:
        return None
    h, mi = sms_time()
    for i in range(0, 8):
        d = dt.date() + timedelta(days=i)
        cand = datetime(d.year, d.month, d.day, h, mi)
        if d.weekday() in days and (cand > dt or (i == 0 and get_setting("sms_last_run", "") != d.isoformat())):
            return cand
    return None

def _reminded_today(today):
    d = today.isoformat()
    rows = q("SELECT tenant_id, client_id FROM tg_out WHERE kind='reminder' AND status IN ('pending','sent') AND substr(created_at,1,10)=? "
             "UNION SELECT tenant_id, client_id FROM sms_log WHERE kind='reminder' AND status IN ('pending','sent','test') AND substr(created_at,1,10)=? "
             "UNION SELECT tenant_id, client_id FROM notifications WHERE kind='reminder' AND status IN ('pending','sent') AND substr(created_at,1,10)=?", (d, d, d))
    return {("t", r[0]) for r in rows if r[0]} | {("c", r[1]) for r in rows if r[1]}

def _target(kind, r, b, days, text, skip_base, sent):
    ch, why = predict_channel(r, kind == "t")
    skip = skip_base or ("сегодня уже отправлено" if (kind, r["id"]) in sent else "") or ("" if ch else why)
    return dict(who=kind, r=r, c=r, b=b, days=days, phone=smsmod.normalize_phone(r["phone"]), ch=ch, why=why, skip=skip,
                use_tg=ch == "tg", use_sms=ch == "sms", no_tg=r["tg_status"] in NO_TG, tgs=r["tg_status"], text=text)

def reminder_targets(today=None):
    """Клиенты-должники, которые получат напоминание при следующем запуске (и почему кто-то пропущен)."""
    today = today or date.today()
    mind = parse_float(get_setting("client_min_usd", "10"), 10)      # долг клиентов — в $, порог тоже в $
    bal, sent = client_balances(), _reminded_today(today)
    doc_today = {r["client_id"] for r in q("SELECT DISTINCT client_id FROM tg_out WHERE kind='invoice' AND client_id IS NOT NULL AND substr(created_at,1,10)=?",
                                            (today.isoformat(),))}
    out = []
    for c in q("SELECT * FROM clients WHERE active=1 ORDER BY name"):
        b = bal.get(c["id"], 0)                                      # сумда (саралаш учун)
        if b <= 0.5 or client_usd_balance(c["id"]) < mind:           # остона — $ да (олдин сум билан солиштириларди)
            continue
        days, _ = client_overdue(c["id"], today)
        base = ("напоминания выключены у клиента" if not c["sms_enabled"] else
                f"пауза до {ddmm(c['sms_paused_until'])}" if client_paused(c, today) else
                "сегодня уже ушло сообщение о накладной/оплате" if c["id"] in doc_today else "")
        out.append(_target("c", c, b, days, client_reminder_text(c), base, sent))
    return sorted(out, key=lambda x: -x["b"])

def tenant_reminder_targets(today=None):
    today = today or date.today()
    sent = _reminded_today(today)
    out = []
    for t, b in debtors(parse_float(get_setting("reminder_min_usd"), 1)):
        base = "" if t["remind_enabled"] else "напоминания выключены в карточке"
        out.append(_target("t", t, b, None, notif_text("reminder", t, b), base, sent))
    return out

def run_client_reminders(today=None):
    n = 0
    for x in reminder_targets(today):
        if not x["skip"]:
            ch, _ = deliver("reminder", x["text"], client=x["r"], commit=False)
            n += 1 if ch else 0
    db().commit()
    return n

def run_reminders(today=None):
    """Плановая рассылка (по умолчанию Пн и Чт 10:00): должники-арендаторы и клиенты-должники."""
    n = 0
    if get_setting("remind_clients") == "1":
        n += run_client_reminders(today)
    if get_setting("remind_tenants") == "1":
        n += send_reminders(today)
    return n

# ---------------------------------------------------------------- страницы клиентов
def _client_rows(show="debtors"):
    """Строки списка клиентов (для страницы и для Excel): show = debtors | active | all."""
    bal = client_balances()
    rows = []
    for c in q("SELECT * FROM clients" + ("" if show == "all" else " WHERE active=1") + " ORDER BY name"):
        b = bal.get(c["id"], 0)
        if show == "debtors" and b <= 0.5:
            continue
        days, _ = client_overdue(c["id"])
        last = q("SELECT MAX(pay_date) d FROM client_payments WHERE client_id=?", (c["id"],), one=True)["d"]
        lsms = messages_for(client_id=c["id"], limit=1)
        rows.append(dict(c=c, b=b, days=days, last=last, lsms=lsms[0] if lsms else None, tg=c["tg_status"]))
    if show == "debtors":
        rows.sort(key=lambda r: (-r["days"], -r["b"]))
    return rows, bal

@app.route("/clients")
@login_required
def clients():
    show = request.args.get("show", "debtors")
    rows, bal = _client_rows(show)
    total = sum(max(v, 0) for v in bal.values())
    kinds = {}
    for r in q("SELECT kind, currency, SUM(amount) s FROM client_debts GROUP BY kind, currency"):
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + (to_uzs(r["s"], 0) if r["currency"] == "USD" else r["s"])
    return render_template("clients.html", channels=client_channels(), rows=rows, show=show, total=total, n_debtors=sum(1 for v in bal.values() if v > 0.5),
                           over30=sum(1 for r in rows if r["days"] > 30 and r["b"] > 0.5), kinds=kinds, nxt=next_sms_run())

@app.route("/clients/new", methods=["GET", "POST"])
@app.route("/clients/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def client_form(cid=None):
    c = get_client(cid) if cid else None
    if request.method == "POST":
        f = request.form
        if not f.get("name", "").strip():
            flash("Укажите имя клиента", "error")
            return redirect(request.url)
        vals = (f["name"].strip(), f.get("phone", "").strip(), f.get("company", "").strip(), f.get("notes", "").strip(),
                1 if f.get("active") else 0, 1 if f.get("sms_enabled") else 0, f.get("sms_paused_until") or None)
        if c:
            db().execute("UPDATE clients SET name=?, phone=?, company=?, notes=?, active=?, sms_enabled=?, sms_paused_until=? WHERE id=?", vals + (cid,))
            if smsmod.normalize_phone(c["phone"]) != smsmod.normalize_phone(vals[1]):
                db().execute("UPDATE clients SET tg_status='', tg_user_id=NULL WHERE id=?", (cid,))
        else:
            cid = db().execute("INSERT INTO clients(name,phone,company,notes,active,sms_enabled,sms_paused_until,tg_code,is_demo,created_at) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?)", vals + (new_code(), 1 if f.get("is_demo") else 0, now())).lastrowid
        if "lang" in f:
            db().execute("UPDATE clients SET lang=? WHERE id=?", (f.get("lang") if f.get("lang") in LANGS else "", cid))
            if "tg_chat" in f:
                db().execute("UPDATE clients SET tg_chat=? WHERE id=?", ((f.get("tg_chat") or "").strip()[:200], cid))
        if is_admin():
            ou, oz, od, on_ = opening_from_form(f, "op_")
            apply_client_opening(cid, ou, oz, od, on_, current_user()["id"])
        db().commit()
        if vals[1] and not smsmod.normalize_phone(vals[1]):
            flash("Внимание: номер не похож на узбекский мобильный (+998 XX XXX XX XX) — уведомления по нему не уйдут", "warn")
        flash("Клиент сохранён", "ok")
        return redirect(url_for("client_card", cid=cid))
    return render_template("client_form.html", c=c, op=client_openings(cid) if c else {"USD": None, "UZS": None, "note": "", "date": ""},
                           op_default=client_opening_default_date(cid if c else None))

@app.route("/clients/<int:cid>")
@login_required
def client_card(cid):
    c = get_client(cid)
    rows = [dict(kind="debt", id=d["id"], date=d["doc_date"], text=(("Заказ №" + d["order_no"] + ": ") if d["order_no"] else "") + (d["description"] or ""),
                 tag=DEBT_KINDS.get(d["kind"], d["kind"]), dkind=d["kind"], due=d["due_date"], debt=d["amount"], pay=0, cur=d["currency"],
                 sort=(d["doc_date"], 0, d["id"]))
            for d in q("SELECT * FROM client_debts WHERE client_id=?", (cid,))]
    for p in q("SELECT * FROM client_payments WHERE client_id=?", (cid,)):
        conv = client_pay_text(p)[len(cur(p["amount"], p["currency"])):].strip()
        up = p["usd_part"] if p["usd_part"] is not None else (p["amount"] if p["currency"] == "USD" else 0)
        zp = p["uzs_part"] if p["uzs_part"] is not None else (0 if p["currency"] == "USD" else p["amount"])
        rows.append(dict(kind="pay", id=p["id"], date=p["pay_date"], tag="", debt=0, pay=p["amount"], cur=p["currency"], up=up, zp=zp,
                         text="Оплата — " + METHODS.get(p["method"], p["method"]) + (f" ({p['note']})" if p["note"] else "") + (f" {conv}" if conv else ""),
                         sort=(p["pay_date"], 1, p["id"])))
    rows.sort(key=lambda r: r["sort"])
    u = z = 0.0
    rate = fx_rate()
    for r in rows:
        if r["kind"] == "debt":
            if r["cur"] == "USD":
                u += r["debt"]
            else:
                z += r["debt"]
        else:
            u -= r["up"]; z -= r["zp"]
        r["balance"] = to_uzs(u, z, rate)
    bal = to_uzs(u, z, rate)
    rows.reverse()
    days, oldest = client_overdue(cid)
    link = deep_link(c) if rent_bot_on() else ""
    qr = ""
    if link:
        import segno
        qr = segno.make(link, error="m").svg_inline(scale=3, dark="#1f2a27")
    oc = c["onec_code"] or ""
    erp_ext = oc[5:] if oc.startswith("1c77:") and q("SELECT 1 FROM erp_clients WHERE ext=? AND folder=0", (oc[5:],), one=True) else ""
    return render_template("client_card.html", c=c, rows=rows, balance=bal, days=days, oldest=oldest, link=link, qr=qr, erp_ext=erp_ext,
                           detail=client_pockets_text(u, z), op=client_openings(cid), op_default=client_opening_default_date(cid),
                           chats=q("SELECT * FROM client_chats WHERE client_id=? ORDER BY id", (cid,)),
                           msgs=messages_for(client_id=cid), chan=predict_channel(c, False), tgu=tgu_ready(), bot_enabled=rent_bot_on(),
                           preview=client_reminder_text(c), phone_ok=bool(smsmod.normalize_phone(c["phone"])),
                           paused=client_paused(c), bot=get_setting("tg_bot_username"), channels=client_channels(),
                           sms_test=get_setting("sms_provider") == "test",
                           grace=setting_int("client_grace_days"), pause_default=(date.today() + timedelta(days=7)).isoformat())

@app.route("/clients/<int:cid>/debt", methods=["POST"])
@login_required
def client_debt(cid):
    c, f = get_client(cid), request.form
    amount = parse_float(f.get("amount"))
    if amount <= 0:
        flash("Сумма долга должна быть больше 0", "error")
    else:
        d = parse_date(f.get("doc_date"), date.today())
        due = parse_date(f.get("due_date")) or (d + timedelta(days=setting_int("client_grace_days")))
        currency = f.get("currency") if f.get("currency") in CURRENCIES else "UZS"
        db().execute("INSERT INTO client_debts(client_id,doc_date,due_date,order_no,description,kind,amount,created_by,created_at,is_demo,currency,fx_rate) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (cid, d.isoformat(), due.isoformat(), f.get("order_no", "").strip(),
                                                    f.get("description", "").strip(), f.get("kind") if f.get("kind") in DEBT_KINDS else "services",
                                                    amount, current_user()["id"], now(), c["is_demo"], currency, fx_rate()))
        db().commit()
        flash(f"Долг {cur(amount, currency)} добавлен. Текущий долг клиента: {money(client_balance(cid))} сум", "ok")
    return redirect(url_for("client_card", cid=cid))

@app.route("/clients/<int:cid>/payment", methods=["POST"])
@login_required
def client_payment(cid):
    c, f = get_client(cid), request.form
    amount = parse_float(f.get("amount"))
    if amount <= 0:
        flash("Сумма оплаты должна быть больше 0", "error")
        return redirect(url_for("client_card", cid=cid))
    d = parse_date(f.get("pay_date"), date.today())
    currency = f.get("currency") if f.get("currency") in CURRENCIES else "UZS"
    rate = fx_rate()
    up, zp = alloc_split(*client_pockets(cid), amount, currency, rate)
    db().execute("INSERT INTO client_payments(client_id,pay_date,amount,method,note,created_by,created_at,is_demo,currency,fx_rate,usd_part,uzs_part) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, d.isoformat(), amount, f.get("method") if f.get("method") in METHODS else "cash", f.get("note", "").strip(),
                  current_user()["id"], now(), c["is_demo"], currency, rate, up, zp))
    db().commit()
    b = client_balance(cid)
    msg = f"Оплата {cur(amount, currency)} записана. " + (f"Остаток долга: {money(b)} сум" if b > 0.5 else "Долг погашен")
    if f.get("notify") and get_setting("sms_on_payment") == "1":
        pr = q("SELECT * FROM client_payments WHERE client_id=? ORDER BY id DESC LIMIT 1", (cid,), one=True)
        res = safe_notify(deliver, "payment", notif_text("payment", c, client_usd_balance(cid), pay_usd(pr)), None, c)
        msg += ". " + channel_note(res).capitalize()
    flash(msg, "ok")
    return redirect(url_for("client_card", cid=cid))

@app.route("/clients/<int:cid>/opening", methods=["POST"])
@admin_required
def client_opening(cid):
    get_client(cid)
    ou, oz, od, on_ = opening_from_form(request.form, "op_")
    if request.form.get("action") == "delete":
        ou, oz = 0.0, 0.0
    res = apply_client_opening(cid, ou, oz, od, on_, current_user()["id"])
    db().commit()
    op = client_openings(cid)
    parts = [cur(op[k]["amount"], k) for k in ("UZS", "USD") if op[k]]
    flash(("Начальный долг сохранён: " + " + ".join(parts) if parts else "Начальный долг удалён") +
          f". Долг клиента: {money(client_balance(cid))} сум. Сообщение клиенту не отправлялось" if res else "Начальный долг не изменился", "ok")
    return redirect(url_for("client_card", cid=cid))

@app.route("/clients/<int:cid>/remind", methods=["POST"])
@login_required
def client_remind(cid):
    c = get_client(cid)
    text = request.form.get("text", "").strip() or client_reminder_text(c)
    ch = request.form.get("channel", "auto")
    res = safe_notify(deliver, "manual", text, None, c, ch if ch in ("auto", "tg", "sms") else "auto")
    flash(channel_note(res).capitalize(), "ok" if res[0] else "error")
    return redirect(url_for("client_card", cid=cid))

@app.route("/clients/<int:cid>/sms", methods=["POST"])
@login_required
def client_sms_settings(cid):
    get_client(cid)
    act = request.form.get("action")
    if act == "pause":
        until = parse_date(request.form.get("until")) or (date.today() + timedelta(days=7))
        db().execute("UPDATE clients SET sms_paused_until=? WHERE id=?", (until.isoformat(), cid))
        flash(f"Напоминания на паузе до {ddmm(until)} включительно", "ok")
    elif act == "resume":
        db().execute("UPDATE clients SET sms_paused_until=NULL, sms_enabled=1 WHERE id=?", (cid,))
        flash("Автонапоминания включены", "ok")
    elif act == "off":
        db().execute("UPDATE clients SET sms_enabled=0 WHERE id=?", (cid,))
        flash("Автонапоминания для клиента выключены", "ok")
    db().commit()
    return redirect(url_for("client_card", cid=cid))

@app.route("/clients/<int:cid>/tg", methods=["POST"])
@login_required
def client_tg(cid):
    get_client(cid)
    if request.form.get("action") in ("regen", "unlink") and not is_admin():
        abort(403)
    if request.form.get("action") == "recheck":
        db().execute("UPDATE clients SET tg_status='', tg_user_id=NULL WHERE id=?", (cid,))
    elif request.form.get("action") == "regen":
        db().execute("UPDATE clients SET tg_code=? WHERE id=?", (new_code(), cid))
    elif request.form.get("action") == "unlink":
        db().execute("DELETE FROM client_chats WHERE id=? AND client_id=?", (request.form.get("chat"), cid))
    db().commit()
    flash("Сохранено", "ok")
    return redirect(url_for("client_card", cid=cid))

@app.route("/client-debts/<int:did>/delete", methods=["POST"])
@admin_required
def client_debt_delete(did):
    d = q("SELECT * FROM client_debts WHERE id=?", (did,), one=True) or abort(404)
    db().execute("DELETE FROM client_debts WHERE id=?", (did,)); db().commit()
    flash("Запись долга удалена", "ok")
    return redirect(url_for("client_card", cid=d["client_id"]))

@app.route("/client-payments/<int:pid>/delete", methods=["POST"])
@admin_required
def client_payment_delete(pid):
    p = q("SELECT * FROM client_payments WHERE id=?", (pid,), one=True) or abort(404)
    db().execute("DELETE FROM client_payments WHERE id=?", (pid,)); db().commit()
    flash("Оплата удалена", "ok")
    return redirect(url_for("client_card", cid=p["client_id"]))

# ---------------------------------------------------------------- язык уведомлений: смена для нескольких сразу
@app.route("/clients.xlsx")
@login_required
def clients_xlsx():
    """Список клиентов (по текущему фильтру) в Excel: долг, просрочка, последняя оплата, контакты."""
    Workbook, Font, *_ = _xl()
    show = request.args.get("show", "debtors")
    if show not in ("debtors", "active", "all"):
        show = "debtors"
    rows, _ = _client_rows(show)
    titles = {"debtors": "должники", "active": "все активные", "all": "все, включая архив"}
    wb = Workbook(); ws = wb.active; ws.title = "Клиенты"
    ws["A1"] = f"Клиенты — {titles[show]} на {date.today().strftime('%d.%m.%Y')}"; ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = "Долг в сумах: + долг клиента, − аванс. Просрочка — дней с даты оплаты самого старого непогашенного заказа"
    head = ["Клиент", "Компания", "Телефон", "Долг, сум", "Просрочка, дн.", "Посл. оплата", "Активен", "Заметки"]
    _xl_header(ws, head, 4)
    total = 0
    for r in rows:
        c, b = r["c"], round(r["b"])
        total += max(b, 0)
        ws.append([c["name"], c["company"] or "", c["phone"] or "", b, r["days"] if b > 0 and r["days"] else None,
                   ddmm(r["last"]) if r["last"] else "", "да" if c["active"] else "нет", c["notes"] or ""])
    ws.append(["ИТОГО долг", "", "", total])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for row in ws.iter_rows(min_row=5, min_col=4, max_col=4):
        for cell in row:
            cell.number_format = "# ##0"
    for col, w in zip("ABCDEFGH", (30, 24, 18, 15, 13, 13, 9, 40)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "B5"
    return _xlsx_response(wb, f"klienty_{show}_{date.today().isoformat()}.xlsx")


def _digits(s):
    return re.sub(r"\D", "", s or "")

@app.route("/search")
@login_required
def search():
    """Быстрый поиск арендаторов и клиентов: имя, помещение, компания, категория, телефон (по цифрам)."""
    term = (request.args.get("q") or "").strip()
    tenants_f, clients_f, eclients, egoods = [], [], [], []
    if term:
        import kb, erp as _E
        lows, dg = [v.casefold() for v in kb.variants(term)], _digits(term)
        _w, _a = _E.words_sql(term)
        if _w:
            eclients = q("SELECT ext, name, phones, ckind FROM erp_clients WHERE folder=0 AND deleted=0" + _w + " ORDER BY name LIMIT 50", _a)
            egoods = q("SELECT ext, code, name, unit FROM erp_goods WHERE folder=0 AND deleted=0" + _w + " ORDER BY name LIMIT 50", _a)
        if len(dg) >= 3:
            have = {r["ext"] for r in eclients}
            eclients = list(eclients) + [r for r in q("SELECT ext, name, phones, ckind FROM erp_clients WHERE folder=0 AND deleted=0 AND phones<>'' ORDER BY name")
                                         if r["ext"] not in have and dg in _digits(r["phones"])][:30]
        tb = all_balances()
        for t in q("SELECT * FROM tenants ORDER BY active DESC, name"):
            hay = " ".join(str(t[k] or "") for k in ("name", "unit", "category", "notes")).casefold()
            if any(lw in hay for lw in lows) or (len(dg) >= 3 and dg in _digits(t["phone"])):
                tenants_f.append(dict(t=t, b=tb.get(t["id"], 0)))
        cb = client_balances()
        for c in q("SELECT * FROM clients ORDER BY active DESC, name"):
            hay = " ".join(str(c[k] or "") for k in ("name", "company", "notes")).casefold()
            if any(lw in hay for lw in lows) or (len(dg) >= 3 and dg in _digits(c["phone"])):
                clients_f.append(dict(c=c, b=cb.get(c["id"], 0)))
        if len(tenants_f) + len(clients_f) == 1 and not eclients and not egoods:
            if tenants_f:
                return redirect(url_for("tenant_card", tid=tenants_f[0]["t"]["id"]))
            return redirect(url_for("client_card", cid=clients_f[0]["c"]["id"]))
    import kb
    alt = [v for v in kb.variants(term)[1:]] if term else []
    return render_template("search.html", term=term, tenants=tenants_f[:100], clients=clients_f[:100], eclients=eclients, egoods=egoods, alt=alt)


# ================================================================ 1С (COM, только чтение)
def onec_conn_string():
    return onec.build_conn_string(get_setting("onec_mode"), get_setting("onec_path"), get_setting("onec_server"),
                                  get_setting("onec_ref"), get_setting("onec_user"), get_setting("onec_password"))

def onec_query_text():
    pr = get_setting("onec_preset")
    if pr == "custom" or pr not in onec.PRESETS:
        return get_setting("onec_query")
    return onec.PRESETS[pr]["query"]

def onec_fetch():
    return onec.fetch(onec_conn_string(), onec_query_text(), get_setting("onec_account"))

def onec_plan(rows):
    """Что изменится в платформе: [{c (клиент или None), code, name, phone, debt_1c, debt_here, diff, action}]."""
    bal = client_balances()
    linked = {c["onec_code"]: c for c in q("SELECT * FROM clients WHERE onec_code IS NOT NULL AND onec_code<>''")}
    by_name = {}
    for c in q("SELECT * FROM clients WHERE onec_code IS NULL OR onec_code='' ORDER BY active DESC, id"):
        by_name.setdefault(c["name"].strip().casefold(), c)
    plan, seen = [], set()
    for r in rows:
        c = linked.get(r["code"])
        how = "linked"
        if not c:
            c = by_name.get(r["name"].strip().casefold())
            how = "by_name" if c else "new"
        if c:
            seen.add(c["id"])
        here = bal.get(c["id"], 0) if c else 0
        if not c and r["debt"] <= 0.5:
            continue                      # нового клиента без долга не заводим
        diff = round(r["debt"] - here, 2)
        plan.append(dict(c=c, code=r["code"], name=r["name"], phone=r["phone"], debt_1c=r["debt"], debt_here=here,
                         diff=diff if abs(diff) >= 1 else 0, how=how))
    for code, c in linked.items():        # был в 1С, сейчас долга там нет -> обнулить
        if c["id"] not in seen:
            here = bal.get(c["id"], 0)
            plan.append(dict(c=c, code=code, name=c["name"], phone="", debt_1c=0.0, debt_here=here,
                             diff=round(-here, 2) if abs(here) >= 1 else 0, how="gone"))
    plan.sort(key=lambda x: (x["diff"] == 0 and x["how"] != "new", -x["debt_1c"]))
    return plan

def onec_apply(plan, uid=None):
    """Привести долги клиентов к 1С: новые клиенты + записи «Сверка с 1С» (долг или оплата). Сообщения не отправляются."""
    today, ts = date.today().isoformat(), now()
    st_ = {"new": 0, "linked": 0, "debt": 0, "pay": 0, "sum_up": 0.0, "sum_down": 0.0}
    remind = 1 if get_setting("onec_new_remind") == "1" else 0
    for x in plan:
        c = x["c"]
        if not c:
            cid = db().execute("INSERT INTO clients(name,phone,company,notes,active,sms_enabled,tg_code,is_demo,created_at,onec_code) "
                               "VALUES(?,?,?,?,1,?,?,0,?,?)",
                               (x["name"][:200], x["phone"][:40], "", f"Из 1С (код {x['code']})", remind, new_code(), ts, x["code"])).lastrowid
            st_["new"] += 1
        else:
            cid = c["id"]
            if (c["onec_code"] or "") != x["code"]:
                db().execute("UPDATE clients SET onec_code=? WHERE id=?", (x["code"], cid)); st_["linked"] += 1
            if x["phone"] and not (c["phone"] or "").strip():
                db().execute("UPDATE clients SET phone=? WHERE id=?", (x["phone"][:40], cid))
        d = x["diff"]
        if d >= 1:
            db().execute("INSERT INTO client_debts(client_id,doc_date,due_date,order_no,description,kind,amount,created_by,created_at,is_demo,currency,onec) "
                         "VALUES(?,?,?,?,?,?,?,?,?,0,'UZS',1)",
                         (cid, today, today, "1С", "Сверка с 1С: долг по 1С " + som(x["debt_1c"]) + " сум", "materials", d, uid, ts))
            st_["debt"] += 1; st_["sum_up"] += d
        elif d <= -1:
            db().execute("INSERT INTO client_payments(client_id,pay_date,amount,method,note,created_by,created_at,is_demo,currency,usd_part,uzs_part,onec) "
                         "VALUES(?,?,?,?,?,?,?,0,'UZS',0,?,1)",
                         (cid, today, -d, "transfer", "Сверка с 1С: долг по 1С " + som(x["debt_1c"]) + " сум", uid, ts, -d))
            st_["pay"] += 1; st_["sum_down"] += -d
    db().commit()
    return st_

def onec_sync(uid=None):
    info, rows, warns = onec_fetch()
    st_ = onec_apply(onec_plan(rows), uid)
    msg = (f"контрагентов с долгом в 1С: {sum(1 for r in rows if r['debt'] > 0.5)}; новых клиентов: {st_['new']}; "
           f"долг увеличен у {st_['debt']} (+{som(st_['sum_up'])} сум), уменьшен у {st_['pay']} (−{som(st_['sum_down'])} сум)")
    set_settings(onec_last_at=now(), onec_last_result=msg, onec_last_error="; ".join(warns),
                 onec_info=f"{info['synonym'] or info['name']} {info['version']}".strip())
    return st_

def onec_due(dt):
    m = setting_int("onec_auto_minutes")
    if m <= 0 or not (get_setting("onec_path") or get_setting("onec_server")):
        return False
    last = get_setting("onec_last_at", "")
    try:
        return (dt - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() >= m * 60
    except ValueError:
        return True

@app.route("/admin/onec", methods=["GET", "POST"])
@admin_required
def onec_page():
    plan = info = None
    warns = []
    if request.method == "POST":
        f, act = request.form, request.form.get("action")
        if act == "save":
            kw = dict(onec_mode="server" if f.get("onec_mode") == "server" else "file",
                      onec_path=f.get("onec_path", "").strip(), onec_server=f.get("onec_server", "").strip(),
                      onec_ref=f.get("onec_ref", "").strip(), onec_user=f.get("onec_user", "").strip(),
                      onec_preset=f.get("onec_preset") if f.get("onec_preset") in onec.PRESETS else "buh_account",
                      onec_query=f.get("onec_query", "").strip(), onec_account=re.sub(r"[^0-9.%]", "", f.get("onec_account", "")) or "4010",
                      onec_auto_minutes=str(max(0, int(parse_float(f.get("onec_auto_minutes"), 0)))),
                      onec_new_remind="1" if f.get("onec_new_remind") else "0")
            if f.get("onec_password"):
                kw["onec_password"] = f["onec_password"]
            if f.get("onec_password_clear"):
                kw["onec_password"] = ""
            set_settings(**kw)
            flash("Настройки 1С сохранены", "ok")
            return redirect(url_for("onec_page"))
        try:
            if act == "test":
                info = onec.test_connection(onec_conn_string())
                set_settings(onec_info=f"{info['synonym'] or info['name']} {info['version']}".strip())
                flash(f"Подключение к 1С работает: {info['synonym'] or info['name']} {info['version']}", "ok")
            elif act == "preview":
                info, rows, warns = onec_fetch()
                plan = onec_plan(rows)
                if not rows:
                    warns.append("Запрос 1С не вернул ни одной строки. Проверьте счёт (поле «Счёт расчётов») или запрос.")
            elif act == "sync":
                u = current_user()
                st_ = onec_sync(u["id"] if u else None)
                flash("Загружено из 1С: " + get_setting("onec_last_result"), "ok")
                return redirect(url_for("onec_page"))
        except onec.OneCError as e:
            set_settings(onec_last_error=str(e))
            flash("1С: " + str(e), "error")
    st = {k: get_setting(k) for k in DEFAULT_SETTINGS if k.startswith("onec_")}
    st["has_password"] = bool(st.pop("onec_password"))
    n_linked = scalar("SELECT COUNT(*) FROM clients WHERE onec_code IS NOT NULL AND onec_code<>''")
    return render_template("onec.html", st=st, plan=plan, info=info, warns=warns, presets=onec.PRESETS,
                           phone_query=onec.PHONE_QUERY, bits=onec.python_bits(), n_linked=n_linked)

@app.route("/admin/lang", methods=["POST"])
@admin_required
def bulk_lang():
    f = request.form
    kind = "clients" if f.get("kind") == "clients" else "tenants"
    lang = f.get("lang") if f.get("lang") in LANGS else ""
    ids = [int(x) for x in f.getlist("ids") if x.isdigit()]
    if not ids:
        flash("Отметьте галочками, кому сменить язык", "warn")
    else:
        db().executemany(f"UPDATE {kind} SET lang=? WHERE id=?", [(lang, i) for i in ids]); db().commit()
        flash(f"Язык уведомлений «{LANGS.get(lang, 'по умолчанию (' + LANGS[lang_of()] + ')')}» установлен: {len(ids)}", "ok")
    return redirect(f.get("back") or url_for(kind))

# ---------------------------------------------------------------- ввод начальных долгов: таблица и импорт из Excel
@app.route("/admin/opening", methods=["GET", "POST"])
@admin_required
def opening_page():
    """Начальные долги всех арендаторов и клиентов одной таблицей (сохранить всё сразу). Сообщения не отправляются."""
    if request.method == "POST":
        f, uid = request.form, current_user()["id"]
        d = parse_date(f.get("date")) if (f.get("date") or "").strip() else None
        nt = nc = 0
        for tid in f.getlist("t_id"):
            if not tid.isdigit() or not q("SELECT 1 FROM tenants WHERE id=?", (tid,), one=True):
                continue
            ou, oz, _, on_ = opening_from_form(f, f"t{tid}_")
            nt += 1 if apply_tenant_opening(int(tid), ou, oz, d, on_, uid) else 0
        for cid in f.getlist("c_id"):
            if not cid.isdigit() or not q("SELECT 1 FROM clients WHERE id=?", (cid,), one=True):
                continue
            ou, oz, _, on_ = opening_from_form(f, f"c{cid}_")
            nc += 1 if apply_client_opening(int(cid), ou, oz, d, on_, uid) else 0
        db().commit()
        flash(f"Сохранено: арендаторов — {nt}, клиентов — {nc}. Сообщения не отправлялись; долги учтены в балансе и плановых напоминаниях"
              if nt or nc else "Изменений нет", "ok" if nt or nc else "warn")
        return redirect(url_for("opening_page"))
    ops, cops = all_openings(), all_client_openings()
    bal, cbal = all_balances(), client_balances()
    trows = [dict(t=t, op=ops.get(t["id"], {"USD": None, "UZS": None, "note": "", "date": ""}), b=bal.get(t["id"], 0),
                  dflt=opening_default_date(t["id"], t["contract_start"]))
             for t in q("SELECT * FROM tenants WHERE active=1 OR id IN (SELECT tenant_id FROM charges WHERE source='opening') ORDER BY category, unit, name")]
    crows = [dict(c=c, op=cops.get(c["id"], {"USD": None, "UZS": None, "note": "", "date": ""}), b=cbal.get(c["id"], 0),
                  dflt=client_opening_default_date(c["id"]))
             for c in q("SELECT * FROM clients WHERE active=1 OR id IN (SELECT client_id FROM client_debts WHERE kind='opening') ORDER BY name")]
    tot = dict(t_usd=sum(r["op"]["USD"]["amount"] for r in trows if r["op"]["USD"]), t_uzs=sum(r["op"]["UZS"]["amount"] for r in trows if r["op"]["UZS"]),
               c_usd=sum(r["op"]["USD"]["amount"] for r in crows if r["op"]["USD"]), c_uzs=sum(r["op"]["UZS"]["amount"] for r in crows if r["op"]["UZS"]))
    return render_template("opening.html", trows=trows, crows=crows, tot=tot, rent_cur=ct_currency(ct_by_code("rent")["id"]) if ct_by_code("rent") else "USD")

def _cur_word(c_):
    return "$" if c_ == "USD" else "сум"

def _import_cols():
    rc = _cur_word(ct_currency(ct_by_code("rent")["id"])) if ct_by_code("rent") else "$"
    tcols = [("id", "ID (не менять; пусто — новый)"), ("name", "Название / ФИО *"), ("unit", "Помещение"), ("category", "Категория"),
             ("phone", "Телефон"), ("area", "Площадь, м²"), ("rent_rate", f"Ставка аренды за м², {rc} (пусто — общая)"),
             ("rent_amount", f"Фикс. аренда в месяц, {rc} (вместо м²)"), ("contract_start", "Начало договора (ДД.ММ.ГГГГ)"),
             ("op_usd", "Начальный долг, $ (− аванс)"), ("op_uzs", "Начальный долг, сум (− аванс)"),
             ("op_date", "Дата долга (пусто — день перед первым счётом)"), ("op_note", "Комментарий к долгу"),
             ("lang", "Язык уведомлений (Русский / O'zbekcha)")]
    ccols = [("id", "ID (не менять; пусто — новый)"), ("name", "Имя / название *"), ("phone", "Телефон"), ("company", "Компания"),
             ("op_uzs", "Начальный долг, сум (− аванс)"), ("op_usd", "Начальный долг, $ (− аванс)"),
             ("op_date", "Дата долга (пусто — сегодня)"), ("op_note", "Комментарий к долгу"),
             ("lang", "Язык уведомлений (Русский / O'zbekcha)")]
    return tcols, ccols

def _hkey(h, kind):
    h = str(h or "").strip().lower()
    if not h:
        return None
    if h.startswith("id"):
        return "id"
    if h.startswith(("начальный долг", "долг на начало", "старый долг", "долг")):
        if "$" in h or "usd" in h or "долл" in h:
            return "op_usd"
        if "сум" in h or "uzs" in h:
            return "op_uzs"
    if h.startswith(("язык", "til", "lang")):
        return "lang"
    rules = [(("назван", "фио", "имя"), "name"), (("помещ",), "unit"), (("катег",), "category"), (("телеф",), "phone"),
             (("площ",), "area"), (("ставк",), "rent_rate"), (("фикс",), "rent_amount"), (("начало дог", "договор"), "contract_start"),
             (("компан",), "company"), (("дата",), "op_date"), (("коммент", "примеч"), "op_note")]
    for pre, k in rules:
        if h.startswith(pre):
            return k
    return None

def _xl_num(v):
    """-> (число|None, ошибка)"""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, ""
    if isinstance(v, (int, float)):
        return float(v), ""
    s_ = str(v).replace(" ", "").replace("\u00a0", "").replace(",", ".").replace("$", "").replace("сум", "")
    s_ = s_.replace("−", "-").replace("–", "-")
    try:
        return float(s_), ""
    except ValueError:
        return None, f"не число: «{v}»"

def _xl_date(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, ""
    if isinstance(v, datetime):
        return v.date(), ""
    if isinstance(v, date):
        return v, ""
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date(), ""
        except ValueError:
            pass
    return None, f"не дата: «{v}» (нужно ДД.ММ.ГГГГ)"

def _parse_lang(v):
    """«Русский» / «ru» -> 'ru', «O'zbekcha» / «uz» / «узбекский» -> 'uz', «по умолчанию» -> '', иначе None."""
    x = v.strip().lower().replace("ʻ", "'").replace("‘", "'").replace("’", "'")
    if x.startswith(("ru", "рус", "rus")):
        return "ru"
    if x.startswith(("uz", "o'z", "oz", "узб", "ўзб", "uzb")):
        return "uz"
    if x.startswith(("по умолч", "default", "общ")):
        return ""
    return None

def _xl_str(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()

@app.route("/admin/opening/template.xlsx")
@admin_required
def opening_template():
    """Шаблон Excel: текущие арендаторы и клиенты (с начальными долгами) — заполнить и загрузить обратно."""
    Workbook, Font, PatternFill, Alignment, *_ = _xl()
    tcols, ccols = _import_cols()
    wb = Workbook(); ws = wb.active; ws.title = "Арендаторы"
    _xl_header(ws, [c[1] for c in tcols], 1)
    ops = all_openings()
    for t in q("SELECT * FROM tenants WHERE active=1 ORDER BY category, unit, name"):
        o = ops.get(t["id"], {"USD": None, "UZS": None, "note": "", "date": ""})
        ws.append([t["id"], t["name"], t["unit"] or "", t["category"] or "", t["phone"] or "", t["area"] or None,
                   t["rent_rate"] if t["rent_rate"] is not None else None, t["rent_amount"] or None,
                   parse_date(t["contract_start"]) if t["contract_start"] else None,
                   o["USD"]["amount"] if o["USD"] else None, o["UZS"]["amount"] if o["UZS"] else None,
                   parse_date(o["date"]) if o["date"] else None, o["note"], LANGS.get(t["lang"], "")])
    ws2 = wb.create_sheet("Клиенты")
    _xl_header(ws2, [c[1] for c in ccols], 1)
    cops = all_client_openings()
    for c in q("SELECT * FROM clients WHERE active=1 ORDER BY name"):
        o = cops.get(c["id"], {"USD": None, "UZS": None, "note": "", "date": ""})
        ws2.append([c["id"], c["name"], c["phone"] or "", c["company"] or "", o["UZS"]["amount"] if o["UZS"] else None,
                    o["USD"]["amount"] if o["USD"] else None, parse_date(o["date"]) if o["date"] else None, o["note"], LANGS.get(c["lang"], "")])
    for w in (ws, ws2):
        w.freeze_panes = "B2"
        for i in range(1, w.max_column + 1):
            w.column_dimensions[w.cell(row=1, column=i).column_letter].width = 16 if i > 2 else (8 if i == 1 else 32)
        w.row_dimensions[1].height = 45
        for row in w.iter_rows(min_row=2):
            for cell in row:
                if hasattr(cell.value, "year"):
                    cell.number_format = "DD.MM.YYYY"
    wi = wb.create_sheet("Инструкция")
    for line in ["Как заполнить:",
                 "1. Лист «Арендаторы»: одна строка — один арендатор. Новые — с пустым ID. Существующих находим по ID (или по названию).",
                 "2. Пустая ячейка у существующего арендатора/клиента — значение не меняется. 0 в «Начальный долг» — удалить начальный долг.",
                 "3. Начальный долг: отдельно в $ и в сумах; со знаком минус — аванс (переплата). Дата пустая — день перед первым счётом.",
                 "4. Лист «Клиенты»: старый долг без заказа — в сумах и/или в $.",
                 "Язык уведомлений: «Русский» или «O'zbekcha» (можно ru / uz); «по умолчанию» — общий язык из «Настройки»; пусто — не менять.",
                 "5. Сохраните файл и загрузите на странице «Начальные долги» → «Импорт из Excel». Перед записью будет показан предпросмотр.",
                 "Сообщения в Telegram при импорте не отправляются; долги учитываются в балансе и плановых напоминаниях."]:
        wi.append([line])
    wi.column_dimensions["A"].width = 120
    wb.move_sheet("Инструкция", offset=-2)
    wb.active = 1
    return _xlsx_response(wb, "nachalnye_dolgi_shablon.xlsx")

def _import_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "imports")
    os.makedirs(d, exist_ok=True)
    for fn in os.listdir(d):                              # старые незавершённые импорты (> 1 суток)
        fp = os.path.join(d, fn)
        if time.time() - os.path.getmtime(fp) > 86400:
            try:
                os.remove(fp)
            except OSError:
                pass
    return d

def parse_import(fileobj):
    """Excel -> предпросмотр: {'tenants': [...], 'clients': [...], 'errors': n}. Ничего не записывает."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(fileobj, data_only=True, read_only=True)
    except Exception as e:  # noqa
        raise ValueError(f"Не удалось прочитать файл Excel (.xlsx): {e}")
    res = {"tenants": [], "clients": [], "errors": 0}
    sheets = {ws.title.strip().lower(): ws for ws in wb.worksheets}
    tsh = sheets.get("арендаторы") or next((w for k, w in sheets.items() if k.startswith("аренд")), None)
    csh = sheets.get("клиенты") or next((w for k, w in sheets.items() if k.startswith("клиент")), None)
    if not tsh and not csh:
        raise ValueError("В файле нет листов «Арендаторы» / «Клиенты». Скачайте шаблон и заполните его")
    by_name_t = {(r["name"] or "").strip().lower(): r for r in q("SELECT * FROM tenants")}
    by_name_c = {(r["name"] or "").strip().lower(): r for r in q("SELECT * FROM clients")}
    for kind, sh in (("tenants", tsh), ("clients", csh)):
        if not sh:
            continue
        rows = list(sh.iter_rows(values_only=True))
        if not rows:
            continue
        keys = [_hkey(h, kind) for h in rows[0]]
        if "name" not in keys:
            raise ValueError(f"Лист «{sh.title}»: не найдена колонка с названием/именем")
        seen = set()
        for n, raw in enumerate(rows[1:], start=2):
            vals = {k: v for k, v in zip(keys, raw) if k}
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in vals.values()):
                continue
            item, errs = {"row": n, "data": {}}, []
            name = _xl_str(vals.get("name"))
            ex = None
            rid, e = _xl_num(vals.get("id"))
            if rid:
                ex = q(f"SELECT * FROM {'tenants' if kind == 'tenants' else 'clients'} WHERE id=?", (int(rid),), one=True)
                if not ex:
                    errs.append(f"ID {int(rid)} не найден")
            if not ex and name:
                ex = (by_name_t if kind == "tenants" else by_name_c).get(name.lower())
            if not name and not ex:
                errs.append("нет названия/имени")
            key = (ex["id"] if ex else None, name.lower())
            if key in seen:
                errs.append("повторяется в файле")
            seen.add(key)
            d = item["data"]
            if name:
                d["name"] = name
            for k in ("unit", "category", "phone", "company", "op_note"):
                if k in vals and _xl_str(vals[k]):
                    d[k] = _xl_str(vals[k])
            for k in ("area", "rent_rate", "rent_amount", "op_usd", "op_uzs"):
                if k in vals:
                    v, e = _xl_num(vals[k])
                    if e:
                        errs.append(f"{k}: {e}")
                    elif v is not None:
                        if k in ("area", "rent_rate", "rent_amount") and v < 0:
                            errs.append(f"{k}: отрицательное значение")
                        d[k] = v
            if "lang" in vals and _xl_str(vals["lang"]):
                lv = _parse_lang(_xl_str(vals["lang"]))
                if lv is None:
                    errs.append(f"язык: «{_xl_str(vals['lang'])}» (нужно Русский / O'zbekcha)")
                else:
                    d["lang"] = lv
            for k in ("contract_start", "op_date"):
                if k in vals:
                    v, e = _xl_date(vals[k])
                    if e:
                        errs.append(e)
                    elif v:
                        d[k] = v.isoformat()
            item.update(id=ex["id"] if ex else None, action="update" if ex else "create", name=name or (ex["name"] if ex else ""), errors=errs)
            # что изменится (для предпросмотра)
            ch = []
            if ex:
                for k in ("name", "unit", "category", "phone", "company", "area", "rent_rate", "rent_amount", "contract_start", "lang"):
                    if k in d and k in ex.keys() and str(d[k]) != str(ex[k] if ex[k] is not None else ""):
                        if k in ("area", "rent_rate", "rent_amount") and ex[k] is not None and abs(float(ex[k]) - d[k]) < 1e-9:
                            continue
                        ch.append(k)
                op = tenant_openings(ex["id"]) if kind == "tenants" else client_openings(ex["id"])
                for k, c_ in (("op_usd", "USD"), ("op_uzs", "UZS")):
                    if k in d and abs((op[c_]["amount"] if op[c_] else 0) - d[k]) >= 0.005:
                        ch.append(k)
                if ("op_date" in d and d["op_date"] != op["date"] and (op["USD"] or op["UZS"] or d.get("op_usd") or d.get("op_uzs"))) or \
                        ("op_note" in d and d["op_note"] != op["note"]):
                    ch.append("op_meta")
            item["changes"] = ch
            if ex and not ch and not errs:
                item["action"] = "same"
            res["errors"] += 1 if errs else 0
            res[kind].append(item)
    return res

def apply_import(data, uid):
    """Записать предпросмотр. -> (создано, обновлено). Сообщения не отправляются."""
    created = updated = 0
    for it in data.get("tenants", []):
        if it["errors"] or it["action"] == "same":
            continue
        d = it["data"]
        if it["action"] == "create":
            tid = db().execute("INSERT INTO tenants(name,unit,area,phone,category,rent_amount,rent_rate,contract_start,notes,active,remind_enabled,"
                               "tg_code,is_demo,created_at,lang) VALUES(?,?,?,?,?,?,?,?,?,1,1,?,0,?,?)",
                               (d["name"], d.get("unit", ""), d.get("area", 0), d.get("phone", ""), d.get("category", ""),
                                d.get("rent_amount", 0), d.get("rent_rate") or None, d.get("contract_start"), "Импорт из Excel",
                                new_code(), now(), d.get("lang", ""))).lastrowid
            created += 1
        else:
            tid = it["id"]
            t = get_tenant(tid)
            sets = {k: d[k] for k in ("name", "unit", "category", "phone", "area", "rent_amount", "contract_start", "lang") if k in d}
            if "rent_rate" in d:
                sets["rent_rate"] = d["rent_rate"] or None
            if sets:
                db().execute("UPDATE tenants SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?", tuple(sets.values()) + (tid,))
                if "phone" in sets and smsmod.normalize_phone(t["phone"]) != smsmod.normalize_phone(sets["phone"]):
                    db().execute("UPDATE tenants SET tg_status='', tg_user_id=NULL WHERE id=?", (tid,))
            updated += 1
        apply_tenant_opening(tid, d.get("op_usd"), d.get("op_uzs"), parse_date(d.get("op_date")), d.get("op_note"), uid)
    for it in data.get("clients", []):
        if it["errors"] or it["action"] == "same":
            continue
        d = it["data"]
        if it["action"] == "create":
            cid = db().execute("INSERT INTO clients(name,phone,company,notes,active,sms_enabled,tg_code,is_demo,created_at,lang) VALUES(?,?,?,?,1,1,?,0,?,?)",
                               (d["name"], d.get("phone", ""), d.get("company", ""), "Импорт из Excel", new_code(), now(), d.get("lang", ""))).lastrowid
            created += 1
        else:
            cid = it["id"]
            c = get_client(cid)
            sets = {k: d[k] for k in ("name", "phone", "company", "lang") if k in d}
            if sets:
                db().execute("UPDATE clients SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?", tuple(sets.values()) + (cid,))
                if "phone" in sets and smsmod.normalize_phone(c["phone"]) != smsmod.normalize_phone(sets["phone"]):
                    db().execute("UPDATE clients SET tg_status='', tg_user_id=NULL WHERE id=?", (cid,))
            updated += 1
        apply_client_opening(cid, d.get("op_usd"), d.get("op_uzs"), parse_date(d.get("op_date")), d.get("op_note"), uid)
    db().commit()
    return created, updated

@app.route("/admin/opening/import", methods=["POST"])
@admin_required
def opening_import():
    f = request.form
    if f.get("action") == "apply":
        tok = f.get("token", "")
        if not re.fullmatch(r"[0-9a-f]{16,64}", tok):
            abort(400)
        fp = os.path.join(_import_dir(), f"import_{tok}.json")
        if not os.path.exists(fp):
            flash("Предпросмотр устарел — загрузите файл ещё раз", "error")
            return redirect(url_for("opening_page"))
        with open(fp, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("errors"):
            flash("В файле есть ошибки — исправьте и загрузите снова", "error")
            return redirect(url_for("opening_page"))
        cr, up = apply_import(data, current_user()["id"])
        os.remove(fp)
        flash(f"Импорт выполнен: создано {cr}, обновлено {up}. Сообщения не отправлялись", "ok")
        return redirect(url_for("opening_page"))
    up = request.files.get("file")
    if not up or not up.filename:
        flash("Выберите файл Excel (.xlsx)", "error")
        return redirect(url_for("opening_page"))
    if not up.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("Нужен файл Excel в формате .xlsx (не больше 5 МБ)", "error")
        return redirect(url_for("opening_page"))
    try:
        data = parse_import(io.BytesIO(up.read()))
    except ValueError as e:
        flash(f"Ошибка: {e}", "error")
        return redirect(url_for("opening_page"))
    tok = secrets.token_hex(16)
    with open(os.path.join(_import_dir(), f"import_{tok}.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return render_template("opening_import.html", data=data, token=tok, fname=up.filename,
                           n_new=sum(1 for k in ("tenants", "clients") for it in data[k] if it["action"] == "create" and not it["errors"]),
                           n_upd=sum(1 for k in ("tenants", "clients") for it in data[k] if it["action"] == "update" and not it["errors"]))

# ---------------------------------------------------------------- настройки SMS
@app.route("/admin/sms", methods=["GET", "POST"])
@admin_required
def sms_settings():
    if request.method == "POST":
        f, act = request.form, request.form.get("action")
        if act == "provider":
            prov = f.get("sms_provider") if f.get("sms_provider") in smsmod.PROVIDERS else "test"
            kw = dict(sms_provider=prov, eskiz_email=f.get("eskiz_email", "").strip(), eskiz_from=f.get("eskiz_from", "").strip() or "4546",
                      pm_login=f.get("pm_login", "").strip(), pm_originator=f.get("pm_originator", "").strip() or "3700",
                      pm_url=f.get("pm_url", "").strip())
            if f.get("eskiz_password"):
                kw["eskiz_password"] = f["eskiz_password"]
            if f.get("pm_password"):
                kw["pm_password"] = f["pm_password"]
            if kw["eskiz_email"] != get_setting("eskiz_email") or f.get("eskiz_password"):
                kw["eskiz_token"] = ""
            set_settings(**kw)
            flash(f"Провайдер: {smsmod.PROVIDERS[prov]}", "ok")
        elif act == "check":
            try:
                p = sms_provider()
                if p.name == "eskiz":
                    p._login(); flash("Eskiz: вход выполнен, токен получен", "ok")
                elif p.name == "playmobile":
                    flash("Play Mobile проверяется отправкой тестового SMS (ниже)", "warn")
                else:
                    flash("Тестовый режим: SMS не отправляются, только пишутся в журнал", "ok")
            except smsmod.SmsError as e:
                flash(str(e), "error")
        elif act == "test_send":
            phone = smsmod.normalize_phone(f.get("phone"))
            if not phone:
                flash("Укажите номер в формате +998 XX XXX XX XX", "error")
            else:
                db().execute("INSERT INTO sms_log(phone,text,kind,provider,status,created_at) VALUES(?,?,?,?,?,?)",
                             (phone, f.get("text", "").strip() or "Test", "test", get_setting("sms_provider"), "pending", now()))
                db().commit(); outbox_event.set()
                flash("Тестовое SMS поставлено в очередь — обновите страницу через несколько секунд и посмотрите журнал", "ok")
        elif act == "schedule":
            days = ",".join(str(i) for i in range(7) if f.get(f"d{i}"))
            tm = f.get("sms_time", "10:00")
            set_settings(sms_auto="1" if f.get("sms_auto") else "0", sms_days=days,
                         sms_time=tm if re.fullmatch(r"\d{1,2}:\d{2}", tm) else "10:00",
                         sms_min_debt=parse_float(f.get("sms_min_debt"), 0), sms_on_payment="1" if f.get("sms_on_payment") else "0",
                         sms_channel="1" if f.get("sms_channel") else "0", client_grace_days=int(parse_float(f.get("client_grace_days"), 0)),
                         remind_clients="1" if f.get("remind_clients") else "0", remind_tenants="1" if f.get("remind_tenants") else "0",
                         reminder_min_usd=parse_float(f.get("reminder_min_usd"), 0),
                         client_min_usd=parse_float(f.get("client_min_usd"), 10))       # мижозлар: қарз $ да
            flash("Расписание сохранено", "ok")
        elif act == "run_now":
            n = run_reminders()
            set_settings(sms_last_run_at=now(), sms_last_run_count=n)
            flash(f"Напоминания поставлены в очередь: {n}. Сообщения уходят по одному с паузой 20–60 сек. — смотрите «Журнал сообщений»" if n
                  else "Некому отправить (см. таблицу «Следующий запуск»)", "ok" if n else "warn")
        elif act == "retry":
            db().execute("UPDATE sms_log SET status='pending', next_try=0 WHERE status='failed' AND phone<>''"); db().commit()
            outbox_event.set()
            flash("Ошибочные SMS поставлены в очередь повторно", "ok")
        return redirect(url_for("sms_settings"))
    st = {k: get_setting(k) for k in DEFAULT_SETTINGS if k.startswith(("sms_", "eskiz_", "pm_", "client_", "remind"))}  # reminder_min_usd — порог для арендаторов, $
    st.update({k: get_setting(k, "") for k in ("sms_last_run", "sms_last_run_at", "sms_last_run_count")})
    targets = reminder_targets()
    ttargets = tenant_reminder_targets()
    sample = (targets[0]["text"] if targets else ttargets[0]["text"] if ttargets else
              fill(msg_tpl("reminder", lang_of()), {"name": "Rasul", "balance_text": bal_phrase(204.22, lang_of()), "company": get_setting("company_name")}))
    return render_template("sms.html", channels=client_channels(), st=st, days=sms_days(), nxt=next_sms_run(), targets=targets,
                           ttargets=ttargets, tgu=tgu_ready(), tq=tgu_queue_info(), st_expire=get_setting("tgu_expire_days"),
                           tq_delay=f"{get_setting('tgu_delay_min')}–{get_setting('tgu_delay_max')} сек.",
                           log=q("SELECT s.*, COALESCE(c.name, t.name) name FROM sms_log s LEFT JOIN clients c ON c.id=s.client_id "
                                 "LEFT JOIN tenants t ON t.id=s.tenant_id ORDER BY s.id DESC LIMIT 60"),
                           counts={r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM sms_log GROUP BY status")},
                           parts=smsmod.sms_parts(sample), sample=sample, chars=len(sample),
                           has_pw=bool(get_setting("eskiz_password")), has_pm=bool(get_setting("pm_password")))

init_db()
import sys as _sys, c1 as _c1
_c1.register(_sys.modules[__name__])
import kassa as _kassa
_kassa.register(_sys.modules[__name__])
import ready as _ready
_ready.register(_sys.modules[__name__])
import raspil as _raspil
_raspil.register(_sys.modules[__name__])
import raspil_bot as _raspil_bot
_raspil_bot.register(_sys.modules[__name__])
import report as _report
_report.register(_sys.modules[__name__])
import erp as _erp
_erp.register(_sys.modules[__name__])
import erp_reports as _erpr
_erpr.register(_sys.modules[__name__])
import partners as _partners
_partners.register(_sys.modules[__name__])
import eman as _eman
_eman.register(_sys.modules[__name__])
import catalog as _catalog
_catalog.register(_sys.modules[__name__])
import decor as _decor
_decor.register(_sys.modules[__name__])
import staff as _staff
_staff.register(_sys.modules[__name__])
import links as _links
_links.register(_sys.modules[__name__])
import roles as _roles
_roles.register(_sys.modules[__name__])
import basis as _basis
_basis.register(_sys.modules[__name__])
import docflow as _docflow
_docflow.register(_sys.modules[__name__])
import printsvc as _printsvc
_printsvc.register(_sys.modules[__name__])
import pickup as _pickup
_pickup.register(_sys.modules[__name__])
import stockimp as _stockimp
_stockimp.register(_sys.modules[__name__])
import invbrak as _invbrak
_invbrak.register(_sys.modules[__name__])
import edgebot as _edgebot
_edgebot.register(_sys.modules[__name__])
import stockbot as _stockbot
_stockbot.register(_sys.modules[__name__])
import online as _online
_online.register(_sys.modules[__name__])
import web3d as _web3d
_web3d.register(_sys.modules[__name__])
import logist as _logist
_logist.register(_sys.modules[__name__])
import i18n as _i18n
_i18n.register(_sys.modules[__name__])
import nav as _nav


@app.context_processor
def _nav_ctx():
    def NAV(sup=False):
        u_ = current_user()
        return _nav.build(u_, request.endpoint, request.args, sup=sup, kassa_allowed=kassa_allowed, kassa_can=kassa_can, limited=_roles_mod().is_limited(u_), kinds=_roles_mod().doc_kinds(u_))
    return dict(NAV=NAV)

if __name__ == "__main__":
    start_background_jobs()
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("RENT_PORT", os.environ.get("PORT", 8081))), debug=False)
