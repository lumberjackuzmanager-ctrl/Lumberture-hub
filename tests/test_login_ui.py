"""1С'дагидек кириш: офис ичида фойдаланувчилар рўйхати, охирги кирган эсда қолади; меню қатори; иш столи белгиси."""
import os, sys, tempfile, base64, re
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A
A.app.testing = True
c = A.app.test_client()
h = c.get("/login").get_data(as_text=True)
assert '<select name="username"' in h and '<option value="admin"' in h, h[:600]
h = c.get("/login", environ_base={"REMOTE_ADDR": "8.8.8.8"})
assert h.status_code in (200, 403) and '<select name="username"' not in h.get_data(as_text=True)
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
assert r.status_code == 302 and "lu=admin" in r.headers.get("Set-Cookie", "")
h = c.get("/erp").get_data(as_text=True)
assert "Документы</button>" not in h                                   # 27.10: «Документы» бўлими йўқ — янги ҳужжат журналдан
for t in ("Справочники", "Журналы", "Отчеты", "Операции", "Накладные", "Из Базиса", "/erp/rep/stock", "/erp/rep/mutual", "Удаление помеченных объектов"):
    assert t in h, t
c.get("/logout")
h = c.get("/login").get_data(as_text=True)
assert '<option value="admin" selected>' in h
b = c.get("/setup/shortcut.bat")
assert b.status_code == 200 and b.headers["Content-Disposition"].endswith(".bat")
bt = b.get_data(as_text=True)
assert "--app=" in bt and "Google\\Chrome\\Application\\chrome.exe" in bt and "Lumberture Hub.lnk" in bt and "cscript" in bt, bt
assert "powershell" not in bt.lower()
assert c.get("/setup/shortcut.bat", environ_base={"REMOTE_ADDR": "8.8.8.8"}).status_code in (403, 404)
# кассир: меню йўқ
with A.app.app_context():
    from werkzeug.security import generate_password_hash
    A.db().execute("INSERT INTO users(username,password_hash,role) VALUES('kas1',?, 'kassa')", (generate_password_hash("Kassa12345"),)); A.db().commit()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "kas1", "password": "Kassa12345", "csrf": "t"})
h = c.get("/kassa").get_data(as_text=True)
assert 'class="menubar"' not in h
print("LOGIN UI OK")
