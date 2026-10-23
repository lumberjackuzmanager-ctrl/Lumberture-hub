"""Автотесты: python tests/test_app.py  (временная БД; Telethon, Bot API, SMS-провайдеры, бот курса @em_mat_info_bot и ЦБ
подменены заглушками — никаких реальных запросов в сеть)."""
import os, sys, io, json, time, asyncio, tempfile
from datetime import date, datetime, timedelta
tmp = tempfile.mkdtemp()
os.environ["RENT_DB"] = os.path.join(tmp, "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A
with A.app.app_context():                       # тестларда «cashier» — аввалгидек кенг роль (созламалардан ташқари ҳаммаси, проведен қилади)
    A.db().execute("UPDATE roles SET kind='full', sections='[]', can_post=1 WHERE key='cashier'"); A.db().commit()
    import roles as _roles_t; _roles_t.reload()
import tg, sms, tguser, rates
from telethon import errors as tlerr
from openpyxl import load_workbook

A.app.testing = True
c = A.app.test_client()
def csrf():
    with c.session_transaction() as s:
        s.setdefault("csrf", "t"); return s["csrf"]
def post(url, data=None):
    d = dict(data or {}); d["csrf"] = csrf(); return c.post(url, data=d)
def ctx():
    return A.app.app_context()

# ---- заглушка Telegram-бота (дополнительный канал)
SENT, FAIL = [], {}
def fake_send(token, chat_id, text, reply_markup=None):
    if chat_id in FAIL:
        raise FAIL[chat_id]
    SENT.append((chat_id, text)); return {"message_id": len(SENT)}
tg.send_message = fake_send
tg.get_me = lambda token: {"username": "test_arenda_bot", "first_name": "Аренда"}

# ---- заглушка Telegram-аккаунта (вместо Telethon)
class FakeSender:
    def __init__(self):
        self.calls, self.outcomes, self.auth, self.codes = [], {}, False, []
    def status(self, api_id, api_hash):
        return {"authorized": self.auth, "me": {"id": 1, "name": "Бухгалтерия", "username": "", "phone": "998900000001"} if self.auth else None}
    def send_code(self, api_id, api_hash, phone):
        self.codes.append((api_id, api_hash, phone))
    def sign_in(self, api_id, api_hash, code):
        if code != "11111":
            raise tguser.TgUserError("Неверный код")
        return "password", None
    def sign_in_password(self, api_id, api_hash, pw):
        self.auth = True
        return "ok", {"id": 1, "name": "Бухгалтерия", "username": "", "phone": "998900000001"}
    def logout(self, api_id, api_hash):
        self.auth = False
    bot_reply, bot_calls = "1$ = 12 000 UZS", []          # так отвечает @em_mat_info_bot на /rate_usd
    def ask_bot(self, api_id, api_hash, bot, text, timeout=25):
        self.bot_calls.append((bot, text))
        if isinstance(self.bot_reply, Exception):
            raise self.bot_reply
        return self.bot_reply
    def send(self, api_id, api_hash, phone, name, text, user_id=None):
        q = self.outcomes.get(phone)
        res = q.pop(0) if isinstance(q, list) and q else (q if isinstance(q, dict) else {"status": "sent", "user_id": int(phone[-7:])})
        self.calls.append(dict(phone=phone, name=name, text=text, user_id=user_id, status=res["status"]))
        return res
FS = FakeSender()
tguser.set_sender(FS)
# ---- заглушка ЦБ (резервный курс)
CBU = {"rate": 11809.19, "fail": False, "calls": 0}
def fake_cbu(timeout=15, get=None):
    CBU["calls"] += 1
    if CBU["fail"]:
        raise rates.RateError("ЦБ (cbu.uz) недоступен: тест")
    return CBU["rate"], "2026-10-08"
rates.fetch_cbu = fake_cbu
def drain(limit=50):
    """Обработать очередь Telegram-аккаунта, пропуская паузы 20–60 сек. (в тестах)."""
    with ctx():
        for _ in range(limit):
            if not A.q("SELECT 1 FROM tg_out WHERE status='pending' AND next_try<=?", (time.time(),), one=True):
                break
            A.set_settings(tgu_next_at="0")
            A.process_tg_one()
def last_out(**where):
    sql, args = "SELECT * FROM tg_out WHERE 1=1", []
    for k, v in where.items():
        sql += f" AND {k}=?"; args.append(v)
    return con.execute(sql + " ORDER BY id DESC", args).fetchone()

assert post("/login", {"username": "admin", "password": "bad"}).status_code == 200
assert post("/login", {"username": "admin", "password": "admin123"}).status_code == 302
con = A.connect()
ct = {r["code"]: r["id"] for r in con.execute("SELECT id, code FROM charge_types WHERE code IS NOT NULL")}
assert set(ct) >= {"rent", "electricity", "trash", "compressor"}
assert con.execute("SELECT kind, apply_all FROM charge_types WHERE code='compressor'").fetchone()[:] == ("fixed", 0)
# валюты по умолчанию: аренда и компрессор — $, свет и мусор — сумы
assert dict(con.execute("SELECT code, currency FROM charge_types WHERE code IS NOT NULL").fetchall()) == \
    {"rent": "USD", "compressor": "USD", "electricity": "UZS", "trash": "UZS", "oneoff": "UZS", "adjust": "UZS", "opening": "UZS"}
# курса ещё нет -> предупреждение; вводим вручную 12 000 сум/$
with ctx(), A.app.test_request_context():
    assert any("Курс доллара не загружен" in w for w, _ in A.setup_warnings())
post("/rates", {"action": "manual", "rate": "500"})                      # неправдоподобный — не сохраняется
assert not con.execute("SELECT 1 FROM fx_rates").fetchone()
post("/rates", {"action": "manual", "rate": "12 000", "rate_date": "2026-10-08"})
with ctx():
    assert A.fx_rate() == 12000 and A.fx_current()["source"] == "manual"
with ctx():
    assert A.get_setting("sms_provider") == "test" and A.client_channels() == (True, False)   # Telegram вкл., SMS выкл.
    assert A.get_setting("bot_enabled") == "0" and not A.tgu_ready()                          # бот выключен, аккаунт не подключён

# ---- виды сборов: ставка аренды за м² (история), тариф на свет, мусор, новые виды
post("/admin/charge-types", {"action": "tariff", "id": ct["rent"], "tariff": "5", "valid_from": "2026-01-01"})        # $5 за м²
post("/admin/charge-types", {"action": "tariff", "id": ct["rent"], "tariff": "5,5", "valid_from": "2026-11-01"})
post("/admin/charge-types", {"action": "save", "id": ct["trash"], "name": "Мусор", "kind": "fixed", "default_amount": "50 000", "apply_all": "on", "active": "on", "sort": 2})
post("/admin/charge-types", {"action": "tariff", "id": ct["electricity"], "tariff": "1000", "valid_from": "2026-01-01"})
post("/admin/charge-types", {"action": "tariff", "id": ct["electricity"], "tariff": "1100", "valid_from": "2026-10-01"})
post("/admin/charge-types", {"action": "save", "name": "Охрана", "kind": "area", "default_amount": "5000", "apply_all": "on", "active": "on", "sort": 5})
post("/admin/charge-types", {"action": "save", "name": "Интернет", "kind": "fixed", "default_amount": "100000", "active": "on", "sort": 6})  # не всем
guard = con.execute("SELECT id FROM charge_types WHERE name='Охрана'").fetchone()[0]
inet = con.execute("SELECT id FROM charge_types WHERE name='Интернет'").fetchone()[0]
comp = ct["compressor"]

# ---- арендаторы
def add_tenant(name, unit, area, phone, cat, extra=None):
    d = {"name": name, "unit": unit, "area": area, "phone": phone, "category": cat, "contract_start": "2026-01-01", "active": "on",
         f"fx_present_{ct['trash']}": 1, f"fx_en_{ct['trash']}": "on", f"fx_present_{guard}": 1, f"fx_en_{guard}": "on",
         f"fx_present_{inet}": 1, f"fx_present_{comp}": 1,
         f"m_present_{ct['electricity']}": 1, f"m_en_{ct['electricity']}": "on", f"m_init_{ct['electricity']}": "1000", "remind_enabled": "on"}
    d.update(extra or {})
    assert post("/tenants/new", d).status_code == 302
    return con.execute("SELECT id FROM tenants WHERE name=?", (name,)).fetchone()[0]
T1 = add_tenant("Мебельщик Алишер", "Цех 1", 100, "+998 90 123-45-67", "Мебельщик",
                {f"fx_en_{inet}": "on", f"fx_amt_{inet}": "80000", f"fx_en_{comp}": "on", f"fx_amt_{comp}": "30"})   # компрессор $30
T2 = add_tenant("Маляр Бобур", "Покраска", 40, "", "Маляр",
                {"rent_rate": "4.5", f"fx_amt_{ct['trash']}": "80000", f"fx_en_{guard}": ""})   # своя ставка $4.5, свой мусор, без охраны
T3 = add_tenant("Склад", "Склад", 30, "", "Другое", {"rent_amount": "100", "active": ""})   # фикс. аренда $100, неактивен
with ctx():
    t1, t2, t3 = A.get_tenant(T1), A.get_tenant(T2), A.get_tenant(T3)
    assert A.rent_amount(t1, "2026-09") == 500 and A.rent_amount(t1, "2026-11") == 550
    assert A.rent_amount(t2, "2026-11") == 180 and A.rent_amount(t3, "2026-11") == 100
    assert A.monthly_totals(t1, "2026-09") == (500 + 30, 50_000 + 100 * 5000 + 80_000)          # ($, сум)
    assert A.monthly_totals(t2, "2026-09") == (180, 80_000)
    assert A.monthly_fixed_total(t1, "2026-09") == 530 + 630_000 / 12000                     # в $ по курсу

# ---- счета за сентябрь (неактивный не получает)
post("/billing", {"action": "generate", "period": "2026-09"})
assert con.execute("SELECT COUNT(*) FROM invoices WHERE period='2026-09'").fetchone()[0] == 2
post("/billing", {"action": "generate", "period": "2026-09"})   # без дублей
assert con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 2
r = con.execute("SELECT description, currency, amount, fx_rate FROM charges WHERE tenant_id=? AND charge_type_id=?", (T1, ct["rent"])).fetchone()
assert r[:] == ("Аренда за сентябрь 2026: 100 м² × $5", "USD", 500, 12000), r[:]
assert con.execute("SELECT currency FROM charges WHERE tenant_id=? AND charge_type_id=?", (T1, ct["trash"])).fetchone()[0] == "UZS"
with ctx():
    assert A.tenant_pockets(T1) == (530, 630_000) and A.tenant_balance(T1) == 582.5            # 530 + 630 000 / 12 000
    assert A.tenant_pockets(T2) == (180, 80_000) and A.tenant_balance(T2) == 186.67

# ---- показания: сентябрь 1000 -> 1250 = 250 × 1000; октябрь — тариф 1100
post("/readings", {"period": "2026-09", "ct": ct["electricity"], "reading_date": "2026-10-01", f"cur_{T1}": "1250", f"cur_{T2}": "999"})
r = con.execute("SELECT * FROM meter_readings WHERE tenant_id=?", (T1,)).fetchone()
assert (r["prev_value"], r["consumption"], r["tariff"], r["amount"]) == (1000, 250, 1000, 250000), dict(r)
assert not con.execute("SELECT 1 FROM meter_readings WHERE tenant_id=?", (T2,)).fetchone()   # меньше начального — отклонено
post("/readings", {"period": "2026-10", "ct": ct["electricity"], "reading_date": "2026-11-01", f"cur_{T1}": "1400"})
r10 = con.execute("SELECT * FROM meter_readings WHERE tenant_id=? AND period='2026-10'", (T1,)).fetchone()
assert (r10["prev_value"], r10["consumption"], r10["tariff"], r10["amount"]) == (1250, 150, 1100, 165000), dict(r10)
post("/readings", {"period": "2026-09", "ct": ct["electricity"], "reading_date": "2026-10-01", f"cur_{T1}": "1300"})   # исправили сентябрь
r10 = con.execute("SELECT * FROM meter_readings WHERE tenant_id=? AND period='2026-10'", (T1,)).fetchone()
assert (r10["prev_value"], r10["consumption"], r10["amount"]) == (1300, 100, 110000), dict(r10)
assert con.execute("SELECT amount FROM charges WHERE id=?", (r10["charge_id"],)).fetchone()[0] == 110000
assert con.execute("SELECT invoice_id IS NOT NULL FROM charges WHERE source='meter' AND period='2026-09' AND tenant_id=?", (T1,)).fetchone()[0] == 1
with ctx():
    assert A.tenant_pockets(T1) == (530, 630_000 + 300_000 + 110_000)
    # итог в $ плавает только на сумовую часть: курс 13 000 -> меньше в $, долларовая часть та же
    assert A.tenant_balance(T1) == 616.67
    A.fx_store(13000, "manual")
    assert A.tenant_balance(T1) == 610 and A.tenant_pockets(T1)[0] == 530
    A.fx_store(12000, "manual")


# ================================================================ Telegram-аккаунт (MTProto)
T1_PHONE = "998901234567"
# аккаунт ещё не подключён -> сообщение не уходит, но пишется в журнал
post(f"/tenants/{T1}/send-balance")
r = last_out(tenant_id=T1)
assert r["status"] == "skipped" and "не подключён" in r["error"] and not FS.calls, dict(r)
# вход через интерфейс: api_id/api_hash/телефон -> код -> облачный пароль
h = c.get("/admin/settings").get_data(as_text=True)
assert 'name="tgu_enabled" checked' in h and 'name="tgu_delay_min" value="20"' in h and 'name="tgu_daily_cap" value="50"' in h
post("/admin/settings", {"action": "tgu_save", "api_id": "12x", "api_hash": "zz", "phone": ""})
with ctx():
    assert A.get_setting("tgu_api_id") == ""                                       # неверный api_id не сохранён
post("/admin/settings", {"action": "tgu_save", "tgu_enabled": "on", "api_id": "1234567", "api_hash": "0123456789abcdef0123456789abcdef",
                         "phone": "+998 90 000 00 01", "tgu_delay_min": "20", "tgu_delay_max": "60", "tgu_daily_cap": "50",
                         "tgu_hour_from": "0", "tgu_hour_to": "24", "tgu_expire_days": "3"})
post("/admin/settings", {"action": "tgu_code"})
assert FS.codes == [("1234567", "0123456789abcdef0123456789abcdef", "+998900000001")], FS.codes
post("/admin/settings", {"action": "tgu_signin", "code": "00000"})
with ctx():
    assert A.get_setting("tgu_authorized") == "0"
post("/admin/settings", {"action": "tgu_signin", "code": "11111"})
with ctx():
    assert A.get_setting("tgu_need_password") == "1"
post("/admin/settings", {"action": "tgu_password", "password": "secret"})
with ctx():
    assert A.tgu_ready() and json.loads(A.get_setting("tgu_me"))["name"] == "Бухгалтерия"
    assert (A.get_setting("tgu_delay_min"), A.get_setting("tgu_delay_max"), A.get_setting("tgu_daily_cap")) == ("20", "60", "50")
assert "Бухгалтерия" in c.get("/admin/settings").get_data(as_text=True)

# оплата в сумах гасит сумовый долг -> «Получена оплата X сум. Остаток задолженности: $Y (≈ Z сум по курсу …)»
TODAY = date.today().strftime("%d.%m.%Y")
post(f"/tenants/{T1}/payment", {"amount": "1 040 000", "currency": "UZS", "pay_date": "2026-10-05", "method": "cash", "notify": "on"})
r = last_out(tenant_id=T1, kind="payment")
assert r["status"] == "pending" and r["phone"] == T1_PHONE and not FS.calls       # только очередь, без сети в запросе
drain()
assert FS.calls[-1]["phone"] == T1_PHONE and FS.calls[-1]["user_id"] is None
# коротко и только в $: оплата в сумах пересчитана по курсу дня оплаты (1 040 000 / 12 000 = $86.67)
CO = con.execute("SELECT value FROM settings WHERE key='company_name'").fetchone()
CO = CO[0] if CO else A.DEFAULT_SETTINGS["company_name"]
assert FS.calls[-1]["text"] == f"Здравствуйте, Мебельщик Алишер! Получена оплата: $86.67. Остаток задолженности: $530. {CO}", FS.calls[-1]["text"]
assert "сум" not in FS.calls[-1]["text"] and "курс" not in FS.calls[-1]["text"]
assert last_out(tenant_id=T1, kind="payment")["status"] == "sent"
p_ = con.execute("SELECT currency, amount, fx_rate, usd_part, uzs_part FROM payments ORDER BY id DESC").fetchone()
assert p_[:] == ("UZS", 1_040_000, 12000, 0, 1_040_000), p_[:]
t1 = con.execute("SELECT * FROM tenants WHERE id=?", (T1,)).fetchone()
assert t1["tg_status"] == "ok" and t1["tg_user_id"] == 1234567
# оплата в $ больше долга -> переплата в $
post(f"/tenants/{T1}/payment", {"amount": "700", "currency": "USD", "pay_date": "2026-10-06", "method": "transfer", "notify": "on"})
drain()
assert FS.calls[-1]["text"] == f"Здравствуйте, Мебельщик Алишер! Получена оплата: $700. На вашем счёте: $170. {CO}", FS.calls[-1]["text"]
assert FS.calls[-1]["user_id"] == 1234567   # повторно без импорта
with ctx():
    assert A.tenant_pockets(T1) == (-170, 0)
# оплата в сумах больше сумового долга: остаток пересчитывается в $ по курсу дня оплаты и гасит долларовый долг
post(f"/tenants/{T2}/payment", {"amount": "1000000", "currency": "UZS", "pay_date": "2026-10-06", "method": "card", "notify": "on"})
r = last_out(tenant_id=T2)
assert r["status"] == "skipped" and "нет номера" in r["error"], dict(r)
p_ = con.execute("SELECT usd_part, uzs_part FROM payments ORDER BY id DESC").fetchone()
assert p_[1] == 80_000 and abs(p_[0] - 920_000 / 12000) < 1e-3, p_[:]
with ctx():
    assert A.tenant_pockets(T2) == (round(180 - 920_000 / 12000, 4), 0) and A.tenant_balance(T2) == 103.33
    A.fx_store(15000, "manual")
    assert A.tenant_balance(T2) == 103.33                     # долг только в $ — от курса не зависит
    A.fx_store(12000, "manual")
    assert "1 000 000 сум (из них $76.67 по курсу 12 000)" in A.pay_text(A.q("SELECT * FROM payments ORDER BY id DESC", one=True))
post(f"/tenants/{T2}/payment", {"amount": "0"})
assert con.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 3
assert "Сумма оплаты должна быть больше 0" in c.get(f"/tenants/{T2}").get_data(as_text=True)
# шаблон редактируется
html = c.get("/admin/settings").get_data(as_text=True)
assert 'name="msg_payment_uz"' in html and 'name="default_lang"' in html and "To&#39;lov qabul qilindi" in html
post("/admin/settings", {"action": "templates", "msg_payment_ru": "Оплата {amount}! {balance_text}", **{k: "" for k in A.DEFAULT_TEMPLATES if k != "msg_payment_ru"}})
with ctx():
    assert A.get_setting("msg_payment_ru") == "Оплата {amount}! {balance_text}" and A.get_setting("msg_debt_uz") == A.DEFAULT_TEMPLATES["msg_debt_uz"]
    t1 = A.q("SELECT * FROM tenants WHERE id=?", (T1,), one=True)
    assert A.notif_text("payment", t1, 0, 10) == "Оплата $10! Задолженности нет. Спасибо!"
post("/admin/settings", {"action": "template_reset"})
with ctx():
    assert A.get_setting("msg_payment_ru") == A.DEFAULT_TEMPLATES["msg_payment_ru"]

# ---- ограничения: пауза 20–60 сек., дневной лимит, часы, FloodWait, PEER_FLOOD, слетевшая сессия, сеть, устаревание
with ctx():
    gap = float(A.get_setting("tgu_next_at")) - time.time()
    assert 15 <= gap <= 60, gap                                                  # после отправки — случайная пауза
n_calls = len(FS.calls)
post(f"/tenants/{T1}/send-balance")
with ctx():
    w = A.process_tg_one()
    assert len(FS.calls) == n_calls and 10 <= w <= 60 and last_out(tenant_id=T1)["status"] == "pending"   # ждёт паузу
    # таниш мижозга (аввал хабар кетган) лимит қўлланмайди — синов учун рақамни «янги» қиламиз
    A.db().execute("UPDATE tg_out SET phone=phone||'#' WHERE phone=? AND status='sent'", (T1_PHONE,)); A.db().commit()
    A.set_settings(tgu_next_at="0", tgu_daily_cap=str(A.tgu_tried_today()))
    A.process_tg_one()
    assert len(FS.calls) == n_calls and "дневной лимит" in A.tgu_queue_info()["why"]                         # лимит на сегодня
    A.db().execute("UPDATE tg_out SET phone=? WHERE phone=?", (T1_PHONE, T1_PHONE + "#")); A.db().commit()
    A.set_settings(tgu_daily_cap="50", tgu_hour_from="8", tgu_hour_to="21")
    A.process_tg_one(datetime.now().replace(hour=23, minute=0))
    assert len(FS.calls) == n_calls                                                                          # ночью не шлём
    A.set_settings(tgu_hour_from="0", tgu_hour_to="24")
FS.outcomes[T1_PHONE] = [{"status": "flood", "wait": 120, "error": "FloodWait 120"}]
drain(1)
with ctx():
    assert last_out(tenant_id=T1)["status"] == "pending" and float(A.get_setting("tgu_flood_until")) > time.time() + 100
    A.set_settings(tgu_next_at="0"); A.process_tg_one()
    assert len(FS.calls) == n_calls + 1 and "пауза по требованию Telegram" in A.tgu_queue_info()["why"]     # ждём FloodWait
    A.set_settings(tgu_flood_until="0")
drain()
assert last_out(tenant_id=T1)["status"] == "sent" and len(FS.calls) == n_calls + 2
FS.outcomes[T1_PHONE] = [{"status": "peer_flood", "error": "PEER_FLOOD"}]
post(f"/tenants/{T1}/send-balance"); drain(1)
with ctx():
    assert float(A.get_setting("tgu_flood_until")) > time.time() + 23 * 3600
    with A.app.test_request_context():
        assert any("PEER_FLOOD" in w for w, _ in A.setup_warnings())
post("/admin/settings", {"action": "tgu_resume"})
FS.outcomes[T1_PHONE] = [{"status": "auth", "error": "сессия"}]
drain(1)
with ctx():
    assert not A.tgu_ready() and last_out(tenant_id=T1)["status"] == "pending"                       # сообщение не потеряно
    with A.app.test_request_context():
        assert any("не подключён" in w for w, _ in A.setup_warnings())
post("/admin/settings", {"action": "tgu_check"})                                                      # сессия снова валидна
FS.outcomes[T1_PHONE] = [{"status": "retry", "error": "Нет связи"}]
drain(1)
r = last_out(tenant_id=T1)
assert r["status"] == "pending" and r["next_try"] > time.time() and r["attempts"] >= 1
con.execute("UPDATE tg_out SET next_try=0"); con.commit()
drain()
assert last_out(tenant_id=T1)["status"] == "sent"
con.execute("INSERT INTO tg_out(tenant_id,kind,phone,name,text,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (T1, "reminder", T1_PHONE, "x", "старое", "pending", (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")))
con.commit(); drain()
assert last_out(tenant_id=T1, text="старое")["status"] == "expired"
assert not any(x["text"] == "старое" for x in FS.calls)

# ---- нет Telegram / приватность -> отметка и SMS-резерв (если включён)
T4 = add_tenant("Без Телеграма", "Цех 9", 10, "+998 97 000 00 07", "Другое", {"active": ""})
T5 = add_tenant("Приватный", "Цех 8", 10, "+998 97 000 00 03", "Другое", {"active": ""})
FS.outcomes["998970000007"] = {"status": "no_tg", "error": "Номер не зарегистрирован в Telegram"}
FS.outcomes["998970000003"] = {"status": "privacy", "error": "Только контакты"}
post(f"/tenants/{T4}/send-balance"); drain()
assert last_out(tenant_id=T4)["status"] == "no_tg" and con.execute("SELECT tg_status FROM tenants WHERE id=?", (T4,)).fetchone()[0] == "none"
assert con.execute("SELECT COUNT(*) FROM sms_log").fetchone()[0] == 0                         # SMS выключены
n_calls = len(FS.calls)
post(f"/tenants/{T4}/send-balance")
assert last_out(tenant_id=T4)["status"] == "no_tg" and len(FS.calls) == n_calls              # повторно номер не дёргаем
assert "нет Telegram" in c.get(f"/tenants/{T4}").get_data(as_text=True)
with ctx():
    A.set_settings(sms_channel="1")
post(f"/tenants/{T4}/send-balance")
s = con.execute("SELECT * FROM sms_log ORDER BY id DESC").fetchone()
assert s["tenant_id"] == T4 and s["status"] == "pending" and "нет Telegram" in s["response"], dict(s)
post(f"/tenants/{T5}/send-balance"); drain()                                                   # приватность -> резерв SMS из фонового потока
assert last_out(tenant_id=T5)["status"] == "privacy" and "SMS" in last_out(tenant_id=T5)["error"]
assert con.execute("SELECT tenant_id FROM sms_log ORDER BY id DESC").fetchone()[0] == T5
with ctx():
    A.process_sms_outbox()
assert con.execute("SELECT COUNT(*) FROM sms_log WHERE status='test'").fetchone()[0] == 2      # тестовый режим — только журнал
post(f"/tenants/{T4}/tg", {"action": "recheck"})
assert con.execute("SELECT tg_status FROM tenants WHERE id=?", (T4,)).fetchone()[0] == ""
with ctx():
    A.set_settings(sms_channel="0")
# единый журнал
html = c.get("/messages").get_data(as_text=True)
assert "нет Telegram" in html and "Без Телеграма" in html and "закрыто приватностью" in html
assert "Без Телеграма" in c.get("/messages?channel=sms").get_data(as_text=True)

# ---- Telethon: разбор ответов (поддельный клиент Telethon)
class FakeTL:
    def __init__(self, users=True, raise_on_send=None, retry=False):
        self.users, self.raise_on_send, self.retry, self.sent, self.imported = users, raise_on_send, retry, [], []
    async def is_user_authorized(self): return True
    async def get_input_entity(self, uid):
        if uid == 42: return "ENTITY42"
        raise ValueError("not cached")
    async def __call__(self, req):
        self.imported.append(req.contacts[0])
        class U: id = 777
        class Res: pass
        r = Res(); r.users = [U()] if self.users else []; r.retry_contacts = [1] if self.retry else []
        return r
    async def send_message(self, entity, text):
        if self.raise_on_send: raise self.raise_on_send
        self.sent.append((entity, text))
f = FakeTL()
_r = asyncio.run(tguser.send_via(f, "998901112233", "Азиз", "Текст")); assert _r["status"] == "sent" and _r["user_id"] == 777, _r
assert f.imported[0].phone == "+998901112233" and f.imported[0].first_name == "Азиз" and f.sent[0][1] == "Текст"
f = FakeTL()
assert asyncio.run(tguser.send_via(f, "998901112233", "Азиз", "Т", user_id=42))["status"] == "sent" and not f.imported and f.sent[0][0] == "ENTITY42"
assert asyncio.run(tguser.send_via(FakeTL(users=False), "998900000007", "", "Т"))["status"] == "no_tg"
assert asyncio.run(tguser.send_via(FakeTL(users=False, retry=True), "998900000007", "", "Т"))["status"] == "flood"
r = asyncio.run(tguser.send_via(FakeTL(raise_on_send=tlerr.FloodWaitError(request=None, capture=33)), "998901112233", "", "Т"))
assert r["status"] == "flood" and r["wait"] == 33
assert asyncio.run(tguser.send_via(FakeTL(raise_on_send=tlerr.PeerFloodError(request=None)), "998901112233", "", "Т"))["status"] == "peer_flood"
assert asyncio.run(tguser.send_via(FakeTL(raise_on_send=tlerr.UserPrivacyRestrictedError(request=None)), "998901112233", "", "Т"))["status"] == "privacy"
assert asyncio.run(tguser.send_via(FakeTL(raise_on_send=tlerr.AuthKeyUnregisteredError(request=None)), "998901112233", "", "Т"))["status"] == "auth"
assert asyncio.run(tguser.send_via(FakeTL(raise_on_send=ConnectionError()), "998901112233", "", "Т"))["status"] == "retry"
# настоящий класс Sender (поток + asyncio) с поддельным TelegramClient: код -> пароль 2FA -> вход -> выход
import telethon
class FakeClient:
    inst = []
    def __init__(self, session, api_id, api_hash, **kw):
        self.session, self.api, self.conn, self.auth = session, (api_id, api_hash), False, False; FakeClient.inst.append(self)
    def is_connected(self): return self.conn
    async def connect(self): self.conn = True
    async def disconnect(self): self.conn = False
    async def is_user_authorized(self): return self.auth
    async def send_code_request(self, phone):
        class S: phone_code_hash = "HASH"
        self.phone = phone; return S()
    async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
        if password is None:
            assert phone_code_hash == "HASH"
            if code != "22222": raise tlerr.PhoneCodeInvalidError(request=None)
            raise tlerr.SessionPasswordNeededError(request=None)
        self.auth = True; return await self.get_me()
    async def get_me(self):
        class U: id, first_name, last_name, username, phone = 5, "Бух", "галтерия", "", "998900000001"
        return U()
    async def log_out(self): self.auth = False
orig_tc = telethon.TelegramClient
telethon.TelegramClient = FakeClient
try:
    snd = tguser.Sender(os.path.join(tmp, "tg_user"))
    try:
        snd.sign_in("1", "h", "1")
        raise AssertionError("должна быть ошибка: код не запрошен")
    except tguser.TgUserError:
        pass
    snd.send_code("1", "h", "+998900000001")
    try:
        snd.sign_in("1", "h", "00000"); raise AssertionError
    except tguser.TgUserError as e:
        assert "Неверный код" in str(e)
    assert snd.sign_in("1", "h", "22222") == ("password", None)
    st_, me = snd.sign_in_password("1", "h", "pw")
    assert st_ == "ok" and me["name"] == "Бух галтерия" and snd.status("1", "h")["authorized"]
    assert FakeClient.inst[0].session.endswith("tg_user") and FakeClient.inst[0].api == (1, "h")
    snd.logout("1", "h")
finally:
    telethon.TelegramClient = orig_tc

# ================================================================ КУРС ДОЛЛАРА: бот @em_mat_info_bot -> ЦБ -> вручную
# разбор ответа бота (реальный формат: «1$ = 11 840 UZS») и возможных вариантов с покупкой/продажей
assert rates.parse_rate("1$ = 11 840 UZS") == 11840
assert rates.parse_rate("08.10.2026\n1$ = 11 840 UZS") == 11840
assert rates.parse_rate("USD покупка 12 650 / продажа 12 700") == 12700
assert rates.parse_rate("Курс USD\nПокупка: 12650\nПродажа: 12700", "buy") == 12650
assert rates.parse_rate("Покупка: 12 650  Продажа: 12 700 (USD)", "avg") == 12675
assert rates.parse_rate("💶 EUR: 13 900\n💵 USD: 12 680,50 сум") == 12680.5
assert rates.parse_rate("Не понял команду") is None and rates.parse_rate("") is None
# ЦБ: разбор JSON cbu.uz (заглушка HTTP)
class _R:
    def __init__(self, js): self._js = js
    def json(self): return self._js
# исходная fetch_cbu из файла (в модуле она подменена заглушкой):
_src = {}; exec(compile(open(rates.__file__, encoding="utf-8").read(), rates.__file__, "exec"), _src)
assert _src["fetch_cbu"](get=lambda url, timeout=None: _R([{"Ccy": "USD", "Rate": "11809.19", "Date": "08.10.2026"}])) == (11809.19, "2026-10-08")
try:
    _src["fetch_cbu"](get=lambda url, timeout=None: _R([{"Ccy": "USD", "Rate": "1.5", "Date": "08.10.2026"}])); raise AssertionError
except _src["RateError"]:
    pass
# Telethon: отправка команды боту и чтение ответа (поддельный клиент)
class _Msg:
    def __init__(self, id, out, text): self.id, self.out, self.raw_text = id, out, text
class FakeBotTL:
    def __init__(self, replies): self.replies, self.sent = replies, []
    async def get_input_entity(self, name): return "BOT:" + name
    async def send_message(self, entity, text):
        self.sent.append((entity, text)); return _Msg(100, True, text)
    async def get_messages(self, entity, min_id=0, limit=10):
        return [_Msg(101 + i, False, t) for i, t in enumerate(self.replies)][::-1] + [_Msg(100, True, "/rate_usd")]
fb = FakeBotTL(["1$ = 11 840 UZS"])
assert asyncio.run(tguser.ask_bot_via(fb, "@em_mat_info_bot", "/rate_usd", timeout=3, poll=0.01)) == "1$ = 11 840 UZS"
assert fb.sent == [("BOT:em_mat_info_bot", "/rate_usd")]
try:
    asyncio.run(tguser.ask_bot_via(FakeBotTL([]), "em_mat_info_bot", "/rate_usd", timeout=0.05, poll=0.01)); raise AssertionError
except tguser.TgUserError as e:
    assert "не ответил" in str(e)
# обновление курса по кнопке: бот (через Telegram-аккаунт платформы)
FS.bot_reply = "1$ = 11 840 UZS"
post("/rates", {"action": "refresh", "back": "/"})
with ctx():
    fx = A.fx_current()
    assert (fx["rate"], fx["source"]) == (11840, "bot") and "1$ = 11 840 UZS" in fx["raw"] and FS.bot_calls[-1] == ("em_mat_info_bot", "/rate_usd")
    assert A.get_setting("fx_bot_raw") == "1$ = 11 840 UZS" and A.get_setting("fx_last_error") == ""
h = c.get("/").get_data(as_text=True)
assert "11 840" in h and "Telegram-бот" in h and "Обновить курс" in h                              # курс и источник на главной
# по умолчанию ЦБ выключен: бот не ответил -> курс остаётся (вчерашний курс бота)
FS.bot_reply = "Неизвестная команда"
with ctx():
    assert A.get_setting("fx_cbu") == "0"
    row, errs = A.fx_fetch(); assert row is None and A.fx_rate() == 11840
    A.set_settings(fx_cbu="1")      # резерв ЦБ можно включить (только для справки)
# бот ответил непонятно -> резерв ЦБ
with ctx():
    row, errs = A.fx_fetch()
    assert row["source"] == "cbu" and row["rate"] == 11809.19 and "не распознан" in errs[0]
# бот недоступен и ЦБ недоступен -> курс не меняется, ошибка видна
FS.bot_reply = tguser.TgUserError("Бот @em_mat_info_bot не ответил за 20 сек.")
CBU["fail"] = True
post("/rates", {"action": "refresh"})
with ctx():
    assert A.fx_current()["source"] == "cbu" and "не ответил" in A.get_setting("fx_last_error") and "ЦБ" in A.get_setting("fx_last_error")
CBU["fail"] = False
# аккаунт не подключён -> сразу ЦБ, бот не дёргаем
with ctx():
    A.set_settings(tgu_authorized="0"); n_b = len(FS.bot_calls)
    row, errs = A.fx_fetch()
    assert row["source"] == "cbu" and len(FS.bot_calls) == n_b and "не подключён" in errs[0]
    A.set_settings(tgu_authorized="1")
# расписание: ежедневно в 9:00, при неудаче — не чаще раза в час; без курса — сразу
with ctx():
    A.set_settings(fx_auto_date="2026-10-08", fx_last_try_at="2026-10-08 09:00:00")
    assert not A.fx_due(datetime(2026, 10, 9, 8, 59)) and A.fx_due(datetime(2026, 10, 9, 9, 0))
    A.set_settings(fx_last_try_at="2026-10-09 09:00:00")
    assert not A.fx_due(datetime(2026, 10, 9, 9, 10)) and A.fx_due(datetime(2026, 10, 9, 9, 15))    # бугунги курс йўқ — ҳар 15 дақиқада қайта
    A.set_settings(fx_auto="0"); assert not A.fx_due(datetime(2026, 10, 9, 10, 1)); A.set_settings(fx_auto="1")
    FS.bot_reply = "1$ = 12 000 UZS"
    assert "rate" in A.scheduler_tick(datetime(2026, 10, 9, 10, 5))
    assert A.fx_current()["source"] == "bot" and A.fx_rate() == 12000 and A.get_setting("fx_auto_date") == "2026-10-09"
    # кун давомида ҳар 30 дақиқада текширади (курс ўзгариши мумкин), 19:00 гача
    assert "rate" not in A.scheduler_tick(datetime(2026, 10, 9, 10, 20))
    n0 = A.scalar("SELECT COUNT(*) FROM fx_rates")
    assert "rate" in A.scheduler_tick(datetime(2026, 10, 9, 10, 40))
    assert A.scalar("SELECT COUNT(*) FROM fx_rates") == n0                     # курс ўзгармади — янги ёзув йўқ
    FS.bot_reply = "1$ = 12 050 UZS"
    assert "rate" in A.scheduler_tick(datetime(2026, 10, 9, 11, 15))
    assert A.fx_rate() == 12050 and A.scalar("SELECT COUNT(*) FROM fx_rates") == n0 + 1
    A.set_settings(fx_last_try_at="2026-10-09 18:40:00")
    assert not A.fx_due(datetime(2026, 10, 9, 19, 30))                          # кечқурун текширмайди
    A.set_settings(fx_auto="0")                                                  # кейинги тестлар курсга боғлиқ эмас
    A.db().execute("DELETE FROM fx_rates WHERE rate=12050"); A.db().commit(); A.g.pop("fx", None)   # кейинги тестлар 12 000 курсида
# ручной ввод — только админ; история курса
h = c.get("/rates").get_data(as_text=True)
assert "История курса" in h and "ЦБ Узбекистана" in h and "Telegram-бот" in h and "1$ = 11 840 UZS" in h
post("/rates", {"action": "settings", "fx_bot": "@em_mat_info_bot", "fx_cmd": "/rate_usd", "fx_pick": "avg", "fx_auto": "on", "fx_cbu": "on", "fx_hour": "9"})
with ctx():
    assert A.get_setting("fx_pick") == "avg" and A.get_setting("fx_bot") == "em_mat_info_bot"
    assert A.get_setting("fx_every") == "30" and A.get_setting("fx_until") == "19"
    A.set_settings(fx_pick="sell", fx_auto="0")

# ---- автосчета 1-го числа (не раньше часа автосчетов) -> счёт в Telegram; напоминания Пн/Чт
with ctx():
    A.set_settings(sms_auto="0")
    assert A.scheduler_tick(datetime(2026, 11, 1, 7, 0)) == []
    assert "billing" in A.scheduler_tick(datetime(2026, 11, 1, 9, 5))
    assert A.scheduler_tick(datetime(2026, 11, 1, 12, 0)) == []
drain()
assert con.execute("SELECT COUNT(*) FROM invoices WHERE period='2026-11'").fetchone()[0] == 2
# счёт — коротко: приветствие + общий долг в $ (без строк начислений, сумов и курса)
with ctx():
    b1 = A.tenant_balance(T1)
    exp = f"Здравствуйте, Мебельщик Алишер! Ваша задолженность на {TODAY}: {A.usd(b1)}. {CO}" if b1 > 0 else None
    assert b1 > 0 and A.notif_text("invoice", A.q("SELECT * FROM tenants WHERE id=?", (T1,), one=True), b1) == exp, b1
inv = [x["text"] for x in FS.calls if x["phone"] == T1_PHONE and "Ваша задолженность" in x["text"]]
assert inv and inv[-1] == exp, inv
assert "сум" not in inv[-1] and "×" not in inv[-1] and "курс" not in inv[-1] and "\n" not in inv[-1]
assert con.execute("SELECT notified_at IS NOT NULL FROM invoices WHERE tenant_id=? AND period='2026-11'", (T1,)).fetchone()[0] == 1
with ctx():
    A.set_settings(sms_auto="1", auto_billing="0")
    assert A.sms_days() == [0, 3] and A.sms_time() == (10, 0)          # по умолчанию Пн и Чт 10:00
    assert not A.client_sms_due(datetime(2026, 11, 5, 9, 59))
    assert "reminders" in A.scheduler_tick(datetime(2026, 11, 5, 10, 0))   # четверг
    assert A.scheduler_tick(datetime(2026, 11, 5, 15, 0)) == []            # раз в день
drain()
assert any(x["text"].startswith("Здравствуйте, Мебельщик Алишер! Ваша задолженность на ") and x["phone"] == T1_PHONE for x in FS.calls[-5:])

# ---- отключение вида сбора, назначение со страницы вида, смена способа расчёта «Компрессора»
post("/admin/charge-types", {"action": "save", "id": inet, "name": "Интернет", "kind": "fixed", "default_amount": "100000", "sort": 6})
with ctx():
    assert A.monthly_totals(A.get_tenant(T1), "2026-10") == (530, 550_000)
post("/admin/charge-types", {"action": "assign", "id": guard, f"present_{T1}": 1, f"en_{T1}": "on", f"present_{T2}": 1, f"en_{T2}": "on", f"amt_{T2}": "3000"})
with ctx():
    assert A.monthly_totals(A.get_tenant(T2), "2026-10") == (180, 80_000 + 120_000)
post("/admin/charge-types", {"action": "save", "id": comp, "name": "Компрессор", "kind": "area", "default_amount": "2", "active": "on", "sort": 3, "currency": "USD"})
with ctx():
    assert A.q("SELECT kind FROM charge_types WHERE id=?", (comp,), one=True)["kind"] == "area"
    # у T1 осталась своя «ставка» 300000 -> теперь за м²; вернём фикс
post("/admin/charge-types", {"action": "save", "id": comp, "name": "Компрессор", "kind": "fixed", "default_amount": "0", "active": "on", "sort": 3, "currency": "USD"})
assert con.execute("SELECT currency FROM charge_types WHERE id=?", (comp,)).fetchone()[0] == "USD"
# новый вид сбора в $ (админ выбирает валюту)
post("/admin/charge-types", {"action": "save", "name": "Вода", "kind": "fixed", "default_amount": "3", "active": "on", "sort": 7, "currency": "USD"})
assert con.execute("SELECT currency, default_amount FROM charge_types WHERE name='Вода'").fetchone()[:] == ("USD", 3)
assert "$3" in c.get("/admin/charge-types").get_data(as_text=True)
post("/admin/charge-types", {"action": "delete", "id": guard})        # есть начисления — нельзя
assert con.execute("SELECT 1 FROM charge_types WHERE id=?", (guard,)).fetchone()

# ---- разовое начисление, скидка, удаление начисления света
post(f"/tenants/{T2}/charge", {"charge_type_id": ct["oneoff"], "amount": "150000", "charge_date": "2026-10-10", "description": "Вывоз крупного мусора"})
post(f"/tenants/{T2}/charge", {"charge_type_id": ct["adjust"], "amount": "-30000", "charge_date": "2026-10-10"})
assert con.execute("SELECT SUM(amount) FROM charges WHERE tenant_id=? AND source='manual'", (T2,)).fetchone()[0] == 120000
assert con.execute("SELECT DISTINCT currency FROM charges WHERE tenant_id=? AND source='manual'", (T2,)).fetchall()[0][0] == "UZS"
post(f"/tenants/{T2}/charge", {"charge_type_id": ct["oneoff"], "amount": "20", "currency": "USD", "charge_date": "2026-10-11", "description": "Ремонт ворот"})
assert con.execute("SELECT currency, amount FROM charges WHERE description='Ремонт ворот'").fetchone()[:] == ("USD", 20)
cid10 = con.execute("SELECT charge_id FROM meter_readings WHERE tenant_id=? AND period='2026-10'", (T1,)).fetchone()[0]
post(f"/charges/{cid10}/delete")
assert not con.execute("SELECT 1 FROM meter_readings WHERE tenant_id=? AND period='2026-10'", (T1,)).fetchone()

# ---- выписка и отчёт
with ctx():
    led = A.ledger(T1, date(2026, 10, 1), date(2026, 10, 31))
    assert led["opening"] == 582.5 and led["opening_p"] == (530, 630_000)
    assert led["closing_p"] == A.tenant_pockets(T1, "2026-11-01") and led["closing"] == A.to_usd(*led["closing_p"])
    assert led["paid"] == {"USD": 700, "UZS": 1_040_000}
    pr = [r for r in led["rows"] if r["kind"] == "payment"]
    assert pr[0]["cur"] == "UZS" and pr[1]["cur"] == "USD"
    cols, rows, tot = A.build_report("2026-10")
    assert abs(tot["closing"] - sum(A.to_usd(*A.tenant_pockets(r["t"]["id"], "2026-11-01")) for r in rows)) < 0.05
    assert tot["paid"]["USD"] == 700 and tot["paid"]["UZS"] == 2_040_000
    assert {(c_["name"], c_["cur"]) for c_ in cols} >= {("Аренда", "USD"), ("Мусор", "UZS"), ("Прочее (разовое)", "USD"), ("Прочее (разовое)", "UZS")}
h = c.get(f"/tenants/{T1}").get_data(as_text=True)
assert "Баланс на сегодня (итог в $)" in h and "$" in h and "по курсу 12 000" in h
h = c.get("/reports?period=2026-10").get_data(as_text=True)
assert "Сальдо на конец, $" in h and "Аренда, $" in h and "Мусор, сум" in h
x = c.get("/reports.xlsx?period=2026-10")
wb = load_workbook(io.BytesIO(x.data))
assert wb.sheetnames[0] == "Сводка" and len(wb.sheetnames) == 3, wb.sheetnames
hdr = [c_.value for c_ in wb["Сводка"][4]]
assert "Оплачено, $" in hdr and "Оплачено, сум" in hdr and "Сальдо на конец, $" in hdr, hdr
assert c.get(f"/tenants/{T1}/statement.xlsx?period=2026-10").status_code == 200

# ================================================================ клиенты-должники
post("/clients/new", {"name": "Азиз", "phone": "+998 (90) 111-22-33", "company": "Mebel Plus", "active": "on", "sms_enabled": "on"})
post("/clients/new", {"name": "Без номера", "phone": "123", "active": "on", "sms_enabled": "on"})
post("/clients/new", {"name": "На паузе", "phone": "+998931112233", "active": "on", "sms_enabled": "on", "sms_paused_until": "2026-12-31"})
post("/clients/new", {"name": "Нет ТГ", "phone": "+998 97 000 11 07", "active": "on", "sms_enabled": "on"})
C1, C2, C3, C4 = [r[0] for r in con.execute("SELECT id FROM clients ORDER BY id")]
FS.outcomes["998970001107"] = {"status": "no_tg", "error": "нет Telegram"}
post(f"/clients/{C1}/debt", {"order_no": "101", "kind": "services", "description": "Распил", "amount": "500000", "doc_date": "2026-09-01", "due_date": "2026-09-08"})
post(f"/clients/{C1}/debt", {"order_no": "102", "kind": "materials", "description": "ЛДСП 10 листов", "amount": "4 500 000", "doc_date": "2026-09-20"})
post(f"/clients/{C2}/debt", {"amount": "300000", "doc_date": "2026-10-01"})
post(f"/clients/{C3}/debt", {"amount": "900000", "doc_date": "2026-10-01"})
post(f"/clients/{C4}/debt", {"amount": "700000", "doc_date": "2026-10-01"})
with ctx():
    assert A.client_balance(C1) == 5_000_000
    days, d = A.client_overdue(C1, date(2026, 10, 8))
    assert days == 30 and d["order_no"] == "101"
# язык уведомлений — массовая смена из списка клиентов (Азиз -> узбекский)
post("/admin/lang", {"kind": "clients", "lang": "uz", "ids": [str(C1)]})
assert con.execute("SELECT lang FROM clients WHERE id=?", (C1,)).fetchone()[0] == "uz"
assert "O&#39;zbekcha" in c.get(f"/clients/{C1}").get_data(as_text=True)
# оплата 600 000 гасит заказ 101 -> просрочка от заказа 102; уведомление — в Telegram по номеру, SMS нет
post(f"/clients/{C1}/payment", {"amount": "600000", "pay_date": "2026-10-02", "method": "cash", "notify": "on"})
with ctx():
    days, d = A.client_overdue(C1, date(2026, 10, 8))
    assert days == 18 and d["order_no"] == "102", (days, dict(d))
drain()
with ctx():
    RATE = A.fx_rate()
    exp = f"Assalomu alaykum, Азиз! To'lov qabul qilindi: {A.usd(600000 / RATE)}. Qolgan qarz: {A.usd(4_400_000 / RATE)}. {A.get_setting('client_company_name')}"
assert FS.calls[-1]["phone"] == "998901112233" and FS.calls[-1]["text"] == exp, (FS.calls[-1]["text"], exp)
assert con.execute("SELECT tg_status FROM clients WHERE id=?", (C1,)).fetchone()[0] == "ok"
n_sms = con.execute("SELECT COUNT(*) FROM sms_log").fetchone()[0]
# ручное напоминание клиенту без Telegram -> «нет Telegram»
post(f"/clients/{C4}/remind", {"text": "", "channel": "tg"}); drain()
assert con.execute("SELECT tg_status FROM clients WHERE id=?", (C4,)).fetchone()[0] == "none"
assert con.execute("SELECT COUNT(*) FROM sms_log").fetchone()[0] == n_sms
# предпросмотр следующего запуска
with ctx():
    A.set_settings(sms_min_debt="100000")
    tg_ = {x["r"]["name"]: x for x in A.reminder_targets(date(2026, 11, 9))}
    assert tg_["Азиз"]["skip"] == "" and tg_["Азиз"]["ch"] == "tg" and tg_["Азиз"]["tgs"] == "ok"
    assert "номер" in tg_["Без номера"]["skip"] and "пауза" in tg_["На паузе"]["skip"]
    assert tg_["Нет ТГ"]["no_tg"] and tg_["Нет ТГ"]["skip"] == "нет Telegram"
    assert A.next_sms_run(datetime(2026, 11, 10, 11, 0)) == datetime(2026, 11, 12, 10, 0)   # следующий — четверг
html = c.get("/admin/sms").get_data(as_text=True)
assert "нет Telegram" in html and "арендатор" in html and "Без номера" in html
# плановая рассылка в понедельник: клиенты + арендаторы, по одному разу в день
n_calls = len(FS.calls)
with ctx():
    assert "reminders" in A.scheduler_tick(datetime(2026, 11, 9, 10, 1))
    assert A.scheduler_tick(datetime(2026, 11, 9, 15, 0)) == []
    assert A.run_reminders(date.today()) == 0            # повторно в тот же день — никому
drain()
new = FS.calls[n_calls:]
assert any(x["phone"] == "998901112233" and x["text"].startswith("Assalomu alaykum, Азиз! ") and "holatiga qarzdorligingiz: $" in x["text"]
           and A.usd(4_400_000 / RATE) in x["text"] for x in new), new
assert any(x["phone"] == T1_PHONE for x in new) and not any(x["phone"] in ("998931112233", "998970001107") for x in new)
# выключили напоминания арендаторам — только клиенты
with ctx():
    A.set_settings(remind_tenants="0")
    con.execute("DELETE FROM tg_out WHERE kind='reminder'"); con.commit()
    A.run_reminders(date.today())
assert not con.execute("SELECT 1 FROM tg_out WHERE kind='reminder' AND tenant_id IS NOT NULL").fetchone()
with ctx():
    A.set_settings(remind_tenants="1")

# ---- SMS: тестовый режим, затем Eskiz (заглушка HTTP), Play Mobile
post(f"/clients/{C1}/remind", {"text": "", "channel": "sms"})
with ctx():
    A.process_sms_outbox()
s = con.execute("SELECT * FROM sms_log ORDER BY id DESC").fetchone()
assert s["status"] == "test" and s["phone"] == "998901112233" and s["kind"] == "manual", dict(s)
calls = []
class R:
    def __init__(self, code, js): self.status_code, self._js, self.text = code, js, json.dumps(js, ensure_ascii=False)
    def json(self): return self._js
def fake_post(url, data=None, headers=None, timeout=None, auth=None, json=None):
    calls.append((url, data, headers, auth))
    if url.endswith("/auth/login"):
        return R(200, {"message": "token_generated", "data": {"token": f"TOK{len(calls)}"}})
    if url.endswith("/message/sms/send"):
        if headers["Authorization"] == "Bearer OLD":
            return R(401, {"message": "Expired"})
        if "BAD" in data["message"]:
            return R(400, {"message": "Этот смс не прошел модерацию", "status": "error"})
        return R(200, {"id": "abc", "message": "Waiting for SMS provider", "status": "waiting"})
    if "smsxabar" in url:
        return R(200, {})
    raise AssertionError(url)
sms.requests.post = fake_post
post("/admin/sms", {"action": "provider", "sms_provider": "eskiz", "eskiz_email": "me@mail.uz", "eskiz_password": "secret", "eskiz_from": "4546"})
with ctx():
    A.set_settings(eskiz_token="OLD")
post(f"/clients/{C1}/remind", {"text": "", "channel": "sms"})
with ctx():
    A.process_sms_outbox()
    assert A.get_setting("eskiz_token").startswith("TOK")      # токен обновлён после 401
last = con.execute("SELECT * FROM sms_log ORDER BY id DESC").fetchone()
assert last["status"] == "sent" and last["provider"] == "eskiz" and "waiting" in last["response"], dict(last)
send_call = [x for x in calls if x[0].endswith("/sms/send")][-1]
assert send_call[1]["mobile_phone"] == "998901112233" and send_call[1]["from"] == "4546" and \
    send_call[1]["message"] == f"Assalomu alaykum, Азиз! Materiallar va bajarilgan xizmatlar uchun to'lovni eslatamiz. {TODAY} holatiga qarzdorligingiz: {A.usd(4_400_000 / RATE)}. {A.DEFAULT_SETTINGS['client_company_name']}", send_call[1]["message"]
post(f"/clients/{C1}/remind", {"text": "BAD text", "channel": "sms"})
with ctx():
    A.process_sms_outbox()
last = con.execute("SELECT * FROM sms_log ORDER BY id DESC").fetchone()
assert last["status"] == "failed" and "шаблон" in last["response"], dict(last)
with ctx():
    A.set_settings(sms_provider="playmobile", pm_login="u", pm_password="p")
    st_, resp = A.sms_provider().send("998901112233", "Test")
    assert st_ == "sent"
assert calls[-1][3] == ("u", "p") and json.loads(calls[-1][1])["messages"][0]["recipient"] == "998901112233"
# SMS-резерв включили: клиент без Telegram получит SMS, с Telegram — Telegram
post("/admin/sms", {"action": "schedule", "sms_auto": "on", "d0": "on", "d3": "on", "sms_time": "10:00", "sms_min_debt": "100000",
                    "sms_channel": "on", "sms_on_payment": "on", "client_grace_days": "0", "remind_clients": "on", "remind_tenants": "on",
                    "reminder_min_usd": "5"})
with ctx():
    A.set_settings(sms_provider="test")
    assert A.client_channels() == (True, True) and A.sms_days() == [0, 3] and A.get_setting("reminder_min_usd") == "5.0"
    tg_ = {x["r"]["name"]: x for x in A.reminder_targets(date(2026, 11, 12))}
    assert tg_["Азиз"]["ch"] == "tg" and tg_["Нет ТГ"]["ch"] == "sms" and not tg_["Нет ТГ"]["skip"] and tg_["Без номера"]["skip"]
post(f"/clients/{C1}/sms", {"action": "pause", "until": "2026-11-20"})
with ctx():
    assert "пауза" in {x["r"]["name"]: x["skip"] for x in A.reminder_targets(date(2026, 11, 12))}["Азиз"]
    A.set_settings(sms_channel="0")
assert sms.normalize_phone("90 111 22 33") == "998901112233" and sms.normalize_phone("+7 999 1234567") == ""
assert sms.sms_parts("a" * 160) == 1 and sms.sms_parts("я" * 71) == 2

# ================================================================ Telegram-бот (дополнительно, по умолчанию выключен)
assert "t.me/" not in c.get(f"/tenants/{T1}").get_data(as_text=True)          # ссылки бота скрыты
post("/admin/settings", {"action": "token", "token": "123456789:AAHfakefakefakefakefakefakefakefake1"})
post("/admin/settings", {"action": "bot_toggle", "bot_enabled": "on"})
# бот — фақат Lumberture мижозлари учун: ижарага ҳеч қандай боғлиқ жойи йўқ (эгаси айтди, 22.10)
with ctx():
    assert A.bot_on() and not A.rent_bot_on() and A.get_setting("tg_bot_username") == "test_arenda_bot"
    code = A.get_tenant(T1)["tg_code"]
    assert A.deep_link(A.get_tenant(T1)) == ""
    A.handle_update({"update_id": 1, "message": {"chat": {"id": 555, "type": "private"}, "from": {"id": 555, "first_name": "Алишер"}, "text": f"/start {code}"}})
    A.handle_update({"update_id": 2, "message": {"chat": {"id": 777, "type": "private"}, "from": {"id": 777}, "text": "/balance"}})
    A.handle_update({"update_id": 3, "message": {"chat": {"id": 888, "type": "private"}, "from": {"id": 888},
                                                 "contact": {"phone_number": "998901234567", "user_id": 888}}})
    A.handle_update({"update_id": 5, "message": {"chat": {"id": -100, "type": "group"}, "from": {"id": 1}, "text": "/balance"}})
    A.process_outbox()
assert con.execute("SELECT COUNT(*) FROM tenant_chats").fetchone()[0] == 0 and con.execute("SELECT COUNT(*) FROM client_chats").fetchone()[0] == 0
texts = {cid: t for cid, t in SENT}
assert set(texts) == {555, 777, 888} and all("Lumberture" in t and "аренд" not in t.casefold() and "баланс" not in t.casefold() for t in texts.values()), texts
h = c.get(f"/tenants/{T1}").get_data(as_text=True)
assert "t.me/" not in h and c.get(f"/tenants/{T1}/qr.png").status_code == 404
SENT.clear()
post(f"/tenants/{T1}/send-balance"); drain()                                # бот ёқиқ — ижара хабари барибир аккаунтдан
with ctx():
    A.process_outbox()
assert not SENT and FS.calls[-1]["phone"] == T1_PHONE
post("/admin/settings", {"action": "bot_toggle"})

# ---- страницы
for u in ["/", "/tenants", f"/tenants/{T1}", f"/tenants/{T1}/edit", "/payments", "/readings", "/billing", "/reports",
          f"/tenants/{T1}/statement", "/admin/charge-types", f"/admin/charge-types?edit={guard}", f"/admin/charge-types?edit={ct['electricity']}",
          f"/admin/charge-types?edit={ct['rent']}", "/admin/settings", "/admin/users", "/clients", f"/clients/{C1}", f"/clients/{C4}", "/admin/sms",
          "/messages", "/messages?channel=tg&status=sent&q=Азиз", "/rates",
          f"/tenants/{T2}", f"/tenants/{T2}/statement?period=2026-10"]:
    assert c.get(u).status_code == 200, u

# ================================================================ начальные долги (перенос из старого учёта)
import re as _re
with ctx():
    A.fx_store(12000, "manual"); A.db().commit()
def n_out():
    return sum(con.execute(f"SELECT COUNT(*) FROM {t_}").fetchone()[0] for t_ in ("tg_out", "notifications", "sms_log"))
N0 = n_out()
OPN = con.execute("SELECT id FROM charge_types WHERE code='opening'").fetchone()[0]
# -- новый арендатор: «Начальный долг» прямо в форме ($ и сумы отдельно), дата по умолчанию — день перед первым счётом
post("/tenants/new", {"name": "Перенос Тест", "unit": "Цех 20", "area": "50", "phone": "+998 90 555 00 01", "active": "on", "remind_enabled": "on",
                      "contract_start": "2026-10-01", "op_usd": "200", "op_uzs": "1 200 000", "op_date": "", "op_note": "долг 2025"})
TO = con.execute("SELECT id FROM tenants WHERE name='Перенос Тест'").fetchone()[0]
exp_d = (max(date.today().replace(day=1), date(2026, 10, 1)) - timedelta(days=1)).isoformat()
with ctx():
    op = A.tenant_openings(TO)
    assert op["USD"]["amount"] == 200 and op["UZS"]["amount"] == 1_200_000 and op["note"] == "долг 2025" and op["date"] == exp_d, dict(op["USD"])
    assert op["USD"]["description"] == "Долг на начало (перенос): долг 2025" and op["USD"]["source"] == "opening" and op["USD"]["invoice_id"] is None
    assert op["USD"]["charge_type_id"] == OPN and op["UZS"]["currency"] == "UZS"
    assert A.tenant_pockets(TO) == (200, 1_200_000) and A.tenant_balance(TO) == 300
    assert A.opening_default_date(TO, "2026-10-01").isoformat() == exp_d
assert n_out() == N0                                                     # при вводе — никаких сообщений
h = c.get(f"/tenants/{TO}").get_data(as_text=True)
assert "Начальный долг (перенос)" in h and "перенос</span>" in h and "Долг на начало (перенос): долг 2025" in h
assert "долг 2025" in c.get(f"/tenants/{TO}/edit").get_data(as_text=True)
# -- счёт за месяц: перенос в строки счёта не входит (даже если дата в том же месяце), но в остатке учтён
post(f"/tenants/{TO}/opening", {"op_usd": "200", "op_uzs": "1200000", "op_date": "2026-10-05", "op_note": "долг 2025"})
with ctx():
    A.generate_invoice(A.get_tenant(TO), "2026-10"); A.db().commit()
    txt = A.invoice_text(A.get_tenant(TO), "2026-10")
    assert txt.startswith("Здравствуйте, Перенос Тест! Ваша задолженность на ") and f": {A.usd(A.tenant_balance(TO))}. " in txt, txt   # итог включает начальный долг
    assert con.execute("SELECT invoice_id FROM charges WHERE tenant_id=? AND source='opening'", (TO,)).fetchone()[0] is None
assert "Долг на начало" not in c.get("/billing?period=2026-10").get_data(as_text=True)
# -- изменить в карточке: только $, сумы удалить; другая дата и комментарий
post(f"/tenants/{TO}/opening", {"op_usd": "150", "op_uzs": "", "op_date": "2026-09-15", "op_note": "сверка"})
with ctx():
    op = A.tenant_openings(TO)
    assert op["USD"]["amount"] == 150 and op["UZS"] is None and op["date"] == "2026-09-15" and op["USD"]["description"] == "Долг на начало (перенос): сверка"
    assert con.execute("SELECT COUNT(*) FROM charges WHERE tenant_id=? AND source='opening'", (TO,)).fetchone()[0] == 1
    # должник из-за переноса попадает в плановые напоминания
    assert any(x["r"]["id"] == TO for x in A.tenant_reminder_targets())
    led = A.ledger(TO)
    assert led["rows"][0]["source"] == "opening" and led["rows"][0]["date"] == "2026-09-15"
# -- минус = аванс
post(f"/tenants/{TO}/opening", {"op_usd": "-40", "op_uzs": "0", "op_date": "2026-09-15", "op_note": ""})
with ctx():
    op = A.tenant_openings(TO)
    assert op["USD"]["amount"] == -40 and op["USD"]["description"] == "Аванс на начало (перенос)"
    inv_u = con.execute("SELECT SUM(amount) FROM charges WHERE tenant_id=? AND currency='USD' AND source<>'opening'", (TO,)).fetchone()[0] or 0
    assert A.tenant_pockets(TO)[0] == round(inv_u - 40, 4)
# -- «Начислить вручную» видом «Долг на начало» нельзя; в «Виды сборов» служебный вид не показывается
post(f"/tenants/{TO}/charge", {"charge_type_id": OPN, "amount": "5"})
assert con.execute("SELECT COUNT(*) FROM charges WHERE tenant_id=? AND source='opening'", (TO,)).fetchone()[0] == 1
assert "Долг на начало" not in c.get("/admin/charge-types").get_data(as_text=True)
assert f'value="{OPN}"' not in c.get(f"/tenants/{TO}").get_data(as_text=True).split('id="cct"')[1].split("</select>")[0]
# -- удалить (кнопка в карточке) и через ✕ в истории
post(f"/tenants/{TO}/opening", {"action": "delete", "op_usd": "-40", "op_uzs": "", "op_date": "", "op_note": ""})
assert not con.execute("SELECT 1 FROM charges WHERE tenant_id=? AND source='opening'", (TO,)).fetchone()
post(f"/tenants/{TO}/opening", {"op_usd": "10", "op_uzs": "", "op_date": "2026-09-01", "op_note": ""})
oid = con.execute("SELECT id FROM charges WHERE tenant_id=? AND source='opening'", (TO,)).fetchone()[0]
post(f"/charges/{oid}/delete")
assert not con.execute("SELECT 1 FROM charges WHERE tenant_id=? AND source='opening'", (TO,)).fetchone()
assert n_out() == N0

# -- страница «Ввод начальных долгов»: все сразу
h = c.get("/admin/opening").get_data(as_text=True)
assert "Ввод начальных долгов" in h and "Мебельщик Алишер" in h and "Азиз" in h and f'name="t{T1}_usd"' in h
with ctx():
    pk1, cb1 = A.tenant_pockets(T1), A.client_balance(C1)
post("/admin/opening", {"t_id": [str(T1), str(TO)], f"t{T1}_usd": "100", f"t{T1}_uzs": "", f"t{T1}_note": "по сверке",
                        f"t{TO}_usd": "", f"t{TO}_uzs": "", f"t{TO}_note": "",
                        "c_id": [str(C1)], f"c{C1}_uzs": "500 000", f"c{C1}_usd": "20", f"c{C1}_note": "тетрадь", "date": "2026-07-31"})
with ctx():
    op = A.tenant_openings(T1)
    assert op["USD"]["amount"] == 100 and op["UZS"] is None and op["date"] == "2026-07-31" and op["note"] == "по сверке"
    assert A.tenant_pockets(T1) == (round(pk1[0] + 100, 4), pk1[1]) and not A.tenant_openings(TO)["USD"]
    cop = A.client_openings(C1)
    assert cop["UZS"]["amount"] == 500_000 and cop["USD"]["amount"] == 20 and cop["UZS"]["order_no"] == "" and cop["UZS"]["kind"] == "opening"
    assert A.client_balance(C1) == round(cb1 + 500_000 + 20 * 12000, 2)
ids_before = con.execute("SELECT GROUP_CONCAT(id) FROM charges WHERE source='opening'").fetchone()[0]
r = post("/admin/opening", {"t_id": [str(T1)], f"t{T1}_usd": "100", f"t{T1}_uzs": "", f"t{T1}_note": "по сверке", "c_id": [str(C1)],
                            f"c{C1}_uzs": "500000", f"c{C1}_usd": "20", f"c{C1}_note": "тетрадь", "date": ""})
assert con.execute("SELECT GROUP_CONCAT(id) FROM charges WHERE source='opening'").fetchone()[0] == ids_before
assert "Изменений нет" in c.get("/admin/opening").get_data(as_text=True)
assert n_out() == N0

# -- клиенты: старый долг без заказа в сумах/$, оплата в $, аванс, новый клиент с долгом из формы
h = c.get(f"/clients/{C1}").get_data(as_text=True)
assert "Перенос" in h and "$20" in h and "Начальный (старый) долг" in h
post(f"/clients/{C1}/payment", {"amount": "20", "currency": "USD", "method": "cash", "pay_date": "2026-10-08"})
p_ = con.execute("SELECT currency, usd_part, uzs_part, fx_rate FROM client_payments WHERE client_id=? ORDER BY id DESC", (C1,)).fetchone()
assert p_[:] == ("USD", 20, 0, 12000), p_[:]
with ctx():
    assert A.client_pockets(C1)[0] == 0 and A.client_balance(C1) == round(cb1 + 500_000, 2)
    cc1 = A.get_client(C1)
    cp_ = A.q("SELECT * FROM client_payments WHERE client_id=? ORDER BY id DESC", (C1,), one=True)
    assert A.pay_usd(cp_) == 20 and A.client_usd_balance(C1) == round(A.client_balance(C1) / 12000, 2)
    assert A.notif_text("payment", cc1, A.client_usd_balance(C1), A.pay_usd(cp_)) == \
        f"Assalomu alaykum, Азиз! To'lov qabul qilindi: $20. Qolgan qarz: {A.usd(A.client_usd_balance(C1))}. {A.DEFAULT_SETTINGS['client_company_name']}"
    assert A.client_balance_message(cc1) == f"Assalomu alaykum, Азиз! Materiallar va bajarilgan xizmatlar uchun to'lovni eslatamiz. {TODAY} holatiga qarzdorligingiz: {A.usd(A.client_usd_balance(C1))}. {A.DEFAULT_SETTINGS['client_company_name']}"
with ctx():
    cb2 = A.client_balance(C2)
post(f"/clients/{C2}/opening", {"op_uzs": "-100000", "op_usd": "", "op_date": "2026-09-01", "op_note": "аванс"})
with ctx():
    assert A.client_balance(C2) == round(cb2 - 100_000, 2) and A.client_openings(C2)["UZS"]["description"] == "Аванс на начало (перенос): аванс"
    A.client_overdue(C2)
post("/clients/new", {"name": "Старый должник", "phone": "+998 90 999 88 77", "active": "on", "sms_enabled": "on",
                      "op_usd": "75", "op_uzs": "", "op_date": "", "op_note": "тетрадь"})
CN = con.execute("SELECT id FROM clients WHERE name='Старый должник'").fetchone()[0]
with ctx():
    cop = A.client_openings(CN)
    assert cop["USD"]["amount"] == 75 and cop["UZS"] is None and cop["date"] == date.today().isoformat()
    assert A.client_balance(CN) == 900_000 and any(x["r"]["id"] == CN for x in A.reminder_targets())
post(f"/clients/{C3}/debt", {"amount": "10", "currency": "USD", "kind": "materials", "description": "Петли"})
assert con.execute("SELECT currency FROM client_debts WHERE client_id=? ORDER BY id DESC", (C3,)).fetchone()[0] == "USD"
assert c.get("/clients").status_code == 200 and c.get(f"/clients/{CN}").status_code == 200 and c.get(f"/clients/{CN}/edit").status_code == 200
assert n_out() == N0

# -- Excel: шаблон -> заполнить -> загрузить -> предпросмотр -> записать
r = c.get("/admin/opening/template.xlsx")
assert r.status_code == 200
wb = load_workbook(io.BytesIO(r.data))
assert {"Арендаторы", "Клиенты", "Инструкция"} <= set(wb.sheetnames)
ws = wb["Арендаторы"]
hdr = [x.value for x in ws[1]]
assert hdr[0].startswith("ID") and hdr[1].startswith("Название") and "Начальный долг, $ (− аванс)" in hdr and "Начальный долг, сум (− аванс)" in hdr
iu, iz, idt, inote, iarea, irate = (hdr.index("Начальный долг, $ (− аванс)"), hdr.index("Начальный долг, сум (− аванс)"),
                                    [i for i, x in enumerate(hdr) if x.startswith("Дата долга")][0], hdr.index("Комментарий к долгу"),
                                    hdr.index("Площадь, м²"), [i for i, x in enumerate(hdr) if x.startswith("Ставка аренды")][0])
row_t1 = [rw for rw in ws.iter_rows(min_row=2) if rw[0].value == T1][0]
assert row_t1[iu].value == 100 and row_t1[inote].value == "по сверке"
row_t1[iz].value = 300000
ilang = hdr.index("Язык уведомлений (Русский / O'zbekcha)")
assert row_t1[ilang].value in ("", None)                     # язык не задан -> по умолчанию
new = [None] * len(hdr); new[1] = "Импорт Новый"; new[2] = "Цех 30"; new[iarea] = 70; new[irate] = 4.5; new[iu] = 500; new[idt] = "31.08.2026"; new[inote] = "импорт"
new[ilang] = "O‘zbekcha"
ws.append(new)
wc = wb["Клиенты"]
ch_ = [x.value for x in wc[1]]
nc = [None] * len(ch_); nc[1] = "Импорт Клиент"; nc[2] = "+998 90 100 20 30"; nc[ch_.index("Начальный долг, сум (− аванс)")] = "2 000 000"
nc[ch_.index("Язык уведомлений (Русский / O'zbekcha)")] = "uz"
wc.append(nc)
bad = [None] * len(hdr); bad[1] = "Плохая строка"; bad[iarea] = "abc"; bad[ilang] = "English"
ws.append(bad)
def upload(wb_):
    buf = io.BytesIO(); wb_.save(buf); buf.seek(0)
    return c.post("/admin/opening/import", data={"csrf": csrf(), "file": (buf, "dolgi.xlsx")}, content_type="multipart/form-data")
r = upload(wb)
h = r.get_data(as_text=True)
assert r.status_code == 200 and "предпросмотр" in h and "ошибка" in h and "не число" in h and _re.search(r"<button[^>]*disabled", h) and "язык: «English»" in h
tok = _re.search(r'name="token" value="([0-9a-f]+)"', h).group(1)
post("/admin/opening/import", {"action": "apply", "token": tok})
assert not con.execute("SELECT 1 FROM tenants WHERE name='Импорт Новый'").fetchone()           # с ошибками — ничего не записано
ws.delete_rows(ws.max_row)
r = upload(wb); h = r.get_data(as_text=True)
assert "Будет создано: <b>2</b>" in h and "Импорт Новый" in h and "новый" in h and not _re.search(r"<button[^>]*disabled", h), h[h.find("Будет"):h.find("Будет") + 200]
tok = _re.search(r'name="token" value="([0-9a-f]+)"', h).group(1)
post("/admin/opening/import", {"action": "apply", "token": tok})
TI = con.execute("SELECT id FROM tenants WHERE name='Импорт Новый'").fetchone()[0]
with ctx():
    t_ = A.get_tenant(TI); op = A.tenant_openings(TI)
    assert t_["area"] == 70 and t_["rent_rate"] == 4.5 and t_["unit"] == "Цех 30" and t_["active"] == 1
    assert op["USD"]["amount"] == 500 and op["date"] == "2026-08-31" and op["note"] == "импорт" and op["UZS"] is None
    op1 = A.tenant_openings(T1)
    assert op1["USD"]["amount"] == 100 and op1["UZS"]["amount"] == 300_000
    CI = con.execute("SELECT id FROM clients WHERE name='Импорт Клиент'").fetchone()[0]
    assert A.client_openings(CI)["UZS"]["amount"] == 2_000_000 and A.client_balance(CI) == 2_000_000
    assert t_["lang"] == "uz" and A.get_client(CI)["lang"] == "uz" and A.get_tenant(T1)["lang"] == ""
    assert A.notif_text("reminder", t_, A.tenant_balance(TI)).startswith("Assalomu alaykum, Импорт Новый! ")
assert not os.listdir(os.path.join(tmp, "imports")) or all(not f_.endswith(".json") or tok not in f_ for f_ in os.listdir(os.path.join(tmp, "imports")))
r = upload(wb); h = r.get_data(as_text=True)                       # повторная загрузка того же файла — ничего нового
assert "Будет создано: <b>0</b>" in h and "обновлено: <b>0</b>" in h, h[h.find("Будет"):h.find("Будет") + 200]
assert n_out() == N0
for u in ["/admin/opening", f"/tenants/{TI}", f"/tenants/{TI}/edit", "/reports?period=2026-07", "/reports.xlsx?period=2026-07", f"/tenants/{T1}/statement", "/"]:
    assert c.get(u).status_code == 200, u

# ---- кассир: без настроек и удаления; журнал смотреть можно
post("/admin/users", {"username": "kassa", "password": "Pul2026kas", "role": "cashier"})
c.get("/logout"); post("/login", {"username": "kassa", "password": "Pul2026kas"})
assert c.get("/admin/settings").status_code == 403 and c.get("/admin/sms").status_code == 403
assert c.get("/payments").status_code == 200 and c.get("/clients").status_code == 200 and c.get("/messages").status_code == 200
assert c.get("/admin/opening").status_code == 403 and post(f"/tenants/{T1}/opening", {"op_usd": "1"}).status_code == 403
assert post(f"/clients/{C1}/opening", {"op_uzs": "1"}).status_code == 403 and c.get("/admin/opening/template.xlsx").status_code == 403
assert post("/rates", {"action": "manual", "rate": "12500"}).status_code == 403          # ручной курс — только админ
n_fx = con.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
post("/rates", {"action": "refresh"})                                                   # обновить курс может и кассир
assert con.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0] == n_fx + 1
assert post("/payments/1/delete").status_code == 403 and post("/messages", {"action": "tgu_clear"}).status_code == 403
c.get("/logout"); post("/login", {"username": "admin", "password": "admin123"})

# ---- выход из аккаунта
post("/admin/settings", {"action": "tgu_logout"})
with ctx():
    assert not A.tgu_ready() and not FS.auth
    A.set_settings(tgu_authorized="1"); FS.auth = True

# ---- демо: сообщения демо-записям не отправляются; создать и удалить (реальные записи остаются)
n_fx = con.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
post("/admin/demo/create")
assert con.execute("SELECT COUNT(*) FROM tenants WHERE is_demo=1").fetchone()[0] == 6
assert con.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0] == n_fx                  # курс уже есть — демо-курс не нужен
assert con.execute("SELECT COUNT(DISTINCT currency) FROM payments WHERE is_demo=1").fetchone()[0] == 2
assert con.execute("SELECT COUNT(*) FROM clients WHERE is_demo=1").fetchone()[0] == 6
assert con.execute("SELECT COUNT(*) FROM charges WHERE is_demo=1 AND source='opening'").fetchone()[0] == 4
assert con.execute("SELECT COUNT(*) FROM client_debts WHERE is_demo=1 AND kind='opening'").fetchone()[0] == 2
dt_ = con.execute("SELECT id FROM tenants WHERE is_demo=1 LIMIT 1").fetchone()[0]
post(f"/tenants/{dt_}/send-balance")
r = last_out(tenant_id=dt_)
assert r["status"] == "skipped" and "демо" in r["error"], dict(r)
post("/admin/demo/delete")
assert con.execute("SELECT COUNT(*) FROM tenants").fetchone()[0] == 7 and con.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 6
assert not con.execute("SELECT 1 FROM charge_types WHERE is_demo=1").fetchone()

# ================================================================ язык уведомлений и короткие тексты (RU / O'zbekcha, только $)
with ctx():
    t1 = A.get_tenant(T1)
    CO = A.get_setting("company_name")
    assert A.lang_of(t1) == "ru"
    ru = {k: A.notif_text(k, t1, b, amt) for k, b, amt in [("reminder", 204.22, None), ("payment", 50, 120.5), ("payment", -30, 100), ("payment", 0, 10)]}
    assert ru["reminder"] == f"Здравствуйте, Мебельщик Алишер! Ваша задолженность на {TODAY}: $204.22. {CO}", ru
    uzt = {k: A.notif_text(k, t1, b, amt, lang="uz") for k, b, amt in [("reminder", 204.22, None), ("payment", 50, 120.5), ("payment", -30, 100), ("payment", 0, 10)]}
    assert uzt["reminder"] == f"Assalomu alaykum, Мебельщик Алишер! {TODAY} holatiga qarzdorligingiz: $204.22. {CO}"
    assert A.notif_text("payment", t1, 50, 120.5, lang="uz") == f"Assalomu alaykum, Мебельщик Алишер! To'lov qabul qilindi: $120.50. Qolgan qarz: $50. {CO}"
    assert A.notif_text("payment", t1, -30, 100, lang="uz") == f"Assalomu alaykum, Мебельщик Алишер! To'lov qabul qilindi: $100. Hisobingizda: $30. {CO}"
    assert A.notif_text("payment", t1, -30, 100) == f"Здравствуйте, Мебельщик Алишер! Получена оплата: $100. На вашем счёте: $30. {CO}"
    assert A.notif_text("payment", t1, 0, 10) == f"Здравствуйте, Мебельщик Алишер! Получена оплата: $10. Задолженности нет. Спасибо! {CO}"
    assert A.notif_text("reminder", t1, 0.001, lang="uz") == f"Assalomu alaykum, Мебельщик Алишер! Qarzdorlik yo'q. Rahmat! {CO}"
    for txt in list(ru.values()) + list(uzt.values()):
        assert "сум" not in txt and "курс" not in txt and "в т.ч" not in txt and "\n" not in txt and "  " not in txt, txt
    assert set(A.LANGS) == {"ru", "uz"}                     # кириллица для узбекского не предусмотрена
# общий язык по умолчанию -> узбекский: у кого язык не задан, тексты на узбекском; явный «Русский» остаётся русским
def opts_form(lang):
    with ctx():
        g = A.get_setting
        f_ = {k: "on" for k in ("notify_payment", "notify_invoice", "notify_meter", "auto_billing") if g(k) == "1"}
        f_.update(action="options", billing_day=g("billing_day"), notify_hour=g("notify_hour"), company_name=g("company_name"),
                  contact_phone=g("contact_phone"), default_lang=lang, tenant_categories=g("tenant_categories"))
    return f_
post("/admin/settings", opts_form("uz"))
with ctx():
    assert A.get_setting("default_lang") == "uz" and A.lang_of(A.get_tenant(T1)) == "uz"
    assert A.balance_message(A.get_tenant(T1)).startswith("Assalomu alaykum, ")
post(f"/tenants/{T1}/edit", {**{k: (str(v) if v is not None else "") for k, v in dict(A.connect().execute("SELECT * FROM tenants WHERE id=?", (T1,)).fetchone()).items()},
                             "lang": "ru", "active": "on", "remind_enabled": "on"})
with ctx():
    assert A.get_tenant(T1)["lang"] == "ru" and A.balance_message(A.get_tenant(T1)).startswith("Здравствуйте, ")
assert "Язык уведомлений" in c.get(f"/tenants/{T1}/edit").get_data(as_text=True)
h = c.get("/tenants").get_data(as_text=True)
assert 'id="langf"' in h and 'form="langf"' in h and "Сменить язык" in h
ids_all = [str(r[0]) for r in con.execute("SELECT id FROM tenants")]
post("/admin/lang", {"kind": "tenants", "lang": "uz", "ids": ids_all})
assert con.execute("SELECT COUNT(*) FROM tenants WHERE lang='uz'").fetchone()[0] == len(ids_all)
post("/admin/lang", {"kind": "tenants", "lang": "", "ids": ids_all})
assert con.execute("SELECT COUNT(*) FROM tenants WHERE lang=''").fetchone()[0] == len(ids_all)
post("/admin/lang", {"kind": "tenants", "lang": "uz"})                      # никого не отметили -> ничего
assert con.execute("SELECT COUNT(*) FROM tenants WHERE lang='uz'").fetchone()[0] == 0
post("/admin/lang", {"kind": "tenants", "lang": "ru", "ids": [str(T1)]})
# SMS: тот же короткий текст на языке получателя (Telegram недоступен -> SMS)
h = c.get("/admin/sms").get_data(as_text=True)
assert "Настройки → Шаблоны уведомлений" in h and 'name="sms_tpl_reminder"' not in h
with ctx():
    cc1 = A.get_client(C1)
    assert A.client_reminder_text(cc1) == A.client_balance_message(cc1) and A.client_reminder_text(cc1).startswith("Assalomu alaykum, Азиз! ")
post("/admin/settings", opts_form("ru"))
with ctx():
    assert A.get_setting("default_lang") == "ru"

# ================================================================ миграция старой БД (до валют): данные сохраняются
old = os.path.join(tmp, "old.db")
oc = __import__("sqlite3").connect(old)
oc.executescript("""
CREATE TABLE charge_types(id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT NOT NULL, kind TEXT NOT NULL,
  default_amount REAL NOT NULL DEFAULT 0, apply_all INTEGER NOT NULL DEFAULT 0, unit TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 100, is_demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE charges(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, charge_type_id INTEGER, period TEXT NOT NULL, charge_date TEXT NOT NULL,
  description TEXT NOT NULL, quantity REAL NOT NULL DEFAULT 1, unit_price REAL NOT NULL DEFAULT 0,
  amount REAL NOT NULL, source TEXT NOT NULL DEFAULT 'manual', invoice_id INTEGER, created_by INTEGER, created_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE payments(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, pay_date TEXT NOT NULL, amount REAL NOT NULL,
  method TEXT NOT NULL DEFAULT 'cash', note TEXT DEFAULT '', created_by INTEGER, created_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
INSERT INTO charge_types(id,code,name,kind,unit,sort) VALUES(1,'rent','Аренда','rent','м²',0),(2,'electricity','Свет','meter','кВт·ч',1),
  (3,'trash','Мусор','fixed','мес',2),(4,'compressor','Компрессор','fixed','мес',3);
INSERT INTO charges(tenant_id,charge_type_id,period,charge_date,description,amount) VALUES(1,1,'2026-09','2026-09-01','Аренда',5000000),
  (1,3,'2026-09','2026-09-01','Мусор',50000);
INSERT INTO payments(tenant_id,pay_date,amount) VALUES(1,'2026-09-05',3000000);
INSERT INTO settings VALUES('tpl_payment','Оплата {amount} сум. {balance_text}'),('txt_debt','Долг: {sum} сум'),('tgu_api_id','42');
CREATE TABLE clients(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT DEFAULT '', company TEXT DEFAULT '', notes TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, sms_enabled INTEGER NOT NULL DEFAULT 1, sms_paused_until TEXT, tg_code TEXT UNIQUE, is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT);
CREATE TABLE client_debts(id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL, doc_date TEXT NOT NULL, due_date TEXT, order_no TEXT DEFAULT '',
  description TEXT DEFAULT '', kind TEXT NOT NULL DEFAULT 'services', amount REAL NOT NULL, created_by INTEGER, created_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE client_payments(id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL, pay_date TEXT NOT NULL, amount REAL NOT NULL,
  method TEXT NOT NULL DEFAULT 'cash', note TEXT DEFAULT '', created_by INTEGER, created_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0);
INSERT INTO clients(id,name) VALUES(1,'Старый клиент');
INSERT INTO client_debts(client_id,doc_date,order_no,amount) VALUES(1,'2026-09-01','77',1000000);
INSERT INTO client_payments(client_id,pay_date,amount) VALUES(1,'2026-09-10',400000);
""")
oc.commit(); oc.close()
saved_path = A.DB_PATH
A.DB_PATH = old
try:
    A.init_db(); A.init_db()                                       # повторный запуск безопасен
    oc = A.connect()
    cur_ = dict(oc.execute("SELECT code, currency FROM charge_types").fetchall())
    assert cur_["rent"] == "UZS" and cur_["compressor"] == "USD" and cur_["trash"] == "UZS"   # по аренде уже были начисления в сумах — не трогаем
    assert oc.execute("SELECT currency, usd_part, uzs_part, amount FROM payments").fetchone()[:] == ("UZS", 0, 3000000, 3000000)
    assert oc.execute("SELECT COUNT(*), MIN(currency) FROM charges").fetchone()[:] == (2, "UZS")
    st_ = dict(oc.execute("SELECT key, value FROM settings").fetchall())
    assert st_["tpl_payment"] == "Оплата {amount}. {balance_text}" and st_["txt_debt"] == "Долг: {sum}" and st_["tgu_api_id"] == "42"
    assert oc.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0] == 0 and oc.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert cur_["opening"] == "UZS" and oc.execute("SELECT kind FROM charge_types WHERE code='opening'").fetchone()[0] == "opening"
    assert oc.execute("SELECT currency, amount FROM client_debts").fetchone()[:] == ("UZS", 1000000)
    assert oc.execute("SELECT currency, usd_part, uzs_part FROM client_payments").fetchone()[:] == ("UZS", 0, 400000)
    assert "lang" in [r_[1] for r_ in oc.execute("PRAGMA table_info(tenants)")] and oc.execute("SELECT lang FROM clients").fetchone()[0] == ""
    oc.close()
    with ctx():                                                   # курса ещё нет -> условный 12 000, сумовый долг не «пропадает»
        assert A.fx_rate() is None and A.tenant_pockets(1) == (0, 2_050_000) and A.tenant_balance(1) == round(2_050_000 / 12000, 2)
        assert A.client_balance(1) == 600_000 and A.client_overdue(1)[1]["order_no"] == "77"
finally:
    A.DB_PATH = saved_path
print("ALL TESTS PASSED")
