"""Кастамону (@ksgroup_agent_bot) бот текшируви: сохта бот (Telethon'сиз) — /start, каталог тугмалари босилади,
буюртма/контакт/тўлов тугмалари БОСИЛМАЙДИ, натижа data/ks_probe.json; Eman жадваллари/файлларига тегилмайди."""
import asyncio, json, os, sys, tempfile, types
os.environ["RENT_UI_LANG"] = "src"
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tl = types.ModuleType("telethon"); fn = types.ModuleType("telethon.functions"); er = types.ModuleType("telethon.errors")
class _Req:
    def __init__(self, **kw): self.__dict__.update(kw)
fn.messages = types.SimpleNamespace(GetBotCallbackAnswerRequest=type("CB", (_Req,), {}))
er.FloodWaitError = type("FW", (Exception,), {"seconds": 5})
tl.functions, tl.errors = fn, er
sys.modules.update({"telethon": tl, "telethon.functions": fn, "telethon.errors": er})

import app as A, ksgroup as K
K.PAUSE = 0

def cb(t, d): return {"_": "KeyboardButtonCallback", "text": t, "data": d}
def kb(t, kind="KeyboardButton"): return {"_": kind, "text": t}
class MK:
    def __init__(self, rows, kind="ReplyInlineMarkup"): self.rows, self.kind = rows, kind
    def to_dict(self): return {"_": self.kind, "rows": [{"buttons": r} for r in self.rows]}
class Msg:
    def __init__(self, id, text, out=False, mk=None): self.id, self.message, self.out, self.reply_markup, self.media = id, text, out, mk, None
MENU = {b"cat": ("Каталог:", [[cb("ЛДСП", b"ldsp"), cb("МДФ", b"mdf")], [cb("🛒 Корзина", b"cart")]]),
        b"ldsp": ("ЛДСП:", [[cb("Белый 16мм — 520 000 сум", b"p1")], [cb("⬅ Назад", b"cat")]]),
        b"mdf": ("МДФ:", [[cb("Оформить заказ", b"order")], [cb("⬅ Назад", b"cat")]]),
        b"p1": ("ЛДСП Белый 16мм\nЦена: 520 000 сум/лист", [[cb("⬅ Назад", b"ldsp")]])}
class Bot:
    def __init__(self): self.msgs, self.n, self.pressed, self.texts = [], 0, [], []
    def add(self, text, out=False, mk=None):
        self.n += 1; m = Msg(self.n, text, out, mk); self.msgs.append(m); return m
    async def get_entity(self, b): return b
    async def send_message(self, ent, text):
        m = self.add(text, out=True); self.texts.append(text)
        if text == "/start":
            self.add("Добро пожаловать", mk=MK([[kb("📒 Каталог")], [kb("📞 Отправить номер", "KeyboardButtonRequestPhone")], [kb("Связаться с менеджером")]], "ReplyKeyboardMarkup"))
        elif text == "📒 Каталог":
            t, rows = MENU[b"cat"]; self.add(t, mk=MK(rows))
        return m
    async def get_messages(self, ent, min_id=None, limit=10, ids=None):
        if ids is not None:
            return next((m for m in self.msgs if m.id == ids), None)
        return [m for m in self.msgs if m.id > (min_id or 0)][-limit:]
    async def __call__(self, req):
        self.pressed.append(req.data)
        m = next(m for m in self.msgs if m.id == req.msg_id)
        t, rows = MENU[req.data]; m.message, m.reply_markup = t, MK(rows)
        return types.SimpleNamespace(message="")

b = Bot(); out = {}
asyncio.run(K.explore(b, out))
assert b.texts == ["/start", "📒 Каталог"], b.texts                      # телефон/менеджер тугмалари юборилмади
assert b"cart" not in b.pressed and b"order" not in b.pressed, b.pressed     # сават ва буюртма босилмади
assert b"ldsp" in b.pressed and b"mdf" in b.pressed and b"p1" in b.pressed, b.pressed
txt = json.dumps(out, ensure_ascii=False)
assert "Цена: 520 000 сум/лист" in txt and out["actions"] <= K.MAX_ACTIONS and out["steps"][0]["action"] == "/start"
assert not K.allowed(dict(text="📞 Номер", kind="KeyboardButtonRequestPhone", data=None))
assert not K.allowed(dict(text="Сайт", kind="KeyboardButtonUrl", data=None))
assert K.allowed(dict(text="ЛДСП", kind="KeyboardButtonCallback", data=b"x"))

# probe_due: сўров файли → натижа файли; аккаунт уланмаган бўлса — сўров қолади
with A.app.app_context():
    req = os.path.join(A.DATA_DIR, "ks_probe.req"); res = os.path.join(A.DATA_DIR, "ks_probe.json")
    open(req, "w").write("explore")
    A.set_settings(tgu_enabled="0")
    assert K.probe_due() is None and os.path.exists(req)
    open(req, "w").write("os.system")
    _ready, _cfg, _snd = A.tgu_ready, A.tgu_cfg, A.tgu_sender
    A.tgu_ready = lambda: True; A.tgu_cfg = lambda: ("1", "h")
    class S:
        def call(self, api_id, api_hash, fn, timeout=240): return asyncio.run(fn(Bot()))
    A.tgu_sender = lambda: S()
    try:
        r = K.probe_due(); assert "функция йўқ" in r["error"] and not os.path.exists(req)     # фақат explore ишлайди
        open(req, "w").write("")
        r = K.probe_due(); assert not os.path.exists(req) and os.path.exists(res) and r.get("steps") and "error" not in r, r
    finally:
        A.tgu_ready, A.tgu_cfg, A.tgu_sender = _ready, _cfg, _snd
    assert not os.path.exists(os.path.join(A.DATA_DIR, "eman_probe.json"))
print("OK ksgroup")
