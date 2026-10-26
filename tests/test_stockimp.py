"""Ввод остатков из 1С: таклиф, сопоставлаш эслаб қолиниши, инвентаризация қораламаси."""
import os, sys, tempfile
from datetime import date
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, stockimp as SI

with A.app.app_context():
    con = A.db()
    td = date.today().isoformat()
    con.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (td, td + " 09:00:00"))
    for ext, name, unit in (("ci1", "ЛДСП Эггер Белый гладкий 16мм W1000 ST9", "лист"), ("ci2", "ЛМДФ Эконом Дуб Вотан 16мм", "лист"), ("ci3", "ХДФ белый 3мм", "лист")):
        con.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES(?,?,?,?,2,0,'',0,0,?,'cat','good',1)",
                    (ext, ext, name, unit, " " + name.casefold() + " "))
    con.commit()
SI.rows_1c = lambda neg=False: [dict(ext="A1", name="ЛМДФ Дуб Вотан 5,0325 (n)", unit="лист", path="ЛМДФ Эконом", qty=7.0),
                      dict(ext="A2", name="Непонятный товар", unit="шт", path="Прочее", qty=2.0)]
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
h = c.get("/erp/stock-import").get_data(as_text=True)
assert "ЛМДФ Дуб Вотан 5,0325" in h and "Непонятный товар" in h, h[:300]
j = c.get("/erp/stock-import/search?q=хдф").get_json()
assert j["items"] and j["items"][0]["ext"] == "ci3", j
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post("/erp/stock-import", data={"csrf": "t", "act": "save", "g_A1": "ci2", "g_A2": "ci3", "use_A1": "1", "use_A2": "1"})
assert r.status_code == 302
with A.app.app_context():
    assert dict(A.db().execute("SELECT c1, good FROM stock_map").fetchall()) == {"A1": "ci2", "A2": "ci3"}
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post("/erp/stock-import", data={"csrf": "t", "act": "inv", "date": td, "g_A1": "ci2", "g_A2": "ci3", "use_A1": "1"})
assert r.status_code == 302 and "/edit2" in r.headers["Location"], r.headers
with A.app.app_context():
    d = A.db().execute("SELECT * FROM erp_docs WHERE kind='inv'").fetchone()
    assert d and d["status"] == "draft"
    ls = [tuple(x) for x in A.db().execute("SELECT good, qty, price FROM erp_lines WHERE doc_id=?", (d["id"],))]
    assert ls == [("ci2", 7.0, 7.0)], ls
# кромка ҳеч қаерда кирмайди; 1С'дан нусха (c1g) — аниқ мослик; бренди бошқа таклиф қилинмайди; номи айнан бир хил — олинади (25.10)
assert SI._skip("ЛДСП Эконом", "Кромка 19/1 Белый") and SI._skip("Прочее / Кромка ЭГГЕР 19/0,8") and not SI._skip("МДФ Эконом", "МДФ Шлифованный 12мм")
with A.app.app_context():
    con = A.db()
    nid = con.execute("INSERT INTO cat_nodes(parent, name, skey) VALUES(0,'МДФ Эконом','c1g:F9')").lastrowid
    iid = con.execute("INSERT INTO cat_items(node, name, skey, unit) VALUES(?, 'МДФ Шлифованный 12мм 5,0325', 'c1g:M12', 'лист')", (nid,)).lastrowid
    for ext, name in ((f"ci{iid}", "МДФ Шлифованный 12мм 5,0325"), ("ci90007", "ЛДСП Ультрадекор 5,0325 Белый гладкий 0110 16мм"), ("ci90008", "ЛДСП Капучино апельсин 5,0325 (n)")):
        con.execute("INSERT OR REPLACE INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES(?,?,?,'лист',2,0,'',0,0,?,'cat','good',1)",
                    (ext, ext, name, " " + name.casefold() + " "))
    con.commit()
    assert SI.direct(con, "M12") == f"ci{iid}" and SI.direct(con, "M13") == ""
    assert SI.suggest(con, "ЛДСП Белый гладкий 5,0325 (n)", "ЛДСП Эконом") == ""              # Эконом -> Ультрадекор эмас
    assert SI.suggest(con, "ЛДСП  Капучино апельсин  5,0325 (n)", "ЛДСП Эконом") == "ci90008"
# каталогда йўқ товар — «+ Добавить в каталог»: шу 1С папкасидаги бошқалари турган каталог папкасига қўшилади ва боғланади
SI.rows_1c = lambda neg=False: [dict(ext="M12", name="МДФ Шлифованный 12мм 5,0325", unit="лист", path="МДФ Эконом", qty=3.0),
                      dict(ext="M14", name="МДФ  Шлифованный 14мм 5,0325", unit="лист", path="МДФ Эконом", qty=2.0),
                      dict(ext="Z1", name="Ёлғиз товар", unit="шт", path="Бошқа", qty=1.0)]
h = c.get("/erp/stock-import").get_data(as_text=True)
assert 'data-ext="M14"' in h and "Добавить в каталог" in h
r = c.post("/erp/stock-import/new", json={"ext": "M14"}, headers={"X-CSRF-Token": "t"})
j = r.get_json()
assert r.status_code == 200 and j["name"] == "МДФ Шлифованный 14мм 5,0325", j
with A.app.app_context():
    con = A.db()
    it = con.execute("SELECT * FROM cat_items WHERE id=?", (int(j["ext"][2:]),)).fetchone()
    assert it["node"] == nid and it["manual"] == 1 and it["unit"] == "лист"
    assert con.execute("SELECT good FROM stock_map WHERE c1='M14'").fetchone()[0] == j["ext"]
    assert con.execute("SELECT name FROM erp_goods WHERE ext=?", (j["ext"],)).fetchone()[0] == "МДФ Шлифованный 14мм 5,0325"
assert c.post("/erp/stock-import/new", json={"ext": "M14"}, headers={"X-CSRF-Token": "t"}).get_json()["ext"] == j["ext"]   # иккинчи марта — ўша товар
assert c.post("/erp/stock-import/new", json={"ext": "Z1"}, headers={"X-CSRF-Token": "t"}).status_code == 400              # папкаси номаълум
# 1С'да минус қолдиқ ҳам ўтади (эгаси, 26.10): факт = ҳозирги учёт + минус; кодсиз ном айнан мос; ўлчами бошқа — таклиф йўқ
with A.app.app_context():
    con = A.db()
    for ext, name in (("ci21", "ЛДСП Ультрадекор 5,0325 Белый гладкий 0110 16мм"), ("ci22", "ЛДСП Ультрадекор 5,0325 Белый гладкий 16мм"),
                      ("ci23", "ЛХДФ 3мм 2500*2070 Черный 5,175кв")):
        con.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES(?,?,?,'шт',2,0,'',0,0,?,'cat','good',1)",
                    (ext, ext, name, " " + name.casefold() + " "))
    con.commit()
    assert SI.suggest(con, "ЛДСП Ультрадекор 5,0325 Белый гладкий 16мм  (0101 SM)") == "ci22"
    assert SI.suggest(con, "ЛХДФ 3мм 2800*2070 Черный 5,796кв  (0190 PE)") != "ci23"
    SI.rows_1c = lambda neg=False: [dict(ext="N1", name="ЛДСП Ультрадекор 5,0325 Белый гладкий 16мм  (0101 SM)", unit="шт", path="ЛДСП", qty=-8.0),
                                    dict(ext="N2", name="Номаълум товар", unit="шт", path="Прочее", qty=-1.0),
                                    dict(ext="N3", name="ЛМДФ Дуб Вотан 5,0325 (n)", unit="лист", path="ЛМДФ Эконом", qty=5.0)] if neg else []
    with A.app.test_request_context():
        r = SI.import_negatives(A.db())
    assert r["n"] == 1 and r["doc"] and "Номаълум товар (-1)" in r["skipped"], r
    import erp
    assert erp.stock_all(A.db()).get("ci22") == -8.0
    d = A.db().execute("SELECT status FROM erp_docs WHERE id=?", (r["doc"],)).fetchone()[0]; assert d == "posted"
print("STOCKIMP OK")
