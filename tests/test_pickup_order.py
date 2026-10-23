"""Ботда буюртма (поставщик → товарлар → шофёр / бошқа шофёр) ва қабул (келган сон → платформада приход қоралама)."""
import os, sys, tempfile, json
from datetime import date
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, pickup as PK, raspil_bot as RB

sent, edits = [], []
RB.token = lambda: "x"
RB.send = lambda chat, text, kb=None, reply_to=None: (sent.append((chat, text, kb)) or {"message_id": 100 + len(sent)})
RB.edit = lambda chat, mid, text, kb=None: edits.append((chat, mid, text, kb))


def cq(d, rw_uid=900):
    return {"data": d, "message": {"chat": {"id": rw_uid}, "message_id": 1}, "from": {"id": rw_uid}, "id": "1"}


def btns(kb):
    return [b["callback_data"] for row in (kb or {}).get("inline_keyboard", []) for b in row]


with A.app.app_context():
    con = A.db()
    PK.init_tables(con)
    td = date.today().isoformat()
    con.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (td, td + " 09:00:00"))
    for ext, name in (("PGEMAN", "Eman materials"), ("2P", "Гилам Сарой"), ("30", "Крафтвуд (Окажон)")):
        con.execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc, lim, ckind) VALUES(?,?,'',0,0,?,0,'supplier')", (ext, name, " " + name.casefold() + " "))
    for ext, name, unit, pin in (("G1", "ЛДСП Белый гладкий 5,0325 (n)", "лист", 40), ("G2", "Кромка 19/04 ПВХ", "пог.м", 0.1), ("G9", "ЛДСП Дуб Вотан 5,0325 (n)", "лист", 41)):
        con.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, price_in, parent, folder, deleted, lc, src, cat) VALUES(?,?,?,?,2,0,?,'',0,0,?,'1c','good')",
                    (ext, ext, name, unit, pin, " " + name.casefold() + " "))
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active) VALUES('Нодирбек','1',?, 'staff', 900, 1)", (A.now(),))
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, driver, active) VALUES('Отабек ака','2',?, 'staff', 555, 1, 1)", (A.now(),))
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, boss, active) VALUES('Жамшид ака','3',?, 'raspil', 777, 1, 1)", (A.now(),))
    con.commit()
    data = {"G1": dict(name="ЛДСП Белый гладкий 5,0325 (n)", unit="лист", path="ЛДСП Эконом", need=5.0, refs=[dict(no="6382", client="Азизбек", qty=6, date=td)]),
            "G2": dict(name="Кромка 19/04 ПВХ", unit="пог.м", path="6 Кромка склад новый", need=100.0, refs=[])}
    PK.sync(con, data)
    W = {r["name"]: dict(r) for r in con.execute("SELECT * FROM ready_workers")}
    mgr, drv, boss = W["Нодирбек"], W["Отабек ака"], W["Жамшид ака"]
    assert not mgr["p_order"]

# рухсатсиз — тугма ишламайди
with A.app.app_context():
    assert "рухсат йўқ" in PK.order_cb(mgr, cq("po:s:PGEMAN"))
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post("/erp/pickup", data={"csrf": "t", "act": "perm", "p_order": [str(mgr["id"])], "p_recv": [str(boss["id"])], "driver_w": [str(drv["id"])]})
assert r.status_code == 302
with A.app.app_context():
    con = A.db()
    mgr = dict(con.execute("SELECT * FROM ready_workers WHERE id=?", (mgr["id"],)).fetchone())
    assert mgr["p_order"] == 1
    kb = RB.kb_for(mgr)
    assert {"text": RB.B_ORDER} in kb["keyboard"][-1]
    # 1) поставщиклар: Эман биринчи (етишмаётган товари бор)
    PK.order_start(mgr, 900)
    assert btns(sent[-1][2])[0] == "po:s:PGEMAN", btns(sent[-1][2])
    # 2) Эман: кромка (Эман гуруҳи) олдиндан танланган; ЛДСП — Панельный, бу ерда йўқ
    assert PK.order_cb(mgr, cq("po:s:PGEMAN")) == "Eman materials"
    t = edits[-1][2]
    assert "Кромка 19/04 ПВХ — 100" in t and "ЛДСП Белый" not in t and "☑️ 1" in t, t
    mid = A.db().execute("SELECT id FROM pickup_msgs WHERE state='draft'").fetchone()[0]
    # 3) қўлда товар: қидириш -> танлаш -> сон
    PK.order_cb(mgr, cq(f"po:a:{mid}"))
    assert PK.text_input(mgr, 900, "дуб вотан")
    assert f"po:g:{mid}:G9" in btns(sent[-1][2]), sent[-1]
    PK.order_cb(mgr, cq(f"po:g:{mid}:G9"))
    assert PK.text_input(mgr, 900, "3")
    assert "ЛДСП Дуб Вотан 5,0325 (n) — 3 лист" in sent[-1][1], sent[-1][1]
    # 4) кромкани олиб ташлаб, қайта қўшиш
    kid = A.db().execute("SELECT id FROM pickup_items WHERE ext='G2'").fetchone()[0]
    PK.order_cb(mgr, cq(f"po:t:{mid}:{kid}")); assert "⬜ 1" in edits[-1][2]
    PK.order_cb(mgr, cq(f"po:t:{mid}:{kid}")); assert "☑️ 1" in edits[-1][2]
    # 5) шофёр
    PK.order_cb(mgr, cq(f"po:d:{mid}"))
    assert f"po:v:{mid}:{drv['id']}" in btns(edits[-1][3]) and f"po:e:{mid}" in btns(edits[-1][3])
    assert PK.order_cb(mgr, cq(f"po:v:{mid}:{drv['id']}")) == "Юборилди"
    assert sent[-1][0] == 555 and "Кромка 19/04 ПВХ" in sent[-1][1] and "Дуб Вотан" in sent[-1][1]
    drv = dict(A.db().execute("SELECT * FROM ready_workers WHERE id=?", (drv["id"],)).fetchone())
    vid = A.db().execute("SELECT id FROM pickup_items WHERE ext LIKE 'm%'").fetchone()[0]
    PK.on_cb(drv, cq(f"pk:ok:{kid}", 555)); PK.on_cb(drv, cq(f"pk:no:{vid}", 555))
    # 6) бошқа шофёр (Панельный — ЛДСП): матн
    PK.order_cb(mgr, cq("po:s:2P"))
    mid2 = A.db().execute("SELECT id FROM pickup_msgs WHERE state='draft'").fetchone()[0]
    PK.order_cb(mgr, cq(f"po:e:{mid2}"))
    assert sent[-1][0] == 900 and "Lumberture — Гилам Сарой" in sent[-1][1] and "ЛДСП Белый гладкий 5,0325 (n) — 5 лист" in sent[-1][1], sent[-1]
    # 7) қабул: цех бошлиғи
    boss = dict(A.db().execute("SELECT * FROM ready_workers WHERE id=?", (boss["id"],)).fetchone())
    PK.recv_start(boss, 777)
    assert f"pr:o:{mid}" in btns(sent[-1][2]) and f"pr:o:{mid2}" in btns(sent[-1][2])
    PK.recv_cb(boss, cq(f"pr:o:{mid}", 777))
    t = edits[-1][2]
    assert "келди 100 / 100" in t and "❌" in t and "келди 0 / 3" in t, t          # йўқ экан -> 0
    PK.recv_cb(boss, cq(f"pr:q:{mid}:{kid}", 777))
    assert PK.text_input(boss, 777, "90")
    assert "келди 90 / 100" in sent[-1][1]
    A.set_settings(pickup_notify=json.dumps([mgr["id"]]))
    n0 = len(sent)
    assert PK.recv_cb(boss, cq(f"pr:k:{mid}", 777)) == "Қабул қилинди"
    m = A.db().execute("SELECT * FROM pickup_msgs WHERE id=?", (mid,)).fetchone()
    assert m["state"] == "received" and m["doc_id"], dict(m)
    d = A.db().execute("SELECT * FROM erp_docs WHERE id=?", (m["doc_id"],)).fetchone()
    assert d["kind"] == "receipt" and d["status"] == "draft" and d["client"] == "PGEMAN"
    ls = A.db().execute("SELECT good, qty, price FROM erp_lines WHERE doc_id=?", (m["doc_id"],)).fetchall()
    assert [tuple(x) for x in ls] == [("G2", 90.0, 0.1)], [tuple(x) for x in ls]
    assert "приход қоралама" in edits[-1][2]
    assert any("Фарқ" in s_[1] and "келди 90" in s_[1] for s_ in sent[n0:]), sent[n0:]
    st = {r["ext"][:1]: r["status"] for r in A.db().execute("SELECT ext, status FROM pickup_items")}
    assert A.db().execute("SELECT status FROM pickup_items WHERE id=?", (kid,)).fetchone()[0] == "taken"
    assert A.db().execute("SELECT status FROM pickup_items WHERE id=?", (vid,)).fetchone()[0] == "missing"
    # такрор қабул — йўқ
    assert "аллақачон" in PK.recv_cb(boss, cq(f"pr:k:{mid}", 777))
    # 1С sync қўлда қўшилганни ёпмайди
    PK.sync(con, data)
    assert A.db().execute("SELECT status FROM pickup_items WHERE id=?", (vid,)).fetchone()[0] == "missing"
    # бекор қилиш
    PK.order_cb(mgr, cq("po:s:30"))
    mid3 = A.db().execute("SELECT id FROM pickup_msgs WHERE state='draft'").fetchone()[0]
    assert PK.order_cb(mgr, cq(f"po:x:{mid3}")) == "Бекор"

h = c.get("/erp/pickup").get_data(as_text=True)
assert "Заказы за 7 дней" in h and "черновик" in h and "Права в цех-боте" in h
print("PICKUP ORDER OK")
# ботга ёзилган матн: «🚚 Заказ» тугмаси ва қидирув ҳолатида оддий матн
with A.app.app_context():
    n0 = len(sent)
    RB.on_message({"chat": {"id": 900, "type": "private"}, "from": {"id": 900}, "text": RB.B_ORDER, "message_id": 5})
    assert "поставщикни танланг" in sent[-1][1], sent[n0:]
    RB.on_message({"chat": {"id": 777, "type": "private"}, "from": {"id": 777}, "text": RB.B_RECV, "message_id": 6})
    assert sent[-1][0] == 777 and ("Қайси заказ" in sent[-1][1] or "заказ йўқ" in sent[-1][1]), sent[-1]
print("PICKUP BOT OK")
