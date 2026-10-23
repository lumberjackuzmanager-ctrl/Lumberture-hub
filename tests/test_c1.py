"""Тест чтения 1С 7.7 (DBF). Нужна копия базы: RENT_C1_TEST=путь_к_папке python tests/test_c1.py (иначе пропуск)."""
import os, sys, tempfile
p = os.environ.get("RENT_C1_TEST", r"C:\new base")
if not os.path.isfile(os.path.join(p, "1Cv7.DD")):
    print("1C TEST SKIPPED (нет копии базы)"); sys.exit(0)
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, c1
from datetime import date
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/1c/settings", data={"c1_path": p, "c1_doc_notify": "1", "csrf": "t"}).status_code == 302
with A.app.app_context():
    per, bal = c1.debts()
    # регистр «Долг» = сумма движений
    s = {}
    for r in c1.table("RA445"):
        s[r["SP446"]] = s.get(r["SP446"], 0) + r["SP447"] * (1 if int(r["DEBKRED"]) == 0 else -1)
    assert all(abs(s.get(k, 0) - v) < 0.05 for k, v in bal.items())
    cl = c1.clients(); assert len(cl) > 10
    some = next(k for k, v in bal.items() if v < -1)
    moves = c1.client_moves(some); assert abs(moves[-1]["balance"] - bal[some]) < 0.05
    per2, st = c1.stock(); assert st
for u in ("/1c/clients", "/1c/clients?show=all&q=а", f"/1c/client/{some}", "/1c/sales?d1=2026-10-01&d2=2026-10-13",
          "/1c/receipts?d1=2026-10-01&d2=2026-10-13", "/1c/payments?d1=2026-10-01&d2=2026-10-13", "/1c/stock", "/1c/stock?show=minus", "/1c/settings"):
    r = c.get(u); assert r.status_code == 200, u; assert "Ошибка" not in r.get_data(as_text=True), u
with A.app.app_context():
    sale = next(x for x in c1.sales(date(2026, 1, 1), date(2026, 12, 31)) if x["usd"] > 1)
    d = c1.sale_doc(sale["doc"]["id"]); assert d and d["lines"] and abs(sum(l["usd"] for l in d["lines"]) - d["head"]["SP905"]) < 0.05
assert c.get(f"/1c/sale/{sale['doc']['id']}").status_code == 200
for f, must, mustnot in (("perm", "Разрешил", "Цена"), ("bez", "Товар получил", "претензии"), ("full", "претензии не принимаются", "Разрешил")):
    h = c.get(f"/1c/sale/{sale['doc']['id']}/print?form={f}").get_data(as_text=True)
    assert must in h and mustnot not in h, f
assert c.get("/1c/client/NOPE").status_code == 404
# синхронизация клиентов 1С -> напоминания
with c.session_transaction() as ss: ss["csrf"] = "t"
assert c.post("/1c/sync", data={"force": "1", "csrf": "t"}).status_code == 302
with A.app.app_context():
    per, bal = c1.debts()
    loc = {r["onec_code"][5:]: r for r in A.q("SELECT * FROM clients WHERE onec_code LIKE '1c77:%'")}
    assert loc and all(r["sms_enabled"] == 0 for r in loc.values())
    bad = [k for k, v in bal.items() if abs(v) >= 0.01 and (k not in loc or abs(A.client_usd_balance(loc[k]["id"]) + v) > 0.05)]
    assert not bad, bad[:5]
    lc = loc[some]
    txt = A.client_reminder_text(A.q("SELECT * FROM clients WHERE id=?", (lc["id"],), one=True))
    assert "$" in txt, txt
    st = c1.sync_clients(); assert st["new"] == 0 and st["updated"] == 0
assert c.post("/1c/settings", data={"c1_path": p, "c1_new_remind": "1", "c1_doc_notify": "1", "csrf": "t"}).status_code == 302
with A.app.app_context():
    assert A.scalar("SELECT COUNT(*) FROM clients WHERE onec_code LIKE '1c77:%' AND sms_enabled=0") == 0
h = c.get("/1c/clients").get_data(as_text=True); assert "Напомнить" in h and "Оплачено" in h
assert c.get(f"/clients/{lc['id']}").status_code == 200
# новая накладная/оплата в 1С -> одно сообщение клиенту (после паузы), первый запуск — без рассылки
import datetime as _dt
with A.app.app_context():
    assert A.scalar("SELECT COUNT(*) FROM c1_seen WHERE notified=0 AND doc_date<?", (_dt.date.today().isoformat(),)) == 0
    A.db().execute("UPDATE c1_seen SET notified=1"); A.db().commit()
    r = A.q("SELECT * FROM c1_seen WHERE kind=210 AND amt<0 ORDER BY doc_date DESC LIMIT 1", one=True)
    if r and r["doc_date"] >= (_dt.date.today() - _dt.timedelta(days=2)).isoformat():
        A.db().execute("DELETE FROM c1_seen WHERE cid=? AND doc_date>=?", (r["cid"], r["doc_date"])); A.db().commit()
        c1.sync_clients()
        assert A.scalar("SELECT COUNT(*) FROM c1_seen WHERE notified=0") >= 1
        assert c1.notify_new_docs() == 0                      # ещё ждём оплату
        n0 = A.scalar("SELECT COUNT(*) FROM tg_out") + A.scalar("SELECT COUNT(*) FROM sms_log")
        pend = [dict(x) for x in A.q("SELECT * FROM c1_seen WHERE notified=0")]
        cc = A.q("SELECT * FROM clients WHERE id=?", (pend[0]["client_id"],), one=True)
        assert c1.notify_new_docs(_dt.datetime.now() + _dt.timedelta(hours=1)) == 1, (pend, cc and dict(cc))
        assert A.scalar("SELECT COUNT(*) FROM c1_seen WHERE notified=0") == 0
        t = A.q("SELECT text FROM tg_out ORDER BY id DESC LIMIT 1", one=True)
        assert t and ("Товары и услуги:" in t["text"] or "Возврат" in t["text"]) and "$" in t["text"], dict(t)
# адашиб хабар кетган ҳужжат бошқа мижозга ўтказилса — эски мижозга узр хабари + ҳозирги қарз
with A.app.app_context():
    lc2 = A.q("SELECT * FROM clients WHERE onec_code LIKE '1c77:%' AND active=1 AND sms_enabled=1 LIMIT 1", one=True)
    cid2 = lc2["onec_code"][5:]
    A.db().execute("UPDATE clients SET c1_doc=1, sms_paused_until=NULL WHERE id=?", (lc2["id"],))
    A.db().execute("INSERT INTO c1_seen(iddoc,cid,client_id,doc_date,kind,amt,seen_at,notified,doc_no,gone,sent) VALUES('ZZFAKE',?,?,?,210,34.22,?,1,'6218',0,1)",
                   (cid2, lc2["id"], (_dt.date.today() - _dt.timedelta(days=20)).isoformat(), A.now())); A.db().commit()
    c1.sync_clients(force=True)
    assert A.scalar("SELECT gone FROM c1_seen WHERE iddoc='ZZFAKE'") == 1
    n1 = A.scalar("SELECT COUNT(*) FROM tg_out")
    c1.notify_new_docs(_dt.datetime.now() + _dt.timedelta(hours=1))           # бир марта топилмаган — ҳали хабар йўқ
    assert A.scalar("SELECT gone FROM c1_seen WHERE iddoc='ZZFAKE'") == 1 and A.scalar("SELECT COUNT(*) FROM tg_out") == n1
    c1.sync_clients(force=True)                                                  # кейинги ўқишда ҳам йўқ — тасдиқланди
    assert A.scalar("SELECT gone_n FROM c1_seen WHERE iddoc='ZZFAKE'") == 2
    c1.notify_new_docs(_dt.datetime.now() + _dt.timedelta(hours=1))
    assert A.scalar("SELECT gone FROM c1_seen WHERE iddoc='ZZFAKE'") == 2
    t = A.q("SELECT text FROM tg_out ORDER BY id DESC LIMIT 1", one=True)
    if A.scalar("SELECT COUNT(*) FROM tg_out") > n1:
        assert "6218" in t["text"] and ("ошибочно" in t["text"] or "adashib" in t["text"]) and "$34.22" in t["text"], t["text"]
        print(t["text"])
# 1С чала ўқилса (бирдан кўп ҳужжат «йўқолса») — узр хабари тайёрланмайди
with A.app.app_context():
    A.db().execute("UPDATE c1_seen SET gone=0, gone_n=0"); A.db().commit()
    real = c1.all_moves
    c1.all_moves = lambda: {k: v for i, (k, v) in enumerate(real().items()) if i % 2}     # ярми «йўқ»
    c1.sync_clients(force=True)
    c1.all_moves = real
    assert A.scalar("SELECT COUNT(*) FROM c1_seen WHERE gone=1") <= 5, A.scalar("SELECT COUNT(*) FROM c1_seen WHERE gone=1")
print("1C77 TESTS PASSED")
