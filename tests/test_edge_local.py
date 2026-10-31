"""31.10 (эгаси): маҳаллий (бозор) кромка — Eman'нинг «Кромка юМестная» ўрнига 1С'даги «Кромка Местная … (Базар)»: товарлар
(«Кромка» ичида «Кромка Местная (Базар)»), ПВХ ва Овал хизматлари (нархи 1С «2/3 Кромка»дан). Craftwood ва «Ташкаридан» — қолади.
Эски хизмат ишлатилган накладной тарихи бузилмайди (gone, ўчирилмайди)."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, catalog as C, erp
erp.norms = lambda: {}
con = A.connect()
E = lambda *a: con.execute(*a)
for ext, name, par, fold, price in (("1D", "", "0", 1, 0), ("BS", "Кромка Местная 19/1 (Базар)", "1D", 1, 0),
                                    ("L1", "Кромка 19/1 Чёрный (местный) (рулон 200м) (n)", "BS", 0, 0), ("L2", "Кромка 19/1 Кашемир текстура (местный) (рулон 200м) (n)", "BS", 0, 0),
                                    ("F2", "2 Кромка", "0", 1, 0), ("A4", "Кромка Местная 19/1 (Базар)", "F2", 1, 0), ("S1", "Кромка 19/1 Чёрный (местный) (рулон 200м) (n)", "A4", 0, 4500),
                                    ("F3", "3 Кромка Овал", "0", 1, 0), ("S2", "Кромка 19/1 ПВХ", "F3", 0, 6900)):
    E("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,cat,src) VALUES(?,?,?,?,?,?,0,?,?,0,?,?,'1c')",
      (ext, ext, name, "пог.м", 1, price, par, fold, " " + name.lower() + " ", "svc" if ext.startswith("S") else ""))
E("INSERT INTO eman_nodes(id,key,parent,name,kind,sort) VALUES(1,'root',0,'Каталог','folder',0)")
for i, k, p, n in ((2, "kr", 1, "Кромка"), (3, "lo", 2, "Кромка юМестная"), (4, "l19", 3, "Кромка Местная 19/1"), (5, "cw", 3, "Кромка Craftwood ( под заказ )")):
    E("INSERT INTO eman_nodes(id,key,parent,name,kind,sort) VALUES(?,?,?,?,'folder',0)", (i, k, p, n))
for nd, t in ((4, "Кромка 19/1 Белый (местный) (рулон 200м)"), (5, "Кромка 19/1 CFT табак ( под заказ )")):
    E("INSERT INTO eman_goods(node,title,price,cur,unit) VALUES(?,?,1098,'сум','м')", (nd, t))
E("UPDATE erp_goods SET deleted=1 WHERE ext='BS'")                    # аввал 1С'да маҳаллий папка йўқ — Eman'ники қолади
con.commit()
C.build(con); con.commit()
assert con.execute("SELECT gone FROM cat_items WHERE name='Кромка 19/1 Белый (местный) (рулон 200м)'").fetchone()[0] == 0
E("UPDATE erp_goods SET deleted=0 WHERE ext='BS'"); con.commit()
C.build(con); con.commit()
old = con.execute("SELECT id FROM cat_items WHERE name='Кромка 19/1 Белый (местный) (рулон 200м)'").fetchone()
assert old and con.execute("SELECT gone FROM cat_items WHERE id=?", (old[0],)).fetchone()[0] == 1          # Eman маҳаллийси — ўрнига 1С'дагиси
cw = con.execute("SELECT gone FROM cat_items WHERE name='Кромка 19/1 CFT табак ( под заказ )'").fetchone()
assert cw and cw[0] == 0                                                                                    # Craftwood қолади
root = con.execute("SELECT id, parent FROM cat_nodes WHERE skey='c1loc:root'").fetchone()
kr = con.execute("SELECT id FROM cat_nodes WHERE skey='e:kr'").fetchone()[0]
assert root and root[1] == kr
its = {r[0]: r for r in con.execute("SELECT name, unit, svc, kind FROM cat_items WHERE skey IN ('c1g:L1','c1g:L2') AND gone=0 AND deleted=0")}
assert set(its) == {"Кромка 19/1 Чёрный (местный) (рулон 200м)", "Кромка 19/1 Кашемир текстура (местный) (рулон 200м)"}, its   # «(n)» олинди
pvc = con.execute("SELECT price FROM cat_items WHERE kind='svc_pvc' AND name='Услуга ПВХ 19/1 Чёрный (местный) (рулон 200м)' AND gone=0").fetchone()
ov = con.execute("SELECT price FROM cat_items WHERE kind='svc_oval' AND name='Услуга Овал 19/1 Чёрный (местный) (рулон 200м)' AND gone=0").fetchone()
assert pvc and pvc[0] == 4500 and ov and ov[0] == 6900, (pvc, ov)
sup = C.node_sup(con)
assert sup.get(root[0]) == C.SUPPLIERS[4], sup.get(root[0])                                                  # бозор
C.build(con); con.commit()                                                                                  # такрор — барқарор
assert con.execute("SELECT COUNT(*) FROM cat_items WHERE skey LIKE 'c1g:L%' AND gone=0 AND deleted=0").fetchone()[0] == 2
print("EDGE LOCAL OK")
