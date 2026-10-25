"""Нотўғри клавиатура тилида ёзилган қидирувни тушуниш: «vty [jpbh» -> «мен хозир», «Утптк» -> «Egger».
Лотин алифбосида ёзилгани ҳам (эгаси, 26.10: ишчиларнинг кўпида лотин): «beliy gladkiy» -> «б_л__ гладк__» (SQL LIKE'да «_» — бир
ҳарф: е/э, и/ы/й каби икки хил ёзилиши мумкин бўлганлар), «kashemir» -> «каш_м_р», «hdf» -> «хдф».
variants(q) -> [асл матн, лотиндан ўқилиши, кириллга ўгирилгани (раскладка), лотинга ўгирилгани] (фақат маъноли бўлганлари)."""
import re

_EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`"
_RU = "йцукенгшщзхъфывапролджэячсмитьбюё"
_EN2RU = {e: r for e, r in zip(_EN, _RU)}
_EN2RU.update({e.upper(): r.upper() for e, r in zip(_EN, _RU) if e.isalpha()})
_EN2RU.update({"{": "Х", "}": "Ъ", ":": "Ж", '"': "Э", "<": "Б", ">": "Ю", "~": "Ё"})
_RU2EN = {r: e for e, r in _EN2RU.items()}
# ўзбек кирилл клавиатураси: ў қ ғ ҳ (рус раскладкасидаги щ ъ ... ўрнида) — лотинга ўгиришда ҳам тушунилсин
_RU2EN.update({"ў": "o", "Ў": "O", "қ": "q", "Қ": "Q", "ғ": "g", "Ғ": "G", "ҳ": "h", "Ҳ": "H"})
_LAT = re.compile(r"[A-Za-z]")
_CYR = re.compile(r"[А-Яа-яЁёЎўҚқҒғҲҳ]")


def to_ru(s):
    return "".join(_EN2RU.get(ch, ch) for ch in s or "")


def to_en(s):
    return "".join(_RU2EN.get(ch, ch) for ch in s or "")


_PH2 = (("shch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ya", "я"), ("yu", "ю"), ("yo", "_"), ("ye", "е"),
        ("o'", "_"), ("oʻ", "_"), ("o`", "_"), ("oʼ", "_"), ("o‘", "_"), ("g'", "_"), ("gʻ", "_"), ("g`", "_"), ("gʼ", "_"), ("g‘", "_"))   # ў/о, ғ/г
_PH1 = dict(a="а", b="б", v="в", w="в", g="г", d="д", e="_", z="з", i="_", y="_", j="ж", k="к", q="к", c="к", l="л", m="м", n="н",
            o="о", p="п", r="р", s="с", t="т", u="у", f="ф", h="х", x="х")


def phon(s):
    """Лотинча ёзилган сўз -> кирилл қидирув намунаси (LIKE учун; «_» — ҳар қандай бир ҳарф). Лотин ҳарфисиз қисмлари ўзгармайди."""
    t = (s or "").casefold()
    out, i = [], 0
    while i < len(t):
        for a, b in _PH2:
            if t.startswith(a, i):
                out.append(b); i += len(a)
                break
        else:
            out.append(_PH1.get(t[i], t[i])); i += 1
    return "".join(out)


def variants(q):
    q = (q or "").strip()
    out = [q] if q else []
    if _LAT.search(q):
        ph = phon(q)                                  # лотинча ёзилган ўзбек/рус сўзи — аввал шу (кўпроқ учрайди)
        if _CYR.search(ph) and not re.search(r"[\[\];,.<>{}:\"~]", re.sub(r"(?<=\d)[.,](?=\d)", "", q)):   # «[ ; .» — раскладка хатоси, лотинча сўз эмас
            out.append(ph)
        r = to_ru(q)                                  # инглиз раскладкасида русча ёзилгани
        if r != q and _CYR.search(r):
            out.append(r)
    if _CYR.search(q):
        e = to_en(q)
        if e != q and _LAT.search(e):
            out.append(e)
    seen, res = set(), []
    for v in out:
        k = v.casefold()
        if k not in seen:
            seen.add(k)
            res.append(v)
    return res


def matches(q, hay):
    """Python'да фильтрлаш учун: q (ёки унинг бошқа раскладкадаги / лотинча варианти) hay ичида борми («_» — ҳар қандай бир ҳарф)."""
    h = (hay or "").casefold().replace("ё", "е")
    for v in variants(q):
        v = v.casefold().replace("ё", "е")
        if "_" in v:
            if re.search(re.escape(v).replace("_", "."), h):
                return True
        elif v in h:
            return True
    return False
