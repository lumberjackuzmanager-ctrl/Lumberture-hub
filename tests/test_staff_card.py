"""Справочники → Сотрудники: қабул, карточка, бот рақами, доступ, бўшатиш/қайтариш, логин, боғланмаган рақамлар."""
import os, sys, tempfile
from datetime import date
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, kassa as K
with A.app.app_context():                       # тестларда «cashier» — аввалгидек кенг роль (созламалардан ташқари ҳаммаси, проведен қилади)
    A.db().execute("UPDATE roles SET kind='full', sections='[]', can_post=1 WHERE key='cashier'"); A.db().commit()
    import roles as _roles_t; _roles_t.reload()
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
def post(u, d, **kw):
    tok(); return c.post(u, data=dict(d, csrf="t"), **kw)
tok(); assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
YM = K.ym_today()

assert c.get("/staff").status_code == 200 and c.get("/staff/new").status_code == 200
# Ф.И.О.сиз қабул қилинмайди
r = post("/staff/new", {"name": "Улмас", "fio": "", "salary": "3000000"}); assert "Ф.И.О." in r.get_data(as_text=True)
r = post("/staff/new", {"name": "Улмас", "fio": "Каримов Улмас", "position": "Кромка", "phone": "+998 90 111 22 33", "hired": "2026-09-15",
                        "salary": "3 000 000", "cur": "UZS"})
assert r.status_code == 302
with A.app.app_context():
    w = A.q("SELECT * FROM k_workers WHERE name='Улмас'", one=True)
    wid = w["id"]
    assert w["position"] == "Кромка" and w["hired"] == "2026-09-15" and w["salary"] == 3000000 and w["active"] == 1
    assert A.q("SELECT 1 FROM k_ws WHERE ym=? AND worker_id=?", (YM, wid), one=True), "табелга тушмади"
# бир хил исм — хато
r = post("/staff/new", {"name": "Улмас", "fio": "Бошқа Одам"}); assert "уже есть" in r.get_data(as_text=True)
h = c.get(f"/staff/{wid}").get_data(as_text=True)
assert "Каримов Улмас" in h and "Кромка" in h and "Добавить номер" in h and "Изменить" in h
h = c.get("/staff").get_data(as_text=True); assert "Улмас" in h and f"/staff/{wid}" in h
# таҳрирлаш
r = post(f"/staff/{wid}/edit", {"name": "Улмас", "fio": "Каримов Улмас Ботирович", "position": "Кромка", "salary": "3500000", "cur": "UZS"}); assert r.status_code == 302
with A.app.app_context():
    assert A.q("SELECT fio FROM k_workers WHERE id=?", (wid,), one=True)["fio"] == "Каримов Улмас Ботирович"
    assert A.q("SELECT salary FROM k_ws WHERE ym=? AND worker_id=?", (YM, wid), one=True)["salary"] == 3500000
# бот рақами, роль, доступ, крой
assert post(f"/staff/{wid}", {"action": "addphone", "phone": "90 111 22 33", "preset": "operator"}).status_code == 302
with A.app.app_context():
    p = A.q("SELECT * FROM ready_workers WHERE kw_id=?", (wid,), one=True)
    assert p["phone"] == "998901112233" and p["role"] == "raspil" and p["active"] == 1
rid = str(p["id"])
post(f"/staff/{wid}", {"action": "perms", "rid": rid, "p_kromka_op": "1"})
post(f"/staff/{wid}", {"action": "kroy", "rid": rid})
h = c.get(f"/staff/{wid}").get_data(as_text=True); assert "Кем работает?" in h and "Кройщик (менеджер по крою)" in h and "Станочник" in h
# тайёр вариант: кройчи — staff + kroy; бошлиқ — raspil + boss
post(f"/staff/{wid}", {"action": "addphone", "phone": "+998901119999", "preset": "kroy"})
with A.app.app_context():
    q_ = A.q("SELECT * FROM ready_workers WHERE phone='998901119999'", one=True)
    assert q_["role"] == "staff" and q_["kroy"] == 1 and q_["kw_id"] == wid
post(f"/staff/{wid}", {"action": "preset", "rid": str(q_["id"]), "preset": "boss"})
with A.app.app_context():
    q_ = A.q("SELECT * FROM ready_workers WHERE phone='998901119999'", one=True)
    assert q_["role"] == "raspil" and q_["boss"] == 1 and q_["kroy"] == 0
post(f"/staff/{wid}", {"action": "delphone", "rid": str(q_["id"])})
with A.app.app_context():
    p = A.q("SELECT * FROM ready_workers WHERE id=?", (rid,), one=True)
    assert p["perms"] == '["kromka:op"]' and p["kroy"] == 1
# бошқа ишчининг рақамини ўзгартириб бўлмайди
post("/staff/new", {"name": "Нодир", "fio": "Нодиров Нодир"})
with A.app.app_context():
    wid2 = A.q("SELECT id FROM k_workers WHERE name='Нодир'", one=True)["id"]
post(f"/staff/{wid2}", {"action": "delphone", "rid": rid})
r = post(f"/staff/{wid2}", {"action": "addphone", "phone": "+998901112233", "preset": "staff"}, follow_redirects=True)
assert "другого сотрудника" in r.get_data(as_text=True)
with A.app.app_context():
    assert A.q("SELECT kw_id FROM ready_workers WHERE id=?", (rid,), one=True)["kw_id"] == wid
# логин боғлаш
with A.app.app_context():
    db = A.db()
    uid = db.execute("INSERT INTO users(username,password_hash,full_name,role) VALUES('ulmas','x','Улмас','cashier')").lastrowid
    db.commit()
post(f"/staff/{wid}", {"action": "login", "user_id": str(uid)})
r = post(f"/staff/{wid2}", {"action": "login", "user_id": str(uid)}, follow_redirects=True); assert "другому сотруднику" in r.get_data(as_text=True)
assert "ulmas" in c.get(f"/staff/{wid}").get_data(as_text=True)
# бўшатиш: бот ва логин ўчади, кейинги ой табелидан чиқади
with A.app.app_context():
    db = A.db()
    db.execute("INSERT OR IGNORE INTO k_ws(ym,worker_id,sort,salary) VALUES('2099-01',?,10,0)", (wid,))
    db.commit()
today = date.today().isoformat()
assert post(f"/staff/{wid}", {"action": "fire", "fired": today}).status_code == 302
with A.app.app_context():
    w = A.q("SELECT * FROM k_workers WHERE id=?", (wid,), one=True)
    assert w["fired"] == today and w["active"] == 0
    assert A.q("SELECT active FROM ready_workers WHERE id=?", (rid,), one=True)["active"] == 0
    assert A.q("SELECT active FROM users WHERE id=?", (uid,), one=True)["active"] == 0
    assert not A.q("SELECT 1 FROM k_ws WHERE ym='2099-01' AND worker_id=?", (wid,), one=True)
    assert A.q("SELECT 1 FROM k_ws WHERE ym=? AND worker_id=?", (YM, wid), one=True)
assert "Улмас" not in c.get("/staff").get_data(as_text=True).split("<tbody>")[1].split("</tbody>")[0]
assert "Улмас" in c.get("/staff?show=fired").get_data(as_text=True)
post(f"/staff/{wid}", {"action": "unfire"})
with A.app.app_context():
    assert A.q("SELECT fired FROM k_workers WHERE id=?", (wid,), one=True)["fired"] is None
    assert A.q("SELECT active FROM ready_workers WHERE id=?", (rid,), one=True)["active"] == 1
# боғланмаган рақам («Буюртма тайёр»дан қўшилган) — рўйхатда кўринади ва ишчига боғланади
with A.app.app_context():
    db = A.db()
    lid = db.execute("INSERT INTO ready_workers(name,phone,created_at) VALUES('Дилмурод присадка','998935556677','2026-10-01')").lastrowid
    db.commit()
assert "998935556677" in c.get("/admin/connections").get_data(as_text=True) and "/admin/connections#contacts" in c.get("/staff").get_data(as_text=True)
post("/staff/loose", {"action": "link", "id": str(lid), "kw": str(wid2)})
with A.app.app_context():
    assert A.q("SELECT kw_id FROM ready_workers WHERE id=?", (lid,), one=True)["kw_id"] == wid2
assert "/admin/connections#contacts" not in c.get("/staff").get_data(as_text=True)
# табель ва «Статьи кассы» карточкага олиб боради
h = c.get("/kassa/staff").get_data(as_text=True); assert f"/staff/{wid}" in h and "/staff/new" in h and 'value="add"' not in h
# админ бўлмаган — кўради, ўзгартирмайди
with A.app.app_context():
    from werkzeug.security import generate_password_hash
    db = A.db()
    db.execute("INSERT INTO users(username,password_hash,full_name,role,weak_pw) VALUES('kas',?,'Кассир','cashier',0)", (generate_password_hash("Kassir12345"),))
    db.commit()
c2 = A.app.test_client()
with c2.session_transaction() as s: s["csrf"] = "t"
assert c2.post("/login", data={"username": "kas", "password": "Kassir12345", "csrf": "t"}).status_code == 302
assert c2.get(f"/staff/{wid}").status_code == 200
with c2.session_transaction() as s: s["csrf"] = "t"
assert c2.post(f"/staff/{wid}", data={"csrf": "t", "action": "fire"}).status_code == 403
assert c2.get("/staff/new").status_code in (302, 403)
print("STAFF CARD OK")
# Базис конструктор: тайёр вариант ва 1С папкасини боғлаш
import raspil as P, json as _j
P.basis_designers = lambda: [("F1", "Абдулахад Базис"), ("F2", "Сайдулла Базис")]
P.basis_sync = lambda ym: 0
post(f"/staff/{wid2}", {"action": "addphone", "phone": "+998901117777", "preset": "basis"})
post(f"/staff/{wid2}", {"action": "basis", "fid": "F2"})
with A.app.app_context():
    assert _j.loads(A.get_setting("basis_map"))["F2"] == wid2
    q_ = A.q("SELECT * FROM ready_workers WHERE phone='998901117777'", one=True); assert q_["role"] == "staff"
h = c.get(f"/staff/{wid2}").get_data(as_text=True)
assert "Сайдулла Базис" in h and "— <b>Конструктор (Базис)</b>" in h, h[-3000:]
print("BASIS CARD OK")
