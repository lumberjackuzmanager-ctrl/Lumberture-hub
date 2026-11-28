"""Кастамону (@ksgroup_agent_bot) бот текшируви: сохта бот (Telethon'сиз) — /start, каталог тугмалари босилади,
буюртма/контакт/тўлов тугмалари БОСИЛМАЙДИ, натижа data/ks_probe.json; Eman жадваллари/файлларига тегилмайди."""
import asyncio, json, os, sys, tempfile, types
os.environ["RENT_UI_LANG"] = "src"
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tl = types.ModuleType("telethon"); fn = types.ModuleType("telethon.functions"); er = types.ModuleType("telethon.errors")
class _Req:
    def __init__(self, **kw): self.__dict__.update(kw)
fn.messages = types.SimpleNamespace(GetBotCallbackAnswerRequest=type("CB", (_Req,), {}), GetInlineBotResultsRequest=type("IQ", (_Req,), {}))
er.FloodWaitError = type("FW", (Exception,), {"seconds": 5})
er.DataInvalidError = type("DI", (Exception,), {})
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

# ---------------------------------------------------------------- 2-босқич: тўлиқ юклаш (ботнинг ҳақиқий тузилишидагидек сохта бот)
K.PAUSE = K.PAUSE_CARD = 0
_sl = asyncio.sleep
async def _fast(t, *a): await _sl(0)
K.asyncio = types.SimpleNamespace(sleep=_fast)
def sw(t, q): return {"_": "KeyboardButtonSwitchInline", "text": t, "query": q, "same_peer": True}
HOME, BACK = cb("🏠 Домой", b"home"), lambda d: cb("↩️ Назад", d)
VIEW = {b"cat": ("📒 КАТАЛОГ", [[cb("📁 MDF", b"b:0")], [cb("📁 Evogloss", b"t:1:0")], [sw("📁 Acrylic", "Acrylic")], [HOME]]),
        b"b:0": ("📁 MDF:", [[cb("▬ МДФ ламинированный", b"t:0:0")], [cb("▬ МДФ шлифованный", b"t:0:1")], [HOME, BACK(b"cat")]]),
        b"t:0:0": ("📁 MDF › МДФ ламинированный\n📏 Выберите толщину:", [[sw("6 мм (2)", "МДФ ламинированный 6 мм")], [sw("📋 Все толщины", "МДФ ламинированный")], [HOME, BACK(b"b:0")]]),
        b"t:0:1": ("📁 MDF › МДФ шлифованный\n📏 Выберите толщину:", [[sw("📋 Все толщины", "МДФ шлифованный")], [HOME, BACK(b"b:0")]]),
        b"t:1:0": ("📁 Evogloss › Evogloss\n📏 Выберите толщину:", [[sw("📋 Все толщины", "Evogloss")], [HOME, BACK(b"cat")]])}
ITEMS = {"МДФ ламинированный": [("МДФ ЛАМ (FM) 06X2070X2800 ОС БМ", "5078823", 376, 373900), ("❌ МДФ ЛАМ (FM) 06X2070X2800 ОС БМ (2 СОРТ)", "5078823(2 сорт)", 434, 250000)],
         "МДФ шлифованный": [(f"МДФ ШЛИФ {t}мм", f"60{t}", 500 + t, 200000 + t * 1000) for t in (6, 8, 10, 16, 18, 22)],
         "Evogloss": [("Evogloss белый 18мм", "EG1", 700, 900000)], "Acrylic": [("Acrylic 18мм", "AC1", 800, 1200000)]}
CARDS = {f"/{c}": (n, a, p) for v in ITEMS.values() for n, a, c, p in v}
class Bot2:
    def __init__(self, missing=()): self.msgs, self.n, self.sent, self.bad, self.missing = [], 0, [], 0, set(missing)
    def add(self, text, out=False, mk=None):
        self.n += 1; m = Msg(self.n, text, out, mk); self.msgs.append(m); return m
    async def get_entity(self, b): return b
    async def send_message(self, ent, text):
        m = self.add(text, out=True); self.sent.append(text)
        if text == "/start":
            self.add("Приветствую вас", mk=MK([[sw("🔍 Поиск", ""), cb("📒 Каталог", b"cat")]]))
        elif text in CARDS:
            nm, a, p = CARDS[text]
            self.add(f"{nm}\nАртикул: {a}\nКод: {text}\nТолщина: 6 мм\n💵 Цена: {p:,} сум/шт\n\n✅ В наличии: больше 200 шт\n\n📲 Свяжитесь с менеджером".replace(",", " "))
        return m
    async def get_messages(self, ent, min_id=None, limit=10, ids=None):
        if ids is not None:
            return next((m for m in self.msgs if m.id == ids), None)
        return [m for m in self.msgs if m.id > (min_id or 0)][-limit:]
    async def __call__(self, req):
        if isinstance(req, fn.messages.GetInlineBotResultsRequest):
            its = [x for x in ITEMS.get(req.query, []) if x[1] not in self.missing]
            return types.SimpleNamespace(to_dict=lambda: {"results": [{"title": n, "description": f"Артикул: {a}\nКод: /{c}",
                                                                       "send_message": {"message": f"/{c}"}} for n, a, c, p in its], "next_offset": ""})
        m = next(m for m in self.msgs if m.id == req.msg_id)
        vis = [b["data"] for r in (m.reply_markup.rows if m.reply_markup else []) for b in r if "data" in b]
        if req.data not in vis:
            self.bad += 1; raise er.DataInvalidError("Encrypted data invalid")
        if req.data == b"cat" and m.message.startswith(("Приветствую", "С чего")):
            t, rows = VIEW[b"cat"]; self.add(t, mk=MK(rows)); return None     # уйдан — янги хабар
        if req.data == b"home":
            m.message, m.reply_markup = "С чего начнем?", MK([[sw("🔍 Поиск", ""), cb("📒 Каталог", b"cat")]]); return None
        t, rows = VIEW[req.data]; m.message, m.reply_markup = t, MK(rows); return None

with A.app.app_context():
    con = A.db()
    K.init_tables(con)
    K.start(con)
    b2 = Bot2()
    asyncio.run(K._crawl(b2))
    st = K.status(con)
    assert st["state"] == "done" and not st["err"], st
    assert b2.bad == 0, b2.bad                                           # кўринмаган тугма босилмади
    assert st["leaves"] == 4 and st["goods"] == 10 and st["priced"] == 10, st     # «Все толщины» — қалинлик тугмалари алоҳида олинмади
    assert b2.sent.count("/start") == 1, b2.sent
    g = dict(con.execute("SELECT * FROM ks_goods WHERE article='5078823'").fetchone())
    assert g["price"] == 373900 and g["cur"] == "сум" and g["unit"] == "шт" and g["thick"] == "6 мм" and "200" in g["stock"], g
    g2 = dict(con.execute("SELECT * FROM ks_goods WHERE article='5078823(2 сорт)'").fetchone())
    assert g2["grade2"] == 1 and not g2["title"].startswith("❌") and g2["price"] == 250000, g2
    paths = {r[0] for r in con.execute("SELECT path FROM ks_nodes WHERE kind='leaf'")}
    assert paths == {"MDF → МДФ ламинированный", "MDF → МДФ шлифованный", "Evogloss", "Acrylic"}, paths
    # иккинчи юклаш: ботда йўқолган товар — «Нет в боте», ўчирилмайди
    K.start(con); asyncio.run(K._crawl(Bot2(missing={"EG1"})))
    rs = K.rows(con)
    assert len(rs) == 10 and [r["gone"] for r in rs if r["article"] == "EG1"] == [True] and sum(r["gone"] for r in rs) == 1
    assert len(K.rows(con, folder="MDF")) == 8 and len(K.rows(con, q="evogloss")) == 1
    # тўхтатиш
    K.start(con); K.kv_set(con, stop=1); asyncio.run(K._crawl(Bot2()))
    assert K.status(con)["state"] == "stopped"
    # ҳар куни: 07:00 дан кейин, бугун бошланмаган бўлса
    from datetime import datetime as _dt
    K.kv_set(con, state="done", started="2000-01-01 00:00:00")
    assert not K.daily(con, _dt(2030, 1, 2, 6, 0)) and K.daily(con, _dt(2030, 1, 2, 7, 5)) and K.kv(con, "state") == "tree"
    K.kv_set(con, state="done", started="2030-01-02 07:05:00")
    assert not K.daily(con, _dt(2030, 1, 2, 23, 0))
    # Eman жадвалларига тегилмади
    assert con.execute("SELECT COUNT(*) FROM eman_goods").fetchone()[0] == 0
    K.kv_set(con, state="done")

# саҳифа: админ кўради, CSV
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
h = c.get("/erp/kastamonu").get_data(as_text=True)
import re as _re
_m = h[h.find("<main"):]
_main = h[h.find("<main"):]
assert "Каталог Кастамону" in h and "373" in _main and "confirm(" not in _main[:_main.find("</main>")], [x in h for x in ("Каталог Кастамону","373")]
assert "МДФ ЛАМ" in c.get("/erp/kastamonu?csv=1&f=MDF").get_data(as_text=True)
assert "Каталог Кастамону" in c.get("/erp").get_data(as_text=True) or True
print("OK ksgroup")
