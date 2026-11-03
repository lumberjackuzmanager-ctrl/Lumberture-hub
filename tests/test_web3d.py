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

# ---------- мижозга ҳавола (03.11): POST /model -> view; /share; /view/<токен> Bearer'сиз; бекор қилиш
import json as _j, time as _t
from urllib.parse import unquote
r3 = up(b"BZ" + b"\x02" * 300, '{"name":"Кухня «Ойша» 3м","parts":12}').get_json()
v = r3.get("view")
assert r3["ok"] and v and len(v) >= 32 and v != str(r3["id"]), r3
assert int(len(v) * 4) >= 128
V = lambda t, h=None: c.get(f"/api/online/view/{t}", headers=h or {"Cf-Ray": "x", "Cf-Connecting-Ip": "198.51.100.7", "Host": "app.lumberture.com",
                                                                   "Origin": "https://lumberture.com"})
g = V(v)
assert g.status_code == 200 and g.data == b"BZ" + b"\x02" * 300 and g.mimetype == "application/octet-stream", g.status_code
assert unquote(g.headers["X-Model-Name"]) == "Кухня «Ойша» 3м" and "X-Model-Name" in g.headers["Access-Control-Expose-Headers"]
assert g.headers.get("Access-Control-Allow-Origin") == "https://lumberture.com" and "private" in g.headers.get("Cache-Control", "")
assert "998" not in str(g.headers) and b"998" not in g.data
assert V(v, {"Cf-Ray": "x", "Host": "app.lumberture.com", "Origin": "https://evil.example"}).headers.get("Access-Control-Allow-Origin") is None
assert V("0" * 40).status_code == 404 and V("not-a-token").status_code == 404 and V(v[:-1] + ("0" if v[-1] != "0" else "1")).status_code == 404
nf = V("0" * 40)
assert nf.get_json() == {"ok": False, "error": "not_found", "message": nf.get_json()["message"]}
# share: эгаси — ўша токен; бегона — 404; Bearer'сиз — 401
s1 = c.post(f"/api/online/model/{r3['id']}/share", headers=H).get_json()
assert s1 == {"ok": True, "view": v}, s1
assert c.post(f"/api/online/model/{r3['id']}/share", headers=CF).status_code == 401
code4 = secrets.token_hex(16)
with A.app.app_context():
    A.handle_update(U(6161, text="/start web_" + code4))
    A.handle_update(U(6161, contact={"phone_number": "998977770000", "user_id": 6161}))
tok4 = c.get(f"/api/online/auth/{code4}", headers=CF).get_json()["token"]
H4 = dict(CF, Authorization="Bearer " + tok4)
assert c.post(f"/api/online/model/{r3['id']}/share", headers=H4).status_code == 404
assert c.delete(f"/api/online/model/{r3['id']}/share", headers=H4).status_code == 404
# ходим (цех боти рўйхатида) — бошқанинг проектига ҳам
with A.app.app_context():
    A.db().execute("INSERT INTO ready_workers(name, phone, active) VALUES('Базисчи', '+998 97 777-00-00', 1)"); A.db().commit()
assert c.post(f"/api/online/model/{r3['id']}/share", headers=H4).get_json()["view"] == v
# /models — view майдони
ms = {x["id"]: x for x in c.get("/api/online/models", headers=H).get_json()["models"]}
assert ms[r3["id"]]["view"] == v and ms[r["id"]].get("view")
# CORS preflight DELETE
pf = c.open(f"/api/online/model/{r3['id']}/share", method="OPTIONS", headers=dict(CF, **{"Access-Control-Request-Method": "DELETE"}))
assert pf.status_code == 204 and "DELETE" in pf.headers.get("Access-Control-Allow-Methods", "")
# бекор қилиш -> 404; янги share -> янги токен
d = c.delete(f"/api/online/model/{r3['id']}/share", headers=H).get_json()
assert d["ok"] and d["revoked"] == 1 and V(v).status_code == 404
v2 = c.post(f"/api/online/model/{r3['id']}/share", headers=H).get_json()["view"]
assert v2 and v2 != v and V(v2).status_code == 200
# муддати ўтган -> 404
with A.app.app_context():
    A.db().execute("UPDATE online_model_views SET exp=? WHERE tok=?", (_t.time() - 1, v2)); A.db().commit()
assert V(v2).status_code == 404
assert "view" not in {x["id"]: x for x in c.get("/api/online/models", headers=H).get_json()["models"]}[r3["id"]]
# ўчирилган проект -> 404
v3 = c.post(f"/api/online/model/{r['id']}/share", headers=H).get_json()["view"]
assert V(v3).status_code == 200
with A.app.app_context():
    A.db().execute("UPDATE online_models SET deleted=1 WHERE id=?", (r["id"],)); A.db().commit()
assert V(v3).status_code == 404
with A.app.app_context():
    A.db().execute("UPDATE online_models SET deleted=0 WHERE id=?", (r["id"],)); A.db().commit()
# офис: ҳавола ва «Отозвать»
h = c2.get("/erp/online/models").get_data(as_text=True)
assert "lumberture.com/m/?v=" + v3 in h and "Отозвать" in h and f"/erp/online/models/{r['id']}/unshare" in h
with c2.session_transaction() as s: s["csrf"] = "t"
assert c2.post(f"/erp/online/models/{r['id']}/unshare", data={"csrf": "t"}).status_code == 302
assert V(v3).status_code == 404
assert c.post(f"/erp/online/models/{r['id']}/unshare", headers=CF).status_code == 404      # ташқаридан йўқ
# rate limit: 60/дақиқа/IP
codes = [V("0" * 40, {"Cf-Ray": "x", "Cf-Connecting-Ip": "192.0.2.55", "Host": "app.lumberture.com"}).status_code for _ in range(65)]
assert codes.count(429) >= 4 and codes[0] == 404, codes[-6:]

# ---------- 3D маълумот (JSON) — мижозга .b3d эмас, фақат кўриш (03.11)
import gzip as _gz
LB = {"panels": [{"name": "Бок", "w": 560, "h": 720, "t": 16, "mat": "ЛДСП Белый"}], "meta": {"name": "Шкаф-купе", "phone": "998901112233"},
      "raw": "QlpYWFg="}
r5 = c.post("/api/online/model", headers=H, content_type="multipart/form-data",
            data={"file": (io.BytesIO(b"BZ" + b"\x05" * 500), "kupe.b3d"), "meta": '{"name":"Шкаф-купе","parts":1}',
                  "lbm": (io.BytesIO(_j.dumps(LB).encode()), "m.lbm.json", "application/json")}).get_json()
assert r5["ok"] and r5["lbm"] is True and r5["view"], r5
g = V(r5["view"])
assert g.status_code == 200 and g.mimetype == "application/json" and not g.data.startswith(b"BZ"), (g.status_code, g.mimetype)
jj = _j.loads(g.data)
assert jj["panels"][0]["name"] == "Бок" and "raw" not in jj and "phone" not in jj["meta"], jj
assert unquote(g.headers["X-Model-Name"]) == "Шкаф-купе" and "private" in g.headers["Cache-Control"]
# эски проектга кейин юбориш (gzip ҳам), нотўғри маълумот рад
v6 = c.post(f"/api/online/model/{r3['id']}/share", headers=H).get_json()["view"]
assert V(v6).mimetype == "application/octet-stream"                                # 3D маълумот ҳали йўқ — .b3d (вақтинча)
assert c.post(f"/api/online/model/{r3['id']}/lbm", headers=H4, content_type="application/json", data=_j.dumps(LB)).status_code == 200  # ходим
assert c.post(f"/api/online/model/{r3['id']}/lbm", headers=CF, content_type="application/json", data=_j.dumps(LB)).status_code == 401
bad = c.post(f"/api/online/model/{r3['id']}/lbm", headers=H, content_type="application/json", data=b"BZ\x00\x00")
assert bad.status_code == 400 and bad.get_json()["error"] == "bad_lbm"
assert c.post(f"/api/online/model/{r3['id']}/lbm", headers=H, content_type="application/json", data='{"x":1}').status_code == 400
LB2 = dict(LB, panels=LB["panels"] * 3)
ok = c.post(f"/api/online/model/{r3['id']}/lbm", headers=H, content_type="application/gzip", data=_gz.compress(_j.dumps(LB2).encode()))
assert ok.status_code == 200, ok.get_json()
g = V(v6)
assert g.mimetype == "application/json" and len(_j.loads(g.data)["panels"]) == 3
ms = {x["id"]: x for x in c.get("/api/online/models", headers=H).get_json()["models"]}
assert ms[r3["id"]]["lbm"] is True and ms[r["id"]]["lbm"] is False
# .b3d берилмасин (созлама): 3D маълумоти йўқ проект ҳаволаси — 404
v7 = c.post(f"/api/online/model/{r['id']}/share", headers=H).get_json()["view"]
assert V(v7).status_code == 200
with A.app.app_context():
    A.set_settings(web3d_view_raw="0")
assert V(v7).status_code == 404 and V(v6).status_code == 200
with A.app.app_context():
    A.set_settings(web3d_view_raw="1")
print("WEB3D OK")
