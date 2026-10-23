"""Бот тили: цех ботида ва мижозлар ботида ўзбек (кирилл/лотин) / рус / инглиз; тугмалар таржимаси ва орқага."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil_bot as B, botlang as BL, online as OL, tg

sent = []
def fake_call(token, method, params=None, timeout=15):
    sent.append((method, params or {}))
    return {"message_id": 900 + len(sent)} if method == "sendMessage" else ({"username": "lumb_bot"} if method == "getMe" else True)
tg.call = fake_call
csent = []
tg.send_message = lambda token, chat, text, markup=None: csent.append((chat, text, markup)) or {"message_id": 1}

def last(method="sendMessage"):
    return [p for m, p in sent if m == method][-1]
def btns(p):
    return [b["text"] for row in (p.get("reply_markup") or {}).get("keyboard", []) for b in row]

d = json.load(open(BL.DICT_PATH, encoding="utf-8"))
assert "🌐 Тил" in d and d[B.B_SAL]["ru"] != B.B_SAL and d[B.B_SAL]["en"] != B.B_SAL, "луғат"
for k, v in d.items():                                         # бўшлиқ ва қаторлар сони асли билан бир хил
    for l in ("ru", "en", "uz"):
        assert v[l].count("\n") == k.count("\n") and v[l][:1].isspace() == k[:1].isspace(), (k, l, v[l])

UID = 555
msg = lambda t: {"message_id": 1, "chat": {"id": UID, "type": "private"}, "from": {"id": UID}, "text": t}
cb = lambda data: {"id": "q", "from": {"id": UID}, "data": data, "message": {"chat": {"id": UID}, "message_id": 950}}

with A.app.app_context():
    B.connect("123456:" + "A" * 30)
    db = A.db()
    db.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active) VALUES('Жасур','1',?, 'staff', ?, 1)", (A.now(), UID))
    db.commit()
    # тил танланмаган — ҳеч нарса ўзгармайди, «🌐 Тил» тугмаси бор
    B.on_message(msg("/start"))
    assert BL.B_LANG in btns(last()) and B.B_SAL in btns(last())
    # /lang → 4 тил
    B.on_message(msg(BL.B_LANG))
    assert [b["callback_data"] for row in last()["reply_markup"]["inline_keyboard"] for b in row] == ["lang:uz", "lang:uzl", "lang:ru", "lang:en"]
    B.on_callback(cb("lang:ru"))
    p = last()
    assert p["text"] == BL.DONE["ru"] and d[B.B_SAL]["ru"] in btns(p) and "🌐 Язык" in btns(p), btns(p)
    # русча тугма босилса — асл тугма ишлайди
    n = len(sent)
    B.on_message(msg(d[B.B_SAL]["ru"]))
    assert len(sent) > n and "Салом" not in last()["text"]
    salary_ru = last()["text"]
    B.on_message(msg("/start"))
    assert not any(ch in last()["text"] for ch in "ЎўҚқҒғҲҳ"), last()["text"]
    # инглиз
    B.on_callback(cb("lang:en"))
    assert d[B.B_SAL]["en"] in btns(last())
    B.on_message(msg(d[B.B_SAL]["en"]))
    assert last()["text"] != salary_ru
    # лотин
    B.on_callback(cb("lang:uzl"))
    p = last()
    assert not any("А" <= ch <= "я" for t in btns(p) for ch in t), btns(p)
    n = len(sent)
    B.on_message(msg(BL.tr(B.B_SAL, "uzl")))
    assert len(sent) > n and not any("А" <= ch <= "я" for ch in last()["text"]), last()["text"]
    # қайта ўзбекча (кирилл)
    B.on_callback(cb("lang:uz"))
    assert B.B_SAL in btns(last())

    # мижозлар боти: Telegram тили рус — ўзи рус; /lang → инглиз
    A.set_settings(bot_enabled="1", tg_token="123:" + "B" * 30)
    OL.on_update({"message": {"chat": {"id": 777, "type": "private"}, "from": {"id": 777, "language_code": "ru"}, "text": "/start"}})
    assert BL.get("client", 777) == "ru" and "Ассалому" not in csent[-1][1], csent[-1][1]
    OL.on_update({"message": {"chat": {"id": 777, "type": "private"}, "from": {"id": 777}, "text": "/lang"}})
    assert "lang:en" in json.dumps(csent[-1][2])
    assert OL.on_update({"callback_query": {"id": "z", "data": "lang:en", "from": {"id": 777}, "message": {"chat": {"id": 777}, "message_id": 5}}})
    assert BL.get("client", 777) == "en" and "Lumberture" in csent[-1][1] and "Ассалому" not in csent[-1][1]
    # навбатдаги хабар (буюртма ҳолати) — мижоз тилида
    OL.queue_msg(db, 777, "Буюртма №5: " + OL.ST_UZ["ready"])
    db.commit()
    A.process_outbox()
    assert csent[-1][0] == 777 and csent[-1][1] != "Буюртма №5: " + OL.ST_UZ["ready"], csent[-1]
    # тил танламаган мижоз — ўзбекча қолади
    OL.queue_msg(db, 888, "Буюртма №6: " + OL.ST_UZ["ready"])
    db.commit()
    A.process_outbox()
    assert csent[-1] [1] == "Буюртма №6: " + OL.ST_UZ["ready"]
# видео-қўлланма: биринчи марта юкланади, кейин file_id билан
class _R:
    def json(self): return {"ok": True, "result": {"video": {"file_id": "VID1"}}}
ups = []
B.requests.post = lambda url, **k: ups.append((url, k.get("data"))) or _R()
with A.app.app_context():
    BL.set_lang("work", UID, "uz")
    B.on_message(msg("/start"))
    assert "/video" in last()["text"]
    B.on_message(msg("/video"))
    assert len(ups) == 1 and ups[0][1]["chat_id"] == UID and A.get_setting("raspil_bot_guide_fid") == "VID1"
    B.on_message(msg("/video"))
    assert len(ups) == 1 and last("sendVideo")["video"] == "VID1"
# шерик (фақат ҳисобот): /video ҳам видео юборади, ҳисобот эмас
with A.app.app_context():
    A.db().execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active, owner) VALUES('Шерик','9',?, 'ready', 556, 1, 1)", (A.now(),))
    A.db().commit()
    n = len([1 for m, p in sent if m == "sendVideo"])
    B.on_message({"message_id": 1, "chat": {"id": 556, "type": "private"}, "from": {"id": 556}, "text": "/video"})
    assert len([1 for m, p in sent if m == "sendVideo"]) == n + 1, sent[-1]
print("BOTLANG OK")
