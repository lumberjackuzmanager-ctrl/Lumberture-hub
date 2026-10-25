"""1С ўчирилгандан кейин (26.10.2026): кунлик ҳисобот, касса «Кирим», завод приходи, олиб келиш рўйхати, мижоз ҳисоботи ва
«тайёр» хабари платформада проведен қилинган ҳужжатларни ҳам ҳисоблайди."""
import os, sys, tempfile
from datetime import date, timedelta
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, owner as O, kassa as K, pickup as PU, report as R, ready as RD, erp

P = os.environ.get("RENT_C1_TEST", "")
D = date(2026, 10, 27)
T = date.today()
with A.app.app_context():
    if P:
        A.set_settings(c1_path=P)
    con = A.db()
    con.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12500,'bot',?,?,'',0)", (D.isoformat(), D.isoformat() + " 09:00:00"))
    con.execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc, lim, ckind, src) VALUES('PX1','Тест мижоз','+998 90 000-00-01',0,0,' тест мижоз ',1000,'client','pf')")
    con.execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc, lim, ckind, src) VALUES('PS1','Эман тест','',0,0,' эман тест ',0,'supplier','pf')")
    con.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat) VALUES('cn9001','','ЛДСП Тест','',2,0,'',1,0,' лдсп тест ','cat','good')")
    con.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat) VALUES('ci9001','','ЛДСП Тест Белый 16мм','лист',2,40,'cn9001',0,0,' лдсп тест белый ','cat','good')")

    def doc(kind, day, client, usd=0, uzs=0, paid=0, pay_usd=0, pay_uzs=0, status="posted", no="7001", tm="10:00:00"):
        return con.execute("INSERT INTO erp_docs(kind,no,date,time,client,rate,usd,uzs,pay_usd,pay_uzs,paid,nal,status,src) VALUES(?,?,?,?,?,12500,?,?,?,?,?,1,?,'pf')",
                           (kind, no, day.isoformat(), tm, client, usd, uzs, pay_usd, pay_uzs, paid, status)).lastrowid
    s1 = doc("sale", D, "PX1", usd=80, uzs=1000000, paid=30, pay_usd=10, pay_uzs=250000)
    con.execute("INSERT INTO erp_lines(doc_id,lineno,good,qty,unit,price,usd,uzs) VALUES(?,1,'ci9001',2,'лист',40,80,1000000)", (s1,))
    p1 = doc("pay", D, "PX1", usd=20, uzs=125000, no="501", tm="11:00:00")
    con.execute("INSERT INTO erp_lines(doc_id,lineno,good,client,qty,usd,uzs,debt) VALUES(?,1,'','PX1',0,10,125000,20)", (p1,))
    r1 = doc("receipt", D, "PS1", usd=500, no="301")
    con.execute("INSERT INTO erp_lines(doc_id,lineno,good,qty,unit,price,usd) VALUES(?,1,'ci9001',3,'лист',30,90)", (r1,))
    doc("sale", D, "PX1", usd=999, status="draft", no="7002")                                   # черновик — ҳисобга кирмайди
    con.commit()

    # 19:00 ҳисоботи: савдо ва завод приходи
    s = O.sales(D)
    assert s["n"] >= 1 and s["usd"] >= 80 - 1e-6 and "Савдо:" in O.sales_text(D), s
    f = O.factory(D)
    assert f["rec_day"] >= 500 - 1e-6 and any(r["name"] == "Эман тест" and abs(r["rec"] - 500) < 1e-6 for r in f["rows"]), f
    # касса: кунлик ва ойлик кирим
    od = K.onec_day(D)
    assert od and od["pf"] == 2 and od["usd"] >= 10 and od["uzs"] >= 125000 and od["s_usd"] >= 10 and od["s_uzs"] >= 250000, od
    mc = dict(days=[dict(day=i, rate=12500) for i in range(1, 32)], avg=12500)
    om = K.onec_month("2026-10", mc)
    assert om and om["zav"] >= 500 - 1e-6 and om["inc"] >= 10 + 10 + (125000 + 250000) / 12500 - 1e-6, om

    # олиб келиш: платформа накладнойи (черновик ҳам) — каталог товари минусга кетса
    s3 = doc("sale", max(T, date.fromisoformat(PU.CUTOVER)), "PX1", usd=400, status="draft", no="7003")
    con.execute("INSERT INTO erp_lines(doc_id,lineno,good,qty,unit,price,usd) VALUES(?,1,'ci9001',10,'лист',40,400)", (s3,))
    con.commit()
    sh = PU.shortages_pf(con)
    assert "ci9001" in sh and sh["ci9001"]["name"] == "ЛДСП Тест Белый 16мм" and sh["ci9001"]["path"] == "ЛДСП Тест", sh
    st = erp.stock_all(con).get("ci9001", 0)                  # приход 3 − сотилган 2 = 1; черновик 10 → −9
    assert abs(sh["ci9001"]["need"] - min(10, -(st - 10))) < 1e-6, (sh, st)
    assert any(r["no"].startswith("7003") for r in sh["ci9001"]["refs"])

    # мижоз ҳисоботи: платформа накладнойи ва тўлови
    stt = R.statement("PX1", days=10, today=D + timedelta(days=1))
    kinds = [(r["kind"], r["no"]) for r in stt["rows"]]
    assert (210, "7001") in kinds and (1195, "501") in kinds and ("7002" not in [n for _, n in kinds]), kinds
    inv = next(r for r in stt["rows"] if r["no"] == "7001")
    assert abs(inv["taken"] - 80) < 1e-6 and abs(inv["paid"] - 30) < 1e-6 and inv["lines"], inv
    assert abs(stt["closing"] - (80 - 30 - 20)) < 1e-6, stt

    # «тайёр»: накладной рақами платформадан топилади
    s4 = doc("sale", T, "PX1", usd=5, no="98765")
    con.execute("INSERT INTO clients(name, phone, active, sms_enabled, onec_code, created_at) VALUES('Тест мижоз','+998 90 000 00 01',1,1,'1c77:PX1',?)", (A.now(),))
    con.commit()
    fd = RD.find_doc("98765")
    assert fd and fd["iddoc"] == f"pf:{s4}" and fd["no"] == "98765", fd
    nm, cl = RD.doc_client(fd["iddoc"])
    assert nm == "Тест мижоз" and cl and cl["onec_code"] == "1c77:PX1", (nm, cl)
print("CUTOVER PF OK")
