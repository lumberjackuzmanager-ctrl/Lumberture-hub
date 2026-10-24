"""3D бўлими: /start web_<код> → рақам → мижоз базада («3D сайт») → GET /auth/<код> токен (бир марта) → .b3d сақлаш, /stats, /models."""
import os, sys, tempfile, io, secrets
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, web3d as W, tg

bot = []
tg.send_message = lambda token, chat, text, markup=None: bot.append((chat, text, markup)) or {"message_id": 1}
CF = {"Cf-Ray": "abc-DME", "Cf-Connecting-Ip": "203.0.113.9", "Host": "app.lumberture.com", "Origin": "https://lumberture.com"}
c = A.app.test_client()
with A.app.app_context():
    A.set_settings(tg_token="1:x", bot_enabled="1", tg_bot_username="Lumberture_bot")
    con = A.db()
    con.execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc, lim, ckind, src) VALUES('C7','Азизбек мебель','+998 90 111-22-33',0,0,' азизбек ',0,'client','1c')")
    con.commit()

r = c.get("/api/online/auth/ping", headers=CF)
assert r.status_code == 200 and r.get_json()["ok"] == 1 and r.headers.get("Access-Control-Allow-Origin") == "https://lumberture.com"
code = secrets.token_hex(16)
assert c.get(f"/api/online/auth/{code}", headers=CF).get_json() == {}
assert c.get("/api/online/auth/NOT-HEX", headers=CF).get_json() == {}
U = lambda cid, **m: {"message": dict({"chat": {"id": cid, "type": "private"}, "from": {"id": cid, "first_name": "Ботир", "username": "botir_m"}}, **m)}
with A.app.app_context():                          # бот поллери — сўровсиз (21:02 хатоси)
    A.handle_update(U(8181, text="/start web_" + code))
    assert "request_contact" in bot[-1][2] and bot[-1][1] == W.ASK_3D and "ўзимизнинг серверда" in bot[-1][1], bot[-1]   # огоҳлантириш
    A.handle_update(U(8181, contact={"phone_number": "998935550011", "user_id": 999}))           # бегона контакт
    assert "Ўз рақамингизни" in bot[-1][1]
    assert c.get(f"/api/online/auth/{code}", headers=CF).get_json() == {}
    A.handle_update(U(8181, contact={"phone_number": "+998 93 555 00 11", "user_id": 8181}))
    assert "Рўйхатдан ўтдингиз" in bot[-1][1], bot[-1]
    cl = A.db().execute("SELECT * FROM erp_clients WHERE phones LIKE '%555-00-11%'").fetchone()
    assert cl and cl["name"] == "Ботир" and W.SRC_NOTE in (cl["cnote"] or "") and cl["tg"] == "@botir_m", dict(cl) if cl else None
r = c.get(f"/api/online/auth/{code}", headers=CF).get_json()
tok = r.get("token")
assert tok and len(tok) >= 40 and r["name"] == "Ботир" and "***" in r["phone"], r
assert c.get(f"/api/online/auth/{code}", headers=CF).get_json() == {}                              # бир марта
H = dict(CF, Authorization="Bearer " + tok)
assert c.get("/api/online/me", headers=H).get_json()["name"] == "Ботир"
# модель: сигнатура, ҳажм, токен
up = lambda data, meta, h=H: c.post("/api/online/model", headers=h, content_type="multipart/form-data",
                                     data={"file": (io.BytesIO(data), "kuxnya.b3d"), "meta": meta})
assert up(b"BZ" + b"x" * 10, '{"name":"Кухня"}', CF).status_code == 401
assert up(b"PK\x03\x04", '{"name":"Кухня"}').get_json()["error"] == "bad_file"
r = up(b"BZ" + b"\x00" * 2048, '{"name":"Кухня 3м","order":"A-17","parts":42,"hw":{"petlya":8,"ruchka":6}}').get_json()
assert r["ok"] and r["id"], r
with A.app.app_context():
    m = A.db().execute("SELECT * FROM online_models WHERE id=?", (r["id"],)).fetchone()
    assert m["parts"] == 42 and m["hw"] == 14 and m["ord"] == "A-17" and m["client"] == cl["ext"] and os.path.isfile(m["path"])
    assert open(m["path"], "rb").read(2) == b"BZ" and m["path"].startswith(os.path.join(A.DATA_DIR, "models"))
up(b"BZ" + b"\x01" * 100, '{"name":"Шкаф","parts":8}')
assert c.get("/api/online/stats", headers=CF).get_json() == {"projects": 2, "parts": 50}
ms = c.get("/api/online/models", headers=H).get_json()["models"]
assert [x["name"] for x in ms] == ["Шкаф", "Кухня 3м"] and "path" not in ms[0]
assert c.get("/api/online/models", headers=CF).status_code == 401
# аввал рақами тасдиқланган чат — рақам қайта сўралмайди; 1С мижози — янги мижоз очилмайди
code2 = secrets.token_hex(16)
with A.app.app_context():                          # бот поллери — сўровсиз (21:02 хатоси)
    A.handle_update(U(8181, text="/start web_" + code2))
    assert "Рўйхатдан ўтдингиз" in bot[-1][1]
    code3 = secrets.token_hex(16)
    A.handle_update(U(7171, text="/start web_" + code3))
    A.handle_update(U(7171, contact={"phone_number": "998901112233", "user_id": 7171}))
assert c.get(f"/api/online/auth/{code2}", headers=CF).get_json().get("token")
assert c.get(f"/api/online/auth/{code3}", headers=CF).get_json()["name"] == "Азизбек мебель"
with A.app.app_context():
    assert A.db().execute("SELECT COUNT(*) FROM erp_clients WHERE phones LIKE '%111-22-33%'").fetchone()[0] == 1
# ташқаридан офис саҳифаси йўқ; ичкаридан (админ) — рўйхат ва файл
assert c.get("/erp/online/models", headers=CF).status_code == 404
c2 = A.app.test_client()
with c2.session_transaction() as s: s["csrf"] = "t"
assert c2.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
h = c2.get("/erp/online/models").get_data(as_text=True)
assert "Кухня 3м" in h and "Ботир" in h and "Проектов" in h
f = c2.get(f"/erp/online/models/{r['id']}/file")
assert f.status_code == 200 and f.data[:2] == b"BZ" and ".b3d" in f.headers.get("Content-Disposition", "")
print("WEB3D OK")
