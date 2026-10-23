"""Базис конструктори (лавозими «Конструктор»): ботда «Ишга келдим / Иш тугади / Бугун» йўқ; ойликда табел куни ва қўлда ёзилган жами."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil as P, raspil_bot as B, botlang as BL, tg, kassa

sent = []
tg.call = lambda token, method, params=None, timeout=15: sent.append((method, params or {})) or ({"message_id": 1} if method == "sendMessage" else {"username": "b"} if method == "getMe" else True)
btns = lambda p: [b["text"] for row in (p.get("reply_markup") or {}).get("keyboard", []) for b in row]
last = lambda: [p for m, p in sent if m == "sendMessage"][-1]
msg = lambda t, uid: {"message_id": 2, "chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": t}
ym = A.now()[:7]
with A.app.app_context():
    B.connect("123456:" + "A" * 30)
    A.set_settings(auto_from=ym)                         # платформада бу ой ойлик автомат (жонли базада ҳам шундай)
    db = A.db()
    k1 = db.execute("INSERT INTO k_workers(name, salary, cur, position) VALUES('Абдулахад', 0, 'USD', 'Конструктор (Базис)')").lastrowid
    k2 = db.execute("INSERT INTO k_workers(name, salary, cur, position) VALUES('Нодирбек', 5000000, 'UZS', 'Менеджер')").lastrowid
    db.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active, kw_id) VALUES('Абдулахад','1',?, 'staff', 801, 1, ?)", (A.now(), k1))
    db.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active, kw_id) VALUES('Нодир','2',?, 'staff', 802, 1, ?)", (A.now(), k2))
    db.execute("INSERT INTO k_ws(ym, worker_id, salary, ts, total_override) VALUES(?,?,?,?,?)", (ym, k2, 5000000, "9|9|9|9", 1214285.71))
    db.commit()
    B.on_message(msg("/start", 801))
    b = btns(last())
    assert B.B_IN not in b and B.B_END not in b and B.B_TODAY not in b and B.B_KUS in b and B.B_SAL in b, b
    assert "Ишга келдим" not in last()["text"], last()["text"]
    B.on_message(msg("/start", 802))                      # оддий staff — ўзгармади
    assert B.B_IN in btns(last()) and B.B_END in btns(last())
    # ойлик: табел куни; «Жами» қўлда ёзилган — «Оклад» чиқмайди
    B.on_message(msg(B.B_SAL, 802))
    t = last()["text"]
    assert "Табел: 4 кун" in t and "Оклад" not in t and "1 214 286 сўм" in t, t
    db.execute("UPDATE k_ws SET total_override=NULL WHERE worker_id=?", (k2,)); db.commit()
    kassa._memo.clear()
    B.on_message(msg(B.B_SAL, 802))
    t = last()["text"]
    assert "Оклад: 714 286 сўм" in t and "Жами ҳисобланган: 714 286 сўм" in t, t
    BL.set_lang("work", 801, "ru")
    B.on_message(msg("/start", 801))
    assert "Склад обрезков" in last()["text"] and "Ишга" not in last()["text"], last()["text"]
print("CONSTR OK")
