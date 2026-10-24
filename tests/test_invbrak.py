"""Остатка: «📋 Сотилган листлар» (кесилмаган / олиб кетилмаган листлар ва кромка — накладной бўйича «Факт»дан айрилади)
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
    # режим ўчиқ: «Сотилган листлар» йўқ, «Брак» бор (шофёрда — йўқ)
    B.on_message(msg("/start", 701)); k = btns(msgs()[-1])
    assert IB.B_BRAK in k and IB.B_INVU not in k and B.B_PAY in k, k
    B.on_message(msg("/start", 703)); assert IB.B_BRAK not in btns(msgs()[-1])
    B.on_message(msg(IB.B_INVU, 701)); assert "ёқилмаган" in msgs()[-1]["text"]
    A.set_settings(inv_unpicked_day=date.today().isoformat())
    B.on_message(msg("/start", 701)); assert IB.B_INVU in btns(msgs()[-1]), btns(msgs()[-1])
    # ишчи накладной рақамини ёзади
    B.on_message(msg(IB.B_INVU, 701)); assert "накладнойини" in msgs()[-1]["text"] and "ib:ue" in cbs(msgs()[-1])
    B.on_message(msg("6169", 701))
    t = msgs()[-1]["text"]
    assert t.startswith("✅ №6169") and "ЛМДФ Фон Мокко" in t and "Кромка ПВХ Дуб Экспрессив" in t and "Распил" not in t and "сверления" not in t, t
    u = db.execute("SELECT * FROM inv_unpicked").fetchone()
    ls = {l["gid"]: l for l in json.loads(u["lines"])}
    assert ls["2BT"]["good"] == "ci9001" and ls["2BT"]["qty"] == 5 and ls["2QM"]["good"] == "ci9002", ls
    assert ls["7K"]["good"] == "ci9003" and ls["7K"]["qty"] == 45.5 and ls["7K"]["unit"] == "м", ls["7K"]
    assert "24F" in ls and not ls["24F"]["good"]            # каталогда мос товар йўқ — офис танлайди
    B.on_message(msg("6169", 701)); assert "аввал ёзилган" in msgs()[-1]["text"]
    B.on_message(msg("салом", 701)); assert "рақамини ёзинг" in msgs()[-1]["text"]
    cb("ib:ue", 701); assert "1 та" in msgs()[-2]["text"] and msgs()[-1]["text"].startswith(B.B_HOME)
    B.on_message(msg("6169", 701)); assert not msgs()[-1]["text"].startswith("ℹ️")   # режимдан чиқди — оддий иш
    # платформа: рўйхат, сонни тузатиш, мос товар танлаш, инвентаризациядан айириш
    assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": tok()}).status_code == 302
    h = c.get("/erp/inv-help").get_data(as_text=True)
    assert "№6169" in h and "ЛМДФ Фон Мокко" in h and "Без товара каталога" in h, h[:500]
    idx = list(ls).index("24F")
    r = c.post("/erp/inv-help", data={"csrf": tok(), "act": "qty", "uid": u["id"], "q_0": "4", f"g_{idx}": "ci9005"}, follow_redirects=True)
    assert "сохранено" in r.get_data(as_text=True)
    ls2 = json.loads(db.execute("SELECT lines FROM inv_unpicked WHERE id=?", (u["id"],)).fetchone()[0])
    assert ls2[0]["qty"] == 4 and ls2[idx]["good"] == "ci9005" and db.execute("SELECT good FROM stock_map WHERE c1='24F'").fetchone()[0] == "ci9005"
    rows, un = IB.summary(db, date.today().isoformat())
    assert {r["good"]: r["qty"] for r in rows} == {"ci9001": 4, "ci9002": 2, "ci9003": 45.5, "ci9005": 2} and [x["gid"] for x in un] == ["343"], (rows, un)   # маҳаллий кромка — каталогда йўқ
    with A.app.test_request_context():
        did = erp.save_doc2(db, "inv", None, dict(date=date.today().isoformat(), note="пересчёт", lines=[
            dict(good="ci9001", fact="10"), dict(good="ci9003", fact="200"), dict(good="ci9002", fact="1")]), post=False)
    db.commit()
    r = c.post("/erp/inv-help", data={"csrf": tok(), "act": "apply", "doc": did}, follow_redirects=True).get_data(as_text=True)
    assert "Вычтено из «Факт»: 3" in r and "Тестлист" in r, r[r.find("flash"):r.find("flash") + 400]     # ci9005 инвентаризацияда йўқ — огоҳлантириш
    facts = {l["good"]: l["price"] for l in db.execute("SELECT good, price FROM erp_lines WHERE doc_id=?", (did,))}
    assert facts == {"ci9001": 6, "ci9003": 154.5, "ci9002": 0}, facts
    r = c.post("/erp/inv-help", data={"csrf": tok(), "act": "apply", "doc": did}, follow_redirects=True).get_data(as_text=True)
    assert "уже вычтено" in r
    assert c.post("/erp/inv-help", data={"csrf": tok(), "act": "add", "no": "6167"}, follow_redirects=True).status_code == 200
    assert db.execute("SELECT COUNT(*) FROM inv_unpicked WHERE doc_no='6167'").fetchone()[0] == 1
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
    A.set_settings(inv_unpicked_day="")
print("INVBRAK OK")
