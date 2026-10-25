"""Лотинча ёзилган қидирув (эгаси, 26.10: ишчиларнинг кўпида лотин алифбоси): «beliy gladkiy» -> «Белый гладкий»,
«kashemir» -> «Кашемир», «hdf» -> «ХДФ» — платформада ва цех-ботда."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb
assert kb.variants("beliy gladkiy")[1] == "б_л__ гладк__" and kb.variants("vty [jpbh rtkfvfy")[1] == "мен хозир келаман"
for q, name in (("beliy gladkiy", "ЛДСП Белый Гладкий 16мм"), ("kashemir", "Кромка 19/1 ПВХ Кашемир"), ("hdf", "ЛХДФ белый"), ("xdf", "ХДФ"),
                ("chernyy", "Черный матовый"), ("kronostar seriy", "ЛДСП Кроностар Серый"), ("egger", "ЛДСП Эггер"), ("dub votan", "Дуб Вотан"),
                ("o'zbek", "Ўзбек"), ("19/0,8 beliy", "Кромка 19/0,8 Белый")):
    assert kb.matches(q, name), (q, name)
assert not kb.matches("kashemir", "Кромка Белый") and not kb.matches("beliy", "Серый")
import app as A, erp
erp.stock_now = lambda: {}
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
with A.app.app_context():
    con = A.db()
    for ext, name in (("ci1", "ЛДСП Белый Гладкий 16мм"), ("ci2", "Кромка 19/1 ПВХ Кашемир"), ("ci3", "ЛДСП Серый 16мм")):
        con.execute("INSERT INTO erp_goods(ext,name,unit,cur,price,parent,folder,deleted,src,cat,cvis,lc) VALUES(?,?,'лист',2,1,'',0,0,'cat','good',1,?)",
                    (ext, name, erp.lc(name)))
    con.commit()
names = lambda q: [x["name"] for x in c.get("/erp/api/goods?q=" + q).get_json()]
assert names("beliy gladkiy") == ["ЛДСП Белый Гладкий 16мм"], names("beliy gladkiy")
assert names("kashemir") == ["Кромка 19/1 ПВХ Кашемир"] and names("ldsp seriy") == ["ЛДСП Серый 16мм"]
print("KB LAT OK")
