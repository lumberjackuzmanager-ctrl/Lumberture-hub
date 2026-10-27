"""Базис XML спецификациялари (Формирование проекта: kx + tb + gb) → битта лойиҳа → битта черновик накладной.
Намуна: Sunnat4144 (NRG) — 3 та XML, zip ичида. Қоидалар: claude/bazis-xml-import.md, bazis-script-qoidalar.md."""
import os, sys, tempfile, json, io, zipfile, datetime
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, basis as B, basis_xml as X
A.app.testing = True
ZIP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fx_bazis_xml_sunnat4144.zip")
z = zipfile.ZipFile(ZIP)
XML = {n: z.read(n) for n in z.namelist()}
today = datetime.date.today().isoformat()

# ---- парсер
items = X.parse_files([(n, XML[n]) for n in sorted(XML)])
assert [it["tag"] for it in items] == ["kx", "tb", "gb"], [it["tag"] for it in items]
assert [it["parts"] for it in items] == [117, 11, 28]
d = X.combine(items)
assert d["src"] == "xml" and d["project"] == "Sunnat4144 · kx+tb+gb" and d["parts"] == 156
P = {p["m"]: p for p in d["panels"]}
assert P["ЛМДФ Кава Бежевый 5кв 16мм"]["n"] == 46 and abs(P["ЛМДФ Кава Бежевый 5кв 16мм"]["a"] / 1e6 - 9.93) < 0.01
assert P["ЛХДФ Белый Апельсин 5,8 кв Premium"]["t"] == 3
# кромка: ҳар томон + 40 мм; «см. чертёж» — алоҳида
E = {(b["m"], b["f"]): b for b in d["butts"]}
k = E[("ПВХ 19/1 Капучино", 0)]
assert k["s"] == 73 and abs(k["l"] - (k["raw"] + 40 * 73)) < 1e-6 and round(k["l"] / 1000, 2) == 51.66, k
assert E[("ПВХ 19/1 Дымчатый Зелёный", 1)]["s"] == 5
# tb: битта эшик 754×359, 4 томон кромка — (754+359)*2 + 4*40
tb = items[1]
dv = [p for p in tb["pl"] if p["nm"] == "Дверь1"][0]
assert dv["h"] == 754 and dv["w"] == 359 and dv["eh"] == 2 and dv["ew"] == 2
# присадка (тешиклар): tb — 2 петля, 10 эксцентрик (+10 глухой), евровинт 2+2, насечка 8 (петля шуруплари)
S = dict(B.hole_slots(tb["holes"], 1))
assert S == {"petlya": 2, "ecc": 10, "glukh": 10, "evro_l": 2, "evro_t": 2, "nasechka": 8}, S
S = dict(B.hole_slots(items[2]["holes"], 1))
assert S["evro_l"] == 37 and S["evro_t"] == 37, S
# пазлар: Длина — жами (5 бўлак, 3800 мм), × детал сони
mc = B.mill_calc(d)
assert abs(mc["led"] - 14.65) < 0.01 and abs(mc["hdf"] - 3.83) < 0.01 and not mc["nocnc"], mc
# фигурали деталлар (Прямоугольная=N) — вырез қўлда
assert sum(f["q"] for f in d["fig"]) == 12 and len(d["fig"]) == 8 and d["fig"][-1]["it"] == "gb — NRGgarderob", d["fig"]
assert sum(p["q"] for p in d["pl"]) == 156 and len(d["pl"]) < 156        # бир хил деталлар — битта қатор
# Изделие/Количество — кўпайтирилади
x2 = XML["Specification_2.xml"].replace(b"<\xd0\x9a\xd0\xbe\xd0\xbb\xd0\xb8\xd1\x87\xd0\xb5\xd1\x81\xd1\x82\xd0\xb2\xd0\xbe>1<", b"<\xd0\x9a\xd0\xbe\xd0\xbb\xd0\xb8\xd1\x87\xd0\xb5\xd1\x81\xd1\x82\xd0\xb2\xd0\xbe>2<", 1)
it2 = X.parse_item("x.xml", x2)
assert it2["qty"] == 2 and it2["parts"] == 22 and dict(B.hole_slots(it2["holes"], 1))["petlya"] == 4
# хато файллар
for bad, msg in (([("a.txt", b"x")], "XML"), ([("a.xml", b"<x/>")], "не спецификация"), ([("a.xml", b"<?xml version='1.0'?><!DOCTYPE x [<!ENTITY a 'b'>]><x/>")], "неподдерживаемый"),
                 ([], "Выберите")):
    try:
        X.parse_files(bad); raise AssertionError(bad)
    except X.XmlError as e:
        assert msg in str(e), (msg, str(e))
# ровер: эни >1190 мм (ХДФ эмас) — тешиклари присадкадан айрилади
wide = XML["Specification_2.xml"].replace("<Длина>754</Длина><Ширина>359</Ширина>".encode(), "<Длина>1754</Длина><Ширина>1259</Ширина>".encode(), 1)
itw = X.parse_item("w.xml", wide)
assert dict(B.hole_slots(itw["holes_nc"], 1)) == {"petlya": 2, "nasechka": 4} and "petlya" not in dict(B.hole_slots(itw["holes"], 1))
assert [p["nm"] for p in itw["pm"] if min(p["w"], p["h"]) > 1190] == ["Дверь1"]

# ---- платформа
def login(u, p):
    cl = A.app.test_client()
    with cl.session_transaction() as s: s["csrf"] = "t"
    assert cl.post("/login", data={"username": u, "password": p, "csrf": "t"}).status_code == 302
    return cl
c = login("admin", "admin123")
with A.app.app_context():
    db = A.db()
    for ext, name, unit, price in (("L1", "ЛМДФ Кава Бежевый 5кв 16мм", "лист", 40), ("L2", "ЛМДФ Оливка 5кв 16мм", "лист", 40),
                                   ("SK", "Услуга ПВХ 19/1 Капучино", "пог.м", 0.4), ("SK2", "Услуга Овал 19/1 Капучино", "пог.м", 0.6),
                                   ("F1", "AOSITE petli", "шт", 1.5),
                                   ("S2", "Услуга сверления Под петли (35мм)", "шт", 0.07), ("S4", "Услуга сверления Евростяжка (лицевая 5*7)", "шт", 0.06),
                                   ("S5", "Услуга сверления Евростяжка (торцевая 7*4,5)", "шт", 0.06), ("S6", "Услуга сверления Глухие отверстия (разного диаметра)", "шт", 0.06),
                                   ("S7", "Услуга сверления Под эксцентрики", "шт", 0.11), ("S9", "Услуга сверления Насечка (3мм)", "шт", 0.01),
                                   ("S1", "Услуга конструктора СТАНДАРТ", "кв-м", 1.25), ("M1", "Услуга паз под подсветку", "пог.м", 0.5),
                                   ("SAW29", "Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", "кол-в", 3), ("SAW30", "Распил ЛДСП/ЛМДФ 5,796 кв (Пакетный)", "кол-в", 4),
                                   ("L3", "ЛМДФ Дуб золотой текстура 5,0325 (n)", "лист", 44)):
        db.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES(?,?,?,?,2,?,'',0,0,?,'cat',?,1)",
                   (ext, ext, name, unit, price, " " + name.casefold() + " ", "svc" if ext.startswith(("S", "M")) else "good"))
    db.execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc, lim) VALUES('C1', 'Мижоз XML', '', 0, 0, ' мижоз xml ', 100000)")
    db.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (today, today + " 09:00:00"))
    db.commit()
assert "Загрузить XML" in c.get("/erp/basis").get_data(as_text=True)
def up(files, bid=None, js=True):
    with c.session_transaction() as s: s["csrf"] = "t"
    data = {"csrf": "t", "files": [(io.BytesIO(b), n) for n, b in files]}
    if bid: data["bid"] = str(bid)
    return c.post("/erp/basis/xml", data=data, content_type="multipart/form-data", headers={"Accept": "application/json"} if js else {})
# браузер файлларни биттадан юборади: биринчиси яратади, кейингилари қўшилади (такрор юборилса — икки марта қўшилмайди)
r = up([("Specification_1.xml", XML["Specification_1.xml"])]); j = r.get_json(); assert r.status_code == 200 and j["ok"], r.data
bid = j["id"]
for n in ("Specification_2.xml", "Specification_3.xml", "Specification_3.xml"):
    r = up([(n, XML[n])], bid); assert r.status_code == 200 and r.get_json()["id"] == bid, r.data
with A.app.app_context():
    dd = json.loads(A.q("SELECT data FROM basis_imports WHERE id=?", (bid,), one=True)["data"])
    assert [it["tag"] for it in dd["items"]] == ["kx", "tb", "gb"] and dd["parts"] == 156
assert up([("a.txt", b"x")]).status_code == 400
# zip — оддий форма (JS'сиз)
r = up([("p.zip", open(ZIP, "rb").read())], js=False); assert r.status_code == 302 and "/erp/basis/" in r.headers["Location"]
h = c.get(f"/erp/basis/{bid}").get_data(as_text=True)
assert "Sunnat4144 · kx+tb+gb" in h and "Присадка по изделиям" in h and "kx — NRGKuxnya" in h and "Фигурные детали" in h, h[:3000]
assert "Присадка по фурнитуре (настройка)" not in h and 'name="fc_0_0"' not in h
import re as _re
assert all(" hidden" in t for t in _re.findall(r'<tr data-kind="saw"[^>]*>', h)) and _re.findall(r'<tr data-kind="saw"[^>]*>', h)   # распил ойнада кўринмайди
with A.app.app_context():
    rows, furn = B.build_rows(A.db(), dd)
R = {}
for x in rows:
    R.setdefault(x.get("svc") or x["kind"], []).append(x)
print([(x.get("svc") or x["kind"], x["src"][:30], x["good"], x["qty"]) for x in rows])
ed = [x for x in R["edge"] if x["src"] == "ПВХ 19/1 Капучино"][0]
assert ed["good"] == "SK" and ed["qty"] == 51.66 and "+ 40 мм" in ed["info"], ed
ef = [x for x in R["edgefig"] if x["src"] == "ПВХ 19/1 Капучино"][0]
assert ef["good"] == "SK2" and ef["qty"] == 1.85, ef
assert R["petlya"][0]["qty"] == 25 and R["evro_l"][0]["qty"] == 195 and R["evro_t"][0]["qty"] == 195 and R["ecc"][0]["qty"] == 44
assert R["nasechka"][0]["qty"] == 112 and R["paz_led"][0]["qty"] == 14.65 and R["paz_led"][0]["good"] == "M1"
assert R["vyrez"][0]["manual"] and "12 шт" in R["vyrez"][0]["info"] and R["vyrez"][0]["qty"] == 0
fg = {x["src"]: x for x in R["fgood"]}
assert fg["AOSITE petli"]["good"] == "F1" and fg["AOSITE petli"]["qty"] == 25 and fg["AOSITE petli"]["use"]
assert fg["Евровинт 7х50"]["good"] == "" and not fg["Евровинт 7х50"]["use"] and fg["Евровинт 7х50"]["qty"] == 195
assert all(not x["slots"] for x in furn)        # фурнитура номи бўйича присадка қўшилмайди (тешиклар деталлардан)
# черновик: листлар қўлда, кромка ҳисобланган, фурнитура (танланган) ва присадка
form = {"client": "C1", "note": "Базис: Sunnat4144 · kx+tb+gb"}
for i, x in enumerate(rows):
    if x.get("use", True) and x["good"]:
        form[f"use_{i}"] = "1"
    form[f"good_{i}"] = x["good"]; form[f"qty_{i}"] = str(x["qty"]); form[f"price_{i}"] = ""
    if x["kind"] == "panel" and x["good"]:
        form[f"qty_{i}"] = "3"
    if x["kind"] == "saw" and x["src"] in ("ЛМДФ Кава Бежевый 5кв 16мм", "ЛМДФ Оливка 5кв"):
        form[f"good_{i}"] = "SAW30"; form.pop(f"use_{i}", None); form[f"qty_{i}"] = ""    # қатор кўринмайди; эски (5,796) тури келса ҳам
        assert "= листов" in x["info"] and x["sub"]
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post(f"/erp/basis/{bid}", data=dict(form, csrf="t")); assert r.status_code == 302, r.get_data(as_text=True)[:2000]
with A.app.app_context():
    doc = A.q("SELECT * FROM erp_docs WHERE id=(SELECT doc_id FROM basis_imports WHERE id=?)", (bid,), one=True)
    assert doc and doc["status"] == "draft" and doc["client"] == "C1" and "kx+tb+gb" in (doc["note"] or "")
    L = {l["good"]: l for l in A.q("SELECT * FROM erp_lines WHERE doc_id=?", (doc["id"],))}
    assert L["SK"]["qty"] == 51.66 and L["F1"]["qty"] == 25 and L["S2"]["qty"] == 25 and L["L1"]["qty"] == 3, {k: v["qty"] for k, v in L.items()}
    n29 = A.scalar("SELECT COUNT(*) FROM erp_lines WHERE doc_id=? AND good='SAW29'", (doc["id"],))
    gl = [l["good"] for l in A.q("SELECT good FROM erp_lines WHERE doc_id=? ORDER BY lineno", (doc["id"],))]
    assert n29 == 1 and L["SAW29"]["qty"] == 9 and "SAW30" not in L, (n29, {k: v["qty"] for k, v in L.items()})   # 3 материал (5,0325) × 3 лист — битта распил қатори
    assert gl.index("SAW29") > max(gl.index("L1"), gl.index("L2"), gl.index("L3")) and gl.index("SAW29") < gl.index("SK"), gl            # материаллардан кейин, кромкадан олдин   # распил: сони = листлар, тури — танланган товар листидан (5,0325 → 4,5–5 кв)
    assert not A.q("SELECT 1 FROM basis_names WHERE key LIKE 'saw:%'", one=True)                 # кўринмас распил эслаб қолинмайди
    assert A.q("SELECT good FROM basis_names WHERE key=?", ("fgood:" + B.norm("AOSITE petli"),), one=True)["good"] == "F1"
# чоп этиш: деталлар буюмлар бўйича
h = c.get(f"/erp/basis/{bid}/print?what=parts").get_data(as_text=True)
assert "kx — NRGKuxnya · ЛМДФ Оливка 5кв" in h and "gb — NRGgarderob" in h
# скрипт оқими ўзгармади: XML бўлмаган маълумотда кромка метражи қўлда
with A.app.app_context():
    rs, _ = B.build_rows(A.db(), {"panels": [], "butts": [{"m": "ПВХ 19/1 Капучино", "t": 1, "w": 19, "l": 10000}], "furn": []})
    assert [x for x in rs if x["kind"] == "edge"][0]["qty"] == ""
# конструктор (basis роли) ҳам юклай олади (27.10: «HTTP 403» — эндпоинт рўйхатда йўқ эди)
from werkzeug.security import generate_password_hash
with A.app.app_context():
    A.db().execute("INSERT INTO users(username,password_hash,role,active) VALUES(?,?,?,1)", ("konstrx", generate_password_hash("Konstr12345x"), "basis")); A.db().commit()
c = login("konstrx", "Konstr12345x")
assert "Загрузить XML" in c.get("/erp/basis").get_data(as_text=True)
r = up([("Specification_2.xml", XML["Specification_2.xml"])]); assert r.status_code == 200 and r.get_json()["ok"], (r.status_code, r.data[:300])
assert c.get(r.get_json()["url"]).status_code == 200
print("BASIS XML OK")
