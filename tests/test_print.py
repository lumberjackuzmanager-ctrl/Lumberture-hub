"""Платформанинг «Печать» ойнаси: PDF тайёрлаш (url/html), олдиндан кўриш, юклаб олиш, принтерга юбориш (сохта принтер), бегона иш — 404."""
import os, sys, tempfile, io, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, printsvc, blank
A.app.testing = True
sent = []
printsvc.printers = lambda: (["HP LaserJet", "PDF"], "HP LaserJet")
printsvc.print_pdf = lambda pdf, pr, copies=1, pages=None, title="": sent.append((pr, copies, pages, title))
_fake_pdf = blank.render_text("Тест", "\n".join("Строка %d" % i for i in range(150)), no="T", sign=False)
calls = []
def fake_h2p(html, base):
    calls.append((html, base)); return _fake_pdf
if not os.environ.get("REAL_BROWSER"):
    printsvc.html_to_pdf = fake_h2p
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
H = {"X-CSRF-Token": "t"}
_post = c.post
def _p(*a, **kw):
    with c.session_transaction() as s: s["csrf"] = "t"
    return _post(*a, **kw)
c.post = _p
# CSRF'сиз — рад
assert c.post("/api/print/prepare", json={"html": "<p>x</p>"}).status_code in (400, 403)
# 1) экрандаги HTML
r = c.post("/api/print/prepare", json={"html": "<!doctype html><html><head></head><body><h1>Накладная</h1></body></html>", "base": "/erp/doc/", "title": "Накладная 5"}, headers=H)
assert r.status_code == 200, r.data[:300]
j = r.get_json(); assert j["pages"] >= 1 and j["printers"] == ["HP LaserJet", "PDF"] and j["default"] == "HP LaserJet" and j["can_print"]
if calls: assert calls[-1][1].endswith("/erp/doc/")
assert c.get(f"/api/print/{j['id']}/p/1.png").data[:4] == b"\x89PNG"
assert c.get(f"/api/print/{j['id']}/p/99.png").status_code == 404
r = c.get(f"/api/print/{j['id']}.pdf?dl=1"); assert r.data[:4] == b"%PDF" and "attachment" in r.headers["Content-Disposition"]
# 2) принтерга
r = c.post("/api/print/send", json={"id": j["id"], "printer": "PDF", "copies": "2", "pages": "1-1, 9"}, headers=H)
assert r.status_code == 200 and sent[-1] == ("PDF", 2, [0], "Накладная 5"), (r.data, sent)
assert c.post("/api/print/send", json={"id": j["id"], "printer": "Nope"}, headers=H).status_code == 400
with A.app.app_context(): assert A.get_setting("print_last_printer") == "PDF"
# 3) url: PDF қайтарадиган саҳифа ўзи олинади; HTML саҳифа — браузер орқали
r = c.post("/api/print/prepare", json={"url": "/docs/blank/sample.pdf"}, headers=H); assert r.status_code == 200, r.data
n0 = len(calls)
r = c.post("/api/print/prepare", json={"url": "/reports"}, headers=H); assert r.status_code == 200, r.data
if not os.environ.get("REAL_BROWSER"): assert len(calls) == n0 + 1 and "__LB_HEADLESS" not in calls[-1][0]
for bad in ("https://evil.com/x", "//evil.com", "/api/print/printers"):
    assert c.post("/api/print/prepare", json={"url": bad}, headers=H).status_code == 400
# 4) бошқа фойдаланувчи бу ишни кўрмайди
with A.app.app_context():
    A.db().execute("INSERT INTO users(username, password_hash, role) VALUES('u2', ?, 'admin')", (A.generate_password_hash("pw123456"),)); A.db().commit()
c2 = A.app.test_client()
with c2.session_transaction() as s: s["csrf"] = "t"
assert c2.post("/login", data={"username": "u2", "password": "pw123456", "csrf": "t"}).status_code == 302
assert c2.get(f"/api/print/{j['id']}.pdf").status_code == 404
# 5) parse_pages
assert printsvc.parse_pages("", 3) == [0, 1, 2] and printsvc.parse_pages("3,1-2,7", 3) == [2, 0, 1]
# 6) саҳифаларда скрипт уланган, браузер ойнаси чақирувлари йўқ
for u in ("/", "/docs"):
    t = c.get(u).get_data(as_text=True); assert "lbprint.js" in t and "LBCSRF" in t
print("PRINT OK")
