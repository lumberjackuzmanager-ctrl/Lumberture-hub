"""«💵 Мижоздан пул олдим»: ишчи накладнойсиз иш учун пул олди -> ботда ёзади -> кассир «Деньги у работников»да қабул қилади."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil as P, raspil_bot as B, c1, tg

sent = []
def fake_call(token, method, params=None, timeout=15):
    sent.append((method, params or {}))
    return {"message_id": 900 + len(sent)} if method == "sendMessage" else ({"username": "lumb_bot"} if method == "getMe" else True)
tg.call = fake_call
c1.journal = lambda: {}
c1.table = lambda name: []
c1.clients = lambda: {}
txts = lambda: [p.get("text") or "" for m, p in sent if m in ("sendMessage", "editMessageText")]
cbs = lambda p: [b["callback_data"] for row in ((p.get("reply_markup") or {}).get("inline_keyboard") or []) for b in row]
msg = lambda t, uid=701: {"message_id": 2, "chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": t}

assert P.parse_money("150000") == (150000, "UZS") and P.parse_money("150 000 сўм") == (150000, "UZS") and P.parse_money("150к") == (150000, "UZS")
assert P.parse_money("20$") == (20, "USD") and P.parse_money("20,5 доллар") == (20.5, "USD") and P.parse_money("150.000") == (150000, "UZS")
assert P.parse_money("abc") is None and P.parse_money("5") is None and P.parse_money("-100000") is None
assert P.cash_fmt(150000, "UZS") == "150 000 сўм" and P.cash_fmt(20, "USD") == "20,00 $"

with A.app.app_context():
    B.connect("123456:" + "A" * 30)
    con = A.db()
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active) VALUES('Улмас','1',?, 'raspil', 701, 1)", (A.now(),))
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active) VALUES('Жасур','2',?, 'staff', 702, 1)", (A.now(),))
    con.commit()
    rw = dict(con.execute("SELECT * FROM ready_workers WHERE bot_uid=701").fetchone())
    code = P.new_code("Алишер 1131")
    P.add_wait(rw, "job", 0, machine="pris", code=code)

    # «📝 Накладнойсиз» — пастида «💵 Мижоздан пул олдим»
    B.on_message(msg(B.B_WAIT))
    last = [p for m, p in sent if m == "sendMessage"][-1]
    assert "wc:menu" in cbs(last), cbs(last)
    B.cash_flow(rw, 701, 950, ["wc", "menu"])
    e = [p for m, p in sent if m == "editMessageText"][-1]
    assert f"wc:c:{code}" in cbs(e), cbs(e)
    B.cash_flow(rw, 701, 950, ["wc", "c", str(code)])
    assert "Қанча олдингиз" in txts()[-1]
    B.on_message(msg("сумма"))
    assert "Фақат сумма" in txts()[-1]
    B.on_message(msg("150 000"))
    assert "✅ Ёзилди" in txts()[-1] and "150 000 сўм" in txts()[-1] and "Кассага" not in txts()[-1], txts()[-1]
    r = con.execute("SELECT * FROM raspil_cash").fetchone()
    assert r["amount"] == 150000 and r["cur"] == "UZS" and r["code"] == code and r["status"] == "held"
    # черновик изоҳида ва код ҳолатида кўринади
    note = con.execute("SELECT note FROM erp_docs WHERE id=?", (P.code_row(code)["doc_id"],)).fetchone()["note"]
    assert "Ишчи пул олди: Улмас — 150 000 сўм (кассага топширилмаган)" in note, note
    assert "Улмас пул олган: 150 000 сўм" in P.code_status(code)

    # эски накладной рақами билан (1С): рақам → сумма доллар
    B.cash_flow(rw, 701, 951, ["wc", "menu"])
    B.on_message(msg("6453"))
    assert "№6453" in txts()[-1] and "Қанча" in txts()[-1], txts()[-1]
    B.on_message(msg("20$"))
    assert "20,00 $" in txts()[-1] and "150 000 сўм + 20,00 $" in txts()[-1], txts()[-1]
    usd = con.execute("SELECT * FROM raspil_cash WHERE cur='USD'").fetchone()
    assert usd["ref"] == "6453" and usd["code"] is None
    # хато ёзди — ўзи бекор қилади
    B.cash_flow(rw, 701, 952, ["wc", "x", str(usd["id"])])
    assert con.execute("SELECT status FROM raspil_cash WHERE id=?", (usd["id"],)).fetchone()[0] == "void" and "Бекор" in txts()[-1]
    B.cash_flow(rw, 701, 952, ["wc", "l"])
    assert "150 000 сўм" in txts()[-1] and "6453" not in txts()[-1]
    # «🏁 Иш тугади» — эслатма
    B.on_message(msg(B.B_END))
    assert "💵 Кассага топширинг: 150 000 сўм" in txts()[-1], txts()[-1]
    # staff (накладнойсиз йўқ): сумма ёзса — пул ёзилмайди
    n0 = con.execute("SELECT COUNT(*) FROM raspil_cash").fetchone()[0]
    B.on_message(msg("150000", 702))
    assert con.execute("SELECT COUNT(*) FROM raspil_cash").fetchone()[0] == n0
    # тил: рус
    import botlang as BL
    BL.set_lang("work", 701, "ru")
    B.cash_flow(rw, 701, 953, ["wc", "menu"])
    e = [p for m, p in sent if m == "editMessageText"][-1]
    assert "Ҳ" not in e["text"] and "Қ" not in e["text"] and "ў" not in e["text"], e["text"]
    BL.set_lang("work", 701, "uz")

# кассир: «Деньги у работников» → «Принять» → ишчига хабар
cl = A.app.test_client()
with cl.session_transaction() as s_: s_["csrf"] = "t"
cl.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
with cl.session_transaction() as s_: s_["csrf"] = "t"
h = cl.get("/kassa").get_data(as_text=True)
assert "Наличные у работников" in h and "150 000 сум" in h
h = cl.get("/kassa/wcash").get_data(as_text=True)
assert "Улмас" in h and "150 000 сум" in h and "Алишер 1131" in h and "Принять всё" in h, h[-2000:]
with A.app.app_context():
    cid = A.db().execute("SELECT id FROM raspil_cash WHERE status='held'").fetchone()[0]
sent.clear()
assert cl.post("/kassa/wcash", data={"csrf": "t", "id": str(cid)}).status_code == 302
with A.app.app_context():
    r = A.db().execute("SELECT * FROM raspil_cash WHERE id=?", (cid,)).fetchone()
    assert r["status"] == "handed" and r["handed_by"] == "admin"
    assert any("✅ Касса қабул қилди: 150 000 сўм" in t for t in txts()), txts()
    note = A.db().execute("SELECT note FROM erp_docs WHERE id=?", (P.code_row(code)["doc_id"],)).fetchone()["note"]
    assert "(кассага топширди)" in note, note
h = cl.get("/kassa/wcash").get_data(as_text=True)
assert "несданных денег нет" in h and "Принято за 7 дней" in h
assert "Наличные у работников" not in cl.get("/kassa").get_data(as_text=True)
assert cl.post("/kassa/wcash", data={"csrf": "t", "id": str(cid)}).status_code == 302      # қайта — ҳеч нарса
# кассир роли («kassa»): саҳифа очилади, қабул қилади
with A.app.app_context():
    from werkzeug.security import generate_password_hash
    A.db().execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", ("kassir1", generate_password_hash("Kassa12345"), "kassa"))
    A.db().execute("DELETE FROM k_edit_lock"); A.db().commit()
    rw = dict(A.db().execute("SELECT * FROM ready_workers WHERE bot_uid=701").fetchone())
    cid2 = P.cash_add(rw, code, 70000, "UZS")
c2 = A.app.test_client()
with c2.session_transaction() as s_: s_["csrf"] = "t"
assert c2.post("/login", data={"username": "kassir1", "password": "Kassa12345", "csrf": "t"}).status_code == 302
with c2.session_transaction() as s_: s_["csrf"] = "t"
h = c2.get("/kassa/wcash").get_data(as_text=True)
assert "70 000 сум" in h and "Деньги у работников" in c2.get("/kassa").get_data(as_text=True)
assert c2.post("/kassa/wcash", data={"csrf": "t", "id": str(cid2)}).status_code == 302
with A.app.app_context():
    assert A.db().execute("SELECT status, handed_by FROM raspil_cash WHERE id=?", (cid2,)).fetchone()[:] == ("handed", "kassir1")
print("WCASH OK")
