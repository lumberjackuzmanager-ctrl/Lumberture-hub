"""«💳 Payme / Click» — ишчилар ботида ҳамма ролларда: Payme ва Click QR карточкалари (static/pay) + ҳаволалар (эгаси, 23.10)."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil as P, raspil_bot as B, botlang as BL, tg

sent, photos = [], []
tg.call = lambda token, method, params=None, timeout=15: sent.append((method, params or {})) or ({"message_id": 1} if method == "sendMessage" else {"username": "b"} if method == "getMe" else True)
class R:
    def __init__(self, ok=True): self.ok = ok
    def json(self): return {"ok": True, "result": {"message_id": 5}}
def fake_post(url, timeout=None, data=None, files=None):
    photos.append((url.rsplit("/", 1)[-1], data, [f[0] for f in files.values()]))
    return R()
B.requests.post = fake_post
btns = lambda p: [b["text"] for row in (p.get("reply_markup") or {}).get("keyboard", []) for b in row]
msgs = lambda: [p for m, p in sent if m == "sendMessage"]
msg = lambda t, uid: {"message_id": 2, "chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": t}
assert os.path.exists(os.path.join(os.path.dirname(os.path.abspath(B.__file__)), "static", "pay", "payme.png"))
assert os.path.exists(os.path.join(os.path.dirname(os.path.abspath(B.__file__)), "static", "pay", "click.png"))
with A.app.app_context():
    B.connect("123456:" + "A" * 30)
    db = A.db()
    kc = db.execute("INSERT INTO k_workers(name, salary, cur, position) VALUES('Абдулахад', 0, 'USD', 'Конструктор (Базис)')").lastrowid
    cols = {r[1] for r in db.execute("PRAGMA table_info(ready_workers)")}
    roles = {901: dict(role="raspil"), 902: dict(role="staff"), 903: dict(role="staff", kw_id=kc), 904: dict(role="mgr", mgr=1),
             905: dict(role="owner", owner=1), 906: dict(role="staff", driver=1), 907: dict(role="staff", kroy=1)}
    for uid, extra in roles.items():
        extra = {k: v for k, v in extra.items() if k in cols or k == "role"}
        keys = ["name", "phone", "created_at", "bot_uid", "active"] + list(extra)
        db.execute(f"INSERT INTO ready_workers({','.join(keys)}) VALUES({','.join('?' * len(keys))})",
                   [f"W{uid}", str(uid), A.now(), uid, 1] + list(extra.values()))
    db.commit()
    for uid in roles:
        sent.clear(); photos.clear()
        B.on_message(msg("/start", uid))
        assert B.B_PAY in btns(msgs()[-1]), (uid, btns(msgs()[-1]))
        sent.clear()
        B.on_message(msg(B.B_PAY, uid))
        assert [p[0] for p in photos] == ["sendMediaGroup"] and photos[0][2] == ["payme.png", "click.png"], (uid, photos)
        import json as _j
        md = _j.loads(photos[0][1]["media"]); t = md[0]["caption"]
        assert "caption" not in md[1] and not msgs(), (md, msgs())           # изоҳ альбомнинг ичида, алоҳида хабар йўқ
        assert "transfer.paycom.uz/69e07fe5bbf621e9322a28a6" in t and "indoor.click.uz/pay?id=085563" in t and "*880*085563*" in t, t
        assert "кассага" in t, t
    # рус тилида: тугма босилса ҳам ишлайди, изоҳ таржима қилинган
    BL.set_lang("work", 902, "ru")
    sent.clear(); photos.clear()
    B.on_message(msg("💳 Payme / Click", 902))
    import json as _j
    assert len(photos) == 1 and "Деньги поступают прямо в кассу" in _j.loads(photos[0][1]["media"])[0]["caption"], photos
    # тўлов келди → QR кўрсатган ишчининг ботига
    import epay
    from datetime import datetime, timedelta
    assert B.on_epay in epay.NEW_HOOKS
    db.execute("UPDATE raspil_payreq SET done=1"); db.commit()
    sent.clear(); photos.clear()
    B.on_message(msg(B.B_PAY, 901))
    assert any(f(db) for f in epay.FAST)                     # кутаётган бор — 2 дақиқада ўқилади
    now = datetime.now()
    def pay(ext, amt, at, src="payme", status="ok"):
        epay.store(db, dict(src=src, ext_id=ext, amount=amt, at=at.strftime("%Y-%m-%d %H:%M:%S"), who="", receipt="", status=status))
        return db.execute("SELECT * FROM erp_epay WHERE src=? AND ext_id=?", (src, ext)).fetchone()
    old = pay("e0", 99000, now - timedelta(minutes=40))      # QR'дан олдинги тўлов — ҳеч кимга эмас
    sent.clear(); B.on_epay(db, [old])
    assert not msgs(), msgs()
    r1 = pay("e1", 150000, now + timedelta(minutes=1))
    sent.clear(); B.on_epay(db, [r1])
    m = msgs()
    assert len(m) == 1 and m[0]["chat_id"] == 901 and "Payme: 150 000 сўм" in m[0]["text"] and "Мижоз тўлади" in m[0]["text"], m
    assert not any(f(db) for f in epay.FAST)                 # битта ишчи эди — сўров ёпилди
    r2 = pay("e2", 50000, now + timedelta(minutes=2), "click")
    sent.clear(); B.on_epay(db, [r2]); assert not msgs()     # кейинги тўлов — ёпилган сўровга бормайди
    # иккита ишчи бир вақтда QR кўрсатди → иккаласига (рус тилидагига таржима)
    B.on_message(msg(B.B_PAY, 901)); B.on_message(msg(B.B_PAY, 902))
    r3 = pay("e3", 70000, datetime.now(), "click")
    sent.clear(); B.on_epay(db, [r3])
    m = msgs()
    assert sorted(x["chat_id"] for x in m) == [901, 902], m
    assert any("Клиент оплатил" in x["text"] for x in m if x["chat_id"] == 902), m
    r4 = pay("e4", 70000, datetime.now(), "click", "cancel")
    sent.clear(); B.on_epay(db, [r4]); assert not msgs()     # бекор қилинган — хабар йўқ
    # «🏠 Асосий меню»: ҳар инлайн клавиатурада; босилса ёзиш кутилган ҳолат бекор ва асосий тугмалар
    assert B.ikb([[("a", "x")]])["inline_keyboard"][-1][0]["callback_data"] == "home" and B.ikb([]) == {"inline_keyboard": []}
    assert B.ikb([[("a", "x")]], home=False) == {"inline_keyboard": [[{"text": "a", "callback_data": "x"}]]}
    rid = db.execute("SELECT id FROM ready_workers WHERE bot_uid=901").fetchone()[0]
    pid = db.execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state) VALUES(?,?,?,?)", (rid, 901, A.now(), "wcash0")).lastrowid
    job = db.execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state) VALUES(?,?,?,?)", (rid, 901, A.now(), "cutting")).lastrowid
    db.commit(); sent.clear()
    B.on_callback({"id": "1", "from": {"id": 901}, "data": "home", "message": {"chat": {"id": 901}, "message_id": 7}})
    st = dict(db.execute("SELECT id, state FROM raspil_pending WHERE id IN (?,?)", (pid, job)).fetchall())
    assert st[pid] == "cancel" and st[job] == "cutting", st
    m = msgs()[-1]
    assert m["text"].startswith(B.B_HOME) and B.B_PAY in btns(m), m
    assert any(mm == "editMessageReplyMarkup" for mm, _ in sent), sent
    sent.clear(); B.on_message(msg("/menu", 901))
    assert msgs()[-1]["text"].startswith(B.B_HOME)
print("PAY QR OK")
