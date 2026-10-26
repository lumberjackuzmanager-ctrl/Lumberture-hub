# -*- coding: utf-8 -*-
"""1С 7.7 (DBF): чтение базы прямо из файлов, без запуска 1С и без лицензии. Только ЧТЕНИЕ.

Таблицы и поля взяты из 1Cv7.DD этой конфигурации:
  SC12 Клиенты, SC55 Товары, SC469 Склад, SC522 Валюта
  DH/DT210 Накладная (реализация), DH/DT430 ПоступлениеТов (приход), DH/DT1195 ПоступлениеДенег (оплата)
  RG/RA445 Долг (взаиморасчёты, $), RG/RA410 ОстатокТов, RG/RA106 Касса
Знак долга: минус = клиент должен нам, плюс = аванс (как в 1С).
"""
import kb
import os, re, threading
from collections import defaultdict
from datetime import date, timedelta

from flask import request, render_template, abort, redirect, url_for, flash
from werkzeug.exceptions import HTTPException

import dbf77

A = None
DEFAULT_PATH = r"C:\new base"
# рабочая база в сети (с\c — кириллица/латиница в имени папки), затем копия
CANDIDATES = (r"\\ADMIN\1с база\new base", r"\\ADMIN\1c база\new base", r"\\Admin\1c baza\new base", DEFAULT_PATH)
_cache, _lock = {}, threading.Lock()
_auto = {"path": None, "at": 0.0}

DOC_NAMES = {210: "Накладная", 430: "Поступление товара", 1195: "Поступление денег", 1133: "Переоценка", 1207: "Расход из кассы",
             1216: "Выплата денег", 1228: "Обмен денег", 1354: "Обнуление долга", 1467: "Остатки взаиморасчётов",
             419: "Ввод остатков товара", 1176: "Остаток кассы", 1485: "Отгрузка"}


def base_path():
    """Папка базы: из настройки, иначе первая доступная из CANDIDATES (проверка раз в 5 минут)."""
    p = (A.get_setting("c1_path", "") or "").strip()
    if p:
        return p
    import time
    if _auto["path"] and time.time() - _auto["at"] < 300:
        return _auto["path"]
    found = next((c for c in CANDIDATES if os.path.isfile(os.path.join(c, "1Cv7.DD"))), DEFAULT_PATH)
    _auto.update(path=found, at=time.time())
    return found

def b36(s):
    s = (s or "").strip()
    try:
        return int(s, 36) if s else 0
    except ValueError:
        return 0

def table(name):
    """Строки таблицы DBF (кэш до изменения файла)."""
    path = os.path.join(base_path(), name + ".DBF")
    try:
        st = os.stat(path)
    except OSError:
        raise FileNotFoundError(f"Не найден файл 1С: {path}")
    key = path
    with _lock:
        c = _cache.get(key)
        if c and c[0] == (st.st_mtime, st.st_size):
            return c[1]
    _, rows, _ = dbf77.read_dbf(path)
    for r in rows:                       # ссылки 1С хранятся с ведущими пробелами: «    1Q» -> «1Q»
        for k, v in r.items():
            if isinstance(v, str) and v[:1] == " ":
                r[k] = v.strip()
    with _lock:
        _cache[key] = ((st.st_mtime, st.st_size), rows)
    return rows

def journal():
    res = {}
    for r in table("1SJOURN"):
        res[r["IDDOC"]] = dict(id=r["IDDOC"], kind=b36(r["IDDOCDEF"]), date=r["DATE"], time=r["TIME"], no=(r["DOCNO"] or "").strip(),
                               posted=bool(r["CLOSED"]), deleted=(r["ISMARK"] or "").strip() == "*")
    return res

def clients():
    return {r["ID"]: dict(id=r["ID"], code=(r["CODE"] or "").strip(), name=r["DESCR"].strip(), full=r["SP17"].strip(),
                          phones=", ".join(p.strip() for p in (r["SP546"], r["SP1525"], r["SP1526"]) if p and p.strip()),
                          parent=r["PARENTID"], folder=int(r["ISFOLDER"] or 0) == 1, deleted=(r["ISMARK"] or "").strip() == "*")
            for r in table("SC12")}

def goods():
    return {r["ID"]: dict(id=r["ID"], code=(r["CODE"] or "").strip(), name=r["DESCR"].strip(), unit=r["SP57"].strip(), price=r["SP134"],
                          parent=r["PARENTID"], folder=int(r["ISFOLDER"] or 0) == 1, deleted=(r["ISMARK"] or "").strip() == "*")
            for r in table("SC55")}

def _last(rows):
    if not rows:
        return None, []
    per = max(r["PERIOD"] for r in rows if r["PERIOD"])
    return per, [r for r in rows if r["PERIOD"] == per]

def debts():
    """Текущее сальдо взаиморасчётов по клиентам, $ (минус — долг клиента)."""
    per, rows = _last(table("RG445"))
    res = defaultdict(float)
    for r in rows:
        res[r["SP446"]] += r["SP447"]
    return per, dict(res)

def stock():
    per, rows = _last(table("RG410"))
    res = defaultdict(float)
    for r in rows:
        res[r["SP412"]] += r["SP413"]
    return per, dict(res)

def cash():
    per, rows = _last(table("RG106"))
    cur = {r["ID"]: r["DESCR"].strip() for r in table("SC522")}
    return per, [(cur.get(r["SP1165"], "$" if r["SP1165"] in ("0", "") else r["SP1165"]), r["SP107"]) for r in rows]

def client_moves(cid):
    """Движения по взаиморасчётам клиента с остатком (по возрастанию даты)."""
    j = journal()
    moves = []
    for r in table("RA445"):
        if r["SP446"] != cid:
            continue
        d = j.get(r["IDDOC"])
        amt = r["SP447"] * (1 if int(r["DEBKRED"]) == 0 else -1)
        moves.append(dict(doc=d, iddoc=r["IDDOC"], amount=amt))
    moves.sort(key=lambda m: ((m["doc"]["date"] or date.min), m["doc"]["time"]) if m["doc"] else (date.min, ""))
    bal = 0.0
    for m in moves:
        bal += m["amount"]; m["balance"] = bal
    return moves

def docs(kind, d1, d2):
    j = journal()
    return {k: v for k, v in j.items() if v["kind"] == kind and v["date"] and d1 <= v["date"] <= d2}

def sales(d1, d2, cid=None):
    jd, cl = docs(210, d1, d2), clients()
    res = []
    for h in table("DH210"):
        d = jd.get(h["IDDOC"])
        if not d or (cid and h["SP232"] != cid):
            continue
        res.append(dict(doc=d, client=cl.get(h["SP232"], {}).get("name", "?"), cid=h["SP232"], usd=h["SP905"], uzs=h["SP559"],
                        rate=h["SP1132"], user=h["SP1266"].strip(), discount=h["SP1356"]))
    res.sort(key=lambda x: (x["doc"]["date"], x["doc"]["time"]), reverse=True)
    return res

def receipts(d1, d2):
    jd, cl = docs(430, d1, d2), clients()
    res = []
    for h in table("DH430"):
        d = jd.get(h["IDDOC"])
        if d:
            res.append(dict(doc=d, client=cl.get(h["SP431"], {}).get("name", "?"), cid=h["SP431"], qty=h["SP433"], amount=h["SP435"],
                            amount_val=h["SP1113"], user=h["SP1269"].strip()))
    res.sort(key=lambda x: (x["doc"]["date"], x["doc"]["time"]), reverse=True)
    return res

def payments(d1, d2):
    jd, cl = docs(1195, d1, d2), clients()
    heads = {h["IDDOC"]: h for h in table("DH1195")}
    res = []
    for r in table("DT1195"):
        d = jd.get(r["IDDOC"])
        if not d:
            continue
        h = heads.get(r["IDDOC"], {})
        res.append(dict(doc=d, client=cl.get(r["SP1198"], {}).get("name", "?"), cid=r["SP1198"], usd=r["SP1199"], uzs=r["SP1202"],
                        to_debt=r["SP1245"], rate=h.get("SP1247", 0), user=(h.get("SP1276") or "").strip()))
    res.sort(key=lambda x: (x["doc"]["date"], x["doc"]["time"]), reverse=True)
    return res

_blob_cache = {}

def blob_texts(field_id):
    """Чексиз узунликдаги реквизитлар (1SBLOB): {объект id: матн}. Масалан, 1362 — Накладнойнинг «Примечание1»."""
    path = os.path.join(base_path(), "1SBLOB.DBF")
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (path, field_id)
    c = _blob_cache.get(key)
    if c and c[0] == (st.st_mtime, st.st_size):
        return c[1]
    fid = ""
    n = field_id
    while n:
        n, r = divmod(n, 36)
        fid = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[r] + fid
    parts = defaultdict(list)
    for r in dbf77.read_dbf(path)[1]:
        if (r["FIELDID"] or "").strip() == fid:
            parts[(r["OBJID"] or "").strip()].append((int(r["BLOCKNO"] or 0), r["BLOCK"] or ""))
    res = {}
    for obj, bl in parts.items():
        raw = "".join(b.ljust(80) for _, b in sorted(bl))       # DBF ўнгдаги бўшлиқларни кесади — 80 гача тиклаймиз
        try:
            ln = int(raw[:9].strip() or "0", 16)
        except ValueError:
            ln = len(raw) - 9
        txt = raw[9:9 + ln].strip()
        if txt:
            res[obj] = txt
    _blob_cache[key] = ((st.st_mtime, st.st_size), res)
    return res

def sale_note1(iddoc):
    return blob_texts(1362).get(iddoc, "")

def pf_sale_doc(doc_id):
    """Платформа накладнойи («pf:<id>») — 1С sale_doc кўринишида (цех боти ва KPI учун)."""
    import app as A
    con = A.db()
    d = con.execute("SELECT d.*, c.name cname, c.phones cphones FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client "
                    "WHERE d.id=? AND d.kind='sale'", (int(doc_id),)).fetchone()
    if not d:
        return None
    ls = con.execute("SELECT l.*, g.name gname FROM erp_lines l LEFT JOIN erp_goods g ON g.ext=l.good WHERE l.doc_id=? ORDER BY l.lineno",
                     (int(doc_id),)).fetchall()
    return dict(doc=dict(id=f"pf:{d['id']}", kind=210, date=date.fromisoformat(d["date"][:10]) if d["date"] else None, no=d["no"], posted=d["status"] == "posted", deleted=d["status"] == "deleted"),
                head=None, cid=d["client"], rate=d["rate"], client=dict(name=d["cname"] or "", phones=d["cphones"] or ""), note1=d["note"] or "",
                lines=[dict(no=int(x["lineno"]), gid=x["good"], name=x["gname"] or "?", unit=(x["unit"] or "").strip(), qty=x["qty"], price=x["price"],
                            usd=x["usd"], price_uzs=x["price_uzs"], uzs=x["uzs"], note=x["note"] or "") for x in ls])


def sale_doc(iddoc):
    if str(iddoc or "").startswith("pf:"):
        return pf_sale_doc(str(iddoc)[3:])
    j = journal().get(iddoc)
    if not j or j["kind"] != 210:
        return None
    h = next((x for x in table("DH210") if x["IDDOC"] == iddoc), None)
    g = goods()
    lines = sorted((x for x in table("DT210") if x["IDDOC"] == iddoc), key=lambda x: x["LINENO"])
    cl = clients().get(h["SP232"], {}) if h else {}
    return dict(doc=j, head=h, client=cl, note1=sale_note1(iddoc), lines=[dict(no=int(x["LINENO"]), gid=x["SP216"], name=g.get(x["SP216"], {}).get("name", "?"), unit=x["SP556"].strip(),
                qty=x["SP217"], price=x["SP904"], usd=x["SP905"], price_uzs=x["SP557"], uzs=x["SP559"],
                note=(x["SP910"].strip() or x["SP1531"].strip())) for x in lines])

MONTHS_GEN = ["", "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
PRINT_FORMS = {"perm": "Разрешение", "bez": "Накладная без", "full": "Накладная"}

def folder_path(items, fid):
    names, seen = [], set()
    while fid and fid not in ("0", "") and fid in items and fid not in seen:
        seen.add(fid); names.append(items[fid]["name"]); fid = items[fid]["parent"]
    return " / ".join(reversed(names))



# ================================================================ синхронизация 1С -> «Должники» (для напоминаний в Telegram)
def _phone(ph):
    """Первый узбекский мобильный из строки телефонов 1С -> «+998 XX XXX XX XX» или ''."""
    for part in re.split(r"[,;/]", ph or ""):
        d = re.sub(r"\D", "", part)
        if len(d) == 9 and d[0] != "0":
            d = "998" + d
        if len(d) == 12 and d.startswith("998") and d[3] != "0":
            return f"+998 {d[3:5]} {d[5:8]} {d[8:10]} {d[10:12]}"
    return ""

def all_moves():
    """Движения регистра «Долг» по всем клиентам: {клиент: [(дата, время, №, вид, сумма$, iddoc, строка)]}."""
    j = journal()
    res = defaultdict(list)
    for r in table("RA445"):
        d = j.get(r["IDDOC"])
        if not d or not d["date"]:
            continue
        amt = r["SP447"] * (1 if int(r["DEBKRED"]) == 0 else -1)
        res[r["SP446"]].append((d["date"], d["time"], d["no"], d["kind"], round(amt, 2), r["IDDOC"], int(r["LINENO"] or 0)))
    for v in res.values():
        v.sort()
    return res

def _pf_con():
    import app as _A
    return _A.db()


def pf_moves(con=None):
    """1С'дан ўтилгандан кейин (26.10.2026) платформада проведен қилинган ҳужжатлар — 1С «Долг» регистри (RA445) кўринишида:
    {мижоз ext: [(сана, вақт, №, тур 210/1195, сумма$ (+ тўлов, − қарз), «pf:<id>», қатор)]}. Накладнойдаги тўлов — алоҳида плюс қатор."""
    con = con or _pf_con()
    res = defaultdict(list)
    try:
        rows = con.execute("SELECT id, no, date, time, client, usd, COALESCE(paid,0) paid FROM erp_docs WHERE src='pf' AND kind='sale' "
                           "AND status='posted' AND COALESCE(nal,1)<>0 AND COALESCE(client,'')<>''").fetchall()
        pays = con.execute("SELECT d.id, d.no, d.date, d.time, l.client, l.lineno, COALESCE(l.debt,0) debt FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id "
                           "WHERE d.src='pf' AND d.kind='pay' AND d.status='posted' AND COALESCE(l.client,'')<>''").fetchall()
    except Exception:
        return {}
    for r in rows:
        try:
            d = date.fromisoformat((r["date"] or "")[:10])
        except ValueError:
            continue
        iid = f"pf:{r['id']}"
        res[r["client"]].append((d, r["time"] or "", str(r["no"] or ""), 210, round(-(r["usd"] or 0), 2), iid, 1))
        if abs(r["paid"] or 0) >= 0.005:
            res[r["client"]].append((d, r["time"] or "", str(r["no"] or ""), 210, round(r["paid"] or 0, 2), iid, 2))
    for r in pays:
        try:
            d = date.fromisoformat((r["date"] or "")[:10])
        except ValueError:
            continue
        if abs(r["debt"]) >= 0.005:
            res[r["client"]].append((d, r["time"] or "", str(r["no"] or ""), 1195, round(r["debt"], 2), f"pf:{r['id']}", int(r["lineno"] or 0)))
    return res


def all_moves_pf():
    """1С архиви + платформа ҳужжатлари (мижозлар қарзи ва хабарлари учун). 1С ўқилмаса — хато (қарзлар ўчиб кетмасин)."""
    res = all_moves()
    for k, v in pf_moves().items():
        res[k].extend(v)
    for v in res.values():
        v.sort(key=lambda m: (m[0], m[1] or "", str(m[5]), m[6]))
    return res


def client_moves_all(cid):
    """client_moves() + платформа ҳужжатлари, қолдиқ билан (накладной расмидаги «олдин/кейин» қарз учун)."""
    j = journal()
    out = []
    for m in client_moves(cid):
        out.append(dict(doc=m["doc"], iddoc=m["iddoc"], amount=m["amount"], key=((m["doc"]["date"] or date.min), m["doc"]["time"] or "") if m["doc"] else (date.min, "")))
    for (d, tm, no, kind, amt, iddoc, ln) in pf_moves().get(cid, []):
        out.append(dict(doc=dict(id=iddoc, kind=kind, date=d, time=tm, no=no, posted=True, deleted=False), iddoc=iddoc, amount=amt, key=(d, tm)))
    out.sort(key=lambda m: m["key"])
    bal = 0.0
    for m in out:
        bal += m["amount"]; m["balance"] = bal
    return out


def clients_pf():
    """1С мижозлари + платформада очилган мижозлар (erp_clients, 1С'да йўқлари)."""
    cl = clients()
    try:
        for r in _pf_con().execute("SELECT ext, name, phones, COALESCE(\"full\",'') AS fl, COALESCE(folder,0) folder, COALESCE(deleted,0) deleted FROM erp_clients"):
            if r["ext"] and r["ext"] not in cl:
                cl[r["ext"]] = dict(id=r["ext"], code=r["ext"], name=(r["name"] or "").strip(), full=r["fl"] or "", phones=r["phones"] or "",
                                    parent="", folder=bool(r["folder"]), deleted=bool(r["deleted"]))
    except Exception:
        pass
    return cl


def totals(moves):
    taken = -sum(m[4] for m in moves if m[4] < 0)
    paid = sum(m[4] for m in moves if m[4] > 0)
    return taken, paid, paid - taken

def sync_clients(force=False):
    """Зеркалит взаиморасчёты 1С в «Должники»: клиент 1С = клиент платформы (по коду 1С), накладные = долги,
    оплаты = платежи, всё в $. Сообщения при этом не отправляются. -> dict статистики."""
    import hashlib, time as _t
    db = A.db()
    cl, mv = clients_pf(), all_moves_pf()            # 26.10.2026 дан: 1С архиви + платформада проведен қилинган ҳужжатлар
    c1cl = set(clients())
    linked = {r["onec_code"]: r for r in A.q("SELECT * FROM clients WHERE onec_code LIKE '1c77:%'")}
    remind = 1 if A.get_setting("c1_new_remind", "0") == "1" else 0
    st = dict(new=0, updated=0, same=0)
    ts = A.now()
    recent = date.today() - timedelta(days=2)
    for cid, moves in mv.items():
        c = cl.get(cid)
        if not c or c["folder"]:
            continue
        code = "1c77:" + cid
        bal = round(sum(m[4] for m in moves), 2)
        loc = linked.get(code)
        if not loc and abs(bal) < 0.01 and not any(m[0] >= recent for m in moves):
            continue                      # закрытых клиентов без долга и без свежих документов не заводим
        h = hashlib.md5(repr(moves).encode()).hexdigest()
        phone = _phone(c["phones"])
        if not loc:
            lid = db.execute("INSERT INTO clients(name,phone,company,notes,active,sms_enabled,tg_code,is_demo,created_at,onec_code,onec_hash) "
                             "VALUES(?,?,?,?,1,?,?,0,?,?,?)", (c["name"][:200], phone, (c["full"] or "")[:200], ("Из 1С (код " if cid in c1cl else "Из платформы (код ") + c["code"] + ")",
                                                                 remind, A.new_code(), ts, code, "")).lastrowid
            st["new"] += 1
            old_hash = ""
        else:
            lid, old_hash = loc["id"], loc["onec_hash"] or ""
            if c["name"] and loc["name"] != c["name"]:
                db.execute("UPDATE clients SET name=? WHERE id=?", (c["name"][:200], lid))
            if phone and (not (loc["phone"] or "").strip() or (loc["phone"] != phone and (loc["tg_status"] or "") != "ok")):
                # карточкадаги биринчи рақам ўзгарса (эгаси, 26.10: янги Telegram рақами қўшилди) — Telegram'и топилмаган
                # мижозда шу рақамга ўтади ва Telegram қайта текширилади; Telegram'и уланган мижозга тегилмайди
                db.execute("UPDATE clients SET phone=?, tg_status='', tg_user_id=NULL WHERE id=?", (phone, lid))
        if old_hash == h and not force:
            st["same"] += 1
            continue
        db.execute("DELETE FROM client_debts WHERE client_id=? AND onec=1", (lid,))
        db.execute("DELETE FROM client_payments WHERE client_id=? AND onec=1", (lid,))
        debts, pays = [], []
        for (d, tm, no, kind, amt, iddoc, ln) in moves:
            title = DOC_NAMES.get(kind, "Документ 1С")
            if amt < 0:
                debts.append((lid, d.isoformat(), d.isoformat(), no, title, "materials", -amt, ts, "USD"))
            elif amt > 0:
                pays.append((lid, d.isoformat(), amt, "transfer", f"1С: {title} № {no}", ts, "USD", amt, 0.0))
        db.executemany("INSERT INTO client_debts(client_id,doc_date,due_date,order_no,description,kind,amount,created_at,is_demo,currency,onec) "
                       "VALUES(?,?,?,?,?,?,?,?,0,?,1)", debts)
        db.executemany("INSERT INTO client_payments(client_id,pay_date,amount,method,note,created_at,is_demo,currency,usd_part,uzs_part,onec) "
                       "VALUES(?,?,?,?,?,?,0,?,?,?,1)", pays)
        db.execute("UPDATE clients SET onec_hash=? WHERE id=?", (h, lid))
        if loc:
            st["updated"] += 1
    _mark_seen(db, mv, linked_ids={r["onec_code"]: r["id"] for r in A.q("SELECT id, onec_code FROM clients WHERE onec_code LIKE '1c77:%'")})
    db.commit()
    A.set_settings(c1_sync_at=A.now(), c1_sync_result=f"новых клиентов {st['new']}, обновлено {st['updated']}, без изменений {st['same']}")
    _auto["synced"] = _t.time()
    return st

# ---------------------------------------------------------------- уведомления о новых документах 1С
DOC_MSG = {
    "ru": "Здравствуйте, {name}!\n{doc_text}\n{balance_text}\n{company}",
    "uz": "Assalomu alaykum, {name}!\n{doc_text}\n{balance_text}\n{company}",
}
DOC_WORDS = {"ru": dict(inv="Накладная №", of="от", more="и ещё", pos="поз.", taken="Товары и услуги", ret="Возврат товара", paid="Приход в кассу", back="Возвращено вам из кассы", total_in="Итого приход", l_ret="возврат", l_kusok="ваш кусок продан, зачтено вам", total="итого", down="Долг уменьшился на", up="Долг увеличился на",
                   tr_out="Перевод долга: ваш долг {amt} переведён на счёт {who}.", tr_in="Перевод долга: долг {who} ({amt}) переведён на ваш счёт."),
             "uz": dict(inv="Nakladnoy №", of="", more="yana", pos="ta", taken="Tovar va xizmatlar", ret="Qaytarilgan tovar", paid="Kassaga kirim", back="Kassadan sizga qaytarildi", total_in="Jami kirim", l_ret="qaytarildi", l_kusok="sizning kusokingiz sotildi, hisobingizga o'tdi", total="jami", down="Qarzingiz kamaydi:", up="Qarzingiz oshdi:",
                   tr_out="Qarz o'tkazildi: {amt} qarzingiz {who} hisobiga o'tkazildi.", tr_in="Qarz o'tkazildi: {who} hisobidagi {amt} qarz sizning hisobingizga o'tkazildi.")}

FIX_WORDS = {
    "ru": dict(head="Приносим извинения, произошла ошибка:", inv="накладная №", pay="приход в кассу №", of="от",
               txt="была ошибочно записана на вас и отменена.", tail="Правильные данные:"),
    "uz": dict(head="Uzr so'raymiz, xatolik bo'ldi:", inv="nakladnoy №", pay="kassaga kirim №", of="",
               txt="sizga adashib yozilgan edi, bekor qilindi.", tail="To'g'ri ma'lumot:"),
}

def fix_message(client, rows):
    """«Узр, накладная № 6218 от 14.10 ($34.22) ошибочно записана на вас и отменена. Ваша задолженность…»"""
    lang = A.lang_of(client)
    w = FIX_WORDS.get(lang, FIX_WORDS["ru"])
    lines = []
    for r in sorted(rows, key=lambda r: (r["doc_date"] or "", r["iddoc"])):
        dd = (r["doc_date"] or "")[8:10] + "." + (r["doc_date"] or "")[5:7] + "." + (r["doc_date"] or "")[:4] if r["doc_date"] else ""
        no = (r["doc_no"] if "doc_no" in r.keys() else "") or ""
        what = (w["inv"] if r["kind"] == 210 else w["pay"]) + (f" {no}" if no else "")
        what += (f" {w['of']} {dd}" if w["of"] else f" {dd}") if dd else ""
        amt = f" ({A.usd(abs(r['amt']))})" if r["amt"] and abs(r["amt"]) >= 0.01 else ""
        lines.append(f"• {what.strip().capitalize()}{amt} {w['txt']}")
    tpl = A.get_setting(f"msg_c_doc_{lang}") or DOC_MSG.get(lang, DOC_MSG["ru"])
    b = A.client_usd_balance(client["id"])
    return A.fill(tpl, {"name": client["name"], "doc_text": w["head"] + "\n" + "\n".join(lines) + "\n\n" + w["tail"], "date": A.ddmm(date.today()),
                        "balance_text": A.bal_phrase(b, lang, after_payment=False),
                        "company": A.get_setting("client_company_name") or A.get_setting("company_name"), "phone": A.get_setting("contact_phone")})

def notify_fixes(now_ts=None):
    """Адашиб хабар кетган ҳужжатлар бўйича узр хабари (бир мижозга бир хабар)."""
    db = A.db()
    try:
        rows = A.q("SELECT * FROM c1_seen WHERE gone=1 AND gone_n>=2")   # камида икки марта (кейинги ўқишда ҳам) топилмаган
    except Exception:
        return 0
    if not rows:
        return 0
    on = A.get_setting("c1_doc_notify", "1") == "1"
    only = {int(x) for x in re.findall(r"\d+", A.get_setting("c1_doc_only", "") or "")}
    import datetime as _dt
    delay = A.parse_float(A.get_setting("c1_doc_delay", "10"), 10)
    now_dt = now_ts or _dt.datetime.now()
    # 1С'да ҳужжат қайта проводка қилинаётганда бир лаҳза йўқолиши мумкин — бир неча дақиқа кутамиз
    rows = [r for r in rows if (now_dt - _dt.datetime.fromisoformat(r["seen_at"].replace(" ", "T")[:19])).total_seconds() >= delay * 60]
    by_client = defaultdict(list)
    for r in rows:
        by_client[r["client_id"]].append(r)
    sent = 0
    for lid, rs in by_client.items():
        c = A.q("SELECT * FROM clients WHERE id=?", (lid,), one=True) if lid else None
        paused = c and c["sms_paused_until"] and c["sms_paused_until"] >= date.today().isoformat()
        if on and c and c["active"] and not paused and (not only or lid in only) and ("c1_doc" not in c.keys() or c["c1_doc"]):
            try:
                A.deliver("invoice", fix_message(c, rs), client=c, commit=False)
                sent += 1
            except Exception:
                A.errlog.exception("1C fix notify %s", lid)
        db.executemany("UPDATE c1_seen SET gone=2 WHERE iddoc=? AND cid=?", [(r["iddoc"], r["cid"]) for r in rs])
    db.commit()
    return sent

def _mark_seen(db, mv, linked_ids):
    """Запоминает документы 1С по клиентам. Первый запуск — всё до сегодняшнего дня считается известным (без рассылки),
    сегодняшние документы уйдут клиентам."""
    db.execute("CREATE TABLE IF NOT EXISTS c1_seen(iddoc TEXT NOT NULL, cid TEXT NOT NULL, client_id INTEGER, doc_date TEXT, kind INTEGER, "
               "amt REAL NOT NULL DEFAULT 0, seen_at TEXT, notified INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(iddoc, cid))")
    cols = {r[1] for r in db.execute("PRAGMA table_info(c1_seen)")}
    if "doc_no" not in cols:
        db.execute("ALTER TABLE c1_seen ADD COLUMN doc_no TEXT DEFAULT ''")
    if "gone" not in cols:   # 0 — ҳужжат жойида, 1 — бошқа мижозга ўтди/ўчирилди (узр хабари кутмоқда), 2 — хабар юборилди
        db.execute("ALTER TABLE c1_seen ADD COLUMN gone INTEGER NOT NULL DEFAULT 0")
    if "gone_n" not in cols:   # нечта кетма-кет ўқишда ҳужжат топилмади (1С вақтинча чала ўқилса — адашмаслик учун)
        db.execute("ALTER TABLE c1_seen ADD COLUMN gone_n INTEGER NOT NULL DEFAULT 0")
    if "sent" not in cols:   # мижозга шу ҳужжат ҳақида хабар ҳақиқатан кетган
        db.execute("ALTER TABLE c1_seen ADD COLUMN sent INTEGER NOT NULL DEFAULT 0")
        # хабарлар 13.10.2026 дан бошланган — ундан олдингилари юборилмаган (биринчи ишга туширишда «маълум» деб белгиланган)
        db.execute("UPDATE c1_seen SET sent=1 WHERE notified=1 AND doc_date>='2026-10-13' AND kind IN (210,1195)")
    first = A.get_setting("c1_seen_init", "0") != "1"
    have = {(r[0], r[1]) for r in db.execute("SELECT iddoc, cid FROM c1_seen")}
    ts = A.now()
    for cid, moves in mv.items():
        lid = linked_ids.get("1c77:" + cid)
        per_doc = defaultdict(float)
        meta = {}
        for (d, tm, no, kind, amt, iddoc, ln) in moves:
            per_doc[iddoc] += amt
            meta[iddoc] = (d, kind, no)
        for iddoc, amt in per_doc.items():
            if (iddoc, cid) in have:
                continue
            d, kind, no = meta[iddoc]
            db.execute("INSERT OR IGNORE INTO c1_seen(iddoc,cid,client_id,doc_date,kind,amt,seen_at,notified,doc_no) VALUES(?,?,?,?,?,?,?,?,?)",
                       (iddoc, cid, lid, d.isoformat(), kind, round(amt, 2), ts, 1 if first and d < date.today() else 0, str(no or "").strip()))
    # хабар юборилган ҳужжат кейин бошқа мижозга ўтказилса ёки ўчирилса — эски мижозга узр хабари
    if mv:
        now_pairs = {(iddoc, cid) for cid, moves in mv.items() for (_, _, _, _, _, iddoc, _) in moves}
        since = (date.today() - timedelta(days=int(A.parse_float(A.get_setting("c1_fix_days", "90"), 90)))).isoformat()
        rows = db.execute("SELECT iddoc, cid, gone FROM c1_seen WHERE sent=1 AND kind IN (210,1195) AND doc_date>=?", (since,)).fetchall()
        new_gone = [r for r in rows if (r[0], r[1]) not in now_pairs and r[2] == 0]
        # бир ўқишда кўп ҳужжат бирдан «йўқолса» — бу 1С файли чала ўқилгани (тармоқ узилиши ва ҳ.к.), ўчирилгани эмас
        limit = int(A.parse_float(A.get_setting("c1_fix_max", "5"), 5))
        if len(new_gone) > limit:
            A.errlog.warning("1C: бирдан %s та ҳужжат топилмади — ўқиш хатоси деб ҳисобланди, узр хабари тайёрланмади", len(new_gone))
            new_gone = []
        for r in new_gone:
            db.execute("UPDATE c1_seen SET gone=1, gone_n=1, seen_at=? WHERE iddoc=? AND cid=?", (ts, r[0], r[1]))
        for r in rows:
            here = (r[0], r[1]) in now_pairs
            if here and r[2] == 1:
                db.execute("UPDATE c1_seen SET gone=0, gone_n=0 WHERE iddoc=? AND cid=?", (r[0], r[1]))
            elif not here and r[2] == 1:
                db.execute("UPDATE c1_seen SET gone_n=gone_n+1 WHERE iddoc=? AND cid=? AND gone_n<99", (r[0], r[1]))
    if first:
        A.set_settings(commit=False, c1_seen_init="1")

def _fmt_uzs(v):
    return f"{v:,.0f}".replace(",", " ") + " сум" if v else ""

def _doc_amounts(rows):
    """Суммы для сообщения: накладные — сум и $ из шапки 1С; оплаты — сколько сумами и сколько долларами, плюс итог в $."""
    sale_h = {h["IDDOC"]: h for h in table("DH210")}
    pay_l = defaultdict(list)     # (документ, клиент) -> [(сум, $)] по строкам
    heads_1195 = {}
    if any(r["kind"] == 1195 for r in rows):
        heads_1195 = {h["IDDOC"]: h for h in table("DH1195")}
        for r in table("DT1195"):
            pay_l[(r["IDDOC"], r["SP1198"])].append((r["SP1202"] or 0, r["SP1199"] or 0))
    pf_ids = sorted({int(str(r["iddoc"])[3:]) for r in rows if str(r["iddoc"]).startswith("pf:")})
    if pf_ids:                    # платформа ҳужжатлари — 1С шапкаси кўринишида
        con = _pf_con()
        ph = ",".join("?" * len(pf_ids))
        for d in con.execute(f"SELECT id, kind, usd, uzs, pay_usd, pay_uzs, rate FROM erp_docs WHERE id IN ({ph})", pf_ids):
            if d["kind"] == "sale":
                sale_h[f"pf:{d['id']}"] = {"SP559": d["uzs"] or 0, "SP905": d["usd"] or 0, "SP1164": d["pay_uzs"] or 0, "SP1163": d["pay_usd"] or 0}
            else:
                heads_1195[f"pf:{d['id']}"] = {"SP1247": d["rate"] or 0}
        for l in con.execute(f"SELECT doc_id, client, COALESCE(uzs,0) uzs, COALESCE(usd,0) usd FROM erp_lines WHERE doc_id IN ({ph}) AND COALESCE(client,'')<>''", pf_ids):
            pay_l[(f"pf:{l['doc_id']}", l["client"])].append((l["uzs"], l["usd"]))
    taken = [0.0, 0.0]            # сум, $ (одна и та же сумма в двух валютах)
    own_accts = None
    transfers = []                # қарз ўтказиш: (бошқа мижоз, $ — плюс: қарзингиз унга ўтди, минус: унинг қарзи сизга ўтди)
    paid = [0.0, 0.0, 0.0, 0.0, 0.0]   # приход сумами, приход $, итог в $, возврат сумами, возврат $ (минус в кассе = вернули клиенту)
    def add_pay(u, d):
        if u > 0: paid[0] += u
        elif u < 0: paid[3] += -u
        if d > 0: paid[1] += d
        elif d < 0: paid[4] += -d
    for r in rows:
        if r["kind"] == 210 and r["iddoc"] in sale_h:
            h = sale_h[r["iddoc"]]
            taken[0] += h["SP559"] or 0; taken[1] += h["SP905"] or 0
            # оплата прямо в накладной: СуммаОплатыСум и СуммаОплатыДол; в долг ушло только остальное
            pu, pd = h.get("SP1164") or 0, h.get("SP1163") or 0      # СуммаОплатыСум1 (SP1244) — не наличные сумы, не берём
            pt = (h["SP905"] or 0) + r["amt"]
            if abs(pt) >= 0.005 or pu or pd:
                add_pay(pu, pd); paid[2] += pt
        elif r["kind"] == 1195:
            mine = pay_l.get((r["iddoc"], r["cid"]), [])
            # қарз ўтказиш: шу ҳужжатда бошқа мижозга айнан тескари сумма (бирига плюс, иккинчисига минус) — касса орқали пул юрмаган
            used = set()
            cl = None
            if own_accts is None:
                # ўзимизнинг ҳисоблар (карталар, «Поступление в счёт», хабарномаси ўчирилганлар) — уларга ўтказма бу оддий тўлов
                own_accts = {(x["onec_code"] or "")[5:] for x in A.q("SELECT onec_code FROM clients WHERE onec_code LIKE '1c77:%' AND (sms_enabled=0 OR active=0)")}
                own_accts |= {k for k, v in clients_pf().items() if (v.get("name") or "").strip().lower().startswith("карта")}
            for u, d in mine:
                hit = None
                if abs(u) >= 1 or abs(d) >= 0.01:
                    for k, v in pay_l.items():
                        if k[0] != r["iddoc"] or k[1] == r["cid"] or k[1] in own_accts:
                            continue
                        for n, (ou, od) in enumerate(v):
                            if (k, n) not in used and abs(u + ou) < 1 and abs(d + od) < 0.005:
                                hit = (k, n); break
                        if hit:
                            break
                if hit:
                    used.add(hit)
                    cl = cl or clients_pf()
                    rate = (heads_1195.get(r["iddoc"], {}).get("SP1247") or 0) or A.fx_rate() or 12000
                    transfers.append((cl.get(hit[0][1], {}).get("name", "?").strip(), d + u / rate))
                else:
                    add_pay(u, d)
            paid[2] += r["amt"]
    _doc_amounts.transfers = transfers
    return taken, paid

def doc_message(client, rows, brief=False):
    """«Товары и услуги: $153.53. Оплачено: $100 + 640 000 сум (итого $153.64). Долг уменьшился на $0.11. Ваша задолженность…»
    Товар — итог в $; оплата — в той валюте, в которой дали; итог и изменение долга — в $."""
    lang = A.lang_of(client)
    w = DOC_WORDS.get(lang, DOC_WORDS["ru"])
    taken, paid = _doc_amounts(rows)
    parts = []
    # каждая накладная — с товарами и услугами по названиям
    MAX_LINES = 25
    for r in sorted((r for r in rows if r["kind"] == 210), key=lambda r: r["iddoc"]):
        try:
            sd = sale_doc(r["iddoc"])
        except Exception:
            sd = None
        if not sd or not sd["lines"]:
            continue
        dd = sd["doc"]["date"].strftime("%d.%m.%Y") if sd["doc"].get("date") else ""
        head = f"{w['inv']} {sd['doc']['no']}" + (f" {w['of']} {dd}".replace("  ", " ") if dd else "") + ":"
        if brief:                                   # товарлар рўйхати расмда — матнда фақат сарлавҳа
            parts.append(head[:-1].replace("№ ", "№").strip() + " — " + A.usd(sum(l["usd"] or 0 for l in sd["lines"])) + ".")
            continue
        ls = []
        for l in sd["lines"][:MAX_LINES]:
            q = f"{l['qty']:g}" if isinstance(l["qty"], (int, float)) else str(l["qty"])
            note = ""
            if (l["usd"] or 0) < 0 or (isinstance(l["qty"], (int, float)) and l["qty"] < 0):
                note = f" ({w['l_kusok'] if 'кусок' in l['name'].lower() else w['l_ret']})"
            ls.append(f"• {l['name']} — {q} {l['unit']} — {A.usd(l['usd'])}{note}".replace("  ", " "))
        if len(sd["lines"]) > MAX_LINES:
            ls.append(f"• … {w['more']} {len(sd['lines']) - MAX_LINES} {w['pos']}")
        parts.append(head + "\n" + "\n".join(ls) + "\n")
    t_usd = taken[1]
    if abs(t_usd) >= 0.01:
        parts.append(f"{w['ret'] if t_usd < 0 else w['taken']}: {A.usd(abs(t_usd))}.")
    tr_sum = sum(a for _, a in getattr(_doc_amounts, "transfers", []))
    for who, amt in getattr(_doc_amounts, "transfers", []):
        parts.append((w["tr_out"] if amt > 0 else w["tr_in"]).format(amt=A.usd(abs(amt)), who=who))
    pu, pd, pt, bu, bd = paid
    def money(u, d):
        return " + ".join(x for x in ((A.usd(d) if d >= 0.01 else ""), (_fmt_uzs(u) if u >= 1 else "")) if x)
    if pu >= 1 or pd >= 0.01:
        parts.append(f"{w['paid']}: {money(pu, pd)}.")
    if bu >= 1 or bd >= 0.01:
        parts.append(f"{w['back']}: {money(bu, bd)}.")
    if (pu >= 1 or pd >= 0.01 or bu >= 1 or bd >= 0.01) and (pu >= 1 or bu >= 1 or (pd >= 0.01 and bd >= 0.01)):
        parts.append(f"{w['total_in']}: {A.usd(pt)}.")
    elif not (pu >= 1 or pd >= 0.01 or bu >= 1 or bd >= 0.01) and abs(pt - tr_sum) >= 0.01:
        parts.append(f"{w['paid']}: {A.usd(pt - tr_sum)}.")
    change = round(pt - t_usd, 2)           # + долг уменьшился, − увеличился
    if abs(change) >= 0.01:
        parts.append(f"{w['down'] if change > 0 else w['up']} {A.usd(abs(change))}.")
    tpl = A.get_setting(f"msg_c_doc_{lang}") or DOC_MSG.get(lang, DOC_MSG["ru"])
    b = A.client_usd_balance(client["id"])
    return A.fill(tpl, {"name": client["name"], "doc_text": "\n".join(parts).replace("\n\n\n", "\n\n"), "date": A.ddmm(date.today()),
                        "balance_text": A.bal_phrase(b, lang, after_payment=False),
                        "company": A.get_setting("client_company_name") or A.get_setting("company_name"), "phone": A.get_setting("contact_phone")})

NAKL_ADDR = {"ru": "Ташкент, ул. Жомий, 5", "uz": "Toshkent, Jomiy ko'chasi, 5"}


def invoice_image(iddoc, client=None, out_dir=None):
    """Накладнойни расм қилиб чизиш (naklimg). -> файл йўли ёки None (хато бўлса — матн юборилади)."""
    try:
        import naklimg, pdfgen
        sd = sale_doc(iddoc)
        if not sd or not sd["lines"]:
            return None
        cid = sd["head"]["SP232"] if sd.get("head") else sd.get("cid")
        prev = after = None
        if cid:
            mv = client_moves_all(cid)
            idx = [i for i, m in enumerate(mv) if m["iddoc"] == iddoc]
            if idx:
                after = -mv[idx[-1]]["balance"]
                prev = -mv[idx[0] - 1]["balance"] if idx[0] else 0.0
        lang = A.lang_of(client) if client else "ru"
        lang = lang if lang in naklimg.T else "ru"
        logo = None
        ui = A.ui_settings()
        if ui.get("logo"):
            p = A.ui_logo_path(ui["logo"])
            im = pdfgen.load_image(p)
            if im:
                logo = dict(path=p, w=im["w"], h=im["h"])
        cph = (client["phone"] if client and client["phone"] else "") or _phone(sd["client"].get("phones", ""))
        rate = sd["head"].get("SP1132") if sd.get("head") else sd.get("rate")
        d = sd["doc"].get("date")
        out_dir = out_dir or os.path.join(A.DATA_DIR, "nakl")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{str(iddoc).strip().replace(':', '_')}_{lang}.png")      # «pf:12» — Windows'да «:» бўлмайди
        naklimg.render(sd, path, prev=prev, after=after, lang=lang, phone=A.get_setting("contact_phone") or "+998 33 409-60-70",
                       address=A.get_setting(f"nakl_address_{lang}") or NAKL_ADDR.get(lang, ""), logo=logo,
                       client_phone=cph, rate=rate or None, rate_date=d.strftime("%d.%m.%Y") if d else "",
                       company=(A.get_setting("nakl_company") or "Lumberture"))
        _cleanup_nakl(out_dir)
        return path if os.path.exists(path) else None
    except Exception:
        A.errlog.exception("invoice image %s", iddoc)
        return None


def _cleanup_nakl(d, days=45):
    import time as _t
    lim = _t.time() - days * 86400
    try:
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if os.path.getmtime(p) < lim:
                os.remove(p)
    except OSError:
        pass


def nakl_test():
    """Созлама nakl_test=<рақам> — шу накладнойни data/nakl/test_<рақам>.png қилиб чизиш (текшириш учун)."""
    no = (A.get_setting("nakl_test", "") or "").strip()
    if not no:
        return
    A.set_settings(nakl_test="")
    for v in journal().values():
        if v["kind"] == 210 and str(v["no"]).strip() == no and not v.get("deleted"):
            lang = A.get_setting("nakl_test_lang", "") or "ru"
            p = invoice_image(v["id"], dict(phone="", lang=lang, id=0, name=""), os.path.join(A.DATA_DIR, "nakl_test"))
            A.errlog.warning("nakl test %s -> %s", no, p)
            return


def notify_new_docs(now_ts=None):
    """Новая накладная / оплата в 1С → клиенту одно сообщение: взял, оплатил, осталось.
    Ждём c1_doc_delay минут после последнего документа, чтобы накладная и оплата ушли одним сообщением."""
    import datetime as _dt
    db = A.db()
    try:
        rows = A.q("SELECT * FROM c1_seen WHERE notified=0")
    except Exception:
        return 0
    if not rows:
        return notify_fixes(now_ts)
    on = A.get_setting("c1_doc_notify", "1") == "1"
    delay = A.parse_float(A.get_setting("c1_doc_delay", "10"), 10)
    now_dt = now_ts or _dt.datetime.now()
    recent = (date.today() - timedelta(days=2)).isoformat()
    # свет/компьютер ўчиб қолган бўлса — ўчган кундан бошлаб (энг кўпи 14 кун) юборилмаганларни юборамиз
    cf, ca = A.get_setting("c1_catchup_from", ""), A.parse_float(A.get_setting("c1_catchup_at", "0"), 0)
    if cf and _dt.datetime.now().timestamp() - ca < 12 * 3600:
        recent = min(recent, max(cf, (date.today() - timedelta(days=14)).isoformat()))
    only = {int(x) for x in re.findall(r"\d+", A.get_setting("c1_doc_only", "") or "")}
    by_client = defaultdict(list)
    for r in rows:
        by_client[r["client_id"]].append(r)
    sent = 0
    for lid, rs in by_client.items():
        last = max(_dt.datetime.fromisoformat(r["seen_at"].replace(" ", "T")[:19]) for r in rs)
        if on and (now_dt - last).total_seconds() < delay * 60:
            continue                                    # ждём, вдруг следом оплата
        ids = [(r["iddoc"], r["cid"]) for r in rs]
        c = A.q("SELECT * FROM clients WHERE id=?", (lid,), one=True) if lid else None
        fresh = [r for r in rs if (r["doc_date"] or "") >= recent and r["kind"] in (210, 1195)]
        paused = c and c["sms_paused_until"] and c["sms_paused_until"] >= date.today().isoformat()
        allowed = not only or lid in only                 # синов: фақат танланган мижозларга
        if on and allowed and c and c["active"] and fresh and not paused and ("c1_doc" not in c.keys() or c["c1_doc"]):
            try:
                imgs = []
                if A.get_setting("c1_doc_image", "1") == "1":
                    imgs = [invoice_image(r["iddoc"], c) for r in sorted((r for r in fresh if r["kind"] == 210), key=lambda r: r["iddoc"])]
                ok = bool(imgs) and all(imgs)
                A.deliver("invoice", doc_message(c, fresh, brief=ok), client=c, commit=False, file=imgs[0] if ok else "")
                invs = sorted((r for r in fresh if r["kind"] == 210), key=lambda r: r["iddoc"])
                for r, p in (list(zip(invs, imgs))[1:] if ok else []):   # бир нечта накладной — қолганлари алоҳида расм
                    w = DOC_WORDS.get(A.lang_of(c), DOC_WORDS["ru"])
                    A.deliver("invoice", f"{w['inv']}{str(r['doc_no'] or '').strip()}", client=c, commit=False, file=p)
                sent += 1
                db.executemany("UPDATE c1_seen SET sent=1 WHERE iddoc=? AND cid=?", [(r["iddoc"], r["cid"]) for r in fresh])
            except Exception:
                A.errlog.exception("1C doc notify %s", lid)
        db.executemany("UPDATE c1_seen SET notified=1 WHERE iddoc=? AND cid=?", ids)
    db.commit()
    return sent + notify_fixes(now_ts)

def _has_notif():
    try:
        return "client_id" in {r[1] for r in A.db().execute("PRAGMA table_info(notifications)")}
    except Exception:
        return False

def sync_if_due(max_age=300):
    import time as _t
    if _t.time() - _auto.get("synced", 0) < max_age:
        return None
    try:
        return sync_clients()
    except Exception:
        A.errlog.exception("1C sync")
        _auto["synced"] = _t.time()
        return None


def apply_patch_file():
    """Разовые правки базы без остановки сервера: data/patch_once.sql выполняется и переименовывается в patch_done_<время>.sql
    (так правки делает поддержка; файл базы при работе сервера не трогаем)."""
    folder = os.path.dirname(os.path.abspath(A.DB_PATH))
    p = os.path.join(folder, "patch_once.sql")
    if not os.path.isfile(p):
        return
    import time as _t
    stamp = _t.strftime("%Y%m%d_%H%M%S")
    try:
        sql = open(p, encoding="utf-8").read()
        db = A.db()
        db.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
        os.replace(p, os.path.join(folder, f"patch_done_{stamp}.sql"))
        A.errlog.warning("patch_once.sql applied")
    except Exception as e:
        try:
            A.db().rollback()
        except Exception:
            pass
        A.errlog.exception("patch_once.sql failed")
        try:
            os.replace(p, os.path.join(folder, f"patch_failed_{stamp}.sql"))
            open(os.path.join(folder, f"patch_failed_{stamp}.txt"), "w", encoding="utf-8").write(repr(e))
        except OSError:
            pass


FETCH_HOSTS = ("cdn.creativeclaw.co",)


def apply_fetch_file():
    """Разовая загрузка файлов поддержкой: data/fetch_once.json [{"url", "name"}] → data/fetch/<name>
    (только https и разрешённые хосты; файл переименовывается в fetch_done_<время>.json). Работает в отдельном потоке."""
    folder = os.path.dirname(os.path.abspath(A.DB_PATH))
    p = os.path.join(folder, "fetch_once.json")
    if not os.path.isfile(p):
        return
    import time as _t, json as _j, threading as _th
    from urllib.parse import urlparse
    stamp = _t.strftime("%Y%m%d_%H%M%S")
    done = os.path.join(folder, f"fetch_done_{stamp}.json")
    try:
        os.replace(p, done)
        items = _j.load(open(done, encoding="utf-8"))
    except Exception:
        A.errlog.exception("fetch_once.json")
        return

    def run():
        import requests
        out = os.path.join(folder, "fetch")
        os.makedirs(out, exist_ok=True)
        ok = 0
        for it in items if isinstance(items, list) else []:
            url, name = str(it.get("url") or ""), os.path.basename(str(it.get("name") or ""))
            u = urlparse(url)
            if u.scheme != "https" or u.hostname not in FETCH_HOSTS or not re.fullmatch(r"[\w.-]{1,80}", name or ""):
                continue
            try:
                r = requests.get(url, timeout=(10, 120))
                r.raise_for_status()
                with open(os.path.join(out, name), "wb") as fh:
                    fh.write(r.content)
                ok += 1
            except Exception as e:
                A.errlog.warning("fetch %s: %s", name, e)
        A.errlog.warning("fetch_once: %s / %s", ok, len(items) if isinstance(items, list) else 0)
    _th.Thread(target=run, daemon=True, name="fetch_once").start()


# ================================================================ маршруты
def register(app_module):
    global A
    A = app_module
    app, lr, ar = A.app, A.login_required, A.admin_required
    con = A.connect()
    cols = {r[1] for r in con.execute("PRAGMA table_info(clients)")}
    if "onec_hash" not in cols:
        con.execute("ALTER TABLE clients ADD COLUMN onec_hash TEXT")
    if "c1_doc" not in cols:
        con.execute("ALTER TABLE clients ADD COLUMN c1_doc INTEGER NOT NULL DEFAULT 1")
    if "tg_chat" not in cols:
        con.execute("ALTER TABLE clients ADD COLUMN tg_chat TEXT DEFAULT ''")     # группа Telegram вместо лички
    if "chat" not in {r[1] for r in con.execute("PRAGMA table_info(tg_out)")}:
        con.execute("ALTER TABLE tg_out ADD COLUMN chat TEXT DEFAULT ''")
    con.commit(); con.close()
    _orig_reminders = A.run_client_reminders
    def run_client_reminders_1c(today=None):
        """Перед плановыми напоминаниями — свежие долги из 1С."""
        try:
            sync_clients()
        except Exception:
            A.errlog.exception("1C sync before reminders")
        return _orig_reminders(today)
    A.run_client_reminders = run_client_reminders_1c
    # шаблон «новый документ 1С» — редактируется в Настройках вместе с остальными
    for lang, t in DOC_MSG.items():
        A.DEFAULT_MSGS.setdefault(lang, {})["c_doc"] = t
        A.DEFAULT_TEMPLATES[f"msg_c_doc_{lang}"] = t
    A.TPL_KINDS["c_doc"] = ("Клиентам: новая накладная / оплата в 1С", "{name} {doc_text} {balance_text} {date} {company} {phone}")
    _orig_tick = A.scheduler_tick
    def scheduler_tick_1c(dt=None):
        """Каждую минуту: свежие данные 1С (раз в 3 мин.) и сообщения о новых накладных/оплатах."""
        res = _orig_tick(dt)
        apply_patch_file()
        try:
            apply_fetch_file()
        except Exception:
            A.errlog.exception("fetch_once")
        try:
            A.tgu_profile_once()
            import tgstickers
            tgstickers.run(A)
        except Exception:
            A.errlog.exception("tg profile tick")
        try:
            nakl_test()
        except Exception:
            A.errlog.exception("nakl test")
        try:
            if base_path() and A.get_setting("c1_doc_notify", "1") == "1":
                sync_if_due(180)
                notify_new_docs()
        except Exception:
            A.errlog.exception("1C doc notify tick")
        return res
    A.scheduler_tick = scheduler_tick_1c

    def period_args():
        today = date.today()
        d1 = A.parse_date(request.args.get("d1")) or today.replace(day=1)
        d2 = A.parse_date(request.args.get("d2")) or today
        return d1, d2

    def safe(fn):
        try:
            return fn(), None
        except FileNotFoundError as e:
            return None, str(e)
        except HTTPException:
            raise
        except Exception as e:
            A.errlog.exception("1C read")
            return None, f"Ошибка чтения базы 1С: {e}"

    @app.route("/1c")
    @app.route("/1c/clients")
    @lr
    def c1_clients():
        show = request.args.get("show", "debtors")
        qs = (request.args.get("q") or "").strip().casefold()
        def load():
            sync_if_due()
            cl, mv = clients(), all_moves()
            loc = {r["onec_code"]: r for r in A.q("SELECT * FROM clients WHERE onec_code LIKE '1c77:%'")}
            rows, tt, tp, tb, nd = [], 0.0, 0.0, 0.0, 0
            try:
                import erp as _e
                km = _e.ckind_map()
            except Exception:
                km = {}
            other = {"partner": 0.0, "account": 0.0, "supplier": 0.0}
            for cid, moves in mv.items():
                c = cl.get(cid)
                if not c or c["folder"]:
                    continue
                taken, paid, bal = totals(moves)
                kind = km.get(cid, "client")
                if bal < -0.005:
                    if kind in other:                     # шерик / ҳисоб / завод — мижозлар қарзига қўшилмайди
                        other[kind] += -bal
                    else:
                        nd += 1; tb += -bal
                if show == "debtors" and bal > -0.5:
                    continue
                if show == "advance" and bal < 0.5:
                    continue
                if qs and not kb.matches(qs, c["name"] + " " + c["full"] + " " + c["phones"]):
                    continue
                lc = loc.get("1c77:" + cid)
                days = A.client_overdue(lc["id"])[0] if lc and bal < -0.5 else 0
                rows.append(dict(c=c, taken=taken, paid=paid, bal=bal, lc=lc, days=days, last=moves[-1][0] if moves else None, kind=kind,
                                 msg=(A.messages_for(client_id=lc["id"], limit=1) or [None])[0] if lc else None))
                tt += taken; tp += paid
            rows.sort(key=lambda r: (r["bal"], r["c"]["name"]))
            return dict(rows=rows, tot_taken=tt, tot_paid=tp, tot_debt=tb, n_debt=nd, other=other, tot_all=tb + sum(other.values()),
                        sync_at=A.get_setting("c1_sync_at", ""), sync_res=A.get_setting("c1_sync_result", ""))
        data, err = safe(load)
        return render_template("c1_clients.html", d=data, err=err, show=show, q=request.args.get("q", ""), nxt=A.next_sms_run(),
                               remind_on=A.get_setting("remind_clients") == "1")

    @app.route("/1c/sync", methods=["POST"])
    @lr
    def c1_sync():
        try:
            st = sync_clients(force=bool(request.form.get("force")))
            flash(f"Данные из 1С обновлены: новых клиентов {st['new']}, изменилось {st['updated']}", "ok")
        except Exception as e:
            flash(f"1С: {e}", "error")
        return redirect(url_for("c1_settings") if request.form.get("back") == "settings" else url_for("c1_clients"))

    @app.route("/1c/client/<cid>")
    @lr
    def c1_client(cid):
        def load():
            c = clients().get(cid)
            if not c:
                abort(404)
            moves = client_moves(cid)
            return dict(c=c, moves=list(reversed(moves))[:400], bal=moves[-1]["balance"] if moves else 0.0, n=len(moves))
        data, err = safe(load)
        return render_template("c1_client.html", d=data, err=err, DOC_NAMES=DOC_NAMES)

    @app.route("/1c/sales")
    @lr
    def c1_sales():
        d1, d2 = period_args()
        data, err = safe(lambda: sales(d1, d2))
        tot = dict(usd=sum(x["usd"] for x in data or [] if x["doc"]["posted"]), uzs=sum(x["uzs"] for x in data or [] if x["doc"]["posted"]))
        return render_template("c1_docs.html", kind="sales", rows=data, err=err, d1=d1, d2=d2, tot=tot)

    @app.route("/1c/sale/<iddoc>")
    @lr
    def c1_sale(iddoc):
        data, err = safe(lambda: sale_doc(iddoc))
        if not err and not data:
            abort(404)
        return render_template("c1_sale.html", s=data, err=err, printable=request.args.get("print") == "1")

    @app.route("/1c/sale/<iddoc>/print")
    @lr
    def c1_sale_print(iddoc):
        form = request.args.get("form") if request.args.get("form") in PRINT_FORMS else "full"
        s, err = safe(lambda: sale_doc(iddoc))
        if err:
            return err, 500
        if not s:
            abort(404)
        d = s["doc"]["date"]
        s["date_text"] = f"{d.day} {MONTHS_GEN[d.month]} {d.year} г." if d else ""
        s["qty_total"] = sum(l["qty"] for l in s["lines"])
        return render_template("c1_print.html", s=s, form=form, titles=PRINT_FORMS)

    @app.route("/1c/receipts")
    @lr
    def c1_receipts():
        d1, d2 = period_args()
        data, err = safe(lambda: receipts(d1, d2))
        tot = dict(qty=sum(x["qty"] for x in data or [] if x["doc"]["posted"]), amount=sum(x["amount"] for x in data or [] if x["doc"]["posted"]))
        return render_template("c1_docs.html", kind="receipts", rows=data, err=err, d1=d1, d2=d2, tot=tot)

    @app.route("/1c/payments")
    @lr
    def c1_payments():
        d1, d2 = period_args()
        def load():
            return dict(rows=payments(d1, d2), cash=cash())
        data, err = safe(load)
        rows = data["rows"] if data else []
        tot = dict(usd=sum(x["usd"] for x in rows if x["doc"]["posted"]), uzs=sum(x["uzs"] for x in rows if x["doc"]["posted"]),
                   to_debt=sum(x["to_debt"] for x in rows if x["doc"]["posted"]))
        return render_template("c1_docs.html", kind="payments", rows=rows, err=err, d1=d1, d2=d2, tot=tot, cashbal=data["cash"] if data else None)

    @app.route("/1c/stock")
    @lr
    def c1_stock():
        qs = (request.args.get("q") or "").strip().casefold()
        show = request.args.get("show", "instock")
        def load():
            per, st = stock()
            g = goods()
            rows = []
            for it in g.values():
                if it["folder"]:
                    continue
                q_ = round(st.get(it["id"], 0.0), 3)
                if show == "instock" and q_ <= 0.0005:
                    continue
                if show == "minus" and q_ >= -0.0005:
                    continue
                grp = folder_path(g, it["parent"])
                if qs and not kb.matches(qs, it["name"] + " " + grp + " " + it["code"]):
                    continue
                rows.append(dict(g=it, qty=q_, group=grp, value=q_ * (it["price"] or 0)))
            rows.sort(key=lambda r: (r["group"], r["g"]["name"]))
            return dict(per=per, rows=rows[:3000], n=len(rows), value=sum(r["value"] for r in rows if r["qty"] > 0),
                        n_pos=sum(1 for v in st.values() if v > 0.0005), n_neg=sum(1 for v in st.values() if v < -0.0005))
        data, err = safe(load)
        return render_template("c1_stock.html", d=data, err=err, q=request.args.get("q", ""), show=show)

    @app.route("/1c/settings", methods=["GET", "POST"])
    @ar
    def c1_settings():
        if request.method == "POST":
            p = (request.form.get("c1_path") or "").strip()
            A.set_settings(commit=False, c1_doc_notify="1" if request.form.get("c1_doc_notify") else "0",
                           c1_doc_only=",".join(re.findall(r"\d+", request.form.get("c1_doc_only") or "")),
                           c1_doc_delay=str(int(min(max(A.parse_float(request.form.get("c1_doc_delay"), 10), 0), 120))))
            on = "1" if request.form.get("c1_new_remind") else "0"
            was = A.get_setting("c1_new_remind", "0")
            A.set_settings(c1_path=p, c1_new_remind=on)
            if on != was:  # включить/выключить напоминания всем клиентам из 1С разом
                A.db().execute("UPDATE clients SET sms_enabled=? WHERE onec_code LIKE '1c77:%'", (int(on),))
                A.db().commit()
            with _lock:
                _cache.clear()
            flash("Настройки 1С сохранены", "ok")
            return redirect(url_for("c1_settings"))
        info, err = None, None
        try:
            names = os.listdir(base_path())
            if "1Cv7.DD" not in names and "1CV7.DD" not in names:
                err = "В этой папке нет базы 1С 7.7 (файла 1Cv7.DD)"
            else:
                per, bal = debts()
                info = dict(clients=sum(1 for c in clients().values() if not c["folder"]), goods=sum(1 for g in goods().values() if not g["folder"]),
                            sales=sum(1 for d in journal().values() if d["kind"] == 210), per=per,
                            last=max((d["date"] for d in journal().values() if d["date"]), default=None))
        except OSError as e:
            err = f"Папка недоступна: {base_path()} ({e.strerror or e})"
        except Exception as e:
            err = f"Ошибка чтения: {e}"
        return render_template("c1_settings.html", path=base_path(), info=info, err=err, default=DEFAULT_PATH,
                               saved=(A.get_setting("c1_path", "") or "").strip(), candidates=CANDIDATES,
                               remind=A.get_setting("c1_new_remind", "0") == "1",
                               doc_notify=A.get_setting("c1_doc_notify", "1") == "1", doc_delay=A.get_setting("c1_doc_delay", "10"),
                               doc_only=A.get_setting("c1_doc_only", ""),
                               doc_only_names=[r["name"] for r in A.q("SELECT name FROM clients WHERE id IN (%s)" % ",".join(
                                   str(int(x)) for x in re.findall(r"\d+", A.get_setting("c1_doc_only", "") or "")) or "0")], sync_at=A.get_setting("c1_sync_at", ""),
                               sync_res=A.get_setting("c1_sync_result", ""),
                               n_local=A.scalar("SELECT COUNT(*) FROM clients WHERE onec_code LIKE '1c77:%'") or 0)
