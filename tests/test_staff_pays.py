"""28.10 (эгаси): ишчи карточкасида «Выдано» тарихи — ой бўйича ҳар бир ойлик/аванс тўлови (сана, нақд/карта, сумма, изоҳ),
кунма-кун кассани очмасдан. «Зарплата по месяцам»даги «Выдано»га картадан берилгани ҳам киради (табелдагидек)."""
import os, sys, tempfile, re
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, kassa as K
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
with A.app.app_context():
    db = A.db()
    wid = db.execute("INSERT INTO k_workers(name, active, cur, salary) VALUES('Комолиддин', 1, 'UZS', 4000000)").lastrowid
    for ym in ("2026-09", "2026-10"):
        db.execute("INSERT INTO k_ws(ym, worker_id, salary) VALUES(?,?,4000000)", (ym, wid))
    db.execute("INSERT INTO k_cells(ym, day, kind, ref_id, uzs, usd, note_uzs) VALUES('2026-10', 5, 'wage', ?, 500000, 0, 'аванс')", (wid,))
    db.execute("INSERT INTO k_cells(ym, day, kind, ref_id, uzs, usd, note_usd) VALUES('2026-10', 12, 'wage', ?, 0, 50, 'долларда')", (wid,))
    db.execute("INSERT INTO k_cells(ym, day, kind, ref_id, uzs, usd) VALUES('2026-09', 30, 'wage', ?, 3000000, 0)", (wid,))
    db.execute("INSERT INTO k_cells(ym, day, kind, ref_id, uzs, usd) VALUES('2026-10', 6, 'wage', 999, 777000, 0)")           # бошқа ишчи
    acc = db.execute("SELECT id FROM erp_accounts WHERE kind='card' ORDER BY id LIMIT 1").fetchone()
    aid = acc[0] if acc else db.execute("INSERT INTO erp_accounts(name, kind, sort) VALUES('Карта АСАКА 05-76', 'card', 1)").lastrowid
    an = db.execute("SELECT name FROM erp_accounts WHERE id=?", (aid,)).fetchone()[0]
    db.execute("INSERT INTO k_noncash(ym, day, acc_id, kind, ref_id, uzs, usd, note) VALUES('2026-10', 20, ?, 'wage', ?, 1000000, 0, 'картадан')", (aid, wid))
    db.commit()
h = c.get(f"/staff/{wid}").get_data(as_text=True)
i = h.find('id="pays"'); assert i > 0, h[-800:]
p = h[i:]
assert "Выдано: зарплата и аванс" in p and 'value="2026-10" selected' in p
for s in ("05.10.2026", "500 000 сум", "аванс", "12.10.2026", "50", "долларда", "20.10.2026", "1 000 000 сум", "картадан", an):
    assert s in p, s
assert "777 000" not in p and "3 000 000" not in p.split("</table>")[0]
assert p.find("05.10.2026") < p.find("12.10.2026") < p.find("20.10.2026")                 # сана бўйича
assert re.search(r"Итого</b> <span class=\"muted small\">\(3\)</span></td>\s*<td class=\"r nowrap\"><b>1 500 000 сум \+ \$?50", p), p[p.find("Итого"):p.find("Итого") + 300]
assert f'href="/kassa?ym=2026-10&amp;d=20"' in p or "/kassa?d=20&amp;ym=2026-10" in p
# ой жамида картадан берилгани ҳам
m = h[h.find("Зарплата по месяцам"):i]
assert "1 500 000 сум" in m, m[:1500]
p9 = c.get(f"/staff/{wid}?pym=2026-09").get_data(as_text=True)
p9 = p9[p9.find('id="pays"'):]
assert "30.09.2026" in p9 and "3 000 000 сум" in p9 and "05.10.2026" not in p9
p8 = c.get(f"/staff/{wid}?pym=2026-08").get_data(as_text=True)
assert "В этом месяце выдач нет." in p8
assert c.get(f"/staff/{wid}?pym=xx").status_code == 200
print("STAFF PAYS OK")
