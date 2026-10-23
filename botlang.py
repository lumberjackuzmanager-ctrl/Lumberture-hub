# -*- coding: utf-8 -*-
"""Бот тили: цех боти (work) ва мижозлар боти (client) учун Ўзбекча (кирилл), Oʻzbekcha (лотин), Русский, English.

Бот матнлари кодда ўзбекча (кирилл) ёзилган. Ишчи/мижоз тил танласа, юборишдан олдин матн ва тугмалар
lang/bot.json луғати бўйича таржима қилинади (кодда ёзилган бўлаклар алмаштирилади), лотин — кириллдан транслит.
Ишчи таржима қилинган тугмани босса — кириш матни асл тугмага қайтарилади, бот одатдагидек ишлайди.
Тил танланмаган бўлса — ҳеч нарса ўзгармайди (ҳозирги ўзбекча матн)."""
import os, re, json, threading, time, logging

import app as A

log = logging.getLogger("botlang")
HERE = os.path.dirname(os.path.abspath(__file__))
DICT_PATH = os.path.join(HERE, "lang", "bot.json")
LANGS = [("uz", "🇺🇿 Ўзбекча"), ("uzl", "🇺🇿 Oʻzbekcha"), ("ru", "🇷🇺 Русский"), ("en", "🇬🇧 English")]
LANG_NAMES = dict(LANGS)
B_LANG = "🌐 Тил"                                   # цех ботидаги тугма (таржимаси луғатда)
ASK = "🌐 Тилни танланг / Выберите язык / Choose language:"
DONE = {"uz": "✅ Тил: Ўзбекча", "uzl": "✅ Til: Oʻzbekcha", "ru": "✅ Язык: русский", "en": "✅ Language: English"}
_L = r"А-Яа-яЁёЎўҚқҒғҲҳA-Za-zʻʼ'"
_lock = threading.Lock()
_st = {"d": None, "mt": None, "re": {}, "rev": {}, "cache": {}, "missing": set()}
_cur = threading.local()


# ---------------------------------------------------------------- луғат
def _load():
    try:
        mt = os.path.getmtime(DICT_PATH)
    except OSError:
        mt = None
    if _st["d"] is not None and _st["mt"] == mt:
        return _st["d"]
    with _lock:
        try:
            d = json.load(open(DICT_PATH, encoding="utf-8")) if mt else {}
        except (OSError, ValueError):
            log.exception("bot.json")
            d = {}
        _st.update(d=d, mt=mt, re={}, rev={})
    return d


def _regex(lang):
    d = _load()
    r = _st["re"].get(lang)
    if r is not None:
        return r
    alts = []
    for src in sorted(d, key=len, reverse=True):
        e = d[src]
        tgt = e.get("uz" if lang == "uzl" else lang)
        if not tgt:
            continue                                    # ўзига тенг таржима ҳам қолади: узун ибора қисқасидан олдин ушланади («🌐 Тил / Язык …»)
        gl, gr = (e.get("g") or [False, False])[:2]
        p = re.escape(src)
        if not gl and re.match(f"[{_L}]", src):
            p = f"(?<![{_L}])" + p
        if not gr and re.search(f"[{_L}]$", src):
            p = p + f"(?![{_L}])"
        alts.append(p)
    r = re.compile("|".join(alts)) if alts else None
    _st["re"][lang] = r
    return r


def tr(text, lang):
    """Чиқувчи матн таржимаси (lang=None — ўзгармайди)."""
    if not text or not lang or not isinstance(text, str):
        return text
    d = _load()
    rx = _regex(lang)
    key = "uz" if lang == "uzl" else lang
    out = rx.sub(lambda m: d[m.group(0)][key], text) if rx else text
    if lang == "uzl":
        import i18n
        out = i18n.translit(out)
    elif lang in ("ru", "en") and re.search(r"[ЎўҚқҒғҲҳ]", out):
        _missing(out)
    return out


def _missing(text):
    """Таржимаси йўқ қолган ўзбекча матн — кейин луғатни тўлдириш учун (data/bot_lang_missing.txt)."""
    k = text[:200]
    if k in _st["missing"] or len(_st["missing"]) > 500:
        return
    _st["missing"].add(k)
    try:
        with open(os.path.join(A.DATA_DIR, "bot_lang_missing.txt"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M ") + text.replace("\n", " ⏎ ")[:600] + "\n")
    except OSError:
        pass


def tr_markup(mk, lang):
    """Тугмалар матни (reply ва inline). dict ёки JSON-сатр қайтади — кирганидек."""
    if not mk or not lang:
        return mk
    as_str = isinstance(mk, str)
    try:
        m = json.loads(mk) if as_str else json.loads(json.dumps(mk))
    except ValueError:
        return mk
    rev = _st["rev"].setdefault(lang, {})
    for k in ("keyboard", "inline_keyboard"):
        for row in m.get(k) or []:
            for b in row:
                if isinstance(b, dict) and b.get("text"):
                    t = tr(b["text"], lang)
                    if t != b["text"]:
                        rev[t] = b["text"]
                    b["text"] = t
    return json.dumps(m, ensure_ascii=False) if as_str else m


def untr(text, lang):
    """Таржима қилинган тугма босилса — асл (ўзбекча) матнига қайтариш."""
    if not text or not lang:
        return text
    rev = _st["rev"].setdefault(lang, {})
    if text in rev:
        return rev[text]
    d = _load()
    if not rev.get("__full__"):
        for src, e in d.items():
            if not any((e.get("g") or [False, False])[:2]) and re.match(r"[^\w\s«]", src):   # фақат тугмалар (эмодзи билан бошланади)
                t = tr(src, lang)
                if t != src:
                    rev.setdefault(t, src)
        rev["__full__"] = "1"
    return rev.get(text, text)


# ---------------------------------------------------------------- ким қайси тилда
def _con():
    try:
        return A.db(), False
    except RuntimeError:
        return A.connect(), True


def init_tables(con):
    con.execute("CREATE TABLE IF NOT EXISTS bot_lang(bot TEXT NOT NULL, chat INTEGER NOT NULL, lang TEXT NOT NULL, auto INTEGER DEFAULT 0, "
                "updated_at TEXT, PRIMARY KEY(bot, chat))")


def get(bot, chat):
    """Танланган тил ёки None (танланмаган — матн ўзгармайди)."""
    if chat is None:
        return None
    k = (bot, int(chat)) if str(chat).lstrip("-").isdigit() else (bot, chat)
    c = _st["cache"].get(k)
    if c is not None and c[1] > time.time():
        return c[0]
    lang = None
    con, own = _con()
    try:
        init_tables(con)
        r = con.execute("SELECT lang FROM bot_lang WHERE bot=? AND chat=?", k).fetchone()
        lang = r[0] if r else None
    except Exception:
        log.exception("bot_lang get")
    finally:
        if own:
            con.close()
    _st["cache"][k] = (lang, time.time() + 60)
    return lang


def set_lang(bot, chat, lang, auto=False):
    if lang not in LANG_NAMES:
        return False
    con, own = _con()
    try:
        init_tables(con)
        if auto:
            con.execute("INSERT OR IGNORE INTO bot_lang(bot, chat, lang, auto, updated_at) VALUES(?,?,?,1,?)", (bot, int(chat), lang, A.now()))
        else:
            con.execute("INSERT INTO bot_lang(bot, chat, lang, auto, updated_at) VALUES(?,?,?,0,?) ON CONFLICT(bot, chat) DO UPDATE SET "
                        "lang=excluded.lang, auto=0, updated_at=excluded.updated_at", (bot, int(chat), lang, A.now()))
        con.commit()
    finally:
        if own:
            con.close()
    _st["cache"].pop((bot, int(chat)), None)
    return True


def from_tg(code):
    """Telegram тил коди -> мижоз учун бошланғич тил."""
    c = (code or "").lower()[:2]
    return {"ru": "ru", "en": "en", "uz": "uz"}.get(c)


def current(bot=None, chat=None):
    """Шу оқимда ишланаётган хабар эгаси (answerCallbackQuery'да chat_id йўқ)."""
    if bot is not None:
        _cur.v = (bot, chat)
    return getattr(_cur, "v", (None, None))


def menu(prefix="lang:"):
    return {"inline_keyboard": [[{"text": n, "callback_data": prefix + k}] for k, n in LANGS]}


def is_lang_cmd(text):
    t = (text or "").strip()
    return t in ("/lang", "/language", "/til", "/язык") or t.startswith("🌐")


def prepare_params(bot, method, p):
    """Bot API чақиришдан олдин: text/caption/тугмалар — суҳбатдошнинг тилида."""
    if method not in ("sendMessage", "editMessageText", "sendPhoto", "sendDocument", "editMessageCaption",
                      "editMessageReplyMarkup", "answerCallbackQuery", "sendMediaGroup"):
        return p
    chat = p.get("chat_id")
    if chat is None:
        b, chat = current()
        if b != bot:
            return p
    lang = get(bot, chat)
    if not lang:
        return p
    p = dict(p)
    for k in ("text", "caption"):
        if isinstance(p.get(k), str):
            p[k] = tr(p[k], lang)
            if k == "text":
                p[k] = p[k][:4096]
    if p.get("reply_markup"):
        p["reply_markup"] = tr_markup(p["reply_markup"], lang)
    return p
