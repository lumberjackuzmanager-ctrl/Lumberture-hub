"""1С'дан ўтилгандан кейин (26.10.2026): платформада проведен қилинган накладной ва тўлов — мижоз қарзи («Должники») ва
Telegram хабарига 1С ҳужжатлари каби тушади (1С архиви + платформа)."""
import os, sys, tempfile
p = os.environ.get("RENT_C1_TEST", r"C:\new base")
if not os.path.isfile(os.path.join(p, "1Cv7.DD")):
    print("1C PF TEST SKIPPED (нет копии базы)"); sys.exit(0)
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, c1, erp
import datetime as _dt
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/1c/settings", data={"c1_path": p, "c1_doc_notify": "1", "c1_new_remind": "1", "csrf": "t"}).status_code == 302
today = _dt.date.today().isoformat()
with A.app.app_context():
    con = A.db()
    erp.sync(force=True, con=con)
    c1.sync_clients(force=True)
    con.execute("UPDATE c1_seen SET notified=1"); con.commit()
    per, bal = c1.debts()
    some = next(k for k, v in bal.items() if v < -1 and c1.clients().get(k, {}).get("name"))
    loc = A.q("SELECT * FROM clients WHERE onec_code=?", ("1c77:" + some,), one=True)
    con.execute("UPDATE clients SET active=1, sms_enabled=1, c1_doc=1, sms_paused_until=NULL WHERE id=?", (loc["id"],))
    b0 = A.client_usd_balance(loc["id"])
    con.execute("INSERT OR REPLACE INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat) "
                "VALUES('ci990001','ci990001','ЛДСП Тест Белый 16мм','лист',2,30,'',0,0,' лдсп тест белый 16мм ','cat','good')")
    con.execute("INSERT INTO erp_clients(ext, code, name, phones, folder, deleted, lc, src) VALUES('P990','P990','Янги Мижоз','+998 90 555 44 33',0,0,' янги мижоз ','pf')")
    # 1С мижозига платформа накладнойи: $60, ичида $20 тўланган; янги мижозга $45 накладной ва кейин $30 тўлов
    d1 = con.execute("INSERT INTO erp_docs(kind,no,date,time,client,rate,usd,uzs,discount,nal,pay_usd,pay_uzs,paid,status,src) "
                     "VALUES('sale','9901',?,'10:00:00',?,12500,60,750000,0,1,20,0,20,'posted','pf')", (today, some)).lastrowid
    con.execute("INSERT INTO erp_lines(doc_id,lineno,good,qty,unit,price,usd,price_uzs,uzs) VALUES(?,1,'ci990001',2,'лист',30,60,375000,750000)", (d1,))
    d2 = con.execute("INSERT INTO erp_docs(kind,no,date,time,client,rate,usd,uzs,discount,nal,pay_usd,pay_uzs,paid,status,src) "
                     "VALUES('sale','9902',?,'11:00:00','P990',12500,45,562500,0,1,0,0,0,'posted','pf')", (today,)).lastrowid
    con.execute("INSERT INTO erp_lines(doc_id,lineno,good,qty,unit,price,usd,price_uzs,uzs) VALUES(?,1,'ci990001',1.5,'лист',30,45,375000,562500)", (d2,))
    d3 = con.execute("INSERT INTO erp_docs(kind,no,date,time,rate,status,src) VALUES('pay','77',?,'12:00:00',12500,'posted','pf')", (today,)).lastrowid
    con.execute("INSERT INTO erp_lines(doc_id,lineno,client,usd,uzs,debt) VALUES(?,1,'P990',10,250000,30)", (d3,))
    con.execute("INSERT INTO erp_docs(kind,no,date,time,client,usd,nal,paid,status,src) VALUES('sale','9903',?,'13:00:00',?,99,1,0,'draft','pf')", (today, some))
    con.commit()
    mv = c1.pf_moves(con)
    assert sorted(m[4] for m in mv[some]) == [-60, 20] and sorted(m[4] for m in mv["P990"]) == [-45, 30], mv     # черновик кирмайди
    c1.sync_clients()
    assert abs(A.client_usd_balance(loc["id"]) - (b0 + 40)) < 0.02, (b0, A.client_usd_balance(loc["id"]))
    nl = A.q("SELECT * FROM clients WHERE onec_code='1c77:P990'", one=True)
    assert nl and nl["name"] == "Янги Мижоз" and "платформы" in nl["notes"] and abs(A.client_usd_balance(nl["id"]) - 15) < 0.02, dict(nl) if nl else None
    seen = {r["iddoc"]: dict(r) for r in A.q("SELECT * FROM c1_seen WHERE notified=0")}
    assert f"pf:{d1}" in seen and f"pf:{d2}" in seen and f"pf:{d3}" in seen, seen
    assert abs(seen[f"pf:{d1}"]["amt"] + 40) < 0.01 and seen[f"pf:{d1}"]["doc_no"] == "9901"
    # накладной расми (Windows: файл номида «:» йўқ), «олдин/кейин» қарз
    im = c1.invoice_image(f"pf:{d1}", A.q("SELECT * FROM clients WHERE id=?", (loc["id"],), one=True))
    assert im and os.path.isfile(im) and ":" not in os.path.basename(im), im
    con.execute("UPDATE clients SET active=1, sms_enabled=1, c1_doc=1 WHERE id=?", (nl["id"],)); con.commit()
    n0 = A.scalar("SELECT COUNT(*) FROM tg_out")
    sent = c1.notify_new_docs(_dt.datetime.now() + _dt.timedelta(hours=1))
    assert sent >= 1 and A.scalar("SELECT COUNT(*) FROM c1_seen WHERE notified=0") == 0
    txts = [r["text"] for r in A.q("SELECT text FROM tg_out WHERE id>? ORDER BY id", (n0,))]
    msg = "\n".join(txts)
    assert "9901" in msg, txts
    k = c1._doc_amounts([dict(kind=210, iddoc=f"pf:{d1}", cid=some, amt=-40.0)])
    assert abs(k[0][1] - 60) < 0.01 and abs(k[1][1] - 20) < 0.01 and abs(k[1][2] - 20) < 0.01, k
    k = c1._doc_amounts([dict(kind=1195, iddoc=f"pf:{d3}", cid="P990", amt=30.0)])
    assert abs(k[1][0] - 250000) < 1 and abs(k[1][1] - 10) < 0.01 and abs(k[1][2] - 30) < 0.01, k
    m = c1.client_moves_all(some)
    assert m[-1]["iddoc"] == f"pf:{d1}" and abs(m[-1]["balance"] - (bal[some] - 40)) < 0.05
    # карточкага янги рақам биринчи қилиб қўшилса (26.10) — Telegram'и топилмаган мижоз шу рақамга ўтади; уланганига тегилмайди
    assert nl["phone"] == "+998 90 555 44 33"
    con.execute("UPDATE clients SET tg_status='none' WHERE id=?", (nl["id"],))
    con.execute("UPDATE erp_clients SET phones='98 365-56-57, +998 90 555 44 33' WHERE ext='P990'"); con.commit()
    c1.sync_clients()
    r = A.q("SELECT phone, tg_status FROM clients WHERE id=?", (nl["id"],), one=True)
    assert r["phone"] == "+998 98 365 56 57" and r["tg_status"] == "", dict(r)
    con.execute("UPDATE clients SET tg_status='ok' WHERE id=?", (nl["id"],))
    con.execute("UPDATE erp_clients SET phones='90 111-22-33' WHERE ext='P990'"); con.commit()
    c1.sync_clients()
    assert A.q("SELECT phone FROM clients WHERE id=?", (nl["id"],), one=True)["phone"] == "+998 98 365 56 57"
print("C1 PF OK")
