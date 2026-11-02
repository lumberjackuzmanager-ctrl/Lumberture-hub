"""2.11 (эгаси): «Зарплата и аванс»да шофёрлар кўриниши шарт эмас — цех ботида «Шофёр» (driver=1) ходим касса куни ва ой итогидаги
ойлик рўйхатида йўқ; унга пул ёзилган бўлса — кўринаверади."""
import os, sys, tempfile, datetime
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, kassa as K
t = datetime.date.today(); ym = f"{t.year}-{t.month:02d}"
with A.app.app_context():
    db = A.db()
    for i, nm in ((1, "Дилмурод"), (2, "Равшан ака 841")):
        db.execute("INSERT INTO k_workers(id,name,salary,cur,sort,active) VALUES(?,?,0,'UZS',?,1)", (i, nm, i * 10))
    db.execute("INSERT INTO ready_workers(name, phone, created_at, role, driver, kw_id, active) VALUES('Равшан ака 841','998900000841',?, 'staff', 1, 2, 1)", (A.now(),))
    db.commit()
    K.ensure_month(ym); K._memo.clear()
c = A.app.test_client()
with c.session_transaction() as s_: s_["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
h = c.get(f"/kassa?ym={ym}&d={t.day}").get_data(as_text=True)
assert "Дилмурод" in h and "Равшан ака 841" not in h, h[-2000:]
h = c.get(f"/kassa/month?ym={ym}").get_data(as_text=True)
assert "Дилмурод" in h and "Равшан ака 841" not in h
with A.app.app_context():                                  # пул ёзилган — кўринади
    A.db().execute("INSERT INTO k_cells(ym,day,kind,ref_id,uzs,usd) VALUES(?,?,'wage',2,100000,0)", (ym, t.day)); A.db().commit(); K._memo.clear()
assert "Равшан ака 841" in c.get(f"/kassa?ym={ym}&d={t.day}").get_data(as_text=True)
assert "Равшан ака 841" in c.get(f"/kassa/month?ym={ym}").get_data(as_text=True)
print("KASSA DRIVER OK")
