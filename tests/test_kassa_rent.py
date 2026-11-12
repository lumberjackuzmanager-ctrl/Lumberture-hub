"""12.11 (эгаси): ой итогида «Аренда» бўлими кўриниб турсин. Банкдан (перечисление) тўланган аренда ҳам кўринади;
бир катакда сўмда тўланган (+) ва $да тушган (−) аренда бир-биридан айирилмайди. Итоглар (харажат, касса) ўзгармайди."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, kassa as K
ym = "2030-05"
with A.app.app_context():
    K.ensure_month(ym)
    db = A.db()
    rent = A.q("SELECT id FROM k_items WHERE kind='exp' AND name='Аренда'", one=True)["id"]
    db.execute("INSERT INTO k_days(ym,day,rate) VALUES(?,4,10000)", (ym,))
    db.execute("INSERT INTO k_days(ym,day,rate) VALUES(?,6,10000)", (ym,))
    db.execute("INSERT INTO k_cells(ym,day,kind,ref_id,uzs,usd) VALUES(?,4,'exp',?,30000000,-1000)", (ym, rent))   # тўланди 3000$, тушди 1000$
    acc = A.q("SELECT id FROM erp_accounts ORDER BY id LIMIT 1", one=True)
    aid = acc["id"] if acc else db.execute("INSERT INTO erp_accounts(name,kind,active,sort) VALUES('Банк','bank',1,10)").lastrowid
    db.execute("INSERT INTO k_noncash(ym,day,acc_id,kind,ref_id,uzs,usd,note) VALUES(?,6,?,'exp',?,25000000,0,'перечисление')", (ym, aid, rent))
    db.commit(); K._memo.clear()
    mc = K.month_calc(ym)
    assert abs(mc["rent_paid"] - 3000) < 0.01 and abs(mc["rent_in_xl"] - 1000) < 0.01, (mc["rent_paid"], mc["rent_in_xl"])
    assert abs(mc["rent_bank"] - 2500) < 0.01 and abs(mc["rent_net"] - (mc["rent_in"] - 5500)) < 0.01, mc["rent_bank"]
    assert abs(mc["sum_exp"]) < 0.01                     # аренда харажат жамига кирмайди (аввалгидек)
    assert abs(mc["days"][3]["end"][0] - (-30000000)) < 1 and abs(mc["days"][3]["end"][1] - 1000) < 0.01   # нақд касса — аввалгидек
c = A.app.test_client()
with c.session_transaction() as s_: s_["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
h = c.get(f"/kassa/month?ym={ym}").get_data(as_text=True)
for t in ("Аренда, $", "Аренда кирим (арендаторлардан)", "Аренда тўланган (кассадан)", "Аренда тўланган (банкдан)", "Аренда натижа"):
    assert t in h, t
assert "$2 500" in h.split("Аренда тўланган (банкдан)")[1][:200] or "2,500" in h.split("Аренда тўланган (банкдан)")[1][:200] \
    or "2 500" in h.split("Аренда тўланган (банкдан)")[1][:200], h.split("Аренда тўланган (банкдан)")[1][:200]
r = c.get(f"/kassa/month/pdf?ym={ym}"); assert r.status_code == 200 and r.data[:4] == b"%PDF", r.status_code
print("KASSA RENT OK")
