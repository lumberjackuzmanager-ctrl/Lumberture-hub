"""Олиб келиш рўйхати: 1С минуслари -> рўйхат, шофёрга юбориш, «Олдим»/«Йўқ экан», эслатма, кун охири, ўзи ёпилиши."""
import os, sys, tempfile, json, re
from datetime import datetime, timedelta
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, pickup as PK, raspil_bot as RB, staff

sent, edits = [], []
RB.token = lambda: "x"
RB.send = lambda chat, text, kb=None, reply_to=None: (sent.append((chat, text, kb, reply_to)) or {"message_id": 100 + len(sent)})
RB.edit = lambda chat, mid, text, kb=None: edits.append((chat, mid, text, kb))

c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})


def post(u, d):
    with c.session_transaction() as s: s["csrf"] = "t"
    return c.post(u, data=dict(d, csrf="t"))


data = {"G1": dict(name="ЛДСП Белый гладкий 5,0325 (n)", unit="лист", path="ЛДСП Эконом", need=5.0),
        "G2": dict(name="Кромка 19/04 ПВХ", unit="пог.м", path="6 Кромка склад новый", need=176.18),
        "G3": dict(name="ЛМДФ Крафтвуд Серый светлый шагрин премиум 5,0325", unit="лист", path="ЛМДФ Крафтвуд / ЛМДФ Крафтвуд Премиум 5,0325", need=2.0)}
with A.app.app_context():
    con = A.db()
    PK.init_tables(con)
    assert PK.sync(con, data) == (3, 0)
    sup = {r["ext"]: r["sup"] for r in con.execute("SELECT ext, sup FROM pickup_items")}
    assert sup["G1"] == "Назиф дунё, Гилам Сарой, Ибн Сино, Панельный" and sup["G2"] == "Эман" and sup["G3"].startswith("Craftwood"), sup
    assert PK.sync(con, data) == (0, 0)                      # такрорланмайди
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, driver, active) VALUES('Отабек ака','998901112233',?, 'staff', 555, 1, 1)", (A.now(),))
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, mgr, active) VALUES('Офис','998901112244',?, 'ready', 777, 1, 1)", (A.now(),))
    con.commit()
    drv = con.execute("SELECT id FROM ready_workers WHERE driver=1").fetchone()[0]
    ids = {r["ext"]: r["id"] for r in con.execute("SELECT ext, id FROM pickup_items")}
    # роллар: кассир ва менежерда бўлим бор
    secs = json.loads(con.execute("SELECT sections FROM roles WHERE key='kassa'").fetchone()[0])
    assert "pickup" in secs, secs

h = c.get("/erp/pickup").get_data(as_text=True)
assert "Отабек ака" in h and "ЛДСП Белый гладкий" in h and "Craftwood" in h, h[:500]
r = post("/erp/pickup", {"act": "send", "driver": str(drv), "id": [str(ids["G1"]), str(ids["G2"])]})
assert r.status_code == 302
assert len(sent) == 2 and all(s[0] == 555 for s in sent), sent        # 2 етказиб берувчи — 2 хабар
eman = [s for s in sent if "Эман" in s[1]][0]
assert "Кромка 19/04 ПВХ — 176.18 пог.м" in eman[1] and eman[2]["inline_keyboard"][0][0]["callback_data"] == f"pk:ok:{ids['G2']}", eman

with A.app.app_context():
    rw = dict(A.db().execute("SELECT * FROM ready_workers WHERE id=?", (drv,)).fetchone())
    cq = lambda d, mid: {"data": d, "message": {"chat": {"id": 555}, "message_id": mid}, "from": {"id": 555}, "id": "1"}
    assert PK.on_cb(rw, cq(f"pk:ok:{ids['G2']}", 102)) == "✅ Олдим"
    assert "✔️ Ҳаммаси белгиланди" in edits[-1][2] and edits[-1][3] is None
    n0 = len(sent)
    assert PK.on_cb(rw, cq(f"pk:no:{ids['G1']}", 101)) == "❌ Йўқ экан"
    assert any(s[0] == 777 and "Йўқ экан" in s[1] and "ЛДСП Белый" in s[1] for s in sent[n0:]), sent[n0:]   # менежерга
    assert PK.on_cb(rw, cq(f"pk:ok:{ids['G1']}", 101)) == "Аввал белгиланган"
    st = {r["ext"]: r["status"] for r in A.db().execute("SELECT ext, status FROM pickup_items")}
    assert st == {"G1": "missing", "G2": "taken", "G3": "open"}, st
    # эслатма: G3 ни юбориб, 3 соатдан кейин
    con = A.db()
    n, err = PK.send_to_driver(con, [ids["G3"]], drv, "admin")
    assert n == 1 and not err
    dt12 = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)     # кун вақтига боғлиқ бўлмасин
    con.execute("UPDATE pickup_msgs SET at=? WHERE sup LIKE 'Craftwood%'", ((dt12 - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),)); con.commit()
    n0 = len(sent)
    assert PK.remind(con, dt12) == 1 and "белгиланмаган" in sent[-1][1]
    assert PK.remind(con, dt12) == 0          # 2 соатда бир марта
    # шофёр рўйхатини қайта сўраса
    PK.driver_list(rw, 555)
    assert "Craftwood" in sent[-1][1] and sent[-1][2]
    # кун охири
    n0 = len(sent)
    assert PK.eod(con, datetime.now().replace(hour=18, minute=5)) and any(s[0] == 777 and "кун охири" in s[1] for s in sent[n0:])
    assert not PK.eod(con, datetime.now().replace(hour=19))              # бир марта
    # 1С'да приход: G2 минусдан чиқди -> ёпилади; G1 эҳтиёж ўзгарди
    d2 = {"G1": dict(data["G1"], need=7.0), "G3": data["G3"]}
    assert PK.sync(con, d2) == (0, 1)
    r1 = con.execute("SELECT status, need FROM pickup_items WHERE ext='G1'").fetchone()
    assert tuple(r1) == ("missing", 7.0) and con.execute("SELECT status FROM pickup_items WHERE ext='G2'").fetchone()[0] == "done"
    # қайта минусга кетса — янги қатор
    assert PK.sync(con, data) == (1, 0)

# «не везём» ва поставщик
r = post("/erp/pickup", {"act": "skip", "id": [str(ids["G1"])]})
with A.app.app_context():
    assert A.db().execute("SELECT status FROM pickup_items WHERE id=?", (ids["G1"],)).fetchone()[0] == "skip"
post("/erp/pickup", {"act": "sup", "item": str(ids["G1"]), "sup": "Бозор"})
with A.app.app_context():
    assert A.db().execute("SELECT sup FROM pickup_sup WHERE ext='G1'").fetchone()[0] == "Бозор"
h = c.get("/erp/pickup").get_data(as_text=True)
assert "Закрытые за 3 дня" in h and "Кому сообщать" in h

# бот: шофёр клавиатураси ва ёрдам
with A.app.app_context():
    kb = RB.kb_for(rw)
assert kb["keyboard"][0][0]["text"] == RB.B_PICK
# ходим карточкаси: «Шофёр» варианти
assert staff.PRESETS["driver"][2] == "staff" and staff.preset_of({"role": "staff", "driver": 1}) == "driver"
# кунлик буюртмадан: эски минус (накладнойсиз) тушмайди; миқдор = min(минус, накладнойдаги)
g = {"A": dict(name="ЛДСП Белый", unit="лист", parent="F1", folder=False, deleted=False),
     "B": dict(name="Кромка 19/04", unit="пог.м", parent="F2", folder=False, deleted=False),
     "C": dict(name="Распил", unit="шт", parent="F3", folder=False, deleted=False),
     "F1": dict(name="ЛДСП Эконом", parent="0", folder=True, deleted=False), "F2": dict(name="6 Кромка склад новый", parent="0", folder=True, deleted=False),
     "F3": dict(name="1 Распил", parent="0", folder=True, deleted=False)}
fp = lambda items, fid: items[fid]["name"] if fid in items else ""
st = {"A": -5, "B": -176, "C": -3}
orders = [dict(gid="A", qty=3, no="101", date="2026-10-22", client="Хасан"), dict(gid="A", qty=4, no="102", date="2026-10-23", client="Саид"),
          dict(gid="C", qty=3, no="101", date="2026-10-22", client="Хасан")]
b = PK.build(st, g, orders, fp)
assert set(b) == {"A"} and b["A"]["need"] == 5 and [x["no"] for x in b["A"]["refs"]] == ["102", "101"], b
assert PK.build({"A": 2}, g, orders, fp) == {}
assert PK.build({"A": -1}, g, orders, fp)["A"]["need"] == 1
print("PICKUP OK")
