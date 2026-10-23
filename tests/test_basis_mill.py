"""Базис скрипти: вырезлар (Гола L/C), пазлар (метр), ЧПУ присадка мумкин эмас, деталлар рўйхати, тешиклар бўйича присадка."""
import os, sys, tempfile, json, re, subprocess
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, basis as B
A.app.testing = True
assert B.vyrez_units(82) == 1 and B.vyrez_units(150) == 1 and B.vyrez_units(151) == 2 and B.vyrez_units(300) == 2 and B.vyrez_units(30) == 1
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
with A.app.app_context():
    db = A.db()
    for ext, name, unit, price in (("M1", "Услуга фрезеровка  (обработка ЧПУ)", "шт", 15000), ("P4", "Услуга сверления Паз (4мм)", "пог.м", 4000),
                                   ("PL", "Услуга сверления Паз (под подсветки)", "пог.м", 15000), ("S2", "Услуга сверления Под петли (35мм)", "шт", 1000),
                                   ("S6", "Услуга сверления Глухие отверстия", "шт", 500)):
        db.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES(?,?,?,?,1,?,'',0,0,?,'cat','svc',1)",
                   (ext, ext, name, unit, price, " " + name.casefold() + " "))
    db.commit()
js = c.get("/setup/basis.js").get_data(as_text=True)
mock = r"""
function L(x1,y1,x2,y2){return {Pos1:{x:x1,y:y1},Pos2:{x:x2,y:y2},ObjLength:function(){return Math.sqrt((x2-x1)*(x2-x1)+(y2-y1)*(y2-y1))},IsArc:function(){return false},IsCircle:function(){return false}}}
function Arc(x1,y1,x2,y2,len){return {Pos1:{x:x1,y:y1},Pos2:{x:x2,y:y2},ObjLength:function(){return len},IsArc:function(){return true},IsCircle:function(){return false}}}
function poly(pts){var o=[];for(var i=0;i<pts.length;i++){var a=pts[i],b=pts[(i+1)%pts.length];o.push(L(a[0],a[1],b[0],b[1]))}return o}
function C(arr){var c={Count:arr.length,Objects:arr};for(var i=0;i<arr.length;i++)c[i]=arr[i];return c}
function panel(nm,mat,t,w,h,contour,cuts,butts){return {Name:nm,MaterialName:mat,Thickness:t,ContourWidth:w,ContourHeight:h,Contour:C(contour),Cuts:C(cuts||[]),
  Butts:{Count:(butts||0),Butts:(function(){var a=[];for(var i=0;i<(butts||0);i++)a.push({Profile:null,ElemIndex:i,Material:'ПВХ 19/1 Белый гладкий',Thickness:1,Width:19,Overhung:0});return a})()}}}
function fast(nm,holes){var o={Name:nm,Holes:holes?C(holes):undefined,toString:function(){return 'TFastener'}};return o}
function H(d,dep,mode){return {Diameter:d,Depth:dep,DrillMode:mode}}
var gola=poly([[0,0],[500,0],[500,668],[474,724-30],[474,724],[0,724]]);  // L notch (fake geometry, 6 elements)
gola=[L(0,0,500,0),L(500,0,500,668),L(500,668,444,668),L(444,668,444,724),L(444,724,0,724),L(0,724,0,0)];   // 56 + 56 notch
var cnotch=[L(0,0,300,0),L(300,0,300,950),L(300,950,0,950),L(0,950,0,70),L(0,70,8,70),L(8,70,8,56),L(8,56,0,56),L(0,56,0,0)];   // 8+14+8 mid-edge
var rect=poly([[0,0],[600,0],[600,2390],[0,2390]]);
var round=[Arc(0,0,600,0,942.5),Arc(600,0,0,0,942.5)];
var wide=poly([[0,0],[1200,0],[1200,2000],[0,2000]]);
var paz={Name:'Фреза фасонная',Sign:'let',Trajectory:C([L(0,10,612,10)])};
var led={Name:'Паз под подсветку',Sign:'LED',Trajectory:C([L(0,10,1000,10)])};
var Model=C([panel('bok','ЛДСП Белый 16мм 5кв\rA1',16,500,724,gola,[],2),panel('bok','ЛДСП Белый 16мм 5кв\rA1',16,500,724,gola,[],2),
  panel('bok','ЛДСП Белый 16мм 5кв\rA1',16,300,950,cnotch,[paz],1),panel('стол','ЛДСП Белый 16мм 5кв\rA1',16,600,600,round,[],0),
  panel('крыша','ЛДСП Белый 16мм 5кв\rA1',16,1200,2000,wide,[led],4),panel('бок2','ЛДСП Белый 16мм 5кв\rA1',16,600,2390,rect,[],0),
  fast('Петля 180',[H(35,12,2),H(3,1,2),H(3,1,2)]),fast('Петля 180',[H(35,12,2),H(3,1,2),H(3,1,2)]),fast('Новый шкант',[H(8,12,2),H(8,30,1)]),fast('Направляющие',null)]);
var EVW=fast('Евровинт 7х50',[{Diameter:7,Depth:16,DrillMode:1,Position:{x:10,y:10,z:5}},{Diameter:4.5,Depth:39,DrillMode:2,Position:{x:10,y:10,z:500}}]);
EVW.ToGlobal=function(p){return p};
Model[4].GMin={x:0,y:0,z:0};Model[4].GMax={x:1200,y:2000,z:16};Model[4].FindConnectedFasteners=function(){return C([EVW,EVW])};
Model=C([Model[0],Model[1],Model[2],Model[3],Model[4],Model[5],Model[6],Model[7],Model[8],Model[9],EVW,EVW]);
var OUT=null;function NewCOMObject(){return {open:function(){},setRequestHeader:function(){},send:function(b){OUT=b},status:500,responseText:'x'}}
function alert(){}
"""
p = os.path.join(tempfile.mkdtemp(), "s.js")
open(p, "w").write(mock + js.replace("walk(Model);", "walk(Model);") + "\nrequire('fs').writeFileSync(process.argv[2], OUT);")
out = p + ".json"
nd = subprocess.run(["node", p, out], capture_output=True, text=True); assert nd.returncode == 0, nd.stderr
data = json.load(open(out))
print(json.dumps(data["pm"], ensure_ascii=False)); print(data["furn"], data["err"])
assert not data["err"], data["err"]
pm = {(x["nm"], x["w"]): x for x in data["pm"]}
assert pm[("bok", 500)]["cut"] == [112] and pm[("bok", 500)]["q"] == 2                    # Гола L: 56+56
assert pm[("bok", 300)]["cut"] == [30] and pm[("bok", 300)]["pz"][0]["l"] == 612           # C: 8+14+8, паз ХДФ 612
assert pm[("стол", 600)]["arcs"] == 2 and pm[("стол", 600)]["cut"] == [1885]
assert ("бок2", 600) not in pm
pl = {x["nm"]: x for x in data["pl"]}; assert pl["bok"]["q"] in (1, 2) and sum(x["q"] for x in data["pl"]) == 6 and data["pl"][0]["e"] == "2 ст."
# кромка томонлари: эни бўйлаб (ew) ва узунлиги бўйлаб (eh) — ўлчам тагида чизиқ учун
assert (pl["крыша"]["ew"], pl["крыша"]["eh"], pl["крыша"]["ef"]) == (2, 2, 0), pl["крыша"]
b5 = [x for x in data["pl"] if x["nm"] == "bok" and x["w"] == 500][0]
assert (b5["ew"], b5["eh"]) == (1, 1) and pl["бок2"]["ew"] == pl["бок2"]["eh"] == 0, data["pl"]
fh = {x["m"]: x for x in data["furn"]}; assert fh["Петля 180"]["h"] == {"35|12|2": 2, "3|1|2": 4} and fh["Направляющие"]["h"] in ({}, None)
with A.app.app_context():
    rows, furn = B.build_rows(A.db(), data)
Q = {x.get("svc"): x for x in rows if x["kind"] == "svc"}
print([(k, v["good"], v["qty"], v["price"]) for k, v in Q.items()])
# вырез: Гола L 112 мм ×2 → 2, C 30 → 1, думалоқ 1885 → 13 ; жами 16 × 5000
assert Q["vyrez"]["good"] == "M1" and Q["vyrez"]["qty"] == 16 and Q["vyrez"]["price"] == 5000 and Q["vyrez"]["use"]
assert Q["paz_hdf"]["good"] == "P4" and Q["paz_hdf"]["qty"] == 0.61 and Q["paz_led"]["good"] == "PL" and Q["paz_led"]["qty"] == 1.0
nc = {x["nm"]: x["why"] for x in data["_mill"]["nocnc"]}
assert set(nc) == {"стол", "крыша"} and "шире 1190 мм" in nc["крыша"] and "радиус" in nc["стол"], nc
fr = {x["src"]: x for x in furn}
assert fr["Петля 180"]["slots"] == [("petlya", 1.0), ("nasechka", 2.0)] and not fr["Петля 180"]["byh"]
assert fr["Новый шкант"]["byh"] and dict(fr["Новый шкант"]["slots"]) == {"glukh": 2.0}, fr["Новый шкант"]
assert rows[0]["src"] == "ЛДСП Белый 16мм 5кв"                                           # артикул номдан олиб ташланди
r = c.post("/api/basis/import", json=data, headers={"X-LB-Token": re.search(r'var TOKEN = "([^"]+)"', js).group(1)}); bid = r.get_json()["id"]
h = c.get(f"/erp/basis/{bid}").get_data(as_text=True)
assert "Присадка на ЧПУ невозможна" in h and "Список деталей" in h and "Вырезы и фигурная фрезеровка" in h and "по отверстиям" in h, h[-3000:]
print("BASIS MILL OK")
with A.app.app_context():
    A.db().execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES('RV','RV','Услуга Ровер','шт',1,120000,'',0,0,' услуга ровер ','cat','svc',1)"); A.db().commit()
    rows, _ = B.build_rows(A.db(), data)
rv = [x for x in rows if x.get("svc") == "rover"][0]
assert rv["good"] == "RV" and rv["qty"] == 2 and not rv["use"] and not rv["price"], rv                               # стол (радиус) + крыша (эни > 1 м)
print("ROVER OK")
assert dict(B.hole_slots({"3|1|0": 4, "5|9|2": 2, "35|12|2": 2, "40|10|2": 2, "15|13|2": 2}, 2)) == {"nasechka": 2, "glukh": 1, "petlya": 1, "cnc35": 1, "ecc": 1}
assert data["nch"] == {"Евровинт 7х50": {"7|16|1": 2}}, data["nch"]
EV = {x.get("svc"): x for x in rows}
assert EV["evro_l"]["minus"] == 2 and EV["evro_l"]["qty"] == 0 and EV["evro_t"]["qty"] == 2, (EV["evro_l"], EV["evro_t"])
print("HOLES OK")
h = c.get(f"/erp/basis/{bid}/print?what=furn").get_data(as_text=True)
assert "Петля 180" in h and "Направляющие" in h and "Список деталей" in h and "Выдал" in h, h[:500]
h = c.get(f"/erp/basis/{bid}/print?what=parts").get_data(as_text=True)
assert '<span class="ed ed2">2000</span>' in h and '<span class="ed ed2">1200</span>' in h and '<span class="ed ed1">724</span>' in h, h[h.find("крыша")-200:h.find("крыша")+400]
h_imp = c.get(f"/erp/basis/{bid}?again=1").get_data(as_text=True)
assert '<span class="ed ed1">500</span>' in h_imp
assert "ЛДСП Белый 16мм 5кв" in h and "Всего деталей: 6 шт" in h and "крыша" in h, h[-800:]
assert 'what=furn' in c.get(f"/erp/basis/{bid}").get_data(as_text=True)
print("PRINT OK")
assert dict(B.hole_slots({"8|34|2": 15, "15|13|2": 15, "10|11|2": 15}, 15)) == {"ecc": 1.0, "glukh": 1.0}     # месан: 1350 + 750
print("MESAN OK")
assert dict(B.hole_slots({"25|13|2": 4, "8|30|1": 4}, 4)) == {"ecc25": 1.0}                          # VB D25: 2 тешик — 1350
print("D25 OK")
assert B.furn_type("Петля 180(Blum)") == "Петли 180°" and B.furn_type("(Примой) Петля для накладной двери") == "Петли накладные (прямые)" and B.furn_type("Месан с шапкой10", {"15|13|2": 1}) == "Эксцентрики" and B.furn_type("Месан раффикс") == "Раффиксы" and B.furn_type("Месан ножка 100") == "Опоры и ножки"
fs = B.furn_summary([("Направляющие FR302-400 Направляющая левая", "", 3), ("Направляющие FR302-400 Направляющая правая", "", 3), ("Евровинт 7х50", "69", 109)])
assert [(g["type"], g["n"], g["unit"]) for g in fs] == [("Направляющие", 3, "компл."), ("Евровинты", 109, "шт")], fs
h = c.get(f"/erp/basis/{bid}/print?what=furn").get_data(as_text=True); assert "lbprint.js" in h and "Петли 180°" in h and "К проекту" in h
h = c.get(f"/erp/basis/{bid}").get_data(as_text=True); assert "Присадка по фурнитуре" in h and h.count('target="_blank"') == h.count("qollanma.html")   # фақат меню: «Как пользоваться»
print("FSUM OK")
# кромка фигурали томонда (вырез ичида) — алоҳида «см. чертёж» қатори, «Услуга Овал»
mock2 = mock.split("var Model=C(")[0] + r"""
var g2=[L(0,0,500,0),L(500,0,500,668),L(500,668,444,668),L(444,668,444,724),L(444,724,0,724),L(0,724,0,0)];
var pn=panel('bok','ЛДСП Белый 16мм 5кв',16,500,724,g2,[],6);
var Model=C([pn]);
var OUT=null;function NewCOMObject(){return {open:function(){},setRequestHeader:function(){},send:function(b){OUT=b},status:500,responseText:'x'}}
function alert(){}
"""
p2 = os.path.join(tempfile.mkdtemp(), "s2.js")
open(p2, "w").write(mock2 + js + "\nrequire('fs').writeFileSync(process.argv[2], OUT);")
nd = subprocess.run(["node", p2, p2 + ".json"], capture_output=True, text=True); assert nd.returncode == 0, nd.stderr
d2 = json.load(open(p2 + ".json"))
bt = {bool(b.get("f")): round(b["l"]) for b in d2["butts"]}
assert bt == {False: 500 + 668 + 444 + 724, True: 56 + 56}, (bt, d2["fe"])
with A.app.app_context():
    A.db().execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES('OV','OV','Услуга Овал 19/1 Белый гладкий','пог.м',1,26000,'',0,0,' услуга овал 19/1 белый гладкий ','cat','svc',1)")
    A.db().execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES('PV','PV','Услуга ПВХ 19/1 Белый гладкий','пог.м',1,4600,'',0,0,' услуга пвх 19/1 белый гладкий ','cat','svc',1)"); A.db().commit()
    B._CAT["at"] = 0
    r2, _ = B.build_rows(A.db(), d2)
E2 = [(x["kind"], x["good"]) for x in r2 if x["kind"] in ("edge", "edgefig")]
assert E2 == [("edge", "PV"), ("edgefig", "OV")], E2
print("EDGEFIG OK")
assert B.hole_svc("5|1|2") == "nasechka" and B.hole_svc("8|1|0") == "nasechka" and B.hole_svc("2|16|0") == "nasechka" and B.hole_svc("5|9|2") == "glukh" and B.hole_svc("35|12|2") == "petlya"
print("DEPTH1 OK")
assert B.hole_svc("40|1|2") == "nasechka" and B.hole_svc("50|1|2") == "cnc35"
print("DEPTH1-40 OK")
mc = B.mill_calc({"pm": [{"nm": "a", "w": 300, "h": 500, "q": 1, "pz": [{"n": "Фреза", "l": 1000, "pw": 4, "pd": 10}, {"n": "Фреза", "l": 500, "pw": 12, "pd": 8}, {"n": "Паз подсветка", "l": 300, "pw": 3, "pd": 1}]}]})
assert mc["hdf"] == 1.0 and mc["led"] == 0.8, mc
print("PAZ WIDTH OK")
assert B.hole_svc("6|3|2") == "nasechka" and B.hole_svc("5|2|2") == "nasechka" and B.hole_svc("6|4|2") == "glukh" and B.hole_svc("8|3|2") == "nasechka" and B.hole_svc("8|10|2") == "glukh"
print("DEPTH3 OK")
mc = B.mill_calc({"pm": [{"nm": "a", "w": 300, "h": 500, "q": 1, "pz": [{"n": "Паз хдф 5х10", "l": 1000, "pw": 5, "pd": 10}]}]})
assert mc["hdf"] == 1.0 and mc["led"] == 0, mc
print("PAZ NAME OK")
with A.app.app_context():
    A.db().execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,parent,folder,deleted,lc,src,cvis) VALUES('cnX','','Папка X','',1,0,'',1,0,' папка x ','cat',1)")
    A.db().execute("UPDATE erp_goods SET parent='cnX' WHERE ext='M1'"); A.db().commit()
h = c.get("/erp/goods?pick=1&g=M1").get_data(as_text=True)
assert 'var GSEL="M1", F="cnX"' in h, re.findall(r"var GSEL[^;]*", h)
print("PICK FOLDER OK")
fs = B.furn_summary([("Hettich тандем 400 мм. L", "", 13), ("Hettich тандем 400 мм. R", "", 13), ("Hettich тандем 500 мм. L", "", 12), ("Hettich тандем 500 мм. R", "", 12)])
assert fs[0]["n"] == 25 and [(r["name"], r["n"]) for r in fs[0]["rows"]] == [("Hettich тандем 400 мм. (L+R)", 13), ("Hettich тандем 500 мм. (L+R)", 12)], fs
# «УТ» (утолщение) — эни 1190 мм дан катта бўлса ҳам станокда; 1050 мм ли оддий деталь ҳам станокда (чегара 1190)
mc = B.mill_calc({"pm": [{"nm": "УТ спинка верхный", "w": 1852, "h": 1250, "q": 1}, {"nm": "бок", "w": 2000, "h": 1050, "q": 1},
                         {"nm": "крыша", "w": 2000, "h": 1200, "q": 1}]})
assert [x["nm"] for x in mc["nocnc"]] == ["крыша"], mc["nocnc"]

print("GUIDE KIT ROWS OK")
