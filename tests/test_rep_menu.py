"""30.10 (эгаси): кассирда (ва бошқа чекланган ролларда) ҳисоботлар — тепада алоҳида «Отчеты» менюси («Ещё» ичида эмас);
«Отчет по накладным»да мижоз майдони — справочникдан рўйхат (браузер тарихи эмас)."""
import os, sys, tempfile, json, re, time
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, roles as R
from werkzeug.security import generate_password_hash
A.app.testing = True
with A.app.app_context():
    db = A.db()
    db.execute("UPDATE roles SET kind='full', sections=?, can_post=1 WHERE key='kassa'", (json.dumps(["pickup"]),))
    db.execute("INSERT INTO users(username,password_hash,role,active) VALUES('kas7',?,'kassa',1)", (generate_password_hash("Kassa12345x"),))
    db.execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc) VALUES('K1','insta_brend_mebel06','+998 90 111 22 33',0,0,' insta_brend_mebel06 901112233 ')")
    db.commit(); R.reload()
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "kas7", "password": "Kassa12345x", "csrf": "t"}).status_code == 302
h = c.get("/work").get_data(as_text=True)
tops = re.findall(r'<button type="button" class="mmt"[^>]*>.*?</svg>\s*([^<]+)</button>', h, re.S) or re.findall(r'class="mmt"[^>]*>(?:<[^>]+>)*([^<]+)</button>', h)
tops = [t.strip() for t in tops]
assert "Отчеты" in tops and "Ещё" in tops and tops.index("Отчеты") < tops.index("Ещё"), tops
i = h.find(">Отчеты</button>"); menu = h[i:h.find("</div></div>", i)]
for t in ("Остаток товаров", "Отчет по накладным", "Отчет по приходу", "Взаиморасчеты"):
    assert t in menu, (t, menu[:500])
j = h.find(">Ещё</button>"); more = h[j:]
assert "Отчет по накладным" not in more[:more.find("</div></div></div>")]
r = c.get("/erp/rep/sales?mode=lines")
assert r.status_code == 200, r.status_code
p = r.get_data(as_text=True)
assert 'id="mq" name="c"' in p and 'autocomplete="off"' in p and 'id="mcx"' in p and "erp/api/clients" in p.replace("\\/", "/")
assert c.get("/erp/api/clients?q=insta").get_json()[0]["ext"] == "K1"
assert "kind=supplier" in c.get("/erp/rep/receipts").get_data(as_text=True)
print("REP MENU OK")
