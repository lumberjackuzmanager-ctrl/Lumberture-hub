"""Накладнойда «Конструктор» ва «Кройчи»: конструктор — «Услуга конструктора» суммаси, кройчи — листлар сони (платформадан автомат)."""
import os, sys, tempfile
from datetime import date
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp, raspil as P
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok(); assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
con = A.connect()
for ext, name, unit, cur, price in (("K1", "Услуга конструктора СТАНДАРТ", "кв-м", 1, 15000), ("L1", "ЛДСП белый 16мм", "лист", 2, 40),
                                    ("H1", "ХДФ белый", "лист", 2, 10), ("S1", "Распил", "шт", 1, 20000)):
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,src) VALUES(?,?,?,?,?,?,0,'0',0,0,?,'1c')",
                (ext, ext, name, unit, cur, price, " " + name.lower() + " "))
con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,ckind,lim) VALUES('C1','C1','Мижоз','','',  '0',0,0,' мижоз ','1c','client',100000)")
kon = con.execute("INSERT INTO k_workers(name,cur,sort,active) VALUES('Абдулахад','USD',10,1)").lastrowid
kro = con.execute("INSERT INTO k_workers(name,cur,sort,active) VALUES('Нодирбек','UZS',20,1)").lastrowid
con.execute("INSERT INTO ready_workers(name,phone,active,kw_id,kroy,role,created_at) VALUES('Нодирбек','998901112233',1,?,1,'staff','2026-10-01')", (kro,))
con.commit()
P.basis_map = lambda: {"F1": kon}
today = date.today().isoformat()
base = {"csrf": "t", "client": "C1", "rate": "12000", "nal": "1", "note": "", "date": today, "minus_ok": "1",
        "good": ["K1", "L1", "H1", "S1"], "qty": ["10", "5", "2", "5"], "price": ["15000", "40", "10", "20000"], "lnote": ["", "", "", ""]}
h = c.get("/erp/sale/new").get_data(as_text=True)
assert 'name="konstr"' in h and "Абдулахад" in h and 'name="kroychi"' in h and "Нодирбек" in h
# «Услуга конструктора» бор, конструктор танланмаган — проведен бўлмайди
tok(); r = c.post("/erp/sale/new", data=dict(base, act="post"), follow_redirects=True)
assert "Конструктор» майдонида" in r.get_data(as_text=True), r.get_data(as_text=True)[:200]
tok(); r = c.post("/erp/sale/new", data=dict(base, act="post", konstr=str(kon), kroychi=str(kro)))
assert r.status_code == 302, r.get_data(as_text=True)[:400]
d = con.execute("SELECT * FROM erp_docs WHERE src='pf' AND kind='sale' ORDER BY id DESC").fetchone()
assert d["konstr"] == kon and d["kroychi"] == kro and d["status"] == "posted"
h = c.get(f"/erp/doc/{d['id']}").get_data(as_text=True)                  # накладнойда кройчи/конструктор ва ҳолат кўриниб турсин (26.10)
assert "<b>Кройчи</b>: <b>Нодирбек" in h and "<b>Конструктор</b>: <b>Абдулахад" in h and 'class="dst posted"' in h and 'dcard posted' in h, h[:300]
ym = today[:7]
ctx = A.app.app_context(); ctx.push()
b = P.pf_basis_days(ym)
assert round(b[kon][0][int(today[8:])]) == 150000 and round(b[kon][1][int(today[8:])], 2) == 12.5, b
k = P.pf_kroy_docs(ym)
assert k and k[0]["main"] == 5 and k[0]["hdf"] == 0 and k[0]["kw"] == kro, k          # крой — распил сонидан (5 та «Распил»)
km = P.kroy_month(ym)
assert km[kro]["sh_main"] == 5 and km[kro]["sh_hdf"] == 0 and km[kro]["docs"] == 1
ku = P.kroy_units
assert ku("Распил ЛДСП/ЛМДФ 5,796 кв (Пакетный)", 4) == (4, 0) and ku("Распил ХДФ", 3) == (0, 3) and ku("Распил Ташкаридан", 2) == (2, 0)
assert ku("Распил Столешница", 2) == (0, 0) and ku("Распил под 45*", 3) == (0, 0) and ku("Распил под углом", 1) == (0, 0) and ku("Распил Кромка", 9) == (0, 0)
assert ku("ЛДСП белый 16мм", 5) == (0, 0) and abs(ku("Распил Кусок", 5.796)[0] - 1) < 1e-9 and ku("Распил ЛДСП", -2) == (-2, 0)
# возврат: листлар айрилади
tok(); r = c.post("/erp/sale/new", data=dict(base, act="post", good=["L1"], qty=["-2"], price=["40"], lnote=[""], kroychi=str(kro)))
assert r.status_code == 302, r.get_data(as_text=True)[:300]
A.db().commit()
assert sum(x["main"] for x in P.pf_kroy_docs(ym)) == 5            # минусли накладной алоҳида — 0 дан кам эмас
assert not P.kroy_bot(dict(id=con.execute("SELECT id FROM ready_workers").fetchone()[0]))     # ботда тугма йўқ
# ⚡ СРОЧНО: распил жуфти ва кромка рўйхати; белги сақланади ва чопда чиқади
for ext, name, par, fold, price in (("FR", "1 Распил", "0", 1, 0), ("FRo", "1 Распил ОБЫЧНЫЙ", "FR", 1, 0), ("FRs", "2 Распил СРОЧНО", "FR", 1, 0),
                                    ("R1", "Распил ЛДСП/ЛМДФ 5,796 кв", "FRo", 0, 37000), ("R2", "СРОЧНЫЙ Распил ЛДСП/ЛМДФ 5,796 кв", "FRs", 0, 45000),
                                    ("FK", "2 Кромка", "0", 1, 0), ("KR1", "Кромка 19/1 ПВХ Белый", "FK", 0, 4500)):
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,src) VALUES(?,?,?,'шт',1,?,0,?,?,0,?,'1c')",
                (ext, ext, name, price, par, fold, " " + name.lower() + " "))
con.commit()
um = erp.urgent_map(A.db())
assert um["add"].get("KR1") == 1500 and um["add"].get("R1") == 25000 and "R2" not in um["add"], um   # 1С'даги эски срочный нархлари ишлатилмайди
# каталог хизматлари (26.10: 1С тўхтагач накладнойда каталог товарлари) — «Услуги / Кромка», «Кромка Овал», «Распил … ОБЫЧНЫЙ»
for ext, name, par, fold in (("cU", "Услуги", "", 1), ("cK", "Кромка", "cU", 1), ("cKO", "Кромка Овал", "cU", 1), ("cR", "Распил", "cU", 1),
                             ("cRo", "Распил ПАКЕТНЫЙ ОБЫЧНЫЙ", "cR", 1), ("ciK", "Услуга ПВХ Кашемир 19/0.8", "cK", 0), ("ciO", "Овал Кашемир", "cKO", 0),
                             ("ciR", "Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", "cRo", 0), ("ciP", "Распил КУСОК (Остатка) (Пакетный)", "cRo", 0),
                             ("ciS", "Распил Столешница", "cRo", 0), ("cL", "ЛДСП", "", 1), ("ciL", "ЛДСП Кромка Белый", "cL", 0)):
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,src,cat) VALUES(?,?,?,'шт',1,1000,0,?,?,0,'','cat','svc')",
                (ext, ext, name, par, fold))
con.commit()
um = erp.urgent_map(A.db())["add"]
assert um.get("ciK") == 1500 and um.get("ciO") == 1500 and um.get("ciR") == 25000 and um.get("ciP") == 4300 and "ciS" not in um and "ciL" not in um, um
A.set_settings(urgent_extra='{"R1": 8000}')
assert erp.urgent_map(A.db())["add"]["R1"] == 8000
A.set_settings(urgent_extra="")
tok(); r = c.post("/erp/sale/new", data=dict(base, act="post", konstr=str(kon), urgent="1", good=["K1", "R2", "KR1"], qty=["1", "1", "10"],
                                             price=["15000", "45000", "5000"], lnote=["", "", ""]))
assert r.status_code == 302, r.get_data(as_text=True)[:300]
nid = int(r.headers["Location"].rstrip("/").split("/")[-1])
assert A.db().execute("SELECT urgent, urgent_free FROM erp_docs WHERE id=?", (nid,)).fetchone()[:] == (1, 0)
h = c.get(f"/erp/doc/{nid}/print?form=perm").get_data(as_text=True); assert 'class="urgb">СРОЧНО' in h
h = c.get(f"/erp/doc/{nid}/print?form=uzs").get_data(as_text=True); assert 'class="urgt">СРОЧНО' in h
h = c.get(f"/erp/doc/{nid}/edit").get_data(as_text=True); assert 'id="urgb"' in h and "urgb on" in h
print("STAFF OK")
