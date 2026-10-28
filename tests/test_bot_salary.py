"""Цех боти: ишчига ойлик стандартда кўрсатилмайди (28.10, эгаси: ойлигини кўриб «менга бўлади» деб дамга чиқиб кетяпти).
«Настройки цеха → Зарплата в цех-боте» белгиланса — олдингидек."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil_bot as B, tg

sent = []
tg.call = lambda token, method, params=None, timeout=15: (sent.append((method, params or {})) or {"message_id": 1})

def btns(p):
    return [b["text"] for r in (p.get("reply_markup") or {}).get("keyboard") or [] for b in r]

def say(t, uid):
    sent.clear()
    B.on_message({"message_id": 5, "chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": t})
    return [p for m, p in sent if m == "sendMessage"][-1]

with A.app.app_context():
    B.connect("123456:" + "A" * 30)
    con = A.db()
    assert A.get_setting("bot_salary", "0") == "0"
    for uid, role, extra in ((901, "raspil", ""), (902, "staff", ""), (903, "staff", "kroy")):
        con.execute(f"INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active{', ' + extra if extra else ''}) "
                    f"VALUES(?, ?, ?, ?, ?, 1{', 1' if extra else ''})", (f"Ишчи{uid}", str(uid), A.now(), role, uid))
    con.commit()
    for uid in (901, 902, 903):
        rw = dict(con.execute("SELECT * FROM ready_workers WHERE bot_uid=?", (uid,)).fetchone())
        b = btns(dict(reply_markup=B.kb_for(rw)))
        assert B.B_SAL not in b and B.B_KPI not in b and b, (uid, b)
        p = say(B.B_SAL, uid)                              # эски тугма (телефонда қолган) — ойлик йўқ
        assert p["text"] == B.SAL_OFF and "сўм" not in p["text"] and B.B_SAL not in btns(p), p
        assert say(B.B_KPI, uid)["text"] == B.SAL_OFF
        assert "Ойлигим" not in say("/start", uid)["text"]
    assert "Ойлигим" not in B._help_no_sal(B.CONSTR_HELP) and "Кусок омбори" in B._help_no_sal(B.CONSTR_HELP)
    A.set_settings(bot_salary="1")
    rw = dict(con.execute("SELECT * FROM ready_workers WHERE bot_uid=901").fetchone())
    assert B.B_SAL in btns(dict(reply_markup=B.kb_for(rw)))
    assert say(B.B_SAL, 901)["text"] != B.SAL_OFF
print("BOT SALARY OK")
