"""Платформа савдо базаси: 1С кўзгуси + платформа накладнойи. RENT_C1_TEST=1С нусхаси (бўлмаса — ўтказиб юборилади)."""
import os, sys, tempfile, time
p = os.environ.get("RENT_C1_TEST", r"C:\new base")
if not os.path.isfile(os.path.join(p, "1Cv7.DD")):
    print("ERP TEST SKIPPED (нет копии базы)"); sys.exit(0)
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, c1, erp
with A.app.app_context():                       # тестларда «cashier» — аввалгидек кенг роль (созламалардан ташқари ҳаммаси, проведен қилади)
    A.db().execute("UPDATE roles SET kind='full', sections='[]', can_post=1 WHERE key='cashier'"); A.db().commit()
    import roles as _roles_t; _roles_t.reload()
from datetime import date
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok()
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
with A.app.app_context():
    A.set_settings(edit_lock_days="0")          # синовда кунлар ёпилмасин (пастда алоҳида текширилади)
tok(); assert c.post("/1c/settings", data={"c1_path": p, "c1_doc_notify": "1", "csrf": "t"}).status_code == 302

t0 = time.time()
with A.app.app_context():
    st = erp.sync(force=True); print("sync", st, round(time.time() - t0, 1), "s")
    assert st["goods"] > 100 and st["clients"] > 10 and st["new"] > 1000
    assert erp.sync() == dict(skipped=True)
    st2 = erp.sync(force=True); assert st2["new"] == 0 and st2["upd"] == 0 and st2["gone"] == 0, st2
    con = A.db()
    # кўзгу 1С билан бир хил: сотув суммалари
    d1, d2 = date(2026, 1, 1), date(2026, 12, 31)
    s1 = [x for x in c1.sales(d1, d2) if x["doc"]["posted"] and not x["doc"]["deleted"]]
    usd1 = round(sum(x["usd"] for x in s1), 2)
    usd2 = A.scalar("SELECT ROUND(SUM(usd),2) FROM erp_docs WHERE kind='sale' AND src='1c' AND status='posted' AND date BETWEEN ? AND ?", (d1.isoformat(), d2.isoformat()))
    assert abs(usd1 - usd2) < 0.05, (usd1, usd2)
    sale = next(x for x in s1 if x["usd"] > 1)
    sd = c1.sale_doc(sale["doc"]["id"])
    row = A.q("SELECT * FROM erp_docs WHERE ext=?", (sale["doc"]["id"],), one=True)
    ls = A.q("SELECT * FROM erp_lines WHERE doc_id=? ORDER BY lineno", (row["id"],))
    assert len(ls) == len(sd["lines"]) and abs(sum(l["uzs"] for l in ls) - row["uzs"]) < 1
    # 1С формуласи сатрларда тўғри
    bad = 0
    for l in A.q("SELECT l.*, d.rate, g.cur FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id JOIN erp_goods g ON g.ext=l.good "
                 "WHERE d.kind='sale' AND d.date>='2026-09-01' AND d.status='posted' AND g.cur IN (1,2)"):
        cc = erp.calc_line(l["cur"], l["qty"], l["price"], l["rate"])
        if abs(cc["uzs"] - l["uzs"]) > 1 or abs(cc["usd"] - l["usd"]) > 0.02:
            bad += 1
    print("formula mismatches", bad)
    tot = A.scalar("SELECT COUNT(*) FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.kind='sale' AND d.date>='2026-09-01'")
    assert bad <= tot * 0.005, (bad, tot)
    for k in ("receipt", "pay", "reval"):
        assert A.scalar("SELECT COUNT(*) FROM erp_docs WHERE kind=?", (k,)) > 0, k
    cl = A.q("SELECT ext, name FROM erp_clients WHERE folder=0 AND deleted=0 LIMIT 1", one=True)
    g2 = A.q("SELECT * FROM erp_goods WHERE folder=0 AND deleted=0 AND cur=2 AND price>0 LIMIT 1", one=True)
    g1 = A.q("SELECT * FROM erp_goods WHERE folder=0 AND deleted=0 AND cur=1 AND price>0 LIMIT 1", one=True)

for u in ("/erp", "/erp?kind=receipt", "/erp?kind=pay&src=1c", "/erp?kind=reval&d1=2024-01-01", "/erp/goods", "/erp/goods?q=кромка",
          "/erp/sale/new", f"/erp/doc/{row['id']}", f"/erp/doc/{row['id']}/print?form=full"):
    r = c.get(u); assert r.status_code == 200, (u, r.status_code)
assert c.get(f"/erp/doc/{row['id']}/edit").status_code == 302          # 1С ҳужжатини ўзгартириб бўлмайди
j = c.get("/erp/api/goods?src=all&q=" + g2["name"][:5]).get_json(); assert any(x["ext"] == g2["ext"] for x in j) or len(j) == 40
assert c.get("/erp/api/clients?q=" + cl["name"][:4]).get_json()

# янги накладной
form = {"csrf": "t", "client": cl["ext"], "rate": "12000", "nal": "1", "note": "синов", "act": "post", "pay_usd": "10", "pay_uzs": "120000",
        "date": "2026-10-16",
        "good": [g2["ext"], g1["ext"], ""], "qty": ["2", "3", ""], "price": [str(g2["price"]), "5000", ""], "lnote": ["оқ", "", ""]}
tok(); r = c.post("/erp/sale/new", data=form); assert r.status_code == 302, r.get_data(as_text=True)[:300]
nid = int(r.headers["Location"].rstrip("/").split("/")[-1])
with A.app.app_context():
    d = A.q("SELECT * FROM erp_docs WHERE id=?", (nid,), one=True)
    mx = max(int(x[0]) for x in A.q("SELECT no FROM erp_docs WHERE kind='sale' AND substr(date,1,4)=? AND id<>?", (str(date.today().year), nid)) if str(x[0]).isdigit())
    assert d["no"] == str(mx + 1), (d["no"], mx)
    no1 = d["no"]
    assert d["status"] == "posted" and d["src"] == "pf" and d["created_by"] == "admin"
    exp = round(2 * g2["price"] + 3 * 5000 / 12000, 2)
    assert abs(d["usd"] - exp) < 0.02, (d["usd"], exp)
    assert abs(d["uzs"] - (round(2 * g2["price"], 2) * 12000 + 15000)) < 1
    assert A.q("SELECT note FROM erp_lines WHERE doc_id=? AND lineno=1", (nid,), one=True)[0] == "оқ"
    assert d["pay_usd"] == 10 and d["pay_uzs"] == 120000 and d["paid"] == 20 and d["date"] == "2026-10-16"
    # 1С кўзгусида ҳам тўлов (СуммаОплатыСум1) бор
    assert A.scalar("SELECT COUNT(*) FROM erp_docs WHERE src='1c' AND kind='sale' AND paid>0") > 100
h = c.get(f"/erp/doc/{nid}/print?form=full").get_data(as_text=True); assert no1 in h and g1["name"][:10] in h
for lg in ("uz", "en", "ru"):                          # чоп этиш тил ўзгарса ҳам русча (26.10: «Razresheniye» чиқаётган эди)
    c.get(f"/lang/{lg}?next=/")
    for fm, ttl in (("perm", "Разрешение №"), ("usd", "Накладная №")):
        h = c.get(f"/erp/doc/{nid}/print?form={fm}").get_data(as_text=True)
        import re as _re
        body = _re.sub(r'<div class="bar">.*?</div>', "", h, flags=_re.S)      # экрандаги тугмалар — тилда
        assert ttl in body and not _re.findall(r"Razresh\w*|Nakladn\w*|Permit\b|Invoice\b", body), (lg, fm, _re.findall(r".{0,60}(?:Razresh|Nakladn|Permit\b|Invoice\b).{0,20}", body))
with A.app.app_context():
    A.db().execute("UPDATE users SET ui_lang=NULL"); A.db().commit()
with c.session_transaction() as s_: s_.pop("ui_lang", None)
# хатолар
tok(); r = c.post("/erp/sale/new", data=dict(form, client="")); assert "Мижоз танланмаган" in r.get_data(as_text=True)
tok(); r = c.post("/erp/sale/new", data=dict(form, good=["", ""], qty=["", ""], price=["", ""], lnote=["", ""])); assert "Битта ҳам товар йўқ" in r.get_data(as_text=True)
tok(); r = c.post("/erp/sale/new", data=dict(form, qty=["0", "3", ""])); assert "сони нотўғри" in r.get_data(as_text=True)
tok(); r = c.post("/erp/sale/new", data=dict(form, price=["0", "5000", ""])); assert "нарх киритилмаган" in r.get_data(as_text=True)
with A.app.app_context():
    assert A.scalar("SELECT COUNT(*) FROM erp_goods WHERE price=0 AND price_last>0") > 100
# таҳрирлаш: версия ва қулф
assert c.get(f"/erp/doc/{nid}/edit").status_code == 200
with A.app.app_context():
    ver = A.q("SELECT updated_at FROM erp_docs WHERE id=?", (nid,), one=True)[0]
    assert A.q("SELECT lock_by FROM erp_docs WHERE id=?", (nid,), one=True)[0] == "admin"
tok(); r = c.post(f"/erp/doc/{nid}/edit", data=dict(form, version="эски")); assert "саҳифани янгиланг" in r.get_data(as_text=True)
with A.app.app_context():
    A.db().execute("UPDATE erp_docs SET lock_by='Жасур', lock_at=? WHERE id=?", (time.time(), nid)); A.db().commit()
tok(); r = c.post(f"/erp/doc/{nid}/edit", data=dict(form, version=ver)); assert r.status_code == 423
r = c.get(f"/erp/doc/{nid}"); _t = __import__("re").sub(r"<script.*?</script>", "", r.get_data(as_text=True), flags=16); assert r.status_code == 423 and "Запись заблокирована" in _t and "<b>Жасур</b>" in _t and "Чоп этиш" not in _t
assert c.get(f"/erp/doc/{nid}/print").status_code == 423 and c.get(f"/erp/doc/{nid}/edit").status_code == 423
assert "банд: Жасур" in c.get("/erp?src=pf&d1=2026-01-01", follow_redirects=True).get_data(as_text=True)
tok(); assert c.post(f"/erp/doc/{nid}/lock", data={"csrf": "t"}).get_json()["ok"] is False
with A.app.app_context():
    A.db().execute("UPDATE erp_docs SET lock_at=? WHERE id=?", (time.time() - 500, nid)); A.db().commit()   # эски қулф ўтиб кетади
tok(); r = c.post(f"/erp/doc/{nid}/edit", data=dict(form, version=ver, qty=["5", "3", ""], act="save")); assert r.status_code == 302
with A.app.app_context():
    d = A.q("SELECT * FROM erp_docs WHERE id=?", (nid,), one=True); assert d["status"] == "draft" and d["lock_by"] is None
    assert abs(d["usd"] - round(5 * g2["price"] + 3 * 5000 / 12000, 2)) < 0.02
tok(); assert c.post(f"/erp/doc/{nid}/status", data={"csrf": "t", "act": "delete"}).status_code == 302
tok(); r = c.post("/erp/sale/new", data=form); assert r.status_code == 302
with A.app.app_context():
    assert A.q("SELECT no FROM erp_docs WHERE src='pf' ORDER BY id DESC LIMIT 1", one=True)[0] == str(int(no1) + 1)   # ўчирилган ҳам рақамни банд қилади
    acts = [x[0] for x in A.q("SELECT action FROM erp_log WHERE doc_id=? ORDER BY id", (nid,))]
    assert acts == ["create+post", "save", "delete"], acts
    # кейинги 1С кўчириш платформа ҳужжатларига тегмайди
    erp.sync(force=True)
    assert A.scalar("SELECT COUNT(*) FROM erp_docs WHERE src='pf'") == 2
with A.app.app_context():
    # 1С ҳам шу рақамни олди -> платформа ҳужжатига кейинги бўш рақам
    no2 = str(int(no1) + 1)
    A.db().execute("INSERT INTO erp_docs(ext,kind,no,date,time,status,src) VALUES('TEST-COLL','sale',?,?,'10:00:00','posted','1c')", (no2, date.today().isoformat()))
    assert erp.fix_collisions(A.db()) == [(no2, str(int(no2) + 1))]
    A.db().commit()
    assert A.q("SELECT no FROM erp_docs WHERE src='pf' ORDER BY id DESC LIMIT 1", one=True)[0] == str(int(no2) + 1)
    assert erp.fix_collisions(A.db()) == []
    assert erp.next_no(A.db(), "sale", 2030) == "1"
h = c.get("/erp?src=pf&d1=2026-01-01", follow_redirects=True).get_data(as_text=True)
assert str(int(no1) + 2) in h and "кейинги накладной рақами: <b>" + str(int(no1) + 3) in h

# ---- кун курси ботдан: менежер ўзгартиролмайди, раҳбар ўзгартира олади
from werkzeug.security import generate_password_hash
with A.app.app_context():
    A.db().execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12090,'bot',?,?,'',0)",
                   (date.today().isoformat(), date.today().isoformat() + " 09:00:13"))
    A.db().execute("INSERT INTO users(username,password_hash,full_name,role,weak_pw) VALUES('jasur',?,'Жасур','cashier',0)",
                   (generate_password_hash("Jasur12345"),)); A.db().commit()
    assert erp.day_rate()[0] == 12090
assert "12090" in c.get("/erp/sale/new").get_data(as_text=True)
assert c.get("/erp/api/rate?d=" + date.today().isoformat()).get_json()["rate"] == 12090
m = A.app.test_client()
with m.session_transaction() as ss: ss["csrf"] = "t"
assert m.post("/login", data={"username": "jasur", "password": "Jasur12345", "csrf": "t"}).status_code == 302
with m.session_transaction() as ss: ss["csrf"] = "t"
assert "readonly" in m.get("/erp/sale/new").get_data(as_text=True)
with m.session_transaction() as ss: ss["csrf"] = "t"
r = m.post("/erp/sale/new", data=dict(form, rate="99999", date=date.today().isoformat())); import re as _re; assert r.status_code == 302, _re.findall(r"flash[^>]*>([^<]*)", r.get_data(as_text=True))
mid = int(r.headers["Location"].rstrip("/").split("/")[-1])
with A.app.app_context():
    assert A.q("SELECT rate, created_by FROM erp_docs WHERE id=?", (mid,), one=True)[:] == (12090, "jasur")
# «СРОЧНО» рухсати (26.10): олинса — тугма ўчиқ, сервер ҳам белгини қабул қилмайди; берилса — қўяди
with A.app.app_context():
    A.db().execute("UPDATE users SET p_urgent=0 WHERE username='jasur'"); A.db().commit()
h = m.get("/erp/sale/new").get_data(as_text=True); assert 'id="urgb" title="Нет доступа' in h and "disabled>СРОЧНО" in h
with m.session_transaction() as ss: ss["csrf"] = "t"
r = m.post("/erp/sale/new", data=dict(form, urgent="1", urgent_free="1", date=date.today().isoformat())); assert r.status_code == 302
uid_ = int(r.headers["Location"].rstrip("/").split("/")[-1])
with A.app.app_context():
    assert A.q("SELECT urgent, urgent_free FROM erp_docs WHERE id=?", (uid_,), one=True)[:] == (0, 0)
    A.db().execute("UPDATE users SET p_urgent=1 WHERE username='jasur'"); A.db().commit()
assert "disabled>СРОЧНО" not in m.get("/erp/sale/new").get_data(as_text=True)
with m.session_transaction() as ss: ss["csrf"] = "t"
r = m.post("/erp/sale/new", data=dict(form, urgent="1", date=date.today().isoformat())); assert r.status_code == 302, r.status_code
with A.app.app_context():
    assert A.q("SELECT urgent FROM erp_docs WHERE id=?", (int(r.headers["Location"].rstrip("/").split("/")[-1]),), one=True)[0] == 1
h = c.get("/erp", follow_redirects=True).get_data(as_text=True); assert "Бугунги курс (бот): <b>12 090" in h
print("RATE OK")

# ---- товар/мижоз: қўшиш — ҳаммага, ўзгартириш/ўчириш — фақат админга
with m.session_transaction() as ss: ss["csrf"] = "t"
r = m.post("/erp/goods/new", data={"csrf": "t", "name": "Синов товар Т1", "cat": "good", "unit": "шт", "cur": "1", "price": "25000"}); assert r.status_code == 302
with A.app.app_context():
    gx = A.q("SELECT * FROM erp_goods WHERE name='Синов товар Т1'", one=True); assert gx["src"] == "pf" and gx["ext"].startswith("P") and gx["price"] == 25000
assert any(x["ext"] == gx["ext"] for x in m.get("/erp/api/goods?src=all&q=синов т1").get_json())
with m.session_transaction() as ss: ss["csrf"] = "t"
assert "аллақачон бор" in m.post("/erp/goods/new", data={"csrf": "t", "name": "синов товар т1", "cat": "good"}).get_data(as_text=True)
r = m.get(f"/erp/goods/{gx['ext']}/edit", follow_redirects=True); assert "доступ йўқ" in r.get_data(as_text=True)
with m.session_transaction() as ss: ss["csrf"] = "t"
r = m.post(f"/erp/goods/{gx['ext']}/edit", data={"csrf": "t", "name": "Бузилган"}, follow_redirects=True); assert "доступ йўқ" in r.get_data(as_text=True)
with m.session_transaction() as ss: ss["csrf"] = "t"
r = m.post(f"/erp/goods/{gx['ext']}/delete", data={"csrf": "t"}, follow_redirects=True); assert "доступ йўқ" in r.get_data(as_text=True)
with A.app.app_context():
    assert A.q("SELECT name, deleted FROM erp_goods WHERE ext=?", (gx["ext"],), one=True)[:] == ("Синов товар Т1", 0)
h = m.get("/erp/goods?old=1&q=синов").get_data(as_text=True); assert "+ Янги товар" in h and "Ўзгартириш</a>" not in h and "Бу ҳаракатга доступ йўқ" in h
# мижоз
with m.session_transaction() as ss: ss["csrf"] = "t"
r = m.post("/erp/clients/new", data={"csrf": "t", "name": "Синов Мижоз М1", "phones": "90 111 22 33", "back": "/erp/sale/new"}); assert r.status_code == 302
with A.app.app_context():
    cx = A.q("SELECT * FROM erp_clients WHERE name='Синов Мижоз М1'", one=True); assert cx["src"] == "pf"
assert "client=" + cx["ext"] in r.headers["Location"] and "Синов Мижоз М1" in m.get(r.headers["Location"]).get_data(as_text=True)
assert any(x["ext"] == cx["ext"] for x in m.get("/erp/api/clients?q=901112233").get_json())
with m.session_transaction() as ss: ss["csrf"] = "t"
assert "доступ йўқ" in m.post(f"/erp/clients/{cx['ext']}/delete", data={"csrf": "t"}, follow_redirects=True).get_data(as_text=True)
with m.session_transaction() as ss: ss["csrf"] = "t"
assert "доступ йўқ" in m.post(f"/erp/doc/{mid}/status", data={"csrf": "t", "act": "delete"}, follow_redirects=True).get_data(as_text=True)
with A.app.app_context():
    assert A.q("SELECT status FROM erp_docs WHERE id=?", (mid,), one=True)[0] == "posted"
    assert A.scalar("SELECT COUNT(*) FROM erp_log WHERE action='denied'") >= 3
assert "Ўчириш</button>" not in m.get(f"/erp/doc/{mid}").get_data(as_text=True)
# админ ўзгартиради ва ўчиради
tok(); assert c.post(f"/erp/goods/{gx['ext']}/edit", data={"csrf": "t", "name": "Синов товар Т2", "unit": "м", "cur": "2", "price": "3.5"}).status_code == 302
tok(); assert c.post(f"/erp/clients/{cx['ext']}/edit", data={"csrf": "t", "name": "Синов Мижоз М2", "phones": ""}).status_code == 302
one_c = None
with A.app.app_context():
    assert A.q("SELECT name, unit, cur, price FROM erp_goods WHERE ext=?", (gx["ext"],), one=True)[:] == ("Синов товар Т2", "м", 2, 3.5)
    assert A.q("SELECT name FROM erp_clients WHERE ext=?", (cx["ext"],), one=True)[0] == "Синов Мижоз М2"
    one_c = A.q("SELECT ext, name FROM erp_goods WHERE src='1c' AND folder=0 AND deleted=0 LIMIT 1", one=True)
tok(); assert c.post(f"/erp/goods/{gx['ext']}/delete", data={"csrf": "t"}).status_code == 302
tok(); assert c.post(f"/erp/goods/{one_c['ext']}/delete", data={"csrf": "t"}).status_code == 302
tok(); assert c.post(f"/erp/clients/{cx['ext']}/delete", data={"csrf": "t"}).status_code == 302
tok(); assert c.post(f"/erp/doc/{mid}/status?act=delete", data={"csrf": "t"}).status_code == 302
with A.app.app_context():
    assert A.q("SELECT deleted FROM erp_goods WHERE ext=?", (gx["ext"],), one=True)[0] == 1
    assert A.q("SELECT status FROM erp_docs WHERE id=?", (mid,), one=True)[0] == "deleted"
    erp.sync(force=True)                                   # 1С'дан қайта кўчириш ўчирилганни тикламайди
    assert A.q("SELECT deleted FROM erp_goods WHERE ext=?", (one_c["ext"],), one=True)[0] == 1
h = c.get("/erp/goods?old=1").get_data(as_text=True); assert "Ўзгартириш</a>" in h and "data-del=" in h
assert c.get("/erp/clients").status_code == 200 and c.get("/erp/clients/new").status_code == 200 and c.get("/erp/goods/new").status_code == 200
print("RIGHTS OK")

# ---- рухсатларни админ беради: «қўшиш» олинса — қўшолмайди; «супер» берилса — ўзгартириш/ўчириш мумкин
with A.app.app_context():
    jid = A.q("SELECT id FROM users WHERE username='jasur'", one=True)[0]
tok(); assert c.post("/admin/users", data={"csrf": "t", "id": str(jid), "full_name": "Жасур", "role": "cashier", "active": "on"}).status_code == 302
with A.app.app_context():
    assert A.q("SELECT p_add, p_super FROM users WHERE id=?", (jid,), one=True)[:] == (0, 0)
with m.session_transaction() as ss: ss["csrf"] = "t"
r = m.post("/erp/goods/new", data={"csrf": "t", "name": "Рухсатсиз товар", "cat": "good"}, follow_redirects=True); assert "доступ йўқ" in r.get_data(as_text=True)
assert "+ Янги товар" not in m.get("/erp/goods?old=1").get_data(as_text=True)
with A.app.app_context():
    assert not A.q("SELECT 1 FROM erp_goods WHERE name='Рухсатсиз товар'")
tok(); assert c.post("/admin/users", data={"csrf": "t", "id": str(jid), "full_name": "Жасур", "role": "cashier", "active": "on", "p_add": "on", "p_super": "on"}).status_code == 302
assert "супер" in c.get("/admin/users").get_data(as_text=True)
with m.session_transaction() as ss: ss["csrf"] = "t"
assert m.post("/erp/goods/new", data={"csrf": "t", "name": "Супер товар С1", "cat": "good", "price": "1"}).status_code == 302
with A.app.app_context():
    sx = A.q("SELECT ext FROM erp_goods WHERE name='Супер товар С1'", one=True)[0]
with m.session_transaction() as ss: ss["csrf"] = "t"
assert m.post(f"/erp/goods/{sx}/edit", data={"csrf": "t", "name": "Супер товар С2", "price": "2"}).status_code == 302
with m.session_transaction() as ss: ss["csrf"] = "t"
assert m.post(f"/erp/goods/{sx}/delete", data={"csrf": "t"}).status_code == 302
with A.app.app_context():
    assert A.q("SELECT name, deleted FROM erp_goods WHERE ext=?", (sx,), one=True)[:] == ("Супер товар С2", 1)
assert "var ADM=true" in m.get("/erp/goods?old=1").get_data(as_text=True)
print("PERMS OK")

# ---- 1С'дагидек ўчириш: белги -> «белгиланганларни ўчириш» тарихи борини ўчирмайди
from datetime import timedelta
with A.app.app_context():
    usedg = A.q("SELECT good FROM erp_lines WHERE good<>'' LIMIT 1", one=True)[0]
tok(); assert c.post("/erp/goods/new", data={"csrf": "t", "name": "Бўш товар Б1", "cat": "good", "price": "1"}).status_code == 302
tok(); assert c.post("/erp/clients/new", data={"csrf": "t", "name": "Бўш мижоз Б1"}).status_code == 302
# накладнойдаги мижоз танлаш ойнасидан «+ Янги мижоз» (26.10: ойна ичида очилмас эди — X-Frame-Options DENY)
h = c.get("/erp/clients?pick=1").get_data(as_text=True)
assert "/erp/clients/new?pick=1" in h, h[h.find("data-ins") - 200:h.find("data-ins") + 300]
r = c.get("/erp/clients/new?pick=1&back=/erp/clients?pick%3D1"); assert r.status_code == 200 and r.headers.get("X-Frame-Options") == "SAMEORIGIN"
assert 'name="pick" value="1"' in r.get_data(as_text=True)
tok(); r = c.post("/erp/clients/new", data={"csrf": "t", "name": "Ойнадан Мижоз О1", "pick": "1"})
h = r.get_data(as_text=True)
assert r.status_code == 200 and r.headers.get("X-Frame-Options") == "SAMEORIGIN" and "pickClient" in h and "Ойнадан Мижоз О1" in h, h[:300]
with A.app.app_context():
    bg = A.q("SELECT ext FROM erp_goods WHERE name='Бўш товар Б1'", one=True)[0]
    bc = A.q("SELECT ext FROM erp_clients WHERE name='Бўш мижоз Б1'", one=True)[0]
tok(); r = c.post(f"/erp/goods/{usedg}/delete", data={"csrf": "t"}, follow_redirects=True); assert "Тарихи бор" in r.get_data(as_text=True)
tok(); c.post(f"/erp/goods/{bg}/delete", data={"csrf": "t"})
tok(); c.post(f"/erp/clients/{bc}/delete", data={"csrf": "t"})
h = c.get("/erp/marked").get_data(as_text=True); assert "Бўш товар Б1" in h and "тарихи бор" in h
assert c.get(f"/erp/history/good/{usedg}").status_code == 200
with A.app.app_context():   # бошқа одам ишлаётган бўлса — монопол режим керак
    A.db().execute("INSERT OR REPLACE INTO k_edit_lock(key,by,at) VALUES('day:x:1','Жасур',?)", (time.time(),)); A.db().commit()
tok(); r = c.post("/erp/marked", data={"csrf": "t"}, follow_redirects=True); assert "Монопол режим" in r.get_data(as_text=True)
with A.app.app_context():
    assert A.q("SELECT 1 FROM erp_goods WHERE ext=?", (bg,))
    A.db().execute("DELETE FROM k_edit_lock"); A.db().commit()
tok(); r = c.post("/erp/marked", data={"csrf": "t"}, follow_redirects=True); assert "белгиланг" in r.get_data(as_text=True)   # ўзи ҳеч нарса ўчирмайди
with A.app.app_context():
    assert A.q("SELECT 1 FROM erp_goods WHERE ext=?", (bg,)) and A.q("SELECT 1 FROM erp_clients WHERE ext=?", (bc,))
tok(); r = c.post("/erp/marked", data={"csrf": "t", "pick": [f"g:{bg}", f"g:{usedg}"]}); assert r.status_code == 200 and "Бутунлай ўчирилди" in r.get_data(as_text=True)
with A.app.app_context():
    assert not A.q("SELECT 1 FROM erp_goods WHERE ext=?", (bg,))
    assert A.q("SELECT 1 FROM erp_clients WHERE ext=?", (bc,))                               # танланмаган мижоз — қолди
tok(); c.post("/erp/marked", data={"csrf": "t", "pick": [f"c:{bc}"]})
with A.app.app_context():
    assert not A.q("SELECT 1 FROM erp_clients WHERE ext=?", (bc,))
    assert A.q("SELECT deleted FROM erp_goods WHERE ext=?", (usedg,), one=True)[0] == 1      # тарихи бор — қолди
tok(); c.post(f"/erp/goods/{usedg}/delete", data={"csrf": "t", "unmark": "1"})
with A.app.app_context():
    assert A.q("SELECT deleted FROM erp_goods WHERE ext=?", (usedg,), one=True)[0] == 0
with m.session_transaction() as ss: ss["csrf"] = "t"
assert m.get("/erp/marked").status_code in (302, 403)
print("MARKED OK")

# ---- кунларни ёпиш: 4 кундан эскиси ўзгармайди, админ очади
with A.app.app_context():
    A.set_settings(edit_lock_days="4")
    old = (date.today() - timedelta(days=4)).isoformat()
    assert A.is_closed(old) and not A.is_closed(date.today() - timedelta(days=3))
tok(); r = c.post("/erp/sale/new", data=dict(form, date=old), follow_redirects=True); assert "ёпилган" in r.get_data(as_text=True)
tok(); assert c.post("/admin/period-lock", data={"csrf": "t", "action": "open", "from": old, "hours": "2"}).status_code == 302
with A.app.app_context():
    assert not A.is_closed(old) and A.is_closed((date.today() - timedelta(days=5)).isoformat())
tok(); r = c.post("/erp/sale/new", data=dict(form, date=old)); assert r.status_code == 302
oid = int(r.headers["Location"].rstrip("/").split("/")[-1])
tok(); assert c.post("/admin/period-lock", data={"csrf": "t", "action": "close"}).status_code == 302
r = c.get(f"/erp/doc/{oid}/edit", follow_redirects=True); assert "ёпилган" in r.get_data(as_text=True)
tok(); r = c.post(f"/erp/doc/{oid}/status", data={"csrf": "t", "act": "unpost"}, follow_redirects=True); assert "ёпилган" in r.get_data(as_text=True)
assert "Кунларни ёпиш" in c.get("/admin/period-lock").get_data(as_text=True)
with A.app.app_context():
    A.set_settings(edit_lock_days="0")
print("CLOSED OK")

# ---- мижоз лимити: 4000 лимит, 3800 қарз, 500 лик накладной проведен бўлмайди; тўлов киритилса бўлади
with A.app.app_context():
    assert A.get_setting("credit_init") == "1" and A.scalar("SELECT COUNT(*) FROM erp_clients WHERE lim IS NULL AND folder=0") == 0
    assert A.scalar("SELECT COUNT(*) FROM erp_clients WHERE blocked=1") > 0
tok(); assert c.post("/erp/goods/new", data={"csrf": "t", "name": "Лимит товар L1", "cat": "good", "cur": "2", "price": "500", "unit": "шт"}).status_code == 302
tok(); assert c.post("/erp/clients/new", data={"csrf": "t", "name": "Лимит Мижоз L1"}).status_code == 302
with A.app.app_context():
    lg = A.q("SELECT ext FROM erp_goods WHERE name='Лимит товар L1'", one=True)[0]
    lcx = A.q("SELECT ext FROM erp_clients WHERE name='Лимит Мижоз L1'", one=True)[0]
    assert erp.credit_info(A.db(), lcx)["lim"] == 1000 and not erp.credit_info(A.db(), lcx)["blocked"]   # янги мижоз — стандарт
    A.db().execute("UPDATE erp_clients SET debt1c=3800 WHERE ext=?", (lcx,)); A.db().commit()
with m.session_transaction() as ss: ss["csrf"] = "t"
r = m.post(f"/erp/clients/{lcx}/credit", data={"csrf": "t", "lim": "4000"}, follow_redirects=True); assert "доступ йўқ" in r.get_data(as_text=True)
tok(); assert c.post(f"/erp/clients/{lcx}/credit", data={"csrf": "t", "lim": "4000"}).status_code == 302
lf = {"csrf": "t", "minus_ok": "1", "client": lcx, "rate": "12000", "nal": "1", "date": date.today().isoformat(), "act": "post",
      "good": [lg], "qty": ["1"], "price": ["500"], "lnote": [""]}
tok(); r = c.post("/erp/sale/new", data=lf); h = r.get_data(as_text=True)
assert r.status_code == 200 and "Лимит етарли эмас" in h and "$3 800" in h and "$500" in h and "тўлиқ тўлансагина" in h, h[h.find("flash"):h.find("flash")+400]
tok(); r = c.post("/erp/sale/new", data=dict(lf, act="save")); assert r.status_code == 302           # қоралама — мумкин
did = int(r.headers["Location"].rstrip("/").split("/")[-1])
tok(); r = c.post(f"/erp/doc/{did}/status", data={"csrf": "t", "act": "post", "minus_ok": "1"}, follow_redirects=True); assert "Лимит етарли эмас" in r.get_data(as_text=True)
tok(); r = c.post("/erp/sale/new", data=dict(lf, pay_usd="300")); assert r.status_code == 200, "лимитга сиғмаган накладной — қисман тўлов етмайди"
tok(); r = c.post("/erp/sale/new", data=dict(lf, pay_usd="500")); assert r.status_code == 302, "тўлиқ тўланса — проведен"
with A.app.app_context():
    ci = erp.credit_info(A.db(), lcx); assert ci["debt"] == 3800 and ci["free"] == 200
    A.db().execute("UPDATE erp_clients SET debt1c=3000 WHERE ext=?", (lcx,)); A.db().commit()    # мижоз 1С кассасига тўлади
tok(); assert c.post(f"/erp/doc/{did}/status", data={"csrf": "t", "act": "post", "minus_ok": "1"}).status_code == 302
with A.app.app_context():
    assert A.q("SELECT status FROM erp_docs WHERE id=?", (did,), one=True)[0] == "posted"
assert c.get(f"/erp/api/credit/{lcx}").get_json()["debt"] == 3500
# запрет: насияга йўқ, тўлиқ тўланса — мумкин
tok(); c.post(f"/erp/clients/{lcx}/credit", data={"csrf": "t", "lim": "100000", "blocked": "on", "note": "синов"})
tok(); r = c.post("/erp/sale/new", data=lf); assert "запрет" in r.get_data(as_text=True)
tok(); assert c.post("/erp/sale/new", data=dict(lf, pay_usd="500")).status_code == 302
tok(); c.post(f"/erp/clients/{lcx}/credit", data={"csrf": "t", "lim": "100000"})
with A.app.app_context():
    assert A.q("SELECT unblocked_at FROM erp_clients WHERE ext=?", (lcx,), one=True)[0]
    # ҳар кунги текширув: эски харидли мижоз автомат запрет олади
    A.db().execute("UPDATE erp_clients SET blocked=0, unblocked_at='2020-01-01 00:00:00' WHERE ext=?", (bc2 := A.q("SELECT client FROM erp_docs WHERE src='1c' AND kind='sale' AND date<'2025-06-01' LIMIT 1", one=True)[0],))
    A.set_settings(credit_block_day="2000-01-01"); A.db().commit()
    erp.credit_after_sync(A.db()); A.db().commit()
    last = A.scalar("SELECT MAX(date) FROM erp_docs WHERE client=? AND kind='sale' AND status='posted'", (bc2,))
    assert A.q("SELECT blocked FROM erp_clients WHERE ext=?", (bc2,), one=True)[0] == (1 if last < erp.block_cutoff().isoformat() else 0)
for u in ("/erp/clients?show=near", "/erp/clients?show=blocked", f"/erp/clients/{lcx}/credit"):
    assert c.get(u).status_code == 200, u
assert "Лимит" in c.get("/erp/clients").get_data(as_text=True)
tok(); assert c.post("/admin/users", data={"csrf": "t", "id": str(jid), "full_name": "Жасур", "role": "cashier", "active": "on", "p_limit": "on"}).status_code == 302
with m.session_transaction() as ss: ss["csrf"] = "t"
assert m.post(f"/erp/clients/{lcx}/credit", data={"csrf": "t", "lim": "5000"}).status_code == 302
with A.app.app_context():
    assert A.q("SELECT lim FROM erp_clients WHERE ext=?", (lcx,), one=True)[0] == 5000
print("CREDIT OK")

# ---- ҳисоблар (карта/терминал/инвестиция/поступление в счёт) ва заводлар — мижоз эмас: лимит/запрет йўқ
with A.app.app_context():
    con = A.db()
    acc = A.q("SELECT ext, ckind, blocked FROM erp_clients WHERE name='Инвестиция'", one=True)
    sup = A.q("SELECT ext, ckind, blocked FROM erp_clients WHERE folder=0 AND parent IN (SELECT ext FROM erp_clients WHERE folder=1 AND name LIKE 'Поставщ%') LIMIT 1", one=True)
    assert acc["ckind"] == "account" and acc["blocked"] == 0 and sup["ckind"] == "supplier" and sup["blocked"] == 0, (tuple(acc), tuple(sup) if sup else None)
    assert erp.credit_check(con, acc["ext"], 999999)["nolimit"]
    assert A.scalar("SELECT COUNT(*) FROM erp_clients WHERE ckind<>'client' AND blocked=1") == 0
    assert A.q("SELECT ckind FROM erp_clients WHERE name LIKE 'Карта%' LIMIT 1", one=True)[0] == "account"
_h = c.get("/erp/clients").get_data(as_text=True)
assert "Инвестиция" not in _h                                   # мижозлар бўлимида ҳисоб ва заводлар йўқ
with A.app.app_context():
    _sn = A.q("SELECT name FROM erp_clients WHERE ext=?", (sup["ext"],), one=True)[0]
assert _sn in c.get("/erp/suppliers?q=" + _sn[:20]).get_data(as_text=True)
tok(); c.post(f"/erp/clients/{acc['ext']}/credit", data={"csrf": "t", "lim": "1000", "ckind": "client"})
with A.app.app_context():
    assert A.q("SELECT ckind, ckind_set FROM erp_clients WHERE ext=?", (acc["ext"],), one=True)[:] == ("client", 1)
    erp.classify_clients(A.db()); A.db().commit()                        # қўлда ўзгартирилгани қайта ўзгармайди
    assert A.q("SELECT ckind FROM erp_clients WHERE ext=?", (acc["ext"],), one=True)[0] == "client"
print("CKIND OK")

# ---- ҳисоблар ва тўлов турлари: Click 1% комиссия, карта, нақд; қолдиқ ва ўтказма
with A.app.app_context():
    con = A.db()
    M = {r["name"]: r for r in erp.methods(con)}
    assert {"Нақд", "Терминал", "Click", "Payme", "Ягона QR тўлов", "Перечисление", "Карта ПСБ 0311"} <= set(M)
    assert erp.fee_pct(con, M["Click"]["id"]) == 1 and erp.fee_pct(con, M["Карта ПСБ 0311"]["id"]) == 0
    A.db().execute("UPDATE erp_clients SET lim=1000000, blocked=0 WHERE ext=?", (lcx,)); A.db().commit()
pf_ = dict(lf, pay_uzs="1200000", pm_method=[str(M["Click"]["id"]), str(M["Карта ПСБ 0311"]["id"])], pm_amount=["2400000", "100"], pm_cur=["uzs", "usd"])
tok(); r = c.post("/erp/sale/new", data=dict(pf_, qty=["2"])); assert r.status_code == 302, r.get_data(as_text=True)[:200]
pid = int(r.headers["Location"].rstrip("/").split("/")[-1])
with A.app.app_context():
    d = A.q("SELECT * FROM erp_docs WHERE id=?", (pid,), one=True)
    assert d["usd"] == 1000 and d["paid"] == round(100 + 200 + 100, 2), d["paid"]           # нақд 1.2м/12000 + Click 2.4м/12000 + карта $100
    ps = {r["mname"]: r for r in A.q("SELECT p.*, m.name mname FROM erp_pays p JOIN erp_methods m ON m.id=p.method_id WHERE doc_id=?", (pid,))}
    assert ps["Click"]["fee"] == 24000 and ps["Карта ПСБ 0311"]["fee"] == 0 and ps["Нақд"]["amount"] == 1200000
    b0 = {b["acc"]["name"]: b for b in erp.account_balances(A.db())}
    A.set_settings(erp_pf_money="1")                  # 1С'дан ўтилгандан кейинги ҳолат: платформа тўловлари ҳисобга тушади
    bal = {b["acc"]["name"]: b for b in erp.account_balances(A.db())}
    assert round(bal["Банк ҳисоби"]["uzs"] - b0["Банк ҳисоби"]["uzs"], 2) >= 2400000 - 24000 - 0.01
    assert round(bal["Карта ПСБ 0311"]["usd"] - b0["Карта ПСБ 0311"]["usd"], 2) >= 100
h = c.get(f"/erp/doc/{pid}").get_data(as_text=True); assert "Click" in h and "комиссия" in h
h = c.get(f"/erp/doc/{pid}/edit").get_data(as_text=True); assert '"2400000"' in h or "2400000" in h
tok(); assert c.post(f"/erp/doc/{pid}/edit", data=dict(pf_, qty=["2"], act="save", version=d["updated_at"])).status_code == 302
with A.app.app_context():
    assert A.scalar("SELECT COUNT(*) FROM erp_pays WHERE doc_id=?", (pid,)) == 3                      # қайта сақлаганда такрорланмайди
tok(); r = c.post("/erp/sale/new", data=dict(lf, pm_method=[""], pm_amount=["5"], pm_cur=["uzs"])); assert "тўлов турини танланг" in r.get_data(as_text=True)
# комиссия ўзгарди: эртадан 1.5%
from datetime import timedelta as _td
tok(); assert c.post("/erp/accounts", data={"csrf": "t", "act": "fee", "method_id": str(M["Click"]["id"]), "pct": "1.5",
                                            "from": (date.today() + _td(days=1)).isoformat()}).status_code == 302
with A.app.app_context():
    assert erp.fee_pct(A.db(), M["Click"]["id"]) == 1 and erp.fee_pct(A.db(), M["Click"]["id"], date.today() + _td(days=1)) == 1.5
    accs = {a["name"]: a["id"] for a in A.q("SELECT * FROM erp_accounts")}
    bal = {b["acc"]["name"]: b for b in erp.account_balances(A.db())}
tok(); assert c.post("/erp/accounts", data={"csrf": "t", "act": "move", "from_acc": str(accs["Карта ПСБ 0311"]), "to_acc": str(accs["Нақд касса"]),
                                            "amount": "50", "cur": "usd", "date": date.today().isoformat()}).status_code == 302
with A.app.app_context():
    bal2 = {b["acc"]["name"]: b for b in erp.account_balances(A.db())}
    assert round(bal2["Карта ПСБ 0311"]["usd"] - bal["Карта ПСБ 0311"]["usd"], 2) == -50 and round(bal2["Нақд касса"]["usd"] - bal["Нақд касса"]["usd"], 2) == 50
h = c.get("/erp/accounts").get_data(as_text=True); assert "Банк ҳисоби" in h and "Click" in h
with m.session_transaction() as ss: ss["csrf"] = "t"
assert m.get("/erp/accounts").status_code in (302, 403)
print("ACCOUNTS OK")

# ---- Click / Payme: гуруҳлардаги бот хабарларини ўқиш, такрорсиз ёзиш, накладной билан боғлаш
import asyncio, tguser, epay
CLICK = "CLICKUZ\n🟢 LUMBERTURE.UZ (85563)\n🆔 5156466021\n📱 +998*****1850\n💳 ***************\n🇺🇿 2,400,000.00 сум\n🕘 10:11:12 {d}\n✅ Успешно подтвержден"
PAYME = "📍 LUMBERTURE\n🧾 30\n💳 626291******0716\n🇺🇿 611 000,00 сум\n🕘 14:39:42 {d}\n🆔 6acf4ddb8373f63e18630063\n✅ Успешно оплачен"
dd = date.today().strftime("%d.%m.%Y")
class _M:
    def __init__(s, i, t): s.id, s.raw_text, s.out = i, t, False
class _D:
    def __init__(s, name, msgs): s.name, s.entity, s.is_group, s.is_channel, s.msgs = name, name, True, False, msgs
class _C:
    def __init__(s): s.ds = [_D("LUMBERTURE.UZ & CLICK", [_M(5, CLICK.format(d=dd)), _M(4, "Смена реквизитов")]), _D("LUMBERTURE (Payme)", [_M(9, PAYME.format(d=dd))])]
    async def iter_dialogs(s, limit=0):
        for d in s.ds: yield d
    async def iter_messages(s, ent, limit=0, min_id=0):
        for d in s.ds:
            if d.entity == ent:
                for m in d.msgs:
                    if m.id > min_id: yield m
class _S:
    def call(s, a, b, fn, timeout=0): return asyncio.run(fn(_C()))
tguser.set_sender(_S())
with A.app.app_context():
    A.set_settings(tgu_enabled="1", tgu_authorized="1", tgu_api_id="1", tgu_api_hash="x")
    r = epay.poll(force=True); assert r["new"] == 2, r
    assert epay.poll(force=True)["new"] == 0                              # такрорланмайди
    rows = {x["src"]: x for x in A.q("SELECT * FROM erp_epay")}
    assert rows["click"]["amount"] == 2400000 and rows["click"]["ext_id"] == "5156466021" and rows["payme"]["amount"] == 611000
    assert rows["click"]["pay_id"] is None
    assert A.get_setting("epay_click_last") == "5" and A.get_setting("epay_payme_last") == "9"
# платформа накладнойи Click 2 400 000 билан — ўзи боғланади
tok(); r = c.post("/erp/sale/new", data=dict(lf, qty=["1"], pay_uzs="", pm_method=[str(M["Click"]["id"])], pm_amount=["2400000"], pm_cur=["uzs"]))
assert r.status_code == 302
with A.app.app_context():
    assert epay.auto_link(A.db()) == 1; A.db().commit()
    assert A.q("SELECT client FROM erp_epay WHERE src='click'", one=True)[0] == lcx
h = c.get("/erp/epay").get_data(as_text=True); assert "2 400 000" in h and "611 000" in h and "боғланмаган" in h
with A.app.app_context():
    pid2 = A.q("SELECT id FROM erp_epay WHERE src='payme'", one=True)[0]
tok(); assert c.post("/erp/epay", data={"csrf": "t", "act": "client", "id": str(pid2), "client": lcx, "note": "синов"}).status_code == 302
with A.app.app_context():
    assert A.q("SELECT client FROM erp_epay WHERE id=?", (pid2,), one=True)[0] == lcx
assert epay.parse("Смена реквизитов info@click.uz") is None
assert epay.parse(PAYME.format(d=dd).replace("Успешно оплачен", "Отменен"), "payme")["status"] == "cancel"
print("EPAY OK")

# ---- накладной ойнаси (1С'дагидек): муддат, «суммани ҳисобга олиш», минус қолдиқ, нусха, возврат, чоп этиш
from datetime import timedelta
tok(); assert c.post("/erp/clients/new", data={"csrf": "t", "name": "Муддат Мижоз M1"}).status_code == 302
with A.app.app_context():
    mx_ = A.q("SELECT ext FROM erp_clients WHERE name='Муддат Мижоз M1'", one=True)[0]
    A.db().execute("UPDATE erp_clients SET lim=100000, blocked=0 WHERE ext=?", (mx_,))
    A.set_settings(erp_due_start="2000-01-01"); A.db().commit()
    assert erp.client_due(A.db(), mx_) == 20
mf = dict(lf, client=mx_, qty=["2"], pay_usd="", pay_uzs="")
# 30 кун олдинги тўланмаган накладной — муддат ўтган
tok(); r = c.post("/erp/sale/new", data=dict(mf, date=(date.today() - timedelta(days=30)).isoformat())); assert r.status_code == 302
old_id = int(r.headers["Location"].rstrip("/").split("/")[-1])
with A.app.app_context():
    ci = erp.credit_info(A.db(), mx_); assert ci["overdue"] and ci["overdue"][0]["days"] == 10 and "10 кун ўтди" in ci["overdue_text"], ci
tok(); r = c.post("/erp/sale/new", data=mf); h = r.get_data(as_text=True); assert r.status_code == 200 and "муддати ўтган" in h, h[h.find("flash"):][:300]
tok(); assert c.post("/erp/sale/new", data=dict(mf, pay_usd="1000")).status_code == 302          # тўлиқ тўланса — мумкин
# мижоз тўлади (1С кассаси) — блок кетади
with A.app.app_context():
    A.db().execute("UPDATE erp_clients SET debt1c=-1000 WHERE ext=?", (mx_,)); A.db().commit()
    assert not erp.credit_info(A.db(), mx_)["overdue"]
    A.db().execute("UPDATE erp_clients SET debt1c=0 WHERE ext=?", (mx_,)); A.db().commit()
    assert erp.credit_info(A.db(), mx_)["overdue"]
# админ муддат блокини очади
tok(); assert c.post(f"/erp/clients/{mx_}/credit", data={"csrf": "t", "act": "due_open"}).status_code == 302
with A.app.app_context():
    assert not erp.credit_info(A.db(), mx_)["overdue"]
tok(); assert c.post("/erp/sale/new", data=mf).status_code == 302
# мижоз муддатини ўзгартириш (0 — текширилмайди)
tok(); assert c.post(f"/erp/clients/{mx_}/credit", data={"csrf": "t", "lim": "100000", "due_days": "45"}).status_code == 302
with A.app.app_context():
    assert erp.client_due(A.db(), mx_) == 45
    A.db().execute("UPDATE erp_clients SET unblocked_at=NULL WHERE ext=?", (mx_,)); A.db().commit()
    assert not erp.credit_info(A.db(), mx_)["overdue"]                   # 30 кун < 45 кун
    A.set_settings(erp_due_start=date.today().isoformat())               # эски накладнойларга муддат тегмайди
    A.db().execute("UPDATE erp_clients SET due_days=NULL WHERE ext=?", (mx_,)); A.db().commit()
    assert not erp.credit_info(A.db(), mx_)["overdue"]
h = c.get(f"/erp/clients/{mx_}/credit").get_data(as_text=True); assert "Тўлов муддати, кун" in h
# «Суммани ҳисобга олиш» йўқ — фақат сони, қарзга тушмайди
with A.app.app_context():
    d0 = erp.client_debt(A.db(), mx_)
tok(); r = c.post("/erp/sale/new", data=dict(mf, nal="0")); assert r.status_code == 302
nz = int(r.headers["Location"].rstrip("/").split("/")[-1])
with A.app.app_context():
    assert A.q("SELECT nal FROM erp_docs WHERE id=?", (nz,), one=True)[0] == 0 and erp.client_debt(A.db(), mx_) == d0
h = c.get(f"/erp/doc/{nz}").get_data(as_text=True); assert "сумма ҳисобга олинмайди" in h
fz = {k: v for k, v in mf.items() if k != "nal"}
tok(); r = c.post("/erp/sale/new", data=dict(fz, pay_usd="10")); assert "тўлов бўлмайди" in r.get_data(as_text=True)
# минус қолдиқ (эгаси, 26.10): тасдиқсиз ҳам проведен бўлади — огоҳлантириш билан; ким минусга ёзгани ёзилади
with A.app.app_context():
    st0 = erp.stock_all(A.db()).get(lg, 0)
tok(); r = c.post("/erp/sale/new", data=dict(mf, minus_ok="", pay_usd="1000"), follow_redirects=True); h = r.get_data(as_text=True)
assert r.status_code == 200 and "минусга ёзилди" in h and "Лимит товар L1" in h, h[:300]
with A.app.app_context():
    mid = A.q("SELECT MAX(id) FROM erp_docs WHERE kind='sale' AND src='pf'", one=True)[0]
    assert A.q("SELECT minus_by FROM erp_docs WHERE id=?", (mid,), one=True)[0] == "admin"
    assert "minus_ok" in [x[0] for x in A.q("SELECT action FROM erp_log WHERE doc_id=?", (mid,))]
    assert erp.stock_all(A.db()).get(lg, 0) == st0 - 2                   # платформа накладнойи қолдиқдан айрилди
# хизматлар қолдиқ текширилмайди
with A.app.app_context():
    svc = A.q("SELECT g.ext FROM erp_goods g WHERE g.folder=0 AND g.name LIKE 'Услуга%' LIMIT 1", one=True)
    if svc:
        assert erp.is_service(A.db(), svc[0]) and not erp.shortages(A.db(), [dict(good=svc[0], qty=999999)])
# Нусха олиш (F9)
h = c.get(f"/erp/sale/new?copy={mid}").get_data(as_text=True); assert lg in h and date.today().isoformat() in h
h = c.get(f"/erp/doc/{mid}").get_data(as_text=True); assert "Нусха олиш" in h and "Возврат" in h
# Возврат: минус сонлар, асл курс, ҳавола
with A.app.app_context():
    A.db().execute("UPDATE erp_docs SET rate=12500 WHERE id=?", (mid,)); A.db().commit()
h = c.get(f"/erp/sale/new?ret={mid}").get_data(as_text=True)
assert "Возврат по накладной №" in h and '"qty": "-2"' in h and 'value="12500"' in h, h[h.find('INIT'):][:300]
rf = dict(mf, ref_doc=str(mid), qty=["-1"], pay_usd="", note="Возврат")
tok(); r = c.post("/erp/sale/new", data=dict(rf, qty=["-3"])); assert "кўпи билан 2" in r.get_data(as_text=True)
tok(); r = c.post("/erp/sale/new", data=dict(rf, qty=["1"])); assert "минус бўлиши керак" in r.get_data(as_text=True)
tok(); r = c.post("/erp/sale/new", data=rf); assert r.status_code == 302
rid = int(r.headers["Location"].rstrip("/").split("/")[-1])
with A.app.app_context():
    rd = A.q("SELECT * FROM erp_docs WHERE id=?", (rid,), one=True)
    assert rd["ref_doc"] == mid and rd["rate"] == 12500 and rd["usd"] < 0 and rd["status"] == "posted"
    assert erp.returned_qty(A.db(), mid) == {lg: 1}
h = c.get(f"/erp/sale/new?ret={mid}").get_data(as_text=True); assert '"qty": "-1"' in h        # қолгани
assert f"№ {rd['no']}" in c.get(f"/erp/doc/{mid}").get_data(as_text=True)
tok(); r = c.post("/erp/sale/new", data=dict(rf, qty=["-1"])); assert r.status_code == 302
assert c.get(f"/erp/sale/new?ret={mid}", follow_redirects=True).get_data(as_text=True).count("Возврат фақат") == 1   # ҳаммаси қайтди
# чоп этиш: $ / сўм / Разрешение — оқ-қора, русча, изоҳлар ва телефонлар
with A.app.app_context():
    A.db().execute("UPDATE erp_clients SET phones='+998 90 111-22-33, +998 91 444-55-66' WHERE ext=?", (mx_,))
    A.db().execute("UPDATE erp_docs SET note='Собир ака олиб кетди' WHERE id=?", (mid,))
    A.db().execute("UPDATE erp_lines SET note='оқ ранг' WHERE doc_id=?", (mid,)); A.db().commit()
hu = c.get(f"/erp/doc/{mid}/print?form=usd").get_data(as_text=True)
hz = c.get(f"/erp/doc/{mid}/print?form=uzs").get_data(as_text=True)
hp = c.get(f"/erp/doc/{mid}/print?form=perm").get_data(as_text=True)
for h in (hu, hz, hp):
    assert "Покупатель" in h and "+998 90 111-22-33, +998 91 444-55-66" in h and "Собир ака олиб кетди" in h and "оқ ранг" in h
    assert "grayscale" in h and "#3E643C" not in h
assert "Цена, $" in hu and "Сумма, сум" not in hu and "Цена, сум" in hz and "Сумма, $" not in hz
assert "Цена" not in hp and "500" not in hp.split("<tbody>")[1] and "Разрешил" in hp
assert "Долг до накладной" in hu and "Долг после накладной" in hz
assert c.get(f"/erp/doc/{mid}/print?form=full").status_code == 200
# 1С накладнойи ҳам чоп этилади ва нусхаланади
with A.app.app_context():
    s1id = A.q("SELECT id FROM erp_docs WHERE src='1c' AND kind='sale' AND status='posted' ORDER BY date DESC LIMIT 1", one=True)[0]
assert "Накладная №" in c.get(f"/erp/doc/{s1id}/print?form=uzs").get_data(as_text=True)
assert c.get(f"/erp/sale/new?copy={s1id}").status_code == 200
print("SALE WINDOW OK")

# ---- масъул шахс: накладнойда белгиланади, муддат ўтса — Telegram эслатма, админ ойликдан ушлайди
import raspil_bot as RB, kassa as KS
sent = []
RB.send = lambda chat, text, kb=None, reply_to=None: (sent.append((chat, text)), True)[1]
with A.app.app_context():
    con = A.db()
    con.execute("INSERT OR IGNORE INTO k_workers(name, salary, cur) VALUES('Нодир синов', 3000000, 'UZS')")
    kwn = con.execute("SELECT id FROM k_workers WHERE name='Нодир синов'").fetchone()[0]
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, kw_id, bot_uid) VALUES('Нодир синов','998900000777',?,'raspil',?,777)", (A.now(), kwn))
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, owner, bot_uid) VALUES('Шерик синов','998900000778',?,'ready',1,778)", (A.now(),))
    con.commit()
    ppl = erp.resp_people(con); keys = {p["key"]: p for p in ppl}
    assert f"kw:{kwn}" in keys and any(p["partner"] and p["name"] == "Шерик синов" for p in ppl)
    A.set_settings(erp_due_start="2000-01-01")
    con.execute("UPDATE erp_clients SET debt1c=0, due_days=NULL, unblocked_at=NULL WHERE ext=?", (mx_,)); con.commit()
h = c.get("/erp/sale/new").get_data(as_text=True); assert "Масъул шахс" in h and "Нодир синов" in h
tok(); r = c.post("/erp/sale/new", data=dict(mf, date=(date.today() - timedelta(days=25)).isoformat(), resp=f"kw:{kwn}", pay_usd="")); assert r.status_code == 302, r.get_data(as_text=True)[:500]
rsd = int(r.headers["Location"].rstrip("/").split("/")[-1])
tok(); r = c.post("/erp/sale/new", data=dict(mf, resp="kw:999999", act="save")); assert "Масъул шахс рўйхатдан танланмаган" in r.get_data(as_text=True)
with A.app.app_context():
    assert A.q("SELECT resp FROM erp_docs WHERE id=?", (rsd,), one=True)[0] == f"kw:{kwn}"
    n = erp.resp_remind(); assert n >= 1 and any(ch == 777 and "Сиз масъулсиз" in t for ch, t in sent), sent
    assert erp.resp_remind() == 0                                    # кунига бир марта
h = c.get(f"/erp/doc/{rsd}").get_data(as_text=True); assert "Масъул шахс:" in h and "Ойликдан ушлаш" in h
# 1С накладнойига ҳам масъул белгилаш мумкин
with A.app.app_context():
    rwp = A.q("SELECT id FROM ready_workers WHERE name='Шерик синов'", one=True)[0]
tok(); assert c.post(f"/erp/doc/{s1id}/resp", data={"csrf": "t", "resp": f"rw:{rwp}"}).status_code == 302
with A.app.app_context():
    assert A.q("SELECT resp FROM erp_docs WHERE id=?", (s1id,), one=True)[0] == f"rw:{rwp}"
    erp.sync(force=True)                                              # 1С кўчириш масъулни ўчирмайди
    assert A.q("SELECT resp FROM erp_docs WHERE id=?", (s1id,), one=True)[0] == f"rw:{rwp}"
# ойликдан ушлаш (фақат админ) — касса ойлигида камаяди
ym = date.today().isoformat()[:7]
with A.app.app_context():
    A.db().execute("INSERT OR IGNORE INTO k_ws(ym, worker_id, salary) VALUES(?,?,3000000)", (ym, kwn)); A.db().commit()
    KS._memo.clear(); t0 = next(x for x in KS.month_calc(ym)["staff"] if x["worker_id"] == kwn)["calc_total"]
with m.session_transaction() as ss: ss["csrf"] = "t"
m.post(f"/erp/doc/{rsd}/resp", data={"csrf": "t", "act": "ded", "amount": "100000"})
with A.app.app_context():
    assert A.scalar("SELECT COUNT(*) FROM erp_resp_ded WHERE doc_id=?", (rsd,)) == 0      # менежерга мумкин эмас
tok(); assert c.post(f"/erp/doc/{rsd}/resp", data={"csrf": "t", "act": "ded", "amount": "250000", "ym": ym}).status_code == 302
with A.app.app_context():
    KS._memo.clear(); row = next(x for x in KS.month_calc(ym)["staff"] if x["worker_id"] == kwn)
    assert abs(row["calc_total"] - (t0 - 250000)) < 1 and row["ded"] == 250000
    did_ = A.q("SELECT id FROM erp_resp_ded WHERE doc_id=?", (rsd,), one=True)[0]
assert "масъул: −" in c.get(f"/kassa/staff?ym={ym}").get_data(as_text=True)
# мижоз кейин тўлади — ушланган пул ишчига қайтади (аввал ярми, кейин ҳаммаси)
with A.app.app_context():
    con = A.db()
    base = A.q("SELECT base_usd FROM erp_resp_ded WHERE id=?", (did_,), one=True)[0]; assert base > 0
    dbt = erp.client_debt(con, mx_)
    con.execute("UPDATE erp_clients SET debt1c=COALESCE(debt1c,0)-? WHERE ext=?", (round(base / 2, 2), mx_)); con.commit()
    sent.clear()
    assert erp.resp_refund() == 1 and erp.resp_refund() == 0
    KS._memo.clear(); row = next(x for x in KS.month_calc(ym)["staff"] if x["worker_id"] == kwn)
    assert abs(row["ded"] - 125000) < 2, row["ded"]
    assert any(ch == 777 and "Ойлигингизга қайтди" in t for ch, t in sent), sent
    con.execute("UPDATE erp_clients SET debt1c=COALESCE(debt1c,0)-? WHERE ext=?", (dbt, mx_)); con.commit()   # қарз тўлиқ ёпилди
    assert erp.resp_refund() == 1
    KS._memo.clear(); assert abs(next(x for x in KS.month_calc(ym)["staff"] if x["worker_id"] == kwn)["ded"]) < 2
    assert A.scalar("SELECT COUNT(*) FROM erp_resp_ded WHERE ref_id=?", (did_,)) == 2
assert "мижоз тўлади — қайтди" in c.get(f"/erp/doc/{rsd}").get_data(as_text=True)
tok(); c.post(f"/erp/doc/{rsd}/resp", data={"csrf": "t", "act": "ded_del", "id": str(did_)})
with A.app.app_context():
    KS._memo.clear(); assert next(x for x in KS.month_calc(ym)["staff"] if x["worker_id"] == kwn)["ded"] == 0
print("RESP OK")

# кассир: мижоз накладнойларини кўриш ва чоп этиш (фақат кўриш)
with A.app.app_context():
    from werkzeug.security import generate_password_hash
    A.db().execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", ("kassir9", generate_password_hash("Kassa12345"), "kassa")); A.db().commit()
    sd = A.q("SELECT id FROM erp_docs WHERE kind='sale' AND src='1c' AND status='posted' ORDER BY id DESC LIMIT 1", one=True)[0]
    rd = A.q("SELECT id FROM erp_docs WHERE kind IN ('receipt','reval') LIMIT 1", one=True)[0]
    A.db().execute("DELETE FROM erp_locks") if A.q("SELECT name FROM sqlite_master WHERE name='erp_locks'") else None
    A.db().commit()
ck = A.app.test_client()
with ck.session_transaction() as s_: s_["csrf"] = "t"
assert ck.post("/login", data={"username": "kassir9", "password": "Kassa12345", "csrf": "t"}).status_code == 302
with A.app.app_context():
    import roles as _R
    def ksecs(*ss):
        A.db().execute("UPDATE roles SET sections=? WHERE key='kassa'", (__import__("json").dumps(list(ss)),)); A.db().commit(); _R.reload()
    ksecs("kassa", "sales", "pay_in")
r_ = ck.get("/erp?kind=receipt", follow_redirects=True)
h = r_.get_data(as_text=True)
assert "Янги накладной" in h and ">Накладные<" in h.split("</header>")[0] and "Поступление товара" not in h and 'kind=receipt"' not in h.split("</header>")[0] and "Поступление денег" in h, h[:300]
assert "+ Поступление денег" in ck.get("/erp?kind=pay", follow_redirects=True).get_data(as_text=True)
h = ck.get(f"/erp/doc/{sd}").get_data(as_text=True)
assert "Накладная $" in h and "Нусха олиш" in h and "erp_doc_resp" not in h
assert ck.get(f"/erp/doc/{sd}/print?form=uzs").status_code == 200
assert ck.get(f"/erp/doc/{rd}").status_code == 404
assert ck.get("/erp/sale/new").status_code == 200 and ck.get("/erp/pay/new2").status_code == 200 and ck.get("/1c/clients").status_code == 302
assert ck.get("/erp/receipt/new2").status_code == 404 and ck.get("/erp/accounts").status_code == 302
assert "pickGood" in ck.get("/erp/goods").get_data(as_text=True)
with ck.session_transaction() as s_: s_["csrf"] = "t"
ck.post(f"/erp/doc/{sd}/status", data={"csrf": "t", "act": "delete"})
with A.app.app_context():
    assert A.q("SELECT status FROM erp_docs WHERE id=?", (sd,), one=True)[0] == "posted"     # ўчириш — кассирга йўқ
with A.app.app_context():
    ksecs("kassa", "sales", "pay_in", "receipts", "stock", "clients")
    rr = A.q("SELECT id FROM erp_docs WHERE kind='receipt' LIMIT 1", one=True)
h = ck.get("/erp?kind=receipt", follow_redirects=True).get_data(as_text=True)
assert "+ Поступление товара" in h                     # рухсат ёқилган: кассир приход ёза олади
if rr:
    assert ck.get(f"/erp/doc/{rr[0]}").status_code == 200
assert ck.get("/erp/receipt/new2").status_code == 200 and ck.get("/erp/rep/mutual").status_code == 302 and ck.get("/erp/rep/stock").status_code == 200
assert ck.get("/erp/clients").status_code == 200
with A.app.app_context():
    ksecs("kassa", "sales", "pay_in")
assert ck.get("/erp/receipt/new2").status_code == 404                # рухсат ўчирилса — йўқ
print("KASSA DOCS OK")
print("ERP OK")

# конструктор/менежер (проведен қила олмайди): тўлов майдонлари йўқ, юборилган тўлов қабул қилинмайди
with A.app.app_context():
    A.db().execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", ("konstr9", generate_password_hash("Konstr12345"), "basis")); A.db().commit()
cb = A.app.test_client()
with cb.session_transaction() as s_: s_["csrf"] = "t"
r_ = cb.post("/login", data={"username": "konstr9", "password": "Konstr12345", "csrf": "t"}); assert r_.status_code == 302, __import__("re").findall(r"flash[^>]*>([^<]+)", r_.get_data(as_text=True))
h = cb.get("/erp/sale/new").get_data(as_text=True)
assert 'name="pay_usd"' not in h and 'name="pay_uzs"' not in h and "pmadd" in h
_ct = __import__("re").search(r'name="csrf" value="([^"]+)"', h).group(1)
with A.app.app_context():
    g1 = A.q("SELECT ext FROM erp_goods WHERE folder=0 AND deleted=0 AND price>0 LIMIT 1", one=True)[0]
    c1x = A.q("SELECT ext FROM erp_clients WHERE folder=0 AND deleted=0 AND COALESCE(blocked,0)=0 LIMIT 1", one=True)[0]
r = cb.post("/erp/sale/new", data={"csrf": _ct, "client": c1x, "rate": "12000", "nal": "1", "act": "save", "pay_usd": "50", "pay_uzs": "100000",
                                    "pm_method": ["1"], "pm_amount": ["5"], "pm_cur": ["usd"], "good": [g1], "qty": ["1"], "price": ["2"], "lnote": [""],
                                    "date": __import__("datetime").date.today().isoformat()})
_h=r.get_data(as_text=True); assert r.status_code == 302, (r.status_code, [_h[m.start()-200:m.start()+200] for m in __import__("re").finditer("error|ошибк|хато", _h)][:2])
with A.app.app_context():
    d = A.q("SELECT * FROM erp_docs ORDER BY id DESC LIMIT 1", one=True)
    assert d["status"] == "draft" and not d["pay_usd"] and not d["pay_uzs"] and not d["paid"], dict(d)
    assert A.scalar("SELECT COUNT(*) FROM erp_pays WHERE doc_id=?", (d["id"],)) == 0
print("NOPAY OK")
