"""Распил KPI: листга айлантириш, смена (9 соат), иккаласига тўлиқ, такрор, бекор, KPI'га ёзиш, саҳифа."""
import os, sys, tempfile, json
from datetime import datetime, timedelta, date
tmp = tempfile.mkdtemp()
os.environ["RENT_DB"] = os.path.join(tmp, "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil as P, ready as R, kassa as K

A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302

L = lambda name, qty, price, unit="кол-в": dict(name=name, qty=qty, price_uzs=price, price=price, unit=unit)
with A.app.app_context():
    A.set_settings(auto_from="2000-01")
    # ---- листга айлантириш
    s, m, det = P.calc([L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", 6, 40000),       # 6 (10 листдан 6 таси кесилди — материал қатори саналмайди)
                        L("ЛДСП Ультрадекор 5,0325 Кашемир", 10, 900000, "лист"),
                        L("Распил ХДФ (Пакетный)", 6, 25000),                         # 6/2 = 3
                        L("Распил ХДФ (Пакетный)", 4, 5000),                          # арзон 4/4 = 1
                        L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв", 10, 25000),               # арзон 10/2 = 5
                        L("Распил Л И С Т Т А Ш К А Р И Д А Н  (Пакетный)", 2, 50000),  # 2
                        L("Распил Столешница", 1, 22000),                             # столешница 1=1 (арзон қоидаси ҳам ишлайди)
                        L("Распил Кусок", 3.5, 11000, "кв.м"),                        # 3.5/5 = 0.7
                        L("Распил под углом", 2, 5000),                               # 2*5000/40000 = 0.25
                        L("Распил под 45*", 6, 5000, "пог.м"),                        # 45°: 6 м
                        L("Распил Кромка", 3, 2000)])                                 # саналмайди
    assert abs(s - (6 + 3 + 1 + 5 + 2 + 1 + 0.7 + 0.25)) < 1e-6 and m == 6, (s, m, det)
    txt = P.detail_text(det)
    assert "ЛДСП/ЛМДФ 6" in txt and "45°: 6 м" in txt and "Кромка" not in txt, txt

    # ---- ишчилар ва исмлар
    db = A.db()
    for nm in ("Жасур", "Улмас", "Огабек", "Жасурбек Носиров"):
        db.execute("INSERT INTO k_workers(name) VALUES(?)", (nm,))
    db.commit()
    wid = {r["name"]: r["id"] for r in A.q("SELECT id, name FROM k_workers")}
    J, U, O = wid["Жасур"], wid["Улмас"], wid["Огабек"]
    op, hp, unk, amb = P.parse_crew("Оператор Жасур, ёрдамчи Улмас")
    assert (op, hp, unk, amb) == (J, U, [], []), (op, hp, unk, amb)
    assert P.parse_crew("operator Og'abek yordamchi Ulmas")[:2] == (O, U)
    assert P.parse_crew("Иш бошладим", sender_kw=O)[:2] == (O, None)
    assert P.parse_crew("оператор Фаррух")[2] == ["Фаррух"]

    # ---- смена: оператор + ёрдамчи иккаласига тўлиқ, 9 соатдан кейин 2-смена
    db.execute("INSERT INTO ready_workers(id,name,phone,user_id,last_id,role,kw_id) VALUES(7,'Жасур (распил)','998901110000',5,10,'raspil',?)", (J,))
    db.commit()
    rw = dict(A.q("SELECT * FROM ready_workers WHERE id=7", one=True))
    T0 = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=1)
    day = T0.date().isoformat()
    db.execute("UPDATE k_workers SET salary=8000000 WHERE id=?", (U,)); db.commit()
    ack = P.start_crew(rw, "Оператор Жасур ёрдамчи Улмас", 100, T0)
    ts = {r["worker_id"]: r["ts"] for r in A.q("SELECT worker_id, ts FROM k_ws WHERE ym=?", (day[:7],))}
    assert K.ts_list(ts[J])[T0.day - 1] == "ли" and K.ts_list(ts[U])[T0.day - 1] == "9", ts
    # табел ботдан қайта тўлади: бўш катак — тўлади, қўлда ёзилгани — қолади, «Тест» ишчи — ёзилмайди
    _ws = lambda kw: K.ts_list(A.q("SELECT ts FROM k_ws WHERE ym=? AND worker_id=?", (day[:7], kw), one=True)["ts"])
    c_ = _ws(J); c_[T0.day - 1] = ""; db.execute("UPDATE k_ws SET ts=? WHERE ym=? AND worker_id=?", ("|".join(c_), day[:7], J))
    c_ = _ws(U); c_[T0.day - 1] = "4.5"; db.execute("UPDATE k_ws SET ts=? WHERE ym=? AND worker_id=?", ("|".join(c_), day[:7], U))
    TW = db.execute("INSERT INTO k_workers(name, salary, active) VALUES('Тест (раҳбар)', 0, 1)").lastrowid
    db.execute("INSERT INTO k_att(day, kw, in_at) VALUES(?,?,?)", (day, TW, day + " 09:00:00")); db.commit(); K._memo.clear()
    P.resync_month(day[:7])
    assert _ws(J)[T0.day - 1] in ("ли", "9") and _ws(U)[T0.day - 1] == "4.5", (_ws(J), _ws(U))
    assert not A.q("SELECT 1 FROM k_ws WHERE ym=? AND worker_id=?", (day[:7], TW)) and P.is_test_kw(TW) and not P.is_test_kw(J)
    A.set_settings(auto_from="2099-01")                                 # KPI ҳали қўлда
    assert "08:00" in ack and "Улмас" in ack, ack

    import c1
    lines_by = {}
    docs = {}
    def fake_pick(text, ocr):
        no = (text or ocr).split()[0] if (text or ocr) else ""
        if no in docs:
            return dict(iddoc=docs[no], no=no, date=date.today()), "ok", "рақам"
        return None, ("nodoc" if no.isdigit() else "unknown"), no
    R.pick_doc = fake_pick
    R.doc_client = lambda iddoc: ("Мижоз " + iddoc, None)
    c1.sale_doc = lambda iddoc: dict(lines=lines_by[iddoc])
    docs.update({"6241": "A1", "6242": "A2", "6243": "A3"})
    lines_by.update({"A1": [L("Распил ЛДСП/ЛМДФ 5,796 кв (Пакетный)", 4, 40000)],
                     "A2": [L("Распил ХДФ (Пакетный)", 4, 25000), L("Распил под 45*", 3, 5000, "пог.м")],
                     "A3": [L("ЛДСП Кашемир", 2, 900000, "лист")]})
    jid, ack = P.handle_job(rw, "6241 кесилди", [], 101, T0 + timedelta(hours=2))
    assert "✅ №6241" in ack and "4 лист" in ack and "Жасур + Улмас" in ack, ack
    jid2, ack = P.handle_job(rw, "6242 хдф билан", [], 102, T0 + timedelta(hours=9, minutes=30))     # 2-смена
    jid3, ack3 = P.handle_job(rw, "6241 кесилди", [], 103, T0 + timedelta(hours=10))
    assert "аввал тўлиқ ҳисобланган" in ack3, ack3
    _, ack4 = P.handle_job(rw, "6243", [], 104, T0 + timedelta(hours=10))
    assert "распил хизмати йўқ" in ack4, ack4
    _, ack5 = P.handle_job(rw, "", ["/x.jpg"], 105, T0 + timedelta(hours=10))
    assert "аниқлай олмадим" in ack5, ack5
    # аниқланмаган ишга рақамни reply қилиб ёзса — шу иш тузатилади
    docs["6244"] = "A4"; lines_by["A4"] = [L("Распил Акрил (Пакетный)", 1, 40000)]
    j5 = A.q("SELECT id FROM raspil_job WHERE msg_id=105", one=True)["id"]
    jj, ack6 = P.handle_job(rw, "6244 ҳаммаси", [], 106, T0 + timedelta(hours=10, minutes=5), reply_to=105)
    assert jj == j5 and "✅ №6244" in ack6, ack6
    t = P.day_totals(day)
    assert t[J]["d"] == 4 and t[J]["n"] == 2 + 1 and t[J]["m45"] == 0 and t[U]["d"] == 4 and t[U]["n"] == 3, t
    # ёрдамчи кечроқ келган бўлса — 9 соат унинг бошлаган вақтидан
    end = P.end_crew(rw, T0 + timedelta(hours=11))
    assert "иш тугади" in end and "Жасур" in end and "11 соат" in end, end

    # ---- KPI'га ёзиш (ёқилганда), ставкалар ой бўйича
    ym = day[:7]
    A.set_settings(auto_from="2099-01")
    P.sync_kpi(day)
    assert not A.q("SELECT 1 FROM k_kpi", one=True)                     # қўлда ой — ёзилмайди
    A.set_settings(raspil_kpi="1", auto_from="2000-01")
    P.sync_kpi(day)
    q = {(r["worker_id"], r["kind"], r["shift"]): r["qty"] for r in
         A.q("SELECT c.worker_id, c.kind, c.shift, k.qty FROM k_kpi k JOIN k_kpi_cols c ON c.id=k.col_id WHERE k.ym=?", (ym,))}
    assert q == {(J, "raspil", "d"): 4, (J, "raspil", "n"): 3, (U, "raspil", "d"): 4, (U, "raspil", "n"): 3}, q
    # бекор → KPI қайта ҳисобланади
    ok, _ = P.cancel_job(jid2)
    assert ok
    q2 = {(r["worker_id"], r["kind"], r["shift"]): r["qty"] for r in
          A.q("SELECT c.worker_id, c.kind, c.shift, k.qty FROM k_kpi k JOIN k_kpi_cols c ON c.id=k.col_id WHERE k.ym=?", (ym,))}
    assert q2.get((J, "raspil", "n")) == 1 and (J, "m45", "d") not in q2 and q2[(J, "raspil", "d")] == 4, q2

    # ---- ready.poll йўналтириш: распилчининг «тайёр»сиз хабари ready'га тушмайди
    msgs = [dict(worker=rw, ent=None, msg_id=200, date=(T0 + timedelta(hours=10)).timestamp(), text="бекор", photo=None, grouped=None, reply_to=None)]
    rep = P.process(msgs)
    assert rep and "бекор" in rep[0][2], rep

# ---- саҳифа
r = c.get("/kassa/raspil?day=" + day); assert r.status_code == 200
html = r.get_data(as_text=True)
assert "Жасур" in html and "6241" in html and "Ҳисоблаш қоидалари" in c.get("/kassa/raspil/settings").get_data(as_text=True), html[:500]
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post("/kassa/raspil", data=dict(csrf="t", action="addphone", day=day, kw=str(O), phone="+998 90 222 33 44"))
assert r.status_code == 302
with A.app.app_context():
    assert A.q("SELECT role, kw_id FROM ready_workers WHERE phone='998902223344'", one=True)["kw_id"] == O
r = c.get("/kassa/kpi?ym=" + day[:7]); assert r.status_code == 200 and "Распил 45°" in r.get_data(as_text=True)
with A.app.app_context():
    # ---- қисман кесиш: 10 листдан бугун 5 та, эртасига қолгани
    assert P.part_count("6245 5 та", "6245") == 5 and P.part_count("6245", "6245") is None and P.part_count("5", "") == 5
    docs["6245"] = "A5"; lines_by["A5"] = [L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", 10, 40000)]
    T1 = T0 + timedelta(days=1)
    _, a1 = P.handle_job(rw, "6245 5 та", [], 300, T1)
    assert "Ҳисобланди: 5 лист" in a1 and "қолди 5" in a1, a1
    _, a2 = P.handle_job(rw, "6245 кесилди", [], 301, T1 + timedelta(days=1))
    assert "Ҳисобланди: 5 лист" in a2 and "тўлиқ кесилди" in a2, a2
    _, a3 = P.handle_job(rw, "6245 кесилди", [], 302, T1 + timedelta(days=1, hours=1))
    assert "аввал тўлиқ ҳисобланган" in a3, a3
    _, a4 = P.handle_job(rw, "6241 20", [], 303, T1)                      # 6241 тўлиқ кесилган
    assert "аввал тўлиқ" in a4, a4
    assert "Кашемир" in P.mats_text(A.q("SELECT mats FROM raspil_job WHERE doc_no='6243'", one=True)["mats"])
    rr = P.report_rows(T0.date().isoformat(), (T1 + timedelta(days=2)).date().isoformat(), q="6245")
    assert len(rr) == 3 and all(r["doc_no"] == "6245" for r in rr), rr
r = c.get(f"/kassa/raspil/report?d1={day}&d2={(T1 + timedelta(days=2)).date().isoformat()}&st=all")
assert r.status_code == 200 and "6245" in r.get_data(as_text=True)
r = c.get(f"/kassa/raspil/report?d1={day}&d2={(T1 + timedelta(days=2)).date().isoformat()}&csv=1")
import io as _io
from openpyxl import load_workbook as _lw
_t = "\n".join(";".join("" if v is None else str(v) for v in row) for ws in _lw(_io.BytesIO(r.data)) for row in ws.iter_rows(values_only=True))
assert r.status_code == 200 and "6245" in _t and "Ким ташлади" in _t, _t[:500]
with A.app.app_context():
    # ---- ХДФ мижоз олиб кетаётганда: аввал «хдфсиз», кейин «хдф»
    assert P.groups_of("хдфсиз") == ("main",) and P.groups_of("ХДФ кейин") == ("main",) and P.groups_of("hdf") == ("hdf",) and P.groups_of("") == ("main",) and P.groups_of("хдф билан") == ("main", "hdf")
    docs["6246"] = "A6"; lines_by["A6"] = [L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", 5, 40000), L("Распил ХДФ (Пакетный)", 6, 25000)]
    _, b1 = P.handle_job(rw, "6246 хдфсиз", [], 400, T1)
    assert "Ҳисобланди: 5 лист" in b1 and "ХДФ 3" in b1 and "«хдф»" in b1, b1
    _, b2 = P.handle_job(rw, "6246 хдф", [], 401, T1 + timedelta(hours=3))
    assert "Ҳисобланди: 3 лист" in b2 and "тўлиқ кесилди" in b2, b2
    _, b3 = P.handle_job(rw, "6246 хдф", [], 402, T1 + timedelta(hours=4))
    assert "аввал тўлиқ" in b3 and "ХДФ" in b3, b3
    # ҳеч нарса ёзилмаса — ХДФ ҳисобланмайди
    docs["6247"] = "A7"; lines_by["A7"] = [L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", 2, 40000), L("Распил ХДФ (Пакетный)", 4, 25000)]
    _, c1_ = P.handle_job(rw, "6247 кесилди", [], 410, T1)
    assert "Ҳисобланди: 2 лист" in c1_ and "ХДФ 2" in c1_, c1_
    _, c2_ = P.handle_job(rw, "6247 кесилди", [], 411, T1)
    assert "кутилмоқда" in c2_ and "«хдф»" in c2_, c2_
    _, c3_ = P.handle_job(rw, "6247 хдф", [], 412, T1)
    assert "Ҳисобланди: 2 лист" in c3_ and "тўлиқ" in c3_, c3_
    # ёзувсиз — умуман ҳисобланмайди; жавоб қилиб ёзса ҳисобланади
    docs["6248"] = "A8"; lines_by["A8"] = [L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", 3, 40000)]
    _, d1 = P.handle_job(rw, "6248", [], 420, T1)
    assert "ёзув йўқ" in d1 and "ЛДСП/ЛМДФ 3" in d1, d1
    _, d2 = P.handle_job(rw, "кесилди", [], 421, T1, reply_to=420)
    assert "✅ №6248" in d2 and "Ҳисобланди: 3 лист" in d2, d2
    assert A.scalar("SELECT COUNT(*) FROM raspil_job WHERE doc_no='6248'") == 1
    docs["6249"] = "A9"; lines_by["A9"] = [L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", 2, 40000)]
    P.handle_job(rw, "6249", [], 430, T1)
    rp = P.process([dict(worker=rw, ent=None, msg_id=431, date=T1.timestamp(), text="кесилди", photo=None, grouped=None, reply_to=430)])
    assert "✅ №6249" in rp[0][2], rp
    _, b4 = P.handle_job(rw, "6241 хдф", [], 403, T1)
    assert "ХДФ распили йўқ" in b4, b4
r = c.get(f"/kassa/raspil/report?d1={day}&d2={(T1 + timedelta(days=2)).date().isoformat()}")
assert "Ишчи изоҳи" in r.get_data(as_text=True) and "хдфсиз" in r.get_data(as_text=True)
with A.app.app_context():
    # ---- двухпилка: столешница/45°/кусок — алоҳида, арра марказида фақат лист
    T2 = T1 + timedelta(days=5)
    docs["6250"] = "B1"; lines_by["B1"] = [L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", 4, 40000), L("Распил Столешница", 1, 22000),
                                          L("Распил под 45*", 3, 5000, "пог.м"), L("Распил Кусок", 5, 11000, "кв.м")]
    rw2 = dict(rw)
    P.start_crew(rw, "Оператор Жасур ёрдамчи Улмас", 500, T2)
    _, e1 = P.handle_job(rw, "6250 кесилди", [], 501, T2 + timedelta(hours=1))
    assert "Ҳисобланди: 4 лист" in e1 and "двухпилка иши 2" in e1 and "45°" not in e1.split("Ҳисобланди")[1].split("\n")[0], e1
    _, e2 = P.handle_job(rw, "6250 кесилди", [], 502, T2 + timedelta(hours=1))
    assert "двухпилкачи ташлайди" in e2, e2
    dpk = A.q("SELECT * FROM ready_workers WHERE phone='998902223344'", one=True)
    rwo = dict(dpk)
    m = P.start_crew(rwo, "Двухпилка Огабек", 503, T2 + timedelta(hours=2))
    assert "двухпилка: Огабек" in m, m
    assert A.q("SELECT machine FROM raspil_crew ORDER BY id DESC LIMIT 1", one=True)["machine"] == "dp"
    assert P.open_crews(kw=J, at=T2 + timedelta(hours=2))[0]["machine"] == "arra"       # аррачилар сменаси ёпилмади
    _, e3 = P.handle_job(rwo, "6250 кесилди", [], 504, T2 + timedelta(hours=12))
    assert "Ҳисобланди: 2 лист, 45°: 3 м" in e3 and "тўлиқ кесилди" in e3, e3
    t = P.day_totals(T2.date().isoformat())
    assert t[O]["d"] == 2 and t[O]["n"] == 0 and t[O]["m45"] == 3 and t[J]["d"] == 4, t                # двухпилкада 2-смена йўқ
    _, e4 = P.handle_job(rwo, "6241 кесилди", [], 505, T2 + timedelta(hours=3))
    assert "двухпилка иши" in e4 and "йўқ" in e4, e4
with A.app.app_context():
    jw = P.add_wait(rw, "main", 3, "Тест мижоз")
r = c.get("/kassa/raspil?day=" + day); h = r.get_data(as_text=True)
assert r.status_code == 200 and "Цехдан келган ишлар" in h and ">Ц" in h and "Тест мижоз" in h, h[:300]
with c.session_transaction() as s_: s_["csrf"] = "t"
r = c.post("/kassa/raspil", data=dict(csrf="t", action="wlink", jid=str(jw), doc_no="6250", day=day)); assert r.status_code == 302
with A.app.app_context():
    assert A.q("SELECT status FROM raspil_job WHERE id=?", (jw,), one=True)["status"] in ("linked", "wait")
print("RASPIL OK")

# ---- Базис: 1С папкалари бўйича 50% ва присадкадан 5%
with A.app.app_context():
    import c1 as C1
    db = A.db()
    db.execute("INSERT INTO k_workers(name) VALUES('Абдулахад')"); db.execute("INSERT INTO k_workers(name) VALUES('Сайдулла')"); db.commit()
    AB = A.q("SELECT id FROM k_workers WHERE name='Абдулахад'", one=True)["id"]
    SA = A.q("SELECT id FROM k_workers WHERE name='Сайдулла'", one=True)["id"]
    C1.goods = lambda: {"R": dict(name="7 Базис Конструктор (Мебельшик)", folder=True, parent="0", deleted=False),
                        "F1": dict(name="Абдулахад", folder=True, parent="R", deleted=False),
                        "F2": dict(name="Садулла", folder=True, parent="R", deleted=False),
                        "S1": dict(name="Услуга конструктора СТАНДАРТ", folder=False, parent="F1", deleted=False),
                        "S2": dict(name="Услуга конструктора ГОЛД", folder=False, parent="F2", deleted=False),
                        "P1": dict(name="Услуга сверления Под петли (35мм)", folder=False, parent="X", deleted=False),
                        "L1": dict(name="ЛДСП Кашемир", folder=False, parent="X", deleted=False)}
    import datetime as _d
    ym = "2026-09"
    C1.journal = lambda: {"D1": dict(kind=210, posted=True, deleted=False, date=_d.date(2026, 9, 3)),
                          "D2": dict(kind=210, posted=False, deleted=False, date=_d.date(2026, 9, 4)),
                          "D3": dict(kind=210, posted=True, deleted=False, date=_d.date(2026, 9, 5))}
    C1.table = lambda name: [dict(IDDOC="D1", SP216="S1", SP559=1000000), dict(IDDOC="D1", SP216="P1", SP559=200000),
                             dict(IDDOC="D2", SP216="S1", SP559=999999), dict(IDDOC="D3", SP216="S2", SP559=400000),
                             dict(IDDOC="D3", SP216="L1", SP559=5000000)] if name == "DT210" else []
    des, pris = P.basis_month(ym)
    assert des["F1"] == {3: 1000000} and des["F2"] == {5: 400000} and pris == {3: 200000}, (des, pris)
    mp = P.basis_map(); assert mp.get("F1") == AB, mp           # «Садулла» ↔ «Сайдулла» — исм бир хил эмас, қўлда танланади
    A.set_settings(auto_from="2000-01", basis_on="1", basis_map=json.dumps({"F1": AB, "F2": SA}))
    P.basis_sync(ym)
    q = {(r["worker_id"], r["kind"]): (r["qty"], r["rate"]) for r in A.q(
        "SELECT c.worker_id, c.kind, SUM(k.qty) qty, m.rate FROM k_kpi k JOIN k_kpi_cols c ON c.id=k.col_id JOIN k_kpi_mcols m ON m.col_id=c.id AND m.ym=k.ym "
        "WHERE k.ym=? GROUP BY c.id", (ym,))}
    assert q[(AB, "basis")] == (1000000, 0.5) and q[(SA, "basis")] == (400000, 0.5) and q[(AB, "basis_pris")] == (200000, 0.05), q
    A.db().execute("UPDATE k_workers SET cur='USD' WHERE id=?", (AB,)); A.db().commit()      # $ ли конструкторга $ ёзилади
    C1.table = lambda name: [dict(IDDOC="D1", SP216="S1", SP559=1000000, SP905=80), dict(IDDOC="D1", SP216="P1", SP559=200000, SP905=16)] if name == "DT210" else []
    P.basis_sync(ym)
    q = {(r["worker_id"], r["kind"]): r["qty"] for r in A.q(
        "SELECT c.worker_id, c.kind, SUM(k.qty) qty FROM k_kpi k JOIN k_kpi_cols c ON c.id=k.col_id WHERE k.ym=? GROUP BY c.id", (ym,))}
    assert q[(AB, "basis")] == 80 and q[(AB, "basis_pris")] == 16, q
    A.set_settings(basis_pris_on="0"); P.basis_sync(ym)
    assert not A.q("SELECT 1 FROM k_kpi k JOIN k_kpi_cols c ON c.id=k.col_id WHERE c.kind='basis_pris' AND k.ym=?", (ym,), one=True)
r = c.get("/kassa/raspil?day=2026-09-10"); h = r.get_data(as_text=True)
assert r.status_code == 200 and "Базис конструкторлар" in h and "Присадкадан" in h, h[:200]
with A.app.app_context():
    A.set_settings(auto_from="2099-01")
    assert not P.auto_on("2026-10") and P.basis_sync("2026-09") == 0 and P.mark_tabel(AB, "2026-09-20") is False
    A.set_settings(auto_from="2000-01")
with A.app.app_context():
    _kt = P.kusok_types; P.kusok_types = lambda: [("KL", "ЛДСП кусок")]
r = c.post("/kassa/raspil", data=dict(csrf="t", action="kadd", day=day, gid="KL", size="2800 1000", color="кашемир", owner="Алишер 1131"))
assert r.status_code == 302
r = c.get("/kassa/raspil?day=" + day); h = r.get_data(as_text=True)
assert r.status_code == 200 and "Кусок омбори" in h and "Алишер 1131" in h and "кашемир" in h, h[:300]
with A.app.app_context():
    kid = P.kusok_stock("KL")[0]["id"]
c.post("/kassa/raspil", data=dict(csrf="t", action="kdel", day=day, kid=str(kid)))
with A.app.app_context():
    assert not P.kusok_stock("KL"); P.kusok_types = _kt
with A.app.app_context():
    db = A.db()
    db.execute("INSERT INTO k_kpi_cols(worker_id,kind,shift,rate,sort) VALUES(9001,'oval','d',777,1)")
    c0 = db.execute("SELECT id FROM k_kpi_cols WHERE worker_id=9001").fetchone()[0]
    db.execute("INSERT INTO k_kpi_mcols(ym,col_id,rate,sort) VALUES('2026-10',?,777,1)", (c0,))
    cid = P._kpi_col(db, 9002, "oval", "d", "2026-10", 0)
    assert db.execute("SELECT rate FROM k_kpi_mcols WHERE col_id=? AND ym='2026-10'", (cid,)).fetchone()[0] == 777
rid = None
with A.app.app_context():
    rid = A.q("SELECT id FROM ready_workers WHERE role='raspil' LIMIT 1", one=True)
if rid:
    r = c.post("/kassa/raspil", data=dict(csrf="t", action="perms", day=day, id=str(rid["id"]), p_kromka_op="1", p_arra_hp="1")); assert r.status_code == 302
    with A.app.app_context():
        assert set(json.loads(A.q("SELECT perms FROM ready_workers WHERE id=?", (rid["id"],), one=True)["perms"])) == {"kromka:op", "arra:hp"}
    with A.app.app_context():
        kw_ = A.q("SELECT kw_id FROM ready_workers WHERE id=?", (rid["id"],), one=True)["kw_id"]
    h = c.get(f"/staff/{kw_}").get_data(as_text=True); assert "Станки:" in h and "Кромка (главный)" in h and "Крой:" in h, h[-2000:]
    # карточкадан доступни ўзгартириш
    r = c.post(f"/staff/{kw_}", data=dict(csrf="t", action="perms", rid=str(rid["id"]), p_arra_op="1")); assert r.status_code == 302
    with A.app.app_context():
        assert json.loads(A.q("SELECT perms FROM ready_workers WHERE id=?", (rid["id"],), one=True)["perms"]) == ["arra:op"]
h = c.get("/kassa/raspil?day=" + day).get_data(as_text=True); assert "Менежер крой" in h and "Настройки цеха" in h
assert c.get("/kassa/raspil/settings").status_code == 200
print("BASIS OK")
