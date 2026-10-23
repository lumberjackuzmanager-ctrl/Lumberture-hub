"""Операции → Связь и контакты: контакт (ходим эмас) қўшиш/белгилар/ходимга ўтказиш/ўчириш, ҳаволалар, токен яширин."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A
A.app.testing = True
c = A.app.test_client()
def post(d, **kw):
    with c.session_transaction() as s: s["csrf"] = "t"
    return c.post("/admin/connections", data=dict(d, csrf="t"), **kw)
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
with A.app.app_context():
    A.set_settings(raspil_bot_token="123456789:AAHsecretsecretsecretXYZ", raspil_bot_username="lumberture_workshop_bot")
h = c.get("/admin/connections").get_data(as_text=True)
assert "Связь и контакты" in h and "@lumberture_workshop_bot" in h and "AAHsecretsecretsecret" not in h and "lumberture.com" in h
assert post({"action": "cadd", "name": "Лочин", "phone": "+998 90 555 66 77", "d_owner": "1"}).status_code == 302
with A.app.app_context():
    r = A.q("SELECT * FROM ready_workers WHERE phone='998905556677'", one=True)
    assert r["owner"] == 1 and r["mgr"] == 0 and r["role"] == "ready" and r["kw_id"] is None
rid = str(r["id"])
post({"action": "cflag", "id": rid, "flag": "mgr"})
post({"action": "cname", "id": rid, "name": "Лочин ака"})
with A.app.app_context():
    r = A.q("SELECT * FROM ready_workers WHERE id=?", (rid,), one=True); assert r["mgr"] == 1 and r["name"] == "Лочин ака"
# ходимнинг рақамини контакт қилиб бўлмайди; ходимга ўтказиш
with A.app.app_context():
    db = A.db(); kw = db.execute("INSERT INTO k_workers(name,fio,sort,active) VALUES('Жасур ака','Жасуров Жасур',10,1)").lastrowid
    db.execute("INSERT INTO ready_workers(name,phone,created_at,role,kw_id) VALUES('Жасур','998901230000','x','staff',?)", (kw,)); db.commit()
r_ = post({"action": "cadd", "name": "X", "phone": "+998901230000"}, follow_redirects=True); assert "у сотрудника" in r_.get_data(as_text=True)
post({"action": "clink", "id": rid, "kw": str(kw)})
with A.app.app_context():
    assert A.q("SELECT kw_id FROM ready_workers WHERE id=?", (rid,), one=True)["kw_id"] == kw
# ҳаволалар
post({"action": "ladd", "kind": "site", "name": "Test", "url": "@lumberture_test"})
with A.app.app_context():
    ls = json.loads(A.get_setting("links")); assert ls[-1]["url"] == "https://t.me/lumberture_test" and len(ls) == 5
post({"action": "ldel", "i": "4"})
with A.app.app_context():
    assert len(json.loads(A.get_setting("links"))) == 4
r_ = post({"action": "ladd", "kind": "site", "url": "javascript:alert(1)"}, follow_redirects=True); assert "https://" in r_.get_data(as_text=True)
# ўчириш (фақат ходим бўлмаган)
post({"action": "cadd", "name": "Офис", "phone": "+998334096070"})
with A.app.app_context():
    oid = A.q("SELECT id FROM ready_workers WHERE phone='998334096070'", one=True)["id"]
post({"action": "cdel", "id": str(oid)})
post({"action": "cdel", "id": rid})
with A.app.app_context():
    assert not A.q("SELECT 1 FROM ready_workers WHERE id=?", (oid,), one=True)
    assert A.q("SELECT 1 FROM ready_workers WHERE id=?", (rid,), one=True), "ходим рақами ўчмаслиги керак"
print("CONNECTIONS OK")
