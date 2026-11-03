"""Мижоз карточкасида телефон ўзгарса — хабар/ҳисобот ёзувидаги рақам ҳам янгиланади, эски Telegram боғланиши тозаланади (03.11)."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp as E
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok(); assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
with A.app.app_context():
    con = A.db()
    con.execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc, lim, ckind, src) VALUES('FR','Сайдулла Базис','97 774-05-83',0,0,' сайдулла базис ',0,'client','1c')")
    con.execute("INSERT INTO clients(name, phone, active, sms_enabled, tg_code, is_demo, created_at, onec_code, tg_status, tg_user_id, tg_chat) "
                "VALUES('Сайдулла Базис', '+998 97 774 05 83', 1, 0, 'x1', 0, '2026-10-01', '1c77:FR', 'ok', 93705732, '')")
    con.commit()
# номи ўзгарди, телефон эмас — хабар ёзуви тегилмайди
tok(); assert c.post("/erp/clients/FR/edit", data={"csrf": "t", "name": "Сайдулла Базис", "phones": "97 774-05-83"}).status_code == 302
with A.app.app_context():
    r = A.db().execute("SELECT * FROM clients WHERE onec_code='1c77:FR'").fetchone()
    assert r["phone"] == "+998 97 774 05 83" and r["tg_user_id"] == 93705732
tok(); assert c.post("/erp/clients/FR/edit", data={"csrf": "t", "name": "Сайдулла Базис", "phones": "+998909705667"}).status_code == 302
with A.app.app_context():
    r = A.db().execute("SELECT * FROM clients WHERE onec_code='1c77:FR'").fetchone()
    assert r["phone"] == "+998 90 970 56 67" and r["tg_user_id"] is None and (r["tg_status"] or "") == "", dict(r)
    assert A.db().execute("SELECT COUNT(*) FROM erp_log WHERE action='notif_phone'").fetchone()[0] == 1
print("NOTIF PHONE OK")
