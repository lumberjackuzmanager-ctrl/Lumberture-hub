# -*- coding: utf-8 -*-
"""Интерфейс тили: русча (стандарт), ўзбекча (лотин), инглизча.

Таржима фақат интерфейсга (шаблонлардаги матн, тугмалар, менюлар, хабарлар) тегади. Маълумотлар — товар, папка, хизмат,
мижоз номлари, изоҳлар — ўзгармайди (улар шаблонга ўзгарувчи бўлиб келади, таржима қилинмайди). Чоп этиладиган накладной
(erp_print, c1_print) доим русча, мижозга кетадиган хабарлар (Telegram) ўз ҳолида.

Қандай ишлайди: Jinja кенгайтмаси шаблон матнини (data) ва {{ }} / {% %} ичидаги кириллча сатрларни таржима функциясига
ўрайди; таржима lang/ui.json луғатидан (ибора -> ru/uz/en). Луғатда йўқ ўзбекча ибора uz учун ўзи лотинга ўгирилади.
"""
import json, os, re

from flask import g, session, request, redirect, has_request_context
from jinja2 import nodes, pass_context
from jinja2.ext import Extension
from jinja2.lexer import Token
from markupsafe import Markup

A = None
LANGS = {"ru": "Русский", "uz": "Oʻzbekcha", "en": "English"}
DEFAULT = os.environ.get("RENT_UI_LANG") or "ru"      # синовларда «src» — таржимасиз (асл матн)
NO_TR = {"erp_print.html", "c1_print.html", "erp_print2.html", "basis_print.html"}          # чоп этиш — доим русча
CY = re.compile("[А-Яа-яЁёЎўҚқҒғҲҳ]")
SEG = re.compile(r"(?:[^<>\"'`\n{}]|(?<=[0-9A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ])'(?=[А-Яа-яЁёЎўҚқҒғҲҳ]))+")   # 1С'дан, Excel'дан — сўз ичидаги апостроф
UZ_ONLY = re.compile("[ЎўҚқҒғҲҳ]")
_D = {}            # ибора -> {ru, uz, en}
_SUB = []          # (ибора, ...) — узундан қисқага (хабарлар ичидан қидириш учун)
_cache = {}


def load(path=None):
    global _D, _SUB
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang", "ui.json")
    try:
        with open(path, encoding="utf-8") as f:
            _D = json.load(f)
    except (OSError, ValueError):
        _D = {}
    _SUB = sorted((k for k in _D if len(k) >= 4), key=len, reverse=True)
    try:
        with open(os.path.join(os.path.dirname(path), "data_words.json"), encoding="utf-8") as f:
            DATA_WORDS.clear(); DATA_WORDS.update(json.load(f))
    except (OSError, ValueError):
        pass
    _cache.clear()


# ---------------------------------------------------------------- ўзбек кирилл -> лотин (луғатда йўқ бўлса)
_T = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
      "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "s", "ч": "ch", "ш": "sh",
      "щ": "sh", "ъ": "ʼ", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya", "ё": "yo", "ў": "oʻ", "қ": "q", "ғ": "gʻ", "ҳ": "h"}
_V = set("аеёиоуўэюяaeiou")


def translit(s):
    out = []
    for i, ch in enumerate(s):
        lo = ch.lower()
        if lo == "е":
            prev = s[i - 1].lower() if i else " "
            r = "ye" if (not prev.isalpha() or prev in _V or prev in "ъь") else "e"
        elif lo in _T:
            r = _T[lo]
        else:
            out.append(ch); continue
        if ch != lo and r:
            nxt = s[i + 1] if i + 1 < len(s) else ""
            r = r.upper() if (nxt and nxt.isupper()) else r[0].upper() + r[1:]
        out.append(r)
    return "".join(out)


# ---------------------------------------------------------------- тил
def lang():
    if not has_request_context():
        return DEFAULT
    if "lang" in g:
        return g.lang
    lg = None
    try:
        u = A.current_user()
        if u is not None and "ui_lang" in u.keys():
            lg = u["ui_lang"]
    except Exception:
        lg = None
    lg = lg or session.get("ui_lang") or DEFAULT
    g.lang = lg if lg in LANGS or lg == "src" else DEFAULT
    return g.lang


def phrase(key, lg):
    """Битта ибора (бошидаги/охиридаги бўшлиқсиз)."""
    e = _D.get(key)
    if e and e.get(lg):
        return e[lg]
    if lg == "uz":
        return translit(key)
    if lg == "en" and e and e.get("ru"):
        return e["ru"]
    return key


def text(s, lg=None):
    """Шаблон матни: кириллча бўлакларни луғатдан алмаштириш (HTML, бўшлиқ ва белгилар сақланади)."""
    lg = lg or lang()
    if not s or lg == "src" or not CY.search(s):
        return s
    ck = (lg, s)
    r = _cache.get(ck)
    if r is not None:
        return r
    def rep(m):
        seg = m.group()
        k = seg.strip()
        if len(CY.findall(k)) < 2:
            return seg
        t = phrase(k, lg)
        if t == k:
            return seg
        i = seg.index(k)
        return seg[:i] + t + seg[i + len(k):]
    r = SEG.sub(rep, s)
    if len(_cache) > 50000:
        _cache.clear()
    _cache[ck] = r
    return r


def tr(s, lg=None):
    """Python'дан келган хабар (flash, хато матни): аввал бутун ибора, кейин ичидаги маълум бўлаклар (узундан)."""
    lg = lg or lang()
    if not s or lg == "src" or not isinstance(s, str) or not CY.search(s):
        return s
    st = s.strip()
    if st in _D:
        return text(s, lg)
    ck = ("tr", lg, s)
    r = _cache.get(ck)
    if r is not None:
        return r
    out, low = s, s
    for k in _SUB:
        if k in low:
            t = phrase(k, lg)
            if t != k:
                out = out.replace(k, t)
                low = low.replace(k, "\0" * len(k))
    if lg == "uz" and CY.search(out) and UZ_ONLY.search(out):
        out = translit(out)                       # қолган ўзбекча бўлаклар
    _cache[ck] = out
    return out


def _txd(s):
    return Markup(text(s))


def _txs(s):
    return text(s)


# 1С'дан келадиган маълумот бўлиши мумкин бўлган сўзлар (ўлчов бирликлари ва ҳ.к.) — {{ }} да таржима қилинмайди
DATA_WORDS = set()     # lang/data_words.json: ўлчов бирликлари, касса справочниги ва ҳисоблар номлари (фойдаланувчи маълумоти)


@pass_context
def _final(ctx, v):
    """{{ ўзгарувчи }}: фақат луғатдаги иборага тенг бўлса (масалан KIND_NAMES, меню номлари) — таржима; товар/мижоз номлари тегилмайди.
    Чоп этиш шаблонлари (NO_TR) — доим русча, ўзгарувчилари ҳам (26.10: «Разрешение» лотинча «Razresheniye» бўлиб чиқаётган эди)."""
    if isinstance(v, str) and v and not isinstance(v, Markup) and lang() != "src" and \
            (getattr(ctx, "name", None) or "").split("/")[-1] not in NO_TR:
        st = v.strip()
        if st in _D and st not in DATA_WORDS:
            return text(v)
        m = _MON.match(st)                      # «Октябрь 2026» (ой номи + йил)
        if m and m.group(1) in _D:
            return phrase(m.group(1), lang()) + m.group(2)
    return v


_MON = re.compile(r"^([А-Яа-яЁё]+)(\s+\d{4})$")


class TrExt(Extension):
    """Шаблон матни ва кириллча сатрларни таржимага ўраш (компиляция пайтида; таржима — ишлаш пайтида, тилга қараб)."""
    def filter_stream(self, stream):
        if (stream.name or "").split("/")[-1] in NO_TR:
            yield from stream
            return
        prev = None
        in_cmp = 0          # «in (...)» ичида — сатрлар солиштириш учун, таржима қилинмайди
        depth = 0
        cmp_depth = None
        for tok in stream:
            if tok.type == "data" and CY.search(tok.value):
                ln = tok.lineno
                yield Token(ln, "variable_begin", "{{")
                yield Token(ln, "name", "_txd")
                yield Token(ln, "lparen", "(")
                yield Token(ln, "string", tok.value)
                yield Token(ln, "rparen", ")")
                yield Token(ln, "variable_end", "}}")
                prev = tok
                continue
            if tok.type == "lparen":
                depth += 1
                if prev is not None and prev.type == "name" and prev.value == "in":
                    cmp_depth = depth
            elif tok.type == "rparen":
                if cmp_depth == depth:
                    cmp_depth = None
                depth -= 1
            if tok.type == "string" and len(CY.findall(tok.value)) >= 2 and cmp_depth is None:
                nxt_cmp = False
                if prev is not None and (prev.type in ("eq", "ne") or (prev.type == "name" and prev.value in ("in", "not"))):
                    nxt_cmp = True
                if not nxt_cmp:
                    yield Token(tok.lineno, "name", "_txs")
                    yield Token(tok.lineno, "lparen", "(")
                    yield tok
                    yield Token(tok.lineno, "rparen", ")")
                    prev = tok
                    continue
            yield tok
            prev = tok


def register(app_module):
    global A
    A = app_module
    app = A.app
    load()
    con = A.connect()
    if "ui_lang" not in {r[1] for r in con.execute("PRAGMA table_info(users)")}:
        con.execute("ALTER TABLE users ADD COLUMN ui_lang TEXT")
        con.commit()
    con.close()
    app.jinja_env.add_extension(TrExt)
    app.jinja_env.globals.update(_txd=_txd, _txs=_txs, UI_LANGS=LANGS, ui_lang=lang)
    app.jinja_env.filters["tr"] = tr
    app.jinja_env.finalize = _final
    app.jinja_env.cache.clear() if app.jinja_env.cache else None

    @app.route("/lang/<code>")
    def set_lang(code):
        code = code if code in LANGS else DEFAULT
        session["ui_lang"] = code
        u = A.current_user()
        if u:
            A.db().execute("UPDATE users SET ui_lang=? WHERE id=?", (code, u["id"]))
            A.db().commit()
        g.pop("lang", None)
        from urllib.parse import urlsplit
        u_ = urlsplit(request.args.get("next") or request.referrer or "/")
        nxt = (u_.path or "/") + (("?" + u_.query) if u_.query else "")
        if not nxt.startswith("/") or nxt.startswith("//") or nxt.startswith("/lang/"):
            nxt = "/"
        return redirect(nxt)
