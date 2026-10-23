"""Нотўғри клавиатура тилида ёзилган қидирувни тушуниш: «vty [jpbh» -> «мен хозир», «Утптк» -> «Egger».
variants(q) -> [асл матн, кириллга ўгирилгани, лотинга ўгирилгани] (фақат маъноли бўлганлари)."""
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


def variants(q):
    q = (q or "").strip()
    out = [q] if q else []
    if _LAT.search(q):
        r = to_ru(q)
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
    """Python'да фильтрлаш учун: q (ёки унинг бошқа раскладкадаги варианти) hay ичида борми."""
    h = (hay or "").casefold().replace("ё", "е")
    return any(v.casefold().replace("ё", "е") in h for v in variants(q))
