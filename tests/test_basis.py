"""Базис → платформа: скрипт (калит, ASCII), /api/basis/import, товарга мослаш (декор коди), черновик накладной, эслаб қолиш."""
import os, sys, tempfile, json, re, subprocess, datetime
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, basis as B
A.app.testing = True
def login(u, p):
    cl = A.app.test_client()
    with cl.session_transaction() as s: s["csrf"] = "t"
    assert cl.post("/login", data={"username": u, "password": p, "csrf": "t"}).status_code == 302
    return cl
def post(cl, u, d):
    with cl.session_transaction() as s: s["csrf"] = "t"
    return cl.post(u, data=dict(d, csrf="t"))
c = login("admin", "admin123")
today = datetime.date.today().isoformat()
with A.app.app_context():
    db = A.db()
    for ext, name, unit, price in (("L1", "ЛДСП Эггер 5,796 Орех Линкольн 16мм ST19 (H1714 ST19)", "лист", 60),
                                   ("L2", "ЛДСП Эггер 5,796 Орех Линкольн 18мм ST19 (H1714 ST19)", "лист", 65),
                                   ("K1", "Кромка Эггер 19/0,8 Орех Линкольн ABS ST19 (H1714 ST19)", "пог.м", 0.3),
                                   ("F1", "Петля накладная Boyard", "шт", 1),
                                   ("SK", "Услуга ПВХ Эггер 19/0,8 Орех Линкольн ABS ST19 (H1714 ST19)", "пог.м", 0.4),
                                   ("S1", "Услуга конструктора СТАНДАРТ", "кв-м", 1.25),
                                   ("S3", "Услуга конструктора ГОЛД", "кв-м", 2),
                                   ("S2", "Услуга сверления Под петли (35мм)", "шт", 0.07),
                                   ("S4", "Услуга сверления Евростяжка (лицевая 5*7)", "шт", 0.06),
                                   ("S5", "Услуга сверления Евростяжка (торцевая 7*4,5)", "шт", 0.06),
                                   ("S6", "Услуга сверления Глухие отверстия (разного диаметра)", "шт", 0.06),
                                   ("S7", "Услуга сверления Под эксцентрики", "шт", 0.11),
                                   ("S8", "Услуга сверления Под эксцентрики (D25мм)", "шт", 0.11),
                                   ("S9", "Услуга сверления Насечка (3мм)", "шт", 0.01)):
        db.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES(?,?,?,?,2,?,'',0,0,?,'cat',?,1)",
                   (ext, ext, name, unit, price, " " + name.casefold() + " ", "svc" if ext.startswith("S") else "good"))
    db.execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc, lim) VALUES('C1', 'Мижоз Базис', '', 0, 0, ' мижоз базис ', 100000)")
    db.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (today, today + " 09:00:00"))
    db.commit()
r = c.get("/setup/basis.js"); js = r.get_data(as_text=True)
assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and all(ord(ch) < 128 for ch in js)
tok = re.search(r'var TOKEN = "([^"]+)"', js).group(1); assert len(tok) > 20 and 'var SERVER = "http://' in js
p = os.path.join(tempfile.mkdtemp(), "s.js"); open(p, "w").write(js.replace("walk(Model);", "var Model={Count:0};function alert(){};walk(Model);"))
nd = subprocess.run(["node", "--check", p], capture_output=True, text=True); assert nd.returncode == 0, nd.stderr
data = {"v": 1, "project": "Шкаф-купе Азиз", "parts": 14,
        "panels": [{"m": "ЛДСП Egger Орех Линкольн H1714 ST19 16мм", "t": 16, "a": 9.2e6, "n": 12}, {"m": "ХДФ белый", "t": 3, "a": 2.1e6, "n": 2}],
        "butts": [{"m": "Кромка ABS H1714 ST19 19/0,8", "t": 0.8, "w": 19, "l": 30000}],
        "furn": [{"m": "Петля накладная Boyard", "n": 8}, {"m": "Евровинт 7х50", "n": 10}, {"m": "Месан с шапкой10", "n": 4, "h": {"15|13|2": 4, "8|34|2": 4, "10|11|2": 4}},
                 {"m": "Тандем 450", "n": 2}, {"m": "Шуруп 4х16 полукр", "n": 6}, {"m": "350 шарик", "n": 2}]}
ac = A.app.test_client()
assert ac.post("/api/basis/import", json=data, headers={"X-LB-Token": "bad" * 10}).status_code == 403
r = ac.post("/api/basis/import", json=data, headers={"X-LB-Token": tok}); j = r.get_json()
assert r.status_code == 200 and j["ok"] and j["url"].endswith(f"/erp/basis/{j['id']}"), r.data
bid = j["id"]
h = c.get(f"/erp/basis/{bid}").get_data(as_text=True)
assert "Шкаф-купе Азиз" in h and "ЛДСП Эггер 5,796 Орех Линкольн 16мм" in h and "Услуга ПВХ Эггер 19/0,8" in h and "Петля накладная" in h and 'name="fc_0_0"' in h
with A.app.app_context():
    rows, furn = B.build_rows(A.db(), data)
    rows2, _ = B.build_rows(A.db(), dict(data, panels=[dict(data["panels"][1], ap=1.0e6)]))
Q = {x.get("svc") or x["kind"] + str(i): x for i, x in enumerate(rows)}
print([(x.get("svc") or x["kind"], x["good"], x["qty"]) for x in rows])
ed = [x for x in rows if x["kind"] == "edge"][0]
pn = [x for x in rows if x["kind"] == "panel"]
assert pn[0]["good"] == "L1" and pn[0]["qty"] == "" and pn[1]["good"] == "" and ed["good"] == "SK" and ed["qty"] == ""
saw = [x for x in rows if x["kind"] == "saw"]                                         # распил — ҳар материал остида, сони = листлар
assert [x["src"] for x in saw] == [pn[0]["src"], pn[1]["src"]] and all(x["qty"] == "" and x["sub"] for x in saw), saw
assert [x["kind"] for x in rows[:4]] == ["panel", "saw", "panel", "saw"]
assert Q["konstr"]["good"] == "S1" and Q["konstr"]["qty"] == 9.2                          # СТАНДАРТ; ХДФ ишловсиз — кирмайди
assert Q["petlya"]["good"] == "S2" and Q["petlya"]["qty"] == 8
assert Q["evro_l"]["qty"] == 10 and Q["evro_t"]["qty"] == 10 and Q["evro_l"]["good"] == "S4" and Q["evro_t"]["good"] == "S5"
assert Q["ecc"]["qty"] == 4 and Q["ecc"]["good"] == "S7" and Q["glukh"]["qty"] == 4 + 2 * 2 and Q["glukh"]["good"] == "S6"
assert Q["nasechka"]["qty"] == 6 and Q["ecc25"]["good"] == "S8" and Q["ecc25"]["qty"] == 0
fb = [x for x in furn if x["src"] == "350 шарик"][0]
assert fb["slots"] == [] and fb["known"] and fb["guess"]["scr"] == 6                         # шарикли: шуруплар бор — қўшилмайди, огоҳлантирилади
assert [x for x in rows2 if x.get("svc") == "konstr"][0]["qty"] == 1.0                     # ишлов берилган ХДФ — киради
f = {"client": "C1", "note": "Базис: Шкаф-купе Азиз"}
for i, x in enumerate(rows):
    f[f"use_{i}"] = "on"; f[f"good_{i}"] = x["good"]; f[f"qty_{i}"] = str(x["qty"]) if x["qty"] != "" else "2"   # листлар — базисчи карта раскроядан ёзади
iH = [i for i, x in enumerate(rows) if x["kind"] == "panel"][1]
f[f"good_{iH}"] = "L2"                                     # ХДФ ни қўлда танлади — эслаб қолинади
for i, x in enumerate(rows):
    if not x["qty"] and x["kind"] not in ("panel", "edge"):
        f.pop(f"use_{i}", None)
iP = [i for i, x in enumerate(rows) if x.get("svc") == "petlya"][0]
jP = [j for j, x in enumerate(furn) if x["src"].startswith("Петля")][0]
f[f"fc_{jP}_0"] = "petlya"; f[f"fm_{jP}_0"] = "2"; f[f"fc_{jP}_1"] = ""; f[f"qty_{iP}"] = "16"   # петля: 2 та — эслаб қолинади
iK = [i for i, x in enumerate(rows) if x.get("svc") == "konstr"][0]; f[f"good_{iK}"] = "S3"
r = post(c, f"/erp/basis/{bid}", f); assert r.status_code == 302 and "/erp/doc/" in r.headers["Location"], r.get_data(as_text=True)[:300]
with A.app.app_context():
    d = A.q("SELECT * FROM erp_docs ORDER BY id DESC LIMIT 1", one=True)
    assert d["status"] == "draft" and d["client"] == "C1"
    assert A.scalar("SELECT COUNT(*) FROM erp_lines WHERE doc_id=?", (d["id"],)) == 3 + 1 + 6
    assert A.q("SELECT doc_id FROM basis_imports WHERE id=?", (bid,), one=True)[0] == d["id"]
    u = A.q("SELECT * FROM users WHERE id=(SELECT uid FROM basis_imports WHERE id=?)", (bid,), one=True)
    rr, ff = B.build_rows(A.db(), data, u)
    QQ = {x.get("svc"): x for x in rr if x.get("svc")}
    assert [x for x in rr if x["kind"] == "panel"][1]["good"] == "L2" and [x for x in ff if x["src"].startswith("Петля")][0]["slots"] == [("petlya", 2.0)] and QQ["petlya"]["qty"] == 16 and QQ["konstr"]["good"] == "S3"
assert "/erp/doc/" in c.get(f"/erp/basis/{bid}").headers.get("Location", "")          # такрор очилса — накладнойга
assert "Шкаф-купе Азиз" in c.get("/erp/basis").get_data(as_text=True)
print("BASIS OK")
