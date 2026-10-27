# -*- coding: utf-8 -*-
"""Базис «Формирование проекта» → XML спецификация (ҳар буюм — алоҳида файл) → битта текшириш ойнаси → битта черновик накладной.

Скрипт (битта очиқ модел) олдингидек ишлайди. XML — бир нечта буюм (kx, tb, gb …) учун: ҳар файл = бир буюм,
`Изделие/Количество` га кўпайтирилади. Ҳисоб скрипт қоидалари бўйича (basis.build_rows), манба — XML:
  • присадка — деталлардаги `Отверстия` (Базис ҳар панелга ҳақиқатда тешиладиган тешикларни ёзади);
  • пазлар — `СписокПазов` (Длина — шу деталдаги пазларнинг жами узунлиги);
  • кромка — `СписокКромок1..4` (тури бўйича), метраж = Σ(томон узунлиги + 40 мм) (эгаси, 27.10);
    `СписокКромокСМЧертеж` — «см. чертёж» қатори (Услуга Овал), метражи ҳам шу қоида билан;
  • `Прямоугольная=N` — фигурали деталь: вырез/ровер нархи қўлда; эни >1190 мм (УТ ва ХДФ эмас) — ровер, тешиклари присадкадан айрилади;
  • фурнитура — номи бўйича жамланади (каталогдаги товарни базисчи танлайди, эслаб қолинади).
Ҳужжат: лойиҳа «claude/bazis-xml-import.md»."""
import io, re, zipfile
import xml.etree.ElementTree as ET

EDGE_ADD_MM = 40          # кромка: ҳар томонга +40 мм (эгаси, 27.10)
CNC_MAX_W = 1190
MAX_FILE = 30 * 1024 * 1024
MAX_ZIP_TOTAL = 120 * 1024 * 1024
HDF_RE = re.compile(r"х\s*д\s*ф|hdf|двп|оргалит", re.I)
UT_RE = re.compile(r"^\s*ут\b|утолщ", re.I)


class XmlError(ValueError):
    pass


def _f(s, d=0.0):
    try:
        return float(str(s).strip().replace(",", ".")) if s not in (None, "") else d
    except ValueError:
        return d


def _n(v):
    """Сонни калит учун: 13.0 -> «13», 12.989 -> «12.989»."""
    v = round(float(v), 3)
    return ("%g" % v) if v else "0"


def _t(e, path):
    return (e.findtext(path) or "").strip()


def _is_hdf(name, thick):
    if re.search(r"зеркал|стекл|акрил", (name or "").casefold()):
        return False
    return bool(HDF_RE.search(name or "")) or 0 < thick <= 4


def files_from_upload(items):
    """[(файл номи, байтлар)] — .xml ёки .zip (ичидаги .xml лар). -> [(ном, байтлар)]"""
    out = []
    for name, raw in items:
        low = (name or "").casefold()
        if low.endswith(".zip"):
            try:
                z = zipfile.ZipFile(io.BytesIO(raw))
            except zipfile.BadZipFile:
                raise XmlError(f"{name}: архив не открылся")
            tot = 0
            for zi in z.infolist():
                if zi.is_dir() or not zi.filename.casefold().endswith(".xml"):
                    continue
                tot += zi.file_size
                if zi.file_size > MAX_FILE or tot > MAX_ZIP_TOTAL:
                    raise XmlError(f"{name}: слишком большой архив")
                out.append((zi.filename.rsplit("/", 1)[-1], z.read(zi)))
        elif low.endswith(".xml"):
            if len(raw) > MAX_FILE:
                raise XmlError(f"{name}: слишком большой файл")
            out.append((name, raw))
        elif name:
            raise XmlError(f"{name}: нужен XML спецификации Базиса (или .zip с ними)")
    if not out:
        raise XmlError("Выберите XML-файлы спецификации из Базиса")
    return out


def parse_item(fname, raw):
    """Битта XML (битта буюм) -> dict (JSON'га сиғадиган)."""
    if b"<!ENTITY" in raw[:4096] or b"<!DOCTYPE" in raw[:4096]:
        raise XmlError(f"{fname}: неподдерживаемый XML")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise XmlError(f"{fname}: не XML ({e})")
    izd = root.find("Изделие") if root.tag == "Проект" else None
    if izd is None:
        raise XmlError(f"{fname}: это не спецификация Базиса (нет «Проект/Изделие»)")
    qty = _f(_t(izd, "Количество"), 1) or 1
    tag = _t(izd, "ОбозначениеВПроекте") or _t(izd, "Артикул")
    name = _t(izd, "Наименование")
    it = dict(file=fname, tag=tag, name=name, order=_t(izd, "Заказ"), proj=root.get("Наименование") or "", qty=qty,
              label=(tag + " — " + name) if tag and name else (tag or name or fname),
              panels={}, edges={}, furn={}, holes={}, holes_nc={}, pl=[], pm=[], fig=[], parts=0)

    def add(d, k, v):
        d[k] = d.get(k, 0) + v

    def panel(o, m):
        m = int(round(m)) if abs(m - round(m)) < 1e-9 else m
        nm = _t(o, "Наименование")
        mat = _t(o, "ОсновнойМатериал/Наименование") or "без материала"
        th = _f(_t(o, "ОбщаяТолщина")) or _f(_t(o, "ОсновнойМатериал/Толщина"))
        L, W = _f(_t(o, "Длина")), _f(_t(o, "Ширина"))
        rect = _t(o, "Прямоугольная").upper() != "N"
        hdf = _is_hdf(mat, th)
        pz = []
        for z in o.findall("СписокПазов/*"):
            ln = _f(_t(z, "Длина"))
            if ln > 0:          # Длина — шу деталдаги шу турдаги пазларнинг жами узунлиги (Количество — бўлаклар сони)
                pz.append(dict(n=(_t(z, "Название") + " " + _t(z, "Обозначение")).strip(), l=round(ln, 1), a=0,
                               pw=_f(_t(z, "Ширина")), pd=_f(_t(z, "Глубина"))))
        k = f"{mat}|{_n(th)}"
        p = it["panels"].setdefault(k, dict(m=mat, t=th, a=0.0, n=0, ap=0.0))
        p["a"] += L * W * m
        p["n"] += m
        if not rect or pz:
            p["ap"] += L * W * m
        it["parts"] += m
        # кромка: ҳар томон (Длина + 40 мм); «см. чертёж» — алоҳида (фигурали)
        ew = eh = ef = eb = 0
        for side in "1234":
            for e in o.findall(f"СписокКромок{side}/Кромка"):
                ln = _f(_t(e, "ДлинаБезСвеса")) or _f(_t(e, "Длина"))
                if ln <= 0:
                    continue
                eb += 1
                if abs(ln - L) < 1.5:
                    eh += 1
                elif abs(ln - W) < 1.5:
                    ew += 1
                else:
                    ef += 1
                _edge(it, e, ln, m, False)
        for e in o.findall("СписокКромокСМЧертеж/Кромка"):
            ln = _f(_t(e, "ДлинаБезСвеса")) or _f(_t(e, "Длина"))
            if ln > 0:
                eb += 1
                ef += 1
                _edge(it, e, ln, m, True)
        # тешиклар
        wide = min(L, W) > CNC_MAX_W and not UT_RE.search(nm) and not hdf
        tgt = it["holes_nc"] if wide else it["holes"]
        for h in o.findall("Отверстия/Отверстие"):
            d = _f(_t(h, "Диаметр"))
            if d <= 0:
                continue
            thr = _t(h, "Тип").casefold().startswith("сквоз")
            dep = _f(_t(h, "Глубина")) or (th if thr else 0)
            face = _f(_t(h, "НаправлениеZ")) != 0
            add(tgt, f"{_n(d)}|{_n(dep)}|{'s' if thr else 'g'}{'z' if face else 'xy'}", m)
        it["pl"].append(dict(it=it["label"], nm=nm, m=mat, t=th, w=round(W, 1), h=round(L, 1), e=(f"{eb} ст." if eb else ""),
                             ew=ew, eh=eh, ef=ef, c=0 if rect else 1, q=m, fig=0 if rect else 1))
        if pz or wide:
            it["pm"].append(dict(it=it["label"], nm=nm, m=mat, w=round(W, 1), h=round(L, 1), cut=[], fb=0, arcs=0, pz=pz, q=m))
        if not rect:
            it["fig"].append(dict(nm=nm, m=mat, w=round(W, 1), h=round(L, 1), q=m))

    def walk(e, mult):
        for c in e.findall("СписокЭлементов/*"):
            q = _f(_t(c, "Количество"), 1)
            if q <= 0:
                q = 1
            typ = _t(c, "ТипОбъекта")
            m = mult * q
            if c.tag == "Объект" and typ == "Панель":
                panel(c, m)
            elif c.tag == "Объект" and typ == "Фурнитура":
                nm = _t(c, "Наименование")
                if nm:
                    add(it["furn"], nm, m)
            walk(c, m)

    walk(izd, qty)
    # бир хил деталлар — битта қатор (сони жамланади); материал, кейин катталиги бўйича
    for key in ("pl", "fig"):
        agg = {}
        for p in it[key]:
            k = tuple((a, json_key(v)) for a, v in sorted(p.items()) if a != "q")
            if k in agg:
                agg[k]["q"] += p["q"]
            else:
                agg[k] = dict(p)
        it[key] = sorted(agg.values(), key=lambda p: ((p.get("m") or "").casefold(), -(p.get("w") or 0) * (p.get("h") or 0), p.get("nm") or ""))
    for p in it["panels"].values():
        p["n"] = int(round(p["n"]))
    it["parts"] = int(round(it["parts"]))
    return it


def json_key(v):
    return tuple(v) if isinstance(v, list) else v


def _edge(it, e, ln, m, fig):
    nm = _t(e, "Наименование") or _t(e, "Обозначение") or "кромка"
    t, w = _f(_t(e, "Толщина")), _f(_t(e, "Ширина"))
    k = f"{nm}|{_n(t)}|{_n(w)}|{'f' if fig else ''}"
    b = it["edges"].setdefault(k, dict(m=nm, t=t, w=w, l=0.0, f=1 if fig else 0, s=0, raw=0.0))
    b["l"] += (ln + EDGE_ADD_MM) * m
    b["raw"] += ln * m
    b["s"] += m


def combine(items):
    """Буюмлар -> basis.build_rows кутадиган маълумот (скрипт форматига ўхшаш) + буюмлар рўйхати."""
    panels, butts, furn = {}, {}, {}
    pl, pm, fig = [], [], []
    for it in items:
        for k, p in it["panels"].items():
            x = panels.setdefault(k, dict(m=p["m"], t=p["t"], a=0.0, n=0, ap=0.0))
            x["a"] += p["a"]; x["n"] += p["n"]; x["ap"] += p["ap"]
        for k, b in it["edges"].items():
            x = butts.setdefault(k, dict(m=b["m"], t=b["t"], w=b["w"], l=0.0, f=b["f"], s=0, raw=0.0))
            x["l"] += b["l"]; x["s"] += b["s"]; x["raw"] += b["raw"]
        for nm, n in it["furn"].items():
            furn[nm] = furn.get(nm, 0) + n
        pl += it["pl"]; pm += it["pm"]; fig += [dict(f, it=it["label"]) for f in it["fig"]]
    tags = [it["tag"] or it["name"] for it in items]
    base = ""
    for it in items:
        b = re.sub(r"\s*\(.*$", "", it["order"] or it["proj"] or "").strip()
        if b:
            base = b
            break
    project = (base + " · " if base else "") + "+".join(t for t in tags if t)
    return dict(v=3, src="xml", project=project[:200], parts=sum(it["parts"] for it in items),
                panels=list(panels.values()), butts=list(butts.values()),
                furn=[dict(m=k, n=int(round(v)), h=None, sc=0) for k, v in sorted(furn.items(), key=lambda x: x[0].casefold())],
                pm=pm, pl=pl, fig=fig, items=items)


def parse_files(files):
    """[(ном, байтлар)] -> [буюм]"""
    return [parse_item(n, raw) for n, raw in files_from_upload(files)]
