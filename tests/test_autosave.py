"""31.10 (эгаси): автосохранение — ток ўчса / платформа чиқариб юборса иш йўқолмасин. Браузер нусхаси (lbautosave.js),
«Восстановить» — сервер шу маълумот билан саҳифани қайта чизади, базага ҳеч нарса ёзилмайди; /api/ping — сессия узилмасин."""
import os, sys, tempfile, json, re, subprocess, datetime, time
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app as A, roles as R
from werkzeug.security import generate_password_hash
A.app.testing = True
t = datetime.date.today().isoformat()
with A.app.app_context():
    db = A.db()
    db.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES('G1','G1','ЛДСП Белый 16мм','лист',2,40,'',0,0,' лдсп белый 16мм ','cat','good',1)")
    db.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (t, t + " 09:00:00"))
    db.execute("INSERT INTO users(username,password_hash,role,active) VALUES('bz5',?,'basis',1)", (generate_password_hash("Basis12345x"),))
    db.commit(); R.reload()
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
h = c.get("/erp/sale/new").get_data(as_text=True)
assert "lbautosave.js" in h and '"sale:new"' in h and "server:true" in h
with A.app.app_context():
    n0 = A.q("SELECT COUNT(*) n FROM erp_docs", one=True)["n"]
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post("/erp/sale/new", data={"csrf": "t", "act": "restore", "date": t, "rate": "12000", "nal": "1", "good": ["G1"], "qty": ["7"], "price": ["40"], "lnote": [""]})
p = r.get_data(as_text=True)
m = re.search(r"var INIT=(\[.*?\]), tb=", p)
assert r.status_code == 200 and "Восстановлено из автосохранения" in p and m, p[:300]
L = json.loads(m.group(1)); assert L and L[0]["good"] == "G1" and str(L[0]["qty"]) in ("7", "7.0"), L
assert "restored:true" in p
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post("/erp/receipt/new2", data={"csrf": "t", "act": "restore", "date": t, "lines_json": json.dumps([{"good": "G1", "qty": "3", "price": "35"}])})
assert r.status_code == 200 and "Восстановлено из автосохранения" in r.get_data(as_text=True)
with A.app.app_context():
    assert A.q("SELECT COUNT(*) n FROM erp_docs", one=True)["n"] == n0            # базага ҳеч нарса ёзилмади
h = c.get("/kassa").get_data(as_text=True)
assert "lbautosave.js" in h and "KASRESTORE" in h and '"kassa:' in h
assert c.get("/api/ping").get_json() == {"ok": True}
b = A.app.test_client()                                                         # чекланган роль ҳам
with b.session_transaction() as s: s["csrf"] = "t"
assert b.post("/login", data={"username": "bz5", "password": "Basis12345x", "csrf": "t"}).status_code == 302
assert b.get("/api/ping").status_code == 200
assert subprocess.run(["node", "--check", os.path.join(ROOT, "static", "lbautosave.js")]).returncode == 0
print("AUTOSAVE OK")
