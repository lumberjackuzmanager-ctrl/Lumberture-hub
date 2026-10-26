"""3-қадам: Поступление товара / Поступление денег / Переоценка платформада — сақлаш, проведен, қолдиқ, қарз, нарх, нусха, изоҳлар."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp
A.app.testing = True
erp.stock_now = lambda: {"G1": 5.0}
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok(); c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
with A.app.app_context():
    con = A.db()
    for e, n, cur, pr in (("G1", "ЛДСП Белый", 2, 40), ("G2", "Кромка 19/1", 1, 700)):
        con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,src,price_last) VALUES(?,?,?,'шт',?,?,30,'0',0,0,?,'1c',0)",
                    (e, e, n, cur, pr, erp.lc(n)))
    for e, n in (("S1", "Жомий Эман база"), ("C1", "Мижоз Бир"), ("C2", "Мижоз Икки")):
        con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,debt1c) VALUES(?,?,?,'','','0',0,0,?,'1c',?)",
                    (e, e, n, erp.lc(n), 100.0 if e == "C1" else 0))
    con.execute("UPDATE erp_clients SET ckind='supplier' WHERE ext='S1'")
    A.set_settings(fx_manual="12000") if hasattr(A, "set_settings") else None
    con.execute("INSERT INTO fx_rates(rate, source, fetched_at) VALUES(12000, 'manual', ?)", (A.now(),)) if A.q("SELECT name FROM sqlite_master WHERE name='fx_rates'") else None
    con.commit()
    rate = erp.day_rate()[0]
    print("rate", rate)
for k in ("receipt", "pay", "reval"):
    h = c.get(f"/erp/{k}/new2").get_data(as_text=True)
    assert erp.DOC2[k] in h and "lines_json" in h, k
def post(url, lines, act="post", **kw):
    tok(); return c.post(url, data=dict(csrf="t", act=act, date=__import__("datetime").date.today().isoformat(), lines_json=json.dumps(lines), **kw))
# приход: қолдиқ ошади
r = post("/erp/receipt/new2", [{"good": "G1", "qty": "10", "price": "41", "note": "", "cnotes": {"price": "чегирма билан"}}], client="S1", note="Окажон олиб келди")
assert r.status_code == 302, __import__("re").findall(r"class=\"flash[^>]*>([^<]*)", r.get_data(as_text=True))
with A.app.app_context():
    con = A.db()
    d = A.q("SELECT * FROM erp_docs WHERE kind='receipt' AND src='pf'", one=True)
    assert d["status"] == "posted" and d["usd"] == 410 and d["client"] == "S1" and d["no"] == "1", dict(d)
    l = A.q("SELECT * FROM erp_lines WHERE doc_id=?", (d["id"],), one=True)
    assert json.loads(l["cnotes"]) == {"price": "чегирма билан"}
    assert erp.stock_all(con)["G1"] == 15.0
    rid = d["id"]
# поставщиксиз — хато
r = post("/erp/receipt/new2", [{"good": "G1", "qty": "1", "price": "1"}])
assert r.status_code == 200 and "Поставщик" in r.get_data(as_text=True)
# тўлов: қарз камаяди, манфий — ортади
r = post("/erp/pay/new2", [{"client": "C1", "usd": "30", "uzs": "120000", "note": "", "cnotes": {"uzs": "Карта АСАКА"}}, {"client": "C2", "usd": "-5", "uzs": ""}], note="касса")
assert r.status_code == 302, r.get_data(as_text=True)[:400]
with A.app.app_context():
    con = A.db()
    rate = erp.day_rate()[0] or 1
    assert abs(erp.client_debt(con, "C1") - round(100 - (30 + 120000 / rate), 2)) < 0.02, erp.client_debt(con, "C1")
    assert erp.client_debt(con, "C2") == 5.0
    pid = A.q("SELECT id FROM erp_docs WHERE kind='pay' AND src='pf'", one=True)[0]
# тўлов ҳужжатида ҳар мижознинг жами қарзи кўриниб туради (26.10)
h = c.get(f"/erp/doc/{pid}").get_data(as_text=True)
assert 'id="pcl"' in h and "Жами қарзи:" in h and 'class="pcrow" data-c="C2"' in h and '"debt": 5.0' in h, h[h.find('id="pcl"'):h.find('id="pcl"') + 800]
# черновик ҳисобга кирмайди
with A.app.app_context():
    A.db().execute("UPDATE erp_docs SET status='draft' WHERE id=?", (pid,)); A.db().commit()
    assert erp.client_debt(A.db(), "C1") == 100.0
    A.db().execute("UPDATE erp_docs SET status='posted' WHERE id=?", (pid,)); A.db().commit()
# переоценка: янги нарх таклиф қилинади
r = post("/erp/reval/new2", [{"good": "G1", "qty": "", "price2": "40", "price": "45"}])
assert r.status_code == 302
j = c.get("/erp/api/goods?src=all&q=ЛДСП").get_json()
assert j[0]["price"] == 45, j
# таҳрирлаш (версия) ва нусха
with A.app.app_context():
    v = A.q("SELECT updated_at FROM erp_docs WHERE id=?", (rid,), one=True)[0]
h = c.get(f"/erp/doc/{rid}/edit2").get_data(as_text=True)
assert '"good": "G1"' in h and json.dumps("чегирма билан") in h
r = post(f"/erp/doc/{rid}/edit2", [{"good": "G1", "qty": "12", "price": "41"}], client="S1", version=v)
assert r.status_code == 302
with A.app.app_context():
    assert erp.stock_all(A.db())["G1"] == 17.0
h = c.get(f"/erp/receipt/new2?copy={rid}").get_data(as_text=True)
assert '"good": "G1"' in h and "Жомий Эман база" in h and 'name="version" value=""' in h
# ҳужжат кўриниши ва журнал
h = c.get(f"/erp/doc/{rid}").get_data(as_text=True)
assert "Нусха олиш" in h and "edit2" in h
h = c.get("/erp?kind=receipt").get_data(as_text=True)
assert "Выбор вида документа" in h and "data-copy=" in h
h = c.get("/erp?kind=pay").get_data(as_text=True)
assert "+ Поступление денег" in h or "Поступление денег" in h
# ўчириш ва проведен эмас
tok(); c.post(f"/erp/doc/{rid}/status", data={"csrf": "t", "act": "unpost"})
with A.app.app_context():
    assert erp.stock_all(A.db())["G1"] == 5.0
print("DOC2 OK")
