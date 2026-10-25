# -*- coding: utf-8 -*-
"""Распил KPI: распилчилар платформа Telegram-аккаунтига ёзади —
  • смена бошида: «Оператор Жасур, ёрдамчи Улмас» → иш бошланган вақт ёзилади;
  • ҳар накладной: расм (ёки рақами) → 1С'дан распил қаторлари → листга айлантирилади → смена одамларига (иккаласига тўлиқ);
  • охирида: «иш тугади» → кунлик хулоса.
Иш вақти ишчи бошлаган вақтдан 9 соат (8 иш + 1 обед); ундан кейинги листлар 2-смена (KPI «тун» устуни, ставка юқори).
Хабарлар ready.poll орқали келади: ready_workers.role='raspil' рақамлардан, «тайёр» сўзи йўқ хабарлар шу ерга."""
import kb
import os, re, json
from collections import defaultdict
from datetime import datetime, timedelta, date

from flask import render_template, request, redirect, url_for, flash, abort, send_file

A = None

OP_RE = re.compile(r"оператор|аператор|operator|oper\b", re.I)
HELP_RE = re.compile(r"[ёе]рдамчи|йордамчи|yordamchi|yordamch|помощник|ёрдам", re.I)
START_RE = re.compile(OP_RE.pattern + "|" + HELP_RE.pattern + r"|бошла|boshla|начал|старт|start|двух\s*-?\s*пил|dvux\s*pil|dvuh\s*pil", re.I)
DP_RE = re.compile(r"двух\s*-?\s*пил|двухпил|dvux\s*pil|dvuh\s*pil|dvuxpil|икки\s*арра|ikki\s*arra", re.I)
ARRA_RE = re.compile(r"(?<![а-яa-z])(?:арра|arra)(?![а-яa-z])|центр|markaz|марказ", re.I)
EXTRA_RE = re.compile(r"кусок|kusok|столеш|stolesh|45|бурчак|burchak|градус|gradus|угол|угл", re.I)
MACHINES = {"arra": "Арра маркази (NPC 330)", "dp": "Двухпилка (Altendorf WA8)", "pris": "Присадка (ND512D)",
            "kromka": "Кромка (NB536)", "oval": "Овал кромка", "utol": "Утолщенка", "rover": "Ровер (ЧПУ)"}
# 1С накладнойдаги хизмат бўйича ҳисобланадиган станоклар: метраж (м) ёки сумманинг %и (ровер)
WORK = {"kromka": dict(kind="kromka", unit="м", pct=False, rate="kromka_rate", shifts=True, icon="🎞"),
        "oval": dict(kind="oval", unit="м", pct=False, rate="oval_rate", shifts=False, icon="〰️"),
        "utol": dict(kind="utol", unit="м", pct=False, rate="utol_rate", shifts=False, icon="📏"),
        "rover": dict(kind="rover", unit="шт", pct=True, rate="rover_pct", shifts=False, icon="🌀")}
JOB_MACHINES = ("pris",) + tuple(WORK)
SALE = "kus"                                     # кусок сотиш — станок эмас (ойликка кирмайди), лекин мижоз кодига қўшилади


def mlabel(m):
    return "Кусок" if m == SALE else MACHINES.get(m, m or "").split(" (")[0]          # накладной бўйича «бажарилди / %» билан ишлайдиганлар
PRIS_KIND, PRIS_PCT_KIND = "prisadka", "prisadka_pct"
END_RE = re.compile(r"туга[дт]|tuga[dt]|тамом|tamom|законч|кончил|кетдик|ketdik|финиш", re.I)
STOP = {"иш", "ish", "бошладик", "бошладим", "бошладк", "boshladik", "boshladim", "смена", "smena", "ва", "va", "билан", "bilan",
        "и", "мен", "men", "биз", "biz", "бугун", "bugun", "ака", "aka", "салом", "salom", "ассалому", "алайкум", "распил", "raspil"}
DEF = dict(basis_pct="50", basis_pris_pct="5", pris_rate="140", pris_pct="10", raspil_sheet_price="40000", raspil_sheet_cheap="30000", raspil_hdf_cheap="12000", raspil_kusok_m2="5", raspil_shift_hours="9", raspil_m45_rate="2000",
           kromka_rate="0", oval_rate="0", utol_rate="0", rover_pct="0",
           basis_sheet_m2="5.796", basis_hdf_m2="5.796", basis_rate_main="0", basis_rate_hdf="0",
           kroy_m2_main="5.796", kroy_m2_hdf="5.796", kroy_rate_main="0", kroy_rate_hdf="0")
OPEN_HOURS = 20                       # очиқ смена шунча соатдан кейин эскирган ҳисобланади
M45_KIND = "m45"


def init_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS raspil_wcode(code INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, at TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS raspil_kusok(id INTEGER PRIMARY KEY, at TEXT DEFAULT '', rw_id INTEGER, who TEXT DEFAULT '', gid TEXT DEFAULT '',
        type TEXT DEFAULT '', color TEXT DEFAULT '', size TEXT DEFAULT '', m2 REAL NOT NULL DEFAULT 0, owner TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'in', src TEXT DEFAULT 'stock', sold_at TEXT DEFAULT '', sold_by TEXT DEFAULT '', sold_rw INTEGER,
        sold_job INTEGER, note TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS raspil_kroy(id INTEGER PRIMARY KEY, at TEXT NOT NULL, day TEXT NOT NULL, rw_id INTEGER, kw_id INTEGER,
        doc_no TEXT DEFAULT '', iddoc TEXT DEFAULT '', client TEXT DEFAULT '', m2_main REAL NOT NULL DEFAULT 0, m2_hdf REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'ok', note TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS raspil_mgrmsg(code INTEGER NOT NULL, chat INTEGER NOT NULL, msg_id INTEGER, PRIMARY KEY(code, chat));
    CREATE TABLE IF NOT EXISTS raspil_whole(id INTEGER PRIMARY KEY, iddoc TEXT NOT NULL, key TEXT NOT NULL, n REAL NOT NULL DEFAULT 1,
        rw_id INTEGER, at TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS raspil_crew(id INTEGER PRIMARY KEY, day TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT DEFAULT '',
        op_id INTEGER, helper_id INTEGER, by_rw INTEGER, msg_id INTEGER, text TEXT DEFAULT '', cancelled INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS raspil_job(id INTEGER PRIMARY KEY, at TEXT NOT NULL, day TEXT NOT NULL, crew_id INTEGER, op_id INTEGER,
        helper_id INTEGER, rw_id INTEGER, msg_id INTEGER, ack_id INTEGER, text TEXT DEFAULT '', photo TEXT DEFAULT '', ocr TEXT DEFAULT '',
        iddoc TEXT DEFAULT '', doc_no TEXT DEFAULT '', client TEXT DEFAULT '', sheets REAL NOT NULL DEFAULT 0, m45 REAL NOT NULL DEFAULT 0,
        detail TEXT DEFAULT '', status TEXT DEFAULT '', info TEXT DEFAULT '');
    CREATE INDEX IF NOT EXISTS ix_raspil_job_day ON raspil_job(day);
    CREATE INDEX IF NOT EXISTS ix_raspil_job_doc ON raspil_job(iddoc, status);
    CREATE INDEX IF NOT EXISTS ix_raspil_crew_day ON raspil_crew(day);
    CREATE TABLE IF NOT EXISTS k_att(day TEXT NOT NULL, kw INTEGER NOT NULL, in_at TEXT, out_at TEXT DEFAULT '', rw_id INTEGER,
        PRIMARY KEY(day, kw));
    CREATE TABLE IF NOT EXISTS raspil_cash(id INTEGER PRIMARY KEY, at TEXT NOT NULL, rw_id INTEGER NOT NULL, code INTEGER, ref TEXT DEFAULT '',
        amount REAL NOT NULL DEFAULT 0, cur TEXT NOT NULL DEFAULT 'UZS', status TEXT NOT NULL DEFAULT 'held', handed_at TEXT DEFAULT '',
        handed_by TEXT DEFAULT '', note TEXT DEFAULT '');
    CREATE INDEX IF NOT EXISTS ix_raspil_cash_st ON raspil_cash(status, rw_id);
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(ready_workers)")}
    if "role" not in cols:
        con.execute("ALTER TABLE ready_workers ADD COLUMN role TEXT NOT NULL DEFAULT 'ready'")
    if "kw_id" not in cols:
        con.execute("ALTER TABLE ready_workers ADD COLUMN kw_id INTEGER")
    for col, ddl in (("perms", "TEXT"), ("stajer", "INTEGER NOT NULL DEFAULT 0"), ("boss", "INTEGER NOT NULL DEFAULT 0"), ("kroy", "INTEGER NOT NULL DEFAULT 0")):
        if col not in cols:
            con.execute(f"ALTER TABLE ready_workers ADD COLUMN {col} {ddl}")
    jc = {r[1] for r in con.execute("PRAGMA table_info(raspil_job)")}
    for col, ddl in (("items", "TEXT DEFAULT ''"), ("mats", "TEXT DEFAULT ''"), ("sh_main", "REAL NOT NULL DEFAULT 0"), ("sh_hdf", "REAL NOT NULL DEFAULT 0"),
                     ("sh_extra", "REAL NOT NULL DEFAULT 0"), ("machine", "TEXT NOT NULL DEFAULT 'arra'"),
                     ("pr_pcs", "REAL NOT NULL DEFAULT 0"), ("pr_sum", "REAL NOT NULL DEFAULT 0"), ("wcode", "INTEGER")):
        if col not in jc:
            con.execute(f"ALTER TABLE raspil_job ADD COLUMN {col} {ddl}")
    if "res_doc" not in {r[1] for r in con.execute("PRAGMA table_info(raspil_kusok)")}:
        con.execute("ALTER TABLE raspil_kusok ADD COLUMN res_doc TEXT DEFAULT ''")
    if "sold_doc" not in {r[1] for r in con.execute("PRAGMA table_info(raspil_kusok)")}:
        con.execute("ALTER TABLE raspil_kusok ADD COLUMN sold_doc INTEGER")     # платформа накладнойида сотилган (erp_docs.id)
    if "del_doc" not in {r[1] for r in con.execute("PRAGMA table_info(raspil_kusok)")}:
        con.execute("ALTER TABLE raspil_kusok ADD COLUMN del_doc INTEGER")      # инвентаризация (−) билан чиқарилган
    if "ret_doc" not in {r[1] for r in con.execute("PRAGMA table_info(raspil_kusok)")}:
        con.execute("ALTER TABLE raspil_kusok ADD COLUMN ret_doc INTEGER")      # возврат накладнойи билан омборга қайтган
    if "sold_to" not in {r[1] for r in con.execute("PRAGMA table_info(raspil_kusok)")}:
        con.execute("ALTER TABLE raspil_kusok ADD COLUMN sold_to TEXT DEFAULT ''")  # кимга сотилди (харидор)
    if "client" not in {r[1] for r in con.execute("PRAGMA table_info(raspil_wcode)")}:
        con.execute("ALTER TABLE raspil_wcode ADD COLUMN client TEXT DEFAULT ''")
    wcc = {r[1] for r in con.execute("PRAGMA table_info(raspil_wcode)")}
    if "doc_id" not in wcc:
        con.execute("ALTER TABLE raspil_wcode ADD COLUMN doc_id INTEGER")          # платформа накладнойи (черновик) — рақами мижозга код
    if "ref_no" not in wcc:
        con.execute("ALTER TABLE raspil_wcode ADD COLUMN ref_no TEXT DEFAULT ''")  # эски накладнойга қўшимча: ўша накладной рақами
    if "ref_client" not in wcc:
        con.execute("ALTER TABLE raspil_wcode ADD COLUMN ref_client TEXT DEFAULT ''")
    if "machine" not in {r[1] for r in con.execute("PRAGMA table_info(raspil_crew)")}:
        con.execute("ALTER TABLE raspil_crew ADD COLUMN machine TEXT NOT NULL DEFAULT 'arra'")


def cfg(key):
    return A.parse_float(A.get_setting(key, DEF[key]), float(DEF[key]))


def _now():
    return datetime.now()


def _ts(s):
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _n(v):
    v = round(float(v or 0), 2)
    return str(int(v)) if v == int(v) else f"{v:.2f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------- платформа накладнойлари (1С ўчгач, 26.10.2026): каталог товарлари
_CATC = {"at": 0.0, "v": None}


def cat_mode():
    """Цех ботидаги рўйхатлар (кромка, распил, присадка хизматлари) 1С эмас — платформа каталогидан (созлама cat_switch санасидан)."""
    return date.today().isoformat() >= (A.get_setting("cat_switch", "") or "2026-11-01")


def _cat_info():
    """Каталог товари («ci<id>») -> dict(root — бўлим номи: хизматларда «Услуги»дан кейинги папка («Кромка», «Кромка Овал», «Ровер»…),
    товарда юқори папка; sheet — плита омбори (столешница ва кусокдан ташқари)). 5 дақиқа кэш."""
    import time
    if _CATC["v"] is not None and time.time() - _CATC["at"] < 300:
        return _CATC["v"]
    out = {}
    try:
        con = A.db()
        nodes = {r[0]: (r[1], r[2] or "", r[3]) for r in con.execute("SELECT id, parent, name, wh FROM cat_nodes")}
        chain = {}

        def path(nid):
            if nid in chain:
                return chain[nid]
            p, seen = [], set()
            x = nid
            while x in nodes and x not in seen and len(p) < 40:
                seen.add(x); p.insert(0, x); x = nodes[x][0]
            chain[nid] = p
            return p
        for iid, node in con.execute("SELECT id, node FROM cat_items WHERE deleted=0"):
            pth = path(node)
            if not pth:
                continue
            names = [nodes[x][1] for x in pth]
            top = names[0]
            root = names[1] if top.strip().casefold() == "услуги" and len(names) > 1 else top
            wh = next((nodes[x][2] for x in reversed(pth) if nodes[x][2] is not None), None)
            sheet = wh == 1 and not re.match(r"\s*(столеш|остат)", top, re.I)
            out[f"ci{iid}"] = dict(root=root, sheet=sheet)
    except Exception:
        A.errlog.exception("raspil cat info")
    _CATC.update(at=time.time(), v=out)
    return out


def sale_lines(iddoc):
    """Накладной қаторлари (1С ёки платформа «pf:<id>»). Платформа плиталари (каталогда «шт») — «лист» деб ҳисобланади."""
    import c1
    lines = (c1.sale_doc(iddoc) or {}).get("lines") or []
    if str(iddoc or "").startswith("pf:"):
        ci = _cat_info()
        for l in lines:
            x = ci.get(l.get("gid") or "")
            if x and x["sheet"] and not (l.get("unit") or "").lower().startswith(("лист", "кв", "м2")):
                l["unit"] = "лист"
    return lines


def bot_goods():
    """Цех ботидаги рўйхатлар учун товарлар (c1.goods() кўринишида): 1С ўчгач (cat_mode) — платформа каталоги («ci…»/«cn…»)."""
    if not cat_mode():
        import c1
        return c1.goods()
    out = {}
    for r in A.q("SELECT ext, name, unit, price, parent, folder, deleted FROM erp_goods WHERE src='cat'"):
        out[r["ext"]] = dict(id=r["ext"], code="", name=(r["name"] or "").strip(), unit=(r["unit"] or "").strip(), price=r["price"],
                             parent=r["parent"] or "", folder=bool(r["folder"]), deleted=bool(r["deleted"]))
    return out


def _pf_sales_meta(since):
    """Платформада проведен накладнойлар (since санасидан): 1С journal кўринишида ва изоҳлари, мижози.
    -> (docs {«pf:<id>»: dict(id, kind, date, time, no, posted, deleted)}, notes {iddoc: матн}, head {iddoc: «pfc:<мижоз>»},
        cl {«pfc:<мижоз>»: dict(name, phones)})"""
    docs, notes, head, cl = {}, {}, {}, {}
    try:
        for d in A.q("SELECT d.id, d.no, d.date, d.time, d.note, d.client, COALESCE(c.name,'') cname, COALESCE(c.phones,'') phones "
                     "FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.src='pf' AND d.kind='sale' AND d.status='posted' "
                     "AND d.date>=?", (since.isoformat(),)):
            k = f"pf:{d['id']}"
            try:
                dd = date.fromisoformat(d["date"][:10])
            except (TypeError, ValueError):
                continue
            docs[k] = dict(id=k, kind=210, date=dd, time=d["time"] or "", no=str(d["no"] or ""), posted=True, deleted=False)
            notes[k] = (d["note"] or "") + " " + " ".join(r[0] or "" for r in A.q("SELECT note FROM erp_lines WHERE doc_id=?", (d["id"],)))
            head[k] = f"pfc:{d['client']}"
            cl[head[k]] = dict(name=d["cname"], phones=d["phones"])
    except Exception:
        A.errlog.exception("pf sales meta")
    return docs, notes, head, cl


# ---------------------------------------------------------------- 1С накладной → листлар
def calc(lines):
    """Накладнойнинг распил қаторлари → (листлар, 45° метр, тафсилот [(label, qty, unit, sheets, note)])."""
    sheet_cheap, hdf_cheap, kusok = cfg("raspil_sheet_cheap"), cfg("raspil_hdf_cheap"), cfg("raspil_kusok_m2") or 5
    det, total, m45 = [], 0.0, 0.0
    for l in lines:
        n = (l.get("name") or "").lower().replace("ё", "е")
        if not n.startswith("распил"):
            continue
        q = float(l.get("qty") or 0)
        price = float(l.get("price_uzs") or l.get("price") or 0)
        unit = (l.get("unit") or "").lower()
        if "угл" in n or "градус" in n:              # сумма ÷ оддий лист распил нархи = лист
            base = cfg("raspil_sheet_price") or 1
            s = q * price / base
            det.append(("Бурчак/градус", q, "", s, "сумма÷лист нархи"))
            total += s
            continue
        if "45" in n:
            m45 += q
            det.append(("45° / бурчак", q, "м", 0.0, ""))
            continue
        if "кромк" in n:
            det.append(("Кромка распил", q, "", 0.0, "ҳисобланмайди"))
            continue
        if "кусок" in n or "остат" in n or unit.startswith("кв"):
            s = q / kusok
            det.append(("Кусок", q, "м²", s, f"÷{_n(kusok)}"))
        elif "хдф" in n or "двп" in n or "hdf" in n or "лхдф" in n:
            cheap = 0 < price < hdf_cheap
            s = q / (4 if cheap else 2)
            det.append(("ХДФ/ДВП", q, "", s, "арзон, 4=1" if cheap else "2=1"))
        elif "столеш" in n:                           # столешница: 1 дона = 1 лист
            s = q
            det.append(("Столешница", q, "", s, ""))
        else:
            cheap = 0 < price < sheet_cheap
            s = q / (2 if cheap else 1)
            lab = ("Ташқаридан лист" if "л и с т" in n or "ташкар" in n else "Акрил" if "акрил" in n else "Столешница" if "столеш" in n
                   else "МДФ" if "мдф от" in n else "ЛДСП/ЛМДФ" if ("лдсп" in n or "лмдф" in n) else (l.get("name") or "Лист")[7:40])
            det.append((lab, q, "", s, "арзон, 2=1" if cheap else ""))
        total += s
    return round(total, 3), round(m45, 3), det


MAT_RE = re.compile(r"лдсп|лмдф|мдф|хдф|лхдф|двп|дсп|акрил|столеш|кусок|фанер|плит", re.I)


def materials(lines):
    """Накладнойдаги листлар (материал): [(номи, сони, ўлчов)] — распил/кромка/услугасиз."""
    out = []
    for l in lines:
        n = (l.get("name") or "").strip()
        low = n.lower()
        if low.startswith(("распил", "услуга", "кромка")) or not (MAT_RE.search(low) or (l.get("unit") or "").lower().startswith("лист")):
            continue
        out.append((n, float(l.get("qty") or 0), (l.get("unit") or "").strip()))
    return out


def mats_text(js):
    try:
        ms = json.loads(js or "[]")
    except ValueError:
        return ""
    return "; ".join(f"{n} — {_n(q)}{(' ' + u) if u and u != 'лист' else ''}" for n, q, u in ms)


def report_rows(d1, d2, kw=None, q=""):
    sql = "SELECT * FROM raspil_job WHERE day>=? AND day<=?"
    args = [d1, d2]
    if kw:
        sql += " AND (op_id=? OR helper_id=?)"
        args += [kw, kw]
    rows = []
    ql = (q or "").strip().casefold()
    for j in A.q(sql + " ORDER BY at", tuple(args)):
        j = dict(j)
        j["mats_t"] = mats_text(j.get("mats"))
        j["det"] = detail_text(json.loads(j["detail"] or "[]"))
        j["who"] = ("Двухпилка: " if j.get("machine") == "dp" else "") + wname(j["op_id"]) + (f" + {wname(j['helper_id'])}" if j["helper_id"] else "")
        if ql and not kb.matches(ql, " ".join(str(j.get(k) or "") for k in ("doc_no", "client", "mats_t", "who", "text"))):
            continue
        rows.append(j)
    return rows


HDF_RE = re.compile(r"хдф|ҳдф|hdf|xdf|двп|dvp", re.I)
HDF_NEG_RE = re.compile(r"(?:хдф|ҳдф|hdf|xdf|двп|dvp)\s*-?\s*(?:сиз|siz|кейин|keyin|кейинроқ|йў?қ|йук|yo'?q|кесилмади|kesilmadi|қолди|колди|qoldi|qolad|колад|қолад)"
                        r"|без\s+(?:хдф|двп)", re.I)


HDF_ALL_RE = re.compile(r"(?:хдф|ҳдф|hdf|xdf|двп)\s*(?:билан|bilan|ҳам|хам|ham)|ҳаммаси|хаммаси|hammasi|барчаси|barchasi", re.I)


def groups_of(text):
    """ХДФ одатда мижоз олиб кетаётганда кесилади — ёзилмаса ҳисобланмайди.
    «хдф» → фақат ХДФ; «хдф билан / ҳаммаси» → ҳаммаси; бошқа ҳолда (ҳеч нарса, «хдфсиз») — ХДФсиз листлар."""
    t = text or ""
    if HDF_ALL_RE.search(t):
        return ("main", "hdf")
    if HDF_NEG_RE.search(t):
        return ("main",)
    if HDF_RE.search(t):
        return ("hdf",)
    return ("main",)


def grp(label):
    if label == "ХДФ/ДВП":
        return "hdf"
    if label in ("Кусок", "Столешница", "Бурчак/градус", "45° / бурчак"):
        return "extra"                       # одатда двухпилкада кесилади
    return "main"


COUNT_RE = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]5)?)(?![\d.,])")


def part_count(text, doc_no=""):
    """Расм тагига «5 та» / «6241 5» — шу сафар нечта лист кесилди (қисман). -> float | None"""
    t = re.sub(r"(?<!\d)" + re.escape(str(doc_no)) + r"(?!\d)", " ", text or "") if doc_no else (text or "")
    t = re.sub(r"(?<![\d.,])\d{3,}(?![\d.,])", " ", t)
    m = COUNT_RE.search(t)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return v if v > 0 else None


def part_info(det):
    for lab, q, unit, s, note in det:
        if lab == "_part":
            return json.loads(note)
    return None


def detail_text(det):
    out = []
    for lab, q, unit, s, note in det:
        if lab == "_part":
            p = json.loads(note)
            out.append(("фақат ХДФ · " if p.get("only") == "hdf" else "двухпилка · " if p.get("only") == "extra" else "")
                       + f"қисман: накладнойда {_n(p['total'])}, олдин {_n(p['before'])}, қолди {_n(p['left'])}")
            continue
        if lab == "45° / бурчак":
            out.append(f"45°: {_n(q)} м")
        elif lab == "Бурчак/градус":
            out.append(f"Бурчак {_n(q)} дона → {_n(s)}")
        elif note == "ҳисобланмайди":
            continue
        elif note or unit:
            out.append(f"{lab} {_n(q)}{(' ' + unit) if unit else ''} → {_n(s)}" + (f" ({note.split(',')[0]})" if note.startswith("арзон") else ""))
        else:
            out.append(f"{lab} {_n(q)}")
    return " · ".join(out)


# ---------------------------------------------------------------- ишчилар (касса рўйхати) ва исмлар
_TR = [("o'", "ў"), ("o‘", "ў"), ("oʻ", "ў"), ("g'", "ғ"), ("g‘", "ғ"), ("gʻ", "ғ"), ("sh", "ш"), ("ch", "ч"), ("yo", "ё"), ("yu", "ю"),
       ("ya", "я"), ("ye", "е"), ("ts", "ц")]
_TR1 = dict(zip("abdefghijklmnopqrstuvxyz", "абдефгхижклмнопкрстувхйз"))


def norm(s):
    s = (s or "").casefold()
    for a, b in _TR:
        s = s.replace(a, b)
    s = "".join(_TR1.get(ch, ch) for ch in s)
    for a, b in (("ё", "е"), ("ў", "у"), ("қ", "к"), ("ғ", "г"), ("ҳ", "х"), ("ъ", ""), ("ь", ""), ("э", "е")):
        s = s.replace(a, b)
    return re.sub(r"[^a-zа-я0-9]", "", s)


def workers():
    today = date.today().isoformat()
    rows = A.q("SELECT w.id, w.name, COALESCE(w.fio,'') fio, EXISTS(SELECT 1 FROM k_kpi_cols c WHERE c.worker_id=w.id AND c.kind='raspil') has_r FROM k_workers w "
               "WHERE w.active=1 AND (w.fired IS NULL OR w.fired='' OR w.fired>=?) AND COALESCE(w.svc,0)=0", (today,))
    return [dict(r) for r in rows]


def match_name(tok, ws):
    t = norm(tok)
    if len(t) < 3:
        return []
    hit = []
    for w in ws:
        words = [norm(x) for x in re.split(r"[\s()\-.,/]+", w["name"]) if x]
        if any(x == t or (len(t) >= 4 and len(x) >= 4 and (x.startswith(t) or t.startswith(x))) for x in words):
            hit.append(w)
    if len(hit) > 1:
        exact = [w for w in hit if norm(re.split(r"[\s()\-.,/]+", w["name"])[0]) == t]
        hit = exact or hit
    if len(hit) > 1:
        hit = [w for w in hit if w["has_r"]] or hit
    return hit


def parse_crew(text, sender_kw=None):
    """«Оператор Жасур, ёрдамчи Улмас» → (op_id, helper_id, ноаниқ исмлар, бир нечта мос келган)."""
    ws = workers()
    op = helper = None
    role, unknown, amb = "op", [], []
    after_kw = False
    for tok in re.findall(r"[\w'‘ʻ]+", text or ""):
        if OP_RE.fullmatch(tok) or OP_RE.match(tok):
            role, after_kw = "op", True
            continue
        if HELP_RE.match(tok):
            role, after_kw = "helper", True
            continue
        if norm(tok) in {norm(s) for s in STOP} or START_RE.match(tok) or tok.isdigit() or DP_RE.match(tok) or ARRA_RE.fullmatch(tok):
            continue
        hit = match_name(tok, ws)
        if len(hit) == 1:
            wid = hit[0]["id"]
            if role == "op" and op is None:
                op = wid
            elif role == "helper" and helper is None:
                helper = wid
            elif op is None:
                op = wid
            elif helper is None and wid != op:
                helper = wid
            role, after_kw = "helper", False
        elif len(hit) > 1:
            amb.append(tok + " (" + ", ".join(h["name"] for h in hit[:4]) + ")")
        elif after_kw:
            unknown.append(tok)
            after_kw = False
    if op is None and helper is None and sender_kw:
        op = sender_kw
    if op == helper:
        helper = None
    return op, helper, unknown, amb


def wfio(kw):
    r = A.q("SELECT fio FROM k_workers WHERE id=?", (kw,), one=True) if kw else None
    return (r["fio"] or "") if r else ""


def wname(wid):
    if not wid:
        return ""
    r = A.q("SELECT name FROM k_workers WHERE id=?", (wid,), one=True)
    return r["name"] if r else "?"


# ---------------------------------------------------------------- смена
def open_crews(kw=None, rw=None, at=None):
    at = at or _now()
    since = _fmt(at - timedelta(hours=OPEN_HOURS))
    rows = A.q("SELECT * FROM raspil_crew WHERE cancelled=0 AND (ended_at='' OR ended_at IS NULL) AND started_at>=? AND started_at<=? "
               "ORDER BY started_at DESC, id DESC", (since, _fmt(at)))
    out = []
    for r in rows:
        if (not kw and not rw) or (kw and kw in (r["op_id"], r["helper_id"])) or (rw and not kw and r["by_rw"] == rw):
            out.append(dict(r))
    return out


def start_crew(rw, text, msg_id, at=None):
    at = at or _now()
    op, helper, unknown, amb = parse_crew(text, rw.get("kw_id"))
    if amb:
        return "❓ Исм бир нечта ишчига тўғри келди: " + "; ".join(amb) + ". Тўлиқ исмини ёзинг."
    if not op:
        return ("❓ Ишчини танимадим" + (f" ({', '.join(unknown)})" if unknown else "") +
                ". Касса → «Табель ва ойлик»даги исм билан ёзинг: «Оператор Жасур, ёрдамчи Улмас».")
    machine = "dp" if DP_RE.search(text or "") else "arra"
    return start_crew_ids(rw, machine, op, helper, msg_id, at, text, unknown)


def tabel_mark(kw):
    """Ишбай ишчининг табел белгиси: кромкачи — «м», распилчи — «ли»; бошқаси — соат."""
    kinds = {r["kind"] for r in A.q("SELECT kind FROM k_kpi_cols WHERE worker_id=? AND active=1", (kw,))}
    return "м" if "kromka" in kinds else "ли" if ("raspil" in kinds or M45_KIND in kinds) else None


def auto_from():
    """Платформа ойликни шу ойдан бошлаб ўзи ҳисоблайди (ундан олдинги ойлар қўлда)."""
    v = (A.get_setting("auto_from", "2026-11") or "2026-11").strip()
    return v if re.fullmatch(r"\d{4}-\d{2}", v) else "2026-11"


def auto_on(ym):
    return ym[:7] >= auto_from()


def is_test_kw(kw):
    """«Тест …» номли ишчи (синов учун) — табел ва KPI'га автомат ёзилмайди."""
    r = A.q("SELECT name FROM k_workers WHERE id=?", (kw,), one=True) if kw else None
    return bool(r and (r["name"] or "").strip().lower().startswith("тест"))


def mark_tabel(kw, day, mark=None, only_existing=False):
    """Ишчи ишга келди → табелга шу кун белгиси (бўш бўлса): окладдаги ишчига соат (9), ишбайга «ли»/«м»."""
    import kassa
    if not kw or A.get_setting("raspil_tabel", "1") != "1" or not auto_on(day) or is_test_kw(kw):
        return False
    ym, d = day[:7], int(day[8:10])
    kassa.ensure_month(ym)
    db = A.db()
    r = db.execute("SELECT ts, salary FROM k_ws WHERE ym=? AND worker_id=?", (ym, kw)).fetchone()
    if not r and only_existing:
        return False
    if not r:
        sal = (db.execute("SELECT salary FROM k_workers WHERE id=?", (kw,)).fetchone() or [0])[0] or 0
        mx = db.execute("SELECT COALESCE(MAX(sort),0) FROM k_ws WHERE ym=?", (ym,)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO k_ws(ym,worker_id,sort,salary) VALUES(?,?,?,?)", (ym, kw, mx + 10, sal))
        ts, salary = "", sal
    else:
        ts, salary = r[0], r[1]
    cells = kassa.ts_list(ts)
    if str(cells[d - 1]).strip():
        return False                                       # қўлда ёзилган бўлса — тегмаймиз
    hours = kassa.fnum(A.get_setting("kassa_day_hours", "9")) or 9
    mark = mark or tabel_mark(kw)
    cells[d - 1] = (str(int(hours)) if hours == int(hours) else str(hours)) if ((salary or 0) > 0 or not mark) else mark
    db.execute("UPDATE k_ws SET ts=? WHERE ym=? AND worker_id=?", ("|".join(cells), ym, kw))
    db.commit()
    kassa._memo.clear()
    return True


def att_in(kw, rw_id=None, at=None, mark=None):
    """Ишга келди: вақт ва табел. -> (янги_ми, келган вақт)"""
    at = at or _now()
    day = at.date().isoformat()
    r = A.q("SELECT in_at FROM k_att WHERE day=? AND kw=?", (day, kw), one=True)
    if r:
        return False, _ts(r["in_at"])
    A.db().execute("INSERT OR IGNORE INTO k_att(day, kw, in_at, rw_id) VALUES(?,?,?,?)", (day, kw, _fmt(at), rw_id))
    A.db().commit()
    try:
        mark_tabel(kw, day, mark)
    except Exception:
        A.errlog.exception("tabel")
    return True, at


def att_out(kw, at=None):
    at = at or _now()
    r = A.q("SELECT * FROM k_att WHERE kw=? AND in_at>=? ORDER BY in_at DESC LIMIT 1", (kw, _fmt(at - timedelta(hours=OPEN_HOURS))), one=True)
    if not r:
        return None
    A.db().execute("UPDATE k_att SET out_at=? WHERE day=? AND kw=?", (_fmt(at), r["day"], kw))
    A.db().commit()
    return dict(r, out_at=_fmt(at))


def start_crew_ids(rw, machine, op, helper, msg_id=None, at=None, text="", unknown=()):
    at = at or _now()
    if op == helper:
        helper = None
    db = A.db()
    for kw in (op, helper):
        if kw:
            for c in open_crews(kw=kw, at=at):
                db.execute("UPDATE raspil_crew SET ended_at=? WHERE id=?", (_fmt(at), c["id"]))
                if (c.get("machine") or "arra") == machine:
                    continue
                # бошқа станокка ўтди: эски станокда шериги қолса — шериги ўзи давом этади (кетган ишчига энди ёзилмайди)
                rest = [x for x in (c["op_id"], c["helper_id"]) if x and x not in (op, helper)]
                if rest:
                    db.execute("INSERT INTO raspil_crew(day, started_at, op_id, helper_id, by_rw, msg_id, text, machine) VALUES(?,?,?,?,?,?,?,?)",
                               (at.date().isoformat(), _fmt(at), rest[0], rest[1] if len(rest) > 1 else None, c["by_rw"], None,
                                f"{wname(kw)} бошқа станокка ўтди", c.get("machine") or "arra"))
    db.execute("INSERT INTO raspil_crew(day, started_at, op_id, helper_id, by_rw, msg_id, text, machine) VALUES(?,?,?,?,?,?,?,?)",
               (at.date().isoformat(), _fmt(at), op, helper, rw["id"], msg_id, (text or "")[:300], machine))
    db.commit()
    for kw in (op, helper):
        if kw:
            att_in(kw, rw.get("id"), at, "м" if machine in ("kromka", "oval", "utol") else None if machine in WORK else "ли")
    hrs = cfg("raspil_shift_hours")
    if machine in WORK:
        what = "кромка" if machine in ("kromka", "oval") else MACHINES[machine].split(" (")[0].lower()
        return "\n".join([f"✅ {at:%H:%M} — {MACHINES[machine]}: {wname(op)}" + (f" + {wname(helper)}" if helper else ""),
                          f"Энди {what} қилинган накладнойларни ташланг (расм ёки рақам)."])
    if machine == "pris":
        return "\n".join([f"✅ {at:%H:%M} — присадка (ND512D): {wname(op)}" + (f" + {wname(helper)}" if helper else ""),
                          "Энди присадка накладнойларини ташланг."])
    if machine == "dp":
        return "\n".join([f"✅ {at:%H:%M} — двухпилка: {wname(op)}" + (f" + {wname(helper)}" if helper else ""),
                          "Энди столешница, 45°, кусок, бурчак кесилган накладнойларни ташланг (тагига «кесилди» ёки «5 та»).",
                          "Арра марказига қайтганда «Оператор …, ёрдамчи …» деб ёзинг."])
    lines = [f"✅ {at:%H:%M} — иш бошланди (арра маркази).", f"Оператор: {wname(op)}"]
    if helper:
        lines.append(f"Ёрдамчи: {wname(helper)}")
    for kw in (op, helper):
        if kw:
            st = worker_start(kw, at.date().isoformat())
            if st and st < at - timedelta(minutes=1):
                lines.append(f"({wname(kw)} бугун {st:%H:%M} да бошлаган — вақт ўшандан ҳисобланади)")
    if unknown:
        lines.append("⚠️ Танимадим: " + ", ".join(unknown))
    lines.append(f"Иш вақти {_n(hrs)} соат, кейин 2-смена. Энди ҳар бир накладной расмини ташланг, тагига ёзинг: «кесилди» (ХДФсиз), «ҳаммаси» (ХДФ билан), «хдф» ёки «5 та».")
    return "\n".join(lines)


def _leave(db, kw, at, keep_machine=None):
    """Ишчи очиқ экипаждан чиқади; шериги қолса — шериги ўз ролида давом этади (янги бўлак)."""
    for c in open_crews(kw=kw, at=at):
        if keep_machine and (c.get("machine") or "arra") == keep_machine:
            continue
        db.execute("UPDATE raspil_crew SET ended_at=? WHERE id=?", (_fmt(at), c["id"]))
        op = c["op_id"] if c["op_id"] != kw else None
        hp = c["helper_id"] if c["helper_id"] != kw else None
        if op or hp:
            db.execute("INSERT INTO raspil_crew(day, started_at, op_id, helper_id, by_rw, msg_id, text, machine) VALUES(?,?,?,?,?,?,?,?)",
                       (at.date().isoformat(), _fmt(at), op, hp, None, None, f"{wname(kw)} чиқди", c.get("machine") or "arra"))


ROLE_NAMES = {"op": "бош оператор", "hp": "ёрдамчи"}


def perms_of(rw):
    """Ишчига платформада белгиланган доступ: {«arra:op», «kromka:hp», …}. None — белгиланмаган (ҳаммаси очиқ)."""
    r = A.q("SELECT perms, stajer, boss FROM ready_workers WHERE id=?", (rw.get("id"),), one=True) if rw and rw.get("id") else None
    if not r:
        return None
    if r["boss"]:
        return None
    if r["stajer"]:
        return set()
    if r["perms"] is None or r["perms"] == "":
        return None
    try:
        return set(json.loads(r["perms"]))
    except ValueError:
        return set()


def is_stajer(rw):
    r = A.q("SELECT stajer FROM ready_workers WHERE id=?", (rw.get("id"),), one=True) if rw and rw.get("id") else None
    return bool(r and r["stajer"])


def is_boss(rw):
    r = A.q("SELECT boss FROM ready_workers WHERE id=?", (rw.get("id"),), one=True) if rw and rw.get("id") else None
    return bool(r and r["boss"])


def can(rw, machine, role=None):
    p = perms_of(rw)
    if p is None:
        return True
    return f"{machine}:{role}" in p if role else any(x.startswith(machine + ":") for x in p)


def perms_text(p):
    if p is None:
        return "ҳаммаси"
    return ", ".join(f"{mlabel(x.split(':')[0])} — {ROLE_NAMES.get(x.split(':')[1], '')}" for x in sorted(p)) or "йўқ"


def join_crew(rw, machine, role, at=None):
    """Ҳар бир ишчи ўзи киради: станок + роль (op — бош оператор, hp — ёрдамчи). Бир станокда битта бош оператор.
    -> (ok, матн)"""
    at = at or _now()
    kw = rw.get("kw_id")
    if not kw:
        return False, "⚠️ Сиз ишчилар рўйхатига боғланмагансиз. Раҳбарга айтинг."
    if is_stajer(rw):
        return False, "ℹ️ Сиз стажерсиз — станокка ёзилмайсиз, фақат келди-кетди белгиланади."
    if not can(rw, machine, role):
        return False, f"⛔ Сизга «{mlabel(machine)} — {ROLE_NAMES.get(role, role)}» доступи берилмаган. Раҳбарга айтинг."
    db = A.db()
    cur = [c for c in open_crews(at=at) if (c.get("machine") or "arra") == machine]
    cur = cur[0] if cur else None
    if cur and kw in (cur["op_id"], cur["helper_id"]) and ((role == "op") == (cur["op_id"] == kw)):
        return True, crew_text(cur)
    if cur:
        other_op = cur["op_id"] if cur["op_id"] != kw else None
        other_hp = cur["helper_id"] if cur["helper_id"] != kw else None
        if role == "op" and other_op:
            return False, f"⛔ {mlabel(machine)}да бош оператор бор: {wname(other_op)}.\nЁрдамчи бўлиб киринг ёки {wname(other_op)} станокни алмаштирсин."
        if role == "hp" and other_hp:
            return False, f"⛔ {mlabel(machine)}да ёрдамчи бор: {wname(other_hp)}."
    else:
        other_op = other_hp = None
    _leave(db, kw, at)                                    # бошқа станокдаги ишлари ўша станокда қолади
    cur = [c for c in open_crews(at=at) if (c.get("machine") or "arra") == machine]
    if cur:
        db.execute("UPDATE raspil_crew SET ended_at=? WHERE id=?", (_fmt(at), cur[0]["id"]))
        other_op = cur[0]["op_id"] if cur[0]["op_id"] != kw else None
        other_hp = cur[0]["helper_id"] if cur[0]["helper_id"] != kw else None
    op, hp = (kw, other_hp) if role == "op" else (other_op, kw)
    cid = db.execute("INSERT INTO raspil_crew(day, started_at, op_id, helper_id, by_rw, msg_id, text, machine) VALUES(?,?,?,?,?,?,?,?)",
                     (at.date().isoformat(), _fmt(at), op, hp, rw["id"], None, f"{wname(kw)} кирди ({'бош оператор' if role == 'op' else 'ёрдамчи'})",
                      machine)).lastrowid
    db.commit()
    att_in(kw, rw.get("id"), at, "м" if machine in ("kromka", "oval", "utol") else None if machine in WORK else "ли")
    return True, crew_text(A.q("SELECT * FROM raspil_crew WHERE id=?", (cid,), one=True))


def crew_text(c):
    c = dict(c)
    return (f"✅ {mlabel(c.get('machine') or 'arra')}\n👷 Бош оператор: {wname(c['op_id']) if c['op_id'] else '— (ҳали йўқ)'}"
            f"\n🤝 Ёрдамчи: {wname(c['helper_id']) if c['helper_id'] else '—'}")


def end_crew(rw, at=None):
    at = at or _now()
    cs = open_crews(kw=rw.get("kw_id"), rw=rw["id"], at=at)
    if not cs:
        return "❓ Очиқ смена топилмади. Иш бошлаганда «Оператор …, ёрдамчи …» деб ёзинг."
    db = A.db()
    me = rw.get("kw_id")
    if me and any(me in (c["op_id"], c["helper_id"]) for c in cs):
        _leave(db, me, at)                                # фақат ўзи кетади — шериги давом этади
        db.commit()
        att_out(me, at)
        cs = [dict(c, op_id=me if c["op_id"] == me else None, helper_id=me if c["helper_id"] == me else None) for c in cs]
    else:
        for c in cs:
            db.execute("UPDATE raspil_crew SET ended_at=? WHERE id=?", (_fmt(at), c["id"]))
        db.commit()
        for kw in {k for c in cs for k in (c["op_id"], c["helper_id"]) if k}:
            att_out(kw, at)
    day = cs[0]["day"]
    out = [f"🏁 {at:%H:%M} — иш тугади."]
    tot = day_totals(day)
    for kw in {k for c in cs for k in (c["op_id"], c["helper_id"]) if k}:
        t = tot.get(kw)
        if not t:
            continue
        out.append(f"{wname(kw)}: {t['start']:%H:%M}–{t['end']:%H:%M} ({hm(t['hours'])}), лист {_n(t['d'] + t['n'])}"
                   + (f" (2-смена {_n(t['n'])})" if t["n"] else "") + (f", 45° {_n(t['m45'])} м" if t["m45"] else "")
                   + f", накладной {t['docs']}")
    return "\n".join(out)


def hm(h):
    m = int(round(h * 60))
    return f"{m // 60} соат {m % 60:02d} дақ"


def hm_ui(h):
    """Сайт учун: «соат»/«дақ» танланган тилда."""
    import i18n
    m = int(round(h * 60))
    return f"{m // 60} {i18n.text('соат')} {m % 60:02d} {i18n.text('дақ')}"


def worker_start(kw, day):
    r = A.q("SELECT MIN(started_at) s FROM raspil_crew WHERE day=? AND cancelled=0 AND (op_id=? OR helper_id=?)", (day, kw, kw), one=True)
    s = r["s"] if r else None
    if not s:
        r = A.q("SELECT MIN(at) s FROM raspil_job WHERE day=? AND status='ok' AND (op_id=? OR helper_id=?)", (day, kw, kw), one=True)
        s = r["s"] if r else None
    return _ts(s) if s else None


# ---------------------------------------------------------------- накладной
def who_for(rw, machine, at=None):
    """Ким қилди: ишчининг шу станокдаги очиқ экипажи (оператор+ёрдамчи); шу станокда экипажи йўқ бўлса — ёзган ишчининг ўзи.
    Ҳамма ишчи ҳамма станокда ишлайди — иш қайси станокда бўлса, ўша ерда ишлаганга ёзилади. -> crew dict ёки None"""
    m = machine or "arra"
    for c in open_crews(kw=rw.get("kw_id"), rw=rw["id"], at=at):
        if (c.get("machine") or "arra") == m:
            return c
    return None


def crew_for(rw, text="", at=None):
    crews = open_crews(kw=rw.get("kw_id"), rw=rw["id"], at=at)
    if DP_RE.search(text or ""):
        crews = [c for c in crews if c.get("machine") == "dp"] or crews
    elif ARRA_RE.search(text or ""):
        crews = [c for c in crews if c.get("machine", "arra") == "arra"] or crews
    return crews[0] if crews else None


# ---------------------------------------------------------------- листлар бўйича (бот): ишчи нечта кесганини танлайди, чегара бор
HDF_NAME_RE = re.compile(r"хдф|лхдф|двп|hdf", re.I)


def _raspil_kind(n, unit):
    """распил қатори → 'main' | 'hdf' | 'own' | None (двухпилка иши / ҳисобланмайди)."""
    if "угл" in n or "градус" in n or "45" in n or "кромк" in n or "кусок" in n or "остат" in n or "столеш" in n or unit.startswith("кв"):
        return None
    if "хдф" in n or "двп" in n or "hdf" in n:
        return "hdf"
    if "л и с т" in n or "ташкар" in n:
        return "own"
    return "main"


def sheet_items(lines):
    """Накладнойдаги листлар (тугмалар учун) ва распил чегаралари.
    -> (items [dict(key, label, qty, kind)], caps {main, hdf, own}, cheap {main, hdf, own})"""
    sheet_cheap, hdf_cheap = cfg("raspil_sheet_cheap"), cfg("raspil_hdf_cheap")
    caps = {"main": 0.0, "hdf": 0.0, "own": 0.0}
    price = {"main": [], "hdf": [], "own": []}
    items, own_items = [], []
    for l in lines:
        n = (l.get("name") or "").lower().replace("ё", "е")
        unit = (l.get("unit") or "").lower()
        q = float(l.get("qty") or 0)
        if n.startswith("распил"):
            k = _raspil_kind(n, unit)
            if k:
                caps[k] += q
                price[k].append((q, float(l.get("price_uzs") or l.get("price") or 0)))
            if k == "own" and q > 0:                       # мижоз листи: номи — қатор примечаниясида
                note = re.sub(r"\s+", " ", (l.get("note") or "")).strip()
                own_items.append(dict(key=f"o{l.get('no', len(own_items) + 1)}", qty=q, kind="own",
                                      label=("Мижоз листи: " + note) if note else "Мижоз листи (ташқаридан)"))
            continue
        if n.startswith(("услуга", "кромка")) or not unit.startswith("лист") or q <= 0:
            continue
        nm = re.sub(r"\s*\([^)]*\)\s*$", "", l.get("name") or "").strip()
        items.append(dict(key=f"m{l.get('no', len(items) + 1)}", label=nm, qty=q, kind="hdf" if HDF_NAME_RE.search(n) else "main"))
    cheap = {}
    for k, lim in (("main", sheet_cheap), ("hdf", hdf_cheap), ("own", sheet_cheap)):
        ps = sorted(price[k], reverse=True)                # энг кўп сонли қаторнинг нархи
        cheap[k] = bool(ps) and 0 < ps[0][1] < lim
    items += own_items
    for k, lab in (("main", "Лист (накладнойда кўрсатилмаган)"), ("hdf", "ХДФ (накладнойда кўрсатилмаган)")):
        if caps[k] > 0 and not any(i["kind"] == k for i in items):
            items.append(dict(key=f"r{k}", label=lab, qty=caps[k], kind=k))
    return items, caps, cheap


def items_state(iddoc):
    """Ҳар бир лист: неча сотилган, неча кесилган, неча кесса бўлади (лист ва распил кичиги)."""
    import c1
    lines = sale_lines(iddoc)
    items, caps, cheap = sheet_items(lines)
    done, used = {}, {"main": 0.0, "hdf": 0.0, "own": 0.0}
    for r in A.q("SELECT items FROM raspil_job WHERE iddoc=? AND status='ok' AND items<>''", (iddoc,)):
        for k, v in json.loads(r["items"] or "{}").items():
            if isinstance(v, (int, float)) and k != "frac":       # кромка/присадка ишлари («lines», «part») — лист эмас
                done[k] = done.get(k, 0) + v
    kinds = {i["key"]: i["kind"] for i in items}
    for k, v in done.items():
        if k in kinds:
            used[kinds[k]] += v
    # бутун кетадиган листлар: сотилган лист распилдан кўп бўлса, қайси бири бутун кетишини ишчи белгилайди
    whole = {}
    for r in A.q("SELECT key, SUM(n) n FROM raspil_whole WHERE iddoc=? GROUP BY key", (iddoc,)):
        whole[r["key"]] = r["n"]
    need = {}
    for kind in ("main", "hdf"):
        its = [i for i in items if i["kind"] == kind and i["key"][0] == "m"]
        excess = sum(i["qty"] for i in its) - caps[kind]
        if A.get_setting("raspil_ask_whole", "0") == "1" and caps[kind] > 0 and excess > 0.001 and len(its) > 1:   # ишчидан сўралмайди
            need[kind] = max(0.0, round(excess - sum(whole.get(i["key"], 0) for i in its), 3))
    for i in items:
        i["done"] = done.get(i["key"], 0.0)
        i["whole"] = whole.get(i["key"], 0.0)
        i["left"] = max(0.0, min(i["qty"] - i["done"] - i["whole"], caps[i["kind"]] - used[i["kind"]]))
        i["blocked"] = need.get(i["kind"], 0) > 0.001
    return dict(items=items, caps=caps, cheap=cheap, used=used, lines=lines, need=need)


def mark_whole(rw, iddoc, key, undo=False):
    """Ишчи: шу лист бутун кетади (кесилмайди). undo — охирги белгини олиб ташлаш. -> (ok, матн)"""
    if undo:
        r = A.q("SELECT id FROM raspil_whole WHERE iddoc=? ORDER BY id DESC LIMIT 1", (iddoc,), one=True)
        if r:
            A.db().execute("DELETE FROM raspil_whole WHERE id=?", (r["id"],)); A.db().commit()
        return bool(r), "Бутун белгиси олиб ташланди"
    st = items_state(iddoc)
    it = next((i for i in st["items"] if i["key"] == key), None)
    if not it or st["need"].get(it["kind"], 0) <= 0.001:
        return False, "Бутун кетадиган лист белгиланиб бўлган"
    if it["qty"] - it["done"] - it["whole"] < 1 - 1e-9:
        return False, "Бу листдан бутун қолмаган"
    A.db().execute("INSERT INTO raspil_whole(iddoc, key, n, rw_id, at) VALUES(?,?,1,?,?)", (iddoc, key, rw.get("id"), A.now()))
    A.db().commit()
    return True, f"📦 {it['label'][:40]} — 1 таси бутун"


def item_factor(kind, cheap):
    if kind == "hdf":
        return 0.25 if cheap else 0.5
    return 0.5 if cheap else 1.0


def record_item(rw, iddoc, doc_no, client, key, n, photo="", msg_id=None, at=None, who=None, info="бот", machine="arra"):
    """Ишчи «нечта кесилди»ни танлади → иш ёзилади. -> (job_id, None) ёки (None, хато матни)
    who — накладнойсиз ишни улаганда: аввалги иш кимники (op, helper, day, machine, crew_id)."""
    at = at or _now()
    st = items_state(iddoc)
    it = next((i for i in st["items"] if i["key"] == key), None)
    if not it:
        return None, "Бу лист накладнойда топилмади"
    if it.get("blocked"):
        return None, "Аввал қайси лист бутун кетишини белгиланг"
    if n <= 0 or n > it["left"] + 1e-9:
        return None, f"Кўпи билан {_n(it['left'])} та киритса бўлади"
    f = item_factor(it["kind"], st["cheap"][it["kind"]])
    sheets = round(n * f, 3)
    crew = who_for(rw, machine, at)
    op, helper = (crew["op_id"], crew["helper_id"]) if crew else (rw.get("kw_id"), None)
    day = crew["day"] if crew else at.date().isoformat()
    crew_id = crew["id"] if crew else None
    if who:
        op, helper, day, machine, crew_id = who["op_id"], who["helper_id"], who["day"], who["machine"] or "arra", who["crew_id"]
    note = ("арзон, " if st["cheap"][it["kind"]] else "") + ("2=1" if f == 0.5 else "4=1" if f == 0.25 else "")
    det = [(it["label"][:40], n, "", sheets, note.strip(", "))]
    db = A.db()
    jid = db.execute("INSERT INTO raspil_job(machine, at, day, crew_id, op_id, helper_id, rw_id, msg_id, text, photo, iddoc, doc_no, client, "
                     "sheets, m45, detail, status, info, mats, items, sh_main, sh_hdf) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,'ok',?,?,?,?,?)",
                     (machine, _fmt(at), day, crew_id, op, helper, rw["id"],
                      msg_id, f"{it['label'][:60]} — {_n(n)} та", photo or "", iddoc, doc_no, client, sheets,
                      json.dumps(det, ensure_ascii=False), info, json.dumps(materials(st["lines"]), ensure_ascii=False),
                      json.dumps({key: n}), sheets if it["kind"] != "hdf" else 0, sheets if it["kind"] == "hdf" else 0)).lastrowid
    db.commit()
    sync_kpi(day)
    return jid, None


# ---------------------------------------------------------------- накладнойсиз кесилган листлар
WAIT_KINDS = {"main": "ЛДСП/ЛМДФ", "hdf": "ХДФ/ДВП", "own": "Мижоз листи"}


def add_wait(rw, kind, n, note="", at=None, machine=None, code=None):
    """Накладнойсиз иш — KPI'га ҳали ёзилмайди (накладнойга уланганда ёки админ тасдиқлаганда)."""
    at = at or _now()
    crew = who_for(rw, machine, at) if machine else crew_for(rw, at=at)
    op, helper = (crew["op_id"], crew["helper_id"]) if crew else (rw.get("kw_id"), None)
    day = crew["day"] if crew else at.date().isoformat()
    db = A.db()
    jid = db.execute("INSERT INTO raspil_job(machine, at, day, crew_id, op_id, helper_id, rw_id, text, client, sheets, detail, status, info, items) "
                     "VALUES(?,?,?,?,?,?,?,?,?,0,?,'wait',?,?)",
                     (machine or ((crew.get("machine") or "arra") if crew else "arra"), _fmt(at), day, crew["id"] if crew else None, op, helper, rw["id"],
                      (note or "")[:200], (note or "")[:120], json.dumps([(WAIT_KINDS.get(kind, "Иш"), n, "", 0, "накладной кутилмоқда")], ensure_ascii=False),
                      "накладной кутилмоқда", json.dumps({"wait": {"kind": kind, "n": n}}))).lastrowid
    if not code:                                          # янги мижоз — янги код; бошқа станокдаги иш — мижознинг ўша коди
        code = db.execute("INSERT INTO raspil_wcode(job_id, at) VALUES(?,?)", (jid, _fmt(at))).lastrowid
    db.execute("UPDATE raspil_job SET items=?, wcode=? WHERE id=?", (json.dumps({"wait": {"kind": kind, "n": n, "code": int(code)}}), int(code), jid))
    db.commit()
    code_doc_safe(code)                                   # платформада накладной черновиги (рақами — мижоз коди)
    return jid


def code_jobs(code):
    return [dict(r) for r in A.q("SELECT * FROM raspil_job WHERE wcode=? AND status IN ('wait','linked') ORDER BY at", (int(code),))]


def code_status(code):
    """Ишчи «Ц27» ёзса: ҳолати (мижознинг ҳамма станокдаги ишлари). -> матн"""
    js = code_jobs(code)
    if not js:
        return f"❓ {clabel(code)} топилмади."
    lines = [f"{'✅' if j['status'] == 'linked' else '⏳'} {mlabel(j['machine'])} — {wname(j['op_id'])}" for j in js]
    lines += [f"💵 {c_['wname']} пул олган: {cash_fmt(c_['amount'], c_['cur'])}" + (" (кассада)" if c_["status"] == "handed" else "")
              for c_ in cash_for_code(code)]
    if all(j["status"] == "linked" for j in js):
        return f"✅ {clabel(code)} — тўланган. Деталларни бериш мумкин.\n" + "\n".join(lines)
    return f"⛔ {clabel(code)} — ҳали тўланмаган. Деталларни берманг.\n" + "\n".join(lines)


def parse_client(text):
    """Мижоз: «Алишер 1131» ёки «Алишер +998 90 123 11 31» → «Алишер 1131» / «Алишер 901231131». Нотўғри → None."""
    t = (text or "").strip()
    dg = re.sub(r"\D", "", t)
    nm = re.sub(r"[+\d()\-\s.,]+", " ", t).strip()
    if len(dg) < 4 or not re.search(r"[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ]{2,}", nm):
        return None
    ph = dg[-9:] if len(dg) >= 9 else dg[-4:]
    return f"{nm[:1].upper()}{nm[1:40]} {ph}"


def new_code(client="", ref_no="", ref_client=""):
    """Янги мижоз коди (иш ҳали йўқ) — мижоз исми/тел билан; эски накладнойга қўшимча бўлса — ўша рақам."""
    c = A.db().execute("INSERT INTO raspil_wcode(job_id, at, client, ref_no, ref_client) VALUES(NULL,?,?,?,?)",
                       (A.now(), (client or "")[:80], (ref_no or "")[:20], (ref_client or "")[:100])).lastrowid
    A.db().commit()
    code_doc_safe(c, force=True)                          # накладной рақами — мижоз коди
    return c


def code_client(code):
    r = A.q("SELECT client FROM raspil_wcode WHERE code=?", (int(code),), one=True) if code else None
    return (r["client"] or "") if r else ""


def open_codes(hours=10):
    """Бугун очиқ (тўланмаган) кодлар: [(code, «Двухпилка (Жамшид) + Кромка (Улмас)»)] — кейинги станок мижознинг кодини босиб танлайди."""
    since = _fmt(_now() - timedelta(hours=hours))
    out = {}
    paid = {r[0] for r in A.q("SELECT DISTINCT wcode FROM raspil_job WHERE wcode IS NOT NULL AND status='linked' AND at>=?", (since,))}
    for r in A.q("SELECT wcode, machine, op_id FROM raspil_job WHERE wcode IS NOT NULL AND status='wait' AND at>=? ORDER BY at DESC", (since,)):
        if r["wcode"] in paid:                            # 1С'га киритилган — ишчига кўрсатилмайди
            continue
        out.setdefault(r["wcode"], []).append(f"{mlabel(r['machine'])} ({(wname(r['op_id']) or '').split()[0] if wname(r['op_id']) else ''})")
    return [(c, " · ".join(x for x in (code_client(c), " + ".join(dict.fromkeys(v))) if x)) for c, v in list(out.items())[:8]]


# ---------------------------------------------------------------- накладнойсиз иш → платформада накладной черновиги
# Ишчи ёзган заҳоти черновик очилади, рақами — мижоз коди («№7012»). Ишчи ёзганлари изоҳда; мижоз ўрнида «Мижоз танланмаган (цех)»,
# кассир тўлов пайтида (ёки кун охирида) ҳақиқий мижозни танлаб проведен қилади → ишлар шу накладнойга уланади, ишчига «✅ тўланди».
PF_FROM = "2026-10-22 21:00:00"                           # шу вақтдан олдин очилган кодларга черновик автомат очилмайди


def code_row(code):
    return A.q("SELECT * FROM raspil_wcode WHERE code=?", (int(code),), one=True) if code else None


def clabel(code):
    """Мижоз коди кўриниши: черновик бўлса — накладной рақами («№7012»), бўлмаса «Ц27»."""
    if not code:
        return ""
    import erp as E
    if E.test_mode():                                     # тест режими: асосий иш 1С'да — код «Ц27» (1С изоҳига шу ёзилади)
        return f"Ц{code}"
    try:
        r = A.q("SELECT d.no FROM raspil_wcode w JOIN erp_docs d ON d.id=w.doc_id WHERE w.code=? AND d.status<>'deleted'", (int(code),), one=True)
    except Exception:
        r = None
    return f"№{str(r['no']).lstrip('0') or r['no']}" if r else f"Ц{code}"


def code_by_no(no):
    """«№7012» → шу рақамли цех черновиги коди ёки None."""
    n = re.sub(r"\D", "", str(no or "")).lstrip("0")
    if not n:
        return None
    for r in A.q("SELECT w.code, d.no FROM raspil_wcode w JOIN erp_docs d ON d.id=w.doc_id WHERE d.status<>'deleted' ORDER BY w.code DESC LIMIT 300"):
        if str(r["no"] or "").lstrip("0") == n:
            return r["code"]
    return None


def find_sale_no(no):
    """Эски накладной (1С ёки платформа) рақами бўйича — охиргиси. -> erp_docs қатори ёки None"""
    n = re.sub(r"\D", "", str(no or "")).lstrip("0")
    if not n:
        return None
    rows = A.q("SELECT d.*, COALESCE(c.name,'') cname FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.kind='sale' "
               "AND d.status<>'deleted' AND d.date>=? ORDER BY d.date DESC, d.id DESC", ((date.today() - timedelta(days=120)).isoformat(),))
    return next((r for r in rows if (str(r["no"] or "").lstrip("0")) == n), None)


def _wait_desc(j):
    w = json.loads(j["items"] or "{}").get("wait") or {}
    who = wname(j["op_id"]) + (f" + {wname(j['helper_id'])}" if j["helper_id"] else "")
    kind = w.get("kind", "main")
    if kind == "sale":
        what = "кусок: " + "; ".join(f"{p['name']} — {_n(p['m'])} {p.get('unit', 'м²')}" for p in w.get("pick") or [])
    elif kind == "job":
        what = j["text"] or ""
    else:
        what = f"{WAIT_KINDS.get(kind, kind)} — {_n(w.get('n') or 0)} лист" + (f" · {j['text']}" if j["text"] and j["text"] != j["client"] else "")
    return f"{mlabel(j['machine']) if kind != 'sale' else '🧩'} ({who}): {what}".strip(), w


def _rover_good(con):
    r = con.execute("SELECT ext FROM erp_goods WHERE folder=0 AND deleted=0 AND lc LIKE '%ровер%' ORDER BY (COALESCE(cat,'')='svc') DESC, "
                    "COALESCE(price_last,0)>0 DESC, ext LIMIT 1").fetchone()
    return r["ext"] if r else None


def code_doc_sync(code, force=False):
    """Шу коддаги ишлар → накладной черновиги (йўқ бўлса очилади). Кассир ўзгартирган бўлса тегилмайди. -> doc_id ёки None"""
    import erp as E
    w = code_row(code)
    if not w:
        return None
    if E.test_mode() and not w["doc_id"]:                 # тест режими: асосий иш 1С'да (кассир «Ц27» ни изоҳга ёзади) — черновик очилмайди
        return None
    con = A.db()
    js = [dict(r) for r in con.execute("SELECT * FROM raspil_job WHERE wcode=? AND status IN ('wait','linked') ORDER BY at", (int(code),))]
    if not js and not w["doc_id"] and not force:
        return None
    rate = E.day_rate(date.today())[0] or 1.0
    note = [f"Цех (бот) — Ц{int(code)}, накладнойсиз иш. Мижозни танлаб, нархларни текшириб проведен қилинг."]
    if w["ref_no"]:
        note.append(f"Қўшимча: №{w['ref_no']} га" + (f" ({w['ref_client']})" if w["ref_client"] else ""))
    if w["client"]:
        note.append(f"Мижоз (ишчи ёзди): {w['client']}")
    lines, rover = [], _rover_good(con)
    for c_ in cash_for_code(code):
        note.append(f"Ишчи пул олди: {c_['wname']} — {cash_fmt(c_['amount'], c_['cur'])} ({'кассага топширди' if c_['status'] == 'handed' else 'кассага топширилмаган'})")
    for j in js:
        desc, wj = _wait_desc(j)
        note.append(desc)
        for p in wj.get("pick") or []:
            if p.get("unit") == "сўм" and rover:                      # ровер — сумма
                g = con.execute("SELECT cur FROM erp_goods WHERE ext=?", (rover,)).fetchone()
                amt = float(p.get("m") or 0)
                lines.append(dict(good=rover, qty=1, price=round(amt / rate, 2) if g and g["cur"] == 2 else amt, note=(wj.get("note") or "")[:150]))
                continue
            gid = p.get("gid") or ""
            if cat_mode() and gid and not gid.startswith("ci"):          # 1С товари (кусок тури) — каталогдаги нусхаси
                ci = con.execute("SELECT id FROM cat_items WHERE skey=? AND deleted=0", ("c1g:" + gid,)).fetchone()
                gid = f"ci{ci['id']}" if ci else gid
            g = con.execute("SELECT ext, price, price_last FROM erp_goods WHERE ext=? AND folder=0", (gid,)).fetchone()
            if g and p.get("m"):
                lines.append(dict(good=g["ext"], qty=float(p["m"]), price=g["price_last"] or g["price"] or 0, note=(p.get("name") or "")[:150]
                                  if p.get("kid") else ""))
    client = None
    if w["ref_no"]:
        d0 = find_sale_no(w["ref_no"])
        client = d0["client"] if d0 and d0["client"] else None
    client = client or E.nocli_ext(con)
    did = E.save_wait_draft(con, w["doc_id"], client, "\n".join(note), lines)
    if did and did != w["doc_id"]:
        con.execute("UPDATE raspil_wcode SET doc_id=? WHERE code=?", (did, int(code)))
        con.commit()
    return did


def code_doc_safe(code, force=False):
    try:
        return code_doc_sync(code, force)
    except Exception:
        A.errlog.exception("цех черновиги Ц%s", code)
        return None


def pf_link_waits(days=10):
    """Цех черновиги проведен бўлди → ишлар шу накладнойга уланади, ишчига «✅ тўланди». Очиқ кодлар черновиги янгиланади. -> уланганлар"""
    n = 0
    since = _fmt(_now() - timedelta(days=days))
    codes = [r[0] for r in A.q("SELECT DISTINCT wcode FROM raspil_job WHERE wcode IS NOT NULL AND status='wait' AND at>=?", (since,))]
    for code in codes:
        w = code_row(code)
        d = A.q("SELECT d.*, COALESCE(c.name,'') cname FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.id=?",
                (w["doc_id"],), one=True) if w and w["doc_id"] else None
        if d and d["status"] == "posted":
            no = str(d["no"]).lstrip("0") or d["no"]
            for j in A.q("SELECT id, rw_id FROM raspil_job WHERE wcode=? AND status='wait'", (int(code),)):
                ok, txt = link_wait(j["id"], f"pf:{d['id']}", no, d["cname"], by="платформа накладнойи")
                if ok:
                    n += 1
                    _notify(j["rw_id"], f"✅ №{no} — тўланди. Деталларни бериш мумкин.")
                else:
                    A.db().execute("UPDATE raspil_job SET info=? WHERE id=? AND status='wait'", (f"⚠️ №{no}: {txt}"[:200], j["id"]))
                    A.db().commit()
        elif (d and d["status"] == "draft") or (not d and w and (w["at"] or "") >= PF_FROM):
            code_doc_safe(code)                           # эски (шу тизимдан олдинги) кодларга черновик очилмайди
    import erp as E                                       # иш бошқа накладнойга уланган/бекор — бўш цех черновиги ўчирилади
    old = _fmt(_now() - timedelta(hours=12))
    for r in A.q("SELECT w.code, d.id, d.created_at FROM raspil_wcode w JOIN erp_docs d ON d.id=w.doc_id WHERE d.status='draft' AND d.updated_by=? "
                 "AND d.created_at>=?", (E.WAIT_WHO, since)):
        st = {x[0] for x in A.q("SELECT status FROM raspil_job WHERE wcode=?", (r["code"],))}
        if "wait" in st or (not st and (r["created_at"] or "") > old):
            continue
        A.db().execute("UPDATE erp_docs SET status='deleted', updated_at=? WHERE id=?", (A.now(), r["id"]))
        E.log(A.db(), r["id"], "delete", "цех боти: иш бошқа накладнойга уланди ёки бекор қилинди")
        A.db().commit()
    return n


def wait_code(jid):
    r = A.q("SELECT wcode FROM raspil_job WHERE id=?", (jid,), one=True)
    if r and r["wcode"]:
        return r["wcode"]
    r = A.q("SELECT code FROM raspil_wcode WHERE job_id=?", (jid,), one=True)
    return r["code"] if r else None


def waits(rw=None, kw=None, days=10):
    since = _fmt(_now() - timedelta(days=days))
    rows = [dict(r) for r in A.q("SELECT * FROM raspil_job WHERE status='wait' AND at>=? ORDER BY at", (since,))]
    if rw or kw:
        rows = [r for r in rows if (rw and r["rw_id"] == rw) or (kw and kw in (r["op_id"], r["helper_id"]))]
    for r in rows:
        w = json.loads(r["items"] or "{}").get("wait") or {}
        r["wkind"], r["wn"], r["code"] = w.get("kind", "main"), float(w.get("n") or 0), r.get("wcode") or w.get("code")
        r["old"] = _now() - _ts(r["at"]) > timedelta(hours=2)
        r["wclient"] = code_client(r["code"]) if r["code"] else ""
        r["wlabel"] = (("🧩 Кусок сотилди" if r["wkind"] == "sale" else f"{mlabel(r['machine'])} иши" if r["wkind"] == "job"
                        else f"{WAIT_KINDS.get(r['wkind'], '?')} {_n(r['wn'])} та") + (f" — {r['client']}" if r["client"] else ""))
    return rows


def record_dp_rest(rw, iddoc, doc_no, client, who, info, at):
    """Двухпилка (кусок, столешница, 45°, бурчак): накладнойдаги қолган иш тўлиқ — накладнойсиз иш уланганда."""
    import c1
    st = doc_state(iddoc)
    ex, m45 = max(0.0, st["left"]["extra"]), max(0.0, st["left"]["m45"])
    if ex <= 0.001 and m45 <= 0.001:
        return None, "накладнойда двухпилка иши йўқ ёки аввал ҳисобланган"
    lines = sale_lines(iddoc)
    det = [d for d in st["det"] if grp(d[0]) == "extra"] + ([("45° / бурчак", m45, "м", 0, "")] if m45 > 0.001 else [])
    db = A.db()
    jid = db.execute("INSERT INTO raspil_job(machine, at, day, crew_id, op_id, helper_id, rw_id, text, iddoc, doc_no, client, sheets, m45, detail, "
                     "status, info, mats, items, sh_extra) VALUES('dp',?,?,?,?,?,?,?,?,?,?,?,?,?,'ok',?,?,'',?)",
                     (_fmt(at), who["day"], who["crew_id"], who["op_id"], who["helper_id"], rw["id"], "двухпилка", iddoc, doc_no, client,
                      round(ex, 3), round(m45, 3), json.dumps(det, ensure_ascii=False), info, json.dumps(materials(lines), ensure_ascii=False),
                      round(ex, 3))).lastrowid
    db.commit()
    sync_kpi(who["day"])
    return jid, None


def link_wait(jid, iddoc, doc_no, client, by=""):
    r = _link_wait(jid, iddoc, doc_no, client, by)
    if r[0]:
        notify_mgr(wait_code(jid))
    return r


def _link_wait(jid, iddoc, doc_no, client, by=""):
    """Накладнойсиз ишни накладнойга улаш: шу турдаги листларга чегара билан тақсимланади. -> (ok, матн)"""
    j = A.q("SELECT * FROM raspil_job WHERE id=? AND status='wait'", (jid,), one=True)
    if not j:
        return False, "Бу иш аввал уланган ёки бекор қилинган"
    w = json.loads(j["items"] or "{}").get("wait") or {}
    kind, need = w.get("kind", "main"), float(w.get("n") or 0)
    rw = dict(A.q("SELECT * FROM ready_workers WHERE id=?", (j["rw_id"],), one=True) or {"id": j["rw_id"], "kw_id": j["op_id"]})
    who = dict(op_id=j["op_id"], helper_id=j["helper_id"], day=j["day"], machine=j["machine"], crew_id=j["crew_id"])
    if kind == "sale":                                    # кусок сотилди — ойликка кирмайди, фақат «тўланди»
        A.db().execute("UPDATE raspil_job SET status='linked', iddoc=?, doc_no=?, info=? WHERE id=?",
                       (iddoc, doc_no, f"→ №{doc_no}" + (f" ({by})" if by else ""), jid))
        A.db().execute("UPDATE raspil_kusok SET status='sold', sold_at=? WHERE sold_job=? AND status='res'", (A.now(), jid))
        A.db().commit()
        return True, f"№{doc_no}га уланди: кусок"
    if kind == "job":                                     # присадка/кромка/овал/утолщенка/ровер — накладнойдаги шу станок иши тўлиқ
        m = j["machine"]
        tag = f"накладнойсиз иш №{jid} уланди" + (f" ({by})" if by else "")
        if m == "rover":                                  # ровер: накладнойдаги ровер хизмати (сумма солиштирилмайди)
            jid2, err = record_work_lines(rw, m, iddoc, doc_no, client, None, j["photo"], j["msg_id"], _ts(j["at"]), who=who, info=tag)
            if jid2 and w.get("note"):
                A.db().execute("UPDATE raspil_job SET text=? WHERE id=?", (w["note"][:200], jid2)); A.db().commit()
        elif m == "dp":
            jid2, err = record_dp_rest(rw, iddoc, doc_no, client, who, tag, _ts(j["at"]))
        elif m in WORK:
            jid2, err = record_work_lines(rw, m, iddoc, doc_no, client, None, j["photo"], j["msg_id"], _ts(j["at"]), who=who, info=tag)
        else:
            jid2, err = record_pris(rw, iddoc, doc_no, client, j["photo"], j["msg_id"], _ts(j["at"]), None, m, who=who, info=tag)
        if not jid2:
            return False, f"№{doc_no}: {err}"
        A.db().execute("UPDATE raspil_job SET status='linked', iddoc=?, doc_no=?, info=? WHERE id=?",
                       (iddoc, doc_no, f"→ №{doc_no}" + (f" ({by})" if by else ""), jid))
        A.db().commit()
        return True, f"№{doc_no}га уланди: {MACHINES.get(m, m).split(' (')[0].lower()} иши ҳисобланди"
    st = items_state(iddoc)
    got, total = [], 0.0
    for it in [i for i in st["items"] if i["kind"] == kind and i["left"] > 0.001]:
        if need - total <= 0.001:
            break
        x = min(it["left"], need - total)
        jid2, err = record_item(rw, iddoc, doc_no, client, it["key"], x, j["photo"], j["msg_id"], _ts(j["at"]), who=who,
                                info=f"накладнойсиз иш №{jid} уланди" + (f" ({by})" if by else ""))
        if jid2:
            got.append(jid2)
            total += x
    if not got:
        return False, f"№{doc_no}да {WAIT_KINDS.get(kind)} учун кесса бўладиган лист қолмаган"
    A.db().execute("UPDATE raspil_job SET status='linked', iddoc=?, doc_no=?, info=? WHERE id=?",
                   (iddoc, doc_no, f"→ №{doc_no}: {_n(total)} та" + (f" ({by})" if by else ""), jid))
    A.db().commit()
    msg = f"№{doc_no}га уланди: {_n(total)} та"
    if need - total > 0.001:
        msg += f" ({_n(need - total)} таси накладнойда йўқ — ҳисобланмади)"
    return True, msg


def approve_wait(jid, by=""):
    """Накладнойсиз тасдиқлаш (мижоз товол қилган ва ҳ.к.): оддий нарх бўйича ҳисобланади."""
    j = A.q("SELECT * FROM raspil_job WHERE id=? AND status='wait'", (jid,), one=True)
    if not j:
        return False, "Топилмади"
    w = json.loads(j["items"] or "{}").get("wait") or {}
    kind, n = w.get("kind", "main"), float(w.get("n") or 0)
    if kind in ("job", "sale"):
        return False, "Бу иш ҳажми накладнойдан олинади — накладной рақамини киритиб уланг"
    sheets = round(n * item_factor(kind, False), 3)
    A.db().execute("UPDATE raspil_job SET status='ok', sheets=?, sh_main=?, sh_hdf=?, info=?, items=? WHERE id=?",
                   (sheets, sheets if kind != "hdf" else 0, sheets if kind == "hdf" else 0, f"накладнойсиз тасдиқланди ({by})",
                    json.dumps({}), jid))
    A.db().commit()
    sync_kpi(j["day"])
    return True, f"Тасдиқланди: {_n(sheets)} лист"


# ---------------------------------------------------------------- присадка (ND512D)
def pris_mults():
    """Созлама pris_mult: «эксцентрик=2; ...» — бир дона неча тешик."""
    out = {}
    for part in re.split(r"[;\n]", A.get_setting("pris_mult", "эксцентрик=2") or ""):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip().lower()] = float(v.strip().replace(",", "."))
            except ValueError:
                pass
    return out


def pris_calc(lines):
    """Присадка: тешиклар — дона (эксцентрик ×2 ва ҳ.к.), паз 4мм пог.м → дона; фрезеровка ЧПУ ва паз под подсветки — сумманинг %.
    -> (дона, %-сумма, тафсилот [(номи, сони, ўлчов, дона/сумма, изоҳ)])"""
    mult = pris_mults()
    pcs, psum, det = 0.0, 0.0, []
    for l in lines:
        n = (l.get("name") or "").lower().replace("ё", "е")
        if not (n.startswith("услуга сверления") or n.startswith("услуга фрезеров") or "присадк" in n):
            continue
        q = float(l.get("qty") or 0)
        nm = re.sub(r"^услуга\s+(сверления\s+)?", "", l.get("name") or "", flags=re.I).strip()
        nm = re.sub(r"\s+", " ", nm[:1].upper() + nm[1:])
        if "фрезеров" in n or "подсвет" in n:
            amt = float(l.get("uzs") or 0) or q * float(l.get("price_uzs") or 0)
            psum += amt
            det.append((nm, q, (l.get("unit") or "").strip(), amt, "%"))
            continue
        k = next((v for key, v in mult.items() if key and key in n), 1.0)
        pcs += q * k
        det.append((nm, q, (l.get("unit") or "").strip(), q * k, f"×{_n(k)}" if k != 1 else ""))
    return round(pcs, 3), round(psum, 2), det


def _root_name(gid):
    if str(gid or "").startswith("ci"):                  # платформа каталоги: «Кромка», «Кромка Овал», «Ровер», «Утолшенка»…
        return (_cat_info().get(gid) or {}).get("root", "")
    import c1
    g = c1.goods()
    k, seen = gid, 0
    while seen < 10 and g.get(k, {}).get("parent", "").strip() in g:
        k, seen = g[k]["parent"], seen + 1
    return (g.get(k) or {}).get("name", "")


def work_group(line):
    """1С сатри қайси станок иши: kromka / oval / utol / rover / None (товар папкаси бўйича)."""
    root = _root_name(line.get("gid")).lower() if line.get("gid") else ""
    n = (line.get("name") or "").lower()
    unit = (line.get("unit") or "").lower()
    if "ровер" in root or n.startswith("услуга ровер"):
        return "rover"
    if "утол" in root or "обманка" in n:
        return "utol"
    metr = "пог" in unit or unit.strip() in ("м", "м.")
    if "овал" in root:
        return "oval" if metr else None
    if ("кромк" in root or n.startswith("услуга пвх") or n.startswith("кромка")) and metr and "клей" not in n:
        return "kromka"
    return None


def work_calc(machine, lines):
    """Станок иши накладнойда: -> (метр/дона, сумма, тафсилот [(номи, сони, ўлчов, сумма, изоҳ)])."""
    q, amt, det = 0.0, 0.0, []
    for l in lines:
        if work_group(l) != machine:
            continue
        x = float(l.get("qty") or 0)
        a = float(l.get("uzs") or 0) or x * float(l.get("price_uzs") or 0)
        q += x
        amt += a
        det.append((re.sub(r"\s+", " ", l.get("name") or "").strip(), x, (l.get("unit") or "").strip(), a, "%" if WORK[machine]["pct"] else ""))
    return round(q, 3), round(amt, 2), det


def work_lines(machine, lines):
    """Станок иши қаторлари: [(сатр №, номи, сони, ўлчов, сумма)] — кромкачи ҳар бирини алоҳида «тугади» қилади."""
    out = []
    for l in lines:
        if work_group(l) != machine:
            continue
        x = float(l.get("qty") or 0)
        a = float(l.get("uzs") or 0) or x * float(l.get("price_uzs") or 0)
        out.append((int(l.get("no") or 0), re.sub(r"\s+", " ", l.get("name") or "").strip(), x, (l.get("unit") or "").strip(), a))
    return out


ROVER_TOL = 1000                                        # сўм — яхлитлаш учун


def rover_left(iddoc):
    """Накладнойда ҳали ҳисобланмаган ровер хизмати қаторлари."""
    import c1
    wl = work_lines("rover", sale_lines(iddoc))
    done = work_done_lines(iddoc, "rover")
    return [w for w in wl if w[0] not in done]


def wait_ref(jid, iddoc, doc_no, client=""):
    """Накладнойсиз ровер иши қайси накладнойдаги заказга қилинди (менежер 1С'да шунга ёки «6262» изоҳли янги накладнойга киритади)."""
    j = A.q("SELECT items FROM raspil_job WHERE id=?", (jid,), one=True)
    it = json.loads(j["items"] or "{}")
    it.setdefault("wait", {})["ref"] = dict(iddoc=iddoc, no=str(doc_no or ""), client=client or "")
    A.db().execute("UPDATE raspil_job SET items=? WHERE id=?", (json.dumps(it, ensure_ascii=False), jid))
    A.db().commit()


def rover_match(iddoc, amount):
    """Ишчи ёзган ровер суммаси накладнойдаги (ҳали ҳисобланмаган) ровер суммасига тенгми.
    Тенг бўлса -> қаторлар рўйхати (ҳаммаси ёки битта қатор), бўлмаса -> None."""
    import c1
    wl = work_lines("rover", sale_lines(iddoc))
    done = work_done_lines(iddoc, "rover")
    left = [w for w in wl if w[0] not in done]
    if not left or amount <= 0:
        return None
    if abs(sum(w[4] for w in left) - amount) <= ROVER_TOL:
        return [w[0] for w in left]
    for w in left:
        if abs(w[4] - amount) <= ROVER_TOL:
            return [w[0]]
    return None


def work_done_lines(iddoc, machine):
    done = set()
    for r in A.q("SELECT items FROM raspil_job WHERE iddoc=? AND machine=? AND status='ok'", (iddoc, machine)):
        done |= set(json.loads(r["items"] or "{}").get("lines") or [])
    return done


def work_part(iddoc, machine):
    """Қисман бажарилган қаторлар: {сатр №: қилинган метр}."""
    out = defaultdict(float)
    for r in A.q("SELECT items FROM raspil_job WHERE iddoc=? AND machine=? AND status='ok'", (iddoc, machine)):
        for k, v in (json.loads(r["items"] or "{}").get("part") or {}).items():
            out[int(k)] += float(v)
    return dict(out)


def record_work_lines(rw, machine, iddoc, doc_no, client, nos=None, photo="", msg_id=None, at=None, who=None, info="бот", meters=None):
    """Кромка/овал/утолщенка/ровер: танланган қаторлар тугади (nos=None — қолганининг ҳаммаси).
    meters — битта қатордан қанча метр қилинди (қисман); қолгани тугаса — қатор тугади. -> (job_id, None) | (None, сабаб)"""
    import c1
    at = at or _now()
    lines = sale_lines(iddoc)
    wl = work_lines(machine, lines)
    if not wl:
        return None, f"накладнойда {MACHINES.get(machine, machine).split(' (')[0].lower()} иши йўқ"
    done = work_done_lines(iddoc, machine)
    if pris_done(iddoc, machine) > 0.999:
        done = {w[0] for w in wl}
    sel = [w for w in wl if w[0] not in done and (nos is None or w[0] in nos)]
    if not sel:
        return None, "бу аввал ҳисобланган"
    tot_q = sum(w[2] for w in wl) or 1
    pt = work_part(iddoc, machine)
    sel = [(no, nm, max(0.0, x - pt.get(no, 0.0)), u, (a * max(0.0, x - pt.get(no, 0.0)) / x) if x else a) for no, nm, x, u, a in sel]
    part = {}
    if meters is not None and len(sel) == 1:
        no, nm, left, u, a = sel[0]
        m = round(float(meters), 3)
        if m <= 0:
            return None, "метр нотўғри"
        if m < left - 0.001:                              # қисман: қатор ҳали тугамади
            sel = [(no, nm, m, u, a * m / left if left else 0)]
            part = {str(no): m}
    q, amt = sum(w[2] for w in sel), sum(w[4] for w in sel)
    det = [(w[1], w[2], w[3], w[4], "%" if WORK[machine]["pct"] else "") for w in sel]
    crew = who_for(rw, machine, at)
    op, helper = (crew["op_id"], crew["helper_id"]) if crew else (rw.get("kw_id"), None)
    day = crew["day"] if crew else at.date().isoformat()
    cid = crew["id"] if crew else None
    if who:                                              # накладнойсиз иш уланди — ўша ишчига, ўша кунга
        op, helper, day, cid = who["op_id"], who["helper_id"], who["day"], who["crew_id"]
    db = A.db()
    jid = db.execute("INSERT INTO raspil_job(machine, at, day, crew_id, op_id, helper_id, rw_id, msg_id, text, photo, iddoc, doc_no, client, "
                     "sheets, detail, status, info, mats, pr_pcs, pr_sum, items) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,'ok',?,?,?,?,?)",
                     (machine, _fmt(at), day, cid, op, helper, rw["id"], msg_id,
                      MACHINES[machine].split(" (")[0].lower(), photo or "", iddoc, doc_no, client,
                      json.dumps(det, ensure_ascii=False), info, json.dumps(materials(lines), ensure_ascii=False), round(q, 3), round(amt, 2),
                      json.dumps({"lines": [] if part else [w[0] for w in sel], "frac": round(q / tot_q, 4),
                                  **({"part": part} if part else {})}))).lastrowid
    db.commit()
    sync_kpi(day)
    return jid, None


def kromka_tree(machine="kromka"):
    """1С'даги кромка папкалари: {папка id: dict(name, parent, kids[папка], items[(id, номи)])} ва илдизлар.
    kromka — «Кромка» папкалари (овалсиз), oval — «Кромка Овал»."""
    g = bot_goods()
    def ok_root(n):
        n = n.lower()
        return "кромк" in n and "склад" not in n and (("овал" in n) if machine == "oval" else ("овал" not in n))   # «склад» — приход
    if cat_mode():                                        # каталог: «Услуги / Кромка», «Услуги / Кромка Овал» (хизматлар)
        svc = {k for k, v in g.items() if v["folder"] and not v["deleted"] and not v["parent"].strip() and v["name"].casefold() == "услуги"}
        roots = [k for k, v in g.items() if v["folder"] and not v["deleted"] and v["parent"] in svc and ok_root(v["name"])]
    else:
        roots = [k for k, v in g.items() if v["folder"] and not v["deleted"] and not (v["parent"] or "").strip() and ok_root(v["name"])]
    if not roots:
        roots = [k for k, v in g.items() if v["folder"] and not v["deleted"] and ok_root(v["name"]) and v["parent"] not in g]
    tree = {}
    kids = defaultdict(list)
    for k, v in sorted(g.items(), key=lambda kv: kv[1]["name"]):
        kids[v["parent"]].append((k, v))
    def walk(fid):
        node = tree[fid] = dict(name=g[fid]["name"], parent=g[fid]["parent"], kids=[], items=[])
        for k, v in kids.get(fid, []):
            if not v["deleted"]:
                if v["folder"]:
                    node["kids"].append(k)
                    walk(k)
                elif "клей" not in v["name"].lower():
                    node["items"].append((k, v["name"]))
    for r in roots:
        walk(r)
    return tree, roots


_POP = {"at": None, "cnt": {}}


def item_popularity(days=180):
    """1С: товар неча накладнойда сотилган (охирги ярим йил) — кўп ишлатиладиганлар олдинда чиқиши учун. 1 соат кэш."""
    import c1, time
    if _POP["at"] and time.time() - _POP["at"] < 3600:
        return _POP["cnt"]
    since = date.today() - timedelta(days=days)
    jr = c1.journal()
    ok = {k for k, v in jr.items() if v["kind"] == 210 and not v["deleted"] and v["date"] and v["date"] >= since}
    cnt = defaultdict(int)
    for r in c1.table("DT210"):
        if r["IDDOC"] in ok:
            cnt[r["SP216"]] += 1
    try:                                                  # платформа накладнойлари (каталог товарлари)
        for r in A.q("SELECT l.good, COUNT(*) n FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.src='pf' AND d.kind='sale' "
                     "AND d.status<>'deleted' AND d.date>=? GROUP BY l.good", (since.isoformat(),)):
            cnt[r["good"]] += r["n"]
    except Exception:
        pass
    _POP.update(at=time.time(), cnt=dict(cnt))
    return _POP["cnt"]


def kromka_top(machine="kromka", n=8):
    tree, _ = kromka_tree(machine)
    pop = item_popularity()
    items = [(k, nm) for node in tree.values() for k, nm in node["items"]]
    return [x for x in sorted(items, key=lambda x: -pop.get(x[0], 0)) if pop.get(x[0], 0) > 0][:n]


def unit_short(u):
    u = (u or "").lower()
    return "м²" if "кв" in u else "м" if ("пог" in u or u.strip() in ("м", "м.")) else "та"


def raspil_services(machine="dp"):
    """Накладнойсиз иш учун 1С хизматлари: dp — «Распил ОБЫЧНЫЙ», pris — «9 Присадка». [(id, номи, бирлик)];
    ЧПУ (обработка) — сумма киритилади (бирлик «сўм»)."""
    g = bot_goods()
    if machine == "pris":
        fol = {k for k, v in g.items() if v["folder"] and not v["deleted"] and "присадк" in v["name"].lower()}
    elif machine == "utol":                               # «8 Утолщенка»: клей, пистолет…
        fol = {k for k, v in g.items() if v["folder"] and not v["deleted"] and re.search(r"утол[щш]", v["name"].lower())}
    else:
        fol = {k for k, v in g.items() if v["folder"] and not v["deleted"] and "распил" in v["name"].lower() and "обычн" in v["name"].lower()
               and "пакет" not in v["name"].lower()}
    pop = item_popularity()
    its = [(k, v["name"], "м" if machine == "utol" else "сўм" if "чпу" in v["name"].lower() else unit_short(v["unit"]))
           for k, v in g.items() if v["parent"] in fol and not v["folder"] and not v["deleted"]]
    return sorted(its, key=lambda x: (-pop.get(x[0], 0), x[1]))


def kusok_types():
    """1С «Остатки (кусок)» папкасидаги кв.м товарлар (ЛДСП кусок, ЛМДФ кусок…) — кўп сотилгани олдинда."""
    import c1
    g = c1.goods()
    roots = {k for k, v in g.items() if v["folder"] and "остат" in v["name"].lower()}
    pop = item_popularity()
    its = [(k, v["name"]) for k, v in g.items() if v["parent"] in roots and not v["folder"] and not v["deleted"] and "кв" in (v["unit"] or "").lower()]
    return sorted(its, key=lambda x: -pop.get(x[0], 0))


def parse_size(text):
    """«2800 1000 кашемир», «2,8х1», «2800*1000» → (м², «2800×1000», ранги)."""
    nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", text or "")][:2]
    if len(nums) < 2:
        return None
    a, b = nums
    m2 = a * b / 1e6 if max(a, b) > 50 else a * b
    rest = re.sub(r"\d+(?:[.,]\d+)?|[xх×*\-/]", " ", text or "", flags=re.I)
    return round(m2, 3), f"{_n(a)}×{_n(b)}", re.sub(r"\s+", " ", rest).strip()


def parse_kus(text):
    """«400 1500 10 кашемир» → {m: бир дона м², s, c: ранги, q: сони}. Нотўғри → None."""
    nums = re.findall(r"\d+(?:[.,]\d+)?", text or "")
    ps = parse_size(text)
    if not ps:
        return None
    q = int(float(nums[2].replace(",", "."))) if len(nums) >= 3 else 1
    q = q if 1 <= q <= 50 else 1
    return dict(m=ps[0], s=ps[1], c=ps[2], q=q)


def kus_color(text):
    """Ранг матни: камида 2 та ҳарф бўлиши керак."""
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:60] if re.search(r"[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ]{2,}", t) else None


OWNER_CO = "РайГаз"                                  # ишхонага тегишли кусок


def parse_owner(text):
    """«РайГаз» / «ишхона» → РайГаз; «Алишер 1131», «Алишер +998901231131» → «Алишер 1131». Нотўғри → None."""
    t = (text or "").strip()
    if re.search(r"рай\s*газ|raygaz|raigaz|ишхона|ishxona|цех|sex", t, re.I):
        return OWNER_CO
    dg = re.sub(r"\D", "", t)
    nm = re.sub(r"[+\d()\-\s.,]+", " ", t).strip()
    if len(dg) < 4 or not re.search(r"[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ]{2,}", nm):
        return None
    return f"{nm[:1].upper()}{nm[1:30]} {dg[-4:]}"


def kusok_label(k, owner=True):
    t = " ".join(x for x in (k["type"], k["color"], k["size"]) if x)
    return t + (f" · {k['owner']}" if owner and k["owner"] else "")


def kusok_add(rw, gid, typ, color, size, m2, owner, status="in", src="stock"):
    rw = rw or {}
    kid = A.db().execute("INSERT INTO raspil_kusok(at, rw_id, who, gid, type, color, size, m2, owner, status, src) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (A.now(), rw.get("id"), wname(rw.get("kw_id")) or rw.get("name") or "", gid or "", typ or "", color or "", size or "",
                          float(m2 or 0), owner or "", status, src)).lastrowid
    A.db().commit()
    return kid


def kusok_stock(gid=None):
    sql, a = "SELECT * FROM raspil_kusok WHERE status='in'", []
    if gid:
        sql += " AND gid=?"; a.append(gid)
    return [dict(r) for r in A.q(sql + " ORDER BY at DESC", a)]


def kusok_sell(kid, jid, rw=None):
    """Кусок мижоз кодига (накладнойсиз иш) қўшилади ва омбордан чиқади. -> picks"""
    k = A.q("SELECT * FROM raspil_kusok WHERE id=?", (kid,), one=True)
    if not k or not (k["status"] == "in" or (k["status"] in ("res", "sold") and not k["sold_job"] and not k["res_doc"])):
        return None                                       # брон қилинган/сотилган кусокни бошқа одам сота олмайди
    rw = rw or {}
    A.db().execute("UPDATE raspil_kusok SET status='res', sold_at=?, sold_by=?, sold_rw=?, sold_job=? WHERE id=?",
                   (A.now(), wname(rw.get("kw_id")) or rw.get("name") or "", rw.get("id"), jid, kid))
    A.db().commit()
    return wait_add_item(jid, k["gid"], kusok_label(dict(k)), k["m2"], "м²", kid=kid)


def kusok_reserve(kid, doc_no, rw=None):
    """Омбордаги кусок накладнойга (крой) брон: бошқа ҳеч ким сота олмайди. 1С'да шу № проведен бўлса — сотилди."""
    no = re.sub(r"\D", "", str(doc_no or "")).lstrip("0")
    if not no:
        return False, "Накладной рақами керак"
    rw = rw or {}
    c = A.db().execute("UPDATE raspil_kusok SET status='res', res_doc=?, sold_at=?, sold_by=?, sold_rw=? WHERE id=? AND status='in'",
                       (no, A.now(), wname(rw.get("kw_id")) or rw.get("name") or "", rw.get("id"), kid))
    A.db().commit()
    return (True, f"№{no}га брон қилинди") if c.rowcount else (False, "Бу кусок омборда йўқ (сотилган ёки брон)")


def kusok_return(kid, rw=None):
    """Брон/сотув бекор — кусок омборга қайтади. Ц-код ишидан ҳам олиб ташланади."""
    k = A.q("SELECT * FROM raspil_kusok WHERE id=?", (kid,), one=True)
    if not k or k["status"] != "res":
        return False, "Қайтариб бўлмайди (сотилган ёки йўқ)"
    rw = rw or {}
    who = wname(rw.get("kw_id")) or rw.get("name") or ""
    if k["sold_job"]:
        j = A.q("SELECT * FROM raspil_job WHERE id=?", (k["sold_job"],), one=True)
        if j and j["status"] == "wait":
            it = json.loads(j["items"] or "{}")
            w = it.setdefault("wait", {})
            w["pick"] = [x for x in w.get("pick") or [] if x.get("kid") != kid]
            txt = "; ".join(f"{x['name']} — {_n(x['m'])} {x.get('unit', 'м')}" for x in w["pick"])
            if w["pick"] or j["machine"] != SALE:
                A.db().execute("UPDATE raspil_job SET items=?, text=?, client=? WHERE id=?",
                               (json.dumps(it, ensure_ascii=False), txt[:500], txt[:120], j["id"]))
            else:
                A.db().execute("UPDATE raspil_job SET items=?, text=?, status='cancelled', info=? WHERE id=?",
                               (json.dumps(it, ensure_ascii=False), "", f"кусок қайтарилди: {who}", j["id"]))
    note = ((k["note"] or "") + f" ↩️ {A.now()[5:16]} {who}" + (f" (№{k['res_doc']})" if k["res_doc"] else "")).strip()
    A.db().execute("UPDATE raspil_kusok SET status='in', src='stock', res_doc='', sold_at='', sold_by='', sold_rw=NULL, sold_job=NULL, note=? WHERE id=?",
                   (note[-300:], kid))
    A.db().commit()
    if k["sold_job"]:
        try:
            notify_mgr(wait_code(k["sold_job"]))
        except Exception:
            A.errlog.exception("kusok return notify")
    return True, "↩️ Омборга қайтарилди"


def kusok_same(kid, n=None):
    """Шу кусок билан бир хил (тури, ранги, ўлчами, эгаси, ҳолати, брони) кусоклар id лари — аввал ўзи. n — нечтаси."""
    k = A.q("SELECT * FROM raspil_kusok WHERE id=?", (kid,), one=True)
    if not k:
        return []
    ids = [r["id"] for r in A.q("SELECT id FROM raspil_kusok WHERE status=? AND gid=? AND color=? AND size=? AND owner=? "
                                "AND COALESCE(res_doc,'')=? AND COALESCE(sold_job,0)=? ORDER BY id",
                                (k["status"], k["gid"], k["color"], k["size"], k["owner"], k["res_doc"] or "", k["sold_job"] or 0))]
    ids = [kid] + [i for i in ids if i != kid] if kid in ids else ids
    return ids[:n] if n else ids


def kusok_groups(rows):
    """Бир хил кусоклар битта қаторга: [(намуна, сони, жами м²)]."""
    out = {}
    for k in rows:
        key = (k["gid"], k["color"], k["size"], k["owner"], k.get("res_doc") or "", k.get("sold_job") or 0)
        if key in out:
            out[key][1] += 1; out[key][2] += k["m2"]
        else:
            out[key] = [k, 1, k["m2"]]
    return list(out.values())


def kusok_catalog(con=None):
    """Бот кусок омбори → каталог «Остатки (кусок)» товарлари: {«ci<id>»: dict(m2 — РайГаз омборда, own[], cli[], res[])}.
    Каталогдаги кусок қолдиғи шу ердан (эгаси: ҳамма кусок «Остатки кусок» ичида; ишхонаники — РайГаз, мижозларники алоҳида)."""
    con = con or A.db()
    out = {}
    try:
        rows = con.execute("SELECT k.*, i.id cid FROM raspil_kusok k JOIN cat_items i ON i.skey='c1g:'||k.gid "
                           "WHERE k.status IN ('in','res') ORDER BY k.at DESC").fetchall()
    except Exception:
        return out
    for k in rows:
        o = out.setdefault(f"ci{k['cid']}", dict(m2=0.0, n=0, own=[], cli=[], res=[]))
        it = dict(color=k["color"] or "", size=k["size"] or "", m2=round(k["m2"] or 0, 3), owner=k["owner"] or "", at=(k["at"] or "")[:10])
        if k["status"] == "res":
            o["res"].append(it)
        elif (k["owner"] or "") == OWNER_CO:
            o["own"].append(it); o["m2"] += k["m2"] or 0; o["n"] += 1
        else:                                             # мижозники ҳам сотилади (кимники кимга сотилгани ёзилади)
            o["cli"].append(it); o["m2"] += k["m2"] or 0; o["n"] += 1
    try:                                                  # охирги 60 кунда сотилганлар: кимдан → кимга
        for k in con.execute("SELECT k.*, i.id cid FROM raspil_kusok k JOIN cat_items i ON i.skey='c1g:'||k.gid "
                             "WHERE k.status='sold' AND k.sold_at>=? ORDER BY k.sold_at DESC LIMIT 300",
                             ((datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d"),)).fetchall():
            o = out.setdefault(f"ci{k['cid']}", dict(m2=0.0, n=0, own=[], cli=[], res=[]))
            o.setdefault("sold", []).append(dict(color=k["color"] or "", size=k["size"] or "", m2=round(k["m2"] or 0, 3),
                                                 owner=k["owner"] or "", to=k["sold_to"] or "", by=k["sold_by"] or "", at=(k["sold_at"] or "")[:10]))
    except Exception:
        pass
    for o in out.values():
        o["m2"] = round(o["m2"], 3)
        o.setdefault("sold", [])
    return out


def kusok_note_parse(note):
    """Платформа қаторининг изоҳи: «Кашемир 1000×500 Азиз 1111» → (ранги, ўлчами, эгаси). Эгаси бўлмаса — РайГаз."""
    t = re.sub(r"\s+", " ", note or "").strip()
    size = ""
    m = re.search(r"(\d{2,4}(?:[.,]\d+)?)\s*[xх×*]\s*(\d{2,4}(?:[.,]\d+)?)", t, re.I)
    if m:
        size = f"{_n(float(m.group(1).replace(',', '.')))}×{_n(float(m.group(2).replace(',', '.')))}"
        t = (t[:m.start()] + " " + t[m.end():]).strip()
    owner = OWNER_CO
    if re.search(r"рай\s*газ|raygaz|raigaz|ишхона|ishxona", t, re.I):
        t = re.sub(r"рай\s*газ|raygaz|raigaz|ишхона|ishxona", " ", t, flags=re.I)
    else:
        mo = re.search(r"([A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ][A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ'ʼ\- ]{1,30}?)\s*\+?[\d\s\-()]{4,}$", t)
        if mo:
            ow = parse_owner(t[mo.start():])
            if ow:
                owner, t = ow, t[:mo.start()]
    color = re.sub(r"\s+", " ", re.sub(r"[·,;]+", " ", t)).strip()[:60]
    return color, size, owner


def kusok_desc(k):
    return " ".join(x for x in (k.get("color"), k.get("size")) if x) + (f" · {k['owner']}" if k.get("owner") else "")


def _kus_gid(con, good):
    if not (good or "").startswith("ci") or not good[2:].isdigit():
        return None
    r = con.execute("SELECT skey FROM cat_items WHERE id=?", (int(good[2:]),)).fetchone()
    return r["skey"][4:] if r and (r["skey"] or "").startswith("c1g:") else None


def kusok_doc_sync(con, doc_id):
    """Платформа ↔ бот кусок омбори (битта рўйхат, қайта киритиш шарт эмас):
    • Поступление / Инвентаризация (+) проведен — кусок омборга тушади (ранги, ўлчами, эгаси — қатор изоҳидан); бекор — чиқади.
    • Накладной проведен — кусок «сотилди» (брон → м² мос → РайГаз → мижозларники), қатор изоҳига қайси кусок ёзилади;
      бекор/ўчирилди — омборга қайтади. Возврат проведен — шу кусоклар омборга қайтади, бекор — яна «сотилди»."""
    d = con.execute("SELECT d.id, d.no, d.kind, d.status, d.ref_doc, COALESCE(c.name,'') cname FROM erp_docs d "
                    "LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.id=?", (doc_id,)).fetchone()
    if not d:
        return 0
    posted = d["status"] == "posted"
    if d["kind"] in ("receipt", "inv"):
        src0 = f"pf:{doc_id}:"
        if not posted:
            con.execute("UPDATE raspil_kusok SET status='del' WHERE src LIKE ? AND status='in'", (src0 + "%",))
            con.execute("UPDATE raspil_kusok SET status='in', del_doc=NULL WHERE del_doc=? AND status='del'", (doc_id,))
            return 0
        n = 0
        for l in con.execute("SELECT lineno, good, qty, note FROM erp_lines WHERE doc_id=?", (doc_id,)).fetchall():
            gid = _kus_gid(con, l["good"])
            if not gid or not l["qty"]:
                continue
            if l["qty"] < 0:                                   # инвентаризация: кам чиқди — изоҳдаги кусок (ёки энг мосини) чиқарамиз
                if con.execute("SELECT 1 FROM raspil_kusok WHERE del_doc=? AND gid=?", (doc_id, gid)).fetchone():
                    continue
                color, size, owner = kusok_note_parse(l["note"])
                need = -float(l["qty"])
                rest = [dict(r) for r in con.execute("SELECT * FROM raspil_kusok WHERE gid=? AND status='in'", (gid,))]
                rest.sort(key=lambda k: (not (size and k["size"] == size), not (color and color.lower() in (k["color"] or "").lower()),
                                         abs((k["m2"] or 0) - need)))
                for k in rest:
                    if need <= 0.005:
                        break
                    con.execute("UPDATE raspil_kusok SET status='del', del_doc=? WHERE id=?", (doc_id, k["id"]))
                    need -= k["m2"] or 0; n += 1
                continue
            color, size, owner = kusok_note_parse(l["note"])
            src = f"{src0}{l['lineno']}"
            ex = con.execute("SELECT id FROM raspil_kusok WHERE src=?", (src,)).fetchone()
            typ = (con.execute("SELECT name FROM cat_items WHERE skey=?", ("c1g:" + gid,)).fetchone() or {"name": ""})["name"]
            if ex:
                con.execute("UPDATE raspil_kusok SET gid=?, type=?, color=?, size=?, m2=?, owner=?, status=CASE WHEN status='del' THEN 'in' ELSE status END "
                            "WHERE id=?", (gid, typ, color, size, round(float(l["qty"]), 3), owner, ex["id"]))
            else:
                con.execute("INSERT INTO raspil_kusok(at, who, gid, type, color, size, m2, owner, status, src) VALUES(?,?,?,?,?,?,?,?, 'in', ?)",
                            (A.now(), f"{'Поступление' if d['kind'] == 'receipt' else 'Инвентаризация'} №{d['no']}", gid, typ, color, size,
                             round(float(l["qty"]), 3), owner, src))
            n += 1
        return n
    if d["kind"] != "sale":
        return 0
    if d["ref_doc"]:                                          # возврат
        if not posted:
            con.execute("UPDATE raspil_kusok SET status='sold', ret_doc=NULL WHERE ret_doc=? AND status='in'", (doc_id,))
            return 0
        n = 0
        for l in con.execute("SELECT good, SUM(qty) q FROM erp_lines WHERE doc_id=? GROUP BY good", (doc_id,)).fetchall():
            gid = _kus_gid(con, l["good"])
            if not gid or (l["q"] or 0) >= 0:
                continue
            back = -float(l["q"]) - float(con.execute("SELECT COALESCE(SUM(m2),0) FROM raspil_kusok WHERE ret_doc=? AND gid=?",
                                                      (doc_id, gid)).fetchone()[0] or 0)
            for k in con.execute("SELECT * FROM raspil_kusok WHERE sold_doc=? AND gid=? AND status='sold' ORDER BY m2 DESC",
                                 (d["ref_doc"], gid)).fetchall():
                if back <= 0.005:
                    break
                con.execute("UPDATE raspil_kusok SET status='in', ret_doc=? WHERE id=?", (doc_id, k["id"]))
                back -= k["m2"] or 0; n += 1
        return n
    if not posted:                                            # қайтариш: шу накладной билан сотилганлар омборга (брон бўлса — бронга)
        con.execute("UPDATE raspil_kusok SET status=CASE WHEN COALESCE(res_doc,'')<>'' THEN 'res' ELSE 'in' END, sold_doc=NULL, "
                    "sold_at='', sold_by='', sold_to='' WHERE sold_doc=? AND status='sold'", (doc_id,))
        return 0
    n = 0
    for l in con.execute("SELECT l.good, SUM(l.qty) q FROM erp_lines l WHERE l.doc_id=? GROUP BY l.good", (doc_id,)).fetchall():
        gid = _kus_gid(con, l["good"])
        if not gid or (l["q"] or 0) <= 0:
            continue
        have = con.execute("SELECT COALESCE(SUM(m2),0) FROM raspil_kusok WHERE sold_doc=? AND gid=? AND status='sold'", (doc_id, gid)).fetchone()[0]
        try:                                                  # цех ботида сотилган (накладнойсиз иш шу накладнойнинг цех коди) — қайта сотилмайди
            have = float(have or 0) + float(con.execute(
                "SELECT COALESCE(SUM(k.m2),0) FROM raspil_kusok k JOIN raspil_job j ON j.id=k.sold_job JOIN raspil_wcode w ON w.code=j.wcode "
                "WHERE w.doc_id=? AND k.gid=? AND k.status IN ('sold','res') AND COALESCE(k.sold_doc,0)<>?", (doc_id, gid, doc_id)).fetchone()[0] or 0)
        except Exception:
            pass
        need = round(float(l["q"]) - float(have or 0), 3)
        if need > 0.005:
            cand = [dict(r) for r in con.execute(
                "SELECT * FROM raspil_kusok WHERE gid=? AND ((status='res' AND res_doc=?) OR status='in') "
                "ORDER BY (status='res') DESC, (owner=?) DESC, at", (gid, str(d["no"]), OWNER_CO))]   # РайГаз биринчи, кейин мижозларники
            pick = [k for k in cand if k["status"] == "res"]           # шу накладнойга брон қилинганлар — биринчи
            need -= sum(k["m2"] or 0 for k in pick)
            rest = [k for k in cand if k["status"] == "in"]
            if need > 0.005 and rest:
                exact = sorted([k for k in rest if abs((k["m2"] or 0) - need) <= 0.01], key=lambda k: k["owner"] != OWNER_CO)
                if exact:
                    pick.append(exact[0]); need = 0
                else:
                    for k in sorted(rest, key=lambda k: (k["owner"] != OWNER_CO, -(k["m2"] or 0))):
                        if need <= 0.005:
                            break
                        if (k["m2"] or 0) <= need + 0.01:
                            pick.append(k); need -= k["m2"] or 0
                    if need > 0.005:
                        big = [k for k in rest if k not in pick and (k["m2"] or 0) >= need]
                        if big:
                            pick.append(min(big, key=lambda k: k["m2"] or 0)); need = 0
            for k in pick:
                con.execute("UPDATE raspil_kusok SET status='sold', sold_doc=?, sold_at=?, sold_by=?, sold_to=? WHERE id=?",
                            (doc_id, A.now(), f"накладной №{d['no']}", d["cname"][:80], k["id"]))
                n += 1
        # қатор изоҳи: қайси кусок (ранги, ўлчами, кимники) — изоҳ бўш бўлса
        desc = "; ".join(kusok_desc(dict(k)) for k in con.execute("SELECT * FROM raspil_kusok WHERE sold_doc=? AND gid=? AND status='sold'",
                                                                  (doc_id, gid)).fetchall())
        if desc:
            con.execute("UPDATE erp_lines SET note=? WHERE doc_id=? AND good=? AND COALESCE(note,'')=''", (desc[:300], doc_id, l["good"]))
    return n


def kusok_reserved():
    return [dict(r) for r in A.q("SELECT k.*, j.wcode FROM raspil_kusok k LEFT JOIN raspil_job j ON j.id=k.sold_job "
                                 "WHERE k.status='res' ORDER BY k.sold_at DESC")]


def kusok_res_label(k):
    return f"№{k['res_doc']}" if k.get("res_doc") else (f"Ц{k['wcode']}" if k.get("wcode") else "брон")


def kusok_auto_sold(jr=None):
    """Брон қилинган накладной (№) 1С'да проведен бўлса — кусок сотилди."""
    rs = [dict(r) for r in A.q("SELECT * FROM raspil_kusok WHERE status='res' AND res_doc<>''")]
    if not rs:
        return 0
    if jr is None:
        import c1
        jr = c1.journal()
    posted = {}
    for k, v in jr.items():
        if v.get("kind") == 210 and v.get("posted") and not v.get("deleted"):
            posted[re.sub(r"\D", "", str(v.get("no") or "")).lstrip("0")] = v.get("no")
    n = 0
    for k in rs:
        if k["res_doc"] in posted:
            A.db().execute("UPDATE raspil_kusok SET status='sold', sold_at=? WHERE id=? AND status='res'", (A.now(), k["id"]))
            n += 1
    A.db().commit()
    return n


def kusok_unsell(jid):
    """Иш бекор қилинса: омбордаги кусок омборга қайтади, бевосита ёзилгани ўчади."""
    A.db().execute("UPDATE raspil_kusok SET status='in', sold_at='', sold_by='', sold_rw=NULL, sold_job=NULL WHERE sold_job=? AND src='stock'", (jid,))
    A.db().execute("UPDATE raspil_kusok SET status='del' WHERE sold_job=? AND src='direct'", (jid,))
    A.db().commit()


def kusok_page(ym):
    stock = kusok_stock()
    sold = [dict(r) for r in A.q("SELECT k.*, j.wcode, j.status jst, j.doc_no FROM raspil_kusok k LEFT JOIN raspil_job j ON j.id=k.sold_job "
                                 "WHERE k.status='sold' AND substr(k.sold_at,1,7)=? ORDER BY k.sold_at DESC", (ym,))]
    by = defaultdict(lambda: [0, 0.0])
    for k in sold:
        by[k["sold_by"] or "?"][0] += 1; by[k["sold_by"] or "?"][1] += k["m2"]
    tp = defaultdict(lambda: [0, 0.0])
    for k in stock:
        tp[k["type"]][0] += 1; tp[k["type"]][1] += k["m2"]
    byin = defaultdict(lambda: [0, 0.0])                  # шу ойда ким омборга киритди
    for k in A.q("SELECT who, m2 FROM raspil_kusok WHERE src='stock' AND status<>'del' AND substr(at,1,7)=?", (ym,)):
        byin[k["who"] or "?"][0] += 1; byin[k["who"] or "?"][1] += k["m2"]
    return dict(stock=stock, sold=sold, res=kusok_reserved(), by=sorted(by.items(), key=lambda x: -x[1][1]), types=sorted(tp.items()),
                byin=sorted(byin.items(), key=lambda x: -x[1][1]),
                m2=sum(k["m2"] for k in stock), sold_m2=sum(k["m2"] for k in sold))


def parse_area(text):
    """Деталь ўлчамлари → м²: «200-500», «2800 400 2» (2 та), «2,8х0,4», бир неча қатор/«;». -> (м², «200×500; 2800×400×2») ёки None.
    Битта рақам (≤50) — тайёр м²."""
    parts = [p for p in re.split(r"[\n;]|,\s+", text or "") if re.search(r"\d", p)]
    tot, lab = 0.0, []
    for p in parts:
        nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", p)]
        if len(nums) == 1 and len(parts) == 1 and nums[0] <= 50:
            return round(nums[0], 3), ""
        if len(nums) < 2:
            return None
        a, b = nums[:2]
        k = nums[2] if len(nums) > 2 else 1
        tot += (a * b / 1e6 if max(a, b) > 50 else a * b) * k
        lab.append(f"{_n(a)}×{_n(b)}" + (f"×{_n(k)}" if k != 1 else ""))
    return (round(tot, 3), "; ".join(lab)) if lab else None


def kromka_search(q, machine="kromka", limit=12):
    q = norm(q)
    tree, _ = kromka_tree(machine)
    pop = item_popularity()
    out = []
    for node in tree.values():
        for k, nm in node["items"]:
            if q and q in norm(nm):
                out.append((k, nm))
    out.sort(key=lambda x: -pop.get(x[0], 0))
    return out[:limit]


def wait_add_item(jid, gid, name, meters, unit="м", kid=None):
    """Накладнойсиз ишга танланган товар қўшилади (менежер учун): кромка — метр, кусок — м²."""
    j = A.q("SELECT * FROM raspil_job WHERE id=?", (jid,), one=True)
    it = json.loads(j["items"] or "{}")
    w = it.setdefault("wait", {})
    w.setdefault("pick", []).append(dict(gid=gid, name=name, m=meters, unit=unit, **({"kid": kid} if kid else {})))
    txt = "; ".join(f"{p['name']} — {_n(p['m'])} {p.get('unit', 'м')}" for p in w["pick"])
    A.db().execute("UPDATE raspil_job SET items=?, text=?, client=? WHERE id=?", (json.dumps(it, ensure_ascii=False), txt[:500], txt[:120], jid))
    A.db().commit()
    if j["wcode"]:
        code_doc_safe(j["wcode"])
    return w["pick"]


def wait_note(jid, note):
    """Накладнойсиз ишга изоҳ (нима қилди). Танланган товарлар бўлса — улар сақланади, изоҳ қўшилади."""
    j = A.q("SELECT * FROM raspil_job WHERE id=?", (jid,), one=True)
    it = json.loads(j["items"] or "{}")
    w = it.setdefault("wait", {})
    w["note"] = (note or "")[:300]
    picks = "; ".join(f"{p['name']} — {_n(p['m'])} {p.get('unit', 'м')}" for p in w.get("pick") or [])
    txt = (picks + " · " if picks else "") + w["note"]
    A.db().execute("UPDATE raspil_job SET items=?, text=?, client=? WHERE id=? AND status='wait'",
                   (json.dumps(it, ensure_ascii=False), txt[:500], txt[:120], jid))
    A.db().commit()
    if j["wcode"]:
        code_doc_safe(j["wcode"])


def work_text(machine, det, frac=1.0):
    """Ишчига: фақат сони/метраж (пул йўқ)."""
    if machine == "pris":
        return pris_text(det, frac)
    return "\n".join(f"• {nm}: {_n(q * frac)} {unit or ''}".rstrip() for nm, q, unit, a, note in det)


def job_calc(machine, lines):
    """pris → (тешик, %-сумма, det); бошқалар → (метр/дона, сумма, det)."""
    return pris_calc(lines) if machine == "pris" else work_calc(machine, lines)


def pris_text(det, frac=1.0):
    """Ишчига: фақат сони (пул йўқ)."""
    out = []
    for nm, q, unit, v, note in det:
        q = q * frac
        if note == "%":
            out.append(f"• {nm}: {_n(q)}" + (f" {unit}" if unit and unit != "шт" else " та"))
        else:
            out.append(f"• {nm}: {_n(q)}" + (f" {unit}" if unit and unit != "шт" else "") + (f" {note} = {_n(v * frac)} тешик" if note else ""))
    return "\n".join(out)


def pris_done(iddoc, machine="pris"):
    """Шу накладнойда шу станок иши қанча қисми бажарилган (0..1)."""
    f = 0.0
    for r in A.q("SELECT items FROM raspil_job WHERE iddoc=? AND machine=? AND status='ok'", (iddoc, machine)):
        f += float(json.loads(r["items"] or "{}").get("frac", 1.0))
    return min(1.0, round(f, 4))


def record_pris(rw, iddoc, doc_no, client, photo="", msg_id=None, at=None, frac=None, machine="pris", who=None, info="бот"):
    """Присадка/кромка/овал/утолщенка/ровер бажарилди (frac — қисми, масалан 0.5; None — қолганининг ҳаммаси).
    -> (job_id, None) | (None, сабаб)"""
    import c1
    at = at or _now()
    left = round(1.0 - pris_done(iddoc, machine), 4)
    if left <= 0.001:
        prev = A.q("SELECT MAX(at) at FROM raspil_job WHERE iddoc=? AND machine=? AND status='ok'", (iddoc, machine), one=True)
        return None, f"аввал тўлиқ ҳисобланган ({prev['at'][8:10]}.{prev['at'][5:7]} {prev['at'][11:16]})"
    frac = left if frac is None else min(frac, left)
    lines = sale_lines(iddoc)
    pcs, psum, det = job_calc(machine, lines)
    if pcs <= 0 and psum <= 0:
        return None, f"накладнойда {MACHINES.get(machine, machine).split(' (')[0].lower()} иши йўқ"
    pcs, psum = round(pcs * frac, 3), round(psum * frac, 2)
    crew = who_for(rw, machine, at)
    op, helper = (crew["op_id"], crew["helper_id"]) if crew else (rw.get("kw_id"), None)
    day = crew["day"] if crew else at.date().isoformat()
    cid = crew["id"] if crew else None
    if who:                                              # накладнойсиз иш уланди — ўша ишчига, ўша кунга
        op, helper, day, cid = who["op_id"], who["helper_id"], who["day"], who["crew_id"]
    db = A.db()
    jid = db.execute("INSERT INTO raspil_job(machine, at, day, crew_id, op_id, helper_id, rw_id, msg_id, text, photo, iddoc, doc_no, client, "
                     "sheets, detail, status, info, mats, pr_pcs, pr_sum, items) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,'ok',?,?,?,?,?)",
                     (machine, _fmt(at), day, cid, op, helper, rw["id"], msg_id,
                      MACHINES.get(machine, machine).split(" (")[0].lower() + (f" — {round(frac * 100)}%" if frac < 0.999 else ""), photo or "", iddoc, doc_no, client,
                      json.dumps(det, ensure_ascii=False), info, json.dumps(materials(lines), ensure_ascii=False), pcs, psum,
                      json.dumps({"frac": round(frac, 4)}))).lastrowid
    db.commit()
    sync_kpi(day)
    return jid, None


# ---------------------------------------------------------------- Базис конструкторлар (1С'дан, ботсиз)
BASIS_KIND, BASIS_PRIS_KIND = "basis", "basis_pris"


def basis_designers():
    """1С: «… Базис Конструктор …» папкаси ичидаги папкалар — ҳар бири битта конструктор. -> [(папка id, номи)]"""
    import c1
    g = c1.goods()
    roots = {k for k, v in g.items() if v["folder"] and "базис" in v["name"].lower() and "конструк" in v["name"].lower()}
    return sorted(((k, v["name"]) for k, v in g.items() if v["folder"] and v["parent"] in roots and not v["deleted"]), key=lambda x: x[1])


def _is_pris_item(name):
    n = (name or "").lower()
    return n.startswith("услуга сверления") or n.startswith("услуга фрезеров") or "присадк" in n


def basis_month(ym, col="SP559"):
    """col: SP559 — сўм, SP905 — $. Ой бўйича (проведен накладнойлар, сана бўйича): {папка: {кун: сўм}}, присадка {кун: сўм}."""
    import c1
    y, m = int(ym[:4]), int(ym[5:7])
    j = {k: v for k, v in c1.journal().items() if v["kind"] == 210 and v["posted"] and not v["deleted"] and v["date"]
         and v["date"].year == y and v["date"].month == m}
    g = c1.goods()
    folders = {fid for fid, _ in basis_designers()}
    des, pris = defaultdict(lambda: defaultdict(float)), defaultdict(float)
    for r in c1.table("DT210"):
        d = j.get(r["IDDOC"])
        if not d:
            continue
        it = g.get(r["SP216"]) or {}
        amt = float(r.get(col) or 0)
        if it.get("parent") in folders:
            des[it["parent"]][d["date"].day] += amt
        elif _is_pris_item(it.get("name")):
            pris[d["date"].day] += amt
    try:                                                  # платформа накладнойлари: присадка (конструктор — pf_basis_days'да)
        pc = "usd" if col == "SP905" else "uzs"
        for r in A.q(f"SELECT d.date, l.{pc} amt, g.name FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id LEFT JOIN erp_goods g ON g.ext=l.good "
                     "WHERE d.src='pf' AND d.kind='sale' AND d.status='posted' AND d.date LIKE ?", (ym + "-%",)):
            if _is_pris_item(r["name"]):
                pris[int(r["date"][8:10])] += float(r["amt"] or 0)
    except Exception:
        A.errlog.exception("basis_month pf")
    return des, pris


def basis_piece_from():
    v = A.get_setting("basis_piece_from", "2026-12") or "2026-12"
    return v if re.fullmatch(r"\d{4}-\d{2}", v) else "2026-12"


def basis_piece(ym):
    """Базис иш бай (синов): кройка м² (1С «Услуга конструктора …», кв-м) → лист сони.
    Накладнойдаги листлар (ЛДСП/ЛМДФ ва ХДФ/ДВП) нисбатида м² бўлинади; лист = м² ÷ лист юзаси.
    -> {папка: dict(docs, m2, sh_main, sh_hdf, pay, days{кун: сўм}, rows[…])}"""
    import c1
    y, m = int(ym[:4]), int(ym[5:7])
    j = {k: v for k, v in c1.journal().items() if v["kind"] == 210 and v["posted"] and not v["deleted"] and v["date"]
         and v["date"].year == y and v["date"].month == m}
    g = c1.goods()
    folders = {fid for fid, _ in basis_designers()}
    a_main, a_hdf = cfg("basis_sheet_m2") or 5.796, cfg("basis_hdf_m2") or 5.796
    r_main, r_hdf = cfg("basis_rate_main"), cfg("basis_rate_hdf")
    per = defaultdict(lambda: dict(q=defaultdict(float), main=0.0, hdf=0.0))
    for r in c1.table("DT210"):
        d = j.get(r["IDDOC"])
        if not d:
            continue
        it = g.get(r["SP216"]) or {}
        q = float(r.get("SP217") or 0)
        unit = ((g.get(r["SP216"]) or {}).get("unit") or "").lower()
        if it.get("parent") in folders:
            per[r["IDDOC"]]["q"][it["parent"]] += q
        elif "лист" in unit and q > 0:
            if HDF_NAME_RE.search(it.get("name") or ""):
                per[r["IDDOC"]]["hdf"] += q * a_hdf
            else:
                per[r["IDDOC"]]["main"] += q * a_main
    out = defaultdict(lambda: dict(docs=0, m2=0.0, sh_main=0.0, sh_hdf=0.0, pay=0.0, days=defaultdict(float), rows=[]))
    for iddoc, p in per.items():
        if not p["q"]:
            continue
        tot = p["main"] + p["hdf"]
        fh = p["hdf"] / tot if tot > 0 else 0.0
        for fid, q in p["q"].items():
            sm, sh = q * (1 - fh) / a_main, q * fh / a_hdf
            pay = sm * r_main + sh * r_hdf
            o = out[fid]
            o["docs"] += 1; o["m2"] += q; o["sh_main"] += sm; o["sh_hdf"] += sh; o["pay"] += pay
            o["days"][j[iddoc]["date"].day] += pay
            o["rows"].append(dict(no=j[iddoc].get("no", ""), date=j[iddoc]["date"], m2=q, sh_main=sm, sh_hdf=sh, pay=pay))
    return out


# ---------------------------------------------------------------- менежер крой (иш бай): бот орқали, менежер ёзган м² → лист
KROY_KIND, KROY_HDF_KIND = "kroy", "kroy_hdf"


def kroy_from():
    """Шу ойдан: кройчи лист бай (платформа накладнойидаги «Кройчи» майдонидан), ботдаги «Крой қилдим» йўқ."""
    v = A.get_setting("kroy_from", "2026-11") or "2026-11"
    return v if re.fullmatch(r"\d{4}-\d{2}", v) else "2026-11"


def kroy_bot(rw):
    """Ботдаги «Крой қилдим» тугмаси олиб ташланди (эгаси: керак эмас) — крой платформа накладнойидаги «Кройчи» майдонидан."""
    return False


def _pf_sales(ym, col):
    """Платформада ёзилган проведен накладнойлар (ой бўйича), «konstr»/«kroychi» белгиланганлари. -> rows"""
    try:
        return A.q(f"SELECT id, no, date, client, {col} who FROM erp_docs WHERE src='pf' AND kind='sale' AND status='posted' "
                   f"AND {col} IS NOT NULL AND date LIKE ?", (ym + "-%",))
    except Exception:
        return []


KROY_SKIP_RE = re.compile(r"столеш|45|угл|кромк", re.I)       # кройга кирмайдиган распиллар (столешница, бурчак, кромка)
KROY_M2_RE = re.compile(r"кусок|остатк", re.I)                  # кусок распили — кв.м: ÷ 1 лист юзаси


def kroy_units(name, qty):
    """Распил хизмати сатри -> (асосий, ХДФ) крой бирлиги (эгаси, 25.10: крой лист сонидан эмас — распил сонидан).
    Распил эмас ёки столешница/бурчак/кромка распили — (0, 0); кусок (кв.м) — ÷ 1 лист юзаси."""
    nm = re.sub(r"\s+", " ", (name or "")).strip()
    if not re.match(r"(\d+\s+)?распил", nm, re.I) or KROY_SKIP_RE.search(nm):
        return 0.0, 0.0
    q = float(qty or 0)
    if KROY_M2_RE.search(nm):
        return q / (cfg("kroy_m2_main") or 5.796), 0.0
    if HDF_NAME_RE.search(nm):
        return 0.0, q
    return q, 0.0


def pf_kroy_docs(ym):
    """Кройчи: накладнойдаги распил хизматлари сони (ЛДСП/ЛМДФ/МДФ/акрил ва ХДФ/ДВП алоҳида; кусок — кв.м ÷ лист юзаси;
    возврат сатрлари айрилади). Распилсиз (бутун) сотилган лист кройга кирмайди, мижознинг листи («Ташкаридан») киради.
    -> [dict(kw, doc, day, main, hdf)]"""
    out = []
    for d in _pf_sales(ym, "kroychi"):
        main = hdf = 0.0
        for l in A.q("SELECT l.qty, g.name FROM erp_lines l JOIN erp_goods g ON g.ext=l.good WHERE l.doc_id=?", (d["id"],)):
            a, b = kroy_units(l["name"], l["qty"])
            main += a; hdf += b
        if main > 0 or hdf > 0:
            out.append(dict(kw=d["who"], no=d["no"], day=d["date"], client=d["client"], main=max(main, 0.0), hdf=max(hdf, 0.0)))
    return out


def pf_basis_days(ym):
    """Конструктор: накладнойдаги «Услуга конструктора» сатрлари суммаси. -> {kw: ({кун: сўм}, {кун: $})}"""
    import erp
    out = defaultdict(lambda: (defaultdict(float), defaultdict(float)))
    for d in _pf_sales(ym, "konstr"):
        day = int(d["date"][8:10])
        for l in A.q("SELECT l.uzs, l.usd, g.name FROM erp_lines l JOIN erp_goods g ON g.ext=l.good WHERE l.doc_id=?", (d["id"],)):
            if erp.KONSTR_RE.search(l["name"] or ""):
                out[d["who"]][0][day] += l["uzs"] or 0
                out[d["who"]][1][day] += l["usd"] or 0
    return out


def is_kroy(rw):
    r = A.q("SELECT kroy FROM ready_workers WHERE id=?", (rw.get("id"),), one=True) if rw and rw.get("id") else None
    return bool(r and r["kroy"])


def kroy_sheets(m2_main, m2_hdf):
    return (m2_main or 0) / (cfg("kroy_m2_main") or 5.796), (m2_hdf or 0) / (cfg("kroy_m2_hdf") or 5.796)


def kroy_add(rw, doc_no, m2_main, m2_hdf, at=None):
    """Менежер крой қилди: накладной № + Базисдаги м² (ЛДСП/ЛМДФ ва ХДФ/ДВП). -> id"""
    at = at or _now()
    iddoc, client = "", ""
    try:
        import ready as R
        d = R.find_doc(doc_no)
        if d:
            iddoc = d["iddoc"]
            client = R.doc_client(d["iddoc"])[0] or ""
    except Exception as e:
        A.errlog.warning("kroy find_doc: %r", e)
    kid = A.db().execute("INSERT INTO raspil_kroy(at, day, rw_id, kw_id, doc_no, iddoc, client, m2_main, m2_hdf) VALUES(?,?,?,?,?,?,?,?,?)",
                         (_fmt(at), at.date().isoformat(), rw.get("id"), rw.get("kw_id"), str(doc_no), iddoc, client[:120],
                          round(float(m2_main or 0), 3), round(float(m2_hdf or 0), 3))).lastrowid
    A.db().commit()
    try:
        kroy_sync(at.date().isoformat()[:7])
    except Exception:
        A.errlog.exception("kroy sync")
    return kid


def kroy_month(ym):
    """-> {kw: dict(docs, m2_main, m2_hdf, sh_main, sh_hdf, pay, d_main{кун: лист}, d_hdf{…}, rows[])}"""
    r_main, r_hdf = cfg("kroy_rate_main"), cfg("kroy_rate_hdf")
    out = defaultdict(lambda: dict(docs=0, m2_main=0.0, m2_hdf=0.0, sh_main=0.0, sh_hdf=0.0, pay=0.0,
                                   d_main=defaultdict(float), d_hdf=defaultdict(float), rows=[]))
    pf = pf_kroy_docs(ym)
    pf_nos = {str(x["no"]) for x in pf}
    a_main, a_hdf = cfg("kroy_m2_main") or 5.796, cfg("kroy_m2_hdf") or 5.796
    for x in pf:                                       # платформа накладнойи: «Кройчи» танланган — распил сони накладнойдан
        sm, sh = x["main"], x["hdf"]
        o = out[x["kw"]]
        o["docs"] += 1; o["m2_main"] += sm * a_main; o["m2_hdf"] += sh * a_hdf; o["sh_main"] += sm; o["sh_hdf"] += sh
        o["pay"] += sm * r_main + sh * r_hdf
        d = int(x["day"][8:10]); o["d_main"][d] += sm; o["d_hdf"][d] += sh
        o["rows"].append(dict(id=0, at=x["day"], iddoc="pf", doc_no=x["no"], day=x["day"], client=x["client"], m2_main=sm * a_main, m2_hdf=sh * a_hdf,
                              sh_main=sm, sh_hdf=sh, pay=sm * r_main + sh * r_hdf, src="pf"))
    for r in A.q("SELECT * FROM raspil_kroy WHERE status='ok' AND day LIKE ? ORDER BY at", (ym + "-%",)):
        if str(r["doc_no"]) in pf_nos:
            continue                                   # бот ва платформа — бир накладной икки марта ҳисобланмайди
        sm, sh = kroy_sheets(r["m2_main"], r["m2_hdf"])
        o = out[r["kw_id"]]
        o["docs"] += 1; o["m2_main"] += r["m2_main"]; o["m2_hdf"] += r["m2_hdf"]; o["sh_main"] += sm; o["sh_hdf"] += sh
        o["pay"] += sm * r_main + sh * r_hdf
        d = int(r["day"][8:10]); o["d_main"][d] += sm; o["d_hdf"][d] += sh
        o["rows"].append(dict(r, sh_main=sm, sh_hdf=sh, pay=sm * r_main + sh * r_hdf))
    return out


def kroy_sync(ym):
    """Крой иш бай KPI'га: фақат «шу ойдан» бошлаб (ундан олдин — синов, фақат кўриш учун)."""
    if ym < kroy_from() or not auto_on(ym):
        return 0
    import kassa
    kassa.ensure_kpi_month(ym)
    n = 0
    for kw, o in kroy_month(ym).items():
        if not kw:
            continue
        _write_kpi_month(kw, KROY_KIND, ym, dict(o["d_main"]), cfg("kroy_rate_main"))
        if o["sh_hdf"] or A.q("SELECT 1 FROM k_kpi_cols WHERE worker_id=? AND kind=?", (kw, KROY_HDF_KIND), one=True):
            _write_kpi_month(kw, KROY_HDF_KIND, ym, dict(o["d_hdf"]), cfg("kroy_rate_hdf"))
        n += 1
    A.db().commit()
    kassa._memo.clear()
    return n


def basis_map():
    """{папка: ишчи id} — созламадан, бўлмаса исм бўйича топилади."""
    try:
        mp = json.loads(A.get_setting("basis_map", "") or "{}")
    except ValueError:
        mp = {}
    ws = workers()
    for fid, name in basis_designers():
        if not mp.get(fid):
            hit = match_name(name.split()[0], ws)
            if len(hit) != 1:  # «Садулла» ↔ «Сайдулла» каби ёзилиш фарқи
                import difflib
                t = norm(name.split()[0])
                hit = [w for w in ws if any(len(x) >= 4 and difflib.SequenceMatcher(None, t, x).ratio() >= 0.85
                                            for x in (norm(y) for y in re.split(r"[\s()\-.,/]+", w["name"]) if y))]
            if len(hit) == 1:
                mp[fid] = hit[0]["id"]
    return mp


def basis_pris_kw():
    kw = int(A.parse_float(A.get_setting("basis_pris_kw", "0"), 0))
    if kw:
        return kw
    mp = basis_map()
    return next((kw for fid, name in basis_designers() for f2, kw in mp.items() if f2 == fid and "абду" in name.lower()), 0)


def _locked(db, ym, cid):
    """Қўлда ўзгартирилган кунлар (KPI саҳифасида) — автомат ҳисоб уларга тегмайди."""
    return {r[0] for r in db.execute("SELECT day FROM k_kpi_lock WHERE ym=? AND col_id=?", (ym, cid))}


def _write_kpi_month(kw, kind, ym, per_day, rate):
    import kassa
    db = A.db()
    cid = _kpi_col(db, kw, kind, "d", ym, rate)
    locked = _locked(db, ym, cid)
    db.execute("DELETE FROM k_kpi WHERE ym=? AND col_id=? AND day NOT IN (SELECT day FROM k_kpi_lock WHERE ym=? AND col_id=?)", (ym, cid, ym, cid))
    for d, v in per_day.items():
        if abs(v) > 0.004 and d not in locked:
            db.execute("INSERT OR REPLACE INTO k_kpi(ym,day,col_id,qty) VALUES(?,?,?,?)", (ym, d, cid, round(v, 2)))
    return cid


def basis_sync(ym):
    """Базис: конструкторнинг 1С суммаси × 50% ва присадка суммаси × 5% → ойликка (кунлар бўйича), автомат ойдан бошлаб."""
    if not auto_on(ym):
        return 0
    import kassa
    kassa.ensure_kpi_month(ym)
    sums = {"UZS": basis_month(ym), "USD": basis_month(ym, "SP905")}   # ишчи валютасида: $ ли ишчига $, сўмлигига сўм
    mp = basis_map()
    db = A.db()

    def cur(kw):
        r = db.execute("SELECT cur FROM k_workers WHERE id=?", (kw,)).fetchone()
        return "USD" if r and r[0] == "USD" else "UZS"
    n = 0
    pf = pf_basis_days(ym)                                 # платформа накладнойлари: «Конструктор» майдони бўйича
    per_kw = defaultdict(lambda: defaultdict(float))
    for fid, _name in basis_designers():
        kw = mp.get(fid)
        if kw:
            for d_, v in sums[cur(kw)][0].get(fid, {}).items():
                per_kw[kw][d_] += v
    for kw, (uz, us) in pf.items():
        for d_, v in (us if cur(kw) == "USD" else uz).items():
            per_kw[kw][d_] += v
    for kw in set(per_kw) | {mp.get(fid) for fid, _ in basis_designers() if mp.get(fid)}:
        _write_kpi_month(kw, BASIS_KIND, ym, dict(per_kw.get(kw, {})), cfg("basis_pct") / 100)
        n += 1
    pkw = basis_pris_kw()
    if pkw:
        if A.get_setting("basis_pris_on", "1") == "1":
            _write_kpi_month(pkw, BASIS_PRIS_KIND, ym, sums[cur(pkw)][1], cfg("basis_pris_pct") / 100)
        else:
            row = db.execute("SELECT id FROM k_kpi_cols WHERE worker_id=? AND kind=? AND shift='d'", (pkw, BASIS_PRIS_KIND)).fetchone()
            if row:
                db.execute("DELETE FROM k_kpi WHERE ym=? AND col_id=? AND day NOT IN (SELECT day FROM k_kpi_lock WHERE ym=? AND col_id=?)",
                           (ym, row[0], ym, row[0]))
    db.commit()
    kassa._memo.clear()
    A.set_settings(basis_synced=A.now())
    return n


def _digits9(s):
    """Телефонлардан охирги 9 рақам: «+998 90 123-45-67, 71 200 00 00» → {'901234567', '712000000'}."""
    out = set()
    for part in re.split(r"[,;/]|\s{2,}", s or ""):
        d = re.sub(r"\D", "", part)
        if len(d) >= 9:
            out.add(d[-9:])
    if not out:                                         # бир қаторда бўш жой билан ёзилган битта рақам
        d = re.sub(r"\D", "", s or "")
        if 9 <= len(d) <= 12:
            out.add(d[-9:])
    return out


def _code_re(code):
    return re.compile(rf"(?<![0-9A-Za-zА-Яа-яЁё])[ЦцCcСсSs]\s*[-№#.]?\s*{int(code)}(?!\d)")


def _notify(rw_id, text):
    """Ишчига ботда хабар (бот уланган бўлса)."""
    try:
        r = A.q("SELECT bot_uid FROM ready_workers WHERE id=?", (rw_id,), one=True)
        if r and r["bot_uid"]:
            import raspil_bot
            raspil_bot.send(r["bot_uid"], text)
    except Exception:
        A.errlog.exception("notify")


# ---------------------------------------------------------------- «💵 Пул олдим»: ишчи мижоздан накладнойсиз иш учун пул олди
# Пул ишчида туради (status='held'), кун охирида кассага топширади: кассир «Деньги у работников» саҳифасида қабул қилади
# (status='handed') → ишчига «✅ қабул қилинди». Кассага ёзув эмас — тўлов 1С/накладнойда кассир томонидан киритилади.
def parse_money(text):
    """«150000», «150 000», «150к», «20$», «20 доллар» -> (сумма, 'UZS'|'USD') ёки None."""
    t = (text or "").strip().lower().replace("сўм", "").replace("сум", "").replace("so'm", "").replace("som", "")
    usd = bool(re.search(r"\$|долл|usd|dollar", t))
    t = re.sub(r"\$|долл\w*|usd|dollar\w*", "", t)
    k = bool(re.search(r"\d\s*(к|k|минг|ming)\s*$", t))
    t = re.sub(r"(к|k|минг|ming)\s*$", "", t).strip()
    t = re.sub(r"[\s'’`]", "", t)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):          # 150.000
        t = t.replace(".", "")
    t = t.replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    v = v * 1000 if k else v
    if v <= 0 or (usd and v > 100000) or (not usd and not 1000 <= v <= 500_000_000):
        return None
    return (round(v, 2) if usd else round(v), "USD" if usd else "UZS")


def cash_fmt(amount, cur):
    if cur == "USD":
        return f"{amount:,.2f}".replace(",", " ").replace(".", ",") + " $"
    return f"{amount:,.0f}".replace(",", " ") + " сўм"


def cash_total(rows):
    """[қатор] -> «300 000 сўм + 20,00 $»"""
    t = {}
    for r in rows:
        t[r["cur"]] = t.get(r["cur"], 0) + (r["amount"] or 0)
    return " + ".join(cash_fmt(t[c], c) for c in ("UZS", "USD") if t.get(c)) or "0"


def cash_add(rw, code, amount, cur="UZS", ref=""):
    cid = A.db().execute("INSERT INTO raspil_cash(at, rw_id, code, ref, amount, cur) VALUES(?,?,?,?,?,?)",
                         (A.now(), rw["id"], int(code) if code else None, (ref or "")[:20], amount, cur)).lastrowid
    A.db().commit()
    if code:
        code_doc_safe(code)                               # черновик изоҳида: «Ишчи пул олди: …»
    return cid


def cash_rows(rw_id=None, status="held", days=None):
    sql, a = "SELECT c.*, COALESCE(w.name,'') wname FROM raspil_cash c LEFT JOIN ready_workers w ON w.id=c.rw_id WHERE c.status=?", [status]
    if rw_id:
        sql += " AND c.rw_id=?"
        a.append(rw_id)
    if days:
        sql += " AND COALESCE(NULLIF(c.handed_at,''), c.at)>=?"
        a.append(_fmt(_now() - timedelta(days=days)))
    return [dict(r) for r in A.q(sql + " ORDER BY c.at", tuple(a))]


def cash_label(r):
    """Пул қайси иш учун: «№7012 · Алишер 1131» ёки «№6453 (1С)»."""
    if r.get("code"):
        cl = code_client(r["code"])
        return f"{clabel(r['code'])}" + (f" · {cl}" if cl else "")
    return f"№{r['ref']}" if r.get("ref") else "—"


def cash_for_code(code):
    return [dict(r) for r in A.q("SELECT c.*, COALESCE(w.name,'') wname FROM raspil_cash c LEFT JOIN ready_workers w ON w.id=c.rw_id "
                                 "WHERE c.code=? AND c.status<>'void' ORDER BY c.at", (int(code),))] if code else []


def cash_accept(ids, by):
    """Кассир қабул қилди -> ишчиларга хабар. -> қабул қилинганлар"""
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        return []
    rows = [dict(r) for r in A.q(f"SELECT * FROM raspil_cash WHERE status='held' AND id IN ({','.join('?' * len(ids))})", tuple(ids))]
    for r in rows:
        A.db().execute("UPDATE raspil_cash SET status='handed', handed_at=?, handed_by=? WHERE id=? AND status='held'", (A.now(), by or "", r["id"]))
    A.db().commit()
    per = {}
    for r in rows:
        per.setdefault(r["rw_id"], []).append(r)
    for rw_id, rs in per.items():
        left = cash_rows(rw_id)
        _notify(rw_id, f"✅ Касса қабул қилди: {cash_total(rs)}\n" + "\n".join(f"• {cash_label(r)} — {cash_fmt(r['amount'], r['cur'])}" for r in rs)
                + (f"\n\n💵 Сизда яна: {cash_total(left)}" if left else ""))
    for c in {r["code"] for r in rows if r["code"]}:
        code_doc_safe(c)
    return rows


def cash_void(cid, rw_id):
    """Ишчи хато ёзди — ўзи бекор қилади (касса қабул қилмаган бўлса)."""
    r = A.q("SELECT * FROM raspil_cash WHERE id=? AND rw_id=? AND status='held'", (int(cid), rw_id), one=True)
    if not r:
        return None
    A.db().execute("UPDATE raspil_cash SET status='void' WHERE id=?", (r["id"],))
    A.db().commit()
    if r["code"]:
        code_doc_safe(r["code"])
    return dict(r)


def code_card(code):
    """Менежерга: Ц-коддаги ҳамма ишлар (1С'га киритиш учун) ва ҳолати. -> матн ёки None"""
    js = [dict(r) for r in A.q("SELECT * FROM raspil_job WHERE wcode=? AND status IN ('wait','linked') ORDER BY at", (int(code),))]
    if not js:
        return None
    refs = [r for r in ((json.loads(j["items"] or "{}").get("wait") or {}).get("ref") for j in js) if r]
    out = [f"🔖 №{refs[0]['no']} — {mlabel('rover').lower()} иши қўшилди" + (f" ({refs[0]['client']})" if refs[0].get("client") else "")] if refs else [f"🔖 {clabel(code)}" + (f" — 👤 {code_client(code)}" if code_client(code) else "")]
    for j in js:
        w = json.loads(j["items"] or "{}").get("wait") or {}
        who = wname(j["op_id"]) + (f" + {wname(j['helper_id'])}" if j["helper_id"] else "")
        icon = "🧩" if j["machine"] == SALE else (WORK.get(j["machine"]) or {}).get("icon") or "🪚"
        out.append(f"\n{icon} {mlabel(j['machine'])} — {who}")
        picks = w.get("pick") or []
        if picks:
            out += [f"• {p['name']} — {_n(p['m'])} {p.get('unit', 'м')}" for p in picks]
        elif w.get("kind") not in ("job", "sale") and w.get("n"):
            out.append(f"• {WAIT_KINDS.get(w.get('kind'), 'Лист')} — {_n(w['n'])} та")
        if w.get("note"):
            out.append(f"💬 {w['note']}")
        elif j["text"] and not picks:
            out.append(f"💬 {j['text']}")
        if j["photo"]:
            out.append("🖼 расми саҳифада")
    if all(j["status"] == "linked" for j in js):
        nos = sorted({j["doc_no"] for j in js if j["doc_no"]})
        out.append(f"\n✅ Тўланди" + (f" — №{', '.join(nos)}" if nos else ""))
    elif refs:
        out.append(f"\n⏳ 1С: №{refs[0]['no']} га ровер хизматини қўшинг — ёки янги накладнойга киритиб, изоҳига «{refs[0]['no']}» ёзинг.")
    else:
        cw = code_row(code)
        cd = A.q("SELECT no, status FROM erp_docs WHERE id=?", (cw["doc_id"],), one=True) if cw and cw["doc_id"] else None
        import erp as E
        out.append(f"\n⏳ Платформада {clabel(code)} черновиги: мижозни танлаб, проведен қилинг." if cd and cd["status"] == "draft" and not E.test_mode()
                   else f"\n⏳ 1С накладнойга киритинг, изоҳига «Ц{code}» ёзинг.")
    return "\n".join(out)


def notify_mgr(code):
    """Менежерларга Ц-код карточкаси: биринчи марта юборилади, кейин ўша хабар янгиланади."""
    if not code:
        return
    try:
        txt = code_card(code)
        if not txt:
            return
        import raspil_bot as RB
        db = A.db()
        for m in A.q("SELECT bot_uid FROM ready_workers WHERE mgr=1 AND active=1 AND bot_uid IS NOT NULL"):
            r = db.execute("SELECT msg_id FROM raspil_mgrmsg WHERE code=? AND chat=?", (int(code), m["bot_uid"])).fetchone()
            if r:
                RB.edit(m["bot_uid"], r[0], txt)
            else:
                res = RB.send(m["bot_uid"], txt)
                if res and res.get("message_id"):
                    db.execute("INSERT OR REPLACE INTO raspil_mgrmsg(code, chat, msg_id) VALUES(?,?,?)", (int(code), m["bot_uid"], res["message_id"]))
        db.commit()
    except Exception:
        A.errlog.exception("notify_mgr")


def auto_link_waits(days=30, ahead=10):
    """Накладнойсиз иш → 1С'да проведен накладной топилса, ўзи улайди:
    1) код (Ц27) накладной изоҳида ёки қатор изоҳида; 2) ишчи ёзган телефон мижоз телефонига мос. -> уланганлар сони"""
    import c1
    try:
        kusok_auto_sold()
    except Exception:
        A.errlog.exception("kusok auto sold")
    n_pf = 0
    try:
        n_pf = pf_link_waits()                            # цех черновиги проведен бўлди → тўланди
    except Exception:
        A.errlog.exception("pf link waits")
    all_w = waits(days=days)
    if not all_w:
        return n_pf
    jr = dict(c1.journal())
    pfj, pfn, pfh, pfc = _pf_sales_meta(date.today() - timedelta(days=days + 1))   # платформада проведен накладнойлар ҳам (1С ўчгач)
    jr.update(pfj)
    n = n_pf
    refd = [(w, (json.loads(w["items"] or "{}").get("wait") or {}).get("ref")) for w in all_w]
    refd = [(w, r) for w, r in refd if r]
    if refd:                                              # ровер: ўша накладнойга қўшилди ёки изоҳида «6262» ёзилган янги накладной
        d_min = min(_ts(w["at"]).date() for w, r in refd)
        rdocs = {k: v for k, v in jr.items() if v["kind"] == 210 and v["posted"] and not v["deleted"] and v["date"] and v["date"] >= d_min}
        rnotes = {}
        for r in c1.table("DT210"):
            if r["IDDOC"] in rdocs:
                rnotes[r["IDDOC"]] = rnotes.get(r["IDDOC"], "") + " " + (r.get("SP910") or "") + " " + (r.get("SP1531") or "")
        for k in rdocs:
            rnotes[k] = (pfn.get(k, "") if k.startswith("pf:") else (c1.sale_note1(k) or "")) + " " + rnotes.get(k, "")
        cl = {**c1.clients(), **pfc}
        head = {**{h["IDDOC"]: h["SP232"] for h in c1.table("DH210")}, **pfh}
        for w, ref in refd:
            no_ = re.sub(r"\D", "", ref.get("no") or "").lstrip("0")
            cands = []
            if ref.get("iddoc") and rover_left(ref["iddoc"]):
                cands.append((ref["iddoc"], ref.get("no")))
            if no_:
                rx = re.compile(r"(?<!\d)0*" + no_ + r"(?!\d)")
                d0 = _ts(w["at"]).date()
                cands += [(k, v["no"]) for _d, k, v in sorted((v["date"], k, v) for k, v in rdocs.items()
                                                                if k != ref.get("iddoc") and d0 <= v["date"] <= d0 + timedelta(days=ahead)
                                                                and rx.search(rnotes.get(k, "")))
                          if rover_left(k)]
            for iddoc, no in cands[:1]:
                ok, _t = link_wait(w["id"], iddoc, no, (cl.get(head.get(iddoc)) or {}).get("name", ""), by=f"автомат: №{ref.get('no')}")
                if ok:
                    n += 1
                    _notify(w["rw_id"], f"✅ №{ref.get('no')} ровер иши ҳисобланди" + (f" (№{no})" if no != ref.get("no") else "") + ".")
        all_w = waits(days=days)
    coded = [w for w in all_w if w.get("code")]
    if coded:
        d_min = min(_ts(w["at"]).date() for w in coded)
        docs = {k: v for k, v in jr.items() if v["kind"] == 210 and v["posted"] and not v["deleted"] and v["date"] and v["date"] >= d_min}
        notes = {}
        for r in c1.table("DT210"):
            if r["IDDOC"] in docs:
                notes[r["IDDOC"]] = notes.get(r["IDDOC"], "") + " " + (r.get("SP910") or "") + " " + (r.get("SP1531") or "")
        for k in docs:
            notes[k] = (pfn.get(k, "") if k.startswith("pf:") else (c1.sale_note1(k) or "")) + " " + notes.get(k, "")
        cl = {**c1.clients(), **pfc}
        head = {**{h["IDDOC"]: h["SP232"] for h in c1.table("DH210")}, **pfh}
        for w in coded:
            rx, d0 = _code_re(w["code"]), _ts(w["at"]).date()
            hits = sorted((v["date"], k, v["no"]) for k, v in docs.items() if d0 <= v["date"] <= d0 + timedelta(days=ahead) and rx.search(notes.get(k, "")))
            if not hits:
                continue
            _d, iddoc, no = hits[0]
            ok, txt = link_wait(w["id"], iddoc, no, (cl.get(head.get(iddoc)) or {}).get("name", ""), by=f"автомат: Ц{w['code']}")
            if ok:
                n += 1
                _notify(w["rw_id"], f"✅ {clabel(w['code'])} — тўланди (№{no}). Деталларни бериш мумкин.")
            else:
                A.db().execute("UPDATE raspil_job SET info=? WHERE id=? AND status='wait'", (f"⚠️ Ц{w['code']} → №{no}: {txt}"[:200], w["id"]))
                A.db().commit()
    ws = [dict(w, text=" ".join(x for x in (w["text"] or w["client"] or "", w.get("wclient") or "") if x)) for w in waits(days=days)]
    ws = [w for w in ws if _digits9(w["text"])]
    if not ws:
        return n
    cl = {**c1.clients(), **pfc}
    head = {**{h["IDDOC"]: h["SP232"] for h in c1.table("DH210")}, **pfh}
    for w in ws:
        ph = _digits9(w["text"] or w["client"])
        d0 = _ts(w["at"]).date()
        cands = sorted(((v["date"], k, v["no"]) for k, v in jr.items()
                        if v["kind"] == 210 and v["posted"] and not v["deleted"] and v["date"] and d0 <= v["date"] <= d0 + timedelta(days=ahead)
                        and ph & _digits9((cl.get(head.get(k)) or {}).get("phones", ""))))
        for _d, iddoc, no in cands:
            ok, _txt = link_wait(w["id"], iddoc, no, (cl.get(head.get(iddoc)) or {}).get("name", ""), by="автомат: телефон бўйича")
            if ok:
                n += 1
                _notify(w["rw_id"], f"✅ №{no} тўланди." if not w.get("code") else f"✅ {clabel(w['code'])} — тўланди (№{no}). Деталларни бериш мумкин.")
                break
    return n


def _basis_loop():
    A._stop.wait(60)
    k = 0
    while not A._stop.is_set():
        try:
            with A.app.app_context():
                auto_link_waits()                         # ишчига «тўланди» тез борсин
        except Exception:
            A.errlog.exception("auto link waits")
        try:
            with A.app.app_context():
                import owner
                if owner.A is not None:
                    owner.tick()                          # шерикларга: кун якуни ва касса ўзгариши
        except Exception:
            A.errlog.exception("owner tick")
        if k % 20 == 0:
            try:
                with A.app.app_context():
                    today = date.today()
                    basis_sync(today.isoformat()[:7])
                    kroy_sync(today.isoformat()[:7])
                    if auto_on(today.isoformat()):
                        resync_month(today.isoformat()[:7])   # KPI ва табел ботдан ўзи тўлади (қўлдагига тегмайди)
                    if today.day <= 5:
                        basis_sync((today.replace(day=1) - timedelta(days=1)).isoformat()[:7])
                        kroy_sync((today.replace(day=1) - timedelta(days=1)).isoformat()[:7])
            except Exception:
                A.errlog.exception("basis sync")
        k += 1
        A._stop.wait(60)                                  # ҳар дақиқада: мижоз кутиб турибди


def worker_month(kw, ym=None):
    """Ишчига: шу ойда қилган ишлари (сонлари) ва ҳисобланган ойлик (оклад + иш бай)."""
    import kassa
    ym = ym or date.today().isoformat()[:7]
    t = month_totals(ym).get(kw) or {}
    cnc = 0.0
    for r in A.q("SELECT detail, items FROM raspil_job WHERE day LIKE ? AND status='ok' AND machine='pris' AND (op_id=? OR helper_id=?)",
                 (ym + "-%", kw, kw)):
        fr = float(json.loads(r["items"] or "{}").get("frac", 1.0))
        cnc += sum(d[1] for d in json.loads(r["detail"] or "[]") if d[4] == "%") * fr
    kassa._memo.clear()
    row = None
    try:
        row = next((x for x in kassa.month_calc(ym)["staff"] if x["worker_id"] == kw), None)
    except Exception:
        A.errlog.exception("worker_month")
    return dict(ym=ym, t=t, cnc=cnc, row=row, title=kassa.ym_title(ym))


def doc_state(iddoc):
    """Ботдаги олдиндан кўриниш: накладнойда нима бор ва нима қолди."""
    import c1
    sd = dict(lines=sale_lines(iddoc))
    tot_s, tot_m, det = calc(sd.get("lines") or [])
    tot_g = {"main": 0.0, "hdf": 0.0, "extra": 0.0}
    for lab, _q, _u, sv, _nt in det:
        tot_g[grp(lab)] += sv
    done = A.q("SELECT COALESCE(SUM(sh_main),0) mn, COALESCE(SUM(sh_hdf),0) hd, COALESCE(SUM(sh_extra),0) ex, COALESCE(SUM(m45),0) m, COUNT(*) k "
               "FROM raspil_job WHERE iddoc=? AND status='ok'", (iddoc,), one=True)
    left = {"main": round(tot_g["main"] - done["mn"], 3), "hdf": round(tot_g["hdf"] - done["hd"], 3),
            "extra": round(tot_g["extra"] - done["ex"], 3), "m45": round(tot_m - done["m"], 3)}
    return dict(det=det, tot=tot_s, tot_m=tot_m, tot_g=tot_g, left=left, done=round(done["mn"] + done["hd"] + done["ex"], 3), k=done["k"])


def handle_job(rw, text, photos, msg_id, at=None, reply_to=None, machine=None):
    """-> (job_id, ack)"""
    import ready as R
    at = at or _now()
    photo = photos[0] if photos else ""
    ocr = ""
    db = A.db()
    # жавоб қилиб ёзилди — аввалги аниқланмаган ёки ёзувсиз ишни тузатиш
    old = None
    if reply_to and not photos:
        old = A.q("SELECT * FROM raspil_job WHERE rw_id=? AND (msg_id=? OR ack_id=?) AND status IN ('unknown','many','nodoc','nocap') "
                  "ORDER BY id DESC LIMIT 1", (rw["id"], reply_to, reply_to), one=True)
    if old and old["status"] == "nocap" and old["iddoc"] and not R.NUM_RE.search(text or ""):
        doc, st, info = dict(iddoc=old["iddoc"], no=old["doc_no"], date=None), "ok", "ёзув жавобда"
    else:
        if not R.NUM_RE.search(text or "") and photos:
            ocr = "\n".join(R.ocr_image(p) for p in photos[:3])
        doc, st, info = R.pick_doc(text, ocr)
    crews = open_crews(kw=rw.get("kw_id"), rw=rw["id"], at=at)
    if DP_RE.search(text or ""):                       # ёзувда машина айтилган бўлса — шу смена
        crews = [c for c in crews if c.get("machine") == "dp"] or crews
    elif ARRA_RE.search(text or ""):
        crews = [c for c in crews if c.get("machine", "arra") == "arra"] or crews
    crew = crews[0] if crews else None
    if machine:                                        # бот: қайси станок иши аниқ — шу станокдаги экипаж ёки ишчининг ўзи
        crew = who_for(rw, machine, at)
    else:
        machine = (crew.get("machine") or "arra") if crew else ("dp" if DP_RE.search(text or "") else "arra")
    op, helper = (crew["op_id"], crew["helper_id"]) if crew else (rw.get("kw_id"), None)
    day = crew["day"] if crew else at.date().isoformat()
    ins = dict(machine=machine, at=_fmt(at), day=day, crew_id=crew["id"] if crew else None, op_id=op, helper_id=helper, rw_id=rw["id"], msg_id=msg_id,
               text=(text or "")[:500], photo=photo if not old else old["photo"], ocr=ocr[:4000] if not old else old["ocr"])
    sheets = m45 = 0.0
    det = []
    if doc:
        import c1
        client = R.doc_client(doc["iddoc"])[0]
        ins.update(iddoc=doc["iddoc"], doc_no=doc["no"], client=client)
        sd = dict(lines=sale_lines(doc["iddoc"]))
        ins["mats"] = json.dumps(materials(sd.get("lines") or []), ensure_ascii=False)
        tot_s, tot_m, det = calc(sd.get("lines") or [])
        tot_g = {"main": 0.0, "hdf": 0.0, "extra": 0.0}
        for lab, _q, _u, sv, _nt in det:
            tot_g[grp(lab)] += sv
        done = A.q("SELECT COALESCE(SUM(sh_main),0) mn, COALESCE(SUM(sh_hdf),0) hd, COALESCE(SUM(sh_extra),0) ex, COALESCE(SUM(m45),0) m, COUNT(*) k, MAX(at) at "
                   "FROM raspil_job WHERE iddoc=? AND status='ok'", (doc["iddoc"],), one=True)
        left = {"main": round(tot_g["main"] - done["mn"], 3), "hdf": round(tot_g["hdf"] - done["hd"], 3),
                "extra": round(tot_g["extra"] - done["ex"], 3)}
        if machine == "dp":
            allowed = ("extra",)                           # двухпилка: столешница, 45°, кусок, бурчак
        else:
            allowed = (("extra",) if re.search(r"фақат\s+кусок|faqat\s+kusok", text or "", re.I) else
                       groups_of(text) + (("extra",) if EXTRA_RE.search(text or "") else ()))
        left_m = round(tot_m - done["m"], 3) if "extra" in allowed else 0.0
        want = part_count(text, doc["no"])
        avail = sum(max(left[g], 0) for g in allowed)
        cap = re.sub(r"(?<!\d)" + re.escape(str(doc["no"]).lstrip("0")) + r"(?!\d)", " ", (text or "").replace(str(doc["no"]), " "))
        cap = re.sub(r"[\s№#.,:;!?\-]+", "", cap)
        if tot_s <= 0 and tot_m <= 0:
            st = "zero"
        elif not cap:
            st, info = "nocap", ""
        elif machine == "dp" and tot_g["extra"] <= 0 and tot_m <= 0:
            st, info = "zero", "накладнойда двухпилка иши (столешница, 45°, кусок, бурчак) йўқ"
        elif allowed == ("hdf",) and tot_g["hdf"] <= 0:
            st, info = "zero", "накладнойда ХДФ распили йўқ"
        elif avail <= 0.001 and left_m <= 0.001 and machine == "arra" and left["main"] <= 0.001 and left["hdf"] <= 0.001 and (left["extra"] > 0.001 or tot_m - done["m"] > 0.001):
            st, info = "zero", "қолгани двухпилка иши (столешница, 45°, кусок) — двухпилкачи ташлайди; арра марказида кесилган бўлса «кусок билан» деб ёзинг"
        elif avail <= 0.001 and left_m <= 0.001 and "hdf" not in allowed and left["hdf"] > 0.001:
            st, info = "zero", f"ХДФ {_n(left['hdf'])} лист кутилмоқда — кесганда расм тагига «хдф» деб ёзинг"
        elif avail <= 0.001 and left_m <= 0.001:
            what = {"main": "ЛДСП ва бошқа листлар", "hdf": "ХДФ"}.get(allowed[0]) if len(allowed) == 1 else ""
            st, info = "dup", (f"{what}: " if what else "") + f"{done['at'][8:10]}.{done['at'][5:7]} {done['at'][11:16]}, жами {_n(tot_s)} лист"
        else:
            take, rem = {}, want
            for g in ("main", "hdf", "extra"):
                if g in allowed:
                    x = max(left[g], 0) if rem is None else min(max(left[g], 0), rem)
                    take[g] = round(x, 3)
                    if rem is not None:
                        rem -= x
            sheets = round(sum(take.values()), 3)
            m45 = max(left_m, 0)
            st = "ok"
            ins.update(sh_main=take.get("main", 0.0), sh_hdf=take.get("hdf", 0.0), sh_extra=take.get("extra", 0.0))
            part = dict(total=tot_s, before=round(done["mn"] + done["hd"] + done["ex"], 3),
                        left=round(sum(max(v, 0) for v in left.values()) - sheets, 3),
                        extra_left=round(max(left["extra"], 0) - take.get("extra", 0), 3),
                        hdf_left=round(max(left["hdf"], 0) - take.get("hdf", 0), 3), want=want, only=allowed[0] if len(allowed) == 1 else "")
            det = det + [("_part", 0, "", 0, json.dumps(part))] if (done["k"] or want or part["left"] > 0.001) else det
    ins.update(sheets=sheets, m45=m45, detail=json.dumps(det, ensure_ascii=False), status=st, info=str(info)[:500])
    if old:
        db.execute("UPDATE raspil_job SET " + ", ".join(f"{k}=?" for k in ins) + " WHERE id=?", tuple(ins.values()) + (old["id"],))
        jid = old["id"]
    else:
        jid = db.execute("INSERT INTO raspil_job(" + ",".join(ins) + ") VALUES(" + ",".join("?" * len(ins)) + ")", tuple(ins.values())).lastrowid
    db.commit()
    if st == "ok":
        sync_kpi(day)
    no = ins.get("doc_no") or (info if st == "nodoc" else "")
    if st == "ok":
        who = wname(op) + (f" + {wname(helper)}" if helper else "")
        t = day_totals(day)
        today = t.get(op, {})
        pi = part_info(det)
        lines = [f"✅ №{no} — {ins['client']}", detail_text([d for d in det if d[0] != "_part"]),
                 f"Ҳисобланди: {_n(sheets)} лист" + (f", 45°: {_n(m45)} м" if m45 else ""),
                 (f"Накладнойда жами {_n(pi['total'])} лист" + (f", олдин {_n(pi['before'])}" if pi["before"] else "")
                  + (f", қолди {_n(pi['left'])}" + "".join(
                        [f" (шундан ХДФ {_n(pi['hdf_left'])} — кесганда «хдф» деб ташланг)" if pi.get("hdf_left", 0) > 0.001 else "",
                         f" (шундан двухпилка иши {_n(pi['extra_left'])} — двухпилкачи ташлайди)" if pi.get("extra_left", 0) > 0.001 and machine == "arra" else ""])
                     + "." if pi["left"] > 0.001 else ", тўлиқ кесилди.")) if pi else "",
                 f"👷 {who}" + (f" · бугун {_n(today.get('d', 0) + today.get('n', 0))} лист" if today else "")]
        if today and today.get("n"):
            lines.append(f"(2-смена: {_n(today['n'])} лист)")
        if not crew:
            lines.append("⚠️ Смена очилмаган — фақат сизга ёзилди. Иш бошида «Оператор …, ёрдамчи …» деб ёзинг.")
        lines.append("Адашган бўлса, шу хабарга «бекор» деб жавоб ёзинг.")
        return jid, "\n".join(l for l in lines if l)
    ex = no or "6241"
    return jid, {
        "dup": f"ℹ️ №{no} аввал тўлиқ ҳисобланган ({info}). Қайта ҳисобланмади.",
        "nocap": (f"✍️ №{no} — {ins.get('client', '')}: ёзув йўқ, ҳисобланмади.\n"
                  "Нима кесилганини шу хабарга жавоб қилиб ёзинг: «кесилди» (ХДФсиз), «ҳаммаси» (ХДФ билан), «хдф» (фақат ХДФ) ёки «5 та»."
                  + ("\nНакладнойда: " + detail_text(det) if det else "")),
        "zero": f"⚠️ №{no} — {ins.get('client', '')}: " + (info if info.startswith(("накладнойда ХДФ", "ХДФ ", "қолгани", "накладнойда двухпилка")) else "накладнойда распил хизмати йўқ") + ", ҳисобланмади.",
        "nodoc": f"⚠️ №{no} накладной 1С'да топилмади. Рақамни текшириб, шу хабарга жавоб қилиб ёзинг.",
        "many": f"❓ Бир нечта рақам чиқди: {info}. Тўғрисини шу хабарга жавоб қилиб ёзинг.",
        "unknown": f"❓ Накладной рақамини аниқлай олмадим. Рақамини шу хабарга жавоб қилиб ёзинг (масалан: «{ex}»).",
    }.get(st, "⚠️ Хатолик")


def cancel_job(jid, by=""):
    r = A.q("SELECT * FROM raspil_job WHERE id=?", (jid,), one=True)
    if not r or r["status"] != "ok":
        return False, "бу иш ҳисобланмаган ёки аввал бекор қилинган"
    A.db().execute("UPDATE raspil_job SET status='cancelled', info=? WHERE id=?", (f"бекор: {by}"[:200], jid))
    A.db().commit()
    sync_kpi(r["day"])
    return True, f"№{r['doc_no']} бекор қилинди"


def _cancel_from_worker(rw, m):
    r = None
    if m.get("reply_to"):
        r = A.q("SELECT * FROM raspil_job WHERE rw_id=? AND (msg_id=? OR ack_id=?) ORDER BY id DESC LIMIT 1",
                (rw["id"], m["reply_to"], m["reply_to"]), one=True)
        if r is None:
            c = A.q("SELECT * FROM raspil_crew WHERE by_rw=? AND msg_id=? AND cancelled=0", (rw["id"], m["reply_to"]), one=True)
            if c:
                A.db().execute("UPDATE raspil_crew SET cancelled=1 WHERE id=?", (c["id"],)); A.db().commit()
                return "🗑 Смена бекор қилинди. Тўғрисини қайта ёзинг: «Оператор …, ёрдамчи …»."
    if r is None:
        since = _fmt(_now() - timedelta(hours=2))
        r = A.q("SELECT * FROM raspil_job WHERE rw_id=? AND status='ok' AND at>=? ORDER BY id DESC LIMIT 1", (rw["id"], since), one=True)
    if r is None:
        return "❓ Бекор қилинадиган иш топилмади. «✅» жавобига «бекор» деб reply қилинг."
    ok, txt = cancel_job(r["id"], by=rw.get("name") or "ишчи")
    return ("🗑 " if ok else "⚠️ ") + txt + "."


def process(msgs):
    """ready.poll'дан: распилчиларнинг хабарлари. -> [(ent, msg_id, text, ("raspil", job_id|None))]"""
    import ready as R
    replies = []
    items = []
    for m in sorted(msgs, key=lambda m: (m["worker"]["id"], m["msg_id"])):
        last = items[-1] if items else None
        if m["photo"] and last and last["photos"] and m["grouped"] and last["grouped"] == m["grouped"] and last["worker"]["id"] == m["worker"]["id"]:
            last["photos"].append(m["photo"])
            last["text"] = (last["text"] + " " + m["text"]).strip()
            continue
        items.append(dict(m, photos=[m["photo"]] if m["photo"] else []))
    for it in items:
        rw, text = it["worker"], (it["text"] or "").strip()
        at = datetime.fromtimestamp(it["date"]) if it.get("date") else _now()
        try:
            if not it["photos"] and R.CANCEL_RE.search(text):
                replies.append((it["ent"], it["msg_id"], _cancel_from_worker(rw, it), None))
            elif not it["photos"] and END_RE.search(text):
                replies.append((it["ent"], it["msg_id"], end_crew(rw, at), None))
            elif not it["photos"] and START_RE.search(text):
                replies.append((it["ent"], it["msg_id"], start_crew(rw, text, it["msg_id"], at), None))
            elif it["photos"] or R.NUM_RE.search(text) or (text and it.get("reply_to") and A.q(
                    "SELECT 1 FROM raspil_job WHERE rw_id=? AND (msg_id=? OR ack_id=?) AND status IN ('unknown','many','nodoc','nocap')",
                    (rw["id"], it["reply_to"], it["reply_to"]), one=True)):
                jid, ack = handle_job(rw, text, it["photos"], it["msg_id"], at, it.get("reply_to"))
                replies.append((it["ent"], it["msg_id"], ack, ("raspil", jid)))
            elif text:
                replies.append((it["ent"], it["msg_id"], "ℹ️ Распил: иш бошида «Оператор Жасур, ёрдамчи Улмас», кейин ҳар накладной расмини ташланг (тагига «кесилди», «ҳаммаси», «хдф» ёки «5 та»), "
                                "охирида «иш тугади» деб ёзинг.", None))
        except Exception:
            A.errlog.exception("raspil process")
            replies.append((it["ent"], it["msg_id"], "⚠️ Платформада хатолик — админ текширади.", None))
    return replies


def set_ack(jid, aid):
    A.db().execute("UPDATE raspil_job SET ack_id=? WHERE id=?", (aid, jid))


# ---------------------------------------------------------------- ҳисоб: кун/ой, KPI'га ёзиш
TOT0 = dict(d=0.0, n=0.0, m45=0.0, pp=0.0, ps=0.0, km_d=0.0, km_n=0.0, ov=0.0, ut=0.0, rv=0.0, rs=0.0, docs=0, hours=0.0)
TOT_KEYS = ("d", "n", "m45", "pp", "ps", "km_d", "km_n", "ov", "ut", "rv", "rs", "docs", "hours")


def day_totals(day):
    """{kw: dict(d, n, m45, docs, start, end, hours)} — 1-смена ишчи бошлаган вақтдан N соат, кейин 2-смена."""
    hrs = cfg("raspil_shift_hours")
    out = {}
    jobs = A.q("SELECT * FROM raspil_job WHERE day=? AND status='ok' ORDER BY at", (day,))
    for j in jobs:
        for kw in (j["op_id"], j["helper_id"]):
            if not kw:
                continue
            t = out.get(kw)
            if t is None:
                st = worker_start(kw, day) or _ts(j["at"])
                t = out[kw] = dict(TOT0, start=st, end=st)
            at = _ts(j["at"])
            m = j["machine"] or "arra"
            late = at - t["start"] >= timedelta(hours=hrs)
            sh = "n" if m == "arra" and late else "d"   # двухпилка — ҳозирча 2-сменасиз
            t[sh] += j["sheets"]
            t["m45"] += j["m45"]
            if m == "pris":
                t["pp"] += j["pr_pcs"] or 0
                t["ps"] += j["pr_sum"] or 0
            elif m == "kromka":
                t["km_n" if late else "km_d"] += j["pr_pcs"] or 0
            elif m == "oval":
                t["ov"] += j["pr_pcs"] or 0
            elif m == "utol":
                t["ut"] += j["pr_pcs"] or 0
            elif m == "rover":
                t["rv"] += j["pr_pcs"] or 0
                t["rs"] += j["pr_sum"] or 0
            t["docs"] += 1
            t["end"] = max(t["end"], at)
    for kw, t in out.items():
        r = A.q("SELECT MAX(ended_at) e FROM raspil_crew WHERE day=? AND cancelled=0 AND ended_at<>'' AND (op_id=? OR helper_id=?)",
                (day, kw, kw), one=True)
        if r and r["e"]:
            t["end"] = max(t["end"], _ts(r["e"]))
        t["hours"] = max(0.0, (t["end"] - t["start"]).total_seconds() / 3600)
    return out


def machine_totals(day):
    """Станок бўйича: [{machine, kw, start, end, sheets, m45, pp, cnc, docs}] — ким қайси станокда нима қилди."""
    out = {}
    for j in A.q("SELECT * FROM raspil_job WHERE day=? AND status='ok' ORDER BY at", (day,)):
        m = j["machine"] or "arra"
        fr = float(json.loads(j["items"] or "{}").get("frac", 1.0)) if m in JOB_MACHINES else 1.0
        cnc = sum(d[1] for d in json.loads(j["detail"] or "[]") if len(d) > 4 and d[4] == "%") * fr if m == "pris" else 0
        wq = (j["pr_pcs"] or 0) if m in WORK else 0
        ws = (j["pr_sum"] or 0) if m == "rover" else 0
        for kw in (j["op_id"], j["helper_id"]):
            if not kw:
                continue
            t = out.setdefault((m, kw), dict(machine=m, kw=kw, start=j["at"][11:16], end=j["at"][11:16], sheets=0.0, m45=0.0, pp=0.0, cnc=0.0,
                                             wq=0.0, ws=0.0, unit=WORK.get(m, {}).get("unit", ""), docs=0))
            t["end"] = j["at"][11:16]
            t["sheets"] += j["sheets"]; t["m45"] += j["m45"]; t["cnc"] += cnc; t["docs"] += 1
            t["pp"] += (j["pr_pcs"] or 0) if m == "pris" else 0
            t["wq"] += wq; t["ws"] += ws
    for c in A.q("SELECT * FROM raspil_crew WHERE day=? AND cancelled=0 ORDER BY started_at", (day,)):
        for kw in (c["op_id"], c["helper_id"]):
            if not kw or is_test_kw(kw):
                continue
            m = c["machine"] or "arra"
            t = out.get((m, kw))
            if not t:                                     # станокда турибди, лекин ҳали иш ёзилмаган
                t = out[(m, kw)] = dict(machine=m, kw=kw, start=c["started_at"][11:16], end="", sheets=0.0, m45=0.0, pp=0.0, cnc=0.0,
                                        wq=0.0, ws=0.0, unit=WORK.get(m, {}).get("unit", ""), docs=0, now=False)
            t["start"] = min(t["start"], c["started_at"][11:16])
            if c["ended_at"]:
                t["end"] = max(t["end"], c["ended_at"][11:16])
            else:
                t["now"] = True
            t["role"] = "бош оп." if kw == c["op_id"] else "ёрдамчи"
    order = {m: i for i, m in enumerate(MACHINES)}
    return sorted(out.values(), key=lambda t: (order.get(t["machine"], 9), t["start"]))


def _common_rate(db, kind, shift, ym, default):
    """Шу ойда бошқа ишчилардаги шу иш (тур+смена) учун энг кўп ишлатилган ставка; йўқ бўлса — созламадаги."""
    r = db.execute("SELECT m.rate, COUNT(*) n FROM k_kpi_cols c JOIN k_kpi_mcols m ON m.col_id=c.id WHERE c.kind=? AND c.shift=? AND m.ym<=? "
                   "AND m.rate>0 GROUP BY m.rate ORDER BY n DESC, MAX(m.ym) DESC LIMIT 1", (kind, shift, ym)).fetchone()
    return r[0] if r else default


def _kpi_col(db, kw, kind, shift, ym, rate):
    row = db.execute("SELECT id, rate FROM k_kpi_cols WHERE worker_id=? AND kind=? AND shift=?", (kw, kind, shift)).fetchone()
    if row:
        cid = row[0]
        db.execute("UPDATE k_kpi_cols SET active=1 WHERE id=?", (cid,))
    else:
        mx = db.execute("SELECT COALESCE(MAX(sort),0) FROM k_kpi_cols").fetchone()[0]
        rate = _common_rate(db, kind, shift, ym, rate)    # бу ишни биринчи марта қилди — бошқа ишчилардаги шу иш ставкаси
        cid = db.execute("INSERT INTO k_kpi_cols(worker_id,kind,shift,rate,sort) VALUES(?,?,?,?,?)", (kw, kind, shift, rate, mx + 10)).lastrowid
    if not db.execute("SELECT 1 FROM k_kpi_mcols WHERE ym=? AND col_id=?", (ym, cid)).fetchone():
        prev = db.execute("SELECT rate FROM k_kpi_mcols WHERE col_id=? AND ym<? ORDER BY ym DESC LIMIT 1", (cid, ym)).fetchone()
        mx = db.execute("SELECT COALESCE(MAX(sort),0) FROM k_kpi_mcols WHERE ym=?", (ym,)).fetchone()[0]
        db.execute("INSERT INTO k_kpi_mcols(ym,col_id,rate,sort) VALUES(?,?,?,?)",
                   (ym, cid, prev[0] if prev else (db.execute("SELECT rate FROM k_kpi_cols WHERE id=?", (cid,)).fetchone()[0] or rate), mx + 10))
    return cid


def sync_kpi(day):
    """Автомат ой бўлса — шу кун распил, 45° ва присадка сонларини касса KPI жадвалига ёзади."""
    if not auto_on(day):
        return
    import kassa
    ym, d = day[:7], int(day[8:10])
    kassa.ensure_kpi_month(ym)
    db = A.db()
    tot = day_totals(day)
    involved = {r[0] for r in db.execute("SELECT op_id FROM raspil_job WHERE day=? UNION SELECT helper_id FROM raspil_job WHERE day=?",
                                         (day, day)) if r[0]}
    m45rate = cfg("raspil_m45_rate")
    for kw in involved | set(tot):
        if is_test_kw(kw):
            continue                                      # синов ишчиси — ойликка ёзилмайди
        t = tot.get(kw, TOT0)
        for kind, shift, v, rate in (("raspil", "d", t["d"], 0), ("raspil", "n", t["n"], 0), (M45_KIND, "d", t["m45"], m45rate),
                                     (PRIS_KIND, "d", t["pp"], cfg("pris_rate")), (PRIS_PCT_KIND, "d", t["ps"], cfg("pris_pct") / 100),
                                     ("kromka", "d", t["km_d"], cfg("kromka_rate")), ("kromka", "n", t["km_n"], cfg("kromka_rate")),
                                     ("oval", "d", t["ov"], cfg("oval_rate")), ("utol", "d", t["ut"], cfg("utol_rate")),
                                     ("rover", "d", t["rs"], cfg("rover_pct") / 100)):
            exists = db.execute("SELECT id FROM k_kpi_cols WHERE worker_id=? AND kind=? AND shift=?", (kw, kind, shift)).fetchone()
            if not v and not exists:
                continue
            cid = _kpi_col(db, kw, kind, shift, ym, rate)
            if d in _locked(db, ym, cid):
                continue                                  # қўлда ўзгартирилган
            if v:
                db.execute("INSERT OR REPLACE INTO k_kpi(ym,day,col_id,qty) VALUES(?,?,?,?)", (ym, d, cid, round(v, 3)))
            else:
                db.execute("DELETE FROM k_kpi WHERE ym=? AND day=? AND col_id=?", (ym, d, cid))
    db.commit()
    kassa._memo.clear()


def resync_month(ym):
    """Ой бўйича KPI (ботдаги ишлардан) ва табел (ботда «Ишга келдим»дан) қайта ёзилади.
    Қўлда ўзгартирилган KPI катаклари ва табелда ёзилган катаклар — тегилмайди."""
    days = [r[0] for r in A.q("SELECT DISTINCT day FROM raspil_job WHERE day LIKE ?", (ym + "-%",))]
    for d in days:
        sync_kpi(d)
    for r in A.q("SELECT day, kw FROM k_att WHERE day LIKE ?", (ym + "-%",)):
        try:
            mark_tabel(r["kw"], r["day"], only_existing=True)
        except Exception:
            A.errlog.exception("tabel resync")
    return len(days)


def month_totals(ym):
    out = defaultdict(lambda: dict(TOT0, days=0))
    for (day,) in A.q("SELECT DISTINCT day FROM raspil_job WHERE day LIKE ? AND status='ok'", (ym + "-%",)):
        for kw, t in day_totals(day).items():
            o = out[kw]
            for k in TOT_KEYS:
                o[k] += t[k]
            o["days"] += 1
    return dict(out)


# ---------------------------------------------------------------- саҳифа
STATUS = {"wait": ("⏳ накладной кутилмоқда", "neg"), "linked": ("накладнойга уланди", "muted"), "ok": ("ҳисобланди", "pos"), "nocap": ("ёзув йўқ", "neg"), "dup": ("такрор", "muted"), "zero": ("распил йўқ", "muted"), "nodoc": ("1С'да йўқ", "neg"),
          "unknown": ("рақам аниқланмади", "neg"), "many": ("бир нечта рақам", "neg"), "cancelled": ("бекор", "muted"), "error": ("хато", "neg")}


def register(app_module):
    global A
    A = app_module
    app, lr = A.app, A.login_required
    con = A.connect()
    init_tables(con)
    con.commit(); con.close()
    import kassa
    kassa.KPI_KINDS.setdefault(M45_KIND, "Распил 45° (м)")
    kassa.KPI_KINDS.setdefault(KROY_KIND, "Крой ЛДСП/ЛМДФ (лист)")
    kassa.KPI_KINDS.setdefault(KROY_HDF_KIND, "Крой ХДФ/ДВП (лист)")
    kassa.KPI_KINDS.setdefault(PRIS_KIND, "Присадка (тешик)")
    kassa.KPI_KINDS.setdefault(PRIS_PCT_KIND, "Присадка ЧПУ/паз (сўм×%)")
    kassa.KPI_KINDS.setdefault(BASIS_KIND, "Базис (1С сумма×%)")
    kassa.KPI_KINDS.setdefault(BASIS_PRIS_KIND, "Присадкадан % (Базис)")
    kassa.KPI_KINDS.setdefault("oval", "Овал кромка (м)")
    for fn in (resync_month, basis_sync):
        if fn not in kassa.KPI_RESYNC:
            kassa.KPI_RESYNC.append(fn)
    kassa.KPI_KINDS.setdefault("utol", "Утолщенка (м)")
    kassa.KPI_KINDS.setdefault("rover", "Ровер (сўм×%)")
    _orig = A.start_background_jobs

    def start_background_jobs():
        first = not A._bg["started"]
        _orig()
        if first:
            import threading
            threading.Thread(target=_basis_loop, name="basis", daemon=True).start()
    A.start_background_jobs = start_background_jobs
    # «Цех ишлари» кассирга кўринмайди (фақат админ)

    def _money_ru(v, cur):
        import i18n
        return (f"{v:,.2f}".replace(",", " ").replace(".", ",") + " $") if cur == "USD" else (f"{v:,.0f}".replace(",", " ") + " " + i18n.tr("сум"))

    def _total_ru(rows):
        t = {}
        for r in rows:
            t[r["cur"]] = t.get(r["cur"], 0) + (r["amount"] or 0)
        return " + ".join(_money_ru(t[c], c) for c in ("UZS", "USD") if t.get(c))

    def wcash_summary():
        """Кассовый день: ишчиларда турган пул (кассага топширилмаган)."""
        try:
            rows = cash_rows()
        except Exception:
            return None
        return dict(total=_total_ru(rows), n=len(rows)) if rows else None
    app.jinja_env.globals["wcash_summary"] = wcash_summary

    @app.route("/kassa/wcash", methods=["GET", "POST"])
    @lr
    def kassa_wcash():
        """Деньги у работников: ишчи мижоздан олган пул («💵 Пул олдим») — кассир қабул қилади."""
        if request.method == "POST":
            u = A.current_user()
            rows = cash_accept(request.form.getlist("id"), u["username"] if u else "")
            flash(("Принято: " + _total_ru(rows)) if rows else "Нечего принимать — уже принято или отменено", "ok" if rows else "warn")
            return redirect(url_for("kassa_wcash"))

        def prep(rows):
            for r in rows:
                r["what"] = cash_label(r)
                r["money"] = _money_ru(r["amount"], r["cur"])
                w = code_row(r["code"]) if r["code"] else None
                r["doc_id"] = w["doc_id"] if w and w["doc_id"] else None
            return rows
        held = prep(cash_rows())
        per = {}
        for r in held:
            per.setdefault((r["rw_id"], r["wname"] or "?"), []).append(r)
        groups = [dict(name=k[1], rows=v, total=_total_ru(v)) for k, v in per.items()]
        done = prep(cash_rows(status="handed", days=7))[::-1][:100]
        return render_template("kassa_wcash.html", groups=groups, done=done, total=_total_ru(held))

    @app.route("/kassa/raspil", methods=["GET", "POST"])
    @lr
    def kassa_raspil():
        day = request.values.get("day") or date.today().isoformat()
        try:
            dd = date.fromisoformat(day)
        except ValueError:
            dd = date.today()
        day = dd.isoformat()
        if request.method == "POST":
            f, act, db = request.form, request.form.get("action"), A.db()
            adm = A.is_admin()
            if act == "cancel":
                ok, txt = cancel_job(int(A.parse_float(f.get("jid"), 0)), by=A.current_user()["username"])
                flash(txt if ok else "Бекор қилинмади: " + txt, "ok" if ok else "error")
            elif act == "settings" and adm:
                vals = {k: str(A.parse_float(f.get(k), float(DEF[k]))) for k in DEF}
                af = (f.get("auto_from") or "").strip()[:7]
                if re.fullmatch(r"\d{4}-\d{2}", af):
                    vals["auto_from"] = af
                vals["pris_mult"] = (f.get("pris_mult") or "").strip()[:300]
                A.set_settings(**vals)
                n = resync_month(day[:7]) if auto_on(day) else 0
                flash("Сақланди" + (f"; KPI'га {n} кун ёзилди" if n else ""), "ok")
            elif act == "addphone" and adm:
                import ready as R
                ph, kw = R.norm_phone(f.get("phone")), int(A.parse_float(f.get("kw"), 0))
                role = "raspil" if f.get("role") != "staff" else "staff"
                if not ph or not kw:
                    flash("Рақам ёки ишчи нотўғри", "error")
                else:
                    name = wname(kw)
                    if A.q("SELECT 1 FROM ready_workers WHERE phone=?", (ph,), one=True):
                        db.execute("UPDATE ready_workers SET role=?, kw_id=?, active=1 WHERE phone=?", (role, kw, ph))
                    else:
                        db.execute("INSERT INTO ready_workers(name, phone, created_at, role, kw_id) VALUES(?,?,?,?,?)",
                                   (name + (" (распил)" if role == "raspil" else ""), ph, A.now(), role, kw))
                    db.commit()
                    flash(f"{name} — распил рақами қўшилди. 30 сониядан кейин хабарлари ўқилади.", "ok")
            elif act in ("wlink", "wok", "wdel"):
                jid = int(A.parse_float(f.get("jid"), 0))
                who = A.current_user()["username"]
                if act == "wlink":
                    import ready as R
                    try:
                        doc = R.find_doc((f.get("doc_no") or "").strip())
                    except Exception as e:
                        A.errlog.error("raspil wlink: %r", e)
                        doc = None
                    if not doc:
                        flash("Накладной 1С'да топилмади", "error")
                    else:
                        ok, txt = link_wait(jid, doc["iddoc"], doc["no"], R.doc_client(doc["iddoc"])[0], by=who)
                        flash(txt, "ok" if ok else "error")
                elif act == "wok" and adm:
                    ok, txt = approve_wait(jid, by=who)
                    flash(txt, "ok" if ok else "error")
                elif act == "wdel" and adm:
                    A.db().execute("UPDATE raspil_job SET status='cancelled', info=? WHERE id=? AND status='wait'", (f"бекор: {who}", jid))
                    A.db().commit()
                    kusok_unsell(jid)
                    notify_mgr(wait_code(jid))
                    flash("Бекор қилинди", "ok")
            elif act == "kadd":
                ps = parse_size(f"{f.get('size', '')} {f.get('color', '')}")
                own = parse_owner(f.get("owner")) if (f.get("owner") or "").strip() else OWNER_CO
                tp = dict(kusok_types()).get(f.get("gid"), "")
                if not ps or not own or not tp:
                    flash("Тури, ўлчами (2800 1000) ва эгаси (РайГаз ёки «Исм 1131») керак", "error")
                else:
                    kusok_add({"name": A.current_user()["username"]}, f.get("gid"), tp, ps[2], ps[1], ps[0], own)
                    flash(f"📥 Омборга қўшилди: {tp} {ps[2]} {ps[1]} — {_n(ps[0])} м²", "ok")
            elif act == "kdel" and adm:
                A.db().execute("UPDATE raspil_kusok SET status='del', note=? WHERE id=? AND status='in'",
                               (f"ўчирди: {A.current_user()['username']}", int(A.parse_float(f.get("kid"), 0))))
                A.db().commit()
                flash("Омбордан ўчирилди", "ok")
            elif act == "basis" and adm:
                mp = {}
                for fid, _nm in basis_designers():
                    v = int(A.parse_float(f.get(f"bm_{fid}"), 0))
                    if v:
                        mp[fid] = v
                A.set_settings(basis_map=json.dumps(mp),
                               basis_pct=str(A.parse_float(f.get("basis_pct"), 50)), basis_pris_pct=str(A.parse_float(f.get("basis_pris_pct"), 5)),
                               basis_pris_kw=str(int(A.parse_float(f.get("basis_pris_kw"), 0))))
                try:
                    basis_sync(day[:7])
                except Exception as e:
                    A.errlog.exception("basis sync")
                    flash(f"1С'дан ўқиб бўлмади: {e}", "error")
                flash("Базис созламаси сақланди", "ok")
            elif act == "basis_pris" and adm:
                A.set_settings(basis_pris_on="0" if A.get_setting("basis_pris_on", "1") == "1" else "1")
                try:
                    basis_sync(day[:7])
                except Exception:
                    A.errlog.exception("basis sync")
                flash("Присадкадан % " + ("ёқилди" if A.get_setting("basis_pris_on") == "1" else "ўчирилди"), "ok")
            elif act == "bot" and adm:
                import raspil_bot
                if f.get("disconnect"):
                    A.set_settings(raspil_bot_token="", raspil_bot_username="", raspil_bot_error="")
                    flash("Бот узилди", "ok")
                else:
                    ok, txt = raspil_bot.connect(f.get("token"))
                    flash(("Бот уланди: " if ok else "Уланмади: ") + txt, "ok" if ok else "error")
            elif act == "owner_add" and adm:
                import ready as R
                ph, nm = R.norm_phone(f.get("phone")), (f.get("name") or "").strip()[:60]
                if not ph or not nm:
                    flash("Исм ва рақам керак", "error")
                else:
                    if A.q("SELECT 1 FROM ready_workers WHERE phone=?", (ph,), one=True):
                        db.execute("UPDATE ready_workers SET owner=1, active=1 WHERE phone=?", (ph,))
                    else:
                        db.execute("INSERT INTO ready_workers(name, phone, created_at, role, owner) VALUES(?,?,?,?,1)", (nm, ph, A.now(), "ready"))
                    db.commit()
                    flash(f"{nm} — ҳисобот ботига қўшилди. Ботда «Рақамимни юбориш»ни боссин.", "ok")
            elif act == "owner_del" and adm:
                db.execute("UPDATE ready_workers SET owner=0 WHERE id=?", (int(A.parse_float(f.get("id"), 0)),))
                db.commit()
                flash("Олиб ташланди", "ok")
            elif act == "owner_hour" and adm:
                v = (f.get("owner_hour") or "19:00").strip()[:5]
                if re.fullmatch(r"\d{1,2}:\d{2}", v):
                    A.set_settings(owner_hour=v)
                    flash("Сақланди", "ok")
            elif act == "kroyflag" and adm:
                db.execute("UPDATE ready_workers SET kroy=1-COALESCE(kroy,0) WHERE id=?", (int(A.parse_float(f.get("id"), 0)),))
                db.commit()
                flash("Сақланди", "ok")
            elif act == "kroy_set" and adm:
                kf = (f.get("kroy_from") or "").strip()[:7]
                A.set_settings(kroy_m2_main=str(A.parse_float(f.get("kroy_m2_main"), 5.796) or 5.796),
                               kroy_m2_hdf=str(A.parse_float(f.get("kroy_m2_hdf"), 5.796) or 5.796),
                               kroy_rate_main=str(A.parse_float(f.get("kroy_rate_main"), 0)),
                               kroy_rate_hdf=str(A.parse_float(f.get("kroy_rate_hdf"), 0)),
                               **({"kroy_from": kf} if re.fullmatch(r"\d{4}-\d{2}", kf) else {}))
                kroy_sync(day[:7])
                flash("Крой созламаси сақланди", "ok")
            elif act == "kroy_del" and adm:
                db.execute("UPDATE raspil_kroy SET status='del', note=? WHERE id=?", (f"ўчирди: {A.current_user()['username']}", int(A.parse_float(f.get("kid"), 0))))
                db.commit()
                kroy_sync(day[:7])
                flash("Ўчирилди", "ok")
            elif act == "perms" and adm:
                pid_ = int(A.parse_float(f.get("id"), 0))
                boss_, staj_ = 1 if f.get("boss") else 0, 1 if f.get("stajer") else 0
                ps = [f"{m}:{r}" for m in MACHINES for r in ("op", "hp") if f.get(f"p_{m}_{r}")]
                db.execute("UPDATE ready_workers SET perms=?, stajer=?, boss=? WHERE id=?", (json.dumps(ps), staj_, boss_, pid_))
                db.commit()
                flash("Доступ сақланди", "ok")
            elif act == "delphone" and adm:
                db.execute("DELETE FROM ready_workers WHERE id=? AND role='staff'", (f.get("id"),))
                db.execute("UPDATE ready_workers SET role='ready', bot_uid=NULL WHERE id=? AND role='raspil'", (f.get("id"),))
                db.commit()
                flash("Рўйхатдан олинди", "ok")
            if f.get("back") == "settings":
                return redirect(url_for("raspil_settings"))
            return redirect(url_for("kassa_raspil", day=day))
        tot = day_totals(day)
        mtot = machine_totals(day)
        crews = A.q("SELECT * FROM raspil_crew WHERE day=? ORDER BY started_at", (day,))
        jobs = A.q("SELECT j.*, w.name rwname FROM raspil_job j LEFT JOIN ready_workers w ON w.id=j.rw_id WHERE j.day=? ORDER BY j.at DESC", (day,))
        jobs = [dict(j, det=detail_text(json.loads(j["detail"] or "[]")), mats_t=mats_text(j["mats"])) for j in jobs]
        mt = month_totals(day[:7])
        rates = {}
        for r in A.q("SELECT c.worker_id, c.kind, c.shift, m.rate FROM k_kpi_cols c JOIN k_kpi_mcols m ON m.col_id=c.id WHERE m.ym=?", (day[:7],)):
            rates[(r["worker_id"], r["kind"], r["shift"])] = r["rate"]
        phones = []
        for r_ in A.q("SELECT * FROM ready_workers WHERE role IN ('raspil','staff') ORDER BY role, name"):
            r_ = dict(r_)
            pp = None if r_.get("perms") in (None, "") else set(json.loads(r_["perms"] or "[]"))
            r_["pset"], r_["ptext"] = pp, ("цех бошлиғи — ҳаммаси" if r_.get("boss") else "стажер" if r_.get("stajer") else
                                            "белгиланмаган — ҳаммаси очиқ" if pp is None else perms_text(pp))
            phones.append(r_)
        owners_ = [dict(r) for r in A.q("SELECT * FROM ready_workers WHERE owner=1 ORDER BY name")]
        att = A.q("SELECT a.*, w.name FROM k_att a LEFT JOIN k_workers w ON w.id=a.kw WHERE a.day=? ORDER BY a.in_at", (day,))
        wl = sorted(waits(days=60), key=lambda w: (w["code"] is None, w["code"] or 0, w["at"]))   # бир мижоз (код) ишлари ёнма-ён
        basis = None
        try:
            des, pris = basis_month(day[:7])
            desu, prisu = basis_month(day[:7], "SP905")
            mp = basis_map()
            basis = dict(rows=[dict(fid=fid, name=nm, kw=mp.get(fid), total=sum(des.get(fid, {}).values()), usd=sum(desu.get(fid, {}).values()))
                               for fid, nm in basis_designers()],
                         pris=sum(pris.values()), pris_usd=sum(prisu.values()), on=A.get_setting("basis_on", "0") == "1", pris_on=A.get_setting("basis_pris_on", "1") == "1",
                         pris_kw=basis_pris_kw(), pct=cfg("basis_pct"), ppct=cfg("basis_pris_pct"), synced=A.get_setting("basis_synced", ""))
        except Exception as e:
            A.errlog.error("basis page: %r", e)
        ym = day[:7]
        try:
            kroy = dict(rows=sorted(kroy_month(ym).items(), key=lambda x: -x[1]["sh_main"]), on=ym >= kroy_from(), frm=kroy_from())
        except Exception:
            A.errlog.exception("kroy page")
            kroy = None
        try:
            kus = kusok_page(ym); ktypes = kusok_types()
        except Exception:
            A.errlog.exception("kusok page")
            kus, ktypes = None, []
        return render_template("kassa_raspil.html", kus=kus, ktypes=ktypes, kroy=kroy, day=day, prev_day=(dd - timedelta(days=1)).isoformat(), next_day=(dd + timedelta(days=1)).isoformat(),
                               tot=tot, mtot=mtot, MACHINES=MACHINES, crews=crews, jobs=jobs, mt=mt, rates=rates, wname=wname, hm=hm_ui, n=_n, STATUS=STATUS, phones=phones, owners=owners_, owner_hour=A.get_setting("owner_hour", "19:00"),
                               ws=workers(), wfio=wfio, wl=wl, att=att, basis=basis, pris_mult=A.get_setting("pris_mult", "эксцентрик=2"), S={k: A.get_setting(k, v) for k, v in DEF.items()}, kpi_on=auto_on(day), auto_from=auto_from(),
                               ym=ym, ym_title=kassa.ym_title(ym), tgu=A.tgu_ready(),
                               bot=dict(user=A.get_setting("raspil_bot_username", ""), on=bool(A.get_setting("raspil_bot_token", "")),
                                        err=A.get_setting("raspil_bot_error", "")))

    @app.route("/kassa/raspil/settings", methods=["GET", "POST"])
    @A.admin_required
    def raspil_settings():
        """Операции → Настройки цеха: ҳисоблаш қоидалари, табель қоидаси, цех боти, шериклар ҳисоботи (аввал «Цех ишлари» журнали остида эди)."""
        if request.method == "POST":
            f = request.form
            A.set_settings(kassa_norm_days=str(A.parse_float(f.get("norm"), 28) or 28).rstrip("0").rstrip("."),
                           kassa_day_hours=str(A.parse_float(f.get("hours"), 9) or 9).rstrip("0").rstrip("."))
            flash("Сохранено", "ok")
            return redirect(url_for("raspil_settings"))
        owners_ = [dict(r) for r in A.q("SELECT * FROM ready_workers WHERE owner=1 ORDER BY name")]
        return render_template("raspil_settings.html", day=date.today().isoformat(), S={k: A.get_setting(k, v) for k, v in DEF.items()},
                               auto_from=auto_from(), pris_mult=A.get_setting("pris_mult", "эксцентрик=2"), owners=owners_,
                               owner_hour=A.get_setting("owner_hour", "19:00"), norm=A.get_setting("kassa_norm_days", "28"),
                               hours=A.get_setting("kassa_day_hours", "9"),
                               bot=dict(user=A.get_setting("raspil_bot_username", ""), on=bool(A.get_setting("raspil_bot_token", "")),
                                        err=A.get_setting("raspil_bot_error", "")))

    @app.route("/kassa/raspil/report")
    @lr
    def kassa_raspil_report():
        today = date.today()
        d1 = request.args.get("d1") or today.replace(day=1).isoformat()
        d2 = request.args.get("d2") or today.isoformat()
        kw = int(A.parse_float(request.args.get("kw"), 0)) or None
        q = request.args.get("q", "")
        only = request.args.get("st", "ok")
        rows = report_rows(d1, d2, kw, q)
        if only == "ok":
            rows = [r for r in rows if r["status"] == "ok"]
        if request.args.get("csv"):
            import xlbrand
            rwk = {r["id"]: r["kw_id"] for r in A.q("SELECT id, kw_id FROM ready_workers")}
            head = ["Сана", "Вақт", "Оператор", "Ёрдамчи", "Накладной №", "Мижоз", "Листлар (накладнойда)", "Распил таркиби",
                    "Ҳисобланган лист", "45°, м", "Ҳолат", "Ким ташлади", "Ишчи изоҳи"]
            data = [[r["day"], r["at"][11:16], wname(r["op_id"]), wname(r["helper_id"]), r["doc_no"], r["client"], r["mats_t"], r["det"],
                     round(r["sheets"], 3), round(r["m45"], 3), STATUS.get(r["status"], (r["status"],))[0], wname(rwk.get(r["rw_id"])), r["text"] or ""]
                    for r in rows]
            kinds = ["date", "text", "text", "text", "text", "text", "text", "text", "qty", "qty", "text", "text", "text"]
            sub = f"{d1[8:10]}.{d1[5:7]}.{d1[:4]} — {d2[8:10]}.{d2[5:7]}.{d2[:4]}" if len(d1) == 10 and len(d2) == 10 else ""
            return xlbrand.book(f"raspil_{d1}_{d2}", "Цех ишлари (распил)", head, data, subtitle=sub, kinds=kinds, totals=True,
                                chart=dict(cat=2, vals=[8], title="Операторлар бўйича листлар", top=10), main_title="Ишлар",
                                kpis=[("Ишлар", len(data), "int"), ("Ҳисобланган лист", sum(x[8] or 0 for x in data), "qty"), ("45°, м", sum(x[9] or 0 for x in data), "qty")])
        tot = sum(r["sheets"] for r in rows if r["status"] == "ok")
        return render_template("kassa_raspil_report.html", rows=rows, d1=d1, d2=d2, kw=kw, q=q, st=only, ws=workers(), n=_n, STATUS=STATUS,
                               tot=tot, m45=sum(r["m45"] for r in rows if r["status"] == "ok"))

    @app.route("/kassa/raspil/photo/<int:jid>")
    @lr
    def kassa_raspil_photo(jid):
        import ready as R
        r = A.q("SELECT photo FROM raspil_job WHERE id=?", (jid,), one=True)
        if not r or not r["photo"] or not os.path.isfile(r["photo"]):
            abort(404)
        if os.path.dirname(os.path.abspath(r["photo"])) != os.path.abspath(R.photo_dir()):
            abort(404)
        return send_file(r["photo"], mimetype="image/jpeg")
