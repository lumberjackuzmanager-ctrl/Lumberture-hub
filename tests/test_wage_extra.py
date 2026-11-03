"""Ойлик: «Ойлик» катагига қўлда ёзилган бўлса ҳам «Қўшимча (±)» қўшилади (эгаси, 03.11); Enter — «Сақлаш» (✕ эмас)."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, kassa as K
A.app.testing = True
ym = "2026-10"
with A.app.app_context():
    db = A.db()
    w1 = db.execute("INSERT INTO k_workers(name, salary, cur, active) VALUES('Абдулахад', 0, 'USD', 1)").lastrowid
    w2 = db.execute("INSERT INTO k_workers(name, salary, cur, active) VALUES('Дилмурод', 2800000, 'UZS', 1)").lastrowid
    db.execute("INSERT INTO k_ws(ym, worker_id, sort, salary, extra, total_override, opening, opening_set, ts) VALUES(?,?,10,0,170,697,654.19,1,'')", (ym, w1))
    db.execute("INSERT INTO k_ws(ym, worker_id, sort, salary, extra, total_override, opening, opening_set, ts) VALUES(?,?,20,2800000,-100000,NULL,0,1,?)",
               (ym, w2, "|".join(["9"] * 28)))
    db.execute("INSERT INTO k_days(ym, day) VALUES(?, 1)", (ym,))
    db.commit(); K._memo.clear()
    st = {r["name"]: r for r in K.month_calc(ym)["staff"]}
    a = st["Абдулахад"]
    assert round(a["total"], 2) == 867 and round(a["closing"], 2) == round(867 + 654.19, 2), (a["total"], a["closing"])
    d = st["Дилмурод"]
    assert round(d["total"]) == 2700000, d["total"]                      # автомат: кунбай + қўшимча (ўзгармади)
    db.execute("UPDATE k_ws SET extra=0 WHERE worker_id=?", (w1,)); db.commit(); K._memo.clear()
    assert round({r["name"]: r for r in K.month_calc(ym)["staff"]}["Абдулахад"]["total"], 2) == 697
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
h = c.get(f"/kassa/staff?ym={ym}").get_data(as_text=True)
i_def, i_rm = h.find('tabindex="-1" aria-hidden="true"'), h.find('value="remove"')
assert 0 < i_def < i_rm, (i_def, i_rm)                                 # Enter — биринчи тугма «Сақлаш»
print("WAGE EXTRA OK")
