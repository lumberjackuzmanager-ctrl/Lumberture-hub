"""Роллар: тайёр роллар (Кассир, Менеджер, Конструктор), янги роль (бўлим) қўшиш, бўлим бўйича доступ, фойдаланувчини сотрудникка боғлаш."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, roles as R
A.app.testing = True
c = A.app.test_client()
def post(cl, u, d):
    with cl.session_transaction() as s: s["csrf"] = "t"
    return cl.post(u, data=dict(d, csrf="t"))
def login(u, p):
    cl = A.app.test_client()
    with cl.session_transaction() as s: s["csrf"] = "t"
    assert cl.post("/login", data={"username": u, "password": p, "csrf": "t"}).status_code == 302
    return cl
c = login("admin", "admin123")
for k in ("admin", "cashier", "kassa", "manager", "basis"):
    assert k in A.ROLES, k
assert A.ROLES["manager"] == "Менеджер (крой)" and A.ROLES["basis"] == "Конструктор (Базис)" and A.ROLES["kassa"] == "Кассир"
h = c.get("/admin/users").get_data(as_text=True)
assert "Пользователи и роли" in h and "Менеджер (крой)" in h and "+ Новая роль" in h and "Конструктор (Базис)" in h
assert c.get("/admin/roles/new").status_code == 200 and c.get("/admin/roles/admin").status_code == 302
# янги роль: «Склад» — фақат остаток ва приход
r = post(c, "/admin/roles/new", {"name": "Склад", "kind": "limited", "s_stock": "1", "s_receipts": "1", "note": "приход и остатки", "d_add": "1"})
assert r.status_code == 302
key = [k for k, v in A.ROLES.items() if v == "Склад"][0]
assert R.role(key)["secs"] == ["receipts", "stock"]
assert post(c, "/admin/roles/new", {"name": "Пусто", "kind": "limited"}).status_code == 302 and "Пусто" not in A.ROLES.values()
# сотрудник билан фойдаланувчи
with A.app.app_context():
    wid = A.db().execute("INSERT INTO k_workers(name, salary, cur, sort, active) VALUES('Азиз', 0, 'UZS', 1, 1)").lastrowid; A.db().commit()
r = post(c, "/admin/users", {"username": "sklad1", "full_name": "Азиз", "password": "Sklad2026x", "role": key, "worker": str(wid), "p_add": "on"})
assert r.status_code == 302
with A.app.app_context():
    u = A.q("SELECT * FROM users WHERE username='sklad1'", one=True)
    assert u["role"] == key and A.q("SELECT user_id FROM k_workers WHERE id=?", (wid,), one=True)[0] == u["id"]
# хато: ёзилганлар сақланади
post(c, "/admin/users", {"username": "xx1", "full_name": "Тест Ном", "password": "123", "role": "manager"})
h = c.get("/admin/users").get_data(as_text=True)
assert 'value="xx1"' in h and 'value="Тест Ном"' in h
s = login("sklad1", "Sklad2026x")
r = s.get("/"); assert r.status_code == 302 and "/erp" in r.headers["Location"]          # бош саҳифа — ўз бўлимига
assert s.get("/erp/rep/stock").status_code == 200 and s.get("/erp?kind=receipt").status_code == 200
assert s.get("/kassa").status_code == 302 and s.get("/erp/rep/mutual").status_code == 302 and s.get("/admin/users").status_code in (302, 403)
assert s.get("/erp/sale/new").status_code == 302                                         # накладной — йўқ
h = s.get("/erp?kind=receipt", follow_redirects=True).get_data(as_text=True)
nav = h.split("</header>")[0]
assert "Остаток товаров" in nav and "Кассовый день" not in nav and "Накладная" not in nav, nav[-1500:]
# менеджер (крой)
post(c, "/admin/users", {"username": "kroy1", "password": "Kroy2026xx", "role": "manager"})
m = login("kroy1", "Kroy2026xx")
assert m.get("/kassa/raspil").status_code == 302 and m.get("/erp/sale/new").status_code == 200 and m.get("/docs").status_code == 200
assert m.get("/kassa").status_code == 302 and m.get("/kassa/staff").status_code == 302
# ролни ўзгартириш дарҳол ишлайди
post(c, f"/admin/roles/{key}", {"name": "Склад", "kind": "limited", "s_stock": "1"})
assert s.get("/erp?kind=receipt").status_code in (302, 403) and s.get("/erp/rep/stock").status_code == 200
# «всё, кроме настроек»
post(c, f"/admin/roles/{key}", {"name": "Склад", "kind": "full"})
assert s.get("/kassa").status_code == 200 and s.get("/admin/settings").status_code == 403
# ўчириш: фойдаланувчиси бор — йўқ
post(c, f"/admin/roles/{key}", {"action": "delete"}); assert key in A.ROLES
post(c, "/admin/users", {"action": "delete", "id": str(u["id"])})
post(c, f"/admin/roles/{key}", {"action": "delete"}); assert key not in A.ROLES
# логинни ўзгартириш, фақат ишлаётган сотрудниклар
with A.app.app_context():
    A.db().execute("INSERT INTO k_workers(name, salary, cur, sort, active) VALUES('Эски ишчи', 0, 'UZS', 2, 0)"); A.db().commit()
    kid = A.q("SELECT id FROM users WHERE username='kroy1'", one=True)[0]
h = c.get(f"/admin/users?edit={kid}").get_data(as_text=True)
assert 'value="kroy1"' in h and "Эски ишчи" not in h and "Азиз" in h
post(c, "/admin/users", {"id": str(kid), "username": "Крой2", "password": "", "role": "manager", "active": "on"})
assert login("Крой2", "Kroy2026xx")
post(c, "/admin/users", {"id": str(kid), "username": "admin", "role": "manager", "active": "on"})
with A.app.app_context(): assert A.q("SELECT username FROM users WHERE id=?", (kid,), one=True)[0] == "Крой2"
# нотўғри раскладкада қидирув
import kb
assert kb.variants("vty [jpbh rtkfvfy")[1] == "мен хозир келаман" and "egger" in [v.casefold() for v in kb.variants("уппук")]
with A.app.app_context():
    A.db().execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc) VALUES('T1', 'Шавкат мебель', '', 0, 0, ' шавкат мебель ')")
    A.db().execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc) VALUES('G1', '001', 'ЛДСП Egger белый', 'лист', 1, 0, '', 0, 0, ' лдсп egger белый ')")
    A.db().commit()
h = c.get("/search?q=" + "ifdrfn").get_data(as_text=True)
assert "Шавкат мебель" in h , h[-800:]
h = c.get("/search?q=" + "уппук").get_data(as_text=True)
assert "ЛДСП Egger белый" in h
assert "Шавкат мебель" in json.dumps(c.get("/erp/api/clients?q=ifdrfn").get_json(), ensure_ascii=False)
assert "Egger" in json.dumps(c.get("/erp/api/goods?src=all&q=уппук").get_json(), ensure_ascii=False)
# провести — фақат кассир; бошқалар қоралама ёзади; проведенни кассир қораламага қайтаради (рақам ўша)
assert R.can_post({"role": "kassa"}) and not R.can_post({"role": "manager"}) and not R.can_post({"role": "cashier"}) and R.can_post({"role": "admin"})
assert R.role("cashier")["kind"] == "limited" and "kassa_month" in R.role("cashier")["secs"]
import datetime as _dt
with A.app.app_context():
    A.db().execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc) VALUES('G2', '002', 'Товар Т2', 'шт', 1, 10, '', 0, 0, ' товар т2 ')")
    A.db().execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc, lim) VALUES('C2', 'Мижоз Т2', '', 0, 0, ' мижоз т2 ', 100000)")
    A.db().execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (_dt.date.today().isoformat(), _dt.date.today().isoformat() + " 09:00:00")); A.db().commit()
sf = {"client": "C2", "rate": "12000", "nal": "1", "date": _dt.date.today().isoformat(), "minus_ok": "1", "good": ["G2"], "qty": ["1"], "price": ["10"], "lnote": [""]}
m = login("Крой2", "Kroy2026xx")
r = post(m, "/erp/sale/new", dict(sf, act="post")); import re as _re; assert r.status_code == 302, (r.status_code, _re.findall(r"flash [a-z]+\">([^<]*)", r.get_data(as_text=True)), r.headers.get("Location"))
did = int(r.headers["Location"].rstrip("/").split("/")[-1])
with A.app.app_context():
    d0 = A.q("SELECT status, no FROM erp_docs WHERE id=?", (did,), one=True); assert d0["status"] == "draft"
h = m.get(f"/erp/doc/{did}/edit").get_data(as_text=True)
assert "Записать и печать" in h and 'value="post"' not in h
post(m, f"/erp/doc/{did}/status", {"act": "post"})
with A.app.app_context(): assert A.q("SELECT status FROM erp_docs WHERE id=?", (did,), one=True)[0] == "draft"
post(c, "/admin/users", {"username": "kassa9", "password": "Kassa2026x", "role": "kassa"})
k = login("kassa9", "Kassa2026x")
with A.app.app_context(): A.db().execute("UPDATE erp_docs SET lock_by=NULL, lock_at=NULL"); A.db().commit()   # менежер ойнани ёпди
post(k, f"/erp/doc/{did}/status", {"act": "post", "minus_ok": "1"})
with A.app.app_context(): assert A.q("SELECT status FROM erp_docs WHERE id=?", (did,), one=True)[0] == "posted"
assert m.get(f"/erp/doc/{did}/edit").status_code == 302                 # проведенни менежер ўзгартирмайди
post(k, f"/erp/doc/{did}/status", {"act": "unpost"})
with A.app.app_context():
    d1 = A.q("SELECT status, no FROM erp_docs WHERE id=?", (did,), one=True); assert d1["status"] == "draft" and d1["no"] == d0["no"]
assert m.get(f"/erp/doc/{did}/edit").status_code == 200
with A.app.app_context(): A.db().execute("UPDATE erp_docs SET lock_by=NULL, lock_at=NULL"); A.db().commit()
post(m, f"/erp/doc/{did}/status", {"act": "delete"})
with A.app.app_context(): assert A.q("SELECT status FROM erp_docs WHERE id=?", (did,), one=True)[0] == "draft"   # ўчириш — йўқ
print("ROLES OK")
