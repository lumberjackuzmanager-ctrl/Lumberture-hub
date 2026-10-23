"""Онлайн буюртма: ташқаридан фақат /api/online/*, каталог, Telegram код билан кириш, буюртма -> накладной черновиги, навбат ва ҳолат хабарлари."""
import os, sys, tempfile, json, re
from datetime import date
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, online as OL, tg, raspil_bot as RB

bot = []
tg.send_message = lambda token, chat, text, markup=None: bot.append((chat, text, markup)) or {"message_id": 1}
staff = []
RB.token = lambda: "x"
RB.send = lambda chat, text, kb=None, reply_to=None: staff.append((chat, text)) or {"message_id": 1}
CF = {"Cf-Ray": "abc-DME", "Cf-Connecting-Ip": "203.0.113.7", "Host": "app.lumberture.com"}

with A.app.app_context():
    con = A.db()
    td = date.today().isoformat()
    con.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (td, td + " 09:00:00"))

    def G(ext, name, parent="", folder=0, price=0, cur=1, unit="", cvis=1):
        con.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES(?,?,?,?,?,?,?,?,0,?,'cat',?,?)",
                    (ext, ext, name, unit, cur, price, parent, folder, " " + name.casefold() + " ", "folder" if folder else "good", cvis))
    G("F1", "ЛМДФ", folder=1); G("F1a", "Moonlight", "F1", 1)
    G("M1", "ЛМДФ Moonlight 5,796 Кашемир 16мм", "F1a", price=52, cur=2, unit="шт")
    G("M2", "Здесь пусто!", "F1a", unit="шт")
    G("F2", "Услуги", folder=1); G("F3", "Распил", "F2", 1); G("F3a", "Распил ОБЫЧНЫЙ", "F3", 1); G("F3b", "Распил ПАКЕТНЫЙ ОБЫЧНЫЙ", "F3", 1)
    G("S6", "Распил ЛДСП/ЛМДФ 5,796 кв", "F3a", price=37000, unit="кол-в"); G("S10", "Распил Ташкаридан", "F3a", price=40000, unit="кол-в")
    G("S30", "Распил ЛДСП/ЛМДФ 5,796 кв (Пакетный)", "F3b", price=47000, unit="кол-в")
    G("F4", "Присадка", "F2", 1)
    for ext, n, p in (("H1", "Услуга сверления Под петли (35мм)", 850), ("H2", "Услуга сверления Глухие отверстия (разного диаметра)", 750),
                      ("H3", "Услуга сверления Евростяжка (лицевая 5*7)", 750), ("H4", "Услуга сверления Евростяжка (торцевая 7*4,5)", 750),
                      ("H5", "Услуга сверления Под эксцентрики", 1350)):
        G(ext, n, "F4", price=p, unit="шт")
    G("F5", "Кромка", "F2", 1); G("F5a", "Кромка Huali", "F5", 1); G("F5b", "Услуга ПВХ", "F5", 1)
    G("E1", "Услуга ПВХ 19/1 Кашемир", "F5a", price=6000, unit="пог.м"); G("E2", "Услуга ПВХ 21/1 Сосна", "F5a", price=0, unit="пог.м")
    G("E9", "Услуга ПВХ от 19/04 до 22/2", "F5b", price=3000, unit="пог.м"); G("E10", "Услуга ПВХ от 35/1 до 48/2", "F5b", price=4000, unit="пог.м")
    con.execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc, lim, ckind, src) VALUES('C7','Азизбек мебель','+998 90 111-22-33, 97 000 00 00',0,0,' азизбек ',0,'client','1c')")
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active, mgr) VALUES('Нодирбек','1',?, 'staff', 900, 1, 1)", (A.now(),))
    con.commit()

c = A.app.test_client()
# 1) ташқаридан: платформа саҳифалари йўқ, API бор
for u in ("/", "/login", "/erp", "/admin/settings", "/static/app.css", "/erp/online"):
    r = c.get(u, headers=CF)
    assert r.status_code == 404 and b"Not found" in r.data, (u, r.status_code)
assert c.get("/erp", headers={"Host": "app.lumberture.com"}).status_code == 404
assert c.post("/login", data={"username": "admin", "password": "admin123"}, headers=CF).status_code == 404
r = c.options("/api/online/order", headers=dict(CF, Origin="https://lumberture.com", **{"Access-Control-Request-Method": "POST"}))
assert r.status_code == 204 and r.headers.get("Access-Control-Allow-Origin") == "https://lumberture.com", r.headers
r = c.get("/api/online/catalog", headers=dict(CF, Origin="https://evil.example"))
assert r.status_code == 200 and "Access-Control-Allow-Origin" not in r.headers and "Set-Cookie" not in r.headers
cat = r.get_json()
m = {x["id"]: x for x in cat["materials"]}
assert "M2" not in m and m["M1"]["sheet"] == {"L": 2800, "W": 2070} and m["M1"]["thick"] == 16 and m["M1"]["price"] == 52 and m["M1"]["cur"] == "USD"
assert m["M1"]["kind"] == "lmdf" and m["M1"]["path"] == ["Moonlight"]
e = {x["id"]: x for x in cat["edges"]}
assert e["E1"]["w"] == 19 and e["E1"]["t"] == 1 and e["E2"]["price"] is None and e["E9"]["client"] and not e["E1"]["client"]
sv = {x["id"]: x for x in cat["services"]}
assert "S6" in sv and "S30" not in sv and sv["S6"]["price"] == 37000 and cat["holes"]["hinge"][0]["price"] == 850 and cat["rate"] == 12000
assert OL.edge_size("Услуга ПВХ от 19/04 до 22/2") == (19, 0.4) and OL.edge_size("Услуга Овал от 19/08 до 22/2") == (19, 0.8)
assert OL.edge_size("Кромка 21/0,8") == (21, 0.8) and OL.edge_size("Кромка Местная 35/1.5") == (35, 1.5) and OL.edge_size("ПВХ 42/2 ташкаридан") == (42, 2)
x = c.get("/api/online/catalog?only=services", headers=CF).get_json()
assert "services" in x and "materials" not in x and "edges" not in x and x["rate"] == 12000
assert c.get("/api/online/orders", headers=CF).status_code == 401

ORDER = {"v": 1, "source": "site", "material": {"id": "M1", "name": "ЛМДФ Кашемир 16 мм", "sheet": {"L": 2800, "W": 2070}, "owner": "ours"},
         "urgent": False, "client": {"name": "Шерзод"},
         "parts": [{"pos": 1, "name": "Фасад", "L": 716, "W": 396, "qty": 2, "grain": True,
                    "edges": {"L1": "E1", "L2": "E1", "W1": "E1", "W2": None}, "holes": {"hinge": 2, "eccentric": 0, "confirmat": 0, "shelfPin": 0, "handle": 1}},
                   {"pos": 2, "name": "Бок", "L": 2000, "W": 560, "qty": 2, "grain": True,
                    "edges": {"L1": None, "L2": None, "W1": "client", "W2": None}, "holes": {"hinge": 0, "eccentric": 0, "confirmat": 4, "shelfPin": 4}}],
         "calc": {"sheets": 1}}
r = c.post("/api/online/calc", json=ORDER, headers=CF)
assert r.status_code == 200, r.data
k = r.get_json()["calc"]
assert k["sheets"] == 1 and k["edges_m"] == {"E1": 3.88, "client": 1.19}, k
assert k["holes"] == {"hinge": 4, "eccentric": 0, "confirmat": 8, "shelfPin": 8}
assert any("Ручка" in w for w in k["warnings"])
assert c.post("/api/online/calc", json=dict(ORDER, v=2), headers=CF).get_json()["error"] == "bad_version"
assert c.post("/api/online/calc", json=dict(ORDER, parts=[dict(ORDER["parts"][0], L=0)]), headers=CF).status_code == 400
assert OL.pack_sheets([(716, 396, 30, True)], 2800, 2070)[0] == 2         # 3 қатор × 5 = 15 та листга, 30 та — 2 лист
assert OL.pack_sheets([(2900, 300, 1, True)], 2800, 2070)[1] == [(2900, 300)]

# 2) кириш: бот уланмаган -> 503 ; уланган -> ҳавола, контакт, код
assert c.post("/api/online/login/start", json={"phone": "+998 90 555 66 77"}, headers=CF).status_code == 503
with A.app.app_context():
    A.set_settings(tg_token="1:x", bot_enabled="1", tg_bot_username="Lumberture_bot")
assert c.post("/api/online/login/start", json={"phone": "12"}, headers=CF).get_json()["error"] == "bad_phone"
r = c.post("/api/online/login/start", json={"phone": "+998 90 555 66 77"}, headers=CF).get_json()
assert r["ok"] and not r["sent"] and r["bot_url"].startswith("https://t.me/Lumberture_bot?start=ol_"), r
nonce = r["nonce"]
assert c.post("/api/online/login/start", json={"phone": "+998905556677"}, headers=CF).status_code == 429     # 30 сония ичида қайта эмас
U = lambda **m: {"message": dict({"chat": {"id": 4242, "type": "private"}, "from": {"id": 4242, "first_name": "Шерзод"}}, **m)}
with A.app.test_request_context():
    A.handle_update(U(text="/start ol_" + nonce))
    assert "request_contact" in bot[-1][2], bot[-1]
    A.handle_update(U(contact={"phone_number": "+998901112233", "user_id": 4242}))
    assert "тўғри келмади" in bot[-1][1]
    A.handle_update(U(contact={"phone_number": "998905556677", "user_id": 4242}))
    code = re.search(r"\b(\d{6})\b", bot[-1][1]).group(1)
    A.handle_update(U(text="салом"))
    assert "Lumberture" in bot[-1][1] and "аренд" not in bot[-1][1].casefold()
    # бот ижара учун эмас (асосий созлама): /balance ва бегона контакт ҳам — Lumberture жавоби, ижара матни эмас
    assert not A.rent_bot_on() and A.bot_on()
    for upd in (dict(text="/balance"), dict(text="/start abc123")):
        A.handle_update(U(**upd))
        assert "Lumberture" in bot[-1][1] and "аренд" not in bot[-1][1].casefold(), (upd, bot[-1])
    A.handle_update({"message": {"chat": {"id": 5151, "type": "private"}, "from": {"id": 5151}, "contact": {"phone_number": "998901234567", "user_id": 5151}}})
    assert bot[-1][0] == 5151 and "Lumberture" in bot[-1][1] and "аренд" not in bot[-1][1].casefold(), bot[-1]
assert c.post("/api/online/login/verify", json={"nonce": nonce, "code": "000000" if code != "000000" else "111111"}, headers=CF).status_code == 401
r = c.post("/api/online/login/verify", json={"nonce": nonce, "code": code}, headers=CF).get_json()
assert r["ok"] and r["phone"] == "+998 90 555-66-77" and r["name"] == "Шерзод", r
H = dict(CF, Authorization="Bearer " + r["token"])
assert c.post("/api/online/login/verify", json={"nonce": nonce, "code": code}, headers=CF).status_code == 401     # бир марта
assert c.get("/api/online/me", headers=H).get_json()["known"] is False

# 3) буюртма -> янги мижоз + накладной черновиги (CSRF'сиз, кукисиз)
r = c.post("/api/online/order", json=ORDER, headers=H)
assert r.status_code == 200, r.data
o = r.get_json()["order"]
assert o["status"] == "sent" and o["sheets"] == 1 and o["status_name"] == "Юборилди"
with A.app.app_context():
    con = A.db()
    row = con.execute("SELECT * FROM online_orders WHERE id=?", (o["id"],)).fetchone()
    assert row["doc_id"] and not row["err"], dict(row)
    cl = con.execute("SELECT * FROM erp_clients WHERE ext=?", (row["client"],)).fetchone()
    assert cl["name"] == "Шерзод" and "555-66-77" in cl["phones"]
    doc = con.execute("SELECT * FROM erp_docs WHERE id=?", (row["doc_id"],)).fetchone()
    assert doc["status"] == "draft" and doc["kind"] == "sale" and doc["src"] == "pf" and "Онлайн №" in doc["note"] and doc["created_by"] == "онлайн"
    ls = {l["good"]: l["qty"] for l in con.execute("SELECT good, qty FROM erp_lines WHERE doc_id=?", (doc["id"],))}
    assert ls == {"M1": 1, "S6": 1, "E1": 3.88, "E9": 1.19, "H1": 4, "H3": 8, "H4": 8, "H2": 8}, ls
    n = con.execute("SELECT text, chat_id FROM notifications WHERE kind='online' ORDER BY id").fetchall()
    assert n and n[-1]["chat_id"] == 4242 and "қабул қилинди" in n[-1]["text"]
assert staff and "Онлайн буюртма №" in staff[-1][1] and staff[-1][0] == 900
# мавжуд мижоз (1С) телефон бўйича топилади; бошқа мижознинг буюртмаси кўринмайди
with A.app.app_context():
    con = A.db()
    assert OL.find_client(con, "998971000000") is None and OL.find_client(con, "+998 97 000-00-00") == "C7" and OL.find_client(con, "901112233") == "C7"
assert c.get(f"/api/online/order/{o['id']}", headers=H).get_json()["order"]["calc"]["edges_m"]["E1"] == 3.88
assert len(c.get("/api/online/orders", headers=H).get_json()["orders"]) == 1
# мижоз материали: распил ташқаридан, материал қатори йўқ
r = c.post("/api/online/calc", json=dict(ORDER, material=dict(ORDER["material"], owner="client", id="")), headers=CF).get_json()["calc"]
assert {l["name"] for l in r["lines"] if l["kind"] in ("material", "raspil")} == {"Распил Ташкаридан"}, r["lines"]

# 4) офисда навбат: ташқаридан эмас
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
with c.session_transaction() as s: s["csrf"] = "t"
h = c.get("/erp/online").get_data(as_text=True)
assert "Шерзод" in h and "Онлайн-заказы" in h
assert c.get(f"/erp/online/{o['id']}").status_code == 200
assert c.get(f"/erp/online/{o['id']}.json").get_json()["parts"][0]["L"] == 716
r = c.post(f"/erp/online/{o['id']}", data={"csrf": "t", "act": "status", "st": "checked"})
assert r.status_code == 302
with A.app.app_context():
    con = A.db()
    t = con.execute("SELECT text FROM notifications WHERE kind='online' ORDER BY id DESC").fetchone()["text"]
    assert "текширилди, сумма" in t and "сўм" in t, t
    s = con.execute("SELECT status, sum_sent FROM online_orders WHERE id=?", (o["id"],)).fetchone()
    assert s["status"] == "checked" and s["sum_sent"] > 0
    exp = round(52 * 12000 + 37000 + 3.88 * 6000 + 1.19 * 3000 + 4 * 850 + 8 * 750 * 3)
    assert abs(s["sum_sent"] - exp) < 5, (s["sum_sent"], exp)
for st in ("paid", "cutting", "ready"):
    c.post(f"/erp/online/{o['id']}", data={"csrf": "t", "act": "status", "st": st})
with A.app.app_context():
    assert "тайёр" in A.db().execute("SELECT text FROM notifications WHERE kind='online' ORDER BY id DESC").fetchone()["text"]
assert c.get(f"/api/online/order/{o['id']}", headers=H).get_json()["order"]["status"] == "ready"
print("ONLINE OK")
