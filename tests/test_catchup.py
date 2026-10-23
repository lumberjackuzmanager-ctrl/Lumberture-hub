"""Свет/компьютер ўчиб қолса: даунтайм белгиланади, retry хабари йўқолмайди, «тайёр» етмаса ишчига огоҳлантириш."""
import os, sys, tempfile, time, threading
tmp = tempfile.mkdtemp()
os.environ["RENT_DB"] = os.path.join(tmp, "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, ready as R
from datetime import datetime

class Fake:
    def __init__(self): self.res = {"status": "no_tg", "error": "нет"}; self.sent = []
    def send(self, *a, **k): return self.res
    def send_chat(self, *a, **k): return self.res
    def call(self, api_id, api_hash, fn, timeout=240):
        import asyncio
        class C:
            async def get_input_entity(s, x): return x
            async def send_message(s, ent, text, reply_to=None): self.sent.append((ent, text, reply_to))
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("r", asyncio.run(fn(C())))); th.start(); th.join()
        return out.get("r")
fk = Fake()
with A.app.app_context():
    A.tgu_sender = lambda: fk
    A.tgu_state_orig = A.tgu_state
    A.tgu_state = lambda dt=None, urgent=False: (True, "", 0)
    A.set_settings(tgu_enabled="1", tgu_authorized="1", tgu_api_id="1", tgu_api_hash="x")
    # 1) даунтайм
    A.set_settings(hb_at=str(time.time() - 3 * 86400))
    A.note_downtime()
    assert A.get_setting("c1_catchup_from") == datetime.fromtimestamp(time.time() - 3 * 86400).date().isoformat()
    assert float(A.get_setting("hb_at")) > time.time() - 5
    A.set_settings(c1_catchup_from="")
    A.note_downtime()                      # қисқа танаффус — белгиланмайди
    assert A.get_setting("c1_catchup_from") == ""
    # 2) retry — 6 мартадан кейин ҳам failed бўлмайди
    db = A.db()
    db.execute("INSERT INTO clients(name, phone, active, sms_enabled, created_at) VALUES('Тест (Ака)', '+998 90 981 82 33', 1, 1, ?)", (A.now(),))
    cid = A.scalar("SELECT MAX(id) FROM clients")
    db.execute("INSERT INTO tg_out(client_id, kind, phone, name, text, status, attempts, next_try, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (cid, "invoice", "998909818233", "Тест", "x", "pending", 9, 0, A.now()))
    oid = A.scalar("SELECT MAX(id) FROM tg_out"); db.commit()
    fk.res = {"status": "retry", "error": "нет сети"}
    A.process_tg_one()
    r = A.q("SELECT * FROM tg_out WHERE id=?", (oid,), one=True)
    assert r["status"] == "pending" and r["next_try"] > time.time() + 500, dict(r)
    db.execute("UPDATE tg_out SET status='sent' WHERE id=?", (oid,)); db.commit()
    # 3) «тайёр» етмади → журнал ва ишчига огоҳлантириш
    db.execute("INSERT INTO ready_workers(id,name,phone,user_id,last_id) VALUES(1,'Жамшид','998901112233',555,0)")
    db.execute("INSERT INTO tg_out(client_id, kind, phone, name, text, status, attempts, next_try, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (cid, "ready", "998909818233", "Тест (Ака)", "tayyor", "pending", 0, 0, A.now()))
    oid = A.scalar("SELECT MAX(id) FROM tg_out")
    db.execute("INSERT INTO ready_log(at, worker_id, msg_id, doc_no, client_id, status, out_id, ack_id) VALUES(?,?,?,?,?,?,?,?)",
               (A.now(), 1, 70, "6133", cid, "sent", oid, 71))
    lid = A.scalar("SELECT MAX(id) FROM ready_log"); db.commit()
    fk.res = {"status": "no_tg", "error": "нет"}
    A.process_tg_one()
    assert A.q("SELECT status FROM ready_log WHERE id=?", (lid,), one=True)["status"] == "nodeliv"
    to = [s for s in fk.sent if s[0] == 555]
    assert to and to[0][2] == 71 and "6133" in to[0][1] and "ЕТМАДИ" in to[0][1] and "+998 90 981 82 33" in to[0][1], fk.sent
    assert any(s[0] == "me" for s in fk.sent)
    # бошқа турдаги хабар — огоҳлантириш йўқ
    n = len(fk.sent)
    db.execute("INSERT INTO tg_out(client_id, kind, phone, name, text, status, attempts, next_try, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (cid, "invoice", "998909818233", "Тест", "x", "pending", 0, 0, A.now())); db.commit()
    A.process_tg_one()
    assert len(fk.sent) == n
print("CATCHUP TESTS PASSED")

# 4) кунлик лимит тўлса ҳам «тайёр» кетади, бошқалари кутади; навбатда «тайёр» биринчи
with A.app.app_context():
    db = A.db()
    db.execute("UPDATE tg_out SET status='sent'")
    A.set_settings(tgu_daily_cap="2", tgu_next_at="0", tgu_hour_from="0", tgu_hour_to="24")
    A.tgu_state = A.tgu_state_orig
    A.tgu_ready = lambda: True
    db.execute("UPDATE tg_out SET tried_at=?", (A.now(),))
    for k in ("invoice", "ready"):
        db.execute("INSERT INTO tg_out(client_id, kind, phone, name, text, status, attempts, next_try, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (cid, k, "998900000001", "Т", k, "pending", 0, 0, A.now()))
    db.commit()
    fk.res = {"status": "sent", "msg_id": 9}
    A.process_tg_one()
    assert A.q("SELECT status FROM tg_out WHERE text='ready' ORDER BY id DESC LIMIT 1", one=True)["status"] == "sent"
    A.set_settings(tgu_next_at="0")
    A.process_tg_one()
    assert A.q("SELECT status FROM tg_out WHERE text='invoice' ORDER BY id DESC LIMIT 1", one=True)["status"] == "pending"
print("CAP TESTS PASSED")

# 5) лимит фақат янги рақамларга: таниш мижозга (аввал хабар кетган) лимит тўлса ҳам кетади, янгига — йўқ
with A.app.app_context():
    db = A.db()
    db.execute("UPDATE tg_out SET status='sent', tried_at=?", (A.now(),)); db.commit()
    A.set_settings(tgu_daily_cap="1", tgu_next_at="0")
    db.execute("INSERT INTO tg_out(client_id, kind, phone, name, text, status, attempts, next_try, created_at, tried_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (cid, "invoice", "998900000777", "Янги0", "old-new", "sent", 1, 0, A.now(), A.now()))
    for ph_, t_ in (("998900000555", "new1"), ("998900000001", "known1")):
        db.execute("INSERT INTO tg_out(client_id, kind, phone, name, text, status, attempts, next_try, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (cid, "invoice", ph_, "Т", t_, "pending", 0, 0, A.now()))
    db.commit()
    assert A.tgu_tried_today() >= 1
    A.process_tg_one()
    st = {r["text"]: r["status"] for r in A.q("SELECT text, status FROM tg_out WHERE text IN ('new1','known1')")}
    assert st == {"new1": "pending", "known1": "sent"}, st
    A.set_settings(tgu_next_at="0")
    A.process_tg_one()
    assert A.q("SELECT status FROM tg_out WHERE text='new1'", one=True)["status"] == "pending"
print("KNOWN TESTS PASSED")
