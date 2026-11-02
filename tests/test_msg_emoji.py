"""2.11 (эгаси, 3В): мижоз хабари ҳар қатори эмодзи билан, бошида «🌲 Lumberture»; Premium аккаунтда 🌲 — Lumberture'нинг
анимацион эмодзиси (MessageEntityCustomEmoji, UTF-16 офсет); эмодзи рад этилса — оддий хабар."""
import os, sys, tempfile, asyncio
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, c1, tguser
from telethon import errors

w = c1.DOC_WORDS["ru"]
assert c1._emo_line("Товары и услуги: $2.44.", w) == "📦 Товары и услуги: $2.44."
assert c1._emo_line("Накладная № 6753 — $2.44.", w).startswith("📦 ")
assert c1._emo_line("Приход в кассу: 30 000 сум.", w) == "💵 Приход в кассу: 30 000 сум."
assert c1._emo_line("Долг уменьшился на $1.", w).startswith("📉 ") and c1._emo_line("Долг увеличился на $1.", w).startswith("📈 ")
assert c1._emo_line("Возврат товара: $5.", w).startswith("↩️ ") and c1._emo_line("что-то", w) == "что-то"
wu = c1.DOC_WORDS["uz"]
assert c1._emo_line("Tovar va xizmatlar: $2.", wu).startswith("📦 ") and c1._emo_line("Kassaga kirim: 1 sum.", wu).startswith("💵 ")

with A.app.app_context():
    A.set_settings(tg_brand_emoji="5368324170671202286")
    A.tgu_sender()
    assert tguser.BRAND == {"🌲": 5368324170671202286}
    txt = c1.BRAND_HEAD + "\nЗдравствуйте, Азиз!\n📦 Товары: $1"
    es = tguser.brand_entities(txt)
    assert len(es) == 1 and es[0].offset == 0 and es[0].length == 2 and es[0].document_id == 5368324170671202286
    es = tguser.brand_entities("ab🌲 x 🌲")
    assert [(e.offset, e.length) for e in es] == [(2, 2), (7, 2)], [(e.offset, e.length) for e in es]


class FakeC:
    def __init__(self, reject=False):
        self.calls, self.reject = [], reject
    async def send_message(self, ent, text, **kw):
        self.calls.append(("msg", text, kw))
        if self.reject and kw.get("formatting_entities"):
            raise errors.RPCError("DOCUMENT_INVALID")
        return type("M", (), {"id": 5})()
    async def send_file(self, ent, f, caption=None, **kw):
        self.calls.append(("file", caption, kw))
        return type("M", (), {"id": 6})()

c = FakeC(); asyncio.run(tguser._out(c, 1, "🌲 Lumberture\nСалом"))
assert c.calls[0][2].get("formatting_entities"), c.calls
c = FakeC(reject=True); m = asyncio.run(tguser._out(c, 1, "🌲 Lumberture\nСалом"))
assert len(c.calls) == 2 and not c.calls[1][2] and m.id == 5, c.calls          # рад этилди — оддий хабар
c = FakeC(); asyncio.run(tguser._out(c, 1, "Салом"))
assert not c.calls[0][2]                                                        # 🌲 йўқ — эски кўриниш
with A.app.app_context():
    A.set_settings(tg_brand_emoji=""); A.tgu_sender()
    assert tguser.BRAND == {}
print("MSG EMOJI OK")
