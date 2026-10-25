"""Цех-бот: «📊 Остатка» (каталог қолдиғи; цех бошлиғига — ҳаммаси, кромкачига — фақат кромка) ва «📝 Кромка заказ»
(кромкачи ёзади → «Олиб келиш» саҳифасига ва кассирга хабар). Эгаси, 26.10."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil_bot as B, invbrak as IB, stockbot as SB, pickup as PK, tg, c1, erp

c1.journal = lambda: {}
c1.table = lambda name: []
c1.clients = lambda: {}
c1.goods = lambda: {}
erp.stock_now = lambda: {}
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
with A.app.app_context():
    B.connect("123456:" + "A" * 30)
    db = A.db()
    for uid, nm, ex in ((801, "Жамшид цех бошлиғи", {"boss": 1}), (802, "Улмас кромка", {}), (803, "Жасур кассир", {"owner": 1}), (804, "Аброр", {})):
        cols = ["name", "phone", "created_at", "role", "bot_uid", "active"] + list(ex)
        db.execute(f"INSERT INTO ready_workers({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                   [nm, str(uid), A.now(), "raspil", uid, 1] + list(ex.values()))
    ids = {r["bot_uid"]: r["id"] for r in db.execute("SELECT id, bot_uid FROM ready_workers")}
    for ext, name, par, fol, unit in (("cf1", "ЛДСП", "", 1, ""), ("cf2", "Эггер ЛДСП", "cf1", 1, ""), ("cf3", "Кромка", "", 1, ""), ("cf4", "Кромка Эггер", "cf3", 1, ""),
                                      ("ci91", "ЛДСП Эггер Белый 16мм", "cf2", 0, "лист"), ("ci92", "ЛДСП Эггер Серый 16мм", "cf2", 0, "лист"),
                                      ("ci93", "Кромка Эггер 19/0,8 Белый ПВХ ST9", "cf4", 0, "м"), ("ci94", "Кромка Эггер 23/0,8 Кашемир ПВХ", "cf4", 0, "м")):
        db.execute("INSERT INTO erp_goods(ext,name,unit,parent,folder,deleted,src,cat,lc) VALUES(?,?,?,?,?,0,'cat','good',?)",
                   (ext, name, unit, par, fol, " " + name.casefold() + " "))
    A.set_settings(pickup_notify="[%d]" % ids[803])
    db.commit()
IB.books = lambda con: {"ci91": 12, "ci93": 250.5, "ci94": 0}
with A.app.app_context():
    # рухсат: платформадаги «Права в цех-боте» формаси
    with c.session_transaction() as s: s["csrf"] = "t"
    c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
    with c.session_transaction() as s: s["csrf"] = "t"
    r = c.post("/erp/pickup", data={"csrf": "t", "act": "perm", f"p_stock_{ids[801]}": "1", f"p_stock_{ids[802]}": "2", "p_kreq": [str(ids[802])]})
    assert r.status_code == 302
    w = {r["bot_uid"]: dict(r) for r in A.db().execute("SELECT * FROM ready_workers")}
    assert w[801]["p_stock"] == 1 and w[802]["p_stock"] == 2 and w[802]["p_kreq"] == 1 and not w[804]["p_stock"]
    h = c.get("/erp/pickup").get_data(as_text=True); assert 'name="p_stock_' in h and 'name="p_kreq"' in h

    B.on_message(msg("/start", 801)); k = btns(msgs()[-1]); assert SB.B_STOCK in k and SB.B_KREQ not in k, k
    B.on_message(msg("/start", 804)); k = btns(msgs()[-1]); assert SB.B_STOCK not in k and SB.B_KREQ not in k
    B.on_message(msg("/start", 802)); k = btns(msgs()[-1]); assert SB.B_STOCK in k and SB.B_KREQ in k, k
    # цех бошлиғи: ҳамма каталог (фақат қолдиғи борлари)
    B.on_message(msg(SB.B_STOCK, 801)); m = msgs()[-1]
    assert "📁 ЛДСП · 1" in str(cbs(m)) or any("ЛДСП" in b["text"] for row in m["reply_markup"]["inline_keyboard"] for b in row), m
    assert "sk:f:cf1:0" in cbs(m) and "sk:f:cf3:0" in cbs(m)
    cb("sk:f:cf2:0", 801); t = msgs()[-1]["text"]; assert "ЛДСП Эггер Белый 16мм — 12 лист" in t and "Серый" not in t, t
    B.on_message(msg("кромка белый", 801)); t = msgs()[-1]["text"]; assert "250,5 м" in t, t
    # кромкачи: фақат кромка
    B.on_message(msg(SB.B_STOCK, 802)); m = msgs()[-1]
    assert "sk:f:cf1:0" not in cbs(m) and "sk:f:cf4:0" in cbs(m), cbs(m)
    cb("sk:f:cf1:0", 802); assert "ЛДСП" not in msgs()[-1]["text"].split("\n")[1] or "Кромка" in msgs()[-1]["text"]
    B.on_message(msg("эггер белый", 802)); t = msgs()[-1]["text"]; assert "ЛДСП" not in t and "Кромка Эггер 19/0,8" in t, t
    # кромка заказ: қидириш → танлаш → метр → кассирга
    B.on_message(msg(SB.B_KREQ, 802)); assert "Рўйхат бўш" in msgs()[-1]["text"]
    B.on_message(msg("кашемир", 802)); m = msgs()[-1]; assert "kq:g:0" in cbs(m), m
    cb("kq:g:0", 802); assert "Неча метр" in msgs()[-1]["text"]
    B.on_message(msg("150", 802)); m = msgs()[-1]; assert "Кашемир" in m["text"] and "150" in m["text"] and "kq:s" in cbs(m), m
    B.on_message(msg("19/0,8 белый", 802)); cb("kq:g:0", 802); B.on_message(msg("abc", 802)); assert "сон" in msgs()[-1]["text"]
    B.on_message(msg("100", 802))
    n0 = len(sent)
    cb("kq:s", 802)
    assert "Кассирга юборилди: 2" in msgs()[-1]["text"], msgs()[-1]
    to = [p for m_, p in sent[n0:] if m_ == "sendMessage" and p.get("chat_id") == 803]
    assert to and "Кромка керак — Улмас кромка" in to[0]["text"] and "Кашемир" in to[0]["text"], sent[n0:]
    pi = A.db().execute("SELECT * FROM pickup_items WHERE manual=1 ORDER BY id").fetchall()
    assert [(r["good"], r["need"], r["status"]) for r in pi] == [("ci94", 150.0, "open"), ("ci93", 100.0, "open")] and "Улмас" in pi[0]["note"]
    assert any(r["good"] == "ci94" for g in PK.page_rows(A.db()) for r in g[1])
    B.on_message(msg(SB.B_KREQ, 802)); assert "Рўйхат бўш" in msgs()[-1]["text"]              # юборилганлар рўйхатдан чиқди
    # рухсатсиз — тугма босилса ҳам ишламайди
    cb("kq:s", 804); cb("sk:f:cf1:0", 804)
print("STOCKBOT OK")
