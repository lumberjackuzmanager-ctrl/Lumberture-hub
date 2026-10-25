# -*- coding: utf-8 -*-
"""«Буюртма тайёр»: цех ишчилари платформа Telegram-аккаунтига накладной расмини «тайёр» деб юборади →
платформа накладнойни аниқлайди (ёзилган рақам ёки расмдан ўқиб) → мижозга «буюртмангиз тайёр» матни (расмсиз).

Фақат рўйхатдаги ишчилар номеридан келган хабарлар ўқилади, бошқа чатлар умуман очилмайди."""
import os, re, json, time, random, threading, subprocess, logging
from datetime import date, datetime, timedelta

from flask import render_template, request, redirect, url_for, flash, abort, send_file

A = None
log = logging.getLogger("telegram")

READY_RE = re.compile(r"та[йи]\s*[ёеэо]р|tay+[oa]r|готов|tayor", re.I)
NUM_RE = re.compile(r"(?<![\d.,])(\d{3,6})(?![\d.,])")
NO_RE = re.compile(r"(?:№|N[oо°º]?\s*[.:]?)\s*(\d{3,6})(?!\d)", re.I)
NAKL_RE = re.compile(r"(?:наклад|nakl|hakл)\w*\s*(?:№|N\S?)?\s*(\d{3,6})(?!\d)", re.I)
QR_RE = re.compile(r"QR:LB:(\d{1,8}):(\d{8})")                                # накладнойдаги QR: LB:<№>:<ЙЙЙЙООКК>
OT_RE = re.compile(r"(?<![\d.,])(\d{3,6})\s+(?:от|ot)\s+\d", re.I)          # «6192 от 14 Октября 2026»
MONTHS_RU = ["", "январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]
CANCEL_RE = re.compile(r"бекор|bekor|хато|xato|отмен|ошиб|cancel", re.I)
PAIR_SEC = 300
PHOTO_ALONE_SEC = 120          # расм ва «тайёр» алоҳида юборилса — 5 дақиқа ичида жуфтлаймиз
POLL_SEC = 30

MSG = {"ru": "Здравствуйте, {name}!\n\nВаш заказ от {date} готов ✅\nНакладная №{no}\n\nМожете забирать. Ждём вас!{balance_text}\n{company}",
       "uz": "Assalomu alaykum, {name}!\n\n{date} kuni bergan buyurtmangiz tayyor ✅\nNakladnoy №{no}\n\nOlib ketishingiz mumkin. Sizni kutamiz!{balance_text}\n{company}"}
ACK = {
    "sent": "✅ №{no} — «{client}» га «буюртма тайёр» хабари {when}.\nАдашган бўлса, шу хабарга «бекор» деб жавоб ёзинг.",
    "dup": "ℹ️ №{no} — «{client}» га бу хабар аввал юборилган ({at}). Қайта юборилмади.",
    "nodoc": "⚠️ №{no} накладной 1С'да топилмади. Рақамни текшириб, расм тагига ёзиб қайта юборинг (масалан: «{no} тайёр»).",
    "unknown": "❓ Накладной рақамини аниқлай олмадим. Расм тагига рақамини ёзиб қайта юборинг (масалан: «6218 тайёр»).",
    "many": "❓ Бир нечта рақам чиқди: {nos}. Қайси бири эканини ёзиб юборинг (масалан: «{first} тайёр»).",
    "noclient": "⚠️ №{no} накладнойнинг мижози ({client}) платформада йўқ ёки ўчирилган — хабар юборилмади.",
    "own": "⚠️ №{no} — «{client}» ҳисобига хабарлар ўчирилган — хабар юборилмади.",
    "nochannel": "⚠️ №{no} — «{client}»га юбориб бўлмади: {why}",
    "off": "",
}
_state = {"last": 0.0, "pending": {}, "lock": threading.Lock()}


def init_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS ready_workers(id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL UNIQUE,
        user_id INTEGER, active INTEGER NOT NULL DEFAULT 1, last_id INTEGER NOT NULL DEFAULT 0, error TEXT DEFAULT '', created_at TEXT);
    CREATE TABLE IF NOT EXISTS ready_log(id INTEGER PRIMARY KEY, at TEXT, worker_id INTEGER, msg_id INTEGER, text TEXT DEFAULT '',
        photo TEXT DEFAULT '', ocr TEXT DEFAULT '', doc_no TEXT DEFAULT '', iddoc TEXT DEFAULT '', client_id INTEGER,
        status TEXT DEFAULT '', info TEXT DEFAULT '');
    CREATE INDEX IF NOT EXISTS ix_ready_doc ON ready_log(iddoc, status);
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(tg_out)")}
    for col, ddl in (("file", "TEXT DEFAULT ''"), ("out_msg_id", "INTEGER"), ("out_peer", "TEXT DEFAULT ''")):
        if col not in cols:
            con.execute(f"ALTER TABLE tg_out ADD COLUMN {col} {ddl}")
    cols = {r[1] for r in con.execute("PRAGMA table_info(ready_log)")}
    for col, ddl in (("out_id", "INTEGER"), ("ack_id", "INTEGER")):
        if col not in cols:
            con.execute(f"ALTER TABLE ready_log ADD COLUMN {col} {ddl}")


def photo_dir():
    d = os.path.join(A.DATA_DIR, "ready")
    os.makedirs(d, exist_ok=True)
    return d


def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    if len(d) == 9:
        d = "998" + d
    return d if len(d) == 12 and d.startswith("998") else ""


# ---------------------------------------------------------------- OCR (Windows.Media.Ocr, Windows 10 ичида бор)
def qr_codes(path):
    """Расмдаги QR кодлар (накладнойга босилган «LB:№:сана»). zxing-cpp бўлса — у, бўлмаса OpenCV. -> [матн]"""
    out = []
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except Exception:
        return out
    try:
        import zxingcpp
        for b in zxingcpp.read_barcodes(im):
            if b.text:
                out.append(b.text)
        if out:
            return out
    except ImportError:
        pass
    except Exception as e:
        A.errlog.warning("zxing: %r", e)
    try:
        import cv2, numpy as np
        arr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
        det = cv2.QRCodeDetector()
        h, w = arr.shape[:2]
        gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        sharp = cv2.addWeighted(gray, 1.8, cv2.GaussianBlur(gray, (0, 0), 3), -0.8, 0)
        thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
        tries = [(arr, 1.0), (arr, 0.5), (arr, 1.6), (sharp, 1.0), (sharp, 1.6), (thr, 1.0), (thr, 0.6)]
        for src, sc in tries:                              # телефон расми катта/кичик, хира бўлиши мумкин
            img = src if sc == 1.0 else cv2.resize(src, (int(w * sc), int(h * sc)))
            try:
                ok, texts, _, _ = det.detectAndDecodeMulti(img)
            except Exception:
                ok, texts = False, []
            texts = [t for t in (texts or []) if t] if ok else []
            if not texts:
                t, _, _ = det.detectAndDecode(img)
                texts = [t] if t else []
            if texts:
                return texts
    except ImportError:
        pass
    except Exception as e:
        A.errlog.warning("qr cv2: %r", e)
    return out


def ocr_image(path):
    """Расмдаги матн: аввал QR (накладнойда бор бўлса — тез ва аниқ), кейин Windows OCR (PowerShell орқали);
    бошқа жойда — RENT_OCR_CMD (тестлар учун). QR топилса — «QR:LB:№:сана» қатори биринчи туради."""
    qr = [t for t in qr_codes(path) if t.startswith("LB:")] if path and os.path.isfile(path) else []
    if qr:
        return "\n".join("QR:" + t for t in qr)
    cmd = os.environ.get("RENT_OCR_CMD")
    if cmd:
        args = cmd.split() + [path]
    elif os.name == "nt":
        ps1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr.ps1")
        args = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps1, path]
    else:
        return ""
    try:
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}       # CREATE_NO_WINDOW
        r = subprocess.run(args, capture_output=True, timeout=90, **kw)
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace").strip()
        if r.returncode or err:
            A.errlog.error("OCR rc=%s: %s", r.returncode, err[:800])
        if not out.strip() and err:
            return "#error " + err[:600]
        return out.strip()
    except Exception as e:
        A.errlog.error("OCR: %r", e)
        return ""


# ---------------------------------------------------------------- 1С: накладной рақами → ҳужжат → мижоз
def find_doc(no, days=180, on=None):
    """№ бўйича энг янги (охирги `days` кун) ўчирилмаган накладной; on — аниқ сана (QR'дан). -> dict(iddoc, no, date) | None"""
    key = str(no).lstrip("0")
    since = date.today() - timedelta(days=days)
    best = None
    try:                                                  # 1С архиви (26.10.2026 дан янги накладной фақат платформада)
        import c1
        jr = list(c1.journal().values())
    except Exception:
        jr = []
    for r in A.q("SELECT id, no, date, time FROM erp_docs WHERE src='pf' AND kind='sale' AND status<>'deleted' AND date>=?", (since.isoformat(),)):
        jr.append(dict(id=f"pf:{r['id']}", kind=210, deleted=False, no=str(r["no"] or ""), date=date.fromisoformat(r["date"][:10]), time=r["time"] or ""))
    for v in jr:
        if v["kind"] != 210 or v["deleted"] or not v["date"] or v["date"] < since:
            continue
        if on and v["date"] != on:
            continue
        if (v["no"] or "").lstrip("0") == key and (best is None or (v["date"], v["time"] or "") > (best["date"], best["time"] or "")):
            best = v
    return dict(iddoc=best["id"], no=best["no"], date=best["date"]) if best else None


def doc_client(iddoc):
    """-> (1С мижоз номи, платформа clients қатори | None)"""
    if str(iddoc or "").startswith("pf:"):              # платформа накладнойи: мижоз — erp_clients (хабарнома ёзуви «1c77:<ext>»)
        r = A.q("SELECT d.client, COALESCE(c.name,'?') cname FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.id=?",
                (int(str(iddoc)[3:]),), one=True)
        if not r or not r["client"]:
            return "?", None
        return r["cname"], A.q("SELECT * FROM clients WHERE onec_code=?", ("1c77:" + r["client"],), one=True)
    import c1
    h = next((x for x in c1.table("DH210") if x["IDDOC"] == iddoc), None)
    if not h:
        return "?", None
    cid = h["SP232"]
    name = c1.clients().get(cid, {}).get("name", "?")
    c = A.q("SELECT * FROM clients WHERE onec_code=?", ("1c77:" + cid,), one=True)
    return name, c


def pick_doc(text, ocr):
    """-> (doc|None, status, info). status: ok | nodoc | unknown | many"""
    nums = list(dict.fromkeys(NUM_RE.findall(text or "")))
    if nums:                                              # ишчи рақам ёзган — шунга ишонамиз
        found = [(n, find_doc(n)) for n in nums]
        ok = [(n, d) for n, d in found if d]
        if len(ok) == 1:
            return ok[0][1], "ok", "рақам ёзилган"
        if not ok:
            return None, "nodoc", nums[0]
        return None, "many", ", ".join(n for n, _ in ok)
    if not ocr:
        return None, "unknown", "расмдан матн ўқилмади"
    for no, ymd in QR_RE.findall(ocr):                    # QR — рақам ва сана аниқ
        try:
            dd = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
        except ValueError:
            continue
        d = find_doc(no, days=400, on=dd) or find_doc(no, days=60)
        if d:
            return d, "ok", "QR орқали"
        return None, "nodoc", no
    ocr = "\n".join(l for l in ocr.splitlines() if not l.startswith("#"))
    pri = list(dict.fromkeys(NAKL_RE.findall(ocr) + OT_RE.findall(ocr) + NO_RE.findall(ocr)))
    allnums = list(dict.fromkeys(NUM_RE.findall(ocr)))
    for group, why in ((pri, "расмдан (№ ёнида)"), (allnums, "расмдан")):
        ok = []
        for n in group:
            d = find_doc(n, days=60)
            if d and d["iddoc"] not in {x["iddoc"] for x in ok}:
                ok.append(d)
        if len(ok) == 1:
            return ok[0], "ok", why
        if len(ok) > 1:                                   # расмдаги сана ёки мижоз номи бўйича танлаймиз
            low = ocr.casefold()
            def date_in(d):
                dt = d["date"]
                return (dt.strftime("%d.%m.%Y") in ocr or dt.strftime("%d.%m.%y") in ocr or
                        re.search(rf"(?<!\d)0?{dt.day}\s+{MONTHS_RU[dt.month]}", low) is not None)
            for test, how in ((date_in, "сана бўйича"),
                              (lambda d: (doc_client(d["iddoc"])[0] or "?").casefold()[:8] in low, "мижоз номи бўйича")):
                hit = [d for d in ok if test(d)]
                if len(hit) == 1:
                    return hit[0], "ok", why + ", " + how
            return None, "many", ", ".join(d["no"] for d in ok[:5])
    return None, "unknown", "расмда накладной рақами топилмади"


def client_text(c, doc):
    lang = A.lang_of(c)
    tpl = A.get_setting(f"msg_c_ready_{lang}") or MSG.get(lang, MSG["ru"])
    b = A.client_usd_balance(c["id"])            # қарзи бўлса — қарздорлик ҳақида бир қатор
    bal = ("\n\n" + A.bal_phrase(b, lang)) if b >= 1 else ""
    return A.fill(tpl, {"name": c["name"], "no": doc["no"], "date": doc["date"].strftime("%d.%m.%Y"), "balance_text": bal,
                        "company": A.get_setting("client_company_name") or A.get_setting("company_name"),
                        "phone": A.get_setting("contact_phone")})


def handle(item):
    """Битта «тайёр» хабари. -> (status, ack_text). Журналга ёзади, мижозга навбатга қўяди."""
    w = item["worker"]
    photo = item["photos"][0] if item["photos"] else ""
    ocr = ""
    if not NUM_RE.search(item["text"]) and photo:
        ocr = ocr_image(photo)
    doc, st, info = pick_doc(item["text"], ocr)
    client, c = ("", None)
    ins = dict(at=A.now(), worker_id=w["id"], msg_id=item["msg_id"], text=item["text"][:500], photo=photo, ocr=ocr[:4000])
    if doc:
        client, c = doc_client(doc["iddoc"])
        ins.update(doc_no=doc["no"], iddoc=doc["iddoc"], client_id=c["id"] if c else None)
        prev = A.q("SELECT at FROM ready_log WHERE iddoc=? AND status='sent' ORDER BY id DESC LIMIT 1", (doc["iddoc"],), one=True)
        if prev:
            st, info = "dup", prev["at"]
        elif not c or not c["active"]:
            st = "noclient"
        elif not c["sms_enabled"]:
            st = "own"
        else:
            ch, why = A.deliver("ready", client_text(c, doc), client=c, commit=False)   # мижозга фақат матн, расм — журналда
            if ch:
                st, info = "sent", f"{info}; канал: {ch}"
                if ch == "tg":
                    oid = A.scalar("SELECT MAX(id) FROM tg_out WHERE kind='ready' AND client_id=?", (c["id"],))
                    ins["out_id"] = oid
                    dl = delay_min()
                    if dl and oid:                         # бекор қилиш учун бир неча дақиқа кутамиз
                        A.db().execute("UPDATE tg_out SET next_try=? WHERE id=?", (time.time() + dl * 60, oid))
            else:
                st, info = "nochannel", why
    ins.update(status=st, info=str(info)[:500])
    item["log_id"] = A.db().execute("INSERT INTO ready_log(" + ",".join(ins) + ") VALUES(" + ",".join("?" * len(ins)) + ")",
                                    tuple(ins.values())).lastrowid
    A.db().commit()
    no = doc["no"] if doc else (info if st == "nodoc" else "")
    nos = info if st == "many" else ""
    when = eta_text(ins.get("out_id")) if ins.get("out_id") else "юборилди"
    ack = ACK.get(st, "").format(no=no, client=(c["name"] if c else client), at=info if st == "dup" else "", why=info, when=when,
                                 nos=nos, first=nos.split(",")[0].strip() if nos else "")
    return st, ack


def eta_text(oid, now=None):
    """Хабар қачон кетишини тахминлаш: «бекор» учун кутиш + олдиндаги «тайёр» хабарлар × хабарлар орасидаги танаффус
    (Telegram аккаунт спамга тушмаслиги учун 2–5 дақиқа) + иш соатлари (8:00–21:00)."""
    import math
    from datetime import datetime, timedelta
    now = now or time.time()
    r = A.db().execute("SELECT next_try FROM tg_out WHERE id=?", (oid,)).fetchone()
    start = max(float(r["next_try"] or 0) if r else 0, now, A.parse_float(A.get_setting("tgu_next_at"), 0))
    ahead = A.scalar("SELECT COUNT(*) FROM tg_out WHERE status='pending' AND kind='ready' AND id<?", (oid,)) or 0
    a, b = A.tgu_delays()
    eta = start + ahead * (a + b) / 2
    h1 = int(A.parse_float(A.get_setting("tgu_hour_from"), 8))
    h2 = int(A.parse_float(A.get_setting("tgu_hour_to"), 21)) or 24
    d = datetime.fromtimestamp(eta)
    if not (h1 <= d.hour < h2):
        day = d if d.hour < h1 else d + timedelta(days=1)
        return f"эртага соат {h1}:00 дан кейин юборилади" if day.date() > datetime.fromtimestamp(now).date() else f"соат {h1}:00 дан кейин юборилади"
    mins = max(1, math.ceil((eta - now) / 60))
    q = f" (навбатда {ahead + 1}-чи)" if ahead else ""
    return f"тахминан {mins} дақиқада юборилади{q}"


def delay_min():
    return max(0, int(A.parse_float(A.get_setting("ready_delay_min", "5"), 5)))


async def _delete(c, o, client):
    """Мижоз чатидан хабарни «ҳамма учун» ўчириш. -> True/False"""
    import tguser
    ent = None
    if o["out_peer"]:
        try:
            ent = await c.get_input_entity(int(o["out_peer"]))
        except Exception:
            ent = None
    if ent is None and o["chat"]:
        chat = o["chat"].strip()
        if re.fullmatch(r"-?\d+", chat):
            ent = await c.get_input_entity(int(chat))
        elif chat.startswith("@") or "t.me/" in chat:
            ent = await c.get_input_entity(chat)
        else:
            async for d in c.iter_dialogs(limit=500):
                if (d.name or "").strip().casefold() == chat.casefold():
                    ent = d.entity
                    break
    if ent is None and client is not None and client["tg_user_id"]:
        try:
            ent = await c.get_input_entity(int(client["tg_user_id"]))
        except Exception:
            ent = None
    if ent is None:
        return False
    ids = [o["out_msg_id"]] if o["out_msg_id"] else []
    if not ids:                                            # эски хабар (id сақланмаган) — матни бўйича қидирамиз
        want = (o["text"] or "").strip()
        for m in await c.get_messages(ent, limit=60):
            if m.out and (m.raw_text or "").strip() == want:
                ids.append(m.id)
                break
    if not ids:
        return False
    await c.delete_messages(ent, ids, revoke=True)
    return True


def cancel(lid, by=""):
    """«Буюртма тайёр» хабарини бекор қилиш: навбатда бўлса — юборилмайди, кетган бўлса — мижоз чатидан ўчирилади.
    -> (ok, матн)"""
    r = A.q("SELECT * FROM ready_log WHERE id=?", (lid,), one=True)
    if not r or r["status"] != "sent":
        return False, "бу хабар юборилмаган ёки аввал бекор қилинган"
    o = A.q("SELECT * FROM tg_out WHERE id=?", (r["out_id"],), one=True) if r["out_id"] else None
    if o is None and r["client_id"]:
        o = A.q("SELECT * FROM tg_out WHERE kind='ready' AND client_id=? AND text LIKE ? ORDER BY id DESC LIMIT 1",
                (r["client_id"], f"%№{r['doc_no']}%"), one=True)
    c = A.q("SELECT * FROM clients WHERE id=?", (r["client_id"],), one=True) if r["client_id"] else None
    if o is None:
        return False, "мижозга кетган хабар топилмади (SMS бўлиши мумкин)"
    if o["status"] == "pending":
        A.db().execute("UPDATE tg_out SET status='cancelled', error='бекор қилинди' WHERE id=?", (o["id"],))
        res, txt = True, "мижозга юборилмади (навбатдан олинди)"
    elif o["status"] == "sent":
        api_id, api_hash = A.tgu_cfg()
        try:
            res = bool(A.tgu_sender().call(api_id, api_hash, lambda cl: _delete(cl, o, c)))
        except Exception as e:
            A.errlog.error("ready cancel: %r", e)
            res = False
        txt = "мижоз чатидан ўчирилди" if res else "мижоз чатидан ўчириб бўлмади — мижозга ўзингиз ёзинг"
        if res:
            A.db().execute("UPDATE tg_out SET status='cancelled', error='мижоздан ўчирилди' WHERE id=?", (o["id"],))
    else:
        return False, "хабар ҳолати: " + (o["status"] or "?")
    if res:
        A.db().execute("UPDATE ready_log SET status='cancelled', info=? WHERE id=?", (f"бекор қилинди{(' — ' + by) if by else ''}: {txt}", lid))
    A.db().commit()
    return res, txt


def _cancel_from_worker(m):
    """Ишчи «бекор» деб ёзди (жавоб сифатида ёки рақам билан, ёки охирги хабарига). -> ack матни"""
    wid = m["worker"]["id"]
    r = None
    if m.get("reply_to"):
        r = A.q("SELECT * FROM ready_log WHERE worker_id=? AND (msg_id=? OR ack_id=?) ORDER BY id DESC LIMIT 1",
                (wid, m["reply_to"], m["reply_to"]), one=True)
    nums = NUM_RE.findall(m["text"])
    if r is None and nums:
        r = A.q("SELECT * FROM ready_log WHERE status='sent' AND doc_no=? ORDER BY id DESC LIMIT 1", (nums[0],), one=True)
    if r is None:
        since = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        r = A.q("SELECT * FROM ready_log WHERE worker_id=? AND status='sent' AND at>=? ORDER BY id DESC LIMIT 1", (wid, since), one=True)
    if r is None:
        return "❓ Бекор қилинадиган хабар топилмади. «✅ юборилди» жавобига «бекор» деб reply қилинг."
    ok, txt = cancel(r["id"], by=m["worker"].get("name") or "ишчи")
    return (f"🗑 №{r['doc_no']} — {txt}." if ok else f"⚠️ №{r['doc_no']} — {txt}.")


def reprocess(lid):
    """Журналдаги хабарни қайта ишлаш (масалан, OCR тузатилгандан кейин). -> (status, ack, worker user_id, msg_id)"""
    r = A.q("SELECT * FROM ready_log WHERE id=?", (lid,), one=True)
    if not r:
        return None
    w = A.q("SELECT * FROM ready_workers WHERE id=?", (r["worker_id"],), one=True)
    item = dict(worker=dict(w) if w else {"id": r["worker_id"], "name": ""}, ent=None, msg_id=r["msg_id"], date=time.time(),
                text=r["text"] or "тайёр", photos=[r["photo"]] if r["photo"] and os.path.isfile(r["photo"]) else [], grouped=None)
    st, ack = handle(item)
    A.db().execute("UPDATE ready_log SET status='redo', info=? WHERE id=?", (f"қайта ўқилди → {st}", lid))
    A.db().commit()
    return st, ack, (w["user_id"] if w else None), r["msg_id"]


def _reprocess_pending():
    """Созлама ready_reprocess=1 (ёки саҳифадаги тугма) — бугунги аниқланмаган хабарларни қайта ўқиш ва ишчига жавоб."""
    if A.get_setting("ready_reprocess", "") != "1":
        return []
    A.set_settings(ready_reprocess="")
    since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    out = []
    for r in A.q("SELECT id FROM ready_log WHERE status IN ('unknown','many','error') AND photo<>'' AND at>=? ORDER BY id", (since,)):
        res = reprocess(r["id"])
        if res and res[1] and res[2]:
            out.append((int(res[2]), res[3], res[1]))
    return out


def _retry_1c():
    """1С очилмай қолган пайтдаги «тайёр» хабарлар — 1С қайта ўқилса, ўзи юборилади (24 соат ичида)."""
    since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows = A.q("SELECT id FROM ready_log WHERE status='retry1c' AND at>=? ORDER BY id", (since,))
    if not rows:
        return []
    try:
        import c1
        c1.journal()
    except Exception:
        return []                                         # ҳали ҳам очилмаяпти
    out = []
    for r in rows:
        try:
            res = reprocess(r["id"])
        except Exception:
            A.errlog.exception("ready retry1c")
            continue
        if res and res[1] and res[2]:
            out.append((int(res[2]), res[3], res[1]))
    return out


async def _reply_ids(c, replies):
    for uid, msg_id, text in replies:
        try:
            ent = await c.get_input_entity(uid)
            await c.send_message(ent, text, reply_to=msg_id)
        except Exception as e:
            log.warning("ready reply: %r", e)


# ---------------------------------------------------------------- Telegram: рўйхатдаги ишчилардан янги хабарлар
async def _fetch(c, workers, ddir):
    from telethon import functions, types
    out, upd = [], {}
    if not await c.is_user_authorized():
        return out, upd
    for w in workers:
        u = upd.setdefault(w["id"], {})
        ent = None
        if w["user_id"]:
            try:
                ent = await c.get_input_entity(int(w["user_id"]))
            except Exception:
                ent = None
        if ent is None:
            res = await c(functions.contacts.ImportContactsRequest([types.InputPhoneContact(
                client_id=random.randrange(1, 2 ** 62), phone="+" + w["phone"], first_name=(w["name"] or w["phone"])[:60], last_name="")]))
            if not res.users:
                u["error"] = "бу рақамда Telegram топилмади"
                continue
            ent = await c.get_input_entity(res.users[0])
            u["user_id"], u["error"] = res.users[0].id, ""
        if not w["last_id"]:                               # биринчи марта — эски хабарларни ўқимаймиз
            m = await c.get_messages(ent, limit=1)
            u["last_id"] = m[0].id if m else 1
            continue
        msgs = await c.get_messages(ent, min_id=w["last_id"], limit=100)
        mx = w["last_id"]
        for m in sorted(msgs, key=lambda m: m.id):
            mx = max(mx, m.id)
            if m.out:
                continue
            path = None
            is_img = bool(m.photo) or bool(m.document and (getattr(m.file, "mime_type", "") or "").startswith("image/"))
            if is_img:
                path = await m.download_media(file=os.path.join(ddir, f"{date.today():%Y%m%d}_{w['id']}_{m.id}"))
            out.append(dict(worker=dict(w), ent=ent, msg_id=m.id, date=m.date.timestamp(), text=m.raw_text or "", photo=path,
                            grouped=m.grouped_id, reply_to=getattr(m, "reply_to_msg_id", None)))
        u["last_id"] = mx
    return out, upd


async def _reply(c, replies):
    ids = {}
    for ent, msg_id, text, *rest in replies:
        try:
            sm = await c.send_message(ent, text, reply_to=msg_id)
            if rest and rest[0] and getattr(sm, "id", None):
                ids[rest[0]] = sm.id
        except Exception as e:
            log.warning("ready reply: %r", e)
    return ids


def _items(msgs):
    """Хабарларни «расм(лар) + матн» бирликларига йиғиш (альбом, расм кейин алоҳида «тайёр»)."""
    pend = _state["pending"]
    items = []
    for m in sorted(msgs, key=lambda m: (m["worker"]["id"], m["msg_id"])):
        wid = m["worker"]["id"]
        last = items[-1] if items and items[-1]["worker"]["id"] == wid else None
        if m["photo"]:
            if last and m["grouped"] and last.get("grouped") == m["grouped"]:
                last["photos"].append(m["photo"])
                last["text"] = (last["text"] + " " + m["text"]).strip()
                continue
            items.append(dict(worker=m["worker"], ent=m["ent"], msg_id=m["msg_id"], date=m["date"], text=m["text"], photos=[m["photo"]],
                              grouped=m["grouped"]))
            continue
        if not m["text"].strip():
            continue
        base = last if last and last["photos"] and not READY_RE.search(last["text"]) else None
        if base is None and wid in pend and m["date"] - pend[wid]["date"] <= PAIR_SEC:
            base = pend.pop(wid)
            items.append(base)
        if base is not None and m["date"] - base["date"] <= PAIR_SEC:
            base["text"] = (base["text"] + " " + m["text"]).strip()
            base["msg_id"] = m["msg_id"]
            continue
        items.append(dict(worker=m["worker"], ent=m["ent"], msg_id=m["msg_id"], date=m["date"], text=m["text"], photos=[], grouped=None))
    ready = []
    for it in items:
        # «тайёр» сўзи бор, ёки расм тагига фақат рақам ёзилган («6222») — ишчилар фақат тайёр буюртмани юборади
        if READY_RE.search(it["text"]) or (it["photos"] and NUM_RE.search(it["text"])):
            ready.append(it)
        elif it["photos"]:
            pend[it["worker"]["id"]] = it                  # «тайёр» кейинроқ келиши мумкин
    now_ts = time.time()
    for k in [k for k, v in pend.items() if now_ts - v["date"] > PHOTO_ALONE_SEC]:
        it = pend.pop(k)                                   # ёзувсиз расм — 2 дақиқадан кейин ҳам «тайёр» деб ишлаймиз
        if not CANCEL_RE.search(it["text"]):
            ready.append(it)
    return ready


def poll(force=False):
    """Фон оқимидан ҳар 30 сек. -> қайта ишланган «тайёр» хабарлар сони."""
    ready_on = A.get_setting("ready_on", "1") == "1"
    if not A.tgu_ready():
        return 0
    if not force and time.time() - _state["last"] < POLL_SEC:
        return 0
    with _state["lock"]:
        _state["last"] = time.time()
        workers = [dict(r) for r in A.q("SELECT * FROM ready_workers WHERE active=1 AND COALESCE(role,'ready')<>'staff'")
                   if ready_on or (dict(r).get("role") or "ready") == "raspil"]
        if not workers:
            return 0
        api_id, api_hash = A.tgu_cfg()
        ddir = photo_dir()
        res = A.tgu_sender().call(api_id, api_hash, lambda c: _fetch(c, workers, ddir))
        if not res:
            return 0
        msgs, upd = res
        for wid, u in upd.items():
            if u:
                A.db().execute("UPDATE ready_workers SET " + ", ".join(f"{k}=?" for k in u) + " WHERE id=?", tuple(u.values()) + (wid,))
        A.db().commit()
        replies = []
        n = 0
        # распилчилар: распил энди фақат бот орқали — аккаунтга келган «тайёр»сиз хабарлари эътиборсиз
        # (ёзувсиз расм «тайёр» бўлиб мижозга кетиб қолмаслиги учун ҳам)
        rmsgs = [m for m in msgs if (m["worker"].get("role") or "ready") == "raspil" and not READY_RE.search(m["text"] or "")]
        if rmsgs:
            msgs = [m for m in msgs if m not in rmsgs]
        cancels = [m for m in msgs if not m["photo"] and CANCEL_RE.search(m["text"]) and not READY_RE.search(m["text"])]
        msgs = [m for m in msgs if m not in cancels]
        for m in cancels:
            try:
                replies.append((m["ent"], m["msg_id"], _cancel_from_worker(m)))
                n += 1
            except Exception:
                A.errlog.exception("ready cancel")
        for it in _items(msgs):
            try:
                st, ack = handle(it)
                n += 1
            except Exception as e:
                A.errlog.exception("ready handle")
                no1c = isinstance(e, FileNotFoundError) or "1С" in str(e)
                st = "retry1c" if no1c else "error"
                ack = ("⏳ 1С базаси ҳозир очилмаяпти. Мижозга хабар 1С уланганда ўзи юборилади." if no1c
                       else "⚠️ Платформада хатолик — админ текширади.")
                try:
                    it["log_id"] = A.db().execute(
                        "INSERT INTO ready_log(at, worker_id, msg_id, text, photo, status, info) VALUES(?,?,?,?,?,?,?)",
                        (A.now(), it["worker"]["id"], it["msg_id"], (it["text"] or "")[:500], (it["photos"] or [""])[0], st, str(e)[:300])).lastrowid
                    A.db().commit()
                except Exception:
                    A.errlog.exception("ready log")
            if ack:
                replies.append((it["ent"], it["msg_id"], ack, it.get("log_id")))
        if replies:
            ids = A.tgu_sender().call(api_id, api_hash, lambda c: _reply(c, replies)) or {}
            for lid, aid in ids.items():
                if isinstance(lid, tuple):
                    import raspil
                    raspil.set_ack(lid[1], aid)
                else:
                    A.db().execute("UPDATE ready_log SET ack_id=? WHERE id=?", (aid, lid))
            A.db().commit()
        redo = _reprocess_pending() + _retry_1c()
        if redo:
            n += len(redo)
            A.tgu_sender().call(api_id, api_hash, lambda c: _reply_ids(c, redo))
        if n:
            A.tg_event.set()
        _cleanup(ddir)
        return n


def _cleanup(ddir, days=45):
    lim = time.time() - days * 86400
    try:
        for f in os.listdir(ddir):
            p = os.path.join(ddir, f)
            if os.path.getmtime(p) < lim:
                os.remove(p)
    except OSError:
        pass


def _loop():
    A._stop.wait(20)
    while not A._stop.is_set():
        try:
            with A.app.app_context():
                poll()
        except Exception:
            A.errlog.exception("ready poll")
        A._stop.wait(POLL_SEC)


# ---------------------------------------------------------------- саҳифа
STATUS = {"sent": ("юборилди", "pos"), "dup": ("аввал юборилган", "muted"), "nodoc": ("накладной топилмади", "neg"),
          "unknown": ("рақам аниқланмади", "neg"), "many": ("бир нечта рақам", "neg"), "noclient": ("мижоз йўқ", "neg"),
          "own": ("хабар ўчирилган ҳисоб", "muted"), "nochannel": ("канал йўқ", "neg"), "error": ("хато", "neg"),
          "redo": ("қайта ўқилди", "muted"), "retry1c": ("1С очилмади — кутилмоқда", "neg"), "cancelled": ("бекор қилинди", "muted"), "nodeliv": ("мижозга етмади", "neg")}

FAIL_WHY = {"no_tg": "мижоз Telegram'да топилмади (рақам нотўғри ёки махфийлик созламаси)",
            "privacy": "мижоз Telegram'и махфийлик созламаси билан ёпиқ", "failed": "Telegram хатоси", "error": "Telegram хатоси"}


def on_tg_fail(o, st, err):
    """«Буюртма тайёр» хабари мижозга етмади → журналда белгилаш, ишчига ва аккаунтнинг «Избранное»сига огоҳлантириш."""
    if o["kind"] != "ready":
        return
    r = A.q("SELECT * FROM ready_log WHERE out_id=? ORDER BY id DESC LIMIT 1", (o["id"],), one=True)
    if not r or r["status"] != "sent":
        return
    w = A.q("SELECT * FROM ready_workers WHERE id=?", (r["worker_id"],), one=True)
    why = FAIL_WHY.get(st, err or st)
    phone = o["phone"] or ""
    ph = f"+{phone[:3]} {phone[3:5]} {phone[5:8]} {phone[8:10]} {phone[10:]}" if len(phone) == 12 else phone
    text = (f"❌ №{r['doc_no']} — «{(o['name'] or '').split('(')[0].strip() or 'мижоз'}» га хабар ЕТМАДИ.\n{why}.\n"
            f"Мижозга қўнғироқ қилинг{(': ' + ph) if ph else ''}.\n"
            f"1С'да рақамини текширинг ёки мижоз карточкасига Telegram'ини (@username) ёзинг.")
    A.db().execute("UPDATE ready_log SET status='nodeliv', info=? WHERE id=?", (f"мижозга етмади: {why}", r["id"]))
    A.db().commit()
    uid, reply_to = (w["user_id"] if w else None), (r["ack_id"] or r["msg_id"])
    api_id, api_hash = A.tgu_cfg()

    async def go(c):
        if uid:
            try:
                await c.send_message(await c.get_input_entity(int(uid)), text, reply_to=reply_to)
            except Exception as e:
                log.warning("ready fail notify worker: %r", e)
        try:
            await c.send_message("me", text + f"\n(ишчи: {w['name'] if w else '?'})")
        except Exception as e:
            log.warning("ready fail notify me: %r", e)
    A.tgu_sender().call(api_id, api_hash, go)


def register(app_module):
    global A
    A = app_module
    app, ar = A.app, A.admin_required
    A.TG_FAIL_HOOKS.append(on_tg_fail)
    con = A.connect()
    init_tables(con)
    con.commit(); con.close()

    _orig_start = A.start_background_jobs

    def start_background_jobs():
        first = not A._bg["started"]
        _orig_start()
        if first:
            threading.Thread(target=_loop, name="ready", daemon=True).start()
    A.start_background_jobs = start_background_jobs

    @app.route("/ready", methods=["GET", "POST"])
    @ar
    def ready_page():
        if request.method == "POST":
            f, act = request.form, request.form.get("action")
            db = A.db()
            if act == "add":
                ph = norm_phone(f.get("phone"))
                if not ph:
                    flash("Рақам нотўғри. Формат: +998 XX XXX XX XX", "error")
                elif A.q("SELECT 1 FROM ready_workers WHERE phone=?", (ph,), one=True):
                    flash("Бу рақам рўйхатда бор", "warn")
                else:
                    db.execute("INSERT INTO ready_workers(name, phone, created_at) VALUES(?,?,?)", ((f.get("name") or "").strip()[:60], ph, A.now()))
                    db.commit()
                    flash("Қўшилди. 30 сониядан кейин платформа шу рақамдан келган хабарларни ўқий бошлайди.", "ok")
            elif act in ("on", "off"):
                db.execute("UPDATE ready_workers SET active=? WHERE id=?", (1 if act == "on" else 0, f.get("id")))
                db.commit()
            elif act == "del":
                db.execute("DELETE FROM ready_workers WHERE id=?", (f.get("id"),))
                db.commit()
                flash("Рўйхатдан олиб ташланди", "ok")
            elif act == "cancel":
                ok, txt = cancel(int(A.parse_float(f.get("lid"), 0)), by=A.current_user()["username"])
                flash(("Бекор қилинди: " if ok else "Бекор қилинмади: ") + txt, "ok" if ok else "error")
            elif act == "redo":
                A.set_settings(ready_reprocess="1")
                flash("Аниқланмаган хабарлар 30 сониядан кейин қайта ўқилади", "ok")
            elif act == "settings":
                A.set_settings(ready_on="1" if f.get("ready_on") else "0",
                               ready_delay_min=str(max(0, min(60, int(A.parse_float(f.get("ready_delay_min"), 5))))))
                flash("Сақланди", "ok")
            elif act == "test":
                up = request.files.get("photo")
                if up and up.filename:
                    p = os.path.join(photo_dir(), f"test_{int(time.time())}.jpg")
                    up.save(p)
                    ocr = ocr_image(p)
                    doc, st, info = pick_doc(f.get("text", ""), ocr)
                    cl = doc_client(doc["iddoc"]) if doc else ("", None)
                    A.set_settings(ready_test=json.dumps(dict(ocr=ocr, st=st, info=str(info), no=doc["no"] if doc else "",
                                                             date=doc["date"].isoformat() if doc else "", client=cl[0]), ensure_ascii=False))
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                    flash("Синов натижаси пастда", "ok")
            return redirect(url_for("ready_page"))
        ws = A.q("SELECT * FROM ready_workers ORDER BY name")
        logs = A.q("SELECT l.*, w.name wname, c.name cname FROM ready_log l LEFT JOIN ready_workers w ON w.id=l.worker_id "
                   "LEFT JOIN clients c ON c.id=l.client_id ORDER BY l.id DESC LIMIT 60")
        try:
            test = json.loads(A.get_setting("ready_test", "") or "null")
        except ValueError:
            test = None
        return render_template("ready.html", ws=ws, logs=logs, on=A.get_setting("ready_on", "1") == "1", STATUS=STATUS, test=test, delay=delay_min(),
                               tgu=A.tgu_ready(), sample=MSG)

    @app.route("/ready/photo/<int:lid>")
    @ar
    def ready_photo(lid):
        r = A.q("SELECT photo FROM ready_log WHERE id=?", (lid,), one=True)
        if not r or not r["photo"] or not os.path.isfile(r["photo"]):
            abort(404)
        if os.path.dirname(os.path.abspath(r["photo"])) != os.path.abspath(photo_dir()):
            abort(404)
        return send_file(r["photo"], mimetype="image/jpeg")
