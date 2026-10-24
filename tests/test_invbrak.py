"""Инвентаризация: «📦 Инвентаризация» (ботда санаш → платформада «Факт»), «Сотилган листлар» (платформа функцияси)
ва «🗑 Брак» (тасдиқлангач — Списание инвентаризацияси). Эгаси, 24.10. RENT_C1_TEST керак."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
p = os.environ.get("RENT_C1_TEST", r"C:\new base")
if not os.path.isfile(os.path.join(p, "1SJOURN.DBF")):
    print("INVBRAK SKIP (нет базы 1С)"); sys.exit(0)
from datetime import date
import app as A, raspil as P, raspil_bot as B, invbrak as IB, erp, tg, basis

sent = []
tg.call = lambda token, method, params=None, timeout=15: sent.append((method, params or {})) or ({"message_id": len(sent)} if method == "sendMessage" else {"username": "b"} if method == "getMe" else True)
class R:
    def json(self): return {"ok": True, "result": {"message_id": 99}}
B.requests.post = lambda *a, **k: R()
btns = lambda p: [b["text"] for row in (p.get("reply_markup") or {}).get("keyboard", []) for b in row]
cbs = lambda p: [b["callback_data"] for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]
msgs = lambda: [p for m, p in sent if m in ("sendMessage", "editMessageText")]
msg = lambda t, uid: {"message_id": 2, "chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": t}
def cb(data, uid, mid=5):
    B.on_callback({"id": "1", "from": {"id": uid}, "data": data, "message": {"chat": {"id": uid}, "message_id": mid}})
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s:
        s.setdefault("csrf", "t"); return s["csrf"]
with A.app.app_context():
    A.set_settings(c1_path=p, edit_lock_days="0", erp_test_mode="1")
    B.connect("123456:" + "A" * 30)
    db = A.db()
    for uid, nm, ex in ((701, "Улмас", {}), (702, "Жамшид", {"owner": 1}), (703, "Отабек", {"driver": 1})):
        cols = ["name", "phone", "created_at", "role", "bot_uid", "active"] + list(ex)
        db.execute(f"INSERT INTO ready_workers({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                   [nm, str(uid), A.now(), "staff" if uid != 702 else "owner", uid, 1] + list(ex.values()))
    # каталог: лист ва кромка (+ «Услуга ПВХ» нормативи) — 1С накладнойи №6169 товарларига мос
    for ext, name, unit, cat in (("ci9001", "ЛМДФ Фон Мокко апельсин 5,0325 16мм", "шт", "good"), ("ci9002", "ЛХДФ Белый Фасадный 5,796", "шт", "good"),
                                 ("ci9003", "Кромка 19/1 ПВХ Дуб Экспрессив бронзовый", "м", "good"), ("ci9004", "Услуга ПВХ 19/1 Дуб Экспрессив бронзовый", "м", "svc"),
                                 ("ci9005", "Тестлист ХДФ оқ", "шт", "good")):
        db.execute("INSERT INTO erp_goods(ext,name,unit,folder,deleted,src,cat,lc) VALUES(?,?,?,0,0,'cat',?,?)", (ext, name, unit, cat, " " + name.casefold() + " "))
    db.execute("INSERT INTO cat_norms(item, comp, qty) VALUES(9004, 9003, 1.0)")
    db.execute("INSERT INTO stock_map(c1, good, at) VALUES('2BT','ci9001',?), ('2QM','ci9002',?)", (A.now(), A.now()))
    db.commit()
    erp._norms["at"] = 0; basis._CAT["at"] = 0
    # «Сотилган листлар» — фақат платформа функцияси (ботда тугма йўқ, эгаси 24.10)
    from datetime import timedelta
    import time as _t
    td, tmr = date.today().isoformat(), (date.today() + timedelta(days=1)).isoformat()
    import ready as RD
    doc, st_, _ = RD.pick_doc("6169", "")
    assert doc and IB.record_unpicked(db, dict(id=None), doc).startswith("✅ №6169")
    rows, un = IB.summary(db, td)
    assert {r["good"]: r["qty"] for r in rows}.get("ci9001") == 5, rows
    # режим ўчиқ: «Инвентаризация» йўқ, «Брак» бор (шофёрда — йўқ)
    B.on_message(msg("/start", 701)); k = btns(msgs()[-1])
    assert IB.B_BRAK in k and IB.B_INVC not in k and IB.B_INVU not in k and B.B_PAY in k and B.B_HOME not in k, k
    B.on_message(msg("/start", 703)); assert IB.B_BRAK not in btns(msgs()[-1])
    B.on_message(msg(IB.B_INVC, 701)); assert "ёқилмаган" in msgs()[-1]["text"]
    # инвентаризация (эртага) — бугун ҳам кўринади; папка → товар → ҳақиқий сони
    db.execute("INSERT INTO erp_goods(ext,name,folder,deleted,src,parent) VALUES('cf1','ЛДСП ва ХДФ',1,0,'cat','')")
    db.execute("UPDATE erp_goods SET parent='cf1' WHERE ext IN ('ci9001','ci9005')"); db.commit()
    erp._stock.update(at=_t.time() + 1e9, v={"ci9001": 7, "ci9005": 3})
    A.set_settings(inv_unpicked_day=tmr)
    B.on_message(msg("/start", 701)); k = btns(msgs()[-1])
    assert IB.B_INVC in k and IB.B_INVU not in k and B.B_HOME not in k, k
    B.on_message(msg("/start", 703)); assert IB.B_INVC not in btns(msgs()[-1])
    sent.clear()
    B.on_message(msg(IB.B_INVC, 701))
    m1, m2 = msgs()[-2], msgs()[-1]
    assert btns(m1) == [IB.B_ICF, IB.B_ICS, IB.B_ICM, B.B_HOME], btns(m1)                 # пастки меню алмашди, «🏠 Асосий меню» шу ерда
    assert "ib:cf:cf1:0" in cbs(m2) and "0/2" in str(m2["reply_markup"]), m2
    cb("ib:cf:cf1:0", 701); e = msgs()[-1]
    assert e["text"].startswith("📦 Инвентаризация") and "ЛДСП ва ХДФ" in e["text"] and "ib:cg:ci9001:cf1:0" in cbs(e) and "ib:cf::0" in cbs(e), e
    cb("ib:cg:ci9001:cf1:0", 701); assert "Платформада: 7" in msgs()[-1]["text"]
    B.on_message(msg("ўн", 701)); assert "Фақат сонини" in msgs()[-1]["text"]
    B.on_message(msg("10", 701))
    assert "— 10" in msgs()[-2]["text"] and "фарқ +3" in msgs()[-2]["text"] and "✅ ЛМДФ" in str(msgs()[-1]["reply_markup"]), msgs()[-2:]
    cb("ib:cg:ci9001:cf1:0", 701); assert "Аввал саналган: 10" in msgs()[-1]["text"]
    B.on_message(msg("+2", 701)); assert "— 12" in msgs()[-2]["text"]
    B.on_message(msg("Тестлист", 701)); assert "ib:cs:0" in cbs(msgs()[-1]) and "ib:cn" in cbs(msgs()[-1])
    cb("ib:cs:0", 701); B.on_message(msg("0", 701)); assert "фарқ -3" in msgs()[-2]["text"], msgs()[-2]
    B.on_message(msg("Ноёб плинтус", 701)); assert "топилмади" in msgs()[-1]["text"]
    cb("ib:cn", 701); assert "Янги товар" in msgs()[-1]["text"]
    B.on_message(msg("5,5", 701)); assert "Янги товар ёзилди" in msgs()[-2]["text"]
    B.on_message(msg(IB.B_ICM, 701)); assert "Сиз санаганлар: 3 та" in msgs()[-1]["text"], msgs()[-1]["text"]
    B.on_message(msg(B.B_TODAY, 701)); assert B.B_IN in btns(msgs()[-1]) and B.B_PAY in btns(msgs()[-1]) and IB._pk().st_get(db, 1)[0] is None    # асосий тугма — санашдан чиқди
    B.on_message(msg(IB.B_INVC, 701)); B.on_message(msg(B.B_HOME, 701)); assert B.B_IN in btns(msgs()[-1])
    rws = {r["good"]: r["fact"] for r in db.execute("SELECT good, fact FROM inv_count WHERE day=?", (tmr,))}
    assert rws == {"ci9001": 12, "ci9005": 0, None: 5.5}, rws
    # платформа: рўйхат, янги товарни боғлаш, инвентаризацияга ўтказиш
    assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": tok()}).status_code == 302
    h = c.get("/erp/inv-help").get_data(as_text=True)
    assert "день пересчёта: " + tmr in h and "Посчитано (2)" in h and "Ноёб плинтус" in h and "Не посчитано" in h, h[:300]
    nid = db.execute("SELECT id FROM inv_count WHERE good IS NULL").fetchone()[0]
    r = c.post("/erp/inv-help", data={"csrf": tok(), "act": "cmap", "cid": nid, "g": "ci9002"}, follow_redirects=True).get_data(as_text=True)
    assert "Товар привязан" in r
    r = c.post("/erp/inv-help", data={"csrf": tok(), "act": "capply", "doc": ""})
    assert r.status_code == 302 and "/edit2" in r.headers["Location"], r.headers.get("Location")
    did = int(r.headers["Location"].split("/doc/")[1].split("/")[0])
    facts = {l["good"]: (l["price"], l["price2"], l["qty"]) for l in db.execute("SELECT good, price, price2, qty FROM erp_lines WHERE doc_id=?", (did,))}
    assert facts == {"ci9001": (12, 7, 5), "ci9005": (0, 3, -3), "ci9002": (5.5, 0, 5.5)}, facts
    assert db.execute("SELECT status FROM erp_docs WHERE id=?", (did,)).fetchone()[0] == "draft"
    cid = db.execute("SELECT id FROM inv_count WHERE good='ci9001'").fetchone()[0]
    c.post("/erp/inv-help", data={"csrf": tok(), "act": "cfact", "cid": cid, "fact": "11"})
    c.post("/erp/inv-help", data={"csrf": tok(), "act": "capply", "doc": str(did)})
    facts = {l["good"]: l["price"] for l in db.execute("SELECT good, price FROM erp_lines WHERE doc_id=?", (did,))}
    assert facts == {"ci9001": 11, "ci9005": 0, "ci9002": 5.5}, facts
    erp._stock.update(at=0)
    # брак: лист → сони → сабаби → расмсиз → шерикка тасдиқлаш → Списание (тест режимида черновик)
    sent.clear()
    B.on_message(msg(IB.B_BRAK, 701)); assert "Қайси лист" in msgs()[-1]["text"]
    B.on_message(msg("Тестлист", 701)); assert "ib:bg:0" in cbs(msgs()[-1]), msgs()[-1]
    cb("ib:bg:0", 701); assert "Нечта" in msgs()[-1]["text"]
    B.on_message(msg("ики", 701)); assert "Фақат рақам" in msgs()[-1]["text"]
    B.on_message(msg("2", 701)); assert "ib:br:sin" in cbs(msgs()[-1])
    cb("ib:br:sin", 701); assert "расмини" in msgs()[-1]["text"] and "ib:bp" in cbs(msgs()[-1])
    cb("ib:bp", 701)
    b = db.execute("SELECT * FROM raspil_brak").fetchone()
    assert b["good"] == "ci9005" and b["qty"] == 2 and b["reason"] == "sin" and b["status"] == "new"
    toown = [p for m, p in sent if m == "sendMessage" and p.get("chat_id") == 702]
    assert toown and "Тестлист" in toown[-1]["text"] and f"ib:ok:{b['id']}" in cbs(toown[-1]), toown
    assert any("sw:kin" in cbs(p) for m, p in sent if m == "sendMessage" and p.get("chat_id") == 701)     # қолган қисми — кусокка
    cb(f"ib:ok:{b['id']}", 703)                               # шофёр тасдиқлай олмайди
    assert db.execute("SELECT status FROM raspil_brak").fetchone()[0] == "new"
    cb(f"ib:ok:{b['id']}", 702)
    b = db.execute("SELECT * FROM raspil_brak").fetchone()
    assert b["status"] == "ok" and b["by_name"] == "Жамшид" and b["doc_id"], dict(b)
    d = db.execute("SELECT * FROM erp_docs WHERE id=?", (b["doc_id"],)).fetchone()
    l = db.execute("SELECT * FROM erp_lines WHERE doc_id=?", (b["doc_id"],)).fetchone()
    assert d["kind"] == "inv" and d["status"] == "draft" and "брак" in d["note"].casefold() and l["good"] == "ci9005" and l["qty"] == -2, (dict(d), dict(l))
    assert any("тасдиқланди" in p.get("text", "") for m, p in sent if m == "sendMessage" and p.get("chat_id") == 701)
    erp.inv_rebook(db, b["doc_id"])                          # проведение пайтида ҳам −2 қолади
    assert db.execute("SELECT qty FROM erp_lines WHERE doc_id=?", (b["doc_id"],)).fetchone()[0] == -2
    cb(f"ib:no:{b['id']}", 702); assert db.execute("SELECT status FROM raspil_brak").fetchone()[0] == "ok"
    # иккинчи брак — офисда рад этилади
    B.on_message(msg(IB.B_BRAK, 701)); B.on_message(msg("Тестлист", 701)); cb("ib:bg:0", 701); B.on_message(msg("1", 701)); cb("ib:br:cut", 701)
    B.on_message(msg(B.B_HOME, 701))                         # ўртада бош менюга чиқди — ёзилмайди
    assert db.execute("SELECT COUNT(*) FROM raspil_brak").fetchone()[0] == 1
    B.on_message(msg(IB.B_BRAK, 701)); B.on_message(msg("Тестлист", 701)); cb("ib:bg:0", 701); B.on_message(msg("1", 701)); cb("ib:br:cut", 701); cb("ib:bp", 701)
    b2 = db.execute("SELECT id FROM raspil_brak ORDER BY id DESC").fetchone()[0]
    r = c.post("/erp/inv-help", data={"csrf": tok(), "act": "bno", "bid": b2}, follow_redirects=True).get_data(as_text=True)
    assert "Брак отклонён" in r and db.execute("SELECT status FROM raspil_brak WHERE id=?", (b2,)).fetchone()[0] == "no"
    h = c.get("/erp/inv-help").get_data(as_text=True)
    assert "Тестлист" in h and "списано" in h and "отклонено" in h
    # учёт: 1С'дан кўчирилган каталог товари (c1g:<1С id>) — 1С қолдиғи (платформада ҳаракат бўлмагунча)
    db.execute("INSERT INTO cat_items(id, node, name, skey, svc) VALUES(9006, 0, 'ЛМДФ Эконом Тест', 'c1g:XX1', 0)")
    db.execute("INSERT INTO erp_goods(ext,name,unit,folder,deleted,src,cat,lc,parent) VALUES('ci9006','ЛМДФ Эконом Тест','лист',0,0,'cat','good',' лмдф ','cf1')")
    db.commit()
    erp._stock.update(at=_t.time() + 1e9, v={"XX1": 4, "ci9001": 7})
    bk = IB.books(db)
    assert bk.get("ci9006") == 4 and bk.get("ci9001") == 7, bk
    assert "ci9006" in [g["good"] for g in IB.stocked(db)]
    erp._stock.update(at=0)
    A.set_settings(inv_unpicked_day=(date.today() - timedelta(days=1)).isoformat())
    assert IB.inv_day() == "" and IB.B_INVC not in IB.buttons(None)
    A.set_settings(inv_unpicked_day="")
print("INVBRAK OK")
