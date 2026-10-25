"""Платформа каталоги: Eman'дан товарлар (склад бўйича), 1С'дан хизматлар, кромка хизматлари (нусха + 1С нархи + норматив)."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, catalog as C, erp
erp.norms = lambda: {"S4": [("GL", 0.005)]}
A.app.testing = True
con = A.connect()
E = lambda *a: con.execute(*a)
# 1С: «2 Кромка» / «Кромка Huali 19/1 Однотонные» (4500), «3 Кромка Овал» / «Кромка 19/1 ПВХ» (6900), «1 Распил» / «Распил ЛДСП» (1000)
for ext, name, par, fold, price in (("F2", "2 Кромка", "0", 1, 0), ("F2a", "Кромка Huali 19/1 Однотонные", "F2", 1, 0),
                                    ("S1", "Кромка 19/1 ПВХ Белый", "F2a", 0, 4500), ("F3", "3 Кромка Овал", "0", 1, 0),
                                    ("S2", "Кромка 19/1 ПВХ", "F3", 0, 6900), ("F1", "1 Распил", "0", 1, 0), ("S3", "Распил ЛДСП", "F1", 0, 1000),
                                    ("G1", "ЛДСП белый", "0", 0, 30), ("F2u", "Услуга ПВХ", "F2", 1, 0), ("S4", "Услуга ПВХ от 19/04 до 22/2", "F2u", 0, 3000), ("GL", "Клей прямой Хенкел ПУР", "0", 0, 5)):
    E("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,cat,src) VALUES(?,?,?,?,?,?,0,?,?,0,?,?,'1c')",
      (ext, ext, name, "м", 1, price, par, fold, " " + name.lower() + " ", "svc" if ext.startswith("S") else ""))
E("INSERT INTO eman_nodes(id,key,parent,name,kind,sort) VALUES(1,'root',0,'Каталог','folder',0)")
for i, k, p, n, s in ((2, "kr", 1, "Кромка", 2), (3, "hu", 2, "Кромка Huali", 0), (4, "h19", 3, "Кромка ПВХ 19/1 Однотонные", 0),
                      (5, "ld", 1, "ЛДСП", 1), (9, "sw", 5, "Старвуд ЛДСП", 0), (6, "kl", 1, "Клей", 3), (7, "ye", 1, "Yechim", 4), (8, "in", 7, "Инструменты", 0)):
    E("INSERT INTO eman_nodes(id,key,parent,name,kind,sort) VALUES(?,?,?,?,'folder',?)", (i, k, p, n, s))
for nd, t, pr in ((4, "Кромка 19/1 Белый", 1200), (4, "Кромка 19/0,4 Белый", 500), (5, "ЛДСП Эггер W980", 400000), (6, "Клей Эман", 1), (8, "Отвертка", 5)):
    E("INSERT INTO eman_goods(node,title,price,cur,unit) VALUES(?,?,?,'сум','шт')", (nd, t, pr))
con.commit()
st = C.build(con)
assert st["goods"] == 4, st                                     # Eman «Клей» олинмайди
whm = C.node_wh(con)
nid = lambda k: con.execute("SELECT id FROM cat_nodes WHERE skey=?", (k,)).fetchone()[0]
assert whm[nid("e:ld")] == C.WH_PLATE and whm[nid("e:kr")] == C.WH_EDGE and whm[nid("e:ye")] == C.WH_FURN and whm[nid("e:in")] == C.WH_TOOL
assert not con.execute("SELECT 1 FROM cat_nodes WHERE skey='e:kl'").fetchone()
pvc = con.execute("SELECT * FROM cat_items WHERE kind='svc_pvc' AND name LIKE '%19/1%' AND name NOT LIKE '%GOLD%'").fetchone()
assert pvc["name"] == "Услуга ПВХ 19/1 Белый" and pvc["price"] == 4500 and pvc["c1_src"] == "F:F2a", dict(pvc)
assert con.execute("SELECT COUNT(*) FROM cat_items WHERE kind='svc_oval' AND deleted=0 AND name NOT LIKE '%GOLD%'").fetchone()[0] == 1   # 19/0,4 — овал йўқ
assert con.execute("SELECT COUNT(*) FROM cat_items WHERE skey LIKE 'x:gold:%'").fetchone()[0] == 4             # GOLD (бозор) 19/1 … 42/1
ov = con.execute("SELECT * FROM cat_items WHERE kind='svc_oval' AND name NOT LIKE '%GOLD%'").fetchone()
assert ov["name"] == "Услуга Овал 19/1 Белый" and ov["price"] == 6900, dict(ov)
kr = con.execute("SELECT id FROM cat_items WHERE skey='e:h19|Кромка 19/1 Белый'").fetchone()[0]
nr = {r["comp"]: r["qty"] for r in con.execute("SELECT * FROM cat_norms WHERE item=?", (pvc["id"],))}
glue = con.execute("SELECT id FROM cat_items WHERE skey='g:pur'").fetchone()[0]
assert nr == {kr: 1.0, glue: round(0.168 * 0.016, 5)}, nr
assert con.execute("SELECT price FROM cat_items WHERE skey='c:S3'").fetchone()[0] == 1000
names = {r[0] for r in con.execute("SELECT name FROM cat_items WHERE svc=1")}
assert {"Доставка", "Упаковка", "Грузчик", "Сборка"} <= names, names
# хизматлар — тепада
top = [r[0] for r in con.execute("SELECT name FROM cat_nodes WHERE parent=0 AND deleted=0 ORDER BY svc DESC, sort, name")]
assert top[0] == "Услуги", top
sv = [r[0] for r in con.execute("SELECT name FROM cat_nodes WHERE parent=(SELECT id FROM cat_nodes WHERE skey='s:root') ORDER BY sort")]
assert sv[0] == "Распил" and "Кромка" in sv and "Кромка Овал" in sv and not any("кромка клиента" in x or "наша кромка" in x for x in sv), sv
ko = [r[0] for r in con.execute("SELECT name FROM cat_nodes WHERE parent=(SELECT id FROM cat_nodes WHERE skey='k:oval') AND deleted=0 ORDER BY sort, name")]
assert ko and ko[0] == "Услуга Овал", ko
# 1С нархи ўзгарса — хизмат нархи ҳам
E("UPDATE erp_goods SET price=5000 WHERE ext='S1'"); con.commit()
C.sync_prices(con); con.commit()
assert con.execute("SELECT price FROM cat_items WHERE id=?", (pvc["id"],)).fetchone()[0] == 5000
# қўлда ўзгартирилган ном қайта қуришда сақланади
E("UPDATE cat_items SET name='Менинг номим', manual=1 WHERE id=?", (pvc["id"],)); con.commit()
C.build(con)
assert con.execute("SELECT name FROM cat_items WHERE id=?", (pvc["id"],)).fetchone()[0] == "Менинг номим"
assert C.build(con)["goods"] == 4 and con.execute("SELECT COUNT(*) FROM cat_items WHERE kind='good' AND skey NOT LIKE 'x:gold:%'").fetchone()[0] == 6   # 3 Eman + 2 клей
# саҳифа ва API
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
assert c.get("/erp/catalog").status_code == 200
assert any(x["name"] == "Услуги" for x in c.get("/erp/api/cat/tree").get_json())
r = c.get("/erp/api/cat/list?q=белый").get_json()
assert any(x["name"].startswith("Услуга Овал") for x in r["items"]), r
r = c.get(f"/erp/api/cat/item/{pvc['id']}").get_json()
assert len(r["norms"]) == 2
with c.session_transaction() as s: tok = s.get("csrf") or "t"; s["csrf"] = tok
r = c.post("/erp/api/cat/save", json={"act": "item", "node": nid("g:glue"), "name": "Клей ПВА", "unit": "кг", "cur": 1, "price": "25000"},
           headers={"X-CSRF-Token": tok})
assert r.status_code == 200, r.get_data(as_text=True)
u = con.execute("SELECT i.*, n.name nn FROM cat_items i JOIN cat_nodes n ON n.id=i.node WHERE i.skey='c:S4'").fetchone()
assert u and u["price"] == 3000 and u["nn"] == "Услуга ПВХ" and con.execute("SELECT n2.skey FROM cat_nodes n JOIN cat_nodes n2 ON n2.id=n.parent WHERE n.id=?", (u["node"],)).fetchone()[0] == "k:pvc", dict(u) if u else None
assert {r["comp"]: r["qty"] for r in con.execute("SELECT * FROM cat_norms WHERE item=?", (u["id"],))} == {glue: round(0.168 * 0.016, 5)}   # мижоз кромкаси — фақат клей
n9 = con.execute("SELECT id, name FROM cat_nodes WHERE skey='e:sw'").fetchone()
assert n9["name"] == "Эконом ЛДСП", n9["name"]
sm = C.node_sup(con)
assert sm[n9["id"]] == "Назиф дунё, Гилам Сарой, Ибн Сино, Панельный" and sm[nid("e:ld")] == "Эман" and sm[nid("e:kr")] == "Эман", sm
# эски гуруҳ номи («… (Панельный)», «Панельный (Назим дунё …)») базада бўлса — янгисига ўзгаради
con.execute("UPDATE cat_nodes SET sup=? WHERE id=?", ("Панельный (Назим дунё, Гилам Сарой, Ибн Сино)", n9["id"]))
con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('pickup_sup_map', ?)", ('{"Назиф дунё, Гилам Сарой, Ибн Сино (Панельный)": "C5"}',))
C.rename_sup(con); con.commit()
assert con.execute("SELECT sup FROM cat_nodes WHERE id=?", (n9["id"],)).fetchone()[0] == C.SUPPLIERS[1]
mp = con.execute("SELECT value FROM settings WHERE key='pickup_sup_map'").fetchone()[0]
assert mp == '{"Назиф дунё, Гилам Сарой, Ибн Сино, Панельный": "C5"}', mp
# папка бўйича клей нормативи (ўнг тугма → «Норматив папки»): кромка 1:1 ўзгармайди; қайта қурилганда сақланади
pf = con.execute("SELECT node FROM cat_items WHERE kind='svc_pvc' AND deleted=0 LIMIT 1").fetchone()[0]
top = pf
while con.execute("SELECT parent FROM cat_nodes WHERE id=?", (top,)).fetchone()[0] and con.execute("SELECT skey FROM cat_nodes WHERE id=?", (top,)).fetchone()[0] != "k:pvc":
    top = con.execute("SELECT parent FROM cat_nodes WHERE id=?", (top,)).fetchone()[0]
inf = c.get(f"/erp/api/cat/fnorm/{top}").get_json()
oval_g = con.execute("SELECT id FROM cat_items WHERE skey='g:oval'").fetchone()[0]
assert inf["n"] >= 1 and inf["cur_glue"] == glue and {g["id"] for g in inf["glues"]} >= {glue, oval_g}, inf
assert c.get(f"/erp/api/cat/fnorm/{nid('g:glue')}").status_code == 400                  # товар папкаси эмас
svcs = [r[0] for r in con.execute("SELECT id FROM cat_items WHERE kind='svc_pvc' AND deleted=0")]
edge_before = {i: [(r[0], r[1]) for r in con.execute("SELECT comp, qty FROM cat_norms WHERE item=? AND comp NOT IN (?,?)", (i, glue, oval_g))] for i in svcs}
r = c.post("/erp/api/cat/save", json={"act": "fnorm", "id": top, "glue": oval_g, "mode": "width", "value": "0,2"}, headers={"X-CSRF-Token": tok}).get_json()
assert r["ok"] and r["n"] == inf["n"], r
nr = {r_["comp"]: r_["qty"] for r_ in con.execute("SELECT * FROM cat_norms WHERE item=?", (pvc["id"],))}
assert glue not in nr and nr[oval_g] == round(0.2 * 0.016, 5), nr                      # 19/1 → 16 мм
C.build(con); con.commit()
nr = {r_["comp"]: r_["qty"] for r_ in con.execute("SELECT * FROM cat_norms WHERE item=?", (pvc["id"],))}
assert glue not in nr and nr[oval_g] == round(0.2 * 0.016, 5), nr                      # қайта қурилганда ПУР қайтмайди
assert all([(r_[0], r_[1]) for r_ in con.execute("SELECT comp, qty FROM cat_norms WHERE item=? AND comp NOT IN (?,?)", (i, glue, oval_g))] == edge_before[i] for i in svcs)
r = c.post("/erp/api/cat/save", json={"act": "fnorm", "id": top, "glue": glue, "mode": "fixed", "value": "0.003"}, headers={"X-CSRF-Token": tok}).get_json()
assert {r_["comp"]: r_["qty"] for r_ in con.execute("SELECT * FROM cat_norms WHERE item=?", (pvc["id"],))}.get(glue) == 0.003
assert c.post("/erp/api/cat/save", json={"act": "fnorm", "id": top, "glue": 999999, "mode": "fixed", "value": "1"}, headers={"X-CSRF-Token": tok}).status_code == 400
# Эггер ПВХ кромкага Эггер ABS нархи олинмайди (эгаси, 25.10: «АБС қиммат, ПВХ арзон»)
pidx = [("E1", "egger", ("19", "0.8"), set(), "abs"), ("E2", "egger", ("35", "2"), set(), "pvc")]
assert C.match_pvc(pidx, "egger", ("19", "0.8"), set(), "pvc") is None and C.match_pvc(pidx, "egger", ("19", "0.8"), set(), "abs") == "E1"
assert C.match_pvc(pidx, "egger", ("35", "2"), set(), "pvc") == "E2" and C.match_pvc(pidx, "egger", ("19", "0.8"), set()) == "E1"
oidx = [("O1", "egger", ("19", "0.8"), {"abs"}, "abs"), ("O2", "", ("19", "1"), set(), "pvc")]
assert C.match_oval(oidx, "egger", ("19", "0.8"), set(), "pvc") is None and C.match_oval(oidx, "egger", ("19", "1"), set(), "pvc") is None
assert C.match_oval(oidx, "egger", ("19", "0.8"), set(), "abs") == "O1" and C.mat_of("Кромка Эггер 19/0,8 Бежевый ПВХ ST9") == "pvc"
# папкага битта нарх (ўнг тугма → «Цена папки…»): қайта қурилганда сақланади, 1С нархи қўйилмайди
r = c.post("/erp/api/cat/save", json={"act": "fprice", "id": top, "price": "7 500"}, headers={"X-CSRF-Token": tok}).get_json()
assert r["ok"] and r["n"] == inf["n"], (r, inf)
C.build(con); con.commit()
assert {x[0] for x in con.execute("SELECT price FROM cat_items WHERE id IN (%s)" % ",".join(map(str, svcs)))} == {7500.0}
assert c.post("/erp/api/cat/save", json={"act": "fprice", "id": top, "price": "0"}, headers={"X-CSRF-Token": tok}).status_code == 400
# папка ичидагилари билан «помечен на удаление»; қайта қурилганда тикланмайди (эгаси, 24.10)
f1 = c.post("/erp/api/cat/save", json={"act": "folder", "name": "Тест папка", "parent": nid("g:glue")}, headers={"X-CSRF-Token": tok}).get_json()["id"]
f2 = c.post("/erp/api/cat/save", json={"act": "folder", "name": "Ички", "parent": f1}, headers={"X-CSRF-Token": tok}).get_json()["id"]
i2 = c.post("/erp/api/cat/save", json={"act": "item", "node": f2, "name": "Тест товар", "unit": "шт", "cur": 1, "price": "1"}, headers={"X-CSRF-Token": tok}).get_json()["id"]
r = c.post("/erp/api/cat/save", json={"act": "del", "folders": [f1]}, headers={"X-CSRF-Token": tok})
assert r.status_code == 200, r.get_data(as_text=True)
assert [con.execute(q, (x,)).fetchone()[0] for q, x in (("SELECT deleted FROM cat_nodes WHERE id=?", f1), ("SELECT deleted FROM cat_nodes WHERE id=?", f2),
                                                         ("SELECT deleted FROM cat_items WHERE id=?", i2))] == [1, 1, 1]
gl_n = con.execute("SELECT id FROM cat_nodes WHERE skey='g:glue'").fetchone()[0]
c.post("/erp/api/cat/save", json={"act": "del", "folders": [gl_n]}, headers={"X-CSRF-Token": tok})
C.build(con); con.commit()
assert con.execute("SELECT deleted FROM cat_nodes WHERE skey='g:glue'").fetchone()[0] == 1           # қурилган папка ҳам қайтмайди
# 1С «МДФ Эконом» — каталог МДФ ичида (эгаси, 25.10)
con.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src) VALUES('MF','MF','МДФ Эконом','',2,0,'0',1,0,' мдф эконом ','1c')")
con.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src) VALUES('MF1','MF1','МДФ Шлифованный 12мм 5,0325','лист',2,21,'MF',0,0,' мдф ','1c')")
con.commit(); C.build(con); con.commit()
mf = con.execute("SELECT id, name FROM cat_nodes WHERE skey='c1g:MF' AND deleted=0").fetchone()
assert mf and mf["name"] == "МДФ Эконом" and con.execute("SELECT name FROM cat_items WHERE node=? AND skey='c1g:MF1'", (mf["id"],)).fetchone()[0] == "МДФ Шлифованный 12мм 5,0325"
print("CATALOG OK")
