"""Ишчилар боти «💰 Ойлигим»: олган пуллари (касса ойлик/аванс, нақд ва картадан) санаси билан, ўтган ойдан қолгани ва «Қолди» (эгаси, 24.10)."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil as P, raspil_bot as B, botlang as BL, tg, kassa

sent = []
tg.call = lambda token, method, params=None, timeout=15: sent.append((method, params or {})) or ({"message_id": 1} if method == "sendMessage" else {"username": "b"} if method == "getMe" else True)
last = lambda: [p for m, p in sent if m == "sendMessage"][-1]
msg = lambda t, uid: {"message_id": 2, "chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": t}
ym = A.now()[:7]
with A.app.app_context():
    B.connect("123456:" + "A" * 30)
    A.set_settings(auto_from=ym)
    db = A.db()
    k1 = db.execute("INSERT INTO k_workers(name, salary, cur, position) VALUES('Нодирбек', 5000000, 'UZS', 'Менеджер')").lastrowid
    k2 = db.execute("INSERT INTO k_workers(name, salary, cur, position) VALUES('Улмас', 500, 'USD', 'Оператор')").lastrowid
    k3 = db.execute("INSERT INTO k_workers(name, salary, cur, position) VALUES('Дилмурод', 3000000, 'UZS', 'Оператор')").lastrowid
    for uid, kw, nm in ((811, k1, "Нодир"), (812, k2, "Улмас"), (813, k3, "Дилмурод")):
        db.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active, kw_id) VALUES(?,?,?, 'staff', ?, 1, ?)", (nm, str(uid), A.now(), uid, kw))
    for kw, sal in ((k1, 5000000), (k2, 500), (k3, 3000000)):
        db.execute("INSERT INTO k_ws(ym, worker_id, salary, ts) VALUES(?,?,?,?)", (ym, kw, sal, "9|9|9|9"))
    db.execute("INSERT INTO k_days(ym, day, rate) VALUES(?,?,?)", (ym, 7, 12000))
    db.execute("INSERT INTO k_cells(ym, day, kind, ref_id, uzs, usd, note_uzs) VALUES(?,?,?,?,?,?,?)", (ym, 5, "wage", k1, 300000, 0, "аванс, дори учун"))
    db.execute("INSERT INTO k_cells(ym, day, kind, ref_id, uzs, usd) VALUES(?,?,?,?,?,?)", (ym, 7, "wage", k1, 0, 10))
    db.execute("INSERT INTO k_noncash(ym, day, acc_id, kind, ref_id, uzs, usd, note) VALUES(?,?,?,?,?,?,?,?)", (ym, 8, 1, "wage", k1, 200000, 0, "Click"))
    db.execute("INSERT INTO k_cells(ym, day, kind, ref_id, uzs, usd) VALUES(?,?,?,?,?,?)", (ym, 7, "wage", k2, 0, 50))
    db.execute("INSERT INTO k_cells(ym, day, kind, ref_id, uzs, usd) VALUES(?,?,?,?,?,?)", (ym, 7, "wage", k3, 900000, 0))   # ортиқча олган
    db.commit()
    kassa._memo.clear()
    row = next(x for x in kassa.month_calc(ym)["staff"] if x["worker_id"] == k1)
    B.on_message(msg(B.B_SAL, 811))
    t = last()["text"]
    assert "Жами ҳисобланган: 714 286 сўм" in t, t
    assert "➖ Олганингиз: 620 000 сўм" in t and "• 05." in t and "300 000 сўм" in t and "10,00 $" in t and "• 08." in t and "200 000 сўм" in t, t
    assert f"✅ Қолди: {B._som(row['closing'])}" in t and "94 286 сўм" in t, t
    assert t.index("• 05.") < t.index("• 07.") < t.index("• 08."), t
    assert "300 000 сўм — аванс, дори учун" in t and "200 000 сўм (карта) — Click" in t, t
    B.on_message(msg(B.B_SAL, 812))                       # $ ойлик
    t = last()["text"]
    assert "Олганингиз: 50,00 $" in t and "Қолди: 21,43 $" in t, t
    B.on_message(msg(B.B_SAL, 813))                       # ортиқча олган
    t = last()["text"]
    assert "⚠️ Ортиқча олинган: 471 429 сўм" in t, t
    # бошқанинг пули кўринмайди
    assert "300 000" not in t and "10,00 $" not in t, t
    # табелда «Жами» қўлда (0, Excel'дан) — ботда тахминий ҳисоб ва «Қолди: тахминан»
    db.execute("UPDATE k_ws SET total_override=0 WHERE ym=? AND worker_id=?", (ym, k1)); db.commit(); kassa._memo.clear()
    B.on_message(msg(B.B_SAL, 811))
    t = last()["text"]
    assert "Тахминий ҳисоб: 714 286 сўм" in t and "Жами ҳисобланган" not in t and "Қолди: тахминан 94 286 сўм" in t and "тест режимида: бу аниқ рақамлар эмас" in t, t
    BL.set_lang("work", 811, "ru")
    B.on_message(msg(B.B_SAL, 811))
    t = last()["text"]
    assert "Вы получили" in t and "Осталось" in t and "не точные цифры" in t, t
print("WAGE PAID OK")
