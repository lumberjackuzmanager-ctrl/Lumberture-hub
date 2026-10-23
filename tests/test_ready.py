"""«Буюртма тайёр»: накладнойни аниқлаш, мижозга навбат (расм билан), такрорлаш, жуфтлаш. RENT_C1_TEST керак."""
import os, sys, tempfile, re
p = os.environ.get("RENT_C1_TEST", r"C:\new base")
if not os.path.isfile(os.path.join(p, "1Cv7.DD")):
    print("READY TEST SKIPPED (нет копии базы)"); sys.exit(0)
tmp = tempfile.mkdtemp()
os.environ["RENT_DB"] = os.path.join(tmp, "t.db")
fake = os.path.join(tmp, "fakeocr.py")
open(fake, "w", encoding="utf-8").write("import sys\nprint(open(sys.argv[1]+'.txt', encoding='utf-8').read())\n")
os.environ["RENT_OCR_CMD"] = f"{sys.executable} {fake}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, c1, ready as R
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
def post(u, d):
    with c.session_transaction() as s: s["csrf"] = "t"
    d = dict(d, csrf="t"); return c.post(u, data=d)
assert post("/1c/settings", {"c1_path": p, "c1_doc_notify": "1"}).status_code == 302
assert post("/1c/sync", {"force": "1"}).status_code == 302
with A.app.app_context():
    A.set_settings(tgu_enabled="1", tgu_authorized="1", tgu_api_id="1", tgu_api_hash="x")
    j = c1.journal()
    recent = sorted((v for v in j.values() if v["kind"] == 210 and not v["deleted"] and v["date"]), key=lambda v: (v["date"], v["time"]), reverse=True)
    for last in recent[:200]:
        cname, cl = R.doc_client(last["id"])
        if cl and R.find_doc(last["no"])["iddoc"] == last["id"]:
            break
    assert cl
    NO = last["no"]
    d = R.find_doc(NO); assert d and d["iddoc"] == last["id"]
    A.db().execute("UPDATE clients SET active=1, sms_enabled=1, phone='+998 90 111 22 33', tg_chat='' WHERE id=?", (cl["id"],)); A.db().commit()
    # рақам ёзилган
    assert R.pick_doc(f"{NO} тайёр", "")[1] == "ok"
    assert R.pick_doc("99999 тайёр", "")[1] == "nodoc"
    # ҳақиқий OCR кўриниши: «№» → «N2», рақам кейинги қаторда, товар коди ҳам рақам
    o = f"#lang ru\n#rot Clockwise90Degrees\nНакладная N2\n{NO} от 14 Октября 2026 г.\nЛДСП Ультрадекор (5981 BS)\n135\n149.00"
    dd, st_, _ = R.pick_doc("Tayor", o); assert st_ == "ok" and dd["iddoc"] == d["iddoc"], st_
    # расмдан: № ёнидаги рақам, бошқа рақамлар (телефон, сана) халақит бермайди
    ph = os.path.join(tmp, "a.jpg"); open(ph, "wb").write(b"x")
    open(ph + ".txt", "w", encoding="utf-8").write(f"#lang ru\n#rot None\nкв 1816 Покупатель 18-16 97 156-18-16\n#rot Clockwise90Degrees\nНакладная Ng {NO} от 14 Октября 2026 г.\nИтого 149.00")
    w = {"id": 1, "name": "Ишчи"}
    A.db().execute("INSERT INTO ready_workers(id,name,phone,user_id,last_id) VALUES(1,'Ишчи','998901112233',5,10)"); A.db().commit()
    it = dict(worker=w, ent=None, msg_id=11, date=1000.0, text="тайёр", photos=[ph], grouped=None)
    b = A.client_usd_balance(cl["id"])
    txt = R.client_text(A.q("SELECT * FROM clients WHERE id=?", (cl["id"],), one=True), d)
    assert ("$" in txt.split("✅")[1]) == (b >= 1) and "{" not in txt, txt
    st, ack = R.handle(it)
    assert st == "sent" and NO in ack, (st, ack)
    r = A.q("SELECT * FROM tg_out ORDER BY id DESC LIMIT 1", one=True)
    assert r["kind"] == "ready" and not r["file"] and NO in r["text"] and r["client_id"] == cl["id"], dict(r)
    # такрор — юборилмайди
    st, ack = R.handle(dict(it, msg_id=12, text=f"{NO} тайёр"))
    assert st == "dup", st
    assert A.scalar("SELECT COUNT(*) FROM tg_out WHERE kind='ready'") == 1
    # рақам аниқланмади
    open(ph + ".txt", "w", encoding="utf-8").write("ноаниқ расм")
    assert R.handle(dict(it, msg_id=13))[0] == "unknown"
    # қайта ўқиш: аввал аниқланмаган, кейин OCR тузалди
    open(ph + ".txt", "w", encoding="utf-8").write("")
    A.db().execute("DELETE FROM ready_log"); A.db().execute("DELETE FROM tg_out"); A.db().commit()
    assert R.handle(dict(it, msg_id=20))[0] == "unknown"
    open(ph + ".txt", "w", encoding="utf-8").write(f"Накладная № {NO} от 14.10.2026")
    A.set_settings(ready_reprocess="1")
    redo = R._reprocess_pending()
    assert len(redo) == 1 and redo[0][0] == 5 and redo[0][1] == 20 and NO in redo[0][2], redo
    assert A.scalar("SELECT COUNT(*) FROM tg_out WHERE kind='ready'") == 1
    assert R._reprocess_pending() == []
    # хабарлари ўчирилган ҳисоб
    A.db().execute("DELETE FROM ready_log"); A.db().execute("UPDATE clients SET sms_enabled=0 WHERE id=?", (cl["id"],)); A.db().commit()
    assert R.handle(dict(it, msg_id=14, text=f"{NO} тайёр"))[0] == "own"
    # жуфтлаш: расм (ёзувсиз) + кейин «тайёр»; альбом; оддий гап ўтказиб юборилади
    import time; T0 = time.time() - 200
    mk = lambda i, t, ph_, g=None, dt=0: dict(worker=w, ent=None, msg_id=i, date=T0 + dt, text=t, photo=ph_, grouped=g)
    items = R._items([mk(1, "", "p1"), mk(2, "тайёр", None, dt=20), mk(3, "салом", None, dt=30),
                      mk(4, "6218 tayyor", "p2", g=7, dt=40), mk(5, "", "p3", g=7, dt=41)])
    assert len(items) == 2 and items[0]["photos"] == ["p1"] and items[1]["photos"] == ["p2", "p3"], items
    # расм тагида фақат рақам — дарҳол тайёр
    it6 = R._items([mk(30, "6222", "p6", dt=150)]); assert len(it6) == 1 and it6[0]["text"] == "6222"
    # ёзувсиз расм — 2 дақиқадан кейин ўзи тайёр деб олинади
    old_ph = R._items([mk(31, "", "p7", dt=-300)]); assert len(old_ph) == 1 and old_ph[0]["photos"] == ["p7"]
    # расм бир сўровда, «тайёр» кейингисида
    assert R._items([mk(20, "", "p9", dt=100)]) == []
    nxt = R._items([mk(21, "Тайёр", None, dt=160)])
    assert len(nxt) == 1 and nxt[0]["photos"] == ["p9"]
# саҳифа
assert c.get("/ready").status_code == 200
assert post("/ready", {"action": "add", "name": "Улмас", "phone": "90 123 45 67"}).status_code == 302
with A.app.app_context():
    assert A.q("SELECT phone FROM ready_workers WHERE name='Улмас'", one=True)["phone"] == "998901234567"
assert "Улмас" in c.get("/ready").get_data(as_text=True)
print("READY TESTS PASSED")

# ---- Telegram қисми: сохта клиент билан poll()
import asyncio, datetime as _dt, tguser
class Msg:
    def __init__(s, i, text="", photo=False, out=False, g=None):
        s.id, s.raw_text, s.photo, s.out, s.grouped_id, s.document = i, text, photo, out, g, None
        s.date = _dt.datetime.now(_dt.timezone.utc); s.file = None
    async def download_media(s, file):
        open(file + ".jpg", "wb").write(b"img"); open(file + ".jpg.txt", "w", encoding="utf-8").write(f"Накладная № {NO}")
        return file + ".jpg"
class U:
    def __init__(s, i): s.id = i
class Fake:
    def __init__(s): s.sent, s.inbox, s.imported, s.deleted = [], {}, [], []
    async def is_user_authorized(s): return True
    async def __call__(s, req):
        s.imported.append(req.contacts[0].phone)
        class R: users = [U(777)] if req.contacts[0].phone == "+998901112233" else []
        return R()
    async def get_input_entity(s, x): return ("peer", getattr(x, "id", x))
    async def get_messages(s, ent, limit=1, min_id=0):
        ms = [m for m in s.inbox.get(ent[1], []) if m.id > min_id]
        return sorted(ms, key=lambda m: -m.id)[:limit]
    async def send_message(s, ent, text, reply_to=None):
        s.sent.append((ent, text, reply_to))
        class SM: pass
        sm = SM(); sm.id = 9000 + len(s.sent); return sm
    async def delete_messages(s, ent, ids, revoke=True): s.deleted.append((ent, list(ids), revoke))
class FakeSender:
    def __init__(s, fc): s.fc = fc
    def call(s, a, b, fn, timeout=240): return asyncio.run(fn(s.fc))
fc = Fake()
tguser.set_sender(FakeSender(fc))
with A.app.app_context():
    A.db().execute("DELETE FROM ready_workers"); A.db().execute("DELETE FROM ready_log")
    A.db().execute("UPDATE clients SET sms_enabled=1 WHERE id=?", (cl["id"],))
    A.db().execute("INSERT INTO ready_workers(name,phone) VALUES('Ишчи','998901112233'), ('Бегона','998909999999')"); A.db().commit()
    fc.inbox[777] = [Msg(5, "эски хабар тайёр")]
    assert R.poll(force=True) == 0                         # биринчи марта: фақат бошланғич нуқта
    w1 = A.q("SELECT * FROM ready_workers WHERE phone='998901112233'", one=True)
    assert w1["user_id"] == 777 and w1["last_id"] == 5
    assert "топилмади" in A.q("SELECT error FROM ready_workers WHERE phone='998909999999'", one=True)["error"]
    fc.inbox[777] += [Msg(6, "тайёр", photo=True), Msg(7, "салом")]
    assert R.poll(force=True) == 1
    assert fc.sent and NO in fc.sent[-1][1] and fc.sent[-1][2] == 6, fc.sent
    assert A.q("SELECT last_id FROM ready_workers WHERE id=?", (w1["id"],), one=True)["last_id"] == 7
    assert R.poll(force=True) == 0                         # қайта ўқилмайди
    # кечиктириш: мижозга дарҳол кетмайди
    lg = A.q("SELECT * FROM ready_log WHERE status='sent' ORDER BY id DESC LIMIT 1", one=True)
    o = A.q("SELECT * FROM tg_out WHERE id=?", (lg["out_id"],), one=True)
    assert o["status"] == "pending" and o["next_try"] > time.time() + 200, dict(o)
    assert lg["ack_id"] and ("тахминан" in fc.sent[-1][1] or "соат" in fc.sent[-1][1]), fc.sent[-1][1]
    # навбат: олдинда «тайёр» хабарлар бўлса — тахминий вақт ва ўрин
    with A.app.app_context():
        A.set_settings(tgu_delay_min="120", tgu_delay_max="300", tgu_hour_from="0", tgu_hour_to="24", tgu_next_at="0")
        t0 = time.time()
        ids = [A.db().execute("INSERT INTO tg_out(kind,phone,name,text,status,created_at,next_try) VALUES('ready','998900000001','x','t','pending',?,?)",
                              (A.now(), t0 + 180)).lastrowid for _ in range(3)]
        A.db().commit()
        txt = R.eta_text(ids[-1], now=t0)
        assert txt.startswith("тахминан") and "навбатда" in txt, txt
        assert int(re.search(r"(\d+) дақиқада", txt).group(1)) >= 3 + 2 * 3, txt      # 3 дақ. кутиш + олдиндаги 2 та × ~3,5 дақ.
        A.db().execute("DELETE FROM tg_out WHERE id IN (%s)" % ",".join(map(str, ids))); A.db().commit()
    # ишчи «бекор» деб ack'га reply қилади → навбатдан олинади
    m8 = Msg(8, "бекор"); m8.reply_to_msg_id = lg["ack_id"]
    fc.inbox[777].append(m8)
    assert R.poll(force=True) == 1
    assert "🗑" in fc.sent[-1][1], fc.sent[-1]
    assert A.q("SELECT status FROM tg_out WHERE id=?", (o["id"],), one=True)["status"] == "cancelled"
    assert A.q("SELECT status FROM ready_log WHERE id=?", (lg["id"],), one=True)["status"] == "cancelled"
    # бекор қилингандан кейин қайта «тайёр» юборса бўлади (dup эмас)
    fc.inbox[777].append(Msg(9, f"{NO} тайёр"))
    assert R.poll(force=True) == 1 and "✅" in fc.sent[-1][1]
    # кетган хабар → мижоз чатидан ўчирилади (админ тугмаси)
    lg2 = A.q("SELECT * FROM ready_log WHERE status='sent' ORDER BY id DESC LIMIT 1", one=True)
    A.db().execute("UPDATE tg_out SET status='sent', out_msg_id=555, out_peer='777' WHERE id=?", (lg2["out_id"],)); A.db().commit()
    ok, txt = R.cancel(lg2["id"], by="admin")
    assert ok and fc.deleted[-1] == (("peer", 777), [555], True), (txt, fc.deleted)
    assert R.cancel(lg2["id"])[0] is False
print("READY TELEGRAM TESTS PASSED")

# 1С очилмаганда — «retry1c», 1С қайтгач ўзи қайта ишланади
with A.app.app_context():
    A.db().execute("DELETE FROM ready_log"); A.db().commit()
    A.db().execute("INSERT INTO ready_log(at, worker_id, msg_id, text, photo, status) VALUES(?,?,?,?,?,?)", (A.now(), 1, 77, f"{NO} тайёр", "", "retry1c")); A.db().commit()
    out = R._retry_1c()
    assert A.scalar("SELECT COUNT(*) FROM ready_log WHERE status='redo'") == 1, A.q("SELECT status,info FROM ready_log")
print("RETRY1C OK")
