# -*- coding: utf-8 -*-
"""Платформанинг ўз савдо базаси (1С ўрнига, 1-босқич).

• 1С'дан кўзгу: товарлар, мижозлар ва 4 та ҳужжат (Накладная 210, Поступление 430, Поступление денег 1195,
  Переоценка 1133) ҳар 5 дақиқада platform базасига кўчирилади (src='1c', фақат ўқиш учун).
• Платформада ёзиладиган накладной (src='pf'): қоралама -> проведен, рақами 1С билан битта кетма-кетликда (1С ҳам шу рақамни олса — кейинги бўш рақам), 1С ҳисобига таъсир қилмайди
  (параллел синов даври). Ким, қачон, нимани ўзгартирди — erp_log.
• Бир вақтда бир неча киши: таҳрирлашда қулф (2 дақиқа, саҳифа очиқ турса янгиланиб туради) ва версия текшируви.
Пул/валюта мантиғи 1С'дагидек: товар нархи ўз валютасида (1 — сўм, 2 — $), сатр суммаси $ ва сўмда.
"""
import hashlib, json, os, re, threading, time
from datetime import date, datetime, timedelta
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from flask import request, render_template, abort, redirect, url_for, flash, jsonify, session

import c1

A = None
KINDS = {210: "sale", 430: "receipt", 1195: "pay", 1133: "reval"}
KIND_NAMES = {"sale": "Накладная", "receipt": "Поступление товара", "pay": "Поступление денег", "reval": "Переоценка", "inv": "Инвентаризация"}
KASSA_KINDS = ("sale", "pay")          # кассир ёзадиган ва кўрадиган ҳужжатлар


def kassa_edit_kinds():
    """Чекланган роль (Кассир, Менеджер …) ёзадиган ҳужжатлар — ролдаги бўлимлардан (roles.doc_kinds)."""
    import roles
    return roles.doc_kinds()


def kassa_view_kinds():
    import roles
    return roles.doc_kinds()
CUR = {1: "сўм", 2: "$"}
LOCK_SEC = 120
SYNC_EVERY = 300
_sync_lock = threading.Lock()
_state = {"sig": None, "at": 0.0, "last": None, "err": ""}

SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_goods(ext TEXT PRIMARY KEY, code TEXT, name TEXT, unit TEXT, cur INTEGER, price REAL,
  price_in REAL, parent TEXT, folder INTEGER, deleted INTEGER, src TEXT DEFAULT '1c');
CREATE TABLE IF NOT EXISTS erp_clients(ext TEXT PRIMARY KEY, code TEXT, name TEXT, full TEXT, phones TEXT, parent TEXT,
  folder INTEGER, deleted INTEGER, src TEXT DEFAULT '1c');
CREATE TABLE IF NOT EXISTS erp_docs(id INTEGER PRIMARY KEY, ext TEXT UNIQUE, kind TEXT, no TEXT, date TEXT, time TEXT,
  client TEXT, rate REAL, usd REAL, uzs REAL, discount REAL, pay_usd REAL, pay_uzs REAL, nal INTEGER, note TEXT, user TEXT,
  status TEXT, src TEXT, hash TEXT, created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT, lock_by TEXT, lock_at REAL);
CREATE INDEX IF NOT EXISTS erp_docs_kd ON erp_docs(kind, date);
CREATE INDEX IF NOT EXISTS erp_docs_cl ON erp_docs(client);
CREATE TABLE IF NOT EXISTS erp_lines(id INTEGER PRIMARY KEY, doc_id INTEGER REFERENCES erp_docs(id) ON DELETE CASCADE, lineno INTEGER,
  good TEXT, client TEXT, qty REAL, unit TEXT, price REAL, usd REAL, price_uzs REAL, uzs REAL, price2 REAL, debt REAL, note TEXT);
CREATE INDEX IF NOT EXISTS erp_lines_doc ON erp_lines(doc_id);
CREATE TABLE IF NOT EXISTS erp_accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'bank', active INTEGER DEFAULT 1,
  sort INTEGER DEFAULT 0, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS erp_methods(id INTEGER PRIMARY KEY, name TEXT NOT NULL, account_id INTEGER REFERENCES erp_accounts(id), active INTEGER DEFAULT 1,
  sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS erp_fees(id INTEGER PRIMARY KEY, method_id INTEGER REFERENCES erp_methods(id), from_date TEXT NOT NULL, pct REAL NOT NULL DEFAULT 0,
  set_by TEXT, set_at TEXT);
CREATE TABLE IF NOT EXISTS erp_pays(id INTEGER PRIMARY KEY, doc_id INTEGER REFERENCES erp_docs(id) ON DELETE CASCADE, method_id INTEGER, account_id INTEGER,
  cur TEXT NOT NULL DEFAULT 'uzs', amount REAL NOT NULL DEFAULT 0, usd REAL NOT NULL DEFAULT 0, fee_pct REAL DEFAULT 0, fee REAL DEFAULT 0, note TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS erp_pays_doc ON erp_pays(doc_id);
CREATE TABLE IF NOT EXISTS erp_moves(id INTEGER PRIMARY KEY, date TEXT NOT NULL, from_acc INTEGER, to_acc INTEGER, cur TEXT NOT NULL DEFAULT 'uzs',
  amount REAL NOT NULL, note TEXT DEFAULT '', created_by TEXT, created_at TEXT, deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS erp_resp_ded(id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL, kw_id INTEGER NOT NULL, ym TEXT NOT NULL,
  amount REAL NOT NULL, cur TEXT NOT NULL DEFAULT 'UZS', usd REAL DEFAULT 0, note TEXT DEFAULT '', by TEXT, at TEXT, deleted INTEGER DEFAULT 0,
  base_usd REAL DEFAULT 0, ref_id INTEGER);
CREATE TABLE IF NOT EXISTS erp_log(id INTEGER PRIMARY KEY, at TEXT, user TEXT, doc_id INTEGER, action TEXT, detail TEXT);
"""

SYNC_TABLES = ("SC55", "SC12", "1SJOURN", "1SCONST", "DH210", "DT210", "DH430", "DT430", "DH1195", "DT1195", "DH1133", "DT1133")


def r2(v):
    """1С'дагидек яхлитлаш (0,005 -> 0,01, манфийда ҳам)."""
    return float(Decimal(repr(round(float(v or 0), 9))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def lc(t):
    """Қидирув учун: кичик ҳарф, ё->е, ортиқча белгилар бўшлиқ."""
    t = (t or "").casefold().replace("ё", "е")
    return " " + re.sub(r"\s+", " ", t).strip() + " "


def _words(qs):
    return [w for w in lc(qs).split() if w][:6]


def words_sql(qs, col="lc"):
    """Қидирув шарти: ҳар бир сўз бор; нотўғри клавиатура тилида ёзилган бўлса — ўгирилган варианти ҳам (kb.variants)."""
    import kb
    groups, args = [], []
    for v in kb.variants(qs):
        ws = _words(v)
        if ws:
            groups.append("(" + " AND ".join(f"{col} LIKE ?" for _ in ws) + ")")
            args += [f"%{w}%" for w in ws]
    return ((" AND (" + " OR ".join(groups) + ")") if groups else ""), args


def print_qr(no, dd):
    """Накладнойга босиладиган QR (SVG data: URI): «LB:<№>:<ЙЙЙЙООКК>». «Тайёр» ва цех-бот расмдан рақамни шу орқали топади."""
    try:
        import segno
        txt = f"LB:{str(no or '').strip().lstrip('0') or '0'}:{dd.strftime('%Y%m%d') if dd else '00000000'}"
        return segno.make(txt, error="m", micro=False).svg_data_uri(scale=4, border=2, dark="#000")
    except Exception:
        return ""


def _has_disc(con):
    """erp_goods.disc устуни бор-йўқлиги (каталог init қўшади)."""
    return "disc" in {r[1] for r in con.execute("PRAGMA table_info(erp_goods)")}


def calc_line(cur, qty, price, rate):
    """1С формуласи: нарх товар валютасида. $ товар: сумма$ = сони×нарх, сўм = сумма$×курс; сўм товар: сўм = сони×нарх, $ = сўм/курс."""
    qty, price, rate = float(qty or 0), float(price or 0), float(rate or 0) or 1.0
    if cur == 2:
        usd = r2(qty * price)
        return dict(price_uzs=r2(price * rate), usd=usd, uzs=r2(usd * rate))
    uzs = r2(qty * price)
    return dict(price_uzs=r2(price), usd=r2(uzs / rate), uzs=uzs)


# ================================================================ 1С -> платформа кўзгуси
def _sig():
    base = c1.base_path()
    out = []
    for t in SYNC_TABLES:
        try:
            st = os.stat(os.path.join(base, t + ".DBF")); out.append((t, st.st_mtime, st.st_size))
        except OSError:
            out.append((t, 0, 0))
    return tuple(out)


def _t(name):
    try:
        return c1.table(name)
    except FileNotFoundError:
        return []


def _prices():
    """Сотув нархи (даврий реквизит «FM» 1SCONST'да) — ҳар бир товар учун охиргиси."""
    best = {}
    for r in _t("1SCONST"):
        if (r["ID"] or "").strip() != "FM":
            continue
        k = (r["DATE"] or date.min, r["TIME"] or "", r.get("ACTNO") or 0)
        o = r["OBJID"]
        if o not in best or k >= best[o][0]:
            try:
                best[o] = (k, float((r["VALUE"] or "0").strip() or 0))
            except ValueError:
                pass
    return {k: v[1] for k, v in best.items()}


def _cur(v):
    v = (v or "").strip()
    return int(v) if v in ("1", "2") else 0


def _docs_from_1c(j):
    """{ext: (head dict, [line dict])} — 4 та ҳужжат тури."""
    res = {}
    def head(iddoc, kind, **kw):
        d = j.get(iddoc)
        if not d or d["kind"] not in KINDS:
            return None
        h = dict(kind=KINDS[d["kind"]], no=d["no"], date=d["date"].isoformat() if d["date"] else "", time=d["time"] or "",
                 status="deleted" if d["deleted"] else ("posted" if d["posted"] else "draft"),
                 client="", rate=0, usd=0, uzs=0, discount=0, pay_usd=0, pay_uzs=0, paid=0, ret=1, nal=0, note="", user="")
        h.update(kw)
        return h
    notes = c1.blob_texts(1362) if _t("1SBLOB") else {}
    for h in _t("DH210"):
        x = head(h["IDDOC"], 210, client=h["SP232"], rate=h["SP1132"], usd=h["SP905"], uzs=h["SP559"], discount=h["SP1356"],
                 pay_usd=h["SP1163"], pay_uzs=h["SP1164"], paid=h["SP1244"], ret=int(h["SP1360"] or 0), nal=int(h["SP841"] or 0), user=(h["SP1266"] or "").strip(),
                 note=notes.get(h["IDDOC"], ""))
        if x:
            res[h["IDDOC"]] = (x, [])
    for l in _t("DT210"):
        if l["IDDOC"] in res:
            res[l["IDDOC"]][1].append(dict(lineno=int(l["LINENO"]), good=l["SP216"], client="", qty=l["SP217"], unit=(l["SP556"] or "").strip(),
                                           price=l["SP904"], usd=l["SP905"], price_uzs=l["SP557"], uzs=l["SP559"], price2=0, debt=0,
                                           note=(l["SP910"] or "").strip() or (l["SP1531"] or "").strip()))
    for h in _t("DH430"):
        x = head(h["IDDOC"], 430, client=h["SP431"], usd=h["SP1113"], uzs=h["SP435"], user=(h["SP1269"] or "").strip(),
                 note=(h["SP765"] or "").strip())
        if x:
            res[h["IDDOC"]] = (x, [])
    gcur = {r["ID"]: _cur(r["SP1158"]) for r in _t("SC55")}
    for l in _t("DT430"):
        if l["IDDOC"] in res:
            # 1С'да «Цена/Сумма» (SP434/SP435) — товар валютасида ($ товар — долларда, сўм товар — сўмда); «Цена#/Сумма#» (SP1112/1113) — қўшимча
            usd_ = gcur.get(l["SP432"]) == 2
            res[l["IDDOC"]][1].append(dict(lineno=int(l["LINENO"]), good=l["SP432"], client="", qty=l["SP433"], unit="",
                                           price=l["SP434"], usd=l["SP435"] if usd_ else 0, price_uzs=0 if usd_ else l["SP434"],
                                           uzs=0 if usd_ else l["SP435"], price2=l["SP1112"], debt=l["SP1113"], note=(l["SP1528"] or "").strip()))
    for k, (hd, ls) in res.items():
        if hd["kind"] == "receipt" and ls:
            hd["usd"] = round(sum(l["usd"] or 0 for l in ls), 2); hd["uzs"] = round(sum(l["uzs"] or 0 for l in ls), 2)
    for h in _t("DH1195"):
        x = head(h["IDDOC"], 1195, rate=h["SP1247"], usd=h["SP1199"], uzs=h["SP1202"], user=(h["SP1276"] or "").strip())
        if x:
            res[h["IDDOC"]] = (x, [])
    for l in _t("DT1195"):
        if l["IDDOC"] in res:
            hd = res[l["IDDOC"]][0]
            if not hd["client"]:
                hd["client"] = l["SP1198"]
            res[l["IDDOC"]][1].append(dict(lineno=int(l["LINENO"]), good="", client=l["SP1198"], qty=0, unit="", price=0,
                                           usd=l["SP1199"], price_uzs=0, uzs=l["SP1202"], price2=0, debt=l["SP1245"], note=""))
    for h in _t("DH1133"):
        x = head(h["IDDOC"], 1133, user=(h["SP1275"] or "").strip())
        if x:
            res[h["IDDOC"]] = (x, [])
    for l in _t("DT1133"):
        if l["IDDOC"] in res:
            res[l["IDDOC"]][1].append(dict(lineno=int(l["LINENO"]), good=l["SP1144"], client="", qty=l["SP1145"], unit=(l["SP1146"] or "").strip(),
                                           price=l["SP1147"], usd=0, price_uzs=0, uzs=0, price2=l["SP1160"], debt=0, note=""))
    return res


def _hash(h, lines):
    s = json.dumps([h, sorted(lines, key=lambda x: x["lineno"])], sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _warm():
    """DBF'ларни кэшга ўқиш (c1.table файл ўзгармагунча кэшлайди). Ёзиш транзакцияси ичида секин диск/тармоқдан ўқилмасин —
    акс ҳолда бу пайтда бошқа ёзувлар (ботлар, касса, Эман) 15 сониягача кутиб қолади («database is locked»)."""
    for t in SYNC_TABLES + ("RG445", "RG410", "SC1258"):
        try:
            c1.table(t)
        except Exception:
            pass
    try:
        if _t("1SBLOB"):
            c1.blob_texts(1362)
    except Exception:
        pass


def sync(force=False, con=None):
    """1С'дан кўчириш. Файллар ўзгармаган бўлса — ҳеч нарса қилмайди. Натижа: dict(goods, clients, new, upd, gone)."""
    if not _sync_lock.acquire(blocking=False):
        return None
    try:
        sig = _sig()
        if not force and sig == _state["sig"]:
            return dict(skipped=True)
        _warm()                                   # 1С файлларини ёзишдан ОЛДИН ўқиб олиш — база қулфи қисқа бўлади
        own = con is None
        con = con or A.connect()
        try:
            st = _sync(con)
            con.commit()
        finally:
            if own:
                con.close()
        _state.update(sig=sig, at=time.time(), last=datetime.now().strftime("%d.%m %H:%M"), err="")
        return st
    except Exception as e:
        _state["err"] = str(e)
        raise
    finally:
        _sync_lock.release()


C1_CAT = {"YU": "good", "YV": "svc"}       # 1С справочник «Номенклатура», реквизит «Категория» (SP1257): Товар / Услуга


def _sync(con):
    st = dict(goods=0, clients=0, new=0, upd=0, gone=0)
    _new_sales.clear()
    prices = _prices()
    rows = [(r["ID"], (r["CODE"] or "").strip(), (r["DESCR"] or "").strip(), (r["SP57"] or "").strip(), _cur(r["SP1158"]),
             prices.get(r["ID"], 0.0), r["SP134"] or 0, r["PARENTID"] or "", int(r["ISFOLDER"] or 0) == 1,
             (r["ISMARK"] or "").strip() == "*", lc((r["DESCR"] or "") + " " + (r["CODE"] or "")),
             C1_CAT.get((r.get("SP1257") or "").strip(), "")) for r in _t("SC55")]
    con.executemany("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,cat,src) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'1c') "
                    "ON CONFLICT(ext) DO UPDATE SET code=excluded.code,name=excluded.name,unit=excluded.unit,cur=excluded.cur,"
                    "price=excluded.price,price_in=excluded.price_in,parent=excluded.parent,folder=excluded.folder,deleted=excluded.deleted,"
                    "lc=excluded.lc,cat=excluded.cat WHERE erp_goods.src='1c'", rows)
    st["goods"] = len(rows)
    cl = c1.clients() if _t("SC12") else {}
    rows = [(k, v["code"], v["name"], v["full"], v["phones"], v["parent"] or "", v["folder"], v["deleted"],
             lc(" ".join((v["name"], v["full"], v["code"], v["phones"], re.sub(r"\D", "", v["phones"]))))) for k, v in cl.items()]
    con.executemany("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src) VALUES(?,?,?,?,?,?,?,?,?,'1c') "
                    "ON CONFLICT(ext) DO UPDATE SET code=excluded.code,name=excluded.name,full=excluded.full,phones=excluded.phones,"
                    "parent=excluded.parent,folder=excluded.folder,deleted=excluded.deleted,lc=excluded.lc WHERE erp_clients.src='1c'", rows)
    st["clients"] = len(rows)
    docs = _docs_from_1c(c1.journal())
    have = {r[0]: (r[1], r[2]) for r in con.execute("SELECT ext, id, hash FROM erp_docs WHERE src='1c'")}
    for ext, (h, lines) in docs.items():
        hs = _hash(h, lines)
        old = have.get(ext)
        if old and old[1] == hs:
            continue
        vals = (h["kind"], h["no"], h["date"], h["time"], h["client"], h["rate"], h["usd"], h["uzs"], h["discount"], h["pay_usd"],
                h["pay_uzs"], h["nal"], h["note"], h["user"], h["status"], hs, h["paid"], h["ret"])
        if old:
            did = old[0]
            con.execute("UPDATE erp_docs SET kind=?,no=?,date=?,time=?,client=?,rate=?,usd=?,uzs=?,discount=?,pay_usd=?,pay_uzs=?,nal=?,"
                        "note=?,user=?,status=?,hash=?,paid=?,ret=? WHERE id=?", vals + (did,))
            con.execute("DELETE FROM erp_lines WHERE doc_id=?", (did,))
            st["upd"] += 1
        else:
            did = con.execute("INSERT INTO erp_docs(kind,no,date,time,client,rate,usd,uzs,discount,pay_usd,pay_uzs,nal,note,user,status,hash,"
                              "paid,ret,ext,src) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'1c')", vals + (ext,)).lastrowid
            st["new"] += 1
            if h["kind"] == "sale":
                _new_sales.append(did)
        con.executemany("INSERT INTO erp_lines(doc_id,lineno,good,client,qty,unit,price,usd,price_uzs,uzs,price2,debt,note) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [(did, l["lineno"], l["good"], l["client"], l["qty"], l["unit"], l["price"], l["usd"], l["price_uzs"], l["uzs"],
                          l["price2"], l["debt"], l["note"]) for l in lines])
    gone = [v[0] for k, v in have.items() if k not in docs]
    for did in gone:
        con.execute("UPDATE erp_docs SET status='deleted', hash='' WHERE id=?", (did,))
    st["gone"] = len(gone)
    st["renum"] = len(fix_collisions(con))
    # охирги сотув нархи (1С'да кўп товарнинг доимий нархи йўқ — нарх накладнойда ёзилади)
    last = {}
    for g, pr in con.execute("SELECT l.good, l.price FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.kind='sale' "
                             "AND d.status='posted' AND l.price>0 ORDER BY d.date, d.time, d.id"):
        last[g] = pr
    con.executemany("UPDATE erp_goods SET price_last=? WHERE ext=?", [(v, k) for k, v in last.items()])
    try:
        credit_after_sync(con)
    except Exception:
        A.errlog.exception("credit after sync")
    return st


# ================================================================ мижоз лимити (кредит) ва запрет
_new_sales = []
CREDIT_DEFAULT = 1000.0


CKINDS = {"client": "Мижоз", "account": "Ҳисоб (карта / терминал / перечисление / инвестиция)", "supplier": "Етказиб берувчи (завод)"}
_ACCT_RE = re.compile(r"^\s*(карта|karta|терминал|перечисл|инвестиц|поступлени[ея] в сч)", re.I)


def classify_clients(con):
    """Мижоз тури: «Поставщики» папкасидагилар — завод; карта/терминал/перечисление/инвестиция/поступление в счёт — ҳисоб
    (1С'да тушган пул шу «мижоз»ларга ёзилади, улар ҳақиқий мижоз эмас). Админ қўлда ўзгартирса (ckind_set=1) — тегилмайди."""
    sup = {r[0] for r in con.execute("SELECT ext, name FROM erp_clients WHERE folder=1")
           if re.search(r"поставщ|завод|етказиб", (r[1] or "").casefold())}
    for r in con.execute("SELECT ext, name, parent FROM erp_clients WHERE folder=0 AND COALESCE(ckind_set,0)=0").fetchall():
        k = "supplier" if r["parent"] in sup else ("account" if _ACCT_RE.search(r["name"] or "") else "client")
        con.execute("UPDATE erp_clients SET ckind=?, blocked=CASE WHEN ?='client' THEN blocked ELSE 0 END WHERE ext=?", (k, k, r["ext"]))


def ckind_map():
    """{1С мижоз коди: тур} — client / partner / account / supplier (ҳисоботларда қарзни ажратиш учун)."""
    try:
        return {r[0]: (r[1] or "client") for r in A.q("SELECT ext, ckind FROM erp_clients")}
    except Exception:
        return {}


def credit_default():
    try:
        return float(A.get_setting("credit_default", "1000") or 1000)
    except (TypeError, ValueError):
        return CREDIT_DEFAULT


def block_cutoff(today=None):
    """Йил бошидан ва охирги 3 ой ичида товар/хизмат олмаган мижоз — запрет."""
    today = today or date.today()
    return min(date(today.year, 1, 1), today - timedelta(days=91))


def _last_sales(con):
    return {r[0]: r[1] for r in con.execute("SELECT client, MAX(date) FROM erp_docs WHERE kind='sale' AND status='posted' GROUP BY client")}


def credit_after_sync(con):
    """1С қарзини (RG445) мижозларга ёзиш; биринчи марта — лимит ва запрет қўйиш; ҳар куни — фаолсизларга запрет;
    1С'да янги накладной лимитдан ошса — шерикларга хабар (параллел даврда 1С'ни тўхтата олмаймиз)."""
    try:
        _, bal = c1.debts()
    except Exception:
        bal = None
    if bal is not None:
        con.execute("UPDATE erp_clients SET debt1c=0 WHERE src<>'pf' OR ext NOT LIKE 'P%'")
        # ишорали: минус — мижознинг олдиндан тўлови (аванс), қарз ва лимитда ҳисобга олинади (26.10: аванс 0 бўлиб қолар эди)
        con.executemany("UPDATE erp_clients SET debt1c=? WHERE ext=?", [(round(-v, 2) + 0.0, k) for k, v in bal.items()])
    classify_clients(con)
    map_c1_accounts(con)
    today = date.today()
    cut = block_cutoff(today).isoformat()
    last = _last_sales(con)
    if A.get_setting("credit_init", "") != "1" and bal is not None:
        dflt = credit_default()
        n_b = 0
        for r in con.execute("SELECT ext, debt1c, ckind FROM erp_clients WHERE folder=0").fetchall():
            lim = max(dflt, float(r["debt1c"] or 0))
            blk = 1 if (r["ckind"] or "client") == "client" and (last.get(r["ext"]) or "") < cut else 0
            n_b += blk
            con.execute("UPDATE erp_clients SET lim=?, blocked=?, block_note=? WHERE ext=?",
                        (round(lim, 2), blk, "автомат: йил бошидан/3 ойдан бери харид йўқ" if blk else "", r["ext"]))
        _cset(con, credit_init="1", credit_block_day=today.isoformat())
        log(con, None, "credit_init", f"лимит: стандарт ${dflt:.0f}, қарздорларга — ҳозирги қарз; запрет: {n_b} та")
    elif A.get_setting("credit_block_day", "") < today.isoformat():
        rows = con.execute("SELECT ext, unblocked_at FROM erp_clients WHERE folder=0 AND deleted=0 AND blocked=0 "
                           "AND COALESCE(ckind,'client')='client'").fetchall()
        for r in rows:
            act = max(last.get(r["ext"]) or "", (r["unblocked_at"] or "")[:10])
            if act and act < cut:
                con.execute("UPDATE erp_clients SET blocked=1, block_note=? WHERE ext=?", ("автомат: йил бошидан/3 ойдан бери харид йўқ", r["ext"]))
        _cset(con, credit_block_day=today.isoformat())
    # 1С'даги бугунги янги накладнойлар лимитдан ошдими
    for did in list(_new_sales):
        d = con.execute("SELECT d.*, c.name cname, c.lim, c.blocked, c.debt1c FROM erp_docs d JOIN erp_clients c ON c.ext=d.client "
                        "WHERE d.id=? AND d.src='1c' AND d.status='posted' AND d.date=? AND COALESCE(c.ckind,'client')='client'",
                        (did, today.isoformat())).fetchone()
        if not d:
            continue
        lim = d["lim"] if d["lim"] is not None else credit_default()
        if (d["debt1c"] or 0) > lim + 0.5 or (d["blocked"] and (d["usd"] or 0) - (d["paid"] or 0) > 0.5):
            if con.execute("SELECT 1 FROM erp_log WHERE doc_id=? AND action='limit_alert'", (did,)).fetchone():
                continue
            why = "запретли мижоз" if d["blocked"] else f"қарзи ${d['debt1c']:,.0f} > лимит ${lim:,.0f}".replace(",", " ")
            con.execute("INSERT INTO erp_log(at,user,doc_id,action,detail) VALUES(?,?,?,?,?)",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "тизим", did, "limit_alert", why))
            try:
                import owner as _own
                _own._send_all(f"⚠️ Лимит: 1С'да накладной № {d['no']} — {d['cname']}, ${d['usd']:,.2f}. {why}.".replace(",", " "))
            except Exception:
                A.errlog.exception("limit alert")


def _cset(con, **kw):
    """Созламани шу уланишда ёзиш (кўчириш транзакцияси ичида бошқа уланиш билан қулфланмаслиги учун)."""
    for k, v in kw.items():
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))


def client_debt(con, ext, exclude_doc=None):
    """Мижознинг ҳозирги қарзи ($): 1С'даги қарз + платформада проведен қилинган накладнойларнинг тўланмаган қисми."""
    r = con.execute("SELECT debt1c FROM erp_clients WHERE ext=?", (ext,)).fetchone()
    base = float(r["debt1c"] or 0) if r else 0.0
    pf = con.execute("SELECT COALESCE(SUM(usd - COALESCE(paid,0)),0) FROM erp_docs WHERE src='pf' AND kind='sale' AND status='posted' "
                     "AND COALESCE(nal,1)=1 AND client=? AND id<>?", (ext, exclude_doc or 0)).fetchone()[0]
    pp = con.execute("SELECT COALESCE(SUM(l.debt),0) FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.src='pf' AND d.kind='pay' "
                     "AND d.status='posted' AND l.client=? AND d.id<>?", (ext, exclude_doc or 0)).fetchone()[0]     # платформада ёзилган тўлов
    return round(base + float(pf or 0) - float(pp or 0), 2)


def due_default():
    try:
        return int(float(A.get_setting("erp_due_days", "20") or 20))
    except (TypeError, ValueError):
        return 20


def client_due(con, ext):
    """Мижознинг тўлов муддати (кун): карточкада ёзилган бўлса — шу, бўлмаса стандарт (20)."""
    r = con.execute("SELECT due_days FROM erp_clients WHERE ext=?", (ext,)).fetchone()
    return int(r[0]) if r and r[0] is not None else due_default()


def doc_unpaid(con, ext):
    """Ҳар бир накладнойнинг тўланмаган қисми ($), тўловлар энг эски накладнойдан ёпилади деб. -> {doc_id: part}"""
    remaining = client_debt(con, ext)
    out = {}
    if remaining <= 0.01:
        return out
    for d in con.execute("SELECT id, usd, COALESCE(paid,0) paid FROM erp_docs WHERE kind='sale' AND status='posted' "
                         "AND COALESCE(nal,1)=1 AND client=? AND usd>0 ORDER BY date DESC, time DESC, id DESC", (ext,)):
        part = min(remaining, max(0.0, (d["usd"] or 0) - (d["paid"] or 0)))
        remaining = round(remaining - part, 2)
        if part > 0.01:
            out[d["id"]] = round(part, 2)
        if remaining <= 0.01:
            break
    return out


def overdue(con, ext, exclude_doc=None, today=None, debt=None):
    """Муддати ўтган тўланмаган накладнойлар. Тўловлар энг эски накладнойдан ёпилади деб ҳисобланади (ҳозирги қарз — энг янги
    накладнойларда). Муддат фақат «erp_due_start» кунидан кейинги накладнойларга (эски қарз — лимит билан назорат қилинади).
    -> [dict(id, no, date, part, days)]"""
    due = client_due(con, ext)
    if due <= 0:
        return []
    today = today or date.today()
    start = A.get_setting("erp_due_start", "") or today.isoformat()
    ub = con.execute("SELECT unblocked_at FROM erp_clients WHERE ext=?", (ext,)).fetchone()
    ub = A.parse_date((ub[0] or "")[:10]) if ub and ub[0] else None
    if ub:                                       # админ блокни очган: ўшангача муддати ўтганлари кечирилади
        start = max(start, (ub - timedelta(days=due - 1)).isoformat())
    cut = (today - timedelta(days=due)).isoformat()
    remaining = client_debt(con, ext, exclude_doc) if debt is None else debt
    out = []
    if remaining <= 0.01:
        return out
    for d in con.execute("SELECT id, no, date, usd, COALESCE(paid,0) paid FROM erp_docs WHERE kind='sale' AND status='posted' "
                         "AND COALESCE(nal,1)=1 AND client=? AND id<>? AND usd>0 ORDER BY date DESC, time DESC, id DESC",
                         (ext, exclude_doc or 0)):
        part = min(remaining, max(0.0, (d["usd"] or 0) - (d["paid"] or 0)))
        remaining = round(remaining - part, 2)
        if part > 0.01 and start <= d["date"] < cut:
            dd = A.parse_date(d["date"])
            out.append(dict(id=d["id"], no=d["no"], date=d["date"], part=round(part, 2), days=(today - dd).days - due if dd else 0))
        if remaining <= 0.01:
            break
    return out


def credit_info(con, ext, exclude_doc=None):
    r = con.execute("SELECT lim, blocked, block_note, ckind FROM erp_clients WHERE ext=?", (ext,)).fetchone()
    lim = (r["lim"] if r and r["lim"] is not None else credit_default())
    debt = client_debt(con, ext, exclude_doc)
    kind = (r["ckind"] if r and r["ckind"] else "client")
    od = overdue(con, ext, exclude_doc, debt=debt) if kind == "client" else []
    return dict(debt=debt, lim=round(lim, 2), free=round(lim - debt, 2), blocked=bool(r and r["blocked"]) and kind == "client",
                note=(r["block_note"] if r else "") or "", kind=kind, nolimit=kind != "client", due=client_due(con, ext),
                overdue=od, overdue_text=overdue_text(od))


def overdue_text(od):
    if not od:
        return ""
    o = od[-1]                                    # энг эскиси
    dd = A.parse_date(o["date"])
    t = f"№ {o['no']} ({dd:%d.%m.%Y}) бўйича ${o['part']:,.2f} тўлов муддати {o['days']} кун ўтди".replace(",", " ")
    if len(od) > 1:
        t += f" (яна {len(od) - 1} та накладной, жами ${sum(x['part'] for x in od):,.2f})".replace(",", " ")
    return t


def credit_check(con, ext, add_usd, exclude_doc=None, total=None):
    """Проведен қилишдан олдин: лимит етадими. Етмаса ValueError (оповещение матни)."""
    ci = credit_info(con, ext, exclude_doc)
    if ci["nolimit"]:                     # карта/терминал/ҳисоб ёки завод — лимит йўқ
        return ci
    add_usd = round(float(add_usd or 0), 2)
    m = lambda v: f"${v:,.2f}".replace(",", " ")
    if ci["blocked"] and add_usd > 0.005:
        raise ValueError(f"Мижозга запрет қўйилган ({ci['note'] or 'админ'}) — насияга бериб бўлмайди. Тўлиқ тўлов киритинг "
                         "ёки админ запретни очсин.")
    if ci["overdue"] and add_usd > 0.005:
        raise ValueError(f"Тўлов муддати ўтган: {ci['overdue_text']}. Насияга бериб бўлмайди — аввал қарзни тўласин, тўлиқ тўлов киритинг "
                         "ёки админ запретни очсин.")
    after = round(ci["debt"] + max(add_usd, float(total or 0)), 2)   # бутун накладной лимитга сиғмаса — тўлиқ тўлансагина проведен
    if add_usd > 0.005 and after > ci["lim"] + 0.005:
        raise ValueError(f"Лимит етарли эмас: мижоз қарзи {m(ci['debt'])}, лимит {m(ci['lim'])}, бу накладнойнинг тўланмаган қисми "
                         f"{m(add_usd)}. Лимитга сиғмаган накладной тўлиқ тўлансагина проведен қилинади ({m(add_usd)} тўлов киритинг) "
                         "ёки админ лимитни оширсин. Қоралама сифатида сақлаш мумкин.")
    return ci


def _credit_fail(con, d):
    try:
        credit_check(con, d["client"], (d["usd"] or 0) - (d["paid"] or 0), exclude_doc=d["id"], total=d["usd"] or 0)
        return ""
    except ValueError as e:
        return str(e)


def sync_due():
    if time.time() - _state["at"] >= SYNC_EVERY:
        try:
            sync()
        except Exception:
            A.errlog.exception("erp sync")


# ================================================================ платформа ҳужжатлари
def _user():
    from flask import has_request_context
    if not has_request_context():
        return "тизим"
    u = A.current_user()
    return (u["username"] if u else "") or ""


def log(con, doc_id, action, detail=""):
    con.execute("INSERT INTO erp_log(at,user,doc_id,action,detail) VALUES(?,?,?,?,?)", (A.now(), _user(), doc_id, action, detail))


def next_no(con, kind, year=None):
    """1С'дагидек: ҳар йили 1 дан, 1С ва платформа ҳужжатлари битта кетма-кетликда (ўчирилганлар ҳам рақамни банд қилади)."""
    year = str(year or date.today().year)
    n, w = 0, 0
    for (no,) in con.execute("SELECT no FROM erp_docs WHERE kind=? AND substr(date,1,4)=?", (kind, year)):
        no = str(no or "").strip()
        if no.isdigit() and int(no) >= n:
            n, w = int(no), (len(no) if no.startswith("0") else 0)
    return str(n + 1).zfill(w)                    # 1С'дагидек: «000001092» (приходлар нол билан)


def fix_collisions(con):
    """Параллел даврда 1С ҳам шу рақамни олса — платформа ҳужжатига кейинги бўш рақам берилади. Қайтаради: [(эски, янги)]."""
    out = []
    rows = con.execute("""SELECT p.id, p.kind, p.no, substr(p.date,1,4) y FROM erp_docs p WHERE p.src='pf' AND EXISTS(
        SELECT 1 FROM erp_docs o WHERE o.src='1c' AND o.kind=p.kind AND o.no=p.no AND substr(o.date,1,4)=substr(p.date,1,4))""").fetchall()
    for r in rows:
        new = next_no(con, r["kind"], r["y"])
        con.execute("UPDATE erp_docs SET no=? WHERE id=?", (new, r["id"]))
        con.execute("INSERT INTO erp_log(at,user,doc_id,action,detail) VALUES(?,?,?,?,?)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "тизим", r["id"], "renum", f"№ {r['no']} 1С'да ҳам олинди → № {new}"))
        out.append((r["no"], new))
    return out


def day_rate(d=None):
    """Кун курси — Эман ботидан (@em_mat_info_bot) олинган охирги курс: шу кун ичида ўзгарса — энг сўнггиси;
    шу кун бот жавоб бермаган бўлса — кечаги курс (ЦБ курси ишлатилмайди).
    -> (курс, манба, вақт) ёки (0, '', '')."""
    d = (d or date.today()).isoformat() if not isinstance(d, str) else d
    r = A.q("SELECT rate, source, rate_date, fetched_at FROM fx_rates WHERE is_demo=0 AND source<>'cbu' AND COALESCE(rate_date, substr(fetched_at,1,10))<=? "
            "ORDER BY COALESCE(rate_date, substr(fetched_at,1,10)) DESC, id DESC LIMIT 1", (d,), one=True)
    return (r["rate"], r["source"], r["fetched_at"]) if r else (0, "", "")


def onec_rate(d=None):
    """Шу куни 1С накладнойларида ёзилган курс (энг кўп учрагани) — солиштириш учун."""
    r = A.q("SELECT rate, COUNT(*) n FROM erp_docs WHERE src='1c' AND kind='sale' AND date=? AND rate>0 AND status<>'deleted' "
            "GROUP BY rate ORDER BY n DESC LIMIT 1", ((d or date.today()).isoformat(),), one=True)
    return r["rate"] if r else 0


def last_rate(con):
    r = con.execute("SELECT rate FROM erp_docs WHERE kind='sale' AND rate>0 ORDER BY date DESC, time DESC, id DESC LIMIT 1").fetchone()
    if r and r[0]:
        return r[0]
    try:
        return A.fx_rate() or 0
    except Exception:
        return 0


def lock_info(d):
    """Бошқа одам ҳозир таҳрирлаяптими: (ким, неча сония олдин) ёки None."""
    if d["lock_by"] and d["lock_by"] != _user() and d["lock_at"] and time.time() - d["lock_at"] < LOCK_SEC:
        return d["lock_by"], int(time.time() - d["lock_at"])
    return None


def reval_prices(con):
    """Платформада проведен қилинган переоценка: {товар: янги нарх} (энг кейингиси)."""
    out = {}
    for r in con.execute("SELECT l.good, l.price FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.src='pf' AND d.kind='reval' "
                         "AND d.status='posted' ORDER BY d.date, d.time, d.id, l.lineno"):
        out[r["good"]] = r["price"]
    return out


DOC2 = {"receipt": "Поступление товара", "pay": "Поступление денег", "reval": "Переоценка товаров", "inv": "Инвентаризация товаров"}


def _doc2_head(con, kind, doc_id, form):
    """Умумий текширувлар: сана ёпилмаган, ҳужжат ўзиники, қулф ва версия."""
    ddate = A.parse_date(form.get("date")) or date.today()
    if A.is_closed(ddate):
        raise ValueError(A.closed_text(ddate))
    d = None
    if doc_id:
        d = con.execute("SELECT * FROM erp_docs WHERE id=?", (doc_id,)).fetchone()
        if not d or d["src"] != "pf" or d["kind"] != kind:
            raise ValueError("Бу ҳужжатни платформада ўзгартириб бўлмайди")
        if d["status"] == "deleted":
            raise ValueError("Ҳужжат ўчирилган")
        if A.is_closed(d["date"]):
            raise ValueError(A.closed_text(d["date"]))
        lk = lock_info(d)
        if lk:
            raise ValueError(f"{lk[0]} ҳозир шу ҳужжатни таҳрирлаяпти")
        if (form.get("version") or "") != (d["updated_at"] or ""):
            raise ValueError(f"Ҳужжатни {d['updated_by']} {d['updated_at']} да ўзгартирган — саҳифани янгиланг")
    return ddate, d


def save_doc2(con, kind, doc_id, form, post=False):
    """Поступление товара / Поступление денег / Переоценка (платформада). form: date, client, rate, note, version, lines=[...]. ValueError."""
    if kind not in DOC2:
        raise ValueError("Ҳужжат тури нотўғри")
    ddate, d = _doc2_head(con, kind, doc_id, form)
    rate = A.parse_float(form.get("rate"), 0) if (A.is_admin() and A.parse_float(form.get("rate"), 0) > 0) else day_rate(ddate)[0]
    if kind not in ("reval", "inv") and rate <= 0:
        raise ValueError("Кун курси ботдан ҳали олинмаган — «Курс $» саҳифасида «Обновить курс» босинг")
    client = (form.get("client") or "").strip()
    if kind == "receipt" and client and not doc_id:
        ck = con.execute("SELECT COALESCE(ckind,'client') FROM erp_clients WHERE ext=?", (client,)).fetchone()
        if not ck or ck[0] != "supplier":
            raise ValueError("Приходда фақат поставщик танланади (мижоз эмас)")
    lines = []
    book = stock_all(con, doc_id) if kind == "inv" else {}
    seen = set()
    for i, l in enumerate(form.get("lines") or []):
        cn = l.get("cnotes") or {}
        cn = {k: str(v)[:500] for k, v in cn.items() if str(v or "").strip()} if isinstance(cn, dict) else {}
        note = (l.get("note") or "").strip()[:300]
        if kind == "pay":
            cl = (l.get("client") or "").strip()
            u, z = A.parse_float(l.get("usd"), 0), A.parse_float(l.get("uzs"), 0)
            if not cl and not u and not z:
                continue
            if not cl or not con.execute("SELECT 1 FROM erp_clients WHERE ext=? AND folder=0", (cl,)).fetchone():
                raise ValueError(f"{i + 1}-қатор: мижоз танланмаган")
            if not u and not z:
                raise ValueError(f"{i + 1}-қатор: сумма ёзилмаган")
            lines.append(dict(good="", client=cl, qty=0, unit="", price=0, usd=r2(u), price_uzs=0, uzs=r2(z), price2=0,
                              debt=r2(u + z / rate), note=note, cnotes=cn))
            continue
        g = con.execute("SELECT * FROM erp_goods WHERE ext=? AND folder=0", ((l.get("good") or "").strip(),)).fetchone()
        qty, price = A.parse_float(l.get("qty"), 0), A.parse_float(l.get("price"), 0)
        if not g and not qty and not price:
            continue
        if not g:
            raise ValueError(f"{i + 1}-қатор: товар танланмаган")
        if kind == "inv":                               # qty — фарқ (омборни тўғрилайди), price — факт, price2 — учёт (сақланган пайтдаги)
            fs = str(l.get("fact") if l.get("fact") is not None else "").strip()
            if fs == "":
                raise ValueError(f"{i + 1}-қатор ({g['name'][:40]}): «Факт» ёзилмаган")
            fact = A.parse_float(fs, 0)
            if fact < 0:
                raise ValueError(f"{i + 1}-қатор: факт манфий бўлмайди")
            if is_service(con, g["ext"]):
                raise ValueError(f"{i + 1}-қатор ({g['name'][:40]}): хизматнинг қолдиғи бўлмайди")
            if g["ext"] in seen:
                raise ValueError(f"{i + 1}-қатор ({g['name'][:40]}): бу товар ҳужжатда икки марта")
            seen.add(g["ext"])
            bk = round(book.get(g["ext"]) or 0, 3)
            lines.append(dict(good=g["ext"], client="", qty=round(fact - bk, 3), unit=g["unit"], price=fact, price_uzs=0, usd=0, uzs=0,
                              price2=bk, debt=0, note=note, cnotes=cn))
        elif kind == "receipt":
            if qty == 0:
                raise ValueError(f"{i + 1}-қатор ({g['name'][:40]}): сони нотўғри")
            if price < 0:
                raise ValueError(f"{i + 1}-қатор: нарх манфий бўлмайди")
            c = calc_line(g["cur"], qty, price, rate)
            lines.append(dict(good=g["ext"], client="", qty=qty, unit=g["unit"], price=price, price2=0, debt=0, note=note, cnotes=cn, **c))
        else:                                           # reval: янги нарх (price), эски нарх (price2), сони — маълумот учун
            if price <= 0:
                raise ValueError(f"{i + 1}-қатор ({g['name'][:40]}): янги нарх ёзилмаган")
            lines.append(dict(good=g["ext"], client="", qty=qty, unit=g["unit"], price=price, price_uzs=0, usd=0, uzs=0,
                              price2=A.parse_float(l.get("price2"), 0), debt=0, note=note, cnotes=cn))
    if not lines:
        raise ValueError("Битта ҳам қатор йўқ")
    if kind == "receipt" and (not client or not con.execute("SELECT 1 FROM erp_clients WHERE ext=? AND folder=0", (client,)).fetchone()):
        raise ValueError("Поставщик танланмаган")
    if kind == "pay":
        client = lines[0]["client"]
    usd = r2(sum(l["usd"] for l in lines)); uzs = r2(sum(l["uzs"] for l in lines))
    who, ts, status = _user(), A.now(), ("posted" if post else "draft")
    note = (form.get("note") or "").strip()[:2000]
    if d:
        con.execute("UPDATE erp_docs SET client=?,rate=?,usd=?,uzs=?,note=?,date=?,status=?,updated_by=?,updated_at=?,lock_by=NULL,lock_at=NULL "
                    "WHERE id=?", (client or None, rate, usd, uzs, note, ddate.isoformat(), status, who, ts, doc_id))
        con.execute("DELETE FROM erp_lines WHERE doc_id=?", (doc_id,))
        log(con, doc_id, "post" if post else "save", f"{len(lines)} қатор")
    else:
        con.commit()
        con.execute("BEGIN IMMEDIATE")
        no = next_no(con, kind, ddate.year)
        doc_id = con.execute("INSERT INTO erp_docs(kind,no,time,client,rate,usd,uzs,discount,nal,note,pay_usd,pay_uzs,paid,date,status,src,user,"
                             "created_by,created_at,updated_by,updated_at,ret) VALUES(?,?,?,?,?,?,?,0,1,?,0,0,0,?,?,'pf',?,?,?,?,?,1)",
                             (kind, no, datetime.now().strftime("%H:%M:%S"), client or None, rate, usd, uzs, note, ddate.isoformat(), status,
                              who, who, ts, who, ts)).lastrowid
        log(con, doc_id, "create+post" if post else "create", f"№ {no}, {len(lines)} қатор")
    for n, l in enumerate(lines, 1):
        con.execute("INSERT INTO erp_lines(doc_id,lineno,good,client,qty,unit,price,usd,price_uzs,uzs,price2,debt,note,cnotes) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (doc_id, n, l["good"], l["client"], l["qty"], l["unit"], l["price"], l["usd"], l["price_uzs"], l["uzs"], l["price2"],
                     l["debt"], l["note"], json.dumps(l["cnotes"], ensure_ascii=False) if l["cnotes"] else ""))
    if kind in ("receipt", "inv"):
        _kusok_sync(con, doc_id)
    return doc_id


MDF_SORTS = [("Асино", 0.0), ("Хитой", 1.0), ("М", 1.0), ("Л", 2.0), ("Гомель", 2.0), ("Алтай", 3.0)]


def mdf_sorts():
    """МДФ сортлари ва Асино'га нисбатан устама ($). Стандарт — Асино. Созламада ўзгартирилади (mdf_sorts JSON)."""
    try:
        v = json.loads(A.get_setting("mdf_sorts", "") or "null")
        if isinstance(v, list) and v:
            return [(str(n), float(d)) for n, d in v]
    except Exception:
        pass
    return MDF_SORTS


def _msort(g):
    """Маҳаллий ЛМДФ (Эконом, Craftwood, Timber zone) — накладнойда МДФ сорти танланади."""
    try:
        return bool(g["msort"])
    except (IndexError, KeyError):
        return False


def inv_rebook(con, doc_id):
    """Инвентаризация: учёт (price2) ва фарқ (qty = факт − учёт) ҳозирги қолдиқ бўйича қайта ҳисобланади."""
    book = stock_all(con, doc_id)
    for l in con.execute("SELECT id, good, price FROM erp_lines WHERE doc_id=? AND COALESCE(note,'') NOT LIKE 'брак #%'", (doc_id,)).fetchall():
        bk = round(book.get(l["good"]) or 0, 3)                # «брак #…» — цех-бот списаниеси: фарқ доим −сони (invbrak)
        con.execute("UPDATE erp_lines SET price2=?, qty=? WHERE id=?", (bk, round((l["price"] or 0) - bk, 3), l["id"]))


def inv_fill(con, folder=None):
    """«Заполнить остатками»: каталог товарлари, қолдиғи 0 эмас (хизматлар йўқ). [dict(good, gname, unit, stock)]."""
    st = stock_all(con)
    rows = con.execute("SELECT ext, name, unit, parent FROM erp_goods WHERE src='cat' AND folder=0 AND deleted=0 ORDER BY name").fetchall()
    keep = None
    if folder:
        kids = defaultdict(list)
        for r in con.execute("SELECT ext, parent FROM erp_goods WHERE src='cat' AND folder=1"):
            kids[r["parent"] or ""].append(r["ext"])
        keep, stk = set(), [folder]
        while stk:
            x = stk.pop()
            if x not in keep:
                keep.add(x); stk.extend(kids.get(x, []))
    out = []
    for r in rows:
        q = st.get(r["ext"])
        if not q or abs(q) < 0.0005 or (keep is not None and r["parent"] not in keep) or is_service(con, r["ext"]):
            continue
        out.append(dict(good=r["ext"], gname=r["name"], unit=r["unit"] or "", stock=round(q, 3)))
    return out


def test_mode():
    """Тест режими (параллел давр): платформада ҳужжат проведен қилинмайди — ҳаммаси черновик. Созлама: erp_test_mode."""
    try:
        return (A.get_setting("erp_test_mode", "0") or "0") == "1"
    except Exception:
        return False


TEST_NOPOST = "Тестовый режим: проводить нельзя — документ сохранён черновиком."
WAIT_WHO = "цех"                       # цех ботидаги накладнойсиз иш черновиги (ишчилар ёзган; кассир мижозни танлайди)
NOCLI_NAME = "Мижоз танланмаган (цех)"


def nocli_ext(con, create=True):
    """Цех черновиги учун вақтинча «мижоз»: проведен қилишдан олдин кассир ҳақиқий мижозни танлайди."""
    r = con.execute("SELECT ext FROM erp_clients WHERE name=? AND folder=0 AND deleted=0", (NOCLI_NAME,)).fetchone()
    if r or not create:
        return r["ext"] if r else None
    ext = _new_ext(con, "erp_clients", "P")                  # бот оқимида (сўровсиз) ҳам ишлайди — save_client'дан фарқли
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,contact,tg,address,inn,cnote,lc,parent,folder,deleted,src,lim,blocked,ckind,ckind_set,"
                "created_by,created_at) VALUES(?,?,?,'','','','','','',?,?,'',0,0,'pf',0,0,'client',1,?,?)",
                (ext, ext, NOCLI_NAME, "Цех ботидаги накладнойсиз иш: проведен қилишдан олдин ҳақиқий мижозни танланг", lc(NOCLI_NAME), WAIT_WHO,
                 datetime.now().strftime("%Y-%m-%d %H:%M")))
    log(con, None, "client_add", f"{ext}: {NOCLI_NAME}")
    return ext


def save_wait_draft(con, doc_id, client, note, lines):
    """Цех боти: накладнойсиз иш -> накладной черновиги (нархсиз қатор ҳам бўлади — кассир текширади, тўлдиради).
    Кассир ўзгартирган черновикка (updated_by цех эмас) ва проведен/ўчирилганга тегилмайди. -> doc_id"""
    rate = day_rate(date.today())[0] or 1.0
    rows = []
    for l in lines:
        g = con.execute("SELECT * FROM erp_goods WHERE ext=? AND folder=0", ((l.get("good") or "").strip(),)).fetchone()
        qty = float(l.get("qty") or 0)
        if not g or not qty:
            continue
        price = max(0.0, float(l.get("price") or 0))
        rows.append(dict(good=g["ext"], qty=qty, unit=g["unit"], price=price, note=(l.get("note") or "")[:150], **calc_line(g["cur"], qty, price, rate)))
    usd, uzs = r2(sum(x["usd"] for x in rows)), r2(sum(x["uzs"] for x in rows))
    ts, note = A.now(), (note or "")[:2000]
    if doc_id:
        d = con.execute("SELECT * FROM erp_docs WHERE id=?", (doc_id,)).fetchone()
        if not d or d["status"] != "draft" or d["src"] != "pf" or (d["updated_by"] or "") != WAIT_WHO or lock_info(d):
            return doc_id
        old = [(x["good"], x["qty"], x["price"], x["note"]) for x in con.execute("SELECT good, qty, price, note FROM erp_lines WHERE doc_id=? ORDER BY lineno", (doc_id,))]
        if d["client"] == client and (d["note"] or "") == note and old == [(x["good"], x["qty"], x["price"], x["note"]) for x in rows]:
            return doc_id                               # ўзгармади — версияга тегмаймиз (кассир очиб турган бўлиши мумкин)
        con.execute("UPDATE erp_docs SET client=?, rate=?, usd=?, uzs=?, note=?, updated_by=?, updated_at=? WHERE id=?",
                    (client, rate, usd, uzs, note, WAIT_WHO, ts, doc_id))
        con.execute("DELETE FROM erp_lines WHERE doc_id=?", (doc_id,))
        log(con, doc_id, "save", f"цех боти: {len(rows)} қатор")
    else:
        con.commit()
        con.execute("BEGIN IMMEDIATE")
        no = next_no(con, "sale", date.today().year)
        doc_id = con.execute("INSERT INTO erp_docs(kind,no,time,client,rate,usd,uzs,discount,nal,note,pay_usd,pay_uzs,paid,date,due_days,"
                             "status,src,user,created_by,created_at,updated_by,updated_at,ret) "
                             "VALUES('sale',?,?,?,?,?,?,0,1,?,0,0,0,?,?,'draft','pf',?,?,?,?,?,1)",
                             (no, datetime.now().strftime("%H:%M:%S"), client, rate, usd, uzs, note, date.today().isoformat(),
                              client_due(con, client), WAIT_WHO, WAIT_WHO, ts, WAIT_WHO, ts)).lastrowid
        log(con, doc_id, "create", f"{no}, цех боти (накладнойсиз иш)")
    con.executemany("INSERT INTO erp_lines(doc_id,lineno,good,client,qty,unit,price,usd,price_uzs,uzs,price2,debt,note,variant) "
                    "VALUES(?,?,?,'',?,?,?,?,?,?,0,0,?,'')",
                    [(doc_id, i + 1, x["good"], x["qty"], x["unit"], x["price"], x["usd"], x["price_uzs"], x["uzs"], x["note"]) for i, x in enumerate(rows)])
    con.commit()
    return doc_id


def save_sale(con, doc_id, form, post=False):
    """Накладнойни сақлаш. form: client, rate, nal, note, discount, version, lines=[{good, qty, price, note}]. Хато бўлса ValueError."""
    client = (form.get("client") or "").strip()
    if not client or not con.execute("SELECT 1 FROM erp_clients WHERE ext=?", (client,)).fetchone():
        raise ValueError("Мижоз танланмаган")
    if post and client == nocli_ext(con, create=False):
        raise ValueError("Мижоз танланмаган: цех накладнойи — изоҳдан қараб ҳақиқий мижозни танланг")
    ddate0 = A.parse_date(form.get("date")) or date.today()
    if A.is_closed(ddate0):
        raise ValueError(A.closed_text(ddate0))
    if doc_id:
        od = con.execute("SELECT date FROM erp_docs WHERE id=?", (doc_id,)).fetchone()
        if od and A.is_closed(od[0]):
            raise ValueError(A.closed_text(od[0]))
    ref = None
    if form.get("ref_doc"):
        ref = con.execute("SELECT * FROM erp_docs WHERE id=? AND kind='sale' AND status='posted'", (int(form.get("ref_doc")),)).fetchone()
        if not ref:
            raise ValueError("Возврат қилинадиган накладной топилмади ёки проведен эмас")
        if ref["client"] != client:
            raise ValueError("Возвратда мижоз асл накладнойдагидек бўлиши керак")
    if ref:
        rate = ref["rate"] or day_rate(ddate0)[0]           # возврат — асл накладной курсида (қарз аниқ шу суммага камаяди)
    elif A.is_admin() and A.parse_float(form.get("rate"), 0) > 0:
        rate = A.parse_float(form.get("rate"), 0)          # раҳбар қўлда ўзгартириши мумкин
    else:
        rate = day_rate(ddate0)[0]                          # бошқалар учун — фақат ботдаги кун курси
        if rate <= 0:
            raise ValueError("Кун курси ботдан ҳали олинмаган — «Курс $» саҳифасида «Обновить курс» босинг")
    # скидка (эгаси, 25.10): накладнойда % — фақат каталогда «скидка мумкин» белгиланган товар/хизматларга; ёзиш — админ ва кассир
    od0 = con.execute("SELECT * FROM erp_docs WHERE id=?", (doc_id,)).fetchone() if doc_id else None
    if A.is_admin() or A.can("post"):
        disc_pct = min(max(A.parse_float(form.get("disc_pct"), 0), 0.0), 100.0)
    else:
        disc_pct = float((od0["disc_pct"] if od0 is not None and "disc_pct" in od0.keys() else 0) or 0)
    if ref:
        disc_pct = 0.0                                    # возврат — асл накладнойдаги (скидкали) нархда
    disc_usd = 0.0
    lines = []
    for i, l in enumerate(form.get("lines") or []):
        g = con.execute("SELECT * FROM erp_goods WHERE ext=? AND folder=0", ((l.get("good") or "").strip(),)).fetchone()
        qty = A.parse_float(l.get("qty"), 0)
        if not g and not qty:
            continue
        if not g:
            raise ValueError(f"{i + 1}-қатор: товар танланмаган")
        if qty == 0:
            raise ValueError(f"{i + 1}-қатор: сони нотўғри")
        price = A.parse_float(l.get("price"), 0)
        if price <= 0:
            raise ValueError(f"{i + 1}-қатор ({g['name'][:40]}): нарх киритилмаган")
        p0 = price
        if disc_pct and "disc" in g.keys() and g["disc"]:
            price = round(p0 * (1 - disc_pct / 100), 4 if g["cur"] == 2 else 0)
            disc_usd += calc_line(g["cur"], qty, p0, rate)["usd"]
        c = calc_line(g["cur"], qty, price, rate)
        if price != p0:
            disc_usd -= c["usd"]
        var = (l.get("var") or "").strip()
        var = var if (var and var in dict(mdf_sorts()) and _msort(g)) else ""
        lines.append(dict(good=g["ext"], qty=qty, unit=g["unit"], price=price, price2=p0 if price != p0 else 0,
                          note=(l.get("note") or "").strip()[:150], var=var, **c))
    if not lines:
        raise ValueError("Битта ҳам товар йўқ")
    if ref:
        _check_return(con, ref, lines, doc_id)
    usd, uzs = r2(sum(l["usd"] for l in lines)), r2(sum(l["uzs"] for l in lines))
    discount = r2(max(0.0, disc_usd))                     # скидка суммаси, $ (қатор суммалари скидкадан кейин)
    pay_usd, pay_uzs = A.parse_float(form.get("pay_usd"), 0), A.parse_float(form.get("pay_uzs"), 0)
    if pay_usd < 0 or pay_uzs < 0:
        raise ValueError("Тўлов манфий бўлиши мумкин эмас")
    pays = parse_pays(con, form, rate, A.parse_date(form.get("date")) or date.today())
    paid = r2(pay_usd + pay_uzs / rate + sum(p["usd"] for p in pays))   # нақд ($ + сўм/курс) + карта/терминал/Click…
    nal = 0 if str(form.get("nal", "1")) in ("0", "", "off") else 1          # 0 — сумма ҳисобга олинмайди (фақат сони: қолдиқ тўғрилаш)
    if not nal and paid:
        raise ValueError("«Суммани ҳисобга олиш» белгиси йўқ накладнойда тўлов бўлмайди")
    if post:                                   # омборда етмаса ҳам проведен бўлади — огоҳлантириш билан (эгаси, 26.10: кромка ҳали
        short = shortages(con, lines, doc_id)  # саналмаган); ким минусга ёзгани тарихда
        save_sale.short = short
    if post and nal:                                                  # лимит: проведен қилишдан олдин (қоралама — текширилмайди)
        try:
            sync()                                            # 1С'даги охирги тўловлар (касса) ҳисобга олинсин
        except Exception:
            pass
        credit_check(con, client, r2(usd - paid), exclude_doc=doc_id, total=usd)
    ddate = A.parse_date(form.get("date")) or date.today()
    who, ts = _user(), A.now()
    status = "posted" if post else "draft"
    resp = (form.get("resp") or "").strip() or None
    if resp and not any(p["key"] == resp for p in resp_people(con)) and not (doc_id and con.execute(
            "SELECT 1 FROM erp_docs WHERE id=? AND resp=?", (doc_id, resp)).fetchone()):
        raise ValueError("Масъул шахс рўйхатдан танланмаган")
    kon_l, kro_l = staff_lists(con)
    konstr = int(A.parse_float(form.get("konstr"), 0)) or None
    kroychi = int(A.parse_float(form.get("kroychi"), 0)) or None
    od2 = con.execute("SELECT konstr, kroychi FROM erp_docs WHERE id=?", (doc_id,)).fetchone() if doc_id else None
    if konstr and konstr not in {x["id"] for x in kon_l} and not (od2 and od2["konstr"] == konstr):
        raise ValueError("Конструктор рўйхатдан танланмаган")
    if kroychi and kroychi not in {x["id"] for x in kro_l} and not (od2 and od2["kroychi"] == kroychi):
        raise ValueError("Кройчи рўйхатдан танланмаган")
    if post and not konstr and any(KONSTR_RE.search(con.execute("SELECT name FROM erp_goods WHERE ext=?", (l["good"],)).fetchone()[0] or "")
                                   for l in lines if l["qty"] > 0):
        raise ValueError("Накладнойда «Услуга конструктора» бор — «Конструктор» майдонида конструкторни танланг")
    head = (client, rate, usd, uzs, discount, nal, (form.get("note") or "").strip()[:2000],
            r2(pay_usd), r2(pay_uzs), paid, ddate.isoformat(), client_due(con, client), ref["id"] if ref else None,
            (who if post and shortages(con, lines, doc_id) else None), resp, konstr, kroychi,
            1 if str(form.get("urgent") or "") in ("1", "on") else 0,
            1 if str(form.get("urgent") or "") in ("1", "on") and str(form.get("urgent_free") or "") in ("1", "on") else 0,
            disc_pct if any(l["price2"] for l in lines) else 0.0)
    if doc_id:
        d = con.execute("SELECT * FROM erp_docs WHERE id=?", (doc_id,)).fetchone()
        if not d or d["src"] != "pf":
            raise ValueError("Бу ҳужжатни платформада ўзгартириб бўлмайди")
        if d["status"] == "deleted":
            raise ValueError("Ҳужжат ўчирилган")
        lk = lock_info(d)
        if lk:
            raise ValueError(f"{lk[0]} ҳозир шу ҳужжатни таҳрирлаяпти")
        if (form.get("version") or "") != (d["updated_at"] or ""):
            raise ValueError(f"Ҳужжатни {d['updated_by']} {d['updated_at']} да ўзгартирган — саҳифани янгиланг")
        con.execute("UPDATE erp_docs SET client=?,rate=?,usd=?,uzs=?,discount=?,nal=?,note=?,pay_usd=?,pay_uzs=?,paid=?,date=?,"
                    "due_days=?,ref_doc=?,minus_by=?,resp=?,konstr=?,kroychi=?,urgent=?,urgent_free=?,disc_pct=?,status=?,updated_by=?,updated_at=?,"
                    "lock_by=NULL,lock_at=NULL WHERE id=?", head + (status, who, ts, doc_id))
        con.execute("DELETE FROM erp_lines WHERE doc_id=?", (doc_id,))
        log(con, doc_id, "post" if post else "save", f"{len(lines)} қатор, ${usd:.2f}")
    else:
        con.commit()
        con.execute("BEGIN IMMEDIATE")          # икки киши бир вақтда сақласа ҳам рақам такрорланмайди
        no = next_no(con, "sale", ddate.year)
        doc_id = con.execute("INSERT INTO erp_docs(kind,no,time,client,rate,usd,uzs,discount,nal,note,pay_usd,pay_uzs,paid,date,due_days,ref_doc,"
                             "minus_by,resp,konstr,kroychi,urgent,urgent_free,disc_pct,status,src,user,created_by,created_at,updated_by,updated_at,ret) "
                             "VALUES('sale',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pf',?,?,?,?,?,1)",
                             (no, datetime.now().strftime("%H:%M:%S")) + head + (status, who, who, ts, who, ts)).lastrowid
        log(con, doc_id, "create" + ("+post" if post else ""), f"{no}, {len(lines)} қатор, ${usd:.2f}")
    if post and head[13]:
        log(con, doc_id, "minus_ok", "; ".join(f"{x['name']}: {A.num(x['stock'])} → {A.num(x['stock'] - x['qty'])}"
                                               for x in shortages(con, lines, doc_id)))
    con.executemany("INSERT INTO erp_lines(doc_id,lineno,good,client,qty,unit,price,usd,price_uzs,uzs,price2,debt,note,variant) "
                    "VALUES(?,?,?,'',?,?,?,?,?,?,?,0,?,?)",
                    [(doc_id, i + 1, l["good"], l["qty"], l["unit"], l["price"], l["usd"], l["price_uzs"], l["uzs"], l.get("price2") or 0, l["note"],
                      l.get("var") or "") for i, l in enumerate(lines)])
    # тўловлар: нақд — «Нақд» тури орқали нақд кассага, қолганлари — ўз ҳисобига (комиссия билан)
    con.execute("DELETE FROM erp_pays WHERE doc_id=?", (doc_id,))
    cash = con.execute("SELECT m.id, m.account_id FROM erp_methods m JOIN erp_accounts a ON a.id=m.account_id WHERE a.kind='cash' "
                       "ORDER BY m.sort, m.id LIMIT 1").fetchone()
    rows = [dict(method_id=cash["id"] if cash else None, account_id=cash["account_id"] if cash else None, cur=c_, amount=r2(a_),
                 usd=r2(a_ if c_ == "usd" else a_ / rate), fee_pct=0, fee=0) for c_, a_ in (("uzs", pay_uzs), ("usd", pay_usd)) if a_ > 0]
    con.executemany("INSERT INTO erp_pays(doc_id,method_id,account_id,cur,amount,usd,fee_pct,fee) VALUES(?,?,?,?,?,?,?,?)",
                    [(doc_id, p["method_id"], p["account_id"], p["cur"], p["amount"], p["usd"], p["fee_pct"], p["fee"]) for p in rows + pays])
    _kusok_sync(con, doc_id)
    return doc_id


def _kusok_sync(con, doc_id):
    try:
        import raspil as _P
        _P.kusok_doc_sync(con, doc_id)
    except Exception:
        A.errlog.exception("kusok sync doc %s", doc_id)


def doc_full(con, doc_id):
    d = con.execute("SELECT d.*, c.name cname, c.phones cphones FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.id=?",
                    (doc_id,)).fetchone()
    if not d:
        return None
    lines = con.execute("SELECT l.*, g.name || CASE WHEN COALESCE(l.variant,'')<>'' THEN ' (' || l.variant || ')' ELSE '' END gname, "
                        "g.cur gcur, c.name cname FROM erp_lines l LEFT JOIN erp_goods g ON g.ext=l.good "
                        "LEFT JOIN erp_clients c ON c.ext=l.client WHERE l.doc_id=? ORDER BY l.lineno", (doc_id,)).fetchall()
    pays = con.execute("SELECT p.*, m.name mname, a.name aname FROM erp_pays p LEFT JOIN erp_methods m ON m.id=p.method_id "
                       "LEFT JOIN erp_accounts a ON a.id=p.account_id WHERE p.doc_id=? ORDER BY p.id", (doc_id,)).fetchall()
    return dict(d=d, lines=lines, pays=pays)


_stock = {"at": 0.0, "v": {}}

def stock_now():
    """Омбор қолдиғи 1С регистридан (ОстатокТов), 60 сония кэш."""
    if time.time() - _stock["at"] > 60:
        try:
            _stock["v"] = c1.stock()[1]
        except Exception:
            _stock["v"] = {}
        _stock["at"] = time.time()
    return _stock["v"]


# ================================================================ масъул шахс (накладной учун жавобгар ходим ёки шерик)
def resp_people(con):
    """Корхона ходимлари (касса ойлик рўйхати, ташқи хизматлардан ташқари) ва шериклар. key: «kw:<id>» ёки «rw:<id>»."""
    out, seen_kw = [], set()
    try:
        for r in con.execute("SELECT id, name FROM k_workers WHERE active=1 AND COALESCE(svc,0)=0 AND (fired IS NULL OR fired='') ORDER BY name"):
            out.append(dict(key=f"kw:{r['id']}", name=r["name"], kw=r["id"], partner=False)); seen_kw.add(r["id"])
    except Exception:
        pass
    try:
        for r in con.execute("SELECT id, name, kw_id FROM ready_workers WHERE owner=1 AND active=1 ORDER BY name"):
            if r["kw_id"] and r["kw_id"] in seen_kw:
                for o in out:
                    if o["kw"] == r["kw_id"]:
                        o["partner"] = True
                continue
            out.append(dict(key=f"rw:{r['id']}", name=r["name"], kw=r["kw_id"], partner=True))
    except Exception:
        pass
    return out


MINUS_WARN = "Омборда етарли эмас — минусга ёзилди: "
KONSTR_RE = re.compile(r"^\s*услуга\s+конструктор", re.I)       # «Услуга конструктора СТАНДАРТ/ПРЕМИУМ/ГОЛД/ПЛАТИНУМ» (кв-м)


def staff_lists(con):
    """Накладнойдаги «Конструктор» ва «Кройчи» рўйхати (касса ишчилари): конструктор — Базис конструкторлари
    (1С папкаларига боғланган ишчилар), кройчи — ботда «крой менежер» белгиси бор ишчилар. -> (konstr[], kroy[])"""
    kon, kro = [], []
    try:
        import raspil as P
        ids = {int(v) for v in (P.basis_map() or {}).values() if v}
    except Exception:
        ids = set()
    try:
        rows = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM k_workers WHERE (fired IS NULL OR fired='')")}
        kon = [dict(id=i, name=rows[i]) for i in sorted(ids, key=lambda i: rows.get(i, "")) if i in rows]
        kro = [dict(id=r["kw_id"], name=rows.get(r["kw_id"]) or r["name"]) for r in
               con.execute("SELECT kw_id, name FROM ready_workers WHERE kroy=1 AND active=1 AND kw_id IS NOT NULL ORDER BY name")
               if r["kw_id"] in rows and not re.match(r"\s*тест", rows.get(r["kw_id"]) or "", re.I)]
    except Exception:
        pass
    return kon, kro


def _setf(key, default):
    try:
        return float(A.get_setting(key, str(default)) or default)
    except (TypeError, ValueError):
        return float(default)


def urgent_add():
    """Срочно: тўғри кромкалаш хизматига 1 метрга қўшимча (Эман прайси: 19/1 4 500 → 6 000)."""
    return _setf("urgent_kromka_add", 1500)


URG_SAW_SKIP = re.compile(r"кромк|столешн", re.I)               # кромка распили ва столешница — срочно таъсир қилмайди
URG_SAW_PIECE = re.compile(r"кусок|остатк", re.I)
URG_SAW_ANGLE = re.compile(r"угл|45", re.I)


def urgent_map(con):
    """Срочно: қайси хизматга 1 бирлик учун қанча қўшилади (1С'даги эски «СРОЧНЫЙ» нархлари ишлатилмайди).
    • распил (двухпилка — «ОБЫЧНЫЙ», пильный центр — «ПАКЕТНЫЙ»): лист +25 000 (Эман прайси), кусок м² +4 300 (25 000 ÷ 5,796),
      бурчак остида / 45° +3 500; кромка распили ва столешница — ўзгармайди;
    • тўғри кромкалаш («2 Кромка») ва овал («3 Кромка Овал»): метрига +1 500;
    • қўшимча: созлама «urgent_extra» (JSON {хизмат ext: сўм}). -> dict(add={ext: сўм})"""
    fold = {r["ext"]: (r["name"] or "").casefold() for r in con.execute("SELECT ext, name FROM erp_goods WHERE folder=1 AND deleted=0")}
    kids = defaultdict(list)
    for r in con.execute("SELECT ext, name, parent, folder, deleted FROM erp_goods WHERE COALESCE(src,'1c')<>'cat'"):
        kids[r["parent"] or "0"].append(r)

    def walk(ext):
        for r in kids.get(ext, []):
            if r["folder"]:
                yield from walk(r["ext"])
            elif not r["deleted"]:
                yield r
    add = {}
    ka, oa = urgent_add(), _setf("urgent_oval_add", 1500)
    sa, pa, aa = _setf("urgent_raspil_add", 25000), _setf("urgent_kusok_add", 4300), _setf("urgent_angle_add", 3500)
    for f, n in fold.items():
        if re.match(r"\s*2\s+кромка", n):
            for r in walk(f):
                add[r["ext"]] = ka
        elif re.match(r"\s*3\s+кромка", n):
            for r in walk(f):
                add[r["ext"]] = oa
        elif "распил" in n and "срочн" not in n and re.search(r"обычн", n):
            for r in walk(f):
                nm = r["name"] or ""
                if URG_SAW_SKIP.search(nm):
                    continue
                add[r["ext"]] = pa if URG_SAW_PIECE.search(nm) else (aa if URG_SAW_ANGLE.search(nm) else sa)
    try:
        for e, v in (json.loads(A.get_setting("urgent_extra", "") or "{}") or {}).items():
            add[e] = float(v or 0)
    except (ValueError, TypeError):
        pass
    return dict(add={k: v for k, v in add.items() if v})


def resp_info(con, key):
    """-> dict(key, name, kw, bot_uid) ёки None."""
    if not key or ":" not in key:
        return None
    t, i = key.split(":", 1)
    try:
        i = int(i)
    except ValueError:
        return None
    if t == "kw":
        w = con.execute("SELECT id, name FROM k_workers WHERE id=?", (i,)).fetchone()
        if not w:
            return None
        rw = con.execute("SELECT bot_uid FROM ready_workers WHERE kw_id=? AND active=1 AND bot_uid IS NOT NULL ORDER BY id LIMIT 1", (i,)).fetchone()
        return dict(key=key, name=w["name"], kw=w["id"], bot_uid=rw[0] if rw else None)
    if t == "rw":
        r = con.execute("SELECT id, name, kw_id, bot_uid FROM ready_workers WHERE id=?", (i,)).fetchone()
        return dict(key=key, name=r["name"], kw=r["kw_id"], bot_uid=r["bot_uid"]) if r else None
    return None


def resp_ded(ym):
    """Масъул сифатида ойликдан ушланган: {kw_id: сумма (ишчи валютасида)}."""
    try:
        return {r[0]: r[1] for r in A.q("SELECT kw_id, SUM(amount) FROM erp_resp_ded WHERE ym=? AND deleted=0 GROUP BY kw_id", (ym,))}
    except Exception:
        return {}


def _kmemo():
    try:
        import kassa as _k
        _k._memo.clear()
    except Exception:
        pass


def resp_refund(con=None, today=None):
    """Мижоз кейин тўласа — ушланган пул ишчига қайтади (тўланган улушига қараб). Қайтарилган сумма тўлов бўлган ойнинг ойлигига
    қўшилади: ўша ой ойлигини ҳали олмаган бўлса — шу ойнинг ҳисобига, олган бўлса — кейинги ойликка (қолдиқ ойдан ойга ўтади)."""
    own = con is None
    con = con or A.db()
    today = today or date.today()
    rows = con.execute("SELECT r.*, d.client, d.no FROM erp_resp_ded r JOIN erp_docs d ON d.id=r.doc_id "
                       "WHERE r.deleted=0 AND r.amount>0 AND r.ref_id IS NULL").fetchall()
    unpaid, n = {}, 0
    for r in rows:
        if r["client"] not in unpaid:
            unpaid[r["client"]] = doc_unpaid(con, r["client"])
        now_u = unpaid[r["client"]].get(r["doc_id"], 0.0)
        base = r["base_usd"] or r["usd"] or 0
        if base <= 0:
            continue
        paid_back = max(0.0, min(base, base - now_u))
        target = round((r["usd"] or 0) * paid_back / base, 2)                  # ушланганнинг тўланган улуши ($)
        done = -(con.execute("SELECT COALESCE(SUM(usd),0) FROM erp_resp_ded WHERE ref_id=? AND deleted=0", (r["id"],)).fetchone()[0] or 0)
        add_usd = round(target - done, 2)
        if add_usd <= 0.009:
            continue
        if r["cur"] == "USD":
            amt = add_usd
        else:
            full = (r["amount"] or 0)
            amt = round(full * add_usd / (r["usd"] or 1), 0)                   # ушланган курс бўйича (ишчи ютқазмасин)
        con.execute("INSERT INTO erp_resp_ded(doc_id,kw_id,ym,amount,cur,usd,note,by,at,ref_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (r["doc_id"], r["kw_id"], today.isoformat()[:7], -amt, r["cur"], -add_usd, "мижоз тўлади — қайтди", "тизим", A.now(), r["id"]))
        con.execute("INSERT INTO erp_log(at,user,doc_id,action,detail) VALUES(?,?,?,?,?)",
                    (A.now(), "тизим", r["doc_id"], "resp_refund", f"мижоз тўлади — ишчига қайтди {A.num(amt)} {'$' if r['cur'] == 'USD' else 'сўм'}"))
        n += 1
        try:
            rw = con.execute("SELECT bot_uid FROM ready_workers WHERE kw_id=? AND active=1 AND bot_uid IS NOT NULL LIMIT 1", (r["kw_id"],)).fetchone()
            if rw:
                import raspil_bot as B
                B.send(rw[0], f"✅ №{r['no']}: мижоз тўлади.\nОйлигингизга қайтди: {A.num(amt)} {'$' if r['cur'] == 'USD' else 'сўм'}")
        except Exception:
            A.errlog.exception("resp refund notify")
    if n:
        _kmemo()
    if own:
        con.commit()
    return n


def resp_remind(today=None, force=False):
    """Муддати ўтган, масъул белгиланган накладнойлар — масъулга Telegram (ходимлар боти) орқали кунига бир марта эслатма."""
    today = today or date.today()
    con = A.db()
    rows = con.execute("SELECT d.id, d.no, d.date, d.client, d.resp, c.name cname FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client "
                       "WHERE d.kind='sale' AND d.status='posted' AND COALESCE(d.nal,1)=1 AND d.resp IS NOT NULL AND d.resp<>''").fetchall()
    if not rows:
        return 0
    od_by_client = {}
    n = 0
    for d in rows:
        if d["client"] not in od_by_client:
            od_by_client[d["client"]] = {o["id"]: o for o in overdue(con, d["client"], today=today)}
        o = od_by_client[d["client"]].get(d["id"])
        if not o:
            continue
        key = f"resp:{d['id']}:{today.isoformat()}"
        if not force and con.execute("SELECT 1 FROM erp_log WHERE action='resp_remind' AND detail LIKE ?", (key + "%",)).fetchone():
            continue
        ri = resp_info(con, d["resp"])
        if not ri or not ri["bot_uid"]:
            continue
        dd = A.parse_date(d["date"])
        txt = (f"⏰ №{d['no']} ({dd:%d.%m}) — {d['cname'] or ''}\n"
               f"Қарз ${o['part']:,.2f}, муддати {o['days']} кун ўтди.\nСиз масъулсиз.").replace(",", " ")
        try:
            import raspil_bot as B
            ok = B.send(ri["bot_uid"], txt)
        except Exception:
            A.errlog.exception("resp remind")
            ok = False
        con.execute("INSERT INTO erp_log(at,user,doc_id,action,detail) VALUES(?,?,?,?,?)",
                    (A.now(), "тизим", d["id"], "resp_remind", f"{key} → {ri['name']}" + ("" if ok else " (юборилмади)")))
        n += 1 if ok else 0
    con.commit()
    return n


_norms = {"at": 0.0, "v": {}}

def norms():
    """1С «Норматив» (SC1258): хизмат сотилганда омбордан қайси товар қанча чиқади. {хизмат: [(товар, норма)]}.
    Масалан «2 Кромка»даги кромкалаш хизмати → «6 Кромка склад новый»даги кромка ×1 ва клей ×0,00521 (1 метрга)."""
    if time.time() - _norms["at"] > 300:
        out = defaultdict(list)
        try:
            for r in c1.table("SC1258"):
                if (r.get("ISMARK") or "").strip() == "*":
                    continue
                svc, g, q = (r.get("PARENTEXT") or "").strip(), (r.get("SP1260") or "").strip(), float(r.get("SP1261") or 0)
                if svc and g and g != "0" and q:
                    out[svc].append((g, q))
        except Exception:
            out = {k: [x for x in v] for k, v in (_norms["v"] or {}).items() if not k.startswith("ci")}
        try:                                   # каталог хизматлари нормативи (cat_norms): «Услуга ПВХ …» → кромка ×1, клей …
            own = None
            try:
                cn = A.db()
            except RuntimeError:               # фон оқими (request контексти йўқ)
                cn = own = A.connect()
            try:
                for r in cn.execute("SELECT item, comp, qty FROM cat_norms WHERE COALESCE(qty,0)<>0"):
                    out.setdefault(f"ci{r[0]}", []).append((f"ci{r[1]}", float(r[2])))
            finally:
                if own is not None:
                    own.close()
        except Exception:
            pass
        _norms["v"], _norms["at"] = dict(out), time.time()
    return _norms["v"]


def expand_lines(pairs):
    """[(товар, сон)] → хизматлар нормативи бўйича омбордан чиқадиган товарлар қўшилган рўйхат (хизматнинг ўзи ҳам қолади)."""
    nm = norms()
    out = []
    for g, q in pairs:
        out.append((g, q))
        for cg, k in nm.get(g, ()):
            out.append((cg, round((q or 0) * k, 6)))
    return out


def stock_all(con, exclude_doc=None):
    """Ҳозирги қолдиқ: 1С регистри − платформада проведен қилинган (1С'да йўқ) накладнойлар сони (хизматлар нормативи билан). {good: qty}"""
    st = dict(stock_now())
    rows = con.execute("SELECT l.good, SUM(l.qty) FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.src='pf' AND d.kind='sale' "
                       "AND d.status='posted' AND d.id<>? GROUP BY l.good", (exclude_doc or 0,)).fetchall()
    for g, q in expand_lines([(r[0], r[1] or 0) for r in rows]):
        st[g] = round((st.get(g) or 0) - (q or 0), 3)
    for g, q in con.execute("SELECT l.good, SUM(l.qty) FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.src='pf' AND d.kind='receipt' "
                            "AND d.status='posted' AND d.id<>? GROUP BY l.good", (exclude_doc or 0,)):
        st[g] = round((st.get(g) or 0) + (q or 0), 3)          # платформада ёзилган приход
    for g, q in con.execute("SELECT l.good, SUM(l.qty) FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.src='pf' AND d.kind='inv' "
                            "AND d.status='posted' AND d.id<>? GROUP BY l.good", (exclude_doc or 0,)):
        st[g] = round((st.get(g) or 0) + (q or 0), 3)          # инвентаризация: фарқ (факт − учёт)
    try:                                                       # кусоклар: қолдиқ — бот кусок омборидан (фақат ишхонаники, РайГаз)
        import raspil as _P
        kc = _P.kusok_catalog(con)
        for g in _kusok_goods(con):
            st[g] = kc.get(g, {}).get("m2", 0.0)
    except Exception:
        pass
    return st


_kg = {"at": 0.0, "v": set()}


def _kusok_goods(con):
    """Каталогдаги «Остатки (кусок)» товарлари (1С «Остатки» папкасидан келган, кв.м): {«ci<id>»}."""
    if time.time() - _kg["at"] > 300:
        try:
            _kg["v"] = {f"ci{r[0]}" for r in con.execute(           # SQLite lower() кириллни кичик қилмайди — Python'да
                "SELECT i.id, n.name nn, i.unit FROM cat_items i JOIN cat_nodes n ON n.id=i.node WHERE i.deleted=0 AND COALESCE(i.svc,0)=0 "
                "AND i.skey LIKE 'c1g:%'") if "остат" in (r[1] or "").casefold() and "кв" in (r[2] or "").casefold()}
        except Exception:
            _kg["v"] = set()
        _kg["at"] = time.time()
    return _kg["v"]


def shortages(con, lines, doc_id=None):
    """Омборда етмайдиган товарлар (минусга кетади): [dict(good, name, stock, qty)]. Хизматлар (папкаси «Услуг…») ва
    қайтаришлар (минус сон) текширилмайди."""
    st = stock_all(con, doc_id)
    need = {}
    for g, q in expand_lines([(l["good"], l.get("qty") or 0) for l in lines if (l.get("qty") or 0) > 0]):
        need[g] = need.get(g, 0) + q                 # кромкалаш хизмати → кромка ва клей ҳам текширилади
    out = []
    for g, q in need.items():
        r = con.execute("SELECT name, parent, src FROM erp_goods WHERE ext=?", (g,)).fetchone()
        if not r or is_service(con, g) or (r["src"] == "cat" and g not in st):   # каталог товари: приход/инвентаризация бўлгандан кейин текширилади
            continue
        have = st.get(g) or 0
        if q > have + 1e-6:
            out.append(dict(good=g, name=r["name"], stock=round(have, 3), qty=round(q, 3)))
    return out


_svc = {"at": 0.0, "v": set()}

def is_service(con, good):
    """Хизмат (распил, кромкалаш, услуга…) — омбор қолдиғи йўқ. 1С'да хизматлар «Услуг…» папкасида."""
    if time.time() - _svc["at"] > 300:
        fp = folder_paths(con, orig=True)
        # 1С: номи рақам билан бошланадиган папкалар (1…9 ва 99) — хизмат (эгаси айтган қоида), фақат «6 Кромка склад новый» — товар:
        # «1 Распил», «2 Кромка», «3 Кромка Овал», «4 Ровер», «5 Остатки (кусок)», «7 Базис Конструктор», «8 Утолшенка», «9 Присадка», «99 Услуги доставки»
        _svc["v"] = {k for k, v in fp.items() if (re.match(r"\s*([1-9]|99)\s", v) and not re.match(r"\s*6\s", v)) or re.search(r"услуг", v.casefold())}
        _svc["at"] = time.time()
    r = con.execute("SELECT parent, name, COALESCE(cat,'') cat FROM erp_goods WHERE ext=?", (good,)).fetchone()
    if r and r["cat"] in ("good", "svc"):        # 1С'даги «Категория» (Товар / Услуга) — асосий белги
        return r["cat"] == "svc"
    return bool(r and (r["parent"] in _svc["v"] or re.match(r"\s*(распил|услуга|срочный распил)", (r["name"] or "").casefold())))


def returned_qty(con, ref_id, exclude_doc=None):
    """Асл накладной бўйича аввал қайтарилган сон: {good: qty>0}."""
    out = {}
    for g, q in con.execute("SELECT l.good, SUM(l.qty) FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.ref_doc=? "
                            "AND d.status<>'deleted' AND d.id<>? GROUP BY l.good", (ref_id, exclude_doc or 0)):
        out[g] = -(q or 0)
    return out


def _check_return(con, ref, lines, doc_id=None):
    sold = {}
    for g, q in con.execute("SELECT good, SUM(qty) FROM erp_lines WHERE doc_id=? GROUP BY good", (ref["id"],)):
        sold[g] = q or 0
    back = returned_qty(con, ref["id"], doc_id)
    now = {}
    for l in lines:
        if l["qty"] > 0:
            raise ValueError("Возвратда сон минус бўлиши керак")
        now[l["good"]] = now.get(l["good"], 0) - l["qty"]
    for g, q in now.items():
        left = round((sold.get(g) or 0) - (back.get(g) or 0), 3)
        if g not in sold:
            raise ValueError("Возвратда фақат асл накладнойдаги товарлар бўлади")
        if q > left + 1e-6:
            nm = con.execute("SELECT name FROM erp_goods WHERE ext=?", (g,)).fetchone()
            raise ValueError(f"{nm[0] if nm else g}: сотилгани {A.num(sold[g])}, аввал қайтарилган {A.num(back.get(g) or 0)} — "
                             f"яна кўпи билан {A.num(left)} қайтариш мумкин")


EFF_PARENT = "COALESCE(NULLIF(pf_parent,''), parent, '')"      # платформадаги папка (ўтказилган бўлса), бўлмаса 1С'дагиси


def gscope(src=None):
    """Товар рўйхати қаердан: 'cat' (асосий) — «Каталог платформы» (админ бўлмаганга фақат плита, кромка, хизматлар);
    '1c' — 1С товарлари (эски ҳужжатларни ўқиш, фақат админ); 'all' — ҳаммаси (ҳисоботлар). -> SQL шарти (AND …)"""
    from flask import request as _rq
    src = src or _rq.args.get("src") or "cat"
    u = A.current_user()
    adm = bool(u) and u["role"] == "admin"
    if src == "1c" and adm:
        return " AND COALESCE(src,'1c')<>'cat'"
    if src == "all":
        return "" if adm else " AND (COALESCE(src,'1c')<>'cat' OR cvis=1)"
    return " AND src='cat'" + ("" if adm else " AND cvis=1")


def folder_paths(con, orig=False):
    """{папка ext: «Ота / Бола»}. orig=True — 1С'даги жойи бўйича (хизмат папкаларини аниқлаш учун)."""
    par = "parent" if orig else EFF_PARENT
    items = {r["ext"]: dict(name=r["name"], parent=r["p"]) for r in con.execute(f"SELECT ext,name,{par} p FROM erp_goods WHERE folder=1")}
    return {k: c1.folder_path(items, k) for k in items}


# ================================================================ ҳисоблар ва тўлов турлари (карта, терминал, Click, Payme, QR…)
ACC_KINDS = {"cash": "Нақд касса", "card": "Карта", "bank": "Банк ҳисоби", "invest": "Жамғарма / инвестиция"}
SEED_ACCOUNTS = [("Нақд касса", "cash"), ("Карта ПСБ 0311", "card"), ("Карта ОФБ 1344", "card"), ("Карта АСАКА 05-76", "card"),
                 ("Банк ҳисоби", "bank"), ("Инвестиция", "invest")]
SEED_METHODS = [("Нақд", "Нақд касса", 0), ("Карта ПСБ 0311", "Карта ПСБ 0311", 0), ("Карта ОФБ 1344", "Карта ОФБ 1344", 0),
                ("Карта АСАКА 05-76", "Карта АСАКА 05-76", 0), ("Терминал", "Банк ҳисоби", 1), ("Click", "Банк ҳисоби", 1),
                ("Payme", "Банк ҳисоби", 1), ("Ягона QR тўлов", "Банк ҳисоби", 1), ("Перечисление", "Банк ҳисоби", 0)]


def seed_accounts(con):
    if con.execute("SELECT 1 FROM erp_accounts LIMIT 1").fetchone():
        return
    ids = {}
    for i, (n, k) in enumerate(SEED_ACCOUNTS):
        ids[n] = con.execute("INSERT INTO erp_accounts(name,kind,sort) VALUES(?,?,?)", (n, k, i)).lastrowid
    for i, (n, acc, pct) in enumerate(SEED_METHODS):
        mid = con.execute("INSERT INTO erp_methods(name,account_id,sort) VALUES(?,?,?)", (n, ids[acc], i)).lastrowid
        con.execute("INSERT INTO erp_fees(method_id,from_date,pct,set_by,set_at) VALUES(?,?,?,?,?)",
                    (mid, "2000-01-01", pct, "тизим", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


def map_c1_accounts(con):
    """1С'даги «Карта ПСБ 0311», «Поступление в счёт», «Инвестиция» мижозларини платформа ҳисобларига боғлаш (бир марта, номи бўйича)."""
    try:
        c1s = con.execute("SELECT ext, name FROM erp_clients WHERE folder=0").fetchall()
    except Exception:
        return
    for a in con.execute("SELECT * FROM erp_accounts WHERE COALESCE(c1_ext,'')=''").fetchall():
        key = (a["name"] or "").casefold()
        digits = re.findall(r"\d{4}|\d{2}-\d{2}", key)
        for c in c1s:
            n = (c["name"] or "").casefold()
            hit = (a["kind"] == "bank" and n.startswith("поступление в сч")) or (a["kind"] == "invest" and n.startswith("инвестиц")) or \
                  (a["kind"] == "card" and n.startswith("карта") and digits and all(dg in n for dg in digits))
            if hit:
                con.execute("UPDATE erp_accounts SET c1_ext=? WHERE id=?", (c["ext"], a["id"]))
                break


def fee_pct(con, method_id, d=None):
    """Комиссия % шу санага (ўзгарса — янги ёзув, эскиси тарихда қолади)."""
    r = con.execute("SELECT pct FROM erp_fees WHERE method_id=? AND from_date<=? ORDER BY from_date DESC, id DESC LIMIT 1",
                    (method_id, (d or date.today()).isoformat() if not isinstance(d, str) else d)).fetchone()
    return float(r[0]) if r else 0.0


def methods(con, all_=False):
    return con.execute("SELECT m.*, a.name aname, a.kind akind FROM erp_methods m LEFT JOIN erp_accounts a ON a.id=m.account_id "
                       + ("" if all_ else "WHERE m.active=1 ") + "ORDER BY m.sort, m.id").fetchall()


def account_balances(con, upto=None):
    """Ҳисоблар қолдиғи: нақд — кунлик касса (охирги кун), бошқалари — кассадаги ҳисоб юритуви (1С қаторлари, нақдсиз тўловлар,
    ўтказмалар, бошланғич қолдиқ). Платформа накладнойлари тўловлари — 1С'дан тўлиқ ўтилгандан кейин (erp_pf_money=1)."""
    import kassa as K
    upto = upto or date.today()
    out = []
    pf = A.get_setting("erp_pf_money", "0") == "1"
    for r in con.execute("SELECT * FROM erp_accounts ORDER BY sort, id").fetchall():
        b = dict(acc=r, uzs=0.0, usd=0.0, fee_uzs=0.0, fee_usd=0.0)
        if r["kind"] == "cash":
            try:
                K._memo.clear()
                o = K.month_calc(K.ym_of(upto.year, upto.month))["days"][upto.day - 1]
                v = o["fact"] or o["end"]
                b["uzs"], b["usd"] = v[0], v[1]
            except Exception:
                pass
        else:
            ad = K.acc_day(r, upto)
            b["uzs"], b["usd"] = ad["end"]
            b["opened"] = ad["opened"]
        if pf:
            for p in con.execute("SELECT p.cur, SUM(p.amount) a, SUM(p.fee) f FROM erp_pays p JOIN erp_docs d ON d.id=p.doc_id "
                                 "WHERE d.status='posted' AND d.date<=? AND p.account_id=? GROUP BY p.cur", (upto.isoformat(), r["id"])):
                b[p["cur"]] += (p["a"] or 0) - (p["f"] or 0); b["fee_" + p["cur"]] += p["f"] or 0
        out.append(b)
    return out


def parse_pays(con, f, rate, d):
    """Нақддан бошқа тўловлар: pm_method[], pm_amount[], pm_cur[] -> [dict]; комиссия шу санадаги % бўйича."""
    out = []
    ms, amts, curs = f.get("pm_method") or [], f.get("pm_amount") or [], f.get("pm_cur") or []
    for i, mid in enumerate(ms):
        amt = A.parse_float(amts[i] if i < len(amts) else "", 0)
        if not mid and not amt:
            continue
        m = con.execute("SELECT * FROM erp_methods WHERE id=?", (mid,)).fetchone() if str(mid).isdigit() else None
        if not m:
            raise ValueError(f"{i + 1}-тўлов: тўлов турини танланг")
        if amt <= 0:
            raise ValueError(f"{i + 1}-тўлов ({m['name']}): суммани ёзинг")
        cur = "usd" if (curs[i] if i < len(curs) else "") == "usd" else "uzs"
        pct = fee_pct(con, m["id"], d)
        out.append(dict(method_id=m["id"], account_id=m["account_id"], cur=cur, amount=r2(amt),
                        usd=r2(amt if cur == "usd" else amt / rate), fee_pct=pct, fee=r2(amt * pct / 100), name=m["name"]))
    return out


DENY = "Бу ҳаракатга доступ йўқ. Рухсатни админ беради."


def _new_ext(con, table, prefix):
    """Платформада қўшилган ёзув учун ID: P1, P2… (1С ID'лари билан тўқнашмайди)."""
    n = 0
    for (e,) in con.execute(f"SELECT ext FROM {table} WHERE ext LIKE ?", (prefix + "%",)):
        try:
            n = max(n, int(e[len(prefix):]))
        except ValueError:
            pass
    return f"{prefix}{n + 1}"


def refs_good(con, ext):
    """Товар нечта ҳужжатда бор (тарих)."""
    return con.execute("SELECT COUNT(DISTINCT doc_id) FROM erp_lines WHERE good=?", (ext,)).fetchone()[0]


def refs_client(con, ext):
    return con.execute("SELECT COUNT(*) FROM erp_docs WHERE client=? OR id IN (SELECT doc_id FROM erp_lines WHERE client=?)",
                       (ext, ext)).fetchone()[0]


def others_working(con):
    """Монопол режим: бошқа фойдаланувчилар ҳозир ҳужжат ёки кассани очиб турибдими — [исмлар]."""
    me, t = _user(), time.time() - LOCK_SEC
    who = {r[0] for r in con.execute("SELECT lock_by FROM erp_docs WHERE lock_by IS NOT NULL AND lock_by<>? AND lock_at>?", (me, t))}
    try:
        who |= {r[0] for r in con.execute("SELECT by FROM k_edit_lock WHERE by<>? AND at>?", (me, t))}
    except Exception:
        pass
    return sorted(who)


def purge_marked(con, pick):
    """Админ танлаганларни бутунлай ўчириш (pick: {"g:EXT", "c:EXT", "d:ID"}). Ҳар бири олдин текширилади:
    тарихи бор ёки 1С'дан келган бўлса — ўчмайди, белги билан қолади. Танланмаганларга тегилмайди."""
    res = dict(deleted=[], kept=[])
    for key in sorted(pick):
        kind, _, ref = key.partition(":")
        if kind == "d":
            d = con.execute("SELECT id, no, date FROM erp_docs WHERE id=? AND src='pf' AND status='deleted'", (ref,)).fetchone()
            if d:
                con.execute("DELETE FROM erp_lines WHERE doc_id=?", (d["id"],))
                con.execute("DELETE FROM erp_docs WHERE id=?", (d["id"],))
                res["deleted"].append(f"Накладной № {d['no']} ({d['date']})")
            continue
        table, refs, label, hk = (("erp_goods", refs_good, "Товар", "good") if kind == "g" else
                                  ("erp_clients", refs_client, "Мижоз", "client") if kind == "c" else (None,) * 4)
        if not table:
            continue
        r = con.execute(f"SELECT ext, name FROM {table} WHERE ext=? AND deleted=1 AND folder=0", (ref,)).fetchone()
        if not r:
            continue
        n = refs(con, r["ext"])
        if n:
            res["kept"].append((f"{label} «{r['name']}»", f"тарихи бор: {n} та ҳужжат", url_for("erp_history", kind=hk, ext=r["ext"])))
        elif not r["ext"].startswith("P"):
            res["kept"].append((f"{label} «{r['name']}»", "1С'дан келган — белги туради", ""))
        else:
            con.execute(f"DELETE FROM {table} WHERE ext=?", (r["ext"],))
            res["deleted"].append(f"{label} «{r['name']}»")
    log(con, None, "purge", f"ўчди {len(res['deleted'])}: " + "; ".join(res["deleted"])[:400])
    return res


def save_good(con, ext, f):
    """Товар/хизмат қўшиш (ext=None) ёки таҳрирлаш. Хато бўлса ValueError."""
    name = re.sub(r"\s+", " ", (f.get("name") or "")).strip()[:100]
    if len(name) < 2:
        raise ValueError("Номини ёзинг")
    dup = con.execute("SELECT ext FROM erp_goods WHERE folder=0 AND deleted=0 AND lc=? AND ext<>?", (lc(name + " "), ext or "")).fetchone()
    if not dup:
        dup = next((r for r in con.execute("SELECT ext, name FROM erp_goods WHERE folder=0 AND deleted=0 AND ext<>?", (ext or "",))
                    if r["name"].casefold().strip() == name.casefold()), None)
    if dup:
        raise ValueError(f"«{name}» деган товар аллақачон бор")
    parent = (f.get("parent") or "").strip()
    if parent and not con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND folder=1", (parent,)).fetchone():
        raise ValueError("Папка топилмади")
    unit = (f.get("unit") or "").strip()[:5] or "шт"
    cur = 2 if str(f.get("cur")) == "2" else 1
    price = max(0.0, A.parse_float(f.get("price"), 0))
    cat = f.get("cat") if f.get("cat") in ("good", "svc") else None
    if not cat and ext:
        old = con.execute("SELECT COALESCE(cat,'') FROM erp_goods WHERE ext=?", (ext,)).fetchone()
        cat = old[0] if old and old[0] in ("good", "svc") else None
    if not cat:
        raise ValueError("Категорияни танланг: товар ёки услуга")
    if ext:
        con.execute("UPDATE erp_goods SET name=?, parent=?, unit=?, cur=?, price=?, lc=?, cat=?, src='pf', pf_parent='' WHERE ext=?",
                    (name, parent, unit, cur, price, lc(name + " " + (f.get("code") or "")), cat, ext))
        log(con, None, "good_edit", f"{ext}: {name}")
    else:
        ext = _new_ext(con, "erp_goods", "P")
        con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,cat,src,price_last) "
                    "VALUES(?,?,?,?,?,?,0,?,0,0,?,?,'pf',0)", (ext, ext, name, unit, cur, price, parent, lc(name + " " + ext), cat))
        log(con, None, "good_add", f"{ext}: {name}")
    return ext


_ALPHA = "аәбвгғдеёжзийклмнопқрстуўфхҳцчшщъыьэюя"
_AIDX = {ch: i for i, ch in enumerate(_ALPHA)}


def name_key(s):
    """Алифбо бўйича саралаш (1С'дагидек): аввал кирилл (А…Я, Ё — Е дан кейин, Ў, Қ, Ғ, Ҳ ўз жойида), кейин лотин, кейин рақамлар."""
    out = []
    for ch in (s or "").strip().casefold():
        if ch in _AIDX:
            out.append((0, _AIDX[ch]))
        elif "a" <= ch <= "z":
            out.append((1, ord(ch)))
        elif ch.isdigit():
            out.append((2, ord(ch)))
        elif ch.isspace():
            out.append((-1, 0))
        else:
            out.append((3, ord(ch)))
    return out


def notif_row(con, ext):
    """Мижоз хабарномаси созламалари («Клиенты и долги» ёзуви): 1С мижози — onec_code «1c77:<ext>»."""
    return con.execute("SELECT * FROM clients WHERE onec_code=? ORDER BY id LIMIT 1", ("1c77:" + str(ext),)).fetchone()


def ensure_notif(con, c):
    """Мижознинг «Клиенты и долги» карточкаси (1С синхрони биринчи ҳужжатда шуни ўзи яратади) — бўлмаса яратилади."""
    r = notif_row(con, c["ext"])
    if r:
        return r["id"]
    import c1 as _c1
    remind = 1 if A.get_setting("c1_new_remind", "0") == "1" else 0
    cid = con.execute("INSERT INTO clients(name,phone,company,notes,active,sms_enabled,tg_code,is_demo,created_at,onec_code,onec_hash,tg_chat) "
                      "VALUES(?,?,?,?,1,?,?,0,?,?,'',?)",
                      (c["name"][:200], _c1._phone(c["phones"] or ""), (c["full"] or "")[:200], "Из 1С (код " + (c["code"] or c["ext"]) + ")",
                       remind, A.new_code(), A.now(), "1c77:" + str(c["ext"]), (c["tg"] or "")[:200])).lastrowid
    log(con, None, "notif_set", f"{c['ext']} {c['name']}: мижоз карточкаси очилди (#{cid})")
    return cid


def save_notif(con, ext, f):
    """Карточкадаги «Хабарномалар» бўлими: накладная/тўлов хабари, қарз эслатмаси, тил, пауза. Ёзуви бўлмаса — яратилади."""
    if not f.get("nf"):
        return
    c = con.execute("SELECT * FROM erp_clients WHERE ext=?", (ext,)).fetchone()
    if not c or (c["ckind"] or "client") == "supplier":
        return
    doc, rem = 1 if f.get("n_doc") else 0, 1 if f.get("n_rem") else 0
    lang = f.get("n_lang") if f.get("n_lang") in A.LANGS else ""
    pause = (f.get("n_pause") or "").strip()[:10] or None
    tg = (c["tg"] or "").strip()
    import c1 as _c1
    phone = _c1._phone(c["phones"] or "")
    r = notif_row(con, ext)
    if r:
        con.execute("UPDATE clients SET c1_doc=?, sms_enabled=?, lang=?, sms_paused_until=? WHERE id=?", (doc, rem, lang, pause, r["id"]))
        if tg and tg != (r["tg_chat"] or ""):
            con.execute("UPDATE clients SET tg_chat=? WHERE id=?", (tg[:200], r["id"]))
        if not (r["phone"] or "").strip() and phone:
            con.execute("UPDATE clients SET phone=?, tg_status='', tg_user_id=NULL WHERE id=?", (phone, r["id"]))
    elif doc or rem:
        cid = con.execute("INSERT INTO clients(name,phone,company,notes,active,sms_enabled,sms_paused_until,tg_code,is_demo,created_at,onec_code,onec_hash,c1_doc,lang,tg_chat) "
                          "VALUES(?,?,?,?,1,?,?,?,0,?,?,'',?,?,?)",
                          (c["name"][:200], phone, (c["full"] or "")[:200], "Платформа карточкасидан (код " + (c["code"] or ext) + ")",
                           rem, pause, A.new_code(), A.now(), "1c77:" + str(ext), doc, lang, tg[:200])).lastrowid
        log(con, None, "notif_set", f"{ext} {c['name']}: хабарнома ёзуви яратилди (#{cid})")


def save_client(con, ext, f):
    """Мижоз карточкаси (платформа). 1С'га ёзилмайди."""
    def g(k, n):
        return re.sub(r"\s+", " ", (f.get(k) or "")).strip()[:n]
    name = g("name", 100)
    if len(name) < 2:
        raise ValueError("Мижоз исмини ёзинг")
    ph = f.getlist("phone") if hasattr(f, "getlist") and f.getlist("phone") else [f.get("phones") or ""]
    phones = ", ".join(p for p in (re.sub(r"\s+", " ", x).strip() for x in ph) if p)[:150]
    full, contact, address, inn, note = g("full", 200), g("contact", 100), g("address", 200), re.sub(r"\D", "", f.get("inn") or "")[:14], (f.get("cnote") or "").strip()[:500]
    tg = g("tg", 60).replace("https://t.me/", "").replace("t.me/", "")
    if tg and not tg.startswith("@") and not tg.lstrip("+").isdigit():
        tg = "@" + tg
    old_nm = con.execute("SELECT name FROM erp_clients WHERE ext=?", (ext,)).fetchone() if ext else None
    same = old_nm and (old_nm["name"] or "").casefold().strip() == name.casefold()     # исми ўзгармаган (1С'да бир хил исмлилар бор) — текширмаймиз
    dup = None if same else next((r for r in con.execute("SELECT ext, name FROM erp_clients WHERE folder=0 AND deleted=0 AND ext<>?", (ext or "",))
                                  if r["name"].casefold().strip() == name.casefold()), None)
    if dup:
        raise ValueError(f"«{name}» деган мижоз аллақачон бор")
    kind = f.get("ckind") if f.get("ckind") in CKINDS else None
    if A.current_user() and A.current_user()["role"] != "admin":
        kind = None if ext else (kind if kind in ("client", "supplier") else "client")
    key = lc(" ".join((name, full, phones, re.sub(r"\D", "", phones), contact, tg, inn)))
    vals = (name, full, phones, contact, tg, address, inn, note, key)
    # филиал: фақат поставщик учун, асосий фирма ҳам поставщик ва ўзи филиал эмас (бир даража)
    cur = con.execute("SELECT ckind FROM erp_clients WHERE ext=?", (ext,)).fetchone() if ext else None
    rkind = kind or ((cur["ckind"] if cur else None) or "client")
    grp = (f.get("grp") or "").strip()
    if grp:
        gm = con.execute("SELECT ext, COALESCE(grp,'') grp FROM erp_clients WHERE ext=? AND folder=0 AND deleted=0 AND ckind='supplier'", (grp,)).fetchone()
        if not gm or gm["ext"] == ext or gm["grp"]:
            raise ValueError("Асосий фирма нотўғри танланган")
        if ext and con.execute("SELECT 1 FROM erp_clients WHERE grp=? LIMIT 1", (ext,)).fetchone():
            raise ValueError("Бу фирманинг ўз филиаллари бор — уни бошқасига филиал қилиб бўлмайди")
    if rkind != "supplier":
        grp = ""
    if ext:
        con.execute("UPDATE erp_clients SET name=?, full=?, phones=?, contact=?, tg=?, address=?, inn=?, cnote=?, lc=?, src='pf' WHERE ext=?", vals + (ext,))
        if kind:
            con.execute("UPDATE erp_clients SET ckind=?, ckind_set=1, blocked=CASE WHEN ?='client' THEN blocked ELSE 0 END WHERE ext=? AND COALESCE(ckind,'client')<>?",
                        (kind, kind, ext, kind))
        if "grp" in f or rkind != "supplier":
            con.execute("UPDATE erp_clients SET grp=? WHERE ext=?", (grp, ext))
        log(con, None, "client_edit", f"{ext}: {name}")
    else:
        ext = _new_ext(con, "erp_clients", "P")
        u = A.current_user()
        con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,contact,tg,address,inn,cnote,lc,parent,folder,deleted,src,lim,blocked,ckind,ckind_set,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,'',0,0,'pf',?,0,?,?,?,?)",
                    (ext, ext) + vals + (credit_default(), kind or "client", 1 if kind else 0, u["username"] if u else "", datetime.now().strftime("%Y-%m-%d %H:%M")))
        if grp:
            con.execute("UPDATE erp_clients SET grp=? WHERE ext=?", (grp, ext))
        log(con, None, "client_add", f"{ext}: {name}")
    return ext


# ================================================================ Flask
def _journal_debts(con, rows):
    """1С журналидаги «Долг»: проведен ва тўлиқ тўланмаган накладнойда — мижознинг шу накладнойдан кейинги қарзи ($).
    Ҳозирги қарздан кейинги ҳаракатлар (накладнойлар ва тўловлар) айириб топилади."""
    need = {r["client"] for r in rows if r["status"] == "posted" and (r["nal"] if r["nal"] is not None else 1) and r["client"]
            and (r["usd"] or 0) - (r["paid"] or 0) > 0.005}
    if not rows or not need:
        return
    start = min(r["date"] for r in rows)
    after = {}
    for ext in need:
        ev = [(x["date"], x["time"] or "", x["id"], (x["usd"] or 0) - (x["paid"] or 0)) for x in con.execute(
            "SELECT id, date, time, usd, COALESCE(paid,0) paid FROM erp_docs WHERE kind='sale' AND status='posted' AND COALESCE(nal,1)<>0 "
            "AND client=? AND date>=?", (ext, start))]
        ev += [(x["date"], x["time"] or "", x["id"], -(x["debt"] or 0)) for x in con.execute(
            "SELECT d.id, d.date, d.time, SUM(l.debt) debt FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.kind='pay' "
            "AND d.status='posted' AND l.client=? AND d.date>=? GROUP BY d.id", (ext, start))]
        ev.sort()
        run = client_debt(con, ext)
        for e in reversed(ev):
            after[(ext, e[2])] = run
            run -= e[3]
    for r in rows:
        v = after.get((r["client"], r["id"]))
        if r["client"] in need and v is not None and r["status"] == "posted" and (r["usd"] or 0) - (r["paid"] or 0) > 0.005:
            r["debt_after"] = round(v, 2)


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    con.executescript(SCHEMA)
    for t in ("erp_goods", "erp_clients"):
        if "lc" not in {r[1] for r in con.execute(f"PRAGMA table_info({t})")}:
            con.execute(f"ALTER TABLE {t} ADD COLUMN lc TEXT")
    if "price_last" not in {r[1] for r in con.execute("PRAGMA table_info(erp_goods)")}:
        con.execute("ALTER TABLE erp_goods ADD COLUMN price_last REAL DEFAULT 0")
    if "cnotes" not in {r[1] for r in con.execute("PRAGMA table_info(erp_lines)")}:
        con.execute("ALTER TABLE erp_lines ADD COLUMN cnotes TEXT DEFAULT ''")   # катак изоҳлари (JSON: {"usd": "...", "uzs": "..."})
    if "variant" not in {r[1] for r in con.execute("PRAGMA table_info(erp_lines)")}:
        con.execute("ALTER TABLE erp_lines ADD COLUMN variant TEXT DEFAULT ''")  # маҳаллий ЛМДФ: МДФ сорти (Асино, Хитой, Гомель, Алтай …)
    if "cat" not in {r[1] for r in con.execute("PRAGMA table_info(erp_goods)")}:
        con.execute("ALTER TABLE erp_goods ADD COLUMN cat TEXT DEFAULT ''")    # 1С «Категория»: good — товар, svc — услуга ('' — номаълум)
    if "pf_parent" not in {r[1] for r in con.execute("PRAGMA table_info(erp_goods)")}:
        con.execute("ALTER TABLE erp_goods ADD COLUMN pf_parent TEXT DEFAULT ''")    # платформада бошқа папкага ўтказилган (1С ўзгармайди)
    ccols = {r[1] for r in con.execute("PRAGMA table_info(erp_clients)")}
    for col, typ in (("lim", "REAL"), ("blocked", "INTEGER DEFAULT 0"), ("block_note", "TEXT"), ("debt1c", "REAL DEFAULT 0"),
                     ("unblocked_at", "TEXT"), ("ckind", "TEXT DEFAULT 'client'"), ("ckind_set", "INTEGER DEFAULT 0"), ("due_days", "INTEGER"),
                     ("contact", "TEXT DEFAULT ''"), ("tg", "TEXT DEFAULT ''"), ("address", "TEXT DEFAULT ''"), ("inn", "TEXT DEFAULT ''"),
                     ("cnote", "TEXT DEFAULT ''"), ("created_by", "TEXT"), ("created_at", "TEXT"),
                     ("grp", "TEXT DEFAULT ''")):          # поставщик филиали: асосий фирманинг ext'и (масалан, Eman materials → Чинобод, Жомий база)
        if col not in ccols:
            con.execute(f"ALTER TABLE erp_clients ADD COLUMN {col} {typ}")
    cols = {r[1] for r in con.execute("PRAGMA table_info(erp_docs)")}
    for col, typ in (("paid", "REAL DEFAULT 0"), ("ret", "INTEGER DEFAULT 1"), ("due_days", "INTEGER"), ("ref_doc", "INTEGER"),
                     ("minus_by", "TEXT"), ("resp", "TEXT"), ("konstr", "INTEGER"), ("kroychi", "INTEGER"),
                     ("urgent", "INTEGER DEFAULT 0"), ("urgent_free", "INTEGER DEFAULT 0"), ("disc_pct", "REAL DEFAULT 0")):
        if col not in cols:
            con.execute(f"ALTER TABLE erp_docs ADD COLUMN {col} {typ}")
    dcols = {r[1] for r in con.execute("PRAGMA table_info(erp_resp_ded)")}
    for col, typ in (("base_usd", "REAL DEFAULT 0"), ("ref_id", "INTEGER")):
        if col not in dcols:
            con.execute(f"ALTER TABLE erp_resp_ded ADD COLUMN {col} {typ}")
    if not con.execute("SELECT 1 FROM settings WHERE key='erp_due_start'").fetchone():   # муддат фақат шу кундан кейинги накладнойларга
        _cset(con, erp_due_start=date.today().isoformat())
    seed_accounts(con)
    if "c1_ext" not in {r[1] for r in con.execute("PRAGMA table_info(erp_accounts)")}:
        con.execute("ALTER TABLE erp_accounts ADD COLUMN c1_ext TEXT DEFAULT ''")   # 1С'даги шу ҳисобнинг «мижоз»и (Карта …, Поступление в счёт)
    map_c1_accounts(con)
    con.commit(); con.close()

    _orig_tick = A.scheduler_tick
    def scheduler_tick_erp(dt=None):
        res = _orig_tick(dt)
        if A.get_setting("erp_sync", "1") == "1" and c1.base_path():
            sync_due()
        try:
            if time.time() - float(A.get_setting("erp_refund_at", "0") or 0) > 300:      # мижоз тўласа — ушланган пул ишчига қайтади
                A.set_settings(erp_refund_at=str(time.time()))
                resp_refund()
        except Exception:
            A.errlog.exception("resp refund")
        try:                                            # масъулларга эслатма — кунига бир марта (стандарт 10:00 дан кейин)
            now = datetime.now()
            hh = (A.get_setting("erp_resp_hour", "10:00") or "10:00").split(":")
            if (now.hour, now.minute) >= (int(hh[0]), int(hh[1] if len(hh) > 1 else 0)) and A.get_setting("erp_resp_day", "") != now.date().isoformat():
                A.set_settings(erp_resp_day=now.date().isoformat())
                resp_remind()
        except Exception:
            A.errlog.exception("resp remind")
        return res
    A.scheduler_tick = scheduler_tick_erp

    lr = A.login_required

    def journal_url(kind, sel=None):
        """Журналга қайтиш — охирги танланган интервал ва филтр сақланади (ҳужжат санасига ўзгармайди)."""
        sv = (session.get("jr") or {}).get(kind)
        extra = {"sel": sel} if sel else {}
        if sv:
            return url_for("erp_docs", kind=kind, **{k: v for k, v in sv.items() if v}, **extra)
        return url_for("erp_docs", kind=kind, **extra)
    app.jinja_env.globals["journal_url"] = journal_url
    app.jinja_env.globals["mdf_sorts"] = mdf_sorts
    app.jinja_env.globals["erp_test"] = test_mode

    @app.route("/erp/test_mode", methods=["POST"])
    @A.admin_required
    def erp_test_mode():
        on = request.form.get("on") == "1"
        A.set_settings(erp_test_mode="1" if on else "0")
        log(A.db(), None, "test_mode", "ёқилди" if on else "ўчирилди")
        A.db().commit()
        flash("Тестовый режим включён: документы только черновиками" if on else "Тестовый режим выключен: проводить можно", "ok")
        return redirect(request.referrer if (request.referrer or "").startswith(request.host_url) else url_for("erp_docs"))

    @app.route("/erp")
    @lr
    def erp_docs():
        kas = A.is_limited()
        kind = request.args.get("kind", "sale")
        kind = kind if kind in KIND_NAMES else "sale"
        if kas and kind not in kassa_view_kinds():
            vk = kassa_view_kinds()                       # чекланган роль: фақат ўзининг ҳужжат турлари
            if not vk:
                abort(403)
            kind = vk[0]
        today = date.today()
        jr = dict(session.get("jr") or {})
        if "d1" not in request.args and kind in jr:          # 1С'дагидек: журнал охирги танланган интервал билан очилади
            sv = jr[kind]
            return redirect(url_for("erp_docs", kind=kind, **{k: v for k, v in sv.items() if v}, **({"sel": request.args["sel"]} if request.args.get("sel") else {})))
        src = request.args.get("src", "")
        d1 = A.parse_date(request.args.get("d1")) or today          # биринчи марта — бугунги кун
        d2 = A.parse_date(request.args.get("d2")) or today
        if d1 > d2:
            d1, d2 = d2, d1
        qs = (request.args.get("q") or "").strip()
        stf = request.args.get("st", "")
        stf = stf if stf in ("posted", "draft", "deleted", "live") else ""
        jr[kind] = dict(d1=d1.isoformat(), d2=d2.isoformat(), src=src if src in ("1c", "pf") else "", st=stf, q=qs)
        session["jr"] = jr
        sql = ("SELECT d.*, c.name cname FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.kind=? AND d.date BETWEEN ? AND ?")
        args = [kind, d1.isoformat(), d2.isoformat()]
        if src in ("1c", "pf"):
            sql += " AND d.src=?"; args.append(src)
        cnt_sql, cnt_args = sql, list(args)                # ҳолат бўйича сонлар — ҳолат фильтрисиз
        if qs:
            _w, _a = words_sql(qs, "c.lc")
            cnt_sql += " AND (d.no LIKE ? OR (1=1" + _w + "))"; cnt_args += [f"%{qs}%"] + _a
        cnt = {r[0]: r[1] for r in A.q(cnt_sql.replace("SELECT d.*, c.name cname", "SELECT d.status, COUNT(*)", 1) + " GROUP BY d.status", cnt_args)}
        if stf == "live":
            sql += " AND d.status<>'deleted'"
        elif stf:
            sql += " AND d.status=?"; args.append(stf)
        if qs:
            _w, _a = words_sql(qs, "c.lc")
            sql += " AND (d.no LIKE ? OR (1=1" + _w + "))"; args += [f"%{qs}%"] + _a
        rows = [dict(r) for r in A.q(sql + " ORDER BY d.date DESC, d.time DESC, d.id DESC LIMIT 2000", args)]
        rows.reverse()                                     # 1С'дагидек: эскиси тепада, янгиси пастда
        con = A.db()
        if kind == "sale":                                 # «Долг» — мижознинг ҳозирги жами қарзи (1С журналидагидек)
            dc = {}
            for r in rows:
                ext = r["client"]
                if ext and ext not in dc:
                    dc[ext] = client_debt(con, ext)
                r["debt_after"] = dc.get(ext)
        elif kind == "pay":
            ids = [r["id"] for r in rows]
            names = {}
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                for x in con.execute(f"SELECT l.doc_id, c.name, COALESCE(c.ckind,'client') ck FROM erp_lines l LEFT JOIN erp_clients c ON c.ext=l.client "
                                     f"WHERE l.doc_id IN ({','.join('?' * len(part))}) ORDER BY l.doc_id, l.lineno", part):
                    names.setdefault(x[0], []).append((x[1] or "", x[2]))
            for r in rows:
                # 1С'дагидек битта ҳужжатда бир неча мижоз: фақат мижоз қаторлари (карта/ҳисоб қаторлари — алоҳида белги)
                nm = names.get(r["id"]) or [(r["cname"] or "", "client")]
                cl, seen = [], set()
                for n_, k_ in nm:
                    if k_ == "client" and n_ not in seen:
                        seen.add(n_); cl.append(n_)
                acc = sorted({n_ for n_, k_ in nm if k_ != "client" and n_})
                r["cnames"] = cl
                r["caccs"] = acc
                r["cname"] = ", ".join(cl[:3]) + (f" +{len(cl) - 3}" if len(cl) > 3 else "") if cl else ", ".join(acc)
        live = [r for r in rows if r["status"] == "posted" and (r["nal"] != 0 or kind != "sale")]
        tot = dict(usd=sum(r["usd"] or 0 for r in live), uzs=sum(r["uzs"] or 0 for r in live), n=len(rows),
                   posted=cnt.get("posted", 0), draft=cnt.get("draft", 0), deleted=cnt.get("deleted", 0))
        return render_template("erp_docs.html", rows=rows, kind=kind, src=src, d1=d1, d2=d2, q=qs, tot=tot, KIND_NAMES=KIND_NAMES,
                               st=_state, stf=stf, kas=kas, KASSA_KINDS=kassa_edit_kinds(), KVIEW=kassa_view_kinds(),
                               next_no=next_no(con, "sale"), drate=day_rate(), crate=onec_rate(), now_ts=time.time(), lock_sec=LOCK_SEC)

    def _busy(d):
        """1С'дагидек: накладной банд бўлса, бошқалар умуман очолмайди (кўриш, чоп этиш ҳам)."""
        lk = lock_info(d)
        return (render_template("erp_locked.html", d=d, lock=lk), 423) if lk else None

    @app.route("/erp/doc/<int:doc_id>")
    @lr
    def erp_doc(doc_id):
        x = doc_full(A.db(), doc_id)
        if not x or (A.is_limited() and x["d"]["kind"] not in kassa_view_kinds()):
            abort(404)
        b = _busy(x["d"])
        if b:
            return b
        logs = A.q("SELECT * FROM erp_log WHERE doc_id=? ORDER BY id DESC", (doc_id,))
        con = A.db()
        d = x["d"]
        rets = A.q("SELECT id, no, date, status, usd FROM erp_docs WHERE ref_doc=? AND status<>'deleted' ORDER BY id", (doc_id,))
        ref = A.q("SELECT id, no, date FROM erp_docs WHERE id=?", (d["ref_doc"],), one=True) if d["ref_doc"] else None
        short = _doc_short(con, d) if d["kind"] == "sale" and d["status"] == "draft" else []
        can_ret = d["kind"] == "sale" and d["status"] == "posted" and not d["ref_doc"] and any(
            (l["qty"] or 0) > 0 for l in x["lines"])
        ri = resp_info(con, d["resp"]) if d["resp"] else None
        deds = A.q("SELECT r.*, w.name wname FROM erp_resp_ded r LEFT JOIN k_workers w ON w.id=r.kw_id WHERE r.doc_id=? ORDER BY r.id", (doc_id,))
        od = None
        if d["kind"] == "sale" and d["client"] and d["status"] == "posted":
            od = next((o for o in overdue(con, d["client"]) if o["id"] == d["id"]), None)
        wcur = None
        if ri and ri["kw"]:
            wr = con.execute("SELECT cur FROM k_workers WHERE id=?", (ri["kw"],)).fetchone()
            wcur = wr[0] if wr else None
        back = journal_url(d["kind"], d["id"])
        try:                                          # ҳисобот/тарих/мижоздан очилган бўлса — ўша жойга (танланган интервал билан) қайтади
            from urllib.parse import urlparse
            rf = urlparse(request.referrer or "")
            if rf.netloc == request.host and rf.path.startswith(("/erp/rep/", "/erp/history", "/erp/clients", "/erp/suppliers", "/kassa")):
                back = rf.path + ("?" + rf.query if rf.query else "")
        except Exception:
            pass
        kname = {}                                    # кройчи ва конструктор номи (эгаси, 26.10: накладнойда кўриниб турсин)
        if d["kind"] == "sale":
            for f in ("konstr", "kroychi"):
                if d[f]:
                    r_ = con.execute("SELECT name FROM k_workers WHERE id=?", (d[f],)).fetchone()
                    kname[f] = r_[0] if r_ else f"#{d[f]}"
        return render_template("erp_doc.html", kname=kname, x=x, d=d, KIND_NAMES=KIND_NAMES, KASSA_KINDS=kassa_edit_kinds(), back=back, CUR=CUR, logs=logs, lock=lock_info(d), rets=rets, ref=ref,
                               people=resp_people(con), ri=ri, deds=deds, od=od, wcur=wcur, drate0=day_rate()[0],
                               short=short, can_ret=can_ret, ci=credit_info(con, d["client"], d["id"]) if d["kind"] == "sale" and d["client"] else None)

    def _cat_remirror(con):
        """Переоценка каталог товарига тегса — накладной нархлари дарров янгиланади."""
        try:
            import catalog as _C
            _C.mirror(con); con.commit()
        except Exception:
            A.errlog.exception("reval remirror")

    def _after_save(nid, act, kind):
        """1С'дагидек: «Провести» — формада қолади, «Провести и закрыть» — ҳужжатга, «Печать» — проведен қилиб чоп этиш ойнаси."""
        if act == "post_stay":
            return redirect(url_for("erp_sale_edit", doc_id=nid) if kind == "sale" else url_for("erp_doc2_edit", doc_id=nid))
        if act in ("post_print", "save_print"):
            return redirect(url_for("erp_doc_print", doc_id=nid, form="usd") if kind == "sale" else url_for("erp_doc2_print", doc_id=nid))
        return redirect(url_for("erp_doc", doc_id=nid))

    POST_ACTS = ("post", "post_stay", "post_print")
    NOPOST = "Провести — фақат кассирда: кассир пулни олади ёки насияга беришни ҳал қилади. Накладной қоралама сифатида сақланди."

    def _posted_guard(d):
        """Проведен ҳужжатни фақат «провести» ҳуқуқи борлар ўзгартиради (қораламага қайтариб)."""
        if d and d["status"] == "posted" and not A.can("post"):
            flash("Ҳужжат проведен — ўзгартириш учун кассир «Отменить проведение» қилсин (рақами сақланади)", "error")
            return redirect(url_for("erp_doc", doc_id=d["id"]))
        return None

    @app.route("/erp/doc/<int:doc_id>/print2")
    @lr
    def erp_doc2_print(doc_id):
        """Поступление товара / денег / переоценка — чоп этиш (накладной услубида: логотип, фирма, жадвал, имзо)."""
        x = doc_full(A.db(), doc_id)
        if not x or x["d"]["kind"] not in ("receipt", "pay", "reval", "inv"):
            abort(404)
        b = _busy(x["d"])
        if b:
            return b
        d = x["d"]
        dd = A.parse_date(d["date"])
        ui = A.ui_settings()
        f = A.firm_info()
        p = dict(kind=d["kind"], title=KIND_NAMES[d["kind"]], no=d["no"], status=d["status"],
                 date_text=f"{dd.day} {c1.MONTHS_GEN[dd.month]} {dd.year} г." if dd else "", party=d["cname"] or "",
                 phones=d["cphones"] or "", note=(d["note"] or "").strip(), rate=d["rate"] or 0, lines=x["lines"],
                 usd=sum((l["usd"] or 0) for l in x["lines"]), uzs=sum((l["uzs"] or 0) for l in x["lines"]),
                 debt=sum((l["debt"] or 0) for l in x["lines"]) if d["kind"] == "pay" else 0,
                 qty=sum((l["qty"] or 0) for l in x["lines"]), back=url_for("erp_doc", doc_id=d["id"]),
                 logo=f.get("logo") or "", company=f.get("company"), phone=f.get("phone"), address=f.get("address"))
        return render_template("erp_print2.html", p=p, CUR=CUR)

    @app.route("/erp/doc/<int:doc_id>/print")
    @lr
    def erp_doc_print(doc_id):
        x = doc_full(A.db(), doc_id)
        if not x or x["d"]["kind"] != "sale":
            abort(404)
        b = _busy(x["d"])
        if b:
            return b
        d = x["d"]
        form = request.args.get("form", "usd")
        form = {"full": "usd", "bez": "usd"}.get(form, form)
        form = form if form in ("usd", "uzs", "perm") else "usd"
        con = A.db()
        dd = A.parse_date(d["date"])
        rate = d["rate"] or 0
        lines = []
        for l in x["lines"]:
            q = l["qty"] or 0
            pu = (l["price"] if l["gcur"] == 2 else (l["usd"] / q if q else 0)) or 0
            lines.append(dict(no=l["lineno"], name=l["gname"] or "?", unit=l["unit"], qty=q, price_usd=pu, usd=l["usd"] or 0,
                              price_uzs=l["price_uzs"] or 0, uzs=l["uzs"] or 0, note=(l["note"] or "").strip()))
        prev = after = None
        if d["nal"] != 0 and d["status"] == "posted" and d["client"]:
            prev, after = _print_debt(con, d)
        ui = A.ui_settings()
        phones = [p.strip() for p in re.split(r"[,;/\n]+", d["cphones"] or "") if p.strip()]
        p = dict(no=d["no"], date_text=f"{dd.day} {c1.MONTHS_GEN[dd.month]} {dd.year} г." if dd else "", client=d["cname"] or "", phones=phones,
                 urgent=bool(d["urgent"]) if "urgent" in d.keys() else False,
                 note=(d["note"] or "").strip(), lines=lines, qty_total=sum(l["qty"] for l in lines), usd=d["usd"] or 0, uzs=d["uzs"] or 0,
                 paid=d["paid"] or 0, rate=rate, prev=prev, after=after, back=url_for("erp_doc", doc_id=d["id"]),
                 discount=(d["discount"] or 0) if d["src"] == "pf" else 0, disc_pct=(d["disc_pct"] or 0) if "disc_pct" in d.keys() else 0,
                 logo=(url_for("ui_logo") + "?v=" + str(ui.get("ver", "1"))) if ui.get("logo") else "",
                 company=A.get_setting("nakl_company") or "Lumberture", phone=A.get_setting("contact_phone") or "+998 33 409-60-70",
                 address=A.get_setting("nakl_address_ru") or c1.NAKL_ADDR["ru"],
                 qr=print_qr(d["no"], dd))
        return render_template("erp_print.html", p=p, form=form)

    def _print_debt(con, d):
        """Қарз накладнойдан олдин ва кейин ($). 1С ҳужжати — 1С ҳаракатларидан; платформа ҳужжати — ҳозирги қарздан."""
        if d["src"] == "1c":
            try:
                mv = c1.client_moves(d["client"])
                idx = [i for i, m in enumerate(mv) if m["iddoc"] == d["ext"]]
                if idx:
                    return (-mv[idx[0] - 1]["balance"] if idx[0] else 0.0), -mv[idx[-1]]["balance"]
            except Exception:
                A.errlog.exception("print debt")
            return None, None
        prev = client_debt(con, d["client"], exclude_doc=d["id"])
        if d["status"] == "posted":                      # кейин проведен қилинган платформа ҳужжатлари (эски накладнойни қайта чоп этишда)
            key = (d["date"] or "", d["time"] or "", d["id"])
            for r in con.execute("SELECT id, date, time, kind, usd, COALESCE(paid,0) paid FROM erp_docs WHERE src='pf' AND kind='sale' "
                                 "AND status='posted' AND COALESCE(nal,1)=1 AND client=? AND id<>?", (d["client"], d["id"])):
                if (r["date"] or "", r["time"] or "", r["id"]) > key:
                    prev -= (r["usd"] or 0) - (r["paid"] or 0)
            for r in con.execute("SELECT x.id, x.date, x.time, SUM(l.debt) debt FROM erp_lines l JOIN erp_docs x ON x.id=l.doc_id WHERE x.src='pf' "
                                 "AND x.kind='pay' AND x.status='posted' AND l.client=? GROUP BY x.id", (d["client"],)):
                if (r["date"] or "", r["time"] or "", r["id"]) > key:
                    prev += r["debt"] or 0
            prev = round(prev, 2)
        return prev, round(prev + (d["usd"] or 0) - (d["paid"] or 0), 2)

    def _form_from_request():
        f = request.form
        goods, qtys, prices, notes, vars_ = f.getlist("good"), f.getlist("qty"), f.getlist("price"), f.getlist("lnote"), f.getlist("lvar")
        lines = [dict(good=g, qty=qtys[i] if i < len(qtys) else "", price=prices[i] if i < len(prices) else "",
                      note=notes[i] if i < len(notes) else "", var=vars_[i] if i < len(vars_) else "") for i, g in enumerate(goods)]
        return dict(client=f.get("client"), rate=f.get("rate"), nal="1" if f.get("nal") in ("1", "on") else "0", note=f.get("note"), discount=f.get("discount"),
                    disc_pct=f.get("disc_pct") or "",
                    minus_ok=f.get("minus_ok"), ref_doc=f.get("ref_doc") or "", resp=f.get("resp") or "",
                    konstr=f.get("konstr") or "", kroychi=f.get("kroychi") or "",
                    urgent="1" if f.get("urgent") in ("1", "on") else "0", urgent_free="1" if f.get("urgent_free") in ("1", "on") else "0",
                    pay_usd=f.get("pay_usd"), pay_uzs=f.get("pay_uzs"), date=f.get("date"),
                    pm_method=f.getlist("pm_method"), pm_amount=f.getlist("pm_amount"), pm_cur=f.getlist("pm_cur"),
                    version=f.get("version"), lines=lines)

    def _keep_pays(con, d, form):
        """Тўлов (нақд $/сўм, карта, терминал…) — фақат проведен қила оладиган (кассир). Қолганлар учун — ҳужжатдаги эски тўловлар."""
        if d:
            nc = con.execute("SELECT p.* FROM erp_pays p JOIN erp_accounts a ON a.id=p.account_id WHERE p.doc_id=? AND a.kind<>'cash' "
                             "ORDER BY p.id", (d["id"],)).fetchall()
            form.update(pay_usd=A.num(d["pay_usd"]) if d["pay_usd"] else "", pay_uzs=A.num(d["pay_uzs"]) if d["pay_uzs"] else "",
                        pm_method=[str(r["method_id"]) for r in nc], pm_amount=[A.num(r["amount"]) for r in nc], pm_cur=[r["cur"] for r in nc])
        else:
            form.update(pay_usd="", pay_uzs="", pm_method=[], pm_amount=[], pm_cur=[])

    @app.route("/erp/sale/new", methods=["GET", "POST"])
    @app.route("/erp/doc/<int:doc_id>/edit", methods=["GET", "POST"])
    @lr
    def erp_sale_edit(doc_id=None):
        con = A.db()
        d = None
        if doc_id:
            d = con.execute("SELECT * FROM erp_docs WHERE id=?", (doc_id,)).fetchone()
            if not d:
                abort(404)
            if d["src"] != "pf" or d["kind"] != "sale" or d["status"] == "deleted":
                flash("Бу ҳужжатни платформада ўзгартириб бўлмайди", "error")
                return redirect(url_for("erp_doc", doc_id=doc_id))
            if A.is_closed(d["date"]):
                flash(A.closed_text(d["date"]), "error")
                return redirect(url_for("erp_doc", doc_id=doc_id))
            b = _busy(d) or _posted_guard(d)
            if b:
                return b
        if request.method == "POST":
            form = _form_from_request()
            if not A.can("post"):                          # тўловлар фақат кассирда: бошқалар ёзган тўлов қабул қилинмайди, борини сақлаймиз
                _keep_pays(con, d, form)
            if not A.can("nal"):                           # «Суммани ҳисобга олиш» — фақат админ рухсат берганга
                form["nal"] = str(d["nal"] if d and d["nal"] is not None else 1)
            try:
                act = request.form.get("act") or "save"
                want = act in POST_ACTS
                nomsg = NOPOST
                if want and test_mode():                        # тест режими: фақат черновик
                    act, nomsg = ("save_print" if act == "post_print" else "save"), TEST_NOPOST
                if want and not A.can("post"):
                    act = "save_print" if act == "post_print" else "save"
                save_sale.short = []
                nid = save_sale(con, doc_id, form, post=act in POST_ACTS)
                con.commit()
                flash(nomsg if want and act not in POST_ACTS else "Сақланди", "warn" if want and act not in POST_ACTS else "ok")
                if act in POST_ACTS and save_sale.short:
                    flash(MINUS_WARN + "; ".join(f"{x['name']} ({A.num(x['stock'])} → {A.num(x['stock'] - x['qty'])})"
                                                 for x in save_sale.short[:12]), "warn")
                return _after_save(nid, act, "sale")
            except ValueError as e:
                con.rollback()
                flash(str(e), "error")
                return render_template("erp_edit.html", d=d, form=form, lines=_lines_view(con, form["lines"], doc_id), lock=None,
                                       cname=_cname(con, form["client"]), drate=day_rate(form.get("date") or None), pmethods=_pm(con),
                                       people=resp_people(con), staff=staff_lists(con), urg=urgent_map(con), next_no=None if d else next_no(con, "sale"))
        lock = None
        if d:
            lock = lock_info(d)
            if not lock:
                con.execute("UPDATE erp_docs SET lock_by=?, lock_at=? WHERE id=?", (_user(), time.time(), doc_id)); con.commit()
            lines = [dict(good=l["good"], qty=A.num(l["qty"]), price=A.num(l["price2"] or l["price"]), note=l["note"], var=l["variant"] or "") for l in
                     con.execute("SELECT * FROM erp_lines WHERE doc_id=? ORDER BY lineno", (doc_id,))]     # скидкали қатор — асл нарх (price2)
            form = dict(client=d["client"], rate=A.num(d["rate"]), nal=str(d["nal"]), note=d["note"], discount=d["discount"], resp=d["resp"] or "",
                        disc_pct=A.num(d["disc_pct"]) if d["disc_pct"] else "",
                        konstr=str(d["konstr"] or ""), kroychi=str(d["kroychi"] or ""),
                        urgent=str(d["urgent"] or 0), urgent_free=str(d["urgent_free"] or 0),
                        pay_usd=A.num(d["pay_usd"]) if d["pay_usd"] else "", pay_uzs=A.num(d["pay_uzs"]) if d["pay_uzs"] else "", date=d["date"],
                        version=d["updated_at"] or "", lines=lines)
            nc = [r for r in con.execute("SELECT p.* FROM erp_pays p JOIN erp_accounts a ON a.id=p.account_id WHERE p.doc_id=? AND a.kind<>'cash' "
                                         "ORDER BY p.id", (doc_id,))]
            form.update(pm_method=[str(r["method_id"]) for r in nc], pm_amount=[A.num(r["amount"]) for r in nc], pm_cur=[r["cur"] for r in nc])
        elif request.args.get("copy") or request.args.get("ret"):
            form = _prefill(con, request.args.get("copy", type=int) or request.args.get("ret", type=int), ret=bool(request.args.get("ret")))
            if not form:
                flash("Асл накладной топилмади" if request.args.get("copy") else "Возврат фақат проведен қилинган накладнойга бўлади", "error")
                return redirect(url_for("erp_docs"))
        else:
            form = dict(client=request.args.get("client", ""), rate=A.num(day_rate()[0]) if day_rate()[0] else "", nal="1", note="", discount=0, version="", lines=[],
                        pay_usd="", pay_uzs="", date=date.today().isoformat())
        if not d and not A.can("nal"):
            form["nal"] = "1"
        return render_template("erp_edit.html", d=d, form=form, lines=_lines_view(con, form["lines"], doc_id), lock=lock,
                               cname=_cname(con, form["client"]), drate=day_rate(form.get("date") or None), pmethods=_pm(con),
                               people=resp_people(con), staff=staff_lists(con), urg=urgent_map(con), next_no=None if d else next_no(con, "sale"))

    @app.route("/erp/<kind>/new2", methods=["GET", "POST"])
    @app.route("/erp/doc/<int:doc_id>/edit2", methods=["GET", "POST"])
    @lr
    def erp_doc2_edit(kind=None, doc_id=None):
        """Поступление товара / Поступление денег / Переоценка — платформада ёзиш (1С'дагидек ойна)."""
        con = A.db()
        d = None
        if doc_id:
            d = con.execute("SELECT * FROM erp_docs WHERE id=?", (doc_id,)).fetchone()
            if not d:
                abort(404)
            kind = d["kind"]
            if d["src"] != "pf" or kind not in DOC2 or d["status"] == "deleted":
                flash("Бу ҳужжатни платформада ўзгартириб бўлмайди", "error")
                return redirect(url_for("erp_doc", doc_id=doc_id))
            if A.is_closed(d["date"]):
                flash(A.closed_text(d["date"]), "error")
                return redirect(url_for("erp_doc", doc_id=doc_id))
            b = _busy(d) or _posted_guard(d)
            if b:
                return b
        if kind not in DOC2 or (A.is_limited() and kind not in kassa_edit_kinds()):
            abort(404)
        if request.method == "POST":
            f = request.form
            try:
                lines = json.loads(f.get("lines_json") or "[]")
            except ValueError:
                lines = []
            form = dict(date=f.get("date"), client=f.get("client"), rate=f.get("rate"), note=f.get("note"), version=f.get("version"), lines=lines)
            try:
                act = f.get("act") or "save"
                want = act in POST_ACTS
                if want and test_mode():
                    act = "save_print" if act == "post_print" else "save"
                    flash(TEST_NOPOST, "warn")
                elif want and not A.can("post"):
                    act = "save_print" if act == "post_print" else "save"
                    flash(NOPOST.replace("Накладной", "Ҳужжат"), "warn")
                nid = save_doc2(con, kind, doc_id, form, post=act in POST_ACTS)
                con.commit()
                if kind == "reval":
                    _cat_remirror(con)
                flash("Сақланди", "ok")
                return _after_save(nid, act, kind)
            except ValueError as e:
                con.rollback()
                flash(str(e), "error")
                return render_template("erp_edit2.html", **_doc2_ctx(con, kind, d, form))
        if d:
            if not lock_info(d):
                con.execute("UPDATE erp_docs SET lock_by=?, lock_at=? WHERE id=?", (_user(), time.time(), doc_id)); con.commit()
            form = _doc2_form(con, d)
        elif request.args.get("copy"):
            sd = con.execute("SELECT * FROM erp_docs WHERE id=? AND kind=? AND status<>'deleted'", (request.args.get("copy", type=int), kind)).fetchone()
            if not sd:
                flash("Асл ҳужжат топилмади", "error"); return redirect(journal_url(kind))
            form = _doc2_form(con, sd)
            form.update(date=date.today().isoformat(), version="", rate=A.num(day_rate()[0]) if day_rate()[0] else "")
        else:
            form = dict(date=date.today().isoformat(), client=request.args.get("client", ""), rate=A.num(day_rate()[0]) if day_rate()[0] else "",
                        note="", version="", lines=[])
        return render_template("erp_edit2.html", **_doc2_ctx(con, kind, d, form))

    def _doc2_form(con, d):
        lines = []
        for l in con.execute("SELECT * FROM erp_lines WHERE doc_id=? ORDER BY lineno", (d["id"],)):
            try:
                cn = json.loads(l["cnotes"] or "{}") if "cnotes" in l.keys() else {}
            except ValueError:
                cn = {}
            lines.append(dict(good=l["good"] or "", client=l["client"] or "", qty=A.num(l["qty"]) if l["qty"] else "",
                              price=A.num(l["price"]) if l["price"] else "", price2=A.num(l["price2"]) if l["price2"] else "",
                              usd=A.num(l["usd"]) if l["usd"] else "", uzs=A.num(l["uzs"]) if l["uzs"] else "", note=l["note"] or "", cnotes=cn,
                              **({"fact": A.num(l["price"] or 0)} if d["kind"] == "inv" else {})))
        return dict(date=d["date"], client=d["client"] or "", rate=A.num(d["rate"]) if d["rate"] else "", note=d["note"] or "",
                    version=d["updated_at"] or "", lines=lines)

    def _doc2_ctx(con, kind, d, form):
        st = stock_all(con, d["id"] if d else None)
        rp = reval_prices(con)
        view = []
        for l in form.get("lines") or []:
            g = con.execute("SELECT name, cur, unit, price, price_last FROM erp_goods WHERE ext=?", (l.get("good") or "",)).fetchone()
            c = con.execute("SELECT name FROM erp_clients WHERE ext=?", (l.get("client") or "",)).fetchone()
            view.append(dict(l, gname=g["name"] if g else "", cur=g["cur"] if g else 0, unit=g["unit"] if g else "",
                             stock=st.get(l.get("good") or "", 0) if g else None, cname=c["name"] if c else "",
                             cur_price=(rp.get(l.get("good")) or g["price"] or g["price_last"] or 0) if g else 0,
                             debt=client_debt(con, l["client"]) if c else None))
        return dict(kind=kind, title=DOC2[kind], d=d, form=form, lines=view, cname=_cname(con, form.get("client")), drate=day_rate(form.get("date") or None),
                    kassa=_kassa_now(), next_no=None if d else next_no(con, kind))

    def _kassa_now():
        """Касса қолдиғи (бугун, кун охирига): [сўм, $] — 1С'дагидек тепада кўрсатиш учун."""
        try:
            import kassa as K
            t = date.today()
            mc, o = K.day_view(f"{t.year}-{t.month:02d}", t.day)
            return o.get("end")
        except Exception:
            return None

    def _prefill(con, src_id, ret=False):
        """«Нусха олиш» (F9) — шу товарлар билан янги накладной (бугунги сана ва курс); «Возврат» — минус сонлар, асл курс."""
        sd = con.execute("SELECT * FROM erp_docs WHERE id=? AND kind='sale' AND status<>'deleted'", (src_id,)).fetchone()
        if not sd or (ret and (sd["status"] != "posted" or sd["ref_doc"])):
            return None
        back = returned_qty(con, sd["id"]) if ret else {}
        lines = []
        for l in con.execute("SELECT * FROM erp_lines WHERE doc_id=? ORDER BY lineno", (src_id,)):
            q = l["qty"] or 0
            if ret:
                take = min(q, back.get(l["good"], 0))
                back[l["good"]] = back.get(l["good"], 0) - take
                q = round(q - take, 3)
                if q <= 0:
                    continue
                q = -q
            lines.append(dict(good=l["good"], qty=A.num(q), price=A.num(l["price"] if ret else (l["price2"] or l["price"])), note=l["note"] or "",
                              var=l["variant"] or ""))             # возврат — скидкали нархда; нусха — асл нарх (скидка % билан)
        if ret and not lines:
            return None
        dd = date.today()
        if ret and not A.is_closed(sd["date"]):
            dd = A.parse_date(sd["date"]) or dd
        rt = sd["rate"] if ret else day_rate()[0]
        sdd = A.parse_date(sd["date"])
        note = (f"Возврат по накладной № {sd['no']} от {sdd:%d.%m.%Y}" if ret else (sd["note"] or ""))
        return dict(client=sd["client"], rate=A.num(rt) if rt else "", nal=str(sd["nal"] if sd["nal"] is not None else 1), note=note, discount=0,
                    disc_pct="" if ret else (A.num(sd["disc_pct"]) if "disc_pct" in sd.keys() and sd["disc_pct"] else ""),
                    urgent=str(sd["urgent"] or 0) if "urgent" in sd.keys() else "0", urgent_free=str(sd["urgent_free"] or 0) if "urgent_free" in sd.keys() else "0",
                    version="", lines=lines, pay_usd="", pay_uzs="", date=dd.isoformat(), ref_doc=str(sd["id"]) if ret else "",
                    ref_no=sd["no"] if ret else "")

    def _pm(con):
        return [dict(id=m["id"], name=m["name"], acc=m["aname"], pct=fee_pct(con, m["id"])) for m in methods(con) if m["akind"] != "cash"]

    @app.route("/erp/api/reval_segment")
    @lr
    def erp_api_reval_segment():
        """Переоценка: сегмент (каталог папкаси, ичидагилари билан) + материал + қалинлик бўйича битта нарх.
        Д/С (икки томонли) — шу нарх, О/С (бир томонли, «1/2») — $2 арзон. -> [dict(good, gname, unit, price2, price, os)]"""
        if A.is_limited() and "reval" not in kassa_edit_kinds():
            abort(403)
        con = A.db()
        folder = (request.args.get("folder") or "").strip()
        mat = (request.args.get("mat") or "").strip().upper()
        th = (request.args.get("thick") or "").strip().replace(",", ".")
        price = A.parse_float(request.args.get("price"), 0)
        diff = A.parse_float(request.args.get("diff"), 2)
        if not folder or price <= 0:
            return jsonify(error="Папка ва нархни танланг"), 400
        kids = defaultdict(list)
        for r in con.execute("SELECT ext, parent FROM erp_goods WHERE src='cat' AND folder=1 AND deleted=0"):
            kids[r["parent"] or ""].append(r["ext"])
        keep, stk = set(), [folder]
        while stk:
            x = stk.pop()
            if x not in keep:
                keep.add(x); stk.extend(kids.get(x, []))
        out = []
        for r in con.execute("SELECT ext, name, unit, cur, price, parent FROM erp_goods WHERE src='cat' AND folder=0 AND deleted=0 ORDER BY name"):
            if r["parent"] not in keep or is_service(con, r["ext"]):
                continue
            nm = r["name"] or ""
            if mat and not re.search(r"(?<![А-ЯA-Z])" + re.escape(mat) + r"(?![А-ЯA-Z])", nm.upper()):
                continue
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:мм|mm)", nm, re.I)
            t = m.group(1).replace(",", ".").lstrip("0") if m else ("16" if re.search(r"ЛДСП|ЛМДФ", nm.upper()) else "")
            if th and (t or "") != th.lstrip("0"):
                continue
            os_ = bool(re.search(r"о\s*/\s*с|o\s*/\s*c|(?<![\d/])1\s*/\s*2(?![\d/])|односторон", nm, re.I))
            out.append(dict(good=r["ext"], gname=nm, unit=r["unit"] or "", price2=r["price"] or 0,
                            price=round(price - diff, 4) if os_ else price, os=os_))
        return jsonify(out)

    @app.route("/erp/api/inv_fill")
    @lr
    def erp_api_inv_fill():
        """Инвентаризация: «Заполнить остатками» — қолдиғи бор каталог товарлари (папка бўйича ҳам)."""
        if A.is_limited() and "inv" not in kassa_edit_kinds():
            abort(403)
        return jsonify(inv_fill(A.db(), (request.args.get("folder") or "").strip() or None))

    @app.route("/erp/api/rate")
    @lr
    def erp_api_rate():
        r = day_rate(A.parse_date(request.args.get("d")) or date.today())
        return jsonify(rate=r[0], src=r[1], at=r[2])

    def _cname(con, ext):
        r = con.execute("SELECT name FROM erp_clients WHERE ext=?", (ext or "",)).fetchone()
        return r[0] if r else ""

    def _lines_view(con, lines, doc_id=None):
        out = []
        st = stock_all(con, doc_id)
        for l in lines:
            g = con.execute("SELECT name, cur, unit, COALESCE(msort,0) msort, " + ("COALESCE(disc,0)" if _has_disc(con) else "0") + " disc "
                            "FROM erp_goods WHERE ext=?", (l.get("good") or "",)).fetchone()
            cat_ = (l.get("good") or "").startswith("ci")
            sv = is_service(con, l.get("good") or "")
            out.append(dict(l, gname=g["name"] if g else "", cur=g["cur"] if g else 0, unit=g["unit"] if g else "",
                            stock=None if sv or (cat_ and (l.get("good") or "") not in st) else st.get(l.get("good") or "", 0),
                            svc=sv, msort=bool(g and g["msort"]), disc=bool(g and g["disc"])))
        return out

    @app.route("/erp/doc/<int:doc_id>/lock", methods=["POST"])
    @lr
    def erp_doc_lock(doc_id):
        con = A.db()
        d = con.execute("SELECT * FROM erp_docs WHERE id=?", (doc_id,)).fetchone()
        if not d:
            abort(404)
        lk = lock_info(d)
        if lk:
            return jsonify(ok=False, by=lk[0])
        if request.form.get("release") == "1":
            con.execute("UPDATE erp_docs SET lock_by=NULL, lock_at=NULL WHERE id=? AND lock_by=?", (doc_id, _user()))
        else:
            con.execute("UPDATE erp_docs SET lock_by=?, lock_at=? WHERE id=?", (_user(), time.time(), doc_id))
        con.commit()
        return jsonify(ok=True)

    @app.route("/erp/doc/<int:doc_id>/status", methods=["POST"])
    @lr
    def erp_doc_status(doc_id):
        con = A.db()
        d = con.execute("SELECT * FROM erp_docs WHERE id=?", (doc_id,)).fetchone()
        if not d or d["src"] != "pf":
            abort(404)
        act = request.values.get("act")
        lk = lock_info(d)
        if act in ("delete", "restore") and not A.can("super"):
            log(con, doc_id, "denied", act); con.commit()
            flash(DENY, "error")
        elif A.is_closed(d["date"]):
            flash(A.closed_text(d["date"]), "error")
        elif lk:
            flash(f"{lk[0]} ҳозир шу ҳужжатни таҳрирлаяпти", "error")
        elif act in ("post", "unpost") and not A.can("post"):
            log(con, doc_id, "denied", act); con.commit()
            flash("Провести / отменить проведение — фақат кассирда", "error")
        elif act == "post" and d["kind"] == "sale" and (d["nal"] if d["nal"] is not None else 1) and _credit_fail(con, d):
            flash(_credit_fail(con, d), "error")
        elif act in ("post", "unpost", "delete", "restore"):
            new = {"post": "posted", "unpost": "draft", "delete": "deleted", "restore": "draft"}[act]
            sh = _doc_short(con, d) if act == "post" else []
            con.execute("UPDATE erp_docs SET status=?, updated_by=?, updated_at=?, minus_by=? WHERE id=?",
                        (new, _user(), A.now(), _user() if sh else (d["minus_by"] if act != "post" else None), doc_id))
            if d["kind"] == "reval":
                con.commit(); _cat_remirror(con)
            if act == "post" and d["kind"] == "inv":
                inv_rebook(con, doc_id)                 # инвентаризация: учёт қолдиғи проведение пайтидаги ҳолатга янгиланади
            if d["kind"] in ("sale", "receipt", "inv"):
                _kusok_sync(con, doc_id)                # кусок: платформа ↔ бот омбори (сотилди / омборга / қайтди)
            log(con, doc_id, act)
            if sh:
                log(con, doc_id, "minus_ok", "; ".join(f"{x['name']}: {A.num(x['stock'])} → {A.num(x['stock'] - x['qty'])}" for x in sh))
                flash(MINUS_WARN + "; ".join(f"{x['name']} ({A.num(x['stock'])} → {A.num(x['stock'] - x['qty'])})" for x in sh[:12]), "warn")
            con.commit()
            flash("Бажарилди", "ok")
        return redirect(url_for("erp_doc", doc_id=doc_id))

    @app.route("/erp/doc/<int:doc_id>/resp", methods=["POST"])
    @lr
    def erp_doc_resp(doc_id):
        """Масъул шахсни белгилаш (1С накладнойига ҳам — платформада сақланади) ва админ: ойликдан ушлаш / бекор қилиш."""
        con = A.db()
        d = con.execute("SELECT * FROM erp_docs WHERE id=? AND kind='sale'", (doc_id,)).fetchone()
        if not d:
            abort(404)
        act = request.form.get("act", "set")
        if act == "set":
            key = (request.form.get("resp") or "").strip() or None
            if key and not any(p["key"] == key for p in resp_people(con)):
                flash("Масъул шахс рўйхатдан танланмаган", "error")
            elif d["resp"] and d["resp"] != key and not A.can("super"):
                log(con, doc_id, "denied", "масъулни ўзгартириш"); con.commit(); flash(DENY, "error")
            else:
                old = resp_info(con, d["resp"]) if d["resp"] else None
                new = resp_info(con, key) if key else None
                con.execute("UPDATE erp_docs SET resp=? WHERE id=?", (key, doc_id))
                log(con, doc_id, "resp", f"{old['name'] if old else '—'} → {new['name'] if new else '—'}")
                con.commit(); flash("Сақланди", "ok")
        elif act == "ded" and A.is_admin():
            ri = resp_info(con, d["resp"]) if d["resp"] else None
            amt = A.parse_float(request.form.get("amount"), 0)
            if not ri or not ri["kw"]:
                flash("Масъулнинг касса ойлик рўйхатида ёзуви йўқ — ушлаб бўлмайди", "error")
            elif amt <= 0:
                flash("Суммани ёзинг", "error")
            else:
                w = con.execute("SELECT cur FROM k_workers WHERE id=?", (ri["kw"],)).fetchone()
                cur = (w[0] if w else "UZS") or "UZS"
                rate = day_rate()[0] or d["rate"] or 1
                ym = (A.parse_date(request.form.get("ym") + "-01") if request.form.get("ym") else None) or date.today()
                base = doc_unpaid(con, d["client"]).get(doc_id, 0.0)
                con.execute("INSERT INTO erp_resp_ded(doc_id,kw_id,ym,amount,cur,usd,note,by,at,base_usd) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (doc_id, ri["kw"], ym.isoformat()[:7], round(amt, 2), cur, round(amt if cur == "USD" else amt / rate, 2),
                             (request.form.get("note") or "").strip()[:200], _user(), A.now(), base))
                log(con, doc_id, "resp_ded", f"{ri['name']}: ойликдан {A.num(amt)} {'$' if cur == 'USD' else 'сўм'} ({ym:%m.%Y})")
                con.commit(); _kmemo(); flash("Ойликдан ушланди", "ok")
        elif act == "ded_del" and A.is_admin():
            con.execute("UPDATE erp_resp_ded SET deleted=1 WHERE (id=? OR ref_id=?) AND doc_id=?",
                        (request.form.get("id", type=int), request.form.get("id", type=int), doc_id))
            log(con, doc_id, "resp_ded_del", request.form.get("id", ""))
            con.commit(); _kmemo(); flash("Бекор қилинди", "ok")
        else:
            log(con, doc_id, "denied", act); con.commit(); flash(DENY, "error")
        return redirect(url_for("erp_doc", doc_id=doc_id))

    def _doc_short(con, d):
        ls = [dict(good=l["good"], qty=l["qty"] or 0) for l in con.execute("SELECT good, qty FROM erp_lines WHERE doc_id=?", (d["id"],))]
        return shortages(con, ls, d["id"])

    @app.route("/erp/api/goods")
    @lr
    def erp_api_goods():
        qs = (request.args.get("q") or "").strip()
        if len(qs) < 2:
            return jsonify([])
        sql = ("SELECT ext, code, name, unit, cur, price, price_last, price_in, parent, src, cstock, eman_sum, COALESCE(msort,0) msort, "
               + ("COALESCE(disc,0)" if _has_disc(A.db()) else "0") + " disc FROM erp_goods WHERE folder=0 AND deleted=0" + gscope())
        args = []
        _w, _a = words_sql(qs)
        sql += _w; args += _a
        rows = A.q(sql + " ORDER BY name LIMIT 40", args)
        con = A.db()
        fp = folder_paths(con)
        st = stock_all(con, request.args.get("doc", type=int))
        rp = reval_prices(con)
        return jsonify([dict(ext=r["ext"], name=r["name"], unit=r["unit"], cur=r["cur"],
                             price=(r["price"] or 0) if r["src"] == "cat" else (rp.get(r["ext"]) or r["price"] or r["price_last"] or 0),
                             price_in=r["price_in"] or 0,
                             last=r["src"] != "cat" and not r["price"] and bool(r["price_last"]), folder=fp.get(r["parent"], ""),
                             stock=None if (is_service(con, r["ext"]) or (r["src"] == "cat" and r["ext"] not in st)) else st.get(r["ext"], 0), svc=is_service(con, r["ext"]),
                             eman=r["cstock"] if r["src"] == "cat" and not is_service(con, r["ext"]) else None, msort=bool(r["msort"]),
                             disc=bool(r["disc"]))
                        for r in rows])

    @app.route("/erp/api/clients")
    @lr
    def erp_api_clients():
        qs = (request.args.get("q") or "").strip()
        if len(qs) < 2 and request.args.get("recent") == "1":      # F4 / бўш майдонга босилса — охирги мижозлар
            rows = A.q("SELECT c.ext, c.name, c.phones FROM erp_clients c JOIN (SELECT client, MAX(date || ' ' || IFNULL(time,'')) m FROM erp_docs "
                       "WHERE kind='sale' AND status!='deleted' GROUP BY client) d ON d.client=c.ext WHERE c.folder=0 AND c.deleted=0 "
                       "ORDER BY d.m DESC LIMIT 30")
            return jsonify([dict(ext=r["ext"], name=r["name"], phones=r["phones"]) for r in rows])
        if len(qs) < 2:
            return jsonify([])
        sql, args = "SELECT ext, name, phones FROM erp_clients WHERE folder=0 AND deleted=0", []
        if request.args.get("kind") == "supplier":             # приходда — фақат поставщиклар
            sql += " AND ckind='supplier'"
        _w, _a = words_sql(qs)
        sql += _w; args += _a
        rows = A.q(sql + " ORDER BY name LIMIT 30", args)
        con = A.db()
        return jsonify([dict(ext=r["ext"], name=r["name"], phones=r["phones"], **credit_info(con, r["ext"])) for r in rows])

    @app.route("/erp/api/credit/<ext>")
    @lr
    def erp_api_credit(ext):
        return jsonify(credit_info(A.db(), ext, request.args.get("doc", type=int)))

    @app.after_request
    def _pick_frameable(resp):
        """Товар танлаш ойнаси (pick=1) шу платформа саҳифаси ичида (iframe) очилади — фақат ўз сайтимизда."""
        try:
            if request.endpoint in ("erp_goods", "erp_clients", "erp_suppliers") and request.args.get("pick") == "1":
                import security as _sec
                resp.headers["X-Frame-Options"] = "SAMEORIGIN"
                resp.headers["Content-Security-Policy"] = _sec.CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        except Exception:
            pass
        return resp

    @app.route("/erp/goods")
    @lr
    def erp_goods():
        kas = A.is_limited()
        if request.args.get("old") != "1" or kas:
            pick = request.args.get("pick") == "1"
            u = A.current_user()
            src = request.args.get("src") or ("1c" if (not pick and u["role"] == "admin") else "cat")
            f0, gsel = request.args.get("f", ""), (request.args.get("g") or "").strip()
            if gsel and not f0:                        # танланган товар — ўз папкасида очилади
                r0 = A.q("SELECT parent FROM erp_goods WHERE ext=? AND folder=0", (gsel,), one=True)
                f0 = (r0["parent"] or "") if r0 else ""
                if f0 in ("0",):
                    f0 = ""
            return render_template("erp_goods_tree.html", pick=pick, q=(request.args.get("q") or "").strip(),
                                   f=f0, gsel=gsel, st=_state, SRC=src,
                                   n=A.scalar("SELECT COUNT(*) FROM erp_goods WHERE folder=0 AND deleted=0" + gscope(src)))
        qs = (request.args.get("q") or "").strip()
        sql = "SELECT * FROM erp_goods WHERE folder=0 AND deleted=0"
        args = []
        _w, _a = words_sql(qs)
        sql += _w; args += _a
        rows = A.q(sql + " ORDER BY name LIMIT 300", args)
        return render_template("erp_goods.html", rows=rows, q=qs, fp=folder_paths(A.db()), CUR=CUR,
                               n=A.scalar("SELECT COUNT(*) FROM erp_goods WHERE folder=0 AND deleted=0"),
                               nc=A.scalar("SELECT COUNT(*) FROM erp_clients WHERE folder=0 AND deleted=0"), st=_state)

    # ---------------------------------------------------------------- товарлар ойнаси (1С'дагидек дарахт)
    def _root(x):
        return "" if x in (None, "", "0") else x

    @app.route("/erp/api/gtree")
    @lr
    def erp_api_gtree():
        """Барча папкалар: [{ext, name, parent, n}] — n: ичидаги товарлар сони (бевосита)."""
        con = A.db()
        sc = gscope()
        cnt = dict(con.execute(f"SELECT {EFF_PARENT}, COUNT(*) FROM erp_goods WHERE folder=0 AND deleted=0{sc} GROUP BY 1").fetchall())
        order = "ORDER BY CASE WHEN cat='svc' THEN 0 ELSE 1 END, name" if "src='cat'" in sc else "ORDER BY name"
        rows = [dict(ext=r["ext"], name=r["name"], parent=_root(r["p"]), n=cnt.get(r["ext"], 0), pf=r["src"] == "pf")
                for r in con.execute(f"SELECT ext, name, {EFF_PARENT} p, src FROM erp_goods WHERE folder=1 AND deleted=0{sc} {order}")]
        return jsonify(rows)

    @app.route("/erp/api/glist")
    @lr
    def erp_api_glist():
        """Папка ичидагилари (папкалар + товарлар) ёки қидириш (бутун дарахт бўйича)."""
        con = A.db()
        f = _root(request.args.get("f"))
        qs = (request.args.get("q") or "").strip()
        fp = folder_paths(con)
        sc = gscope()
        if qs:
            sql, args = f"SELECT *, {EFF_PARENT} p FROM erp_goods WHERE deleted=0{sc}", []
            _w, _a = words_sql(qs)
            sql += _w; args += _a
            rows = con.execute(sql + " ORDER BY folder DESC, name LIMIT 500", args).fetchall()
        else:
            rows = con.execute(f"SELECT *, {EFF_PARENT} p FROM erp_goods WHERE deleted=0{sc} AND "
                               f"(CASE WHEN {EFF_PARENT} IN ('', '0') THEN '' ELSE {EFF_PARENT} END)=? ORDER BY folder DESC, "
                               f"CASE WHEN cat='svc' THEN 0 ELSE 1 END, name", (f,)).fetchall()
        st = stock_all(con)
        rp = reval_prices(con)
        out = []
        for r in rows:
            d = dict(ext=r["ext"], code=r["code"] or "", name=r["name"], folder=bool(r["folder"]), parent=_root(r["p"]),
                     path=fp.get(r["p"], "") if qs else "")
            if not r["folder"]:
                svc = is_service(con, r["ext"])
                cat_ = r["src"] == "cat"
                d.update(unit=r["unit"] or "", cur=r["cur"], price=(r["price"] or 0) if cat_ else (rp.get(r["ext"]) or r["price"] or r["price_last"] or 0),
                         last=not cat_ and not rp.get(r["ext"]) and not r["price"] and bool(r["price_last"]),
                         stock=None if svc else (st.get(r["ext"], 0) if not cat_ or r["ext"] in st else (0 if cat_ else None)), svc=svc,
                         eman=r["cstock"] if cat_ and not svc else None, cat=cat_, msort=bool(r["msort"]) if "msort" in r.keys() else False)
            out.append(d)
        return jsonify(dict(items=out, path=fp.get(f, "") if f else "", parent=_root(con.execute(f"SELECT {EFF_PARENT} FROM erp_goods WHERE ext=?",
                                                                                                    (f,)).fetchone()[0]) if f else None))

    @app.route("/erp/api/gmove", methods=["POST"])
    @lr
    def erp_api_gmove():
        """Товар/папкаларни бошқа папкага ўтказиш (фақат платформада; 1С'даги жойи ўзгармайди)."""
        con = A.db()
        if not A.can("super"):
            log(con, None, "denied", "товар ўтказиш"); con.commit()
            return jsonify(error=DENY), 403
        j = request.get_json(silent=True) or {}
        to = _root(j.get("to"))
        if to and not con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND folder=1 AND deleted=0", (to,)).fetchone():
            return jsonify(error="Папка топилмади"), 400
        par = {r[0]: _root(r[1]) for r in con.execute(f"SELECT ext, {EFF_PARENT} FROM erp_goods WHERE folder=1")}
        moved = []
        for ext in (j.get("items") or [])[:500]:
            r = con.execute("SELECT ext, name, folder, parent FROM erp_goods WHERE ext=?", (ext,)).fetchone()
            if not r or ext == to:
                continue
            if r["folder"]:                           # ўзининг ичига ўтказиб бўлмайди
                x, seen = to, set()
                while x and x not in seen:
                    if x == ext:
                        return jsonify(error=f"«{r['name']}» папкасини ўзининг ичига ўтказиб бўлмайди"), 400
                    seen.add(x); x = par.get(x, "")
            orig = _root(r["parent"])
            con.execute("UPDATE erp_goods SET pf_parent=? WHERE ext=?", ("" if to == orig else (to or "0"), ext))
            moved.append(r["name"])
        if moved:
            tn = con.execute("SELECT name FROM erp_goods WHERE ext=?", (to,)).fetchone() if to else None
            log(con, None, "good_move", f"{', '.join(moved)[:300]} → {tn[0] if tn else 'асосий'}")
        con.commit()
        return jsonify(ok=True, n=len(moved))

    @app.route("/erp/api/gfolder", methods=["POST"])
    @lr
    def erp_api_gfolder():
        """Янги папка (группа) ёки номини ўзгартириш."""
        con = A.db()
        j = request.get_json(silent=True) or {}
        name = re.sub(r"\s+", " ", (j.get("name") or "")).strip()[:100]
        if len(name) < 1:
            return jsonify(error="Папка номини ёзинг"), 400
        ext = j.get("ext")
        if ext:
            if not A.can("super"):
                return jsonify(error=DENY), 403
            r = con.execute("SELECT src FROM erp_goods WHERE ext=? AND folder=1", (ext,)).fetchone()
            if not r:
                return jsonify(error="Папка топилмади"), 404
            if r["src"] != "pf":
                return jsonify(error="1С'дан келган папка номи 1С'да ўзгартирилади"), 400
            con.execute("UPDATE erp_goods SET name=?, lc=? WHERE ext=?", (name, lc(name), ext))
            log(con, None, "folder_edit", f"{ext}: {name}")
        else:
            if not A.can("add"):
                return jsonify(error=DENY), 403
            parent = _root(j.get("parent"))
            if parent and not con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND folder=1", (parent,)).fetchone():
                return jsonify(error="Папка топилмади"), 400
            ext = _new_ext(con, "erp_goods", "P")
            con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,src,price_last,pf_parent) "
                        "VALUES(?,?,?,'',1,0,0,?,1,0,?,'pf',0,'')", (ext, ext, name, parent or "0", lc(name)))
            log(con, None, "folder_add", f"{ext}: {name}")
        con.commit()
        _svc["at"] = 0
        return jsonify(ok=True, ext=ext)

    def _units(con):
        return [r[0] for r in con.execute("SELECT unit, COUNT(*) n FROM erp_goods WHERE folder=0 AND unit<>'' GROUP BY unit ORDER BY n DESC LIMIT 15")]

    def _folders(con):
        fp = folder_paths(con)
        dead = {r[0] for r in con.execute("SELECT ext FROM erp_goods WHERE folder=1 AND deleted=1")}
        return sorted(((k, v) for k, v in fp.items() if k not in dead), key=lambda x: x[1].casefold())

    @app.route("/erp/goods/new", methods=["GET", "POST"])
    @app.route("/erp/goods/<ext>/edit", methods=["GET", "POST"])
    @lr
    def erp_good_form(ext=None):
        con = A.db()
        g = None
        if ext and not A.can("super") or not ext and not A.can("add"):
            log(con, None, "denied", f"товар {'ўзгартириш ' + ext if ext else 'қўшиш'}"); con.commit()
            flash(DENY, "error"); return redirect(url_for("erp_goods"))
        if ext:
            g = con.execute("SELECT * FROM erp_goods WHERE ext=? AND folder=0", (ext,)).fetchone()
            if not g:
                abort(404)
        cp = con.execute("SELECT * FROM erp_goods WHERE ext=? AND folder=0", (request.args.get("copy"),)).fetchone() if not ext and request.args.get("copy") else None
        if request.method == "POST":
            f = dict(request.form)
        elif g:
            f = dict(g); f["parent"] = (g["pf_parent"] or g["parent"] or "")
        elif cp:
            f = dict(name=cp["name"], unit=cp["unit"], cur=str(cp["cur"]), price=cp["price"] or "", parent=(cp["pf_parent"] or cp["parent"] or ""))
        else:
            f = dict(cur="1", unit="шт", parent=request.args.get("parent", ""))
        if request.method == "POST":
            try:
                nx = save_good(con, ext, request.form)
                con.commit()
                flash("Сақланди" if ext else "Янги товар қўшилди", "ok")
                return redirect(request.form.get("back") or url_for("erp_goods", q=con.execute("SELECT name FROM erp_goods WHERE ext=?", (nx,)).fetchone()[0]))
            except ValueError as e:
                con.rollback(); flash(str(e), "error")
        return render_template("erp_good_form.html", g=g, f=f, folders=_folders(con), units=_units(con), CUR=CUR, back=request.values.get("back", ""))

    @app.route("/erp/goods/<ext>/delete", methods=["POST"])
    @lr
    def erp_good_delete(ext):
        con = A.db()
        if not A.can("super"):
            log(con, None, "denied", f"товар {ext}"); con.commit()
            flash(DENY, "error"); return redirect(url_for("erp_goods"))
        g = con.execute("SELECT * FROM erp_goods WHERE ext=?", (ext,)).fetchone()
        if not g:
            abort(404)
        if request.form.get("unmark") == "1":
            con.execute("UPDATE erp_goods SET deleted=0 WHERE ext=?", (ext,))
            log(con, None, "good_unmark", f"{ext}: {g['name']}"); con.commit()
            flash(f"«{g['name']}» белгиси олиб ташланди", "ok")
            return redirect(request.form.get("back") or url_for("erp_goods"))
        if g["folder"] and con.execute(f"SELECT 1 FROM erp_goods WHERE deleted=0 AND {EFF_PARENT}=? LIMIT 1", (ext,)).fetchone():
            flash(f"«{g['name']}» папкаси бўш эмас — аввал ичидагиларни бошқа папкага ўтказинг ёки ўчиринг", "error")
            return redirect(request.form.get("back") or url_for("erp_goods"))
        con.execute("UPDATE erp_goods SET deleted=1, src='pf' WHERE ext=?", (ext,))
        log(con, None, "good_mark", f"{ext}: {g['name']}"); con.commit()
        n = refs_good(con, ext)
        flash(f"«{g['name']}» ўчиришга белгиланди" + (f". Тарихи бор ({n} та ҳужжатда) — бутунлай ўчмайди, белги туради." if n else
              ". Бутунлай ўчириш — админ «Ўчиришга белгиланганлар» бўлимида."), "ok")
        return redirect(request.form.get("back") or url_for("erp_goods"))

    def _clients_list(mode):
        """Мижозлар (mode='client') ёки етказиб берувчилар (mode='supplier') рўйхати. Қидирув — накладнойдагидек (номи, телефон)."""
        qs = (request.args.get("q") or "").strip()
        sql, args = "SELECT * FROM erp_clients WHERE folder=0 AND deleted=0", []
        _w, _a = words_sql(qs)
        sql += _w; args += _a
        if mode == "supplier":
            show = ""
            sql += " AND ckind='supplier'"
        else:
            show = request.args.get("show", "")
            show = show if show in ("near", "blocked") else ""
            sql += " AND COALESCE(ckind,'client')='client'"
            if show == "blocked":
                sql += " AND blocked=1"
        sort = request.args.get("sort", "name")
        sort = sort if sort in ("name", "debt", "lim", "free") else "name"
        desc = request.args.get("dir", "desc" if sort == "debt" else "asc") == "desc"
        con = A.db()
        items = []
        for r in A.q(sql + " LIMIT 3000", args):
            ci = credit_info(con, r["ext"])
            if show == "near" and not (ci["free"] < ci["lim"] * 0.1 and ci["debt"] > 0.005):
                continue
            items.append((r, ci))
        if sort == "name":
            items.sort(key=lambda x: name_key(x[0]["name"]), reverse=desc)
        else:
            items.sort(key=lambda x: x[1][sort], reverse=desc)
        grp_tot = {}
        if mode == "supplier":                     # филиаллар асосий фирма тагида; асосий фирма қаторида — бутун гуруҳ сальдоси
            byext = {r["ext"]: (r, ci) for r, ci in items}
            kids = defaultdict(list)
            for r, ci in items:
                g = (r["grp"] or "") if "grp" in r.keys() else ""
                if g and g in byext:
                    kids[g].append((r, ci))
            for g, ks in kids.items():
                grp_tot[g] = (round(byext[g][1]["debt"] + sum(ci["debt"] for _, ci in ks), 2), len(ks))
            if not qs:
                out = []
                for r, ci in items:
                    g = (r["grp"] or "") if "grp" in r.keys() else ""
                    if g and g in byext:
                        continue
                    out.append((r, ci))
                    out += kids.get(r["ext"], [])
                items = out
        total = len(items)
        cnt = "COALESCE(ckind,'client')='client'" if mode == "client" else "ckind='supplier'"
        return render_template("_clients_table.html" if request.args.get("part") == "1" else "erp_clients.html", rows=items[:500], total=total, q=qs, show=show, sort=sort, desc=desc, mode=mode,
                               ep=request.endpoint, part=request.args.get("part") == "1",
                               n=A.scalar(f"SELECT COUNT(*) FROM erp_clients WHERE folder=0 AND deleted=0 AND {cnt}"),
                               nb=A.scalar("SELECT COUNT(*) FROM erp_clients WHERE folder=0 AND deleted=0 AND blocked=1 AND COALESCE(ckind,'client')='client'"),
                               dflt=credit_default(), CKINDS=CKINDS, grp_tot=grp_tot,
                               gname={r["ext"]: r["name"] for r, _ in items} if mode == "supplier" else {})

    @app.route("/erp/clients")
    @lr
    def erp_clients():
        return _clients_list("client")

    @app.route("/erp/suppliers")
    @lr
    def erp_suppliers():
        return _clients_list("supplier")

    @app.route("/erp/clients/<ext>/credit", methods=["GET", "POST"])
    @lr
    def erp_client_credit(ext):
        con = A.db()
        c = con.execute("SELECT * FROM erp_clients WHERE ext=? AND folder=0", (ext,)).fetchone()
        if not c:
            abort(404)
        if request.method == "POST":
            if not A.can("limit"):
                log(con, None, "denied", f"лимит {ext}"); con.commit()
                flash(DENY, "error")
            elif request.form.get("act") == "due_open":
                con.execute("UPDATE erp_clients SET unblocked_at=? WHERE ext=?", (A.now(), ext))
                log(con, None, "credit_set", f"{ext} {c['name']}: муддати ўтган накладнойлар бўйича блок очилди")
                con.commit(); flash("Муддат блоки очилди", "ok")
            else:
                lim = A.parse_float(request.form.get("lim"), -1)
                blk = 1 if request.form.get("blocked") else 0
                dv = (request.form.get("due_days") or "").strip()
                due = None if dv == "" else max(0, int(A.parse_float(dv, 0)))
                if due != c["due_days"]:
                    con.execute("UPDATE erp_clients SET due_days=? WHERE ext=?", (due, ext))
                    log(con, None, "credit_set", f"{ext} {c['name']}: тўлов муддати → {due if due is not None else 'стандарт'}")
                kind = request.form.get("ckind") if request.form.get("ckind") in CKINDS else None
                if kind and kind != (c["ckind"] or "client"):
                    con.execute("UPDATE erp_clients SET ckind=?, ckind_set=1 WHERE ext=?", (kind, ext))
                    log(con, None, "credit_set", f"{ext} {c['name']}: тури → {CKINDS[kind]}")
                    if kind != "client":
                        blk = 0
                if lim < 0:
                    flash("Лимитни тўғри ёзинг", "error")
                else:
                    old = credit_info(con, ext)
                    con.execute("UPDATE erp_clients SET lim=?, blocked=?, block_note=?, unblocked_at=? WHERE ext=?",
                                (round(lim, 2), blk, (request.form.get("note") or "").strip()[:200] if blk else "",
                                 A.now() if (old["blocked"] and not blk) else c["unblocked_at"], ext))
                    log(con, None, "credit_set", f"{ext} {c['name']}: лимит ${old['lim']:.0f}→${lim:.0f}"
                        + (f", запрет {'қўйилди' if blk else 'очилди'}" if blk != int(old["blocked"]) else ""))
                    con.commit()
                    flash("Сақланди", "ok")
            return redirect(request.form.get("back") or url_for("erp_client_credit", ext=ext))
        logs = A.q("SELECT * FROM erp_log WHERE action IN ('credit_set','denied') AND detail LIKE ? ORDER BY id DESC LIMIT 20", (f"%{ext}%",))
        return render_template("erp_client_credit.html", c=con.execute("SELECT * FROM erp_clients WHERE ext=?", (ext,)).fetchone(),
                               ci=credit_info(con, ext), logs=logs, back=request.args.get("back", ""), CKINDS=CKINDS, due0=due_default())

    @app.route("/erp/clients/<ext>/card")
    @lr
    def erp_client_card(ext):
        """Справочник → Клиенты: мижоз очилганда — кўриш карточкаси (қарз, тарих, хабарлар). Ўзгартириш — «Изменить» тугмаси орқали."""
        con = A.db()
        c = con.execute("SELECT * FROM erp_clients WHERE ext=? AND folder=0", (ext,)).fetchone()
        if not c:
            abort(404)
        if (c["ckind"] or "client") == "supplier" or A.is_limited():
            return redirect(url_for("erp_client_form", ext=ext, back=request.args.get("back", "")) if A.can("super")
                            else url_for("erp_history", kind="client", ext=ext, back=request.args.get("back", "")))
        cid = ensure_notif(con, c)
        con.commit()
        return redirect(url_for("client_card", cid=cid, back=request.args.get("back") or url_for("erp_clients")))

    @app.route("/erp/clients/new", methods=["GET", "POST"])
    @app.route("/erp/clients/<ext>/edit", methods=["GET", "POST"])
    @lr
    def erp_client_form(ext=None):
        con = A.db()
        c = None
        if ext and not A.can("super") or not ext and not A.can("add"):
            log(con, None, "denied", f"мижоз {'ўзгартириш ' + ext if ext else 'қўшиш'}"); con.commit()
            flash(DENY, "error"); return redirect(url_for("erp_clients"))
        if ext:
            c = con.execute("SELECT * FROM erp_clients WHERE ext=? AND folder=0", (ext,)).fetchone()
            if not c:
                abort(404)
        f = dict(request.form) if request.method == "POST" else (dict(c) if c else {})
        if request.method == "POST":
            try:
                nx = save_client(con, ext, request.form)
                save_notif(con, nx, request.form)
                con.commit()
                flash("Сақланди" if ext else "Янги мижоз қўшилди", "ok")
                back = request.form.get("back") or ""
                if back.startswith("/erp/sale/new"):
                    return redirect(url_for("erp_sale_edit", client=nx))
                if back.startswith("/erp/receipt/new2"):
                    return redirect(url_for("erp_doc2_edit", kind="receipt", client=nx))
                rr = con.execute("SELECT name, ckind FROM erp_clients WHERE ext=?", (nx,)).fetchone()
                return redirect(back or url_for("erp_suppliers" if rr["ckind"] == "supplier" else "erp_clients", q=rr["name"]))
            except ValueError as e:
                con.rollback(); flash(str(e), "error")
        if request.method == "POST":
            f["phone"] = request.form.getlist("phone")
        is_sup = (f.get("ckind") or (c["ckind"] if c else "") or request.values.get("ckind") or "") == "supplier"
        mains = [dict(r) for r in con.execute("SELECT ext, name FROM erp_clients WHERE folder=0 AND deleted=0 AND ckind='supplier' "
                                              "AND COALESCE(grp,'')='' AND ext<>? ORDER BY name", (ext or "",))] if is_sup else []
        has_br = bool(ext and con.execute("SELECT 1 FROM erp_clients WHERE grp=? AND deleted=0 LIMIT 1", (ext,)).fetchone())
        nc = notif_row(con, ext) if ext else None
        back_ = request.values.get("back", "") or (url_for("client_card", cid=nc["id"]) if nc else "")
        nmsgs = A.messages_for(client_id=nc["id"], limit=5) if nc else []
        return render_template("erp_client_form.html", c=c, f=f, back=back_, CKINDS=CKINDS, is_sup=is_sup, mains=mains,
                               has_br=has_br, menu_sup=is_sup, nc=nc, nmsgs=nmsgs, LANGS=A.LANGS,
                               ci=credit_info(con, ext) if c else None, dflt=credit_default(), due0=due_default())

    @app.route("/erp/clients/<ext>/delete", methods=["POST"])
    @lr
    def erp_client_delete(ext):
        con = A.db()
        if not A.can("super"):
            log(con, None, "denied", f"мижоз {ext}"); con.commit()
            flash(DENY, "error"); return redirect(url_for("erp_clients"))
        c = con.execute("SELECT * FROM erp_clients WHERE ext=?", (ext,)).fetchone()
        if not c:
            abort(404)
        if request.form.get("unmark") == "1":
            con.execute("UPDATE erp_clients SET deleted=0 WHERE ext=?", (ext,))
            log(con, None, "client_unmark", f"{ext}: {c['name']}"); con.commit()
            flash(f"«{c['name']}» белгиси олиб ташланди", "ok")
            return redirect(request.form.get("back") or url_for("erp_clients"))
        con.execute("UPDATE erp_clients SET deleted=1, src='pf' WHERE ext=?", (ext,))
        log(con, None, "client_mark", f"{ext}: {c['name']}"); con.commit()
        n = refs_client(con, ext)
        flash(f"«{c['name']}» ўчиришга белгиланди" + (f". Тарихи бор ({n} та ҳужжатда) — бутунлай ўчмайди, белги туради." if n else
              ". Бутунлай ўчириш — админ «Ўчиришга белгиланганлар» бўлимида."), "ok")
        return redirect(url_for("erp_clients"))

    @app.route("/erp/history/<kind>/<ext>")
    @lr
    def erp_history(kind, ext):
        con = A.db()
        if kind == "good":
            it = con.execute("SELECT name FROM erp_goods WHERE ext=?", (ext,)).fetchone()
            rows = A.q("SELECT d.*, c.name cname FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.id IN "
                       "(SELECT doc_id FROM erp_lines WHERE good=?) ORDER BY d.date DESC, d.time DESC LIMIT 300", (ext,))
        elif kind == "client":
            it = con.execute("SELECT name FROM erp_clients WHERE ext=?", (ext,)).fetchone()
            rows = [dict(r) for r in A.q("SELECT d.*, c.name cname FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.client=? OR d.id IN "
                       "(SELECT doc_id FROM erp_lines WHERE client=?) ORDER BY d.date DESC, d.time DESC LIMIT 300", (ext, ext))]
            # «Поступление денег» бир неча мижозга бўлиши мумкин — шу мижознинг қатори (сумма ва номи), ҳужжат бошидаги мижоз эмас
            ids = [r["id"] for r in rows if r["kind"] == "pay"]
            part = {}
            for i in range(0, len(ids), 500):
                ch = ids[i:i + 500]
                for x in con.execute(f"SELECT doc_id, SUM(COALESCE(debt,0)) debt, SUM(COALESCE(usd,0)) usd, SUM(COALESCE(uzs,0)) uzs, COUNT(*) n FROM erp_lines "
                                     f"WHERE client=? AND doc_id IN ({','.join('?' * len(ch))}) GROUP BY doc_id", [ext] + ch):
                    part[x["doc_id"]] = x
            for r in rows:
                x = part.get(r["id"])
                if x:
                    r["cname"] = it[0]
                    r["usd"] = x["debt"] or x["usd"] or ((x["uzs"] / r["rate"]) if r["rate"] else 0)
        else:
            abort(404)
        if not it:
            abort(404)
        back = request.args.get("back") or ""
        if not back.startswith("/"):
            back = url_for("erp_clients") if kind == "client" else url_for("erp_goods")
        return render_template("erp_history.html", name=it[0], kind=kind, rows=rows, KIND_NAMES=KIND_NAMES, back=back)

    @app.route("/erp/marked", methods=["GET", "POST"])
    @A.admin_required
    def erp_marked():
        con = A.db()
        res = None
        if request.method == "POST":
            busy = others_working(con)
            if busy:
                flash("Монопол режим керак: ҳозир бошқа фойдаланувчилар ишлаяпти (" + ", ".join(busy) + "). Улар чиққач қайта босинг.", "error")
            else:
                pick = set(request.form.getlist("pick"))
                if not pick:
                    flash("Ўчириладиганларни белгиланг", "error")
                else:
                    res = purge_marked(con, pick); con.commit()
                    flash(f"Текширилди: {len(res['deleted'])} та бутунлай ўчирилди" +
                          (f", {len(res['kept'])} таси тарихи бор — ўчмади" if res["kept"] else ""), "ok")
        goods = [(r, refs_good(con, r["ext"])) for r in con.execute("SELECT * FROM erp_goods WHERE deleted=1 AND folder=0 ORDER BY name")]
        clients = [(r, refs_client(con, r["ext"])) for r in con.execute("SELECT * FROM erp_clients WHERE deleted=1 AND folder=0 ORDER BY name")]
        docs = A.q("SELECT d.*, c.name cname FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.src='pf' AND d.status='deleted' ORDER BY d.date DESC")
        return render_template("erp_marked.html", goods=goods, clients=clients, docs=docs, res=res, busy=others_working(con))

    @app.route("/erp/accounts", methods=["GET", "POST"])
    @A.admin_required
    def erp_accounts():
        con = A.db()
        if request.method == "POST":
            f, act = request.form, request.form.get("act")
            try:
                if act == "move":
                    amt = A.parse_float(f.get("amount"), 0)
                    fa, ta = int(f.get("from_acc") or 0) or None, int(f.get("to_acc") or 0) or None
                    if amt <= 0 or fa == ta or not (fa or ta):
                        raise ValueError("Ўтказмани тўғри тўлдиринг")
                    con.execute("INSERT INTO erp_moves(date,from_acc,to_acc,cur,amount,note,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                                ((A.parse_date(f.get("date")) or date.today()).isoformat(), fa, ta, "usd" if f.get("cur") == "usd" else "uzs",
                                 r2(amt), (f.get("note") or "")[:200], _user(), A.now()))
                    log(con, None, "acc_move", f"{fa}→{ta} {amt} {f.get('cur')}")
                elif act == "move_del":
                    con.execute("UPDATE erp_moves SET deleted=1 WHERE id=?", (f.get("id"),)); log(con, None, "acc_move_del", f.get("id"))
                elif act == "fee":
                    mid, pct = int(f.get("method_id")), A.parse_float(f.get("pct"), -1)
                    if not 0 <= pct <= 20:
                        raise ValueError("Комиссия 0–20% бўлсин")
                    fd = (A.parse_date(f.get("from")) or date.today()).isoformat()
                    con.execute("INSERT INTO erp_fees(method_id,from_date,pct,set_by,set_at) VALUES(?,?,?,?,?)", (mid, fd, pct, _user(), A.now()))
                    log(con, None, "fee_set", f"тўлов тури {mid}: {pct}% ({fd} дан)")
                elif act == "acc_add":
                    n = (f.get("name") or "").strip()[:80]
                    if not n:
                        raise ValueError("Номини ёзинг")
                    con.execute("INSERT INTO erp_accounts(name,kind,sort) VALUES(?,?,99)", (n, f.get("kind") if f.get("kind") in ACC_KINDS else "bank"))
                elif act == "method_add":
                    n = (f.get("name") or "").strip()[:80]
                    if not n:
                        raise ValueError("Номини ёзинг")
                    mid = con.execute("INSERT INTO erp_methods(name,account_id,sort) VALUES(?,?,99)", (n, int(f.get("account_id")))).lastrowid
                    con.execute("INSERT INTO erp_fees(method_id,from_date,pct,set_by,set_at) VALUES(?,?,?,?,?)",
                                (mid, "2000-01-01", max(0.0, A.parse_float(f.get("pct"), 0)), _user(), A.now()))
                elif act == "acc_open":
                    con.execute("INSERT INTO k_acc_open(acc_id,date,uzs,usd,set_by,set_at) VALUES(?,?,?,?,?,?)",
                                (int(f.get("acc_id")), (A.parse_date(f.get("date")) or date.today()).isoformat(),
                                 A.parse_float(f.get("uzs"), 0), A.parse_float(f.get("usd"), 0), _user(), A.now()))
                    log(con, None, "acc_open", f"{f.get('acc_id')}: {f.get('date')} {f.get('uzs')} сўм {f.get('usd')} $")
                elif act == "acc_c1":
                    con.execute("UPDATE erp_accounts SET c1_ext=? WHERE id=?", ((f.get("c1_ext") or "").strip(), int(f.get("acc_id"))))
                elif act == "method_toggle":
                    con.execute("UPDATE erp_methods SET active=1-active WHERE id=?", (f.get("id"),))
                con.commit(); flash("Сақланди", "ok")
            except (ValueError, TypeError) as e:
                con.rollback(); flash(str(e) or "Хато", "error")
            return redirect(url_for("erp_accounts"))
        ms = [(m, fee_pct(con, m["id"]), con.execute("SELECT * FROM erp_fees WHERE method_id=? ORDER BY from_date DESC, id DESC", (m["id"],)).fetchall())
              for m in methods(con, all_=True)]
        mv = A.q("SELECT mv.*, a.name fa, b.name ta FROM erp_moves mv LEFT JOIN erp_accounts a ON a.id=mv.from_acc LEFT JOIN erp_accounts b ON b.id=mv.to_acc "
                 "WHERE mv.deleted=0 ORDER BY mv.date DESC, mv.id DESC LIMIT 50")
        d1 = date.today().replace(day=1).isoformat()
        bym = A.q("SELECT m.name, p.cur, SUM(p.amount) a, SUM(p.fee) f, COUNT(DISTINCT p.doc_id) n FROM erp_pays p JOIN erp_docs d ON d.id=p.doc_id "
                  "LEFT JOIN erp_methods m ON m.id=p.method_id WHERE d.status='posted' AND d.date>=? GROUP BY m.name, p.cur ORDER BY m.name", (d1,))
        c1acc = con.execute("SELECT ext, name FROM erp_clients WHERE ckind='account' AND folder=0 ORDER BY name").fetchall()
        return render_template("erp_accounts.html", bal=account_balances(con), ms=ms, mv=mv, bym=bym, ACC_KINDS=ACC_KINDS, c1acc=c1acc,
                               accs=con.execute("SELECT * FROM erp_accounts ORDER BY sort, id").fetchall(), today=date.today().isoformat())

    import sys as _sys, epay as _epay
    _epay.register(A, _sys.modules[__name__])

    @app.route("/erp/sync", methods=["POST"])
    @A.admin_required
    def erp_sync():
        try:
            r = sync(force=True)
            flash("1С'дан кўчирилди: " + (", ".join(f"{k} {v}" for k, v in r.items()) if r else "ҳозир бошқа кўчириш кетяпти"), "ok")
        except Exception as e:
            A.errlog.exception("erp sync")
            flash(f"Хато: {e}", "error")
        return redirect(request.form.get("back") or url_for("erp_docs"))
