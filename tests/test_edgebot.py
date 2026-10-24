"""Кромка этикеткаси: OCR матндан каталогга мослаш, кодлар эсда қолиши, рулон/метр, бозор кромкаси, инвентаризацияга қўшилиши."""
import os, sys, tempfile, json
from datetime import date
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, edgebot as EB, raspil_bot as RB, ready, stockimp as SI

sent = []
RB.token = lambda: "x"
RB.send = lambda chat, text, kb=None, reply_to=None: (sent.append((chat, text, kb)) or {"message_id": 1})
RB.edit = lambda *a, **k: None
RB.send_typing = lambda chat: None
RB.download_try = lambda fid, dest, tries=2: (open(dest, "wb").write(b"x") or True)
OCR = {"t": ""}
ready.ocr_image = lambda path: OCR["t"]


def btns(kb):
    return [b["callback_data"] for row in (kb or {}).get("inline_keyboard", []) for b in row]


def cq(d):
    return {"data": d, "message": {"chat": {"id": 600}, "message_id": 1}, "from": {"id": 600}, "id": "1"}


assert EB.sizes_in("Кромка 19/0,8 ПВХ") == {(19, 0.8)} and EB.sizes_in("U727 9 19 x 0.80") == {(19, 0.8)} and (23, 0.8) in EB.sizes_in("F416 10 23 X 0,80")
assert "U704CA" in EB.codes_in("U704CAКромка 19/1") or "U704" in "".join(EB.codes_in("U704CA Кромка 19/1"))
assert EB.parse_qty("3 рулон", 1) == (3, 3) and EB.parse_qty("250", 1) == (250, None) and EB.parse_qty("2р", 1) == (2, 2)

with A.app.app_context():
    con = A.db()
    td = date.today().isoformat()
    con.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (td, td + " 09:00:00"))
    for ext, code, name in (("ci1", "U704 CA", "Кромка 19/1 ПВХ Кашемир U704"), ("ci2", "F416 ST10", "Кромка Эггер 23/0,8 ABS F416 Текстиль бежевый"),
                            ("ci3", "19/0.8 UZB", "Кромка ПВХ Сосна 19/0.8 (Huali Central Asia)"), ("ci4", "", "Кромка Местная 19/1"),
                            ("ci5", "U704 CA", "Кромка 35/1 ПВХ Кашемир U704")):
        con.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES(?,?,?,'м',2,0,'',0,0,?,'cat','good',1)",
                    (ext, code, name, " " + name.casefold() + " "))
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active, p_edge) VALUES('Улмас','1',?, 'raspil', 600, 1, 1)", (A.now(),))
    con.commit()
    c = EB.candidates(con, "U704CAКромка 19/1 ПВХ Кашемир 100м")
    assert c and c[0][0] == "ci1", c                                          # артикул + ўлчам: 35/1 эмас, 19/1
    c = EB.candidates(con, "F416 10 23 X 0,80")
    assert c and c[0][0] == "ci2", c                                          # Egger: декор коди + ўлчам
    c = EB.candidates(con, "Кромка ПВХ Сосна 19/08 HuUz00123")
    assert c and c[0][0] == "ci3", c                                          # Huali: сўз + ўлчам
    rw = dict(con.execute("SELECT * FROM ready_workers").fetchone())
    # бот: тугма -> расм -> таклиф -> танлаш -> рулон
    RB.on_message({"chat": {"id": 600, "type": "private"}, "from": {"id": 600}, "text": RB.B_EDGE, "message_id": 1})
    assert "этикетка расмини" in sent[-1][1]
    # кромка кутилаётганда /start — кромка қидирилмайди, бош меню (эгаси, 24.10: «Не найдено…» чиқарди)
    RB.on_message({"chat": {"id": 600, "type": "private"}, "from": {"id": 600}, "text": "/start", "message_id": 1})
    import pickup as PK_
    assert PK_.st_get(A.db(), rw["id"])[0] is None and "топилмади" not in sent[-1][1].casefold(), sent[-1]
    RB.on_message({"chat": {"id": 600, "type": "private"}, "from": {"id": 600}, "text": RB.B_EDGE, "message_id": 1})
    OCR["t"] = "HuUz00123 Кромка ПВХ Сосна 19/08"
    RB.on_message({"chat": {"id": 600, "type": "private"}, "from": {"id": 600}, "photo": [{"file_id": "F"}], "message_id": 2})
    assert "pe:g:ci3" in btns(sent[-1][2]), sent[-1]
    EB.on_cb(rw, cq("pe:g:ci3"))
    assert "Неча метр" in sent[-1][1]
    RB.on_message({"chat": {"id": 600, "type": "private"}, "from": {"id": 600}, "text": "3 рулон", "message_id": 3})
    assert "Метрини ёзинг" in sent[-1][1]
    RB.on_message({"chat": {"id": 600, "type": "private"}, "from": {"id": 600}, "text": "450", "message_id": 30})
    assert "450 м" in sent[-1][1], sent[-1]
    EB.on_cb(rw, cq("pe:g:ci3"))
    RB.on_message({"chat": {"id": 600, "type": "private"}, "from": {"id": 600}, "text": "400 м", "message_id": 32})
    assert "400 м" in sent[-1][1], sent[-1]
    # код эсда қолди: энди бошқача ёзилган этикетка ҳам танилади
    assert EB.candidates(con, "HuUz00123")[0][:1] == ("ci3",) and EB.candidates(con, "HuUz00123")[0][3]
    # бозор кромкаси: ўлчам -> бор ранг / янги ранг
    EB.on_cb(rw, cq("pe:b")); EB.on_cb(rw, cq("pe:z:19/1"))
    assert "pe:g:ci4" in btns(sent[-1][2])
    EB.on_cb(rw, cq("pe:n:19/1"))
    RB.on_message({"chat": {"id": 600, "type": "private"}, "from": {"id": 600}, "text": "кашемир", "message_id": 4})
    RB.on_message({"chat": {"id": 600, "type": "private"}, "from": {"id": 600}, "text": "120", "message_id": 5})
    assert "Янги ранг" in sent[-1][1]
    e = [dict(r) for r in con.execute("SELECT good, qty_m, rolls, roll_m, state, note FROM edge_entries ORDER BY id")]
    assert e[0]["good"] == "ci3" and e[0]["qty_m"] == 450 and e[0]["rolls"] is None and e[1]["qty_m"] == 400 and e[2]["state"] == "new" and "кашемир" in e[2]["note"], e
    EB.on_cb(rw, cq("pe:x"))
    assert "жами 970 м" in sent[-1][1], sent[-1]

# платформа: янги рангга товар танлаш ва инвентаризацияга қўшиш
SI.rows_1c = lambda: []
cl = A.app.test_client()
with cl.session_transaction() as s: s["csrf"] = "t"
cl.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
h = cl.get("/erp/stock-import").get_data(as_text=True)
assert "Кромка из цех-бота" in h and "Новые цвета" in h
with A.app.app_context():
    nid = A.db().execute("SELECT id FROM edge_entries WHERE state='new'").fetchone()[0]
with cl.session_transaction() as s: s["csrf"] = "t"
r = cl.post("/erp/stock-import", data={"csrf": "t", "act": "inv", "date": td, "edges": "1", f"eg_{nid}": "ci4"})
assert r.status_code == 302 and "/edit2" in r.headers["Location"], r.headers
with A.app.app_context():
    d = A.db().execute("SELECT id FROM erp_docs WHERE kind='inv'").fetchone()
    ls = sorted(tuple(x) for x in A.db().execute("SELECT good, qty FROM erp_lines WHERE doc_id=?", (d[0],)))
    assert ls == [("ci3", 850.0), ("ci4", 120.0)], ls
    assert A.db().execute("SELECT COUNT(*) FROM edge_entries WHERE used_doc IS NULL").fetchone()[0] == 0
print("EDGEBOT OK")
