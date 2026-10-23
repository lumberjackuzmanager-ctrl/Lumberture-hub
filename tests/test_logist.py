"""Логистика: шофёр ботда рейс (тариф ва қўлда) ва харажат ёзади, ҳафталик ҳисоб 45/55, СТО 45/55, платформа саҳифаси ва ҳафта охири хабари."""
import os, sys, tempfile
from datetime import date, datetime, timedelta
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, logist as L, raspil_bot as RB

sent = []
RB.token = lambda: "x"
RB.send = lambda chat, text, kb=None, reply_to=None: (sent.append((chat, text, kb)) or {"message_id": 1})
RB.edit = lambda chat, mid, text, kb=None: sent.append((chat, text, kb))


def btns(kb):
    return [b["callback_data"] for row in (kb or {}).get("inline_keyboard", []) for b in row]


def cq(d, uid=500):
    return {"data": d, "message": {"chat": {"id": uid}, "message_id": 1}, "from": {"id": uid}, "id": "1"}


def msg(t, uid=500):
    RB.on_message({"chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": t, "message_id": 1})


assert L.parse_sum("Жомий → Чилонзор 150 000") == (150000, "Жомий → Чилонзор") and L.parse_sum("200 минг")[0] == 200000 and L.parse_sum("салом")[0] is None

with A.app.app_context():
    con = A.db()
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active, owner) VALUES('Жамшид Нурмухамедов','1',?, 'ready', 600, 1, 1)", (A.now(),))
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active, driver) VALUES('Отабек ака','2',?, 'staff', 500, 1, 1)", (A.now(),))
    con.commit()
    con.execute("DELETE FROM lg_cars"); con.execute("DELETE FROM settings WHERE key='lg_seeded'"); con.commit()
    L.init_tables(con); con.commit()
    car = con.execute("SELECT * FROM lg_cars").fetchone()
    assert car["owner"] == "Жамшид Нурмухамедов" and car["owner_rw"], dict(car)
    routes = {r["name"]: r for r in con.execute("SELECT * FROM lg_routes")}
    assert routes["Чинобод"]["price"] == 120000 and routes["База Жомий (Эман)"]["price"] == 80000
    rw = dict(con.execute("SELECT * FROM ready_workers WHERE bot_uid=500").fetchone())
    kb = RB.kb_for(rw)
    assert any(b["text"] == L.B_TRIP for row in kb["keyboard"] for b in row), kb
    # рейс: тариф бўйича
    msg(L.B_TRIP)
    assert f"lg:r:{routes['Чинобод']['id']}" in btns(sent[-1][2])
    L.on_cb(rw, cq(f"lg:r:{routes['Чинобод']['id']}"))
    assert "120 000" in sent[-1][1]
    # мижозга рейс — қўлда сумма
    msg(L.B_TRIP); L.on_cb(rw, cq("lg:c"))
    msg("Жомий → Чилонзор 150000")
    assert "150 000" in sent[-1][1] and "Чилонзор" in sent[-1][1]
    # нотўғри ёзилган рейсни ўчириш
    msg(L.B_TRIP); L.on_cb(rw, cq("lg:c")); msg("хато 999000")
    did = btns(sent[-1][2])[0]
    L.on_cb(rw, cq(did))
    # харажатлар
    for k, t in (("fuel", "200000"), ("gas", "50 минг"), ("other", "30000 мойка"), ("sto", "400000 тормоз")):
        msg(L.B_EXP); L.on_cb(rw, cq(f"lg:e:{k}")); msg(t)
    # бошқа тугма — ҳолат бекор
    msg(L.B_EXP); L.on_cb(rw, cq("lg:e:fuel")); msg(RB.B_PICK)
    c = L.calc(con, rw["id"], L.week_start())
    assert c["rev"] == 270000 and c["common"] == 280000 and c["sto"] == 400000, c
    # (270000 − 280000) = −10000 ; шофёр 45%: −4500 − 400000×45% = −184500 ; эгаси −5500 − 220000 = −225500
    assert c["driver"] == -184500 and c["owner"] == -225500, (c["driver"], c["owner"])
    msg(L.B_WEEK)
    assert "Шофёрга жами" in sent[-1][1] and "СТО" in sent[-1][1]

# платформа: ставкани ўзгартириш, сумма тузатиш
cl = A.app.test_client()
with cl.session_transaction() as s: s["csrf"] = "t"
cl.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
with cl.session_transaction() as s: s["csrf"] = "t"
h = cl.get("/erp/logist").get_data(as_text=True)
assert "Отабек ака" in h and "Чинобод" in h and "Водителю" in h
with A.app.app_context():
    con = A.db()
    did = rw["id"]
    car_id, owner_id = con.execute("SELECT id, owner_rw FROM lg_cars").fetchone()
    rids = [r[0] for r in con.execute("SELECT id FROM lg_routes")]
form = {"csrf": "t", "act": "settings", f"dc_{did}": str(car_id), f"dp_{did}": "50", f"ds_{did}": "40", f"cn_{car_id}": "Дамас", f"co_{car_id}": str(owner_id), "rn_new": "Кастамону", "rp_new": "300000"}
for r in rids:
    with A.app.app_context():
        rr = A.db().execute("SELECT * FROM lg_routes WHERE id=?", (r,)).fetchone()
    form[f"rn_{r}"], form[f"rp_{r}"] = rr["name"], str(rr["price"])
assert cl.post("/erp/logist", data=form).status_code == 302
with A.app.app_context():
    con = A.db()
    c = L.calc(con, did, L.week_start())
    assert c["trip_pct"] == 50 and c["sto_pct"] == 40 and c["car"]["name"] == "Дамас"
    assert con.execute("SELECT price FROM lg_routes WHERE name='Кастамону'").fetchone()[0] == 300000
    eid = con.execute("SELECT id FROM lg_exp WHERE kind='sto'").fetchone()[0]
assert cl.post("/erp/logist", data={"csrf": "t", "act": "fix_exp", "id": str(eid), "amount": "100000"}).status_code == 302
with A.app.app_context():
    c = L.calc(A.db(), did, L.week_start())
    assert c["sto"] == 100000 and c["driver"] == round(-10000 * 0.5 - 100000 * 0.4), c["driver"]
    # душанба 9:00 — ўтган ҳафта ҳисоби шофёрга ва машина эгасига
    con = A.db()
    ws = L.week_start()
    con.execute("UPDATE lg_trips SET at=?", ((ws - timedelta(days=3)).isoformat() + " 10:00:00",)); con.commit()
    sent.clear()
    L.tick(datetime.combine(ws, datetime.min.time()).replace(hour=9, minute=5))
    to = {s[0] for s in sent}
    assert to == {500, 600}, sent
    sent.clear()
    L.tick(datetime.combine(ws, datetime.min.time()).replace(hour=10))
    assert not sent                                                             # бир марта
print("LOGIST OK")
