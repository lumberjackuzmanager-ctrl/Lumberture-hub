"""Эконом/Craftwood декорлари: бир хил ном бирлашади, ўхшаши тасдиққа, нарх матрицаси (сорт, бир томонли)."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, decor as D
con = A.connect()
for ext, name, par, fold, price in (("E6", "ЛДСП Эконом", "0", 1, 0), ("g1", "ЛДСП Дуб Вотан 5,0325 (n)", "E6", 0, 0),
                                    ("g2", "ЛДСП Дуб Каньен темный текстура 5,0325 (n)", "E6", 0, 0),
                                    ("g3", "ЛДСП Серый шагрень 5,0325", "E6", 0, 0)):
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,src) VALUES(?,?,?,'лист',2,?,0,?,?,0,'','1c')",
                (ext, ext, name, price, par, fold))
con.execute("INSERT INTO eman_nodes(id,key,parent,name,kind) VALUES(1,'root',0,'Каталог','folder'),(2,'s',1,'Старвуд ЛМДФ','folder'),(3,'st',2,'УЗБ ЛМДФ 5кв Стандарт','leaf')")
for t, p in (("ЛМДФ Дуб Вотан 5,0325", 549000), ("ЛМДФ Дуб Каньон темный текстура 5,0325", 549000), ("ЛМДФ Черный шагрень 5,0325", 549000)):
    con.execute("INSERT INTO eman_goods(node,title,price,cur,unit) VALUES(3,?,?,'сум','шт')", (t, p))
con.commit()
with A.app.app_context():
    D.A = A
    c = A.db()
    st = D.build(c)
    names = {r["key"]: r["id"] for r in c.execute("SELECT * FROM cat_decor WHERE fam='econom'")}
    assert "дуб вотан" in names and len([k for k in names if "вотан" in k]) == 1, names        # 1С + Eman бир хил — битта
    pairs = c.execute("SELECT a, b FROM cat_decor_pair").fetchall()
    assert len(pairs) == 1, [tuple(p) for p in pairs]                                           # Каньен/Каньон — тасдиққа; Серый/Черный — йўқ
    D.resolve(c, c.execute("SELECT id FROM cat_decor_pair").fetchone()[0], True)
    assert c.execute("SELECT COUNT(*) FROM cat_decor WHERE fam='econom' AND merged_into IS NULL").fetchone()[0] == 4
    d = names["дуб вотан"]
    A.set_settings(decor_prices='{"econom": {"ЛМДФ 16": {"std": 45}, "ЛДСП 16": {"std": 31}}}')
    assert D.price(c, d, "ЛМДФ 16", 2, "Асино") == 45 and D.price(c, d, "ЛМДФ 16", 2, "Хитой") == 46
    assert D.price(c, d, "ЛМДФ 16", 2, "Гомель") == 47 and D.price(c, d, "ЛМДФ 16", 2, "Алтай") == 48
    assert D.price(c, d, "ЛДСП 16", 1) == 29 and D.price(c, d, "ЛМДФ 10") is None
cl = A.app.test_client()
with cl.session_transaction() as s: s["csrf"] = "t"
assert cl.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
h = cl.get("/erp/catalog/decors").get_data(as_text=True)
assert "Дуб Вотан" in h, h[:300]
print("DECOR OK")
