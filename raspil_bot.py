# -*- coding: utf-8 -*-
"""Распил боти: ишчилар ёзмайди — тугма босади.
  ▶️ Арра / ▶️ Двухпилка → ким ишлайди (тугма) → смена бошланди
  📷 накладной расми → бот нима борлигини кўрсатади → [✅ Кесилди] [➕ ХДФ билан] [📦 Фақат ХДФ] [🧩 Кусок билан] [✂️ Қисман]
  🏁 Иш тугади · 📊 Бугун
Бот алоҳида (@BotFather'дан), токен — созламада (raspil_bot_token). Ишчи биринчи марта рақамини юборади (тугма)."""
import os, re, json, time, threading, logging
from datetime import datetime, date, timedelta

import requests

import tg
import raspil as P

A = None
log = logging.getLogger("telegram")

B_ARRA, B_DP, B_END, B_TODAY, B_WAIT, B_IN = "▶️ Арра маркази", "▶️ Двухпилка", "🏁 Иш тугади", "📊 Бугун", "📝 Накладнойсиз", "✅ Ишга келдим"
B_PRIS, B_SAL = "▶️ Присадка", "💰 Ойлигим"
B_KROMKA, B_OVAL, B_UTOL, B_ROVER = "▶️ Кромка", "▶️ Овал", "▶️ Утолщенка", "▶️ Ровер"
B_OPEN = "📂 Тўхтатилган ишлар"
B_OPEN_OLD = "📂 Очиқ ишлар"                 # эски тугма (ишчиларда қолган клавиатура)
B_SWITCH = "🔄 Станок / бошқа"
BTN_MACHINE = {B_ARRA: "arra", B_DP: "dp", B_PRIS: "pris", B_KROMKA: "kromka", B_OVAL: "oval", B_UTOL: "utol", B_ROVER: "rover"}
# пастда фақат 4 тугма: станоклар, очиқ ишлар, накладнойсиз — «🔄» ичида
B_KUS = "🧩 Кусок омбори"
MAIN_KB = {"keyboard": [[{"text": B_IN}, {"text": B_SWITCH}], [{"text": B_WAIT}, {"text": B_OPEN}], [{"text": B_KUS}, {"text": B_END}],
                        [{"text": B_TODAY}, {"text": B_SAL}]],
           "resize_keyboard": True, "is_persistent": True}
STAFF_KB = {"keyboard": [[{"text": B_IN}, {"text": B_KUS}], [{"text": B_END}, {"text": B_TODAY}], [{"text": B_SAL}]], "resize_keyboard": True, "is_persistent": True}
B_KROY = "📐 Крой қилдим"
# кройчи (ready_workers.kroy=1): ўз кройлари, ойлик/KPI, кусок, тайёр накладнойлар
B_KROYS, B_KPI, B_READY = "📐 Кройларим", "📈 Ойлик / KPI", "✅ Тайёр накладнойлар"
B_READY_OLD = "📋 Буюртмалар ҳолати"
STAFF_KB_K = {"keyboard": [[{"text": B_IN}, {"text": B_KROY}], [{"text": B_KUS}, {"text": B_TODAY}], [{"text": B_END}, {"text": B_SAL}]], "resize_keyboard": True, "is_persistent": True}
STAFF_HELP = ("Қандай ишлайди:\n"
              "1️⃣ Ишга келганда — «✅ Ишга келдим».\n"
              "2️⃣ Кетишда — «🏁 Иш тугади».\n"
              "Табел ўзи тўлади.")


MGR_HELP = ("👔 Сиз менежерсиз.\nЦехдан Ц-код ишлари шу ерга келади — платформада накладнойга киритинг, изоҳига кодни ёзинг.\n«Ц9» ёзсангиз — шу код рўйхати.\n"
            "«🧩 Кусок омбори» — кусокни кўриш ва накладнойга брон қилиш.")


def is_mgr(rw):
    return bool(rw and dict(rw).get("mgr") and dict(rw).get("role") not in ("raspil", "staff"))


def is_owner(rw):
    return bool(rw and dict(rw).get("owner"))


def owner_only(rw):
    """Фақат шерик (ишчи ҳам, менежер ҳам эмас)."""
    return is_owner(rw) and not is_mgr(rw) and dict(rw).get("role") not in ("raspil", "staff")


B_OWN = "📈 Ҳисобот"
OWN_KB = {"keyboard": [[{"text": B_OWN}]], "resize_keyboard": True, "is_persistent": True}


def owner_menu():
    return ikb([[("📊 Бугун", "own:today"), ("💵 Касса", "own:kassa")], [("🏭 Цех", "own:shop"), ("📅 Ой итоги", "own:month")],
                [("📒 Қарздорлар", "own:debt"), ("🚚 Завод", "own:fact")], [("⬅️ Кеча", "own:yday")]])


def owner_view(key):
    import owner as O
    d = date.today()
    try:
        if key == "today":
            return O.today_text(d)
        if key == "yday":
            y = d - timedelta(days=1)
            return O.today_text(y)
        if key == "kassa":
            return O.kassa_text(d)
        if key == "shop":
            return O.shop_text(d)
        if key == "month":
            return O.month_text(d)
        if key == "debt":
            return O.debt_text()
        if key == "fact":
            return O.factory_text(d, short=True)          # ҳозирча фақат итог суммалар
    except Exception:
        A.errlog.exception("owner view")
        return "⚠️ Маълумот олишда хато."
    return "?"


def kb_for(rw):
    kb = _kb_add_lang(_kb_add_pay(_kb_full(rw)))
    return kb


B_PAY = "💳 Payme / Click"
PAY_CARDS = (("payme.png", "Payme", "https://transfer.paycom.uz/69e07fe5bbf621e9322a28a6"),
             ("click.png", "Click", "https://indoor.click.uz/pay?id=085563&t=0"))
PAY_NOTE = "Мижоз QR'ни сканерласин ёки ҳаволани унга юборинг. Пул тўғри кассага тушади."


def _kb_add_pay(kb):
    """«💳 Payme / Click» — ҳамма ролларда (эгаси, 23.10): мижозга тўлов QR'ини кўрсатиш."""
    rows = [list(r) for r in kb.get("keyboard") or []]
    if not rows:
        return kb
    if len(rows[-1]) < 2:
        rows[-1].append({"text": B_PAY})
    else:
        rows.append([{"text": B_PAY}])
    return dict(kb, keyboard=rows)


def send_photo(chat, path, caption=""):
    """Расм юбориш — multipart. -> натижа ёки None."""
    if not token() or not os.path.exists(path):
        return None
    import botlang
    cap = botlang.prepare_params("work", "sendPhoto", {"chat_id": chat, "caption": caption})["caption"] if caption else ""
    try:
        with open(path, "rb") as fh:
            r = requests.post(f"{tg.API_BASE}/bot{token()}/sendPhoto", timeout=(7, 60),
                              data={"chat_id": chat, "caption": cap[:1000]}, files={"photo": (os.path.basename(path), fh, "image/png")})
        j = r.json() or {}
        if not j.get("ok"):
            log.warning("send_photo %s: %s", chat, j.get("description"))
        return j.get("result")
    except (requests.RequestException, ValueError) as e:
        log.warning("send_photo %s: %s", chat, e)
        return None


PAY_WAIT_MIN = 20          # QR кўрсатилгандан кейин шунча дақиқа ичида келган Payme/Click тўлови — шу ишчига хабар


def send_pay(chat, rw):
    """Payme ва Click QR карточкалари (static/pay) + ҳаволалари. Кейинги 20 дақиқада келган тўлов шу ишчининг ботига хабар бўлиб боради."""
    A.db().execute("INSERT INTO raspil_payreq(rw_id, chat_id, at) VALUES(?,?,?)", (rw["id"], chat, A.now()))
    A.db().commit()
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "pay")
    for fn, name, url in PAY_CARDS:
        cap = f"💳 {name}\n{url}" + ("\nUSSD: *880*085563*сумма#" if name == "Click" else "")
        if not send_photo(chat, os.path.join(folder, fn), cap):
            send(chat, cap)
    send(chat, PAY_NOTE, kb_for(rw))


def pay_open(con, at=None):
    """Очиқ QR сўровлари (охирги 20 дақиқа)."""
    t0 = ((at or datetime.now()) - timedelta(minutes=PAY_WAIT_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    return con.execute("SELECT * FROM raspil_payreq WHERE done=0 AND at>=?", (t0,)).fetchall()


def on_epay(con, rows):
    """Янги Payme/Click тўловлари (epay.poll) → QR'ни кўрсатган ишчининг ботига. Битта ишчи кутаётган бўлса — унга ва сўров ёпилади;
    бир вақтда бир нечта ишчи QR кўрсатган бўлса — ҳаммасига (ким учунлигини сумма ва вақтдан билади)."""
    for e in rows:
        if e["status"] != "ok":
            continue
        try:
            pt = datetime.strptime(e["at"] or "", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        reqs = [r for r in pay_open(con, pt) if r["at"] <= (pt + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")]
        seen = set()
        for r in reqs:
            if r["chat_id"] in seen:
                continue
            seen.add(r["chat_id"])
            name = "Payme" if e["src"] == "payme" else "Click"
            send(r["chat_id"], f"✅ {name}: {P.cash_fmt(e['amount'], 'uzs')} · {(e['at'] or '')[11:16]}\nМижоз тўлади — пул тушди.")
        if len({r["rw_id"] for r in reqs}) == 1:
            con.execute("UPDATE raspil_payreq SET done=1 WHERE rw_id=? AND done=0", (reqs[0]["rw_id"],))
    con.commit()


def _kb_add_lang(kb):
    """«🌐 Тил» тугмаси — охирги қаторга (ўзбек / рус / инглиз)."""
    import botlang
    rows = [list(r) for r in kb.get("keyboard") or []]
    if not rows:
        return kb
    if len(rows[-1]) < 3:
        rows[-1].append({"text": botlang.B_LANG})
    else:
        rows.append([{"text": botlang.B_LANG}])
    return dict(kb, keyboard=rows)


def _kb_full(rw):
    kb = _kb_base(rw)
    extra = [{"text": t} for t, f in ((B_ORDER, "p_order"), (B_RECV, "p_recv"), (B_EDGE, "p_edge")) if dict(rw or {}).get(f)]
    if extra:                                             # олиб келиш: буюртма ва қабул (рухсат платформада)
        kb = dict(kb, keyboard=kb["keyboard"] + [extra])
    if dict(rw or {}).get("driver"):                      # шофёр: рейс, харажат, ҳафта ҳисоби (логистика)
        import logist
        kb = dict(kb, keyboard=kb["keyboard"] + logist.buttons(rw))
    return kb


CONSTR_KB = {"keyboard": [[{"text": B_KUS}, {"text": B_SAL}]], "resize_keyboard": True, "is_persistent": True}
CONSTR_HELP = ("«🧩 Кусок омбори» — кусокларни кўриш.\n"
               "«💰 Ойлигим» — шу ойдаги ойлигингиз.")


def is_constr(rw):
    """Базис конструктори (ишчилар рўйхатида лавозими «Конструктор»): 50/50 ишлайди — «Ишга келдим / Иш тугади» керак эмас (эгаси, 23.10)."""
    kw = (rw or {}).get("kw_id")
    if not kw or (rw or {}).get("role") != "staff":
        return False
    r = A.q("SELECT position FROM k_workers WHERE id=?", (kw,), one=True)
    return bool(r and "конструктор" in (r["position"] or "").casefold())


def _kb_base(rw):
    if owner_only(rw):
        return OWN_KB
    if is_constr(rw) and not P.is_kroy(rw) and not dict(rw).get("driver") and not is_mgr(rw):
        kb = CONSTR_KB
        if is_owner(rw):
            kb = dict(kb, keyboard=kb["keyboard"] + [[{"text": B_OWN}]])
        return kb
    if is_mgr(rw):
        kb = MGR_KB
    elif P.is_kroy(rw):
        kb = KROY_KB
    elif dict(rw or {}).get("driver"):                  # шофёр: фақат цех буюртмалари (олиб келиш), рейс, харажат, ҳафта ҳисоби, тил —
        kb = {"keyboard": [[{"text": B_PICK}]], "resize_keyboard": True, "is_persistent": True}   # келдим/кетдим, кусок керак эмас (эгаси, 23.10)
    elif (rw or {}).get("role") == "staff":
        kb = STAFF_KB                                    # «📐 Крой қилдим» олиб ташланди (эгаси: керак эмас)
    else:
        kb = MAIN_KB
    if is_owner(rw):                                      # шерик ҳам ишчи/менежер бўлса — ҳисобот тугмаси қўшилади
        kb = dict(kb, keyboard=kb["keyboard"] + [[{"text": B_OWN}]])
    return kb
B_PICK = "🚚 Олиб келиш"
B_ORDER = "🚚 Заказ"
B_RECV = "📥 Қабул"
B_EDGE = "🏷 Кромка"
DRIVER_HELP = ("🚚 Сиз шофёрсиз.\n«🚚 Олиб келиш» — цех буюртмалари: ҳар товар учун «✅ Олдим» ёки «❌ Йўқ экан».\n"
               "«🚗 Рейс» — цехни (Lumberture ёки Chinor) ва йўналишни танланг. Мижозга олиб борсангиз — «🏠 Мижозга доставка».\n"
               "«⛽ Харажат» — бензин, газ, СТО. «📊 Ҳафта ҳисоби» — ҳафтада қаерга юрдингиз ва қанча ишладингиз.")
MGR_KB = {"keyboard": [[{"text": "🧩 Кусок омбори"}]], "resize_keyboard": True, "is_persistent": True}
KROY_KB = {"keyboard": [[{"text": B_IN}, {"text": B_END}], [{"text": B_KROYS}, {"text": B_KPI}], [{"text": B_KUS}]],
           "resize_keyboard": True, "is_persistent": True}
CONTACT_KB = {"keyboard": [[{"text": "📱 Рақамимни юбориш", "request_contact": True}]], "resize_keyboard": True, "one_time_keyboard": True}
HELP = ("1️⃣ «✅ Ишга келдим»\n"
        "2️⃣ Накладной расмини ташланг\n"
        "3️⃣ «🏁 Иш тугади»")
_OLD_HELP = ("Қандай ишлайди:\n"
        "0️⃣ Ишга келганда — «✅ Ишга келдим».\n"
        "1️⃣ Иш бошида: ўз станогингиз тугмасини босинг (▶️ Арра, Двухпилка, Присадка, Кромка, Овал, Ровер, Утолщенка) → исмларни босинг.\n"
        "2️⃣ Накладной расмини ташланг. Аррада — листни босинг → «✅ Кесилди» → нечталиги. Бошқа станокда — «✅ Ҳаммаси тугади» ёки бугун қилинган %.\n"
        "⏸ Орада мижоз келса: ишни тўхтатинг, мижоз накладнойини ташлаб бажаринг. Кейин «📂 Тўхтатилган ишлар» → тўхтатган накладнойни босиб давом эттиринг.\n"
        "3️⃣ Охирида: «🏁 Иш тугади».\n"
        "📝 Накладнойсиз кессангиз — «📝 Накладнойсиз»: накладной ёзилгач, расмини ташласангиз бот улашни сўрайди.")
NUMRE = re.compile(r"(?<![\d.,])(\d{3,6})(?![\d.,])")
_st = {"thread": None}


GUIDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "bot_qollanma.mp4")
VIDEO_LINE = "\n\n🎬 Видео-қўлланма: /video"
GUIDE_CAP = "🎬 Цех боти — қандай ишлатилади"


def send_guide(chat):
    """Видео-қўлланма (static/bot_qollanma.mp4): биринчи марта юкланади, кейин Telegram file_id билан тез юборилади."""
    import botlang
    fid = A.get_setting("raspil_bot_guide_fid", "") or ""
    if fid:
        try:
            return api("sendVideo", chat_id=chat, video=fid, caption=GUIDE_CAP, supports_streaming=True)
        except tg.TgError:
            A.set_settings(raspil_bot_guide_fid="")
    if not os.path.exists(GUIDE) or not token():
        return send(chat, "Қўлланма ҳали юкланмаган.")
    cap = botlang.prepare_params("work", "sendVideo", {"chat_id": chat, "caption": GUIDE_CAP})["caption"]
    try:
        with open(GUIDE, "rb") as fh:
            r = requests.post(f"{tg.API_BASE}/bot{token()}/sendVideo", timeout=(7, 180),
                              data={"chat_id": chat, "caption": cap, "supports_streaming": "true", "width": "720", "height": "1280"},
                              files={"video": ("qollanma.mp4", fh, "video/mp4")})
        res = (r.json() or {}).get("result") or {}
    except (requests.RequestException, ValueError) as e:
        log.warning("guide upload: %s", e)
        return send(chat, "⏳ Видео юкланмади, бироздан кейин /video деб қайта ёзинг.")
    fid = (res.get("video") or res.get("document") or {}).get("file_id")
    if fid:
        A.set_settings(raspil_bot_guide_fid=fid)
    return res


def send_file(chat, path, name=None, caption=""):
    """Файл (zip, pdf…) юбориш — multipart. -> натижа ёки None."""
    if not token() or not os.path.exists(path):
        return None
    import botlang
    cap = botlang.prepare_params("work", "sendDocument", {"chat_id": chat, "caption": caption})["caption"] if caption else ""
    try:
        with open(path, "rb") as fh:
            r = requests.post(f"{tg.API_BASE}/bot{token()}/sendDocument", timeout=(7, 120),
                              data={"chat_id": chat, "caption": cap[:1000]}, files={"document": (name or os.path.basename(path), fh)})
        j = r.json() or {}
        if not j.get("ok"):
            log.warning("send_file %s: %s", chat, j.get("description"))
        return j.get("result")
    except (requests.RequestException, ValueError) as e:
        log.warning("send_file %s: %s", chat, e)
        return None


def init_tables(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(ready_workers)")}
    if "bot_uid" not in cols:
        con.execute("ALTER TABLE ready_workers ADD COLUMN bot_uid INTEGER")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS raspil_pending(id INTEGER PRIMARY KEY, rw_id INTEGER, chat_id INTEGER, at TEXT, photo TEXT DEFAULT '',
        iddoc TEXT DEFAULT '', doc_no TEXT DEFAULT '', client TEXT DEFAULT '', user_msg INTEGER, bot_msg INTEGER, state TEXT DEFAULT '',
        job_id INTEGER);
    CREATE TABLE IF NOT EXISTS raspil_payreq(id INTEGER PRIMARY KEY, rw_id INTEGER, chat_id INTEGER, at TEXT, done INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS raspil_photo_retry(id INTEGER PRIMARY KEY, rw_id INTEGER, chat INTEGER, msg_id INTEGER, file_id TEXT,
        text TEXT DEFAULT '', path TEXT, at TEXT, tries INTEGER DEFAULT 0, last_at TEXT, state TEXT DEFAULT '');
    """)
    if "mgr" not in {r[1] for r in con.execute("PRAGMA table_info(ready_workers)")}:
        con.execute("ALTER TABLE ready_workers ADD COLUMN mgr INTEGER NOT NULL DEFAULT 0")
    if "owner" not in {r[1] for r in con.execute("PRAGMA table_info(ready_workers)")}:
        con.execute("ALTER TABLE ready_workers ADD COLUMN owner INTEGER NOT NULL DEFAULT 0")
    if "machine" not in {r[1] for r in con.execute("PRAGMA table_info(raspil_pending)")}:
        con.execute("ALTER TABLE raspil_pending ADD COLUMN machine TEXT DEFAULT ''")


def token():
    return (A.get_setting("raspil_bot_token", "") or "").strip()


# ---------------------------------------------------------------- Telegram
def api(method, **p):
    if method == "getUpdates":
        return tg.call(token(), method, p, timeout=40)
    # интернет вақти-вақти билан узилади (api.telegram.org'га уланиб бўлмайди) — жавоб йўқолмасин: бир неча марта қайта уриниш
    import botlang
    p = botlang.prepare_params("work", method, p)                # ишчи тил танлаган бўлса — матн ва тугмалар шу тилда
    for i, wait in enumerate((0, 3, 8, 15)):
        if wait:
            time.sleep(wait)
        try:
            return tg.call(token(), method, p, timeout=20)
        except tg.TgError as e:
            if e.code != 0 or i == 3:
                raise
            log.info("raspil bot %s: тармоқ хатоси, қайта уриниш (%d)", method, i + 1)


def send(chat, text, kb=None, reply_to=None):
    p = dict(chat_id=chat, text=text[:4000], disable_web_page_preview=True)
    if kb is not None:
        p["reply_markup"] = kb
    if reply_to:
        p["reply_to_message_id"] = reply_to
        p["allow_sending_without_reply"] = True
    try:
        return api("sendMessage", **p)
    except tg.TgError as e:
        log.warning("raspil bot send: %s", e)
        return None


def edit(chat, mid, text, kb=None):
    p = dict(chat_id=chat, message_id=mid, text=text[:4000], disable_web_page_preview=True)
    p["reply_markup"] = kb or {"inline_keyboard": []}
    try:
        api("editMessageText", **p)
    except tg.TgError as e:
        if "not modified" not in (e.description or ""):
            log.warning("raspil bot edit: %s", e)


def ikb(rows):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows if row]}


def download(file_id, dest, timeout=60):
    f = api("getFile", file_id=file_id)
    r = requests.get(f"{tg.API_BASE}/file/bot{token()}/{f['file_path']}", timeout=timeout)
    r.raise_for_status()
    with open(dest, "wb") as fh:
        fh.write(r.content)
    return dest


PHOTO_RETRY_MAX = 15          # ~15 дақиқа давомида ҳар дақиқада қайта уринади


def download_try(file_id, dest, tries=2):
    """Интернет секин бўлса — бир неча марта уриниш. -> True/False"""
    for i in range(tries):
        try:
            download(file_id, dest, timeout=30)
            return True
        except Exception as e:
            A.errlog.error("raspil bot photo (уриниш %d): %r", i + 1, e)
    return False


def retry_photos(force=False):
    """Юкланмай қолган расмлар: ҳар дақиқада қайта уриниш; юкланса — одатдагидек давом этади (накладной топилади)."""
    now = datetime.now()
    rows = A.q("SELECT * FROM raspil_photo_retry WHERE state='' ORDER BY id")
    for r in rows:
        if not force and r["last_at"] and r["last_at"] > (now - timedelta(seconds=55)).strftime("%Y-%m-%d %H:%M:%S"):
            continue
        rw = A.q("SELECT * FROM ready_workers WHERE id=?", (r["rw_id"],), one=True)
        if not rw:
            A.db().execute("UPDATE raspil_photo_retry SET state='fail' WHERE id=?", (r["id"],)); A.db().commit()
            continue
        if download_try(r["file_id"], r["path"], tries=1):
            A.db().execute("UPDATE raspil_photo_retry SET state='done', last_at=? WHERE id=?", (A.now(), r["id"])); A.db().commit()
            try:
                pid, doc, st, info = new_pending(dict(rw), r["chat"], r["msg_id"], r["path"], r["text"] or "")
                show(dict(rw), r["chat"], pid, doc, st, info)
            except Exception:
                A.errlog.exception("retry photo continue")
            continue
        tries = (r["tries"] or 0) + 1
        st = "fail" if tries >= PHOTO_RETRY_MAX else ""
        A.db().execute("UPDATE raspil_photo_retry SET tries=?, last_at=?, state=? WHERE id=?", (tries, A.now(), st, r["id"])); A.db().commit()
        if st == "fail":
            send(r["chat"], "⚠️ Расм юкланмади. Қайта юборинг.")


# ---------------------------------------------------------------- ишчи
def worker_by_uid(uid):
    r = A.q("SELECT * FROM ready_workers WHERE bot_uid=? AND active=1 AND (role IN ('raspil','staff') OR mgr=1 OR owner=1)", (uid,), one=True)
    return dict(r) if r else None


def link_contact(msg):
    import ready as R
    c = msg.get("contact") or {}
    uid = msg["from"]["id"]
    if c.get("user_id") and c["user_id"] != uid:
        return None, "⚠️ Ўзингизнинг рақамингизни юборинг (пастдаги тугма)."
    ph = R.norm_phone(c.get("phone_number", ""))
    r = A.q("SELECT * FROM ready_workers WHERE phone=? AND (role IN ('raspil','staff') OR mgr=1 OR owner=1) AND active=1", (ph,), one=True) if ph else None
    if not r:
        return None, "⛔ Бу рақам рўйхатда йўқ."
    A.db().execute("UPDATE ready_workers SET bot_uid=NULL WHERE bot_uid=?", (uid,))
    A.db().execute("UPDATE ready_workers SET bot_uid=? WHERE id=?", (uid, r["id"]))
    A.db().commit()
    if owner_only(r):
        return dict(r, bot_uid=uid), f"✅ Салом, {r['name']}!\n\n📈 Ҳисобот: касса, савдо, цех. Ҳар куни кечқурун кун якуни ва касса ўзгарса — хабар келади."
    if is_mgr(r):
        return dict(r, bot_uid=uid), f"✅ Салом, {r['name']}!\n\n" + MGR_HELP
    return dict(r, bot_uid=uid), f"✅ Салом, {P.wname(r['kw_id']) or r['name']}!\n\n" + (STAFF_HELP if r["role"] == "staff" else HELP)


# ---------------------------------------------------------------- смена: тугмалар билан
def people_kb(prefix, skip=None, extra=None):
    ws = sorted(P.workers(), key=lambda w: (not w["has_r"], w["name"]))
    btn = [(w["name"][:28], f"{prefix}:{w['id']}") for w in ws if w["id"] != skip][:40]
    rows = [btn[i:i + 2] for i in range(0, len(btn), 2)]
    if extra:
        rows.insert(0, [extra])
    return ikb(rows)


def role_kb(machine, rw=None):
    """Ишчи ўзи киради: бош оператор ёки ёрдамчи (фақат платформада берилган доступ). Ҳозир станокда ким борлиги ҳам кўринади."""
    cur = [c for c in P.open_crews() if (c.get("machine") or "arra") == machine]
    now = ""
    if cur:
        c = cur[0]
        now = ("\nҲозир: " + (f"бош оператор — {P.wname(c['op_id'])}" if c["op_id"] else "бош оператор йўқ")
               + (f", ёрдамчи — {P.wname(c['helper_id'])}" if c["helper_id"] else ""))
    rows = [[(lbl, f"jr:{machine}:{r}")] for r, lbl in (("op", "👷 Бош оператор"), ("hp", "🤝 Ёрдамчи")) if not rw or P.can(rw, machine, r)]
    if not rows:
        return (f"▶️ {P.MACHINES.get(machine, machine).split(' (')[0]}\n\n⛔ Сизга бу станок очилмаган. Раҳбарга айтинг.", ikb([[("‹ Орқага", "sw:menu")]]))
    return (f"▶️ {P.MACHINES.get(machine, machine).split(' (')[0]}{now}\n\nСиз кимсиз?", ikb(rows))


def ask_operator(chat, machine, rw=None):
    t, kb = role_kb(machine, rw)
    send(chat, t, kb)


# ---------------------------------------------------------------- накладной: олдиндан кўриниш ва тугмалар
DROP_WORDS = {"лдсп", "лмдф", "лхдф", "мдф", "хдф", "дсп", "двп", "м", "ультрадекор", "premium", "премиум", "лист"}


def _compact(label, kind=""):
    """Тугма учун: тури/ўлчамларсиз (ранги ва қалинлиги қолади). «ЛДСП Ультрадекор 5,0325 Кашемир 16мм» → «Кашемир 16мм»."""
    t = re.sub(r"\([^)]*\)", " ", label or "")
    w = [x for x in re.split(r"\s+", t) if x and x.lower() not in DROP_WORDS
         and not re.fullmatch(r"\d+([.,]\d+)?(кв)?", x, re.I) and not re.fullmatch(r"\d+\*\d+", x)]
    c = " ".join(w).strip(" -—:") or (label or "").strip()
    return ("ХДФ " + c) if kind == "hdf" and "хдф" not in c.lower() else c


def _short(label, n=30):
    """Узун номни қисқартириш: биринчи сўз (тури) + охирги сўзлар (ранги, қалинлиги)."""
    t = re.sub(r"\s+", " ", label).strip()
    if len(t) <= n:
        return t
    w = t.split(" ")
    tail = []
    for x in reversed(w[1:]):
        if len(w[0]) + 3 + len(" ".join([x] + tail)) > n:
            break
        tail.insert(0, x)
    return (w[0] + " … " + " ".join(tail)) if tail else t[:n - 1] + "…"


def pmachine(rw, p):
    """Накладной қайси станок иши сифатида очилган: ишчи танлагани, бўлмаса — ҳозирги станоги."""
    m = (p.get("machine") if hasattr(p, "get") else p["machine"]) or ""
    if m:
        return m
    crew = P.crew_for(rw)
    return (crew.get("machine") or "arra") if crew else "arra"


def preview(rw, p, note=""):
    """Накладной: арра марказида — листлар тугма бўлиб; двухпилкада — кусок/столешница/45°."""
    machine = pmachine(rw, p)
    crew = P.who_for(rw, machine)
    pid = p["id"]
    lines = [f"📄 №{p['doc_no']} — {p['client']}"]
    if note:
        lines.append(note)
    if crew:
        who = P.wname(crew["op_id"]) + (f" + {P.wname(crew['helper_id'])}" if crew["helper_id"] else "")
        lines.append(f"👷 {P.MACHINES.get(machine, machine).split(' (')[0]}: {who}")
    else:
        lines.append("⚠️ Аввал «🔄 Станок»ни танланг.")
    rows = []
    if machine in P.WORK:
        return work_preview(rw, p, machine, lines)
    if machine in P.JOB_MACHINES:
        import c1
        pcs, psum, det = P.job_calc(machine, (c1.sale_doc(p["iddoc"]) or {}).get("lines") or [])
        done = P.pris_done(p["iddoc"], machine)
        left = round(1 - done, 4)
        mname = P.MACHINES[machine].split(" (")[0]
        if not det:
            lines.append(f"\nℹ️ Бу накладнойда {mname.lower()} иши йўқ.")
        elif machine == "pris":
            lines.append("\n🔩 Присадка:\n" + P.pris_text(det))
            cnc = sum(d[1] for d in det if d[4] == "%")
            lines.append(f"\nЖами: {P._n(pcs)} тешик" + (f" + ЧПУ/паз {P._n(cnc)}" if cnc else ""))
        else:
            wk = P.WORK[machine]
            lines.append(f"\n{wk['icon']} {mname}:\n" + P.work_text(machine, det))
            lines.append(f"\nЖами: {P._n(pcs)} {wk['unit']}")
        if det:
            if left <= 0.001:
                lines.append("\n✔️ Бу накладной тўлиқ ҳисобланган.")
            else:
                if done > 0.001:
                    lines.append(f"\n⏳ Олдин {round(done * 100)}% бажарилган, қолди {round(left * 100)}%.")
                rows.append([("✅ " + ("Қолгани тугади" if done > 0.001 else "Ҳаммаси тугади"), f"j:{pid}:pris")])
                parts = [(f"⏸ {k}%", f"pf:{pid}:{k}") for k in (25, 50, 75) if k / 100 < left - 0.001]
                if parts:
                    rows.append(parts)
                lines.append("\n👇 Нечта бажарилди?")
        rows.append([("✖️ Ёпиш", f"j:{pid}:close")])
        return "\n".join(lines), ikb(rows)
    s = P.doc_state(p["iddoc"])
    ext = s["left"]["extra"] > 0.001 or s["left"]["m45"] > 0.001
    if machine == "dp":
        if ext:
            lines.append("Двухпилка иши: " + P.detail_text([d for d in s["det"] if P.grp(d[0]) == "extra"]))
            rows = [[("✅ Кесилди", f"j:{pid}:cut")], [("✂️ Қисман", f"j:{pid}:part")]]
            lines.append("\n👇 Кесиб бўлгач босинг:")
        else:
            lines.append("\nℹ️ Двухпилка иши йўқ.")
    else:
        st = P.items_state(p["iddoc"])
        its = [i for i in st["items"] if i["left"] > 0.001]
        num = {i["key"]: k + 1 for k, i in enumerate(st["items"])}
        if st["items"]:
            lines.append("\n🪵 Листлар:")
            for i in st["items"]:
                if st["caps"][i["kind"]] <= 0:
                    lines.append(f"▪️ {num[i['key']]}. {i['label']} — {P._n(i['qty'])} та (распилсиз, бутун)")
                    continue
                # распил чегарасига етди — кесилмаган листлар бутун кетади (ишчи кесганини босади, қолгани ўзи бутун)
                mark = "✅" if i["left"] <= 0.001 else "▫️"
                whole = i["whole"] or (i["qty"] - i["done"] if i["left"] <= 0.001 and i["qty"] - i["done"] > 0.001 else 0)
                info = ([f"кесилди {P._n(i['done'])}"] if i["done"] else []) + ([f"{P._n(whole)} таси бутун"] if whole else [])
                lines.append(f"{mark} {num[i['key']]}. {i['label']} — {P._n(i['qty'])} та" + (f" ({', '.join(info)})" if info else ""))
        need = {k: v for k, v in (st.get("need") or {}).items() if v > 0.001}
        if need:
            for kind, v in need.items():
                sold = sum(i["qty"] for i in st["items"] if i["kind"] == kind and i["key"][0] == "m")
                lines.append(f"\n⚠️ {'ХДФ' if kind == 'hdf' else 'Лист'}: сотилган {P._n(sold)} та, распил {P._n(st['caps'][kind])} та — "
                             f"{P._n(v)} таси бутун кетади. Қайси бири бутун кетишини босинг:")
                for i in st["items"]:
                    if i["kind"] == kind and i["key"][0] == "m" and i["qty"] - i["done"] - i["whole"] >= 1 - 1e-9:
                        rows.append([(f"📦 {num[i['key']]}. {_compact(i['label'], i['kind'])} — бутун", f"bw:{pid}:{i['key']}")])
        if any(i["whole"] for i in st["items"]):
            rows.append([("↩️ Бутун белгисини бекор қилиш", f"bwx:{pid}:0")])
        for i in its:
            if not i.get("blocked"):
                rows.append([(f"{num[i['key']]}. {_compact(i['label'], i['kind'])} — {P._n(i['left'])} та", f"i:{pid}:{i['key']}")])
        if ext:
            rows.append([("🧩 Кусок (арра марказида)", f"j:{pid}:kus")])
        wl = P.waits(rw=rw["id"], kw=rw.get("kw_id"))
        if crew:
            wl += [w for w in P.waits(kw=crew["op_id"]) + (P.waits(kw=crew["helper_id"]) if crew["helper_id"] else []) if w["id"] not in {x["id"] for x in wl}]
        wl = [w for w in wl if any(i["kind"] == w["wkind"] and i["left"] > 0.001 for i in st["items"])]
        if wl:
            lines.append("\n🔗 Накладнойсиз иш шуникими?")
            for w in wl[:5]:
                rows.insert(0, [(f"🔗 {_short(w['wlabel'], 34)}", f"l:{pid}:{w['id']}")])
        if not st["items"] and not ext:
            lines.append("\n❌ Бу накладнойда распил йўқ.")
        elif not rows:
            lines.append("\n✔️ Тўлиқ кесилган.")
        else:
            lines.append("\n👇 Қайси листни кесасиз?" if not (st.get("need") and any(v > 0.001 for v in st["need"].values())) else "")
    rows.append([("✔️ Тайёр / ёпиш", f"j:{pid}:close")])
    return "\n".join(lines), ikb(rows)


def new_pending(rw, chat, user_msg, photo="", caption=""):
    import ready as R
    ocr = ""
    if photo and not NUMRE.search(caption or ""):
        ocr = R.ocr_image(photo)
    doc, st, info = R.pick_doc(caption, ocr)
    db = A.db()
    pid = db.execute("INSERT INTO raspil_pending(rw_id, chat_id, at, photo, user_msg, state) VALUES(?,?,?,?,?,?)",
                     (rw["id"], chat, A.now(), photo or "", user_msg, "")).lastrowid
    db.commit()
    return pid, doc, st, info


def show(rw, chat, pid, doc, st, info, mid=None):
    import ready as R
    db = A.db()
    if not doc:
        db.execute("UPDATE raspil_pending SET state='wait_no' WHERE id=?", (pid,)); db.commit()
        txt = {"nodoc": f"❓ №{info} 1С'да топилмади.", "many": f"❓ Бир нечта рақам: {info}."}.get(st, "❓ Накладной рақамини ўқий олмадим.")
        txt += "\n\n✍️ Накладной рақамини ёзинг."
        return send(chat, txt, ikb([[("✖️ Бекор", f"j:{pid}:close")]])) if not mid else edit(chat, mid, txt)
    client = R.doc_client(doc["iddoc"])[0]
    db.execute("UPDATE raspil_pending SET iddoc=?, doc_no=?, client=?, state='ask' WHERE id=?", (doc["iddoc"], doc["no"], client, pid))
    db.commit()
    p = dict(A.q("SELECT * FROM raspil_pending WHERE id=?", (pid,), one=True))
    text, kb = preview(rw, p)
    if mid:
        edit(chat, mid, text, kb)
        bm = mid
    else:
        m = send(chat, text, kb)
        bm = m and m.get("message_id")
    db.execute("UPDATE raspil_pending SET bot_msg=? WHERE id=?", (bm, pid)); db.commit()


ACTION_TEXT = {"cut": "кесилди", "kus": "фақат кусок кесилди"}


def switch_kb(rw=None):
    ms = [(k, v) for k, v in P.MACHINES.items() if not rw or P.can(rw, k)]
    full = [(k, v) for k, v in ms if "(" in v and k != "rover"]       # модели бор станоклар — тўлиқ номи билан, ҳар бири алоҳида қатор
    rest = [(k, v) for k, v in ms if (k, v) not in full]
    rows = [[("▶️ " + v.replace(" (", " · ").rstrip(")"), f"sw:m:{k}")] for k, v in full]
    rows += [[(("▶️ " + v.split(" (")[0]), f"sw:m:{k}") for k, v in rest[i:i + 2]] for i in range(0, len(rest), 2)]
    # «📂 Тўхтатилган ишлар» ва «📝 Накладнойсиз» — пастдаги тугмаларда (бу ерда такрорланса ишчиларни чалғитади)
    return ikb(rows)


def switch_flow(rw, chat, mid, parts):
    if parts[1] == "menu":
        edit(chat, mid, "🔄 Қайси станокда ишлайсиз?", switch_kb(rw))
    elif parts[1] == "m":
        t, kb = role_kb(parts[2], rw)
        edit(chat, mid, t, kb)
    elif parts[1] == "open":
        txt, kb = open_jobs_msg(rw)
        edit(chat, mid, txt, kb)
    elif parts[1] == "wait":                      # аввал: қайси станок иши (ҳозиргиси биринчи)
        crew = P.crew_for(rw)
        cur = (crew.get("machine") or "arra") if crew else None
        order = [m for m in ([cur] if cur else []) + [m for m in P.MACHINES if m != cur] if P.can(rw, m)]
        rows = [[(("✓ " if m == cur else "") + P.MACHINES[m].split(" (")[0], f"w:m:{m}")] for m in order]
        rows.append([("🧩 Кусок сотиш", f"w:m:{P.SALE}")])
        edit(chat, mid, "📝 Накладнойсиз иш. Қайси станок?", ikb(rows))
    elif parts[1] == "today":
        edit(chat, mid, today_text(rw))
    elif parts[1] == "kroy":
        kroy_start(rw, chat, mid)
    elif parts[1] == "kst":                       # кусок омбори менюси
        t, kb = kstore_msg()
        edit(chat, mid, t, kb)
    elif parts[1] == "ksee":                      # омбордагилар рўйхати
        t, kb = kstore_list()
        edit(chat, mid, t, kb)
    elif parts[1] == "kin":                       # кусок омборга киритиш (сотилмаган)
        pid = A.db().execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state, bot_msg) VALUES(?,?,?,?,?)",
                             (rw["id"], chat, A.now(), "kpick", mid)).lastrowid
        A.db().commit()
        t, kb = kusok_menu(pid, "📥 Омборга. ")
        edit(chat, mid, t, kb)
    return ""


def remind_open(rw, chat, skip_iddoc=""):
    """Иш тугагач: тўхтатилган бошқа накладнойлар бўлса — ўзи эслатади (ишчи эслаб қолиши шарт эмас)."""
    try:
        machine, jobs = open_jobs(rw)
    except Exception:
        A.errlog.exception("remind_open")
        return
    jobs = [j for j in jobs if A.q("SELECT iddoc FROM raspil_pending WHERE id=?", (j[0],), one=True)["iddoc"] != skip_iddoc]
    if jobs:
        send(chat, "⏸ Тўхтатилган иш:",
             ikb([[(f"№{no} · {lbl}", f"o:{pid}")] for pid, no, cl, lbl in jobs[:4]]))


def _wlabel(name):
    """Кромка қатори учун қисқа ном: «Кромка 19/1 Шифер темный (местный) (рулон 200м)» → «19/1 Шифер темный»."""
    t = re.sub(r"\([^)]*\)", " ", name or "")
    w = [x for x in t.split() if x.lower() not in ("кромка", "пвх", "услуга", "pvc", "abs")]
    return " ".join(w).strip() or (name or "")[:30]


def work_preview(rw, p, machine, lines):
    """Кромка/овал/утолщенка/ровер: ҳар бир қатор — тугма; босилса «тугади», кейингисига ўтилади."""
    import c1
    pid = p["id"]
    wk = P.WORK[machine]
    mname = P.MACHINES[machine].split(" (")[0]
    dl = (c1.sale_doc(p["iddoc"]) or {}).get("lines") or []
    wl = P.work_lines(machine, dl)
    nt = {int(l.get("no") or 0): re.sub(r"\s+", " ", l.get("note") or "").strip() for l in dl}   # 1С қатор изоҳи (ранги ва ҳ.к.)
    rows = []
    if machine == "rover" and not wl:             # накладнойда ровер хизмати йўқ: ишчи сумма+изоҳ ёзади → менежер 1С'га қўшади
        A.db().execute("UPDATE raspil_pending SET state='rsum2', at=? WHERE id=?", (A.now(), pid)); A.db().commit()
        lines.append("\nℹ️ Бу накладнойда ровер хизмати йўқ.\n💰 Ровер иши суммаси қанча? Рақам ёзинг (масалан 150000):")
        rows.append([("✖️ Ёпиш", f"j:{pid}:close")])
        return "\n".join(lines), ikb(rows)
    if not wl:
        lines.append(f"\nℹ️ Бу накладнойда {mname.lower()} иши йўқ.")
    else:
        done = P.work_done_lines(p["iddoc"], machine)
        if P.pris_done(p["iddoc"], machine) > 0.999:
            done = {w[0] for w in wl}
        pt = P.work_part(p["iddoc"], machine)
        lines.append(f"\n{wk['icon']} {mname}:")
        for k, (no, nm, q, unit, a) in enumerate(wl, 1):
            lines.append(f"{'✅' if no in done else '▫️'} {k}. {nm}" + (f" · 🎨 {nt[no]}" if nt.get(no) else "") + f": {P._n(q)} {wk['unit']}"
                         + (f" (қилинди {P._n(pt[no])})" if pt.get(no) and no not in done else ""))
        left = [(no, nm, q - pt.get(no, 0.0), unit, a) for no, nm, q, unit, a in wl if no not in done]
        lines.append(f"\nЖами: {P._n(sum(w[2] for w in wl))} {wk['unit']}" + (f", қолди {P._n(sum(w[2] for w in left))} {wk['unit']}" if left and (done or pt) else ""))
        if not left:
            lines.append("\n✔️ Бу накладной тўлиқ бажарилган.")
        else:
            num = {w[0]: k for k, w in enumerate(wl, 1)}
            for no, nm, q, unit, a in left:
                rows.append([((f"✅ {num[no]}. {_wlabel(nm)}" + (f" {nt[no]}" if nt.get(no) else "") + f" — {P._n(q)} {wk['unit']}")[:60], f"wl:{pid}:{no}")])
            if len(left) > 1:
                rows.append([("✅✅ Ҳаммаси тугади", f"j:{pid}:pris")])
            if wk["unit"] == "м":
                rows.append([("✏️ Қилган метримни ёзаман", f"wm:{pid}")])
            lines.append("\n👇 Қайси бири тугаса — ўшани босинг." + ("\nҚисман қилган бўлсангиз — «✏️ метр» босинг." if wk["unit"] == "м" else ""))
    rows.append([("✖️ Ёпиш", f"j:{pid}:close")])
    return "\n".join(lines), ikb(rows)


def work_line(rw, chat, mid, pid, no):
    p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (pid, rw["id"]), one=True)
    if not p or p["state"] == "done":
        return "Эскирган"
    machine = pmachine(rw, dict(p))
    if machine not in P.WORK:
        return "Эскирган"
    jid, err = P.record_work_lines(rw, machine, p["iddoc"], p["doc_no"], p["client"], nos=[no], photo=p["photo"], msg_id=p["user_msg"])
    if err:
        return err
    j = A.q("SELECT * FROM raspil_job WHERE id=?", (jid,), one=True)
    A.db().execute("UPDATE raspil_job SET ack_id=? WHERE id=?", (mid, jid)); A.db().commit()
    wk = P.WORK[machine]
    text, kb = preview(rw, dict(p), f"✅ {_wlabel(json.loads(j['detail'])[0][0])}: {P._n(j['pr_pcs'])} {wk['unit']} тугади")
    kb["inline_keyboard"].insert(-1, [{"text": "🗑 Охиргисини бекор қилиш", "callback_data": f"x:{jid}"}])
    edit(chat, mid, text, kb)
    if P.pris_done(p["iddoc"], machine) > 0.999:
        A.db().execute("UPDATE raspil_pending SET state='done', job_id=? WHERE id=?", (jid, pid)); A.db().commit()
        remind_open(rw, chat, p["iddoc"])
    return "✅"


def work_meter(rw, chat, mid, parts):
    """wm:<pid> — қайси қатор; wm:<pid>:<no> — метр сўрайди (матн билан)."""
    import c1
    pid = int(parts[1])
    p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (pid, rw["id"]), one=True)
    if not p or p["state"] == "done":
        return "Эскирган"
    machine = pmachine(rw, dict(p))
    if machine not in P.WORK:
        return "Эскирган"
    wl = P.work_lines(machine, (c1.sale_doc(p["iddoc"]) or {}).get("lines") or [])
    done, pt = P.work_done_lines(p["iddoc"], machine), P.work_part(p["iddoc"], machine)
    left = [(no, nm, q - pt.get(no, 0.0)) for no, nm, q, u, a in wl if no not in done]
    if not left:
        return "Ҳаммаси тугаган"
    if len(parts) < 3 and len(left) > 1:
        rows = [[((f"{_wlabel(nm)} — қолди {P._n(q)} м")[:60], f"wm:{pid}:{no}")] for no, nm, q in left]
        rows.append([("‹ Орқага", f"wb:{pid}")])
        edit(chat, mid, "✏️ Қайси кромкадан метр ёзасиз?", ikb(rows))
        return ""
    no = int(parts[2]) if len(parts) > 2 else left[0][0]
    ln = next((x for x in left if x[0] == no), None)
    if not ln:
        return "Бу қатор тугаган"
    A.db().execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state, iddoc, doc_no, client, machine, bot_msg, user_msg, job_id) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (rw["id"], chat, A.now(), "wpart", p["iddoc"], p["doc_no"], p["client"], machine, mid, pid, no))
    A.db().commit()
    edit(chat, mid, f"✏️ {_wlabel(ln[1])} — қолди {P._n(ln[2])} м\n\n📏 Неча метр қилдингиз? Рақам ёзинг:")
    return ""


def work_part_text(rw, chat, text):
    """«wpart» ҳолатида метр ёзилди → қисман (ёки тўлиқ) ҳисобланади. -> True агар шу жараённики."""
    p = A.q("SELECT * FROM raspil_pending WHERE rw_id=? ORDER BY id DESC LIMIT 1", (rw["id"],), one=True)
    if not (p and p["state"] == "wpart" and text and p["at"] >= (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")):
        return False
    if text in (B_KUS, B_IN, B_END, B_SAL, B_SWITCH, B_TODAY):
        return False
    try:
        m = float(re.sub(r"[^0-9.,]", "", text).replace(",", "."))
    except ValueError:
        m = 0
    if m <= 0:
        send(chat, "📏 Фақат рақам ёзинг, масалан 6 ёки 4.5")
        return True
    par = A.q("SELECT * FROM raspil_pending WHERE id=?", (p["user_msg"],), one=True)
    jid, err = P.record_work_lines(rw, p["machine"], p["iddoc"], p["doc_no"], p["client"], nos=[p["job_id"]],
                                   photo=(par["photo"] if par else ""), msg_id=(par["user_msg"] if par else None), meters=m)
    A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (p["id"],)); A.db().commit()
    if err:
        send(chat, "⚠️ " + err)
        return True
    j = A.q("SELECT * FROM raspil_job WHERE id=?", (jid,), one=True)
    full = not json.loads(j["items"] or "{}").get("part")
    text_, kb = preview(rw, dict(par), f"✅ {_wlabel(json.loads(j['detail'])[0][0])}: {P._n(j['pr_pcs'])} м " + ("— тугади" if full else "ёзилди"))
    kb["inline_keyboard"].insert(-1, [{"text": "🗑 Охиргисини бекор қилиш", "callback_data": f"x:{jid}"}])
    A.db().execute("UPDATE raspil_job SET ack_id=? WHERE id=?", (p["bot_msg"], jid)); A.db().commit()
    edit(chat, p["bot_msg"], text_, kb)
    if P.pris_done(p["iddoc"], p["machine"]) > 0.999 and par:
        A.db().execute("UPDATE raspil_pending SET state='done', job_id=? WHERE id=?", (jid, par["id"])); A.db().commit()
        remind_open(rw, chat, p["iddoc"])
    return True


def open_jobs(rw, days=3):
    """Ишчи бошлаган, лекин тугатмаган накладнойлар (мижоз келиб, орада тўхтатилганлар): [(pid, doc_no, client, қолди)]."""
    crew = P.crew_for(rw)
    machine = (crew.get("machine") or "arra") if crew else "arra"
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = A.q("SELECT iddoc, MAX(id) pid, doc_no, client FROM raspil_pending WHERE rw_id=? AND iddoc<>'' AND at>=? "
               "GROUP BY iddoc ORDER BY MAX(id) DESC LIMIT 30", (rw["id"], since))
    out = []
    for r in rows:
        try:
            if machine in P.WORK:
                import c1
                wl = P.work_lines(machine, (c1.sale_doc(r["iddoc"]) or {}).get("lines") or [])
                dn = P.work_done_lines(r["iddoc"], machine) if P.pris_done(r["iddoc"], machine) < 0.999 else {w[0] for w in wl}
                pt_ = P.work_part(r["iddoc"], machine)
                rem = sum(w[2] - pt_.get(w[0], 0.0) for w in wl if w[0] not in dn)
                lbl = f"{P._n(rem)} {P.WORK[machine]['unit']} қолди" if rem > 0.001 and (dn or pt_) else ""
            elif machine in P.JOB_MACHINES:
                import c1
                q, sm, det = P.job_calc(machine, (c1.sale_doc(r["iddoc"]) or {}).get("lines") or [])
                left = round(1 - P.pris_done(r["iddoc"], machine), 4) if det else 0
                lbl = f"{round(left * 100)}% қолди" if left > 0.001 else ""
            elif machine == "dp":
                st = P.doc_state(r["iddoc"])
                left = st["left"]["extra"] + st["left"]["m45"]
                lbl = f"қолди {P._n(left)}" if left > 0.001 else ""
            else:
                st = P.items_state(r["iddoc"])
                left = sum(i["left"] for i in st["items"])
                lbl = f"{P._n(left)} лист қолди" if left > 0.001 else ""
        except Exception:
            A.errlog.exception("open_jobs")
            continue
        if lbl:
            out.append((r["pid"], r["doc_no"], r["client"], lbl))
        if len(out) >= 8:
            break
    return machine, out


def open_jobs_msg(rw):
    machine, jobs = open_jobs(rw)
    if not jobs:
        return "📂 Тугамаган иш йўқ.", None
    txt = "📂 Тугамаган ишлар:"
    return txt, ikb([[(f"№{no} · {lbl}", f"o:{pid}")] for pid, no, cl, lbl in jobs])


def reopen(rw, chat, mid, pid):
    """Тўхтатилган накладнойга қайтиш: янги ойна очилади, қолган иш кўринади."""
    p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (pid, rw["id"]), one=True)
    if not p:
        return "Эскирган"
    db = A.db()
    npid = db.execute("INSERT INTO raspil_pending(rw_id, chat_id, at, photo, iddoc, doc_no, client, user_msg, state) VALUES(?,?,?,?,?,?,?,?,'ask')",
                      (rw["id"], chat, A.now(), p["photo"], p["iddoc"], p["doc_no"], p["client"], p["user_msg"])).lastrowid
    db.execute("UPDATE raspil_pending SET state='done' WHERE rw_id=? AND iddoc=? AND id<>? AND state<>'done'", (rw["id"], p["iddoc"], npid))
    db.commit()
    np_ = A.q("SELECT * FROM raspil_pending WHERE id=?", (npid,), one=True)
    text, kb = preview(rw, dict(np_), "▶️ Давом эттиряпсиз")
    edit(chat, mid, text, kb)
    db.execute("UPDATE raspil_pending SET bot_msg=? WHERE id=?", (mid, npid)); db.commit()
    return "Давом"


def do_action(rw, cq, pid, act, n=None):
    chat, mid = cq["message"]["chat"]["id"], cq["message"]["message_id"]
    p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (pid, rw["id"]), one=True)
    if not p:
        return "Эскирган"
    if p["state"] == "done":
        return "Аввал бажарилган"
    if act == "close":
        A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (pid,)); A.db().commit()
        tot = A.q("SELECT COALESCE(SUM(sheets),0) s, COALESCE(SUM(m45),0) m FROM raspil_job WHERE iddoc=? AND rw_id=? AND status='ok' AND at>=?",
                  (p["iddoc"], rw["id"], p["at"]), one=True) if p["iddoc"] else None
        if tot and (tot["s"] or tot["m"]):
            edit(chat, mid, f"✔️ №{p['doc_no']} — {p['client']}\nҲисобланди: {P._n(tot['s'])} лист" + (f", 45° {P._n(tot['m'])} м" if tot["m"] else "")
                 + "\n\n📷 Кейинги накладнойни ташланг.")
        else:
            edit(chat, mid, f"✖️ №{p['doc_no'] or ''} ёпилди.")
        return ""
    if act == "part":
        nums = [(str(i), f"n:{pid}:{i}") for i in range(1, 21)]
        edit(chat, mid, f"📄 №{p['doc_no']} — {p['client']}\n\n✂️ Нечта лист кесилди?",
             ikb([nums[i:i + 5] for i in range(0, 20, 5)] + [[("‹ Орқага", f"j:{pid}:back")]]))
        return ""
    if act == "pris":
        machine = pmachine(rw, dict(p))
        machine = machine if machine in P.JOB_MACHINES else "pris"
        if machine in P.WORK:
            jid, err = P.record_work_lines(rw, machine, p["iddoc"], p["doc_no"], p["client"], None, p["photo"], p["user_msg"])
        else:
            jid, err = P.record_pris(rw, p["iddoc"], p["doc_no"], p["client"], p["photo"], p["user_msg"], frac=n, machine=machine)
        if err:
            edit(chat, mid, f"📄 №{p['doc_no']} — {p['client']}\n⚠️ {err}")
            return err
        A.db().execute("UPDATE raspil_pending SET state='done', job_id=? WHERE id=?", (jid, pid))
        A.db().execute("UPDATE raspil_job SET ack_id=? WHERE id=?", (mid, jid)); A.db().commit()
        j = A.q("SELECT * FROM raspil_job WHERE id=?", (jid,), one=True)
        t = P.day_totals(j["day"]).get(j["op_id"]) or {}
        fr = float(json.loads(j["items"] or "{}").get("frac", 1))
        cnc = sum(d[1] for d in json.loads(j["detail"] or "[]") if d[4] == "%") * fr if machine == "pris" else 0
        left = round(1 - P.pris_done(p["iddoc"], machine), 4)
        if machine != "pris":
            wk = P.WORK[machine]
            day_q = {"kromka": t.get("km_d", 0) + t.get("km_n", 0), "oval": t.get("ov", 0), "utol": t.get("ut", 0), "rover": t.get("rv", 0)}[machine]
            edit(chat, mid, f"✅ №{p['doc_no']} — {p['client']}\n{P.MACHINES[machine].split(' (')[0]}: {P._n(j['pr_pcs'])} {wk['unit']}"
                 + (f" ({round(fr * 100)}%)\n⏳ Қолди {round(left * 100)}%" if left > 0.001 else "\n✔️ Тўлиқ бажарилди."),
                 ikb([[("🗑 Хато — бекор қилиш", f"x:{jid}")]]))
            if left <= 0.001:
                remind_open(rw, chat, p["iddoc"])
            return "✅"
        edit(chat, mid, f"✅ №{p['doc_no']} — {p['client']}\nПрисадка: {P._n(j['pr_pcs'])} тешик" + (f" + ЧПУ/паз {P._n(cnc)}" if cnc else "")
             + (f" ({round(fr * 100)}%)\n⏳ Қолди {round(left * 100)}%" if left > 0.001 else "\n✔️ Тўлиқ бажарилди.")
             + (f"\n📊 Бугун: {P._n(t.get('pp', 0))} тешик" if t else ""),
             ikb([[("🗑 Хато — бекор қилиш", f"x:{jid}")]]))
        if left <= 0.001:
            remind_open(rw, chat, p["iddoc"])
        return "✅"
    if act == "back":
        text, kb = preview(rw, dict(p))
        edit(chat, mid, text, kb)
        return ""
    machine = pmachine(rw, dict(p))
    if n is not None:
        cap = f"{n} та" + (" хдф билан" if machine == "arra" else "")
    else:
        cap = ACTION_TEXT.get(act, "кесилди")
    db = A.db()
    if act != "kus":
        db.execute("UPDATE raspil_pending SET state='done' WHERE id=? AND state<>'done'", (pid,))
        db.commit()
    photos = [p["photo"]] if p["photo"] and os.path.isfile(p["photo"]) else []
    jid, ack = P.handle_job(rw, f"{p['doc_no']} {cap}", photos, p["user_msg"], machine=machine if machine in ("arra", "dp") else None)
    db.execute("UPDATE raspil_pending SET job_id=? WHERE id=?", (jid, pid))
    db.execute("UPDATE raspil_job SET ack_id=? WHERE id=?", (mid, jid))
    db.commit()
    j = A.q("SELECT * FROM raspil_job WHERE id=?", (jid,), one=True)
    st = j["status"]
    if act == "kus":
        note = (f"✅ Кусок: {P._n(j['sheets'])} лист ҳисобланди" if st == "ok" else ack.split("\n")[0])
        text, kb = preview(rw, dict(p), note)
        if st == "ok":
            kb["inline_keyboard"].insert(-1, [{"text": "🗑 Охиргисини бекор қилиш", "callback_data": f"x:{jid}"}])
        edit(chat, mid, text, kb)
        return "✅" if st == "ok" else ""
    if st == "ok":
        ack = result_text(rw, j)
    edit(chat, mid, ack, ikb([[("🗑 Хато — бекор қилиш", f"x:{jid}")]]) if st == "ok" else None)
    return "✅" if st == "ok" else ""


# ---------------------------------------------------------------- янгиланишлар
def on_message(msg):
    chat = msg["chat"]["id"]
    if msg["chat"].get("type") != "private":
        return
    uid = msg["from"]["id"]
    import botlang
    botlang.current("work", chat)
    lang = botlang.get("work", chat)
    if lang and msg.get("text"):                            # таржима қилинган тугма — асл ўзбекча матнига
        msg = dict(msg, text=botlang.untr(msg["text"].strip(), lang))
    if botlang.is_lang_cmd(msg.get("text")):
        send(chat, botlang.ASK, botlang.menu())
        return
    if (msg.get("text") or "").strip() in ("/video", "/qollanma", "🎬 Видео-қўлланма"):   # ҳамма учун (шерик ҳисоботидан олдин)
        send_guide(chat)
        return
    rw = worker_by_uid(uid)
    if msg.get("contact"):
        rw2, txt = link_contact(msg)
        send(chat, txt, kb_for(rw2) if rw2 else CONTACT_KB)
        return
    if not rw:
        send(chat, "Салом! Пастдаги тугмани босинг 👇", CONTACT_KB)
        return
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if text == B_PAY:                                       # ҳамма ролларда: мижозга Payme / Click QR
        send_pay(chat, rw)
        return
    if dict(rw).get("p_edge"):                              # кромка этикеткаси (ҳолат бўлса — расм ва матн шу ерда)
        import edgebot
        if text == B_EDGE:
            edgebot.start(rw, chat)
            return
        if msg.get("photo") and edgebot.on_photo(rw, chat, msg):
            return
        if text and not msg.get("photo") and edgebot.on_text(rw, chat, text):
            return
    if dict(rw).get("driver") and text and not msg.get("photo"):    # шофёр: рейс ва харажат (логистика)
        import logist
        if logist.on_text(rw, chat, text):
            return
    if text in (B_ORDER, B_RECV) or (text and not msg.get("photo")):
        import pickup
        if text == B_ORDER and dict(rw).get("p_order"):
            pickup.order_start(rw, chat)
            return
        if text == B_RECV and dict(rw).get("p_recv"):
            pickup.recv_start(rw, chat)
            return
        if text and not msg.get("photo") and pickup.text_input(rw, chat, text):
            return
    if is_owner(rw) and (text == B_OWN or (owner_only(rw) and not msg.get("photo"))):
        send(chat, owner_view("today"), owner_menu())
        if text != B_OWN:
            send(chat, "👇", kb_for(rw))
        return
    if is_mgr(rw):                                        # менежер: Ц-код карточкалари + кусок омбори
        if text in (B_KUS, "🧩 Кусок"):
            t, kb = kstore_msg(mgr=True)
            send(chat, t, kb)
            return
        if kres_text(rw, chat, text):
            return
        mc_ = re.fullmatch(r"\s*[ЦцCcСсSs]\s*[-№#.]?\s*(\d{1,6})\s*", text or "")
        send(chat, (P.code_card(mc_.group(1)) or f"❓ Ц{mc_.group(1)} топилмади.") if mc_ else MGR_HELP, kb_for(rw))
        return
    if text == B_PICK and dict(rw).get("driver"):
        import pickup
        pickup.driver_list(rw, chat)
        return
    if text in ("/start", "/help", "ℹ️ Ёрдам"):
        send(chat, ((DRIVER_HELP if dict(rw).get("driver") else CONSTR_HELP if is_constr(rw) else STAFF_HELP) if rw["role"] == "staff" else HELP)
             + VIDEO_LINE, kb_for(rw))
        return
    if text == B_SAL:
        send(chat, salary_text(rw), kb_for(rw))
        return
    if text == B_IN:
        new, at = P.att_in(rw.get("kw_id"), rw["id"])
        msg_ = f"✅ {at:%H:%M} — ишга келдингиз. Хайрли иш!" if new else f"ℹ️ Бугун {at:%H:%M} да белгилангансиз."
        if P.is_stajer(rw):
            send(chat, msg_ + "\n🎓 Стажер — ойлик белгиланган, станокка ёзилиш шарт эмас.", kb_for(rw))
            return
        if rw["role"] != "staff" and not P.crew_for(rw):
            last_ = A.q("SELECT * FROM raspil_crew WHERE cancelled=0 AND (by_rw=? OR op_id=?) ORDER BY started_at DESC, id DESC LIMIT 1",
                        (rw["id"], rw.get("kw_id") or -1), one=True)
            rows = []
            if last_ and P.can(rw, last_["machine"] or "arra", "hp" if last_["helper_id"] == rw.get("kw_id") else "op"):
                m_ = last_["machine"] or "arra"
                role_ = "hp" if last_["helper_id"] == rw.get("kw_id") else "op"
                rows.append([(f"✅ Кечагидек: {P.MACHINES.get(m_, m_).split(' (')[0]} — {'ёрдамчи' if role_ == 'hp' else 'бош оператор'}"[:60],
                              f"jr:{m_}:{role_}")])
            rows.append([("🔄 Бошқа станок", "sw:menu")])
            send(chat, msg_ + "\n\n🛠 Бугун қаерда ишлайсиз?", ikb(rows))
            return
        send(chat, msg_, kb_for(rw))
        return
    if text == B_KROY and P.kroy_bot(rw):
        return kroy_start(rw, chat)
    if text == B_KROYS:
        send(chat, kroys_text(rw), kb_for(rw))
        return
    if text == B_KPI:
        send(chat, kroy_kpi_text(rw), kb_for(rw))
        return
    if text in (B_READY, B_READY_OLD):
        send(chat, ready_list_text(7), kb_for(rw))         # фақат тайёрлари (цех жараёни керак эмас — эгаси)
        return
    if text in (B_KUS, "🧩 Кусок"):                                     # кусок: ҳамма учун — омборга киритиш ёки сотиш
        t, kb = kstore_msg()
        send(chat, t, kb)
        return
    if kres_text(rw, chat, text):
        return
    if work_part_text(rw, chat, text):
        return
    if numask_text(rw, chat, text):
        return
    kp_ = A.q("SELECT * FROM raspil_pending WHERE rw_id=? ORDER BY id DESC LIMIT 1", (rw["id"],), one=True)
    if (kp_ and kp_["state"] in ("kno", "km2", "kh") and text and not msg.get("photo")
            and kp_["at"] >= (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
            and text not in (B_IN, B_END, B_SAL, B_SWITCH, B_TODAY)):
        kroy_text(rw, chat, dict(kp_), text)
        return
    if rw["role"] == "staff" and kp_ and kp_["state"] in ("ksize", "kcolor", "kowner", "kpick", "wnote") and text and text not in (B_IN, B_END, B_SAL, B_TODAY):
        rw = dict(rw, role="raspil_kus")                  # кусок жараёни давом этяпти — умумий ишловчига ўтади
    if rw["role"] == "staff":
        if text == B_END:
            r = P.att_out(rw.get("kw_id"))
            send(chat, (f"🏁 {r['out_at'][11:16]} — иш тугади. Келган: {r['in_at'][11:16]}, "
                        f"{P.hm((P._ts(r['out_at']) - P._ts(r['in_at'])).total_seconds() / 3600)}." if r
                        else "❓ Бугун «✅ Ишга келдим» босилмаган."), kb_for(rw))
        elif text == B_TODAY:
            r = A.q("SELECT * FROM k_att WHERE day=? AND kw=?", (date.today().isoformat(), rw.get("kw_id")), one=True)
            send(chat, (f"📊 Бугун: келдингиз {r['in_at'][11:16]}" + (f", кетдингиз {r['out_at'][11:16]}" if r["out_at"] else "")) if r
                 else "📊 Бугун ҳали белгиланмагансиз.", kb_for(rw))
        else:
            send(chat, CONSTR_HELP if is_constr(rw) else STAFF_HELP, kb_for(rw))
        return
    if text in BTN_MACHINE:
        return ask_operator(chat, BTN_MACHINE[text], rw)
    if text == B_SWITCH:
        send(chat, "🔄 Нима қиласиз?", switch_kb(rw))
        return
    if text in (B_OPEN, B_OPEN_OLD):
        txt, kb = open_jobs_msg(rw)
        send(chat, txt, kb or MAIN_KB)
        return
    if text == B_END:
        txt = P.end_crew(rw)
        if txt.startswith("❓"):
            r = P.att_out(rw.get("kw_id"))
            txt = (f"🏁 {r['out_at'][11:16]} — иш тугади (келган: {r['in_at'][11:16]})." if r else txt)
        held = P.cash_rows(rw["id"])
        if held:                                          # мижоздан олган пули — кассага топширишни эслатиш
            txt += f"\n\n💵 Кассага топширинг: {P.cash_total(held)}\n" + "\n".join(f"• {P.cash_label(r)} — {P.cash_fmt(r['amount'], r['cur'])}" for r in held)
        send(chat, txt, MAIN_KB)
        return
    if text == B_TODAY:
        send(chat, today_text(rw), MAIN_KB)
        return
    if text == B_WAIT:
        return wait_start(chat, rw)
    wcs = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND state IN ('wcash0','wcash') AND at>=? ORDER BY id DESC LIMIT 1",
              (rw["id"], (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")), one=True)
    if wcs and text and not msg.get("photo"):
        _lp = A.q("SELECT id FROM raspil_pending WHERE rw_id=? ORDER BY id DESC LIMIT 1", (rw["id"],), one=True)
        if _lp and _lp["id"] == wcs["id"]:                # «💵 Пул олдим»: накладной рақами / сумма
            return cash_text(rw, chat, dict(wcs), text)
    since10 = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    wn = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND state='wnote' AND at>=? ORDER BY id DESC LIMIT 1", (rw["id"], since10), one=True)
    _lt = A.q("SELECT id FROM raspil_pending WHERE rw_id=? ORDER BY id DESC LIMIT 1", (rw["id"],), one=True)
    last_pid = _lt["id"] if _lt else None                 # матнни фақат охирги очилган жараён олади (эскилари аралашмасин)
    mc = re.fullmatch(r"\s*[ЦцCcСсSs]\s*[-№#.]?\s*(\d{1,6})\s*", text or "")
    if mc:                                                # «Ц27» — тўланганми?
        send(chat, P.code_status(mc.group(1)), MAIN_KB)
        return
    mn = re.fullmatch(r"\s*№\s*(\d{1,9})\s*", text or "")
    if mn and P.code_by_no(mn.group(1)):                  # «№7012» — цех накладнойи тўланганми?
        send(chat, P.code_status(P.code_by_no(mn.group(1))), MAIN_KB)
        return
    wr_ = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND state='wref' AND at>=? ORDER BY id DESC LIMIT 1",
              (rw["id"], (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")), one=True)
    if wr_ and wr_["id"] == last_pid and text and not msg.get("photo"):   # эски накладнойга қўшимча: рақами
        dg = re.sub(r"\D", "", text)
        if not 1 <= len(dg) <= 9:
            send(chat, "🔢 Фақат накладной рақамини ёзинг, масалан 6453")
            return
        d0 = P.find_sale_no(dg)
        code = P.new_code(d0["cname"] if d0 else "", ref_no=str(int(dg)), ref_client=d0["cname"] if d0 else "")
        A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (wr_["id"],)); A.db().commit()
        m0 = send(chat, f"🔖 {P.clabel(code)} · ➕ №{int(dg)} га қўшимча" + (f" · 👤 {d0['cname']}" if d0 and d0["cname"] else
                                                                            "\n(эски накладной топилмади — кассир мижозни ўзи танлайди)"))
        if m0 and m0.get("message_id"):
            wait_flow(rw, chat, m0["message_id"], ["w", "c", wr_["machine"] or "arra", str(code)])
        return
    wc_ = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND state='wcli' AND at>=? ORDER BY id DESC LIMIT 1",
              (rw["id"], (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")), one=True)
    if wc_ and wc_["id"] == last_pid and text and not msg.get("photo"):
        cl = P.parse_client(text)
        if not cl:
            send(chat, "👤 Исм ва телефон: Алишер 1131 ёки Алишер 901231131")
            return
        code = P.new_code(cl)
        A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (wc_["id"],)); A.db().commit()
        m0 = send(chat, f"🔖 {P.clabel(code)} · 👤 {cl}")
        if m0 and m0.get("message_id"):
            wait_flow(rw, chat, m0["message_id"], ["w", "c", wc_["machine"] or "arra", str(code)] + ([wc_["iddoc"]] if wc_["iddoc"] else []))
        return
    r2 = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND state IN ('rsum2','jnote') AND at>=? ORDER BY id DESC LIMIT 1",
             (rw["id"], (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")), one=True)
    if r2 and r2["id"] == last_pid and text and not msg.get("photo"):
        if r2["state"] == "jnote":                # накладнойли ровер: изоҳ
            A.db().execute("UPDATE raspil_job SET text=? WHERE id=?", (text[:200], r2["job_id"]))
            A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (r2["id"],)); A.db().commit()
            send(chat, "✅ Сақланди.", MAIN_KB)
            return
        dg = re.sub(r"[\s'’`.,]", "", text)
        m_ = re.fullmatch(r"(\d{3,10})(?:сўм|сум)?", dg, re.I)
        if not m_:
            send(chat, "💰 Фақат сумма ёзинг, масалан 150000")
            return
        amt = int(m_.group(1))
        jid = P.add_wait(rw, "job", 0, machine="rover")
        P.wait_ref(jid, r2["iddoc"], r2["doc_no"], r2["client"])
        P.wait_add_item(jid, "", "Ровер иши", amt, "сўм")
        A.db().execute("UPDATE raspil_pending SET state='wnote', job_id=?, at=? WHERE id=?", (jid, A.now(), r2["id"])); A.db().commit()
        send(chat, f"⚙️ №{r2['doc_no']} — ровер: {_som(amt)}\n\n✍️ Нима қилдингиз? Ёзинг (масалан: 2 та фасад, фрезеровка):",
             ikb([[("✅ Тамом", f"w:skip:{r2['id']}")]]))
        return
    rs_ = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND state='rsum' AND at>=? ORDER BY id DESC LIMIT 1",
              (rw["id"], (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")), one=True)
    if rs_ and rs_["id"] == last_pid and text and not msg.get("photo"):
        dg = re.sub(r"[\s'’`.,]", "", text)
        m_ = re.fullmatch(r"(\d{3,10})(?:сўм|сум|so'?m|сўм)?", dg, re.I)
        if not m_ or int(m_.group(1)) <= 0:
            send(chat, "💰 Фақат сумма ёзинг, масалан 150000")
            return
        P.wait_add_item(rs_["job_id"], "", "Ровер иши", int(m_.group(1)), "сўм")
        A.db().execute("UPDATE raspil_pending SET state='wnote', at=? WHERE id=?", (A.now(), rs_["id"])); A.db().commit()
        send(chat, f"🔖 {P.clabel(P.wait_code(rs_['job_id']))} · Ровер — {_som(int(m_.group(1)))}\n\n✍️ Нима қилдингиз? Ёзинг (масалан: 2 та фасад, фрезеровка):",
             ikb([[("✅ Тамом", f"w:skip:{rs_['id']}")]]))
        return
    rq = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND state='rqty' AND at>=? ORDER BY id DESC LIMIT 1",
             (rw["id"], (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")), one=True)
    if rq and rq["id"] == last_pid and text and not msg.get("photo"):
        nm_ = rq["doc_no"]
        if rq["client"] == "м²":                  # деталь ўлчами → м² ўзи ҳисобланади
            pa = P.parse_area(text)
            if not pa or pa[0] <= 0:
                send(chat, "📏 Ўлчамини ёзинг: 2800 400\n2 та бўлса: 2800 400 2")
                return
            q, lab = pa
            nm_ = f"{nm_} {lab}".strip()
        elif rq["client"] == "сўм":
            dg = re.sub(r"[\s'’`.,]|сўм|сум", "", text, flags=re.I)
            q = float(dg) if dg.isdigit() else 0
            if q <= 0:
                send(chat, "💰 Фақат сумма ёзинг, масалан 150000")
                return
        else:
            nums = re.findall(r"\d+(?:[.,]\d+)?", text)
            q = float(nums[0].replace(",", ".")) if len(nums) == 1 else 0
            if rq["client"] == "м" and q > 50:    # 2800 (мм) → 2.8 м
                q = q / 1000
            if q <= 0:
                send(chat, "🔢 Битта рақам ёзинг, масалан 3")
                return
        picks = P.wait_add_item(rq["job_id"], rq["iddoc"], nm_, q, rq["client"] or "та")
        A.db().execute("UPDATE raspil_pending SET state='rpick' WHERE id=?", (rq["id"],)); A.db().commit()
        send(chat, f"🔖 {P.clabel(P.wait_code(rq['job_id']))} · {P.mlabel(_jmach(rq['id']))}\n" + "\n".join(f"✅ {x['name']} — {P._n(x['m'])} {x.get('unit', '')}" for x in picks),
             ikb([[("➕ Яна", f"rm:{rq['id']}")], [("✅ Тамом", f"kd:{rq['id']}")]]))
        return
    ks = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND state IN ('ksize','kcolor','kowner') AND at>=? ORDER BY id DESC LIMIT 1",
             (rw["id"], (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")), one=True)
    if ks and ks["id"] == last_pid and text and not msg.get("photo"):
        if ks["state"] == "kowner":
            own = P.parse_owner(text)
            if not own:
                send(chat, "👤 Исм ва тел охирги 4 рақами: Алишер 1131\nИшхонаники бўлса — РайГаз", ikb([[("🏭 РайГаз", f"ko:{ks['id']}")]]))
                return
            t, kb = kusok_owner(dict(ks), rw, own)
            send(chat, t, kb)
            return
        if ks["state"] == "kcolor":
            d = P.json.loads(ks["client"] or "{}")
            c = P.kus_color(text)
            if not c:
                send(chat, COLOR_ASK)
                return
            d["c"] = c
        else:
            d = P.parse_kus(text)
            if not d:
                send(chat, "📏 Иккита ўлчам ёзинг, масалан: 2800 1000\n2 та бўлса: 2800 1000 2")
                return
            d["c"] = P.kus_color(d["c"]) or ""
        st = "kowner" if d["c"] else "kcolor"
        A.db().execute("UPDATE raspil_pending SET state=?, client=? WHERE id=?",
                       (st, P.json.dumps(d, ensure_ascii=False), ks["id"])); A.db().commit()
        head = f"🧩 {ks['doc_no']} {d['c']} {d['s']}" + (f" × {d['q']} та" if d.get("q", 1) > 1 else "") + f" — {P._n(d['m'] * d.get('q', 1))} м²"
        if st == "kcolor":
            send(chat, f"{head}\n\n{COLOR_ASK}")
        else:
            send(chat, f"{head}\n\n" + OWNER_ASK, ikb([[("🏭 РайГаз", f"ko:{ks['id']}")]]))
        return
    kp = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND state IN ('wpick','wmeter','wcolor') AND at>=? ORDER BY id DESC LIMIT 1",
             (rw["id"], (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")), one=True)
    if kp and kp["id"] == last_pid and text and not msg.get("photo"):
        mach = A.q("SELECT machine FROM raspil_job WHERE id=?", (kp["job_id"],), one=True)["machine"]
        if kp["state"] == "wcolor":                     # овал кромка: аввал ранги
            c = P.kus_color(text)
            if not c:
                send(chat, "🎨 Кромка рангини ёзинг (масалан: оқ глянец, кашемир):")
                return
            A.db().execute("UPDATE raspil_pending SET state='wmeter', client=? WHERE id=?", (c, kp["id"])); A.db().commit()
            send(chat, f"🎞 {_klabel(kp['doc_no']) or kp['doc_no']} · 🎨 {c}\n\n📏 Неча метр? Ёзинг:")
            return
        if kp["state"] == "wmeter":
            try:
                m = float(re.sub(r"[^0-9.,]", "", text).replace(",", "."))
            except ValueError:
                m = 0
            if m <= 0:
                send(chat, "📏 Фақат рақам ёзинг, масалан 3.5")
                return
            picks = P.wait_add_item(kp["job_id"], kp["iddoc"], kp["doc_no"] + (f" · {kp['client']}" if mach == "oval" and kp["client"] else ""), m)
            A.db().execute("UPDATE raspil_pending SET state='wpick' WHERE id=?", (kp["id"],)); A.db().commit()
            code = P.wait_code(kp["job_id"])
            send(chat, f"🔖 {P.clabel(code)}\n" + "\n".join(f"✅ {_klabel(x['name'])} — {P._n(x['m'])} м" for x in picks),
                 ikb([[("➕ Яна кромка", f"kf:{kp['id']}:-:0")], [("✅ Тамом", f"kd:{kp['id']}")]]))
            return
        if re.search(r"[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ]{3,}", text):
            found = P.kromka_search(text, mach)
            if found:
                send(chat, "🔎 Топилди:", ikb([[(_klabel(nm)[:40], f"ki:{kp['id']}:{k}")] for k, nm in found] + [[("‹ Рўйхат", f"kf:{kp['id']}:-:0")]]))
            else:
                send(chat, "🔎 Топилмади. Бошқача ёзинг ёки рўйхатдан танланг.", ikb([[("‹ Рўйхат", f"kf:{kp['id']}:-:0")]]))
            return
    since5 = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    if wn and wn["id"] == last_pid and (text or (msg.get("photo") and wn["at"] >= since5)):
        code = P.wait_code(wn["job_id"])
        if msg.get("photo"):                              # кромка/лист расми — менежер саҳифада кўради
            import ready as R
            path = os.path.join(R.photo_dir(), f"bot_{date.today():%Y%m%d}_{rw['id']}_{msg['message_id']}.jpg")
            if download_try(msg["photo"][-1]["file_id"], path):
                A.db().execute("UPDATE raspil_job SET photo=? WHERE id=? AND status='wait'", (path, wn["job_id"]))
        if text:
            P.wait_note(wn["job_id"], text)
        A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (wn["id"],))
        A.db().commit()
        rn = _refno(wn["job_id"])
        if wn["bot_msg"] and not rn:
            edit(chat, wn["bot_msg"], f"🔖 {P.clabel(code)} — мижозга айтинг.")
        send(chat, f"✅ №{rn} ровер иши менежерга юборилди." if rn else f"✅ {P.clabel(code)} сақланди.", MAIN_KB)
        P.notify_mgr(code)
        return
    if wn:
        A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (wn["id"],)); A.db().commit()
    photo = None
    if msg.get("photo"):
        photo = msg["photo"][-1]["file_id"]
    elif msg.get("document") and (msg["document"].get("mime_type") or "").startswith("image/"):
        photo = msg["document"]["file_id"]
    if photo:
        import ready as R
        path = os.path.join(R.photo_dir(), f"bot_{date.today():%Y%m%d}_{rw['id']}_{msg['message_id']}.jpg")
        if not download_try(photo, path):
            A.db().execute("INSERT INTO raspil_photo_retry(rw_id, chat, msg_id, file_id, text, path, at, tries, last_at, state) "
                           "VALUES(?,?,?,?,?,?,?,0,?,'')", (rw["id"], chat, msg["message_id"], photo, text, path, A.now(), A.now()))
            A.db().commit()
            send(chat, "⏳ Интернет секин. Расм ўзи юкланади — кутинг.")
            return
        send_typing(chat)
        pid, doc, st, info = new_pending(rw, chat, msg["message_id"], path, text)
        show(rw, chat, pid, doc, st, info)
        return
    nums = NUMRE.findall(text)
    if nums:
        import ready as R
        since = (datetime.now() - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
        p = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND state='wait_no' AND at>=? ORDER BY id DESC LIMIT 1", (rw["id"], since), one=True)
        doc, st, info = R.pick_doc(nums[0], "")
        if p:
            show(rw, chat, p["id"], doc, st, info)
            return
        pid = A.db().execute("INSERT INTO raspil_pending(rw_id, chat_id, at, user_msg, state) VALUES(?,?,?,?,?)",
                             (rw["id"], chat, A.now(), msg["message_id"], "")).lastrowid
        A.db().commit()
        show(rw, chat, pid, doc, st, info)
        return
    send(chat, HELP, MAIN_KB)


def send_typing(chat):
    try:
        api("sendChatAction", chat_id=chat, action="typing")
    except tg.TgError:
        pass


def result_text(rw, j):
    """Ишчига қисқа натижа."""
    det = json.loads(j["detail"] or "[]")
    pi = P.part_info(det) or {}
    out = [f"✅ Ҳисобланди: {P._n(j['sheets'])} лист" + (f" + 45° {P._n(j['m45'])} м" if j["m45"] else ""),
           f"📄 №{j['doc_no']} — {j['client']}"]
    if pi.get("left", 0) > 0.001:
        parts = []
        hl, el = pi.get("hdf_left", 0), pi.get("extra_left", 0)
        other = pi["left"] - max(hl, 0) - max(el, 0)
        if other > 0.001:
            parts.append(f"лист {P._n(other)}")
        if hl > 0.001:
            parts.append(f"ХДФ {P._n(hl)}")
        if el > 0.001:
            parts.append(f"двухпилка {P._n(el)}")
        out.append("⏳ Қолди: " + ", ".join(parts) + " — кесилганда расмни яна ташланг.")
    else:
        out.append("✔️ Накладной тўлиқ кесилди.")
    t = P.day_totals(j["day"]).get(j["op_id"]) or {}
    who = P.wname(j["op_id"]) + (f" + {P.wname(j['helper_id'])}" if j["helper_id"] else "")
    out.append(f"👷 {who}" + (f" · бугун {P._n(t.get('d', 0) + t.get('n', 0))} лист" if t else ""))
    return "\n".join(out)


def _item(rw, pid, key):
    p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (pid, rw["id"]), one=True)
    if not p:
        return None, None
    st = P.items_state(p["iddoc"])
    return dict(p), next((i for i in st["items"] if i["key"] == key), None)


def pick_item(rw, chat, mid, pid, key):
    p, it = _item(rw, pid, key)
    if not it:
        return "Эскирган"
    if it["left"] <= 0.001:
        t, kb = preview(rw, p, "ℹ️ Бу лист тўлиқ кесилган.")
        edit(chat, mid, t, kb)
        return ""
    A.db().execute("UPDATE raspil_pending SET state='cutting' WHERE id=?", (pid,)); A.db().commit()
    edit(chat, mid, f"📄 №{p['doc_no']} — {p['client']}\n\n✂️ Кесиляпти:\n{it['label']}\n(кўпи билан {P._n(it['left'])} та)\n\n"
         "👇 Кесиб бўлгач «✅ Кесилди»ни босинг.",
         ikb([[("✅ Кесилди", f"c:{pid}:{key}")], [("‹ Бошқа лист", f"b:{pid}")]]))
    return ""


def ask_count(rw, chat, mid, pid, key):
    p, it = _item(rw, pid, key)
    if not it or it["left"] <= 0.001:
        return "Эскирган"
    mx = int(it["left"]) if it["left"] >= 1 else 0
    nums = [(str(k), f"q:{pid}:{key}:{k}") for k in range(1, min(mx, 20) + 1)]
    rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
    if mx > 20 or it["left"] - mx > 0.001 or mx == 0:
        rows.append([(f"Ҳаммаси — {P._n(it['left'])} та", f"q:{pid}:{key}:{it['left']}")])
    rows.append([("‹ Орқага", f"i:{pid}:{key}")])
    numask_open(rw, chat, mid, dict(t="q", pid=pid, key=key, mx=it["left"]))
    edit(chat, mid, f"📄 №{p['doc_no']}\n{it['label']}\n\n🔢 Нечта лист кесилди? (кўпи билан {P._n(it['left'])})\nТугмани босинг ёки рақамини ёзинг:", ikb(rows))
    return ""


def numask_open(rw, chat, mid, d):
    """Сонни тугма ўрнига ёзиш мумкин (30 дан кўп листлар учун)."""
    A.db().execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state, client, bot_msg) VALUES(?,?,?,?,?,?)",
                   (rw["id"], chat, A.now(), "numask", json.dumps(d), mid))
    A.db().commit()


def numask_text(rw, chat, text):
    """«numask» ҳолатида рақам ёзилди → худди тугма босилгандек. -> True агар шу жараённики."""
    p = A.q("SELECT * FROM raspil_pending WHERE rw_id=? ORDER BY id DESC LIMIT 1", (rw["id"],), one=True)
    if not (p and p["state"] == "numask" and text and p["at"] >= (datetime.now() - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")):
        return False
    m_ = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:та|ta|лист|list)?\s*", text, re.I)
    if not m_:
        return False
    n = float(m_.group(1).replace(",", "."))
    d = json.loads(p["client"] or "{}")
    A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (p["id"],)); A.db().commit()
    if n <= 0:
        send(chat, "🔢 0 дан катта сон ёзинг.")
        return True
    if d.get("t") == "q":
        if d.get("mx") and n > d["mx"] + 0.001:
            send(chat, f"⚠️ Бу листдан кўпи билан {P._n(d['mx'])} та қолган. Тугмадан танланг ёки тўғри сонни ёзинг.")
            numask_open(rw, chat, p["bot_msg"], d)
            return True
        note = save_count(rw, chat, p["bot_msg"], d["pid"], d["key"], n)
        if note and not note.startswith("✅"):
            send(chat, "⚠️ " + note)
        return True
    if d.get("t") == "wn":
        wait_flow(rw, chat, p["bot_msg"], ["w", "n", d["kind"], P._n(n).replace(" ", "")] + ([d["m"], d["cd"]] if d.get("m") else []))
        return True
    return False


def save_count(rw, chat, mid, pid, key, n):
    p, it = _item(rw, pid, key)
    if not p:
        return "Эскирган"
    jid, err = P.record_item(rw, p["iddoc"], p["doc_no"], p["client"], key, n, p["photo"], p["user_msg"], machine=pmachine(rw, dict(p)))
    if err:
        return err
    j = A.q("SELECT * FROM raspil_job WHERE id=?", (jid,), one=True)
    A.db().execute("UPDATE raspil_job SET ack_id=? WHERE id=?", (mid, jid)); A.db().commit()
    t = P.day_totals(j["day"]).get(j["op_id"]) or {}
    note = (f"✅ {it['label']}: {P._n(n)} та → {P._n(j['sheets'])} лист ҳисобланди"
            + (f"\n📊 Бугун: {P._n(t.get('d', 0) + t.get('n', 0))} лист" if t else ""))
    text, kb = preview(rw, p, note)
    kb["inline_keyboard"].insert(-1, [{"text": "🗑 Охиргисини бекор қилиш", "callback_data": f"x:{jid}"}])
    edit(chat, mid, text, kb)
    try:
        if not any(i["left"] > 0.001 for i in P.items_state(p["iddoc"])["items"]):
            remind_open(rw, chat, p["iddoc"])
    except Exception:
        A.errlog.exception("remind")
    return "✅ Ҳисобланди"


def _nat(name):
    """Табиий тартиб: бренд (ABC), кейин ўлчам рақам бўйича — «19/1» «21/1» «35/1» «42/1»."""
    t = re.sub(r"(?i)^кромка\s+", "", name or "")
    return [(0, float(x.replace(",", "."))) if re.fullmatch(r"\d+(?:[.,]\d+)?", x) else (1, x.lower()) for x in re.findall(r"\d+(?:[.,]\d+)?|[^\d\s/]+", t)]


def _fold_key(name):
    """Папкалар: аввал «Услуга ПВХ», кейин «Huali Central Asia», қолгани — табиий тартибда."""
    n = (name or "").lower()
    return (0 if "услуг" in n else 1 if "central asia" in n else 2, _nat(name))


def _klabel(nm):
    return re.sub(r"(?i)^услуга\s+", "", nm).strip() if nm.lower().startswith("услуга") else _wlabel(nm)


def kromka_menu(pid, machine, fid=None, page=0, head=""):
    """Кромка танлаш: 1С папкалари → кромкалар (тугма). Қидириш: 3 ҳарф ёзса бўлади."""
    tree, roots = P.kromka_tree(machine)
    if fid and fid in tree:
        node = tree[fid]
        btn = [(f"📁 {tree[k]['name'].replace('Кромка ', '')[:40]}", f"kf:{pid}:{k}:0") for k in sorted(node["kids"], key=lambda k: _fold_key(tree[k]["name"]))] + \
              [(_klabel(nm)[:40] or nm[:40], f"ki:{pid}:{k}") for k, nm in sorted(node["items"], key=lambda x: _nat(_klabel(x[1])))]
        title = node["name"]
        back = f"kf:{pid}:{node['parent'] if node['parent'] in tree else '-'}:0"
    else:                                   # биринчи ойна: энг кўп ишлатиладиганлар, кейин папкалар
        try:
            top = P.kromka_top(machine)
        except Exception:
            top = []
        btn = [(f"⭐ {_klabel(nm)[:38]}", f"ki:{pid}:{k}") for k, nm in top]
        pop = P.item_popularity() if top else {}
        def fpop(k):
            node = tree[k]
            return -(sum(pop.get(i, 0) for i, _ in node["items"]) + sum(sum(pop.get(i, 0) for i, _ in tree[c]["items"]) for c in node["kids"]))
        folders = [k for r in roots for k in tree[r]["kids"]]
        shown = {k for k, _ in top}
        btn += [(_klabel(nm)[:40], f"ki:{pid}:{k}") for r in roots for k, nm in tree[r]["items"] if k not in shown]   # хизматлар — тўғридан-тўғри
        btn += [(f"📁 {tree[k]['name'].replace('Кромка ', '')[:40]}", f"kf:{pid}:{k}:0") for k in sorted(folders, key=lambda k: _fold_key(tree[k]["name"]))]
        title, back = "Кромка", None
    per = 12
    rows = [[b] for b in btn[page * per:(page + 1) * per]]
    nav = []
    if page > 0:
        nav.append(("◀️", f"kf:{pid}:{fid or '-'}:{page - 1}"))
    if (page + 1) * per < len(btn):
        nav.append(("▶️", f"kf:{pid}:{fid or '-'}:{page + 1}"))
    if nav:
        rows.append(nav)
    if back:
        rows.append([("‹ Орқага", back)])
    rows.append([("Ўтказиш", f"w:skip:{pid}")])
    if machine == "oval" and not back:
        return head + "〰️ Овал кромка — размерини танланг:", ikb(rows)
    return head + f"🎞 {title}:\n(ёки номидан 3 ҳарф ёзинг)", ikb(rows)


def _jmach(pid):
    r = A.q("SELECT j.machine FROM raspil_pending p JOIN raspil_job j ON j.id=p.job_id WHERE p.id=?", (pid,), one=True)
    return (r["machine"] if r else None) or "dp"


def _slabel(nm):
    return re.sub(r"^(Распил|Услуга сверления|Услуга)\s+", "", re.sub(r"\s+", " ", nm)).strip()


def rsv_menu(pid, head=""):
    mach = _jmach(pid)
    try:
        its = P.raspil_services(mach)
    except Exception:
        A.errlog.exception("raspil services")
        its = []
    rows = [[(_slabel(nm)[:40], f"rs:{pid}:{k}")] for k, nm, u in its[:14]]
    rows.append([("✅ Тамом", f"kd:{pid}")])
    return head + {"pris": "🔩 Нима қилдингиз?", "utol": "📏 Утолщенка — нима қилдингиз?"}.get(mach, "🪚 Нима қилдингиз?"), ikb(rows)


def rsv_flow(rw, chat, mid, parts):
    pid = int(parts[1])
    p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (pid, rw["id"]), one=True)
    if not p or not p["job_id"]:
        return "Эскирган"
    if parts[0] == "rm":                          # яна хизмат
        A.db().execute("UPDATE raspil_pending SET state='rpick' WHERE id=?", (pid,)); A.db().commit()
        t, kb = rsv_menu(pid)
        edit(chat, mid, t, kb)
        return ""
    it = {k: (nm, u) for k, nm, u in P.raspil_services(_jmach(pid))}.get(parts[2])
    if not it:
        return "Топилмади"
    nm, u = it
    A.db().execute("UPDATE raspil_pending SET state='rqty', iddoc=?, doc_no=?, client=?, bot_msg=? WHERE id=?", (parts[2], nm[:120], u, mid, pid))
    A.db().commit()
    edit(chat, mid, f"{ {'pris': '🔩', 'utol': '📏'}.get(_jmach(pid), '🪚') } {nm}\n\n" + ("📏 Деталь ўлчами? Ёзинг:\n2800 400\n(2 та бўлса: 2800 400 2)" if u == "м²" else
                                        "💰 Суммаси қанча? (масалан 150000)" if u == "сўм" else
                                        "📏 Неча метр? (масалан 2.8)" if u == "м" else "🔢 Неча та? Рақам ёзинг:"))
    return ""


def _refno(jid):
    j = A.q("SELECT items FROM raspil_job WHERE id=?", (jid,), one=True) if jid else None
    r = (json.loads(j["items"] or "{}").get("wait") or {}).get("ref") if j else None
    return r["no"] if r else None


def kroy_start(rw, chat, mid=None):
    pid = A.db().execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state) VALUES(?,?,?,?)", (rw["id"], chat, A.now(), "kno")).lastrowid
    A.db().commit()
    t = "📐 Крой. Накладной рақами?"
    if mid:
        edit(chat, mid, t)
    else:
        send(chat, t)


def kroy_text(rw, chat, p, text):
    """Крой: № → ЛДСП м² → ХДФ м² → сақлаш. -> True агар матн қабул қилинган бўлса."""
    if p["state"] == "kno":
        m = re.search(r"\d{2,7}", text or "")
        if not m:
            send(chat, "📐 Накладной рақамини ёзинг, масалан 6262")
            return True
        A.db().execute("UPDATE raspil_pending SET state='km2', doc_no=? WHERE id=?", (m.group(0), p["id"])); A.db().commit()
        send(chat, f"📄 №{m.group(0)}\n\n📐 ЛДСП/ЛМДФ неча м²? (Базисдаги жами)")
        return True
    nums = re.findall(r"\d+(?:[.,]\d+)?", text or "")
    v = float(nums[0].replace(",", ".")) if len(nums) == 1 else -1
    if v < 0:
        send(chat, "🔢 Битта рақам ёзинг, масалан 12.5")
        return True
    if p["state"] == "km2":
        A.db().execute("UPDATE raspil_pending SET state='kh', client=? WHERE id=?", (str(v), p["id"])); A.db().commit()
        send(chat, "📐 ХДФ/ДВП неча м²?", ikb([[("0 — йўқ", f"ky:{p['id']}")]]))
        return True
    kroy_save(rw, chat, dict(p), v)
    return True


def kroy_save(rw, chat, p, hdf, mid=None):
    main = float(p["client"] or 0)
    kid = P.kroy_add(rw, p["doc_no"], main, hdf)
    A.db().execute("UPDATE raspil_pending SET state='done', job_id=? WHERE id=?", (kid, p["id"])); A.db().commit()
    sm, sh = P.kroy_sheets(main, hdf)
    t = (f"✅ №{p['doc_no']} крой: ЛДСП {P._n(main)} м² ({P._n(round(sm, 2))} лист)"
         + (f", ХДФ {P._n(hdf)} м² ({P._n(round(sh, 2))} лист)" if hdf else ""))
    kb = ikb([[("🗑 Хато — бекор", f"kx0:{kid}")]])
    if mid:
        edit(chat, mid, t, kb)
    else:
        send(chat, t, kb)


def kstore_msg(mgr=False):
    """Кусок омбори менюси: 📋 кўриш / 📥 киритиш / ✏️ омборда йўқ — сотиш."""
    st, rs = P.kusok_stock(), P.kusok_reserved()
    t = f"🧩 Кусок омбори — {len(st)} та, {P._n(sum(k['m2'] for k in st))} м²" if st else "🧩 Кусок омбори бўш."
    if rs:
        t += f"\n🔒 Брон: {len(rs)} та"
    rows = [[("📋 Омборни кўриш", "sw:ksee")]]
    if not mgr:
        rows += [[("📥 Омборга киритиш", "sw:kin")], [("✏️ Омборда йўқ — сотиш", f"w:m:{P.SALE}")]]
    return t, ikb(rows)


def _kown(o):
    return ("🏭 " if o == P.OWNER_CO else "👤 ") + (o or "?")


def kstore_list():
    """Омбор: аввал эгалари (РайГаз / мижозлар) — ичида бир хил кусоклар жамланган."""
    st, rs = P.kusok_stock(), P.kusok_reserved()
    rows = [[("‹ Орқага", "sw:kst")]]
    if not st and not rs:
        return "📋 Омборда ҳозир кусок йўқ.\nКиритиш учун: ‹ Орқага → «📥 Омборга киритиш».", ikb(rows)
    own = {}
    for k in st + rs:
        o = own.setdefault(k["owner"] or "", dict(rep=k["id"], n=0, m2=0.0, r=0))
        if k["status"] == "in":
            o["n"] += 1; o["m2"] += k["m2"]
        else:
            o["r"] += 1
    order = sorted(own.items(), key=lambda x: (x[0] != P.OWNER_CO, x[0].lower()))
    rows = [[((f"{_kown(o)} — {v['n']} та, {P._n(v['m2'])} м²" + (f" · 🔒 {v['r']} брон" if v["r"] else ""))[:64], f"kg:{v['rep']}")]
            for o, v in order] + rows
    t = f"📋 Омборда: {len(st)} та, {P._n(sum(k['m2'] for k in st))} м²" + (f"\n🔒 Брон: {len(rs)} та" if rs else "")
    return t + "\n\nКимники? Танланг:", ikb(rows)


def kstore_owner(kid):
    """Бир эганинг кусоклари: бир хиллари жамланган (× 9 та)."""
    k = A.q("SELECT * FROM raspil_kusok WHERE id=?", (kid,), one=True)
    owner = k["owner"] if k else ""
    st = [x for x in P.kusok_stock() if x["owner"] == owner]
    rs = [x for x in P.kusok_reserved() if x["owner"] == owner]
    rows = []
    for g, n, m2 in P.kusok_groups(st):
        rows.append([(f"{P.kusok_label(g, owner=False)}" + (f" × {n} та" if n > 1 else "") + f" — {P._n(m2)} м²", f"kb:{g['id']}")])
    for g, n, m2 in P.kusok_groups(rs):
        rows.append([(f"🔒 БРОН {P.kusok_res_label(g)} · {P.kusok_label(g, owner=False)}" + (f" × {n}" if n > 1 else ""), f"kr:{g['id']}")])
    rows.append([("‹ Омбор", "sw:ksee")])
    t = f"{_kown(owner)} — {len(st)} та, {P._n(sum(x['m2'] for x in st))} м²"
    if not st and not rs:
        t += "\nБу эганинг кусоги қолмади."
    elif st:
        t += "\n\nСотиш ёки брон учун кусокни босинг:"
    return t, ikb([[(b[0][:64], b[1]) for b in r] for r in rows])


def _kinfo(k, n=1):
    k = dict(k)
    return f"🧩 {P.kusok_label(k)}" + (f" × {n} та" if n > 1 else "") + f" — {P._n(k['m2'] * n)} м²"


def _kref(s_):
    """«12.3» → (12, 3); «12» → (12, 1)."""
    a, _, b = str(s_).partition(".")
    return int(a), int(b or 1)


def kstore_sell(rw, chat, mid, parts):
    """kg:<kid> — эгаси; kb:<kid>[:n] — нечта/меню; kc:<kid>:<code>:<n> — Ц-кодга; kn:<kid>:<n> — накладнойга брон;
    kr:<kid> — брон карточкаси; kv:<kid> — қайтариш (шу брондаги ҳаммаси)."""
    kid = int(parts[1])
    if parts[0] == "kg":
        t, kb = kstore_owner(kid)
        edit(chat, mid, t, kb)
        return ""
    k = A.q("SELECT k.*, j.wcode FROM raspil_kusok k LEFT JOIN raspil_job j ON j.id=k.sold_job WHERE k.id=?", (kid,), one=True)
    if parts[0] in ("kr", "kv"):
        if not k or k["status"] != "res":
            t, kb = kstore_list()
            edit(chat, mid, "⚠️ Бу кусок брон эмас.\n\n" + t, kb)
            return ""
        ids = P.kusok_same(kid)
        if parts[0] == "kv" and len(parts) < 3 and len(ids) > 1:     # нечтасини қайтаради?
            nums = [(str(i), f"kv:{kid}:{i}") for i in range(1, min(len(ids), 20) + 1)]
            rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
            if len(ids) > 20:
                rows.append([(f"Ҳаммаси ({len(ids)})", f"kv:{kid}:{len(ids)}")])
            rows.append([("‹ Орқага", f"kr:{kid}")])
            edit(chat, mid, f"{_kinfo(k, len(ids))}\n🔒 Брон: {P.kusok_res_label(dict(k))}\n\n↩️ Нечтасини омборга қайтарасиз?", ikb(rows))
            return ""
        if parts[0] == "kv":
            ids = ids[:int(parts[2])] if len(parts) > 2 else ids
            for i in ids:
                ok, txt = P.kusok_return(i, rw)
            t, kb = kstore_owner(kid)
            edit(chat, mid, f"↩️ Омборга қайтарилди: {P.kusok_label(dict(k))}" + (f" × {len(ids)} та" if len(ids) > 1 else "") + "\n\n" + t, kb)
            return "Қайтарилди"
        edit(chat, mid, f"{_kinfo(k, len(ids))}\n\n🔒 Брон: {P.kusok_res_label(dict(k))} · {k['sold_by'] or '?'} · {(k['sold_at'] or '')[5:16]}\n"
                        "Бошқа ҳеч ким сота олмайди. Сотилмаса — омборга қайтаринг.",
             ikb([[("↩️ Қайтариш — омборга", f"kv:{kid}")], [("‹ Орқага", f"kg:{kid}")]]))
        return ""
    if not k or k["status"] != "in":
        t, kb = kstore_list()
        edit(chat, mid, "⚠️ Бу кусок аллақачон сотилган ёки брон қилинган.\n\n" + t, kb)
        return "Сотилган"
    have = len(P.kusok_same(kid))
    if parts[0] == "kb":
        if len(parts) < 3 and have > 1:              # бир хилидан нечтаси?
            nums = [(str(i), f"kb:{kid}:{i}") for i in range(1, min(have, 20) + 1)]
            rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
            if have > 20:
                rows.append([(f"Ҳаммаси ({have})", f"kb:{kid}:{have}")])
            rows.append([("‹ Орқага", f"kg:{kid}")])
            edit(chat, mid, f"{_kinfo(k, have)}\n\n🔢 Нечтаси?", ikb(rows))
            return ""
        n = min(int(parts[2]) if len(parts) > 2 else 1, have)
        rows = [[("📄 Накладнойга брон (крой)", f"kn:{kid}:{n}")]]
        if not is_mgr(rw):
            rows.append([("🆕 Янги мижоз (Ц-код)", f"kc:{kid}:0:{n}")])
            rows += [[(f"{P.clabel(c)} · {lbl}"[:60], f"kc:{kid}:{c}:{n}")] for c, lbl in P.open_codes()]
        rows.append([("‹ Орқага", f"kg:{kid}")])
        edit(chat, mid, f"{_kinfo(k, n)}\n\nКимга? Накладнойга брон ёки мижоз коди:", ikb(rows))
        return ""
    if parts[0] == "kn":
        n = int(parts[2]) if len(parts) > 2 else 1
        A.db().execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state, iddoc, bot_msg) VALUES(?,?,?,?,?,?)",
                       (rw["id"], chat, A.now(), "kres", f"{kid}.{n}", mid))
        A.db().commit()
        edit(chat, mid, f"{_kinfo(k, n)}\n\n📄 Накладной рақамини ёзинг (масалан: 6262):")
        return ""
    n = int(parts[3]) if len(parts) > 3 else 1
    return wait_flow(rw, chat, mid, ["w", "c", P.SALE, parts[2], f"{kid}.{n}"])


def kres_text(rw, chat, text):
    """Накладной рақами ёзилди → кусок брон. -> True агар матн шу жараённики бўлса."""
    p = A.q("SELECT * FROM raspil_pending WHERE rw_id=? ORDER BY id DESC LIMIT 1", (rw["id"],), one=True)
    if not (p and p["state"] == "kres" and text and p["at"] >= (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")):
        return False
    if text in (B_KUS, "🧩 Кусок", B_IN, B_END, B_SAL, B_SWITCH, B_TODAY):
        return False
    if not re.search(r"\d", text):
        send(chat, "📄 Фақат накладной рақамини ёзинг, масалан: 6262")
        return True
    kid, n = _kref(p["iddoc"])
    k = A.q("SELECT * FROM raspil_kusok WHERE id=?", (kid,), one=True)
    got, txt = 0, "Бу кусок омборда йўқ"
    for i in (P.kusok_same(kid, n) if k and k["status"] == "in" else []):
        ok, txt = P.kusok_reserve(i, text, rw)
        got += ok
    A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (p["id"],)); A.db().commit()
    send(chat, (f"🔒 {txt}: {P.kusok_label(dict(k))}" + (f" × {got} та" if got > 1 else "") +
                "\n1С'да шу накладной проведен бўлса — ўзи «сотилди» бўлади." if got else "⚠️ " + txt),
         ikb([[("‹ Омбор", "sw:ksee")]]))
    return True


def kusok_menu(pid, head=""):
    try:
        types = P.kusok_types()
    except Exception:
        A.errlog.exception("kusok types")
        types = []
    rows = [[(nm[:40], f"ks:{pid}:{k}")] for k, nm in types[:12]]
    rows.append([("✅ Тамом", f"kz:{pid}")])
    return head + "🧩 Қайси кусок?", ikb(rows)


def _kdone(p, picks=None, kid=None):
    """Кусок сақлангач: сотиш — мижоз коди рўйхати; омборга — қўшилгани."""
    if p["job_id"]:
        picks = picks if picks is not None else (P.json.loads(A.q("SELECT items FROM raspil_job WHERE id=?", (p["job_id"],), one=True)["items"] or "{}")
                                                 .get("wait", {}).get("pick", []))
        gp = {}
        for x in picks:
            g = gp.setdefault((x["name"], x.get("unit", "м")), [0, 0.0]); g[0] += 1; g[1] += x["m"]
        return (f"🔖 {P.clabel(P.wait_code(p['job_id']))} · Кусок\n" + "\n".join(f"✅ {nm}" + (f" × {c} та" if c > 1 else "") + f" — {P._n(m)} {u}"
                                                                     for (nm, u), (c, m) in gp.items()),
                ikb([[("➕ Яна кусок", f"kk:{p['id']}")], [("✅ Тамом", f"kz:{p['id']}")]]))
    kids = kid if isinstance(kid, list) else [kid]
    ks = [A.q("SELECT * FROM raspil_kusok WHERE id=?", (i,), one=True) for i in kids]
    k = ks[0]
    n = f" × {len(ks)} та" if len(ks) > 1 else ""
    return (f"📥 Омборга қўшилди:\n✅ {P.kusok_label(dict(k))}{n} — {P._n(sum(x['m2'] for x in ks))} м²",
            ikb([[("➕ Яна кусок", f"kk:{p['id']}")], [("✅ Тамом", f"kz:{p['id']}")]]))


def kusok_owner(p, rw, owner):
    """Ўлчам (pending.client да) + эгаси → омборга ёки мижоз кодига (сотилди)."""
    d = P.json.loads(p["client"] or "{}")
    kid, picks = [], None
    for _ in range(int(d.get("q") or 1)):
        if p["job_id"]:
            k = P.kusok_add(rw, p["iddoc"], p["doc_no"], d.get("c", ""), d.get("s", ""), d.get("m", 0), owner, status="sold", src="direct")
            picks = P.kusok_sell(k, p["job_id"], rw)
        else:
            k = P.kusok_add(rw, p["iddoc"], p["doc_no"], d.get("c", ""), d.get("s", ""), d.get("m", 0), owner)
        kid.append(k)
    A.db().execute("UPDATE raspil_pending SET state='kpick' WHERE id=?", (p["id"],)); A.db().commit()
    return _kdone(p, picks, kid)


COLOR_ASK = "🎨 Ранги қандай? Ёзинг:\n(масалан: кашемир, оқ, дуб сонома)"
OWNER_ASK = "👤 Кимники?\nИшхонаники — «🏭 РайГаз» босинг.\nМижозники — исми ва тел охирги 4 рақами: Алишер 1131"


def kusok_flow(rw, chat, mid, parts):
    pid = int(parts[1])
    p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (pid, rw["id"]), one=True)
    if not p:
        return "Эскирган"
    if parts[0] == "kk":                          # яна кусок
        A.db().execute("UPDATE raspil_pending SET state='kpick' WHERE id=?", (pid,)); A.db().commit()
        t, kb = kusok_menu(pid, "" if p["job_id"] else "📥 Омборга. ")
        edit(chat, mid, t, kb)
        return ""
    if parts[0] == "kz":                          # тамом
        A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (pid,)); A.db().commit()
        edit(chat, mid, f"✅ {P.clabel(P.wait_code(p['job_id']))} сақланди — мижозга айтинг." if p["job_id"] else "✅ Омборга сақланди.")
        if p["job_id"]:
            P.notify_mgr(P.wait_code(p["job_id"]))
        return ""
    if parts[0] == "kx":                          # омбордаги кусок сотилди (бир хилидан нечтаси — сўралади)
        kid = int(parts[2])
        same = P.kusok_same(kid)
        k0 = A.q("SELECT * FROM raspil_kusok WHERE id=? AND status='in'", (kid,), one=True)
        if not k0 or not p["job_id"]:
            return "Бу кусок йўқ"
        if len(parts) < 4 and len(same) > 1:
            nums = [(str(i), f"kx:{pid}:{kid}:{i}") for i in range(1, min(len(same), 20) + 1)]
            rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
            if len(same) > 20:
                rows.append([(f"Ҳаммаси ({len(same)})", f"kx:{pid}:{kid}:{len(same)}")])
            rows.append([("‹ Орқага", f"ks:{pid}:{k0['gid']}")])
            edit(chat, mid, f"{_kinfo(k0, len(same))}\n\n🔢 Нечтаси?", ikb(rows))
            return ""
        picks = None
        for i in P.kusok_same(kid, int(parts[3]) if len(parts) > 3 else 1):
            picks = P.kusok_sell(i, p["job_id"], rw) or picks
        if picks is None:
            return "Бу кусок йўқ"
        A.db().execute("UPDATE raspil_pending SET state='kpick' WHERE id=?", (pid,)); A.db().commit()
        t, kb = _kdone(dict(p), picks)
        edit(chat, mid, t, kb)
        return "Сотилди"
    if parts[0] == "ko":                          # эгаси — РайГаз
        if p["state"] != "kowner":
            return "Эскирган"
        t, kb = kusok_owner(dict(p), rw, P.OWNER_CO)
        edit(chat, mid, t, kb)
        return ""
    if parts[0] == "kw":                          # омборда йўқ — ўлчамини ёзади
        A.db().execute("UPDATE raspil_pending SET state='ksize' WHERE id=?", (pid,)); A.db().commit()
        edit(chat, mid, f"🧩 {p['doc_no']}\n\n📏 Ўлчами ва сони? Ёзинг:\n(масалан: 2800 1000 ёки 400 1500 10)")
        return ""
    import c1
    nm = (c1.goods().get(parts[2]) or {}).get("name", "Кусок")
    st = P.kusok_stock(parts[2]) if p["job_id"] else []
    A.db().execute("UPDATE raspil_pending SET state=?, iddoc=?, doc_no=?, bot_msg=? WHERE id=?",
                   ("kpick" if st else "ksize", parts[2], nm[:120], mid, pid))
    A.db().commit()
    if st:                                        # сотиш: аввал омбордагилар
        rows = [[((f"{P.kusok_label(g)}" + (f" × {n} та" if n > 1 else "") + f" — {P._n(m2)} м²")[:64], f"kx:{pid}:{g['id']}")]
                for g, n, m2 in P.kusok_groups(st)[:15]]
        rows.append([("✏️ Бу ерда йўқ — ёзаман", f"kw:{pid}")])
        edit(chat, mid, f"🧩 {nm} — омборда:", ikb(rows))
    else:
        edit(chat, mid, f"🧩 {nm}\n\n📏 Ўлчами ва сони? Ёзинг:\n(масалан: 2800 1000 ёки 400 1500 10)")
    return ""


def kromka_flow(rw, chat, mid, parts):
    pid = int(parts[1])
    p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (pid, rw["id"]), one=True)
    if not p or not p["job_id"]:
        return "Эскирган"
    mach = A.q("SELECT machine FROM raspil_job WHERE id=?", (p["job_id"],), one=True)["machine"]
    if parts[0] == "kf":
        fid = None if parts[2] == "-" else parts[2]
        A.db().execute("UPDATE raspil_pending SET state='wpick' WHERE id=?", (pid,)); A.db().commit()
        t, kb = kromka_menu(pid, mach, fid, int(parts[3]))
        edit(chat, mid, t, kb)
    elif parts[0] == "ki":
        import c1
        nm = (c1.goods().get(parts[2]) or {}).get("name", "?")
        oval = mach == "oval"                      # овал: размер (1С) → ранги (ишчи ёзади) → метр
        A.db().execute("UPDATE raspil_pending SET state=?, iddoc=?, doc_no=?, client=NULL, bot_msg=? WHERE id=?",
                       ("wcolor" if oval else "wmeter", parts[2], nm[:120], mid, pid))
        A.db().commit()
        edit(chat, mid, f"〰️ {_klabel(nm) or nm}\n\n🎨 Кромка ранги? Ёзинг (масалан: оқ глянец, кашемир):" if oval else f"🎞 {nm}\n\n📏 Неча метр? Ёзинг:")
    elif parts[0] == "kd":
        A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=?", (pid,)); A.db().commit()
        edit(chat, mid, f"✅ {P.clabel(P.wait_code(p['job_id']))} сақланди — мижозга айтинг.")
        P.notify_mgr(P.wait_code(p["job_id"]))
    return ""


B_CASH = "💵 Мижоздан пул олдим"


def wait_start(chat, rw=None):
    held = P.cash_rows(rw["id"]) if rw else []
    send(chat, "📝 Накладнойсиз кесилди.\n\nҚайси лист?" + (f"\n\n💵 Сизда кассага топширилмаган пул: {P.cash_total(held)}" if held else ""),
         ikb([[(f"🪵 {P.WAIT_KINDS['main']}", "w:k:main")], [(f"📦 {P.WAIT_KINDS['hdf']}", "w:k:hdf")], [(f"👤 {P.WAIT_KINDS['own']}", "w:k:own")],
              [(B_CASH, "wc:menu")]]))


CASH_ASK = "💵 Қанча олдингиз? Ёзинг: 150000 (сўм) ёки 20$"


def cash_menu(rw):
    held = P.cash_rows(rw["id"])
    rows = [[(f"{P.clabel(c)} · {lbl}"[:60], f"wc:c:{c}")] for c, lbl in P.open_codes()]
    if held:
        rows.append([(f"📋 Менда турган пул: {P.cash_total(held)}"[:60], "wc:l")])
    return ("💵 Мижоздан пул олдингизми?\nҚайси иш учун — кодни босинг ёки накладной рақамини ёзинг (масалан 7012):", ikb(rows) if rows else None)


def cash_flow(rw, chat, mid, parts):
    """wc:menu | wc:c:<code> | wc:l | wc:x:<id> — ишчи мижоздан пул олди, кун охирида кассага топширади."""
    con = A.db()
    if parts[1] == "menu":
        con.execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state, bot_msg) VALUES(?,?,?,?,?)", (rw["id"], chat, A.now(), "wcash0", mid))
        con.commit()
        t, kb = cash_menu(rw)
        edit(chat, mid, t, kb)
        return ""
    if parts[1] == "c":
        code = int(parts[2])
        con.execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state, bot_msg, iddoc) VALUES(?,?,?,?,?,?)",
                    (rw["id"], chat, A.now(), "wcash", mid, f"c:{code}"))
        con.commit()
        edit(chat, mid, f"💵 {P.clabel(code)}" + (f" · {P.code_client(code)}" if P.code_client(code) else "") + f"\n\n{CASH_ASK}")
        return ""
    if parts[1] == "l":
        held = P.cash_rows(rw["id"])
        if not held:
            edit(chat, mid, "💵 Сизда кассага топширилмаган пул йўқ.")
            return ""
        edit(chat, mid, f"💵 Сизда: {P.cash_total(held)}\nКун охирида кассага топширинг. Хато ёзган бўлсангиз — ✖ босинг.\n\n"
                        + "\n".join(f"• {r['at'][11:16]} {P.cash_label(r)} — {P.cash_fmt(r['amount'], r['cur'])}" for r in held),
             ikb([[(f"✖ {P.cash_label(r)} — {P.cash_fmt(r['amount'], r['cur'])}"[:60], f"wc:x:{r['id']}")] for r in held[:10]]))
        return ""
    if parts[1] == "x":
        r = P.cash_void(int(parts[2]), rw["id"])
        held = P.cash_rows(rw["id"])
        edit(chat, mid, (f"🗑 Бекор қилинди: {P.cash_label(r)} — {P.cash_fmt(r['amount'], r['cur'])}" if r else "❓ Топилмади (касса қабул қилган бўлиши мумкин).")
             + (f"\n\n💵 Сизда: {P.cash_total(held)}" if held else ""))
        return "Бекор қилинди" if r else ""
    return ""


def cash_text(rw, chat, p, text):
    """Пул ёзиш жараёни: wcash0 — накладной рақами; wcash — сумма."""
    con = A.db()
    if p["state"] == "wcash0":
        dg = re.sub(r"\D", "", text or "")
        if not 1 <= len(dg) <= 9:
            send(chat, "🔢 Накладной рақамини ёзинг, масалан 7012")
            return
        code = P.code_by_no(dg)
        ref = "" if code else str(int(dg))
        d0 = None if code else P.find_sale_no(dg)
        con.execute("UPDATE raspil_pending SET state='wcash', iddoc=?, at=? WHERE id=?", (f"c:{code}" if code else f"r:{ref}", A.now(), p["id"]))
        con.commit()
        lab = (f"{P.clabel(code)}" + (f" · {P.code_client(code)}" if P.code_client(code) else "")) if code else \
            f"№{ref}" + (f" · {d0['cname']}" if d0 and d0["cname"] else "")
        send(chat, f"💵 {lab}\n\n{CASH_ASK}")
        return
    m = P.parse_money(text)
    if not m:
        send(chat, "💵 Фақат сумма ёзинг: 150000 ёки 20$")
        return
    amt, cur = m
    tag = p["iddoc"] or ""
    code = int(tag[2:]) if tag.startswith("c:") and tag[2:].isdigit() else None
    ref = tag[2:] if tag.startswith("r:") else ""
    P.cash_add(rw, code, amt, cur, ref)
    con.execute("UPDATE raspil_pending SET state='done' WHERE id=?", (p["id"],))
    con.commit()
    held = P.cash_rows(rw["id"])
    lab = P.cash_label(dict(code=code, ref=ref))
    send(chat, f"✅ Ёзилди: {lab} — {P.cash_fmt(amt, cur)}\nКун охирида кассага топширинг.\n\n💵 Сизда жами: {P.cash_total(held)}", kb_for(rw))


def wait_flow(rw, chat, mid, parts):
    if parts[1] == "m":                           # w:m:<machine> → мижознинг коди бор/йўқ
        m = parts[2]
        rows = [[(f"🆕 Янги мижоз", f"w:c:{m}:0")], [("➕ Эски накладнойга қўшимча", f"w:r:{m}")]]
        rows += [[(f"{P.clabel(c)} · {lbl}"[:60], f"w:c:{m}:{c}")] for c, lbl in P.open_codes()]
        edit(chat, mid, f"📝 {P.mlabel(m)}.\nМижознинг коди борми? Бўлса — ўшани босинг:", ikb(rows))
        return ""
    if parts[1] == "r":                           # w:r:<machine> → эски накладной рақами (қўшимча иш)
        m = parts[2]
        A.db().execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state, machine, bot_msg) VALUES(?,?,?,?,?,?)",
                       (rw["id"], chat, A.now(), "wref", m, mid))
        A.db().commit()
        edit(chat, mid, f"📝 {P.mlabel(m)}. Эски накладнойга қўшимча.\n\n🔢 Эски накладной рақамини ёзинг (масалан: 6453):")
        return ""
    if parts[1] == "c":                           # w:c:<machine>:<code|0>
        m, code = parts[2], int(parts[3])
        if not code:                              # янги мижоз — аввал исми ва телефони (менежер ким учунлигини билсин)
            A.db().execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state, machine, bot_msg, iddoc) VALUES(?,?,?,?,?,?,?)",
                           (rw["id"], chat, A.now(), "wcli", m, mid, parts[4] if len(parts) > 4 else None))
            A.db().commit()
            edit(chat, mid, f"📝 {P.mlabel(m)}. Янги мижоз.\n\n👤 Мижоз исми ва телефони?\n(масалан: Алишер 1131 ёки Алишер 901231131)")
            return ""
        if m == P.SALE:                           # кусок сотиш: тури (1С) → ўлчами ва ранги
            jid = P.add_wait(rw, "sale", 0, machine=P.SALE, code=code or None)
            pid = A.db().execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state, job_id, bot_msg) VALUES(?,?,?,?,?,?)",
                                 (rw["id"], chat, A.now(), "kpick", jid, mid)).lastrowid
            A.db().commit()
            if len(parts) > 4 and parts[4]:       # омбордан танланган кусок(лар) — дарров сотилади
                kid_, n_ = _kref(parts[4])
                picks = None
                for i in P.kusok_same(kid_, n_):
                    picks = P.kusok_sell(i, jid, rw) or picks
                if picks is not None:
                    t, kb = _kdone(dict(id=pid, job_id=jid), picks)
                    edit(chat, mid, "Мижозга айтинг. «✅ тўланди» келмагунча берманг.\n" + t, kb)
                    return f"{P.clabel(P.wait_code(jid))}"
            t, kb = kusok_menu(pid, f"🔖 {P.clabel(P.wait_code(jid))} · Кусок\nМижозга айтинг. «✅ тўланди» келмагунча берманг.\n\n")
            edit(chat, mid, t, kb)
            return f"{P.clabel(P.wait_code(jid))}"
        if m in P.JOB_MACHINES or m == "dp":
            return wait_flow(rw, chat, mid, ["w", "job", m, str(code)])
        edit(chat, mid, f"📝 {P.MACHINES[m].split(' (')[0]}. Қайси лист?",
             ikb([[(f"🪵 {P.WAIT_KINDS['main']}", f"w:k:main:{m}:{code}")], [(f"📦 {P.WAIT_KINDS['hdf']}", f"w:k:hdf:{m}:{code}")],
                  [(f"👤 {P.WAIT_KINDS['own']}", f"w:k:own:{m}:{code}")]]))
        return ""
    if parts[1] == "k":
        kind = parts[2]
        m = parts[3] if len(parts) > 3 else ""
        cd = parts[4] if len(parts) > 4 else "0"
        nums = [(str(k), f"w:n:{kind}:{k}" + (f":{m}:{cd}" if m else "")) for k in range(1, 21)]
        numask_open(rw, chat, mid, dict(t="wn", kind=kind, m=m, cd=cd))
        edit(chat, mid, f"📝 {P.WAIT_KINDS[kind]}\n\n🔢 Нечта кесилди?\nТугмани босинг ёки рақамини ёзинг (масалан 45):", ikb([nums[i:i + 5] for i in range(0, 20, 5)]))
        return ""
    if parts[1] in ("n", "job"):
        if parts[1] == "n":
            kind, n, m = parts[2], float(parts[3]), (parts[4] if len(parts) > 4 else None)
            code = int(parts[5]) if len(parts) > 5 else 0
        else:
            kind, n, m = "job", 0.0, (parts[2] if len(parts) > 2 else None)
            code = int(parts[3]) if len(parts) > 3 else 0
        jid = P.add_wait(rw, kind, n, machine=m, code=code or None)
        pid = A.db().execute("INSERT INTO raspil_pending(rw_id, chat_id, at, state, job_id, bot_msg) VALUES(?,?,?,?,?,?)",
                             (rw["id"], chat, A.now(), "wnote", jid, mid)).lastrowid
        A.db().commit()
        code = P.wait_code(jid)
        mach = A.q("SELECT machine FROM raspil_job WHERE id=?", (jid,), one=True)["machine"]
        if mach in ("dp", "pris", "utol"):        # двухпилка / присадка / утолщенка: 1С хизматидан танлайди, сонини ёзади
            A.db().execute("UPDATE raspil_pending SET state='rpick' WHERE id=?", (pid,)); A.db().commit()
            t, kb = rsv_menu(pid, f"🔖 {P.clabel(code)} · {P.mlabel(mach)}\nМижозга айтинг. «✅ тўланди» келмагунча деталь берманг.\n\n")
            edit(chat, mid, t, kb)
            return f"{P.clabel(code)}"
        if mach in ("kromka", "oval"):
            A.db().execute("UPDATE raspil_pending SET state='wpick' WHERE id=?", (pid,)); A.db().commit()
            t, kb = kromka_menu(pid, mach, None, 0, f"🔖 {P.clabel(code)} · {P.MACHINES.get(mach, mach).split(' (')[0]}\nМижозга айтинг. «✅ тўланди» келмагунча деталь берманг.\n\n")
            edit(chat, mid, t, kb)
            return f"{P.clabel(code)}"
        if mach == "rover":                       # ровер: иш суммаси, кейин изоҳ (нима қилди)
            A.db().execute("UPDATE raspil_pending SET state='rsum' WHERE id=?", (pid,)); A.db().commit()
            edit(chat, mid, f"🔖 {P.clabel(code)} · Ровер\nМижозга айтинг. «✅ тўланди» келмагунча деталь берманг.\n\n"
                            "💰 Иш суммаси қанча? Рақам ёзинг (масалан 150000):")
            return f"{P.clabel(code)}"
        hint = {"pris": "40 тешик, паз подсветка", "rover": "2 та фасад", "utol": "6 м", "dp": "кусок 2800×400 — 1 та",
                "arra": "5 та лист"}.get(mach, "")
        edit(chat, mid, f"🔖 {P.clabel(code)} · {P.MACHINES.get(mach, mach).split(' (')[0]}\nМижозга айтинг. «✅ тўланди» келмагунча деталь берманг.\n\n"
                        f"✍️ Хоҳласангиз ёзинг, нима қилдингиз ({hint}):",
             ikb([[("✅ Тамом", f"w:skip:{pid}")]]))
        return f"{P.clabel(code)}"
    if parts[1] == "skip":
        A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=? AND rw_id=?", (int(parts[2]), rw["id"])); A.db().commit()
        j = A.q("SELECT job_id FROM raspil_pending WHERE id=?", (int(parts[2]),), one=True)
        rn = _refno(j["job_id"]) if j else None
        edit(chat, mid, f"✅ №{rn} ровер иши менежерга юборилди." if rn else
             f"🔖 {P.clabel(P.wait_code(j['job_id']))} — мижозга айтинг." if j and j["job_id"] else "✅")
        if j and j["job_id"]:
            P.notify_mgr(P.wait_code(j["job_id"]))
    return ""


def _money(v, cur="UZS"):
    return P.cash_fmt(v or 0, "USD") if cur == "USD" else _som(v)


def _som(v):
    return f"{v:,.0f}".replace(",", " ") + " сўм"


def salary_text(rw):
    kw = rw.get("kw_id")
    m = P.worker_month(kw)
    t, row = m["t"], m["row"]
    td = today_text(rw)
    out = ([td, ""] if not td.startswith("📊 Бугун ҳали") else []) + [f"💰 {m['title']} — {P.wname(kw)}"]
    if t.get("d") or t.get("n"):
        out.append(f"🪵 Распил: {P._n(t['d'] + t['n'])} лист" + (f" (2-смена {P._n(t['n'])})" if t.get("n") else ""))
    if t.get("m45"):
        out.append(f"📐 45°: {P._n(t['m45'])} м")
    if t.get("pp") or m["cnc"]:
        out.append(f"🔩 Присадка: {P._n(t.get('pp', 0))} тешик" + (f", ЧПУ/паз {P._n(m['cnc'])}" if m["cnc"] else ""))
    for lbl, v, u in (("🎞 Кромка", t.get("km_d", 0) + t.get("km_n", 0), "м"), ("〰️ Овал кромка", t.get("ov", 0), "м"),
                      ("📏 Утолщенка", t.get("ut", 0), "м"), ("🌀 Ровер", t.get("rv", 0), "шт")):
        if v:
            out.append(f"{lbl}: {P._n(v)} {u}" + (f" (2-смена {P._n(t['km_n'])})" if lbl.endswith("Кромка") and t.get("km_n") else ""))
    att = A.q("SELECT COUNT(*) c FROM k_att WHERE kw=? AND day LIKE ?", (kw, m["ym"] + "-%"), one=True)["c"]
    if row and row.get("days"):                           # табел бўйича (касса → «Табель и зарплата»); бот белгиси — фақат бот уланган кунлар
        out.append(f"📅 Табел: {P._n(row['days'])} кун")
    elif att and not is_constr(rw):
        out.append(f"📅 Ишга келган кун: {att}")
    if not P.auto_on(m["ym"]):
        import kassa
        out.append(f"\n📝 Бу ой ойлик қўлда ҳисобланади ({kassa.ym_title(P.auto_from())} дан автомат).")
        if row and row["total"]:
            out.append(f"💵 Ҳозирча ёзилган: {_som(row['total'])}")
    elif row and row.get("override"):                     # табелда «Жами» қўлда ёзилган — оклад ҳисоби кўрсатилмайди (чалғитмасин)
        out.append("")
        out.append(f"💵 Жами ҳисобланган: {_money(row['total'], row['cur'])}")
    elif row:
        out.append("")
        if row["base"]:
            out.append(f"Оклад: {_money(row['base'], row['cur'])}")
        if row["kpi"]:
            out.append(f"Иш бай: {_money(row['kpi'], row['cur'])}")
        if row["s"]["extra"]:
            out.append(f"Қўшимча: {_money(row['s']['extra'], row['cur'])}")
        out.append(f"💵 Жами ҳисобланган: {_money(row['total'], row['cur'])}")
    else:
        out.append("\nОйлик ҳали йўқ — ҳисобчига айтинг.")
    return "\n".join(out)


def _kroy_me(rw, ym):
    return P.kroy_month(ym).get(rw.get("kw_id")) or dict(docs=0, sh_main=0.0, sh_hdf=0.0, pay=0.0, rows=[])


def kroys_text(rw):
    """Кройчи: шу ой қилган кройлари (платформа накладнойидаги «Кройчи» майдонидан)."""
    ym = date.today().isoformat()[:7]
    o = _kroy_me(rw, ym)
    import kassa
    out = [f"📐 Кройларим — {kassa.ym_title(ym)}"]
    if not o["rows"]:
        return out[0] + "\n\nҲали йўқ. Накладнойда «Кройчи» майдонида сиз танланган бўлса, шу ерда чиқади."
    td = date.today().isoformat()
    rows = sorted(o["rows"], key=lambda r: (r.get("day") or "", str(r.get("doc_no") or "")), reverse=True)
    for r in rows[:40]:
        d = (r.get("day") or "")[8:10] + "." + (r.get("day") or "")[5:7]
        sh = f"{P._n(r['sh_main'])} лист" + (f" + ХДФ {P._n(r['sh_hdf'])}" if r.get("sh_hdf") else "")
        out.append(("🟢 " if r.get("day") == td else "• ") + f"{d} · №{r.get('doc_no')} · {(r.get('client') or '')[:28]} — {sh}")
    if len(rows) > 40:
        out.append(f"… яна {len(rows) - 40} та")
    out.append(f"\nЖами: {o['docs']} накладной · {P._n(o['sh_main'])} лист" + (f" + ХДФ {P._n(o['sh_hdf'])}" if o["sh_hdf"] else ""))
    return "\n".join(out)


def kroy_kpi_text(rw):
    """Кройчи: ойлик ва иш бай (лист бай) — шу ой."""
    ym = date.today().isoformat()[:7]
    o = _kroy_me(rw, ym)
    import kassa
    out = [f"📈 KPI — {kassa.ym_title(ym)}",
           f"📐 Крой: {o['docs']} накладной · {P._n(o['sh_main'])} лист" + (f" + ХДФ {P._n(o['sh_hdf'])}" if o["sh_hdf"] else "")]
    if ym >= P.kroy_from():
        out.append(f"💵 Лист бай: {_som(o['pay'])}")
    else:
        out.append(f"ℹ️ Лист бай ҳисоб {kassa.ym_title(P.kroy_from())} дан. Ҳозирча — кўриш учун: {_som(o['pay'])}")
    out += ["", salary_text(rw)]
    return "\n".join(out)


def _doc_progress(iddoc):
    """Накладной цехда қаерда: [(станок номи, белги)] — ✔ тугади, ⏳ қисман, ○ бошланмаган."""
    import c1
    try:
        lines = (c1.sale_doc(iddoc) or {}).get("lines") or []
    except Exception:
        lines = []
    jobs = {r["machine"] for r in A.q("SELECT DISTINCT machine FROM raspil_job WHERE iddoc=? AND status='ok'", (iddoc,))}
    out = []
    try:
        st = P.items_state(iddoc)
        its = [i for i in st["items"] if i["qty"] > 0]
        if its:
            left = sum(i["left"] for i in its)
            done = sum(i["done"] + i["whole"] for i in its)
            out.append(("Распил", "✔" if left <= 0.001 else ("⏳" if done > 0 else "○")))
    except Exception:
        if jobs & {"arra", "dp"}:
            out.append(("Распил", "⏳"))
    if any(re.search(r"сверлен|присадк", (l.get("name") or "").lower()) for l in lines):
        out.append(("Присадка", "✔" if "pris" in jobs else "○"))
    for m in ("kromka", "oval", "utol", "rover"):
        need = {n for n, *_ in P.work_lines(m, lines)}
        if not need:
            continue
        done = P.work_done_lines(iddoc, m)
        part = P.work_part(iddoc, m)
        mark = "✔" if need <= done else ("⏳" if (done & need) or part else "○")
        out.append((P.MACHINES[m].split(" (")[0], mark))
    return out


def orders_status_text(days=7, limit=25):
    """Буюртмалар: цехда қайси жараёнда (станоклар бўйича) ва тайёр бўлганлари."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    rdy = {}
    for r in A.q("SELECT r.iddoc, r.at, r.doc_no, CASE WHEN r.status='sent' AND o.status IS NOT NULL AND o.status<>'sent' THEN 'wait' "
                 "ELSE r.status END status, c.name cname FROM ready_log r LEFT JOIN clients c ON c.id=r.client_id "
                 "LEFT JOIN tg_out o ON o.id=r.out_id WHERE r.at>=? AND r.iddoc<>'' AND r.status IN ('sent','own','noclient','nochannel') "
                 "ORDER BY r.at", (since,)):
        rdy[r["iddoc"]] = dict(r)
    docs = A.q("SELECT iddoc, MAX(doc_no) doc_no, MAX(client) client, MAX(at) last FROM raspil_job WHERE status='ok' AND iddoc<>'' AND at>=? "
               "GROUP BY iddoc ORDER BY last DESC", (since,))
    work, ready = [], []
    seen = set()
    for d in docs:
        seen.add(d["iddoc"])
        if d["iddoc"] in rdy:
            continue
        work.append(d)
    for iddoc, r in sorted(rdy.items(), key=lambda x: x[1]["at"], reverse=True):
        ready.append(r)
    out = [f"⚙️ Цехда жараёнда ({len(work)}):"]
    for d in work[:limit]:
        pr = _doc_progress(d["iddoc"])
        st = " · ".join(f"{n} {m}" for n, m in pr) if pr else "иш бошланган"
        out.append(f"№{d['doc_no']} · {(d['client'] or '')[:26]} — {st}")
    if not work:
        out.append("йўқ")
    icon = {"sent": "📨", "wait": "⏳ хабар кетмади", "own": "👤", "noclient": "❔", "nochannel": "📵 ёзиб бўлмади"}
    out.append(f"\n✅ Тайёр ({len(ready)}):")
    for r in ready[:limit]:
        out.append(f"№{r['doc_no']} · {(r['cname'] or '')[:26]} — {r['at'][8:10]}.{r['at'][5:7]} {r['at'][11:16]} {icon.get(r['status'], '')}")
    if not ready:
        out.append("йўқ")
    out.append("\n✔ тугади · ⏳ қисман · ○ бошланмаган. ⏳ хабар кетмади / 📵 — мижозга қўнғироқ қилинг.")
    return "\n".join(out)


def ready_list_text(days=3):
    """Цехда тайёр бўлган накладнойлар (цех бошлиғи «тайёр» деб ташлаганлари) — охирги кунлар."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    rows = A.q("SELECT r.at, r.doc_no, CASE WHEN r.status='sent' AND o.status IS NOT NULL AND o.status<>'sent' THEN 'wait' ELSE r.status END status, "
               "c.name cname FROM ready_log r LEFT JOIN clients c ON c.id=r.client_id LEFT JOIN tg_out o ON o.id=r.out_id "
               "WHERE r.at>=? AND r.doc_no<>'' AND r.status IN ('sent','own','noclient','nochannel') ORDER BY r.at DESC LIMIT 60", (since,))
    if not rows:
        return f"✅ Охирги {days} кунда тайёр накладной йўқ."
    icon = {"sent": "📨", "wait": "⏳", "own": "👤", "noclient": "❔", "nochannel": "📵"}
    out = [f"✅ Тайёр накладнойлар (охирги {days} кун):"]
    for r in rows:
        out.append(f"{icon.get(r['status'], '•')} {r['at'][8:10]}.{r['at'][5:7]} {r['at'][11:16]} · №{r['doc_no']} · {(r['cname'] or '')[:30]}")
    out.append("\n📨 мижозга хабар кетди · ⏳ хабар ҳали кетмади (қўнғироқ қилинг) · 👤 ўзимизники · 📵 ёзиб бўлмади")
    return "\n".join(out)


def today_text(rw):
    day = date.today().isoformat()
    t = P.day_totals(day).get(rw.get("kw_id"))
    if not t:
        return "📊 Бугун ҳали ҳисобланган иш йўқ."
    s = f"📊 Бугун ({P.wname(rw['kw_id'])}):\n⏱ {t['start']:%H:%M} дан, {P.hm(t['hours'])}\n📄 Накладной: {t['docs']}" + (f"\n🪵 Лист: {P._n(t['d'] + t['n'])}" if (t['d'] + t['n']) else "")
    if t["n"]:
        s += f" (2-смена: {P._n(t['n'])})"
    if t["m45"]:
        s += f"\n📐 45°: {P._n(t['m45'])} м"
    if t.get("pp") or t.get("ps"):
        s += f"\n🔩 Присадка: {P._n(t['pp'])} тешик" + (" + ЧПУ/паз" if t["ps"] else "")
    for lbl, v, u in (("🎞 Кромка", t.get("km_d", 0) + t.get("km_n", 0), "м"), ("〰️ Овал", t.get("ov", 0), "м"),
                      ("📏 Утолщенка", t.get("ut", 0), "м"), ("🌀 Ровер", t.get("rv", 0), "шт")):
        if v:
            s += f"\n{lbl}: {P._n(v)} {u}"
    return s


def on_callback(cq):
    uid = cq["from"]["id"]
    rw = worker_by_uid(uid)
    data = cq.get("data") or ""
    note = ""
    import botlang
    botlang.current("work", (cq.get("message") or {}).get("chat", {}).get("id", uid))
    if data.startswith("lang:"):
        chat = (cq.get("message") or {}).get("chat", {}).get("id", uid)
        lg = data[5:]
        if botlang.set_lang("work", chat, lg):
            if rw:                                        # танлов хабари ўчирилади, тасдиқ — янги тугмалар билан (иккита бир хил хабар бўлмасин)
                try:
                    api("deleteMessage", chat_id=chat, message_id=cq["message"]["message_id"])
                except Exception:
                    pass
                send(chat, botlang.DONE[lg], kb_for(rw))
            else:
                try:
                    edit(chat, cq["message"]["message_id"], botlang.DONE[lg])
                except Exception:
                    pass
        try:
            api("answerCallbackQuery", callback_query_id=cq["id"])
        except tg.TgError:
            pass
        return
    if not rw:
        note = "Аввал рақамингизни юборинг"
    elif data[:3] in ("pk:", "po:", "pr:", "pe:", "lg:"):  # олиб келиш: шофёр белгиси / буюртма / қабул; кромка этикеткаси; логистика
        try:
            import pickup, edgebot, logist
            note = {"pk:": pickup.on_cb, "po:": pickup.order_cb, "pr:": pickup.recv_cb, "pe:": edgebot.on_cb, "lg:": logist.on_cb}[data[:3]](rw, cq)
        except Exception:
            A.errlog.exception("pickup callback")
            note = "Хатолик"
    elif is_owner(rw) and data.startswith("own:"):
        chat, mid = cq["message"]["chat"]["id"], cq["message"]["message_id"]
        edit(chat, mid, owner_view(data[4:]), owner_menu())
    elif is_mgr(rw):
        chat, mid = cq["message"]["chat"]["id"], cq["message"]["message_id"]
        parts = data.split(":")
        try:
            if data == "sw:kst":
                t, kb = kstore_msg(mgr=True)
                edit(chat, mid, t, kb)
            elif data == "sw:ksee":
                t, kb = kstore_list()
                edit(chat, mid, t, kb)
            elif parts[0] in ("kb", "kn", "kr", "kv", "kg"):
                note = kstore_sell(rw, chat, mid, parts)
        except Exception:
            A.errlog.exception("mgr callback")
            note = "Хато"
    else:
        chat, mid = cq["message"]["chat"]["id"], cq["message"]["message_id"]
        parts = data.split(":")
        A.db().execute("UPDATE raspil_pending SET state='done' WHERE rw_id=? AND state='numask'", (rw["id"],)); A.db().commit()
        try:
            if parts[0] in ("op", "hp") and P.perms_of(rw) is not None and not P.is_boss(rw):
                note = "⛔ Ҳар ким ўз телефонидан киради"
            elif parts[0] == "op":                                   # op:<machine>:<kw>
                machine, op = parts[1], int(parts[2])
                edit(chat, mid, f"👷 {P.wname(op)}\n\n🤝 Ёрдамчи ким?",
                     people_kb(f"hp:{machine}:{op}", skip=op, extra=("🚫 Ёрдамчисиз", f"hp:{machine}:{op}:0")))
            elif parts[0] == "jr":                                 # jr:<machine>:op|hp — ишчи ўзи киради
                ok, txt = P.join_crew(rw, parts[1], parts[2])
                if ok:
                    edit(chat, mid, txt + "\n\n📷 Накладной расмини ташланг.")
                    note = "Иш бошланди"
                else:
                    t2, kb2 = role_kb(parts[1], rw)
                    edit(chat, mid, txt + "\n\n" + t2, kb2)
                    note = "⛔"
            elif parts[0] == "hp":                                 # hp:<machine>:<op>:<helper>
                machine, op, hp = parts[1], int(parts[2]), int(parts[3])
                txt = P.start_crew_ids(rw, machine, op, hp or None, None)
                txt = (f"✅ {P.MACHINES.get(machine, machine).split(' (')[0]}: {P.wname(op)}" + (f" + {P.wname(hp)}" if hp else "")
                       + "\n\n📷 Накладной расмини ташланг.")
                edit(chat, mid, txt)
                note = "Смена бошланди"
            elif parts[0] == "i":                                  # i:<pid>:<key> — шу листни кесяпти
                note = pick_item(rw, chat, mid, int(parts[1]), parts[2])
            elif parts[0] == "c":                                  # c:<pid>:<key> — кесилди → нечта?
                note = ask_count(rw, chat, mid, int(parts[1]), parts[2])
            elif parts[0] == "q":                                  # q:<pid>:<key>:<n>
                note = save_count(rw, chat, mid, int(parts[1]), parts[2], float(parts[3]))
            elif parts[0] in ("bw", "bwx"):                        # bw:<pid>:<key> — шу лист бутун кетади; bwx — бекор
                p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (int(parts[1]), rw["id"]), one=True)
                if p:
                    ok, txt = P.mark_whole(rw, p["iddoc"], parts[2], undo=parts[0] == "bwx")
                    t, kb = preview(rw, dict(p), txt if ok else "⚠️ " + txt)
                    edit(chat, mid, t, kb)
                    note = txt
            elif parts[0] == "ky":                                 # крой: ХДФ 0
                p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=? AND state='kh'", (int(parts[1]), rw["id"]), one=True)
                if p:
                    kroy_save(rw, chat, dict(p), 0, mid)
                    note = "Сақланди"
            elif parts[0] == "kx0":                                # крой: бекор
                r_ = A.q("SELECT * FROM raspil_kroy WHERE id=? AND rw_id=? AND status='ok'", (int(parts[1]), rw["id"]), one=True)
                if r_:
                    A.db().execute("UPDATE raspil_kroy SET status='del', note='бот: бекор' WHERE id=?", (r_["id"],)); A.db().commit()
                    P.kroy_sync(r_["day"][:7])
                    edit(chat, mid, f"🗑 №{r_['doc_no']} крой бекор қилинди.")
                    note = "Бекор"
            elif parts[0] == "jn":                                 # jn:<pid> — изоҳсиз тамом
                A.db().execute("UPDATE raspil_pending SET state='done' WHERE id=? AND rw_id=?", (int(parts[1]), rw["id"])); A.db().commit()
                edit(chat, mid, "✅ Сақланди.")
            elif parts[0] in ("rs", "rm"):                         # двухпилка хизмати (1С «Распил ОБЫЧНЫЙ»)
                note = rsv_flow(rw, chat, mid, parts)
            elif parts[0] in ("kb", "kc", "kn", "kr", "kv", "kg"):  # кусок омбори: сотиш / брон / қайтариш
                note = kstore_sell(rw, chat, mid, parts)
            elif parts[0] in ("ks", "kk", "kx", "kw", "ko", "kz"):  # кусок: сотиш / омборга
                note = kusok_flow(rw, chat, mid, parts)
            elif parts[0] in ("kf", "ki", "kd"):                   # кромка танлаш (накладнойсиз иш)
                note = kromka_flow(rw, chat, mid, parts)
            elif parts[0] == "wm":                                 # wm:<pid>[:<сатр №>] — қисман метр
                note = work_meter(rw, chat, mid, parts)
            elif parts[0] == "wb":                                 # wb:<pid> — накладнойга қайтиш
                p_ = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (int(parts[1]), rw["id"]), one=True)
                if p_:
                    t_, kb_ = preview(rw, dict(p_))
                    edit(chat, mid, t_, kb_)
            elif parts[0] == "wl":                                 # wl:<pid>:<сатр №> — шу кромка тугади
                note = work_line(rw, chat, mid, int(parts[1]), int(parts[2]))
            elif parts[0] == "sw":                                 # sw:menu|m:<machine>|open|wait|today
                note = switch_flow(rw, chat, mid, parts)
            elif parts[0] == "o":                                  # o:<pid> — тўхтатилган накладнойга қайтиш
                note = reopen(rw, chat, mid, int(parts[1]))
            elif parts[0] == "b":
                p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (int(parts[1]), rw["id"]), one=True)
                if p:
                    t, kb = preview(rw, dict(p)); edit(chat, mid, t, kb)
            elif parts[0] == "wc":                                 # «💵 Мижоздан пул олдим»
                note = cash_flow(rw, chat, mid, parts)
            elif parts[0] == "w":                                  # накладнойсиз: w:k:<kind> | w:n:<kind>:<n> | w:skip:<pid>
                note = wait_flow(rw, chat, mid, parts)
            elif parts[0] == "l":                                  # l:<pid>:<jid> — накладнойсиз ишни шу накладнойга улаш
                p = A.q("SELECT * FROM raspil_pending WHERE id=? AND rw_id=?", (int(parts[1]), rw["id"]), one=True)
                if p:
                    ok, txt = P.link_wait(int(parts[2]), p["iddoc"], p["doc_no"], p["client"], by=P.wname(rw.get("kw_id")) or "бот")
                    t, kb = preview(rw, dict(p), ("🔗 " if ok else "⚠️ ") + txt)
                    edit(chat, mid, t, kb)
                    note = "Уланди" if ok else txt
            elif parts[0] == "j":
                note = do_action(rw, cq, int(parts[1]), parts[2])
            elif parts[0] == "n":
                note = do_action(rw, cq, int(parts[1]), "n", n=int(parts[2]))
            elif parts[0] == "pf":                                 # pf:<pid>:<фоиз> — присадка қисман
                note = do_action(rw, cq, int(parts[1]), "pris", n=int(parts[2]) / 100)
            elif parts[0] == "x":
                jid = int(parts[1])
                j = A.q("SELECT * FROM raspil_job WHERE id=? AND rw_id=?", (jid, rw["id"]), one=True)
                if j:
                    ok, txt = P.cancel_job(jid, by=P.wname(rw.get("kw_id")) or "ишчи")
                    p = A.q("SELECT * FROM raspil_pending WHERE rw_id=? AND iddoc=? AND state<>'done' ORDER BY id DESC LIMIT 1",
                            (rw["id"], j["iddoc"]), one=True)
                    if p and ok:
                        t, kb = preview(rw, dict(p), "🗑 " + txt + ". Тўғрисини қайта танланг.")
                        edit(chat, mid, t, kb)
                    else:
                        edit(chat, mid, ("🗑 " if ok else "⚠️ ") + txt + ("\nТўғрисини қайта ташланг." if ok else ""))
                    note = "Бекор қилинди" if ok else txt
        except Exception:
            A.errlog.exception("raspil bot callback")
            note = "Хатолик"
    try:
        api("answerCallbackQuery", callback_query_id=cq["id"], text=(note or "")[:190])
    except tg.TgError:
        pass


def handle_update(u):
    try:
        if "callback_query" in u:
            on_callback(u["callback_query"])
        elif "message" in u:
            on_message(u["message"])
    except FileNotFoundError:                              # 1С базаси очилмаяпти (тармоқ)
        A.errlog.warning("raspil bot: 1С очилмади")
        msg = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        if chat:
            send(chat, "⏳ 1С очилмаяпти, бироздан кейин қайта ташланг.")


_chat_locks = {}
_pool = None


def _chat_of(u):
    m = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
    return (m.get("chat") or {}).get("id")


def _handle_async(u):
    """Ҳар бир ишчининг хабари ўз навбатида (бир чатда тартиб сақланади), лекин бир ишчи кутиб қолса, бошқалари тўхтамайди."""
    global _pool
    from concurrent.futures import ThreadPoolExecutor
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="raspil_upd")
    lk = _chat_locks.setdefault(_chat_of(u), threading.Lock())
    def run():
        with lk, A.app.app_context():
            try:
                handle_update(u)
            except Exception:
                A.errlog.exception("raspil bot update")
    _pool.submit(run)


def poll_once(timeout=25, parallel=False):
    tok = token()
    if not tok:
        return 0
    off = int(A.parse_float(A.get_setting("raspil_bot_offset", "0"), 0))
    ups = api("getUpdates", offset=off, timeout=timeout, allowed_updates=["message", "callback_query"]) or []
    for u in ups:
        A.set_settings(raspil_bot_offset=str(u["update_id"] + 1))
        if parallel:
            _handle_async(u)
            continue
        try:
            handle_update(u)
        except Exception:
            A.errlog.exception("raspil bot update")
    return len(ups)


def _photo_loop():
    """Юкланмай қолган расмлар — алоҳида оқимда (секин интернетда хабарларни қабул қилишни тўхтатмаслиги учун)."""
    A._stop.wait(30)
    while not A._stop.is_set():
        try:
            with A.app.app_context():
                if token():
                    retry_photos()
        except Exception:
            A.errlog.exception("retry photos")
        A._stop.wait(30)


def _loop():
    A._stop.wait(15)
    errs = 0
    try:                                                  # менюда /lang (тил танлаш) — бир марта
        with A.app.app_context():
            if token() and A.get_setting("raspil_bot_cmds", "") != "3":
                tg.call(token(), "setMyCommands", {"commands": [{"command": "start", "description": "Бошлаш / ёрдам"},
                                                                {"command": "video", "description": "Видео-қўлланма"},
                                                                {"command": "lang", "description": "Тил / Язык / Language"}]})
                A.set_settings(raspil_bot_cmds="3")
    except Exception:
        A.errlog.warning("raspil bot setMyCommands")
    threading.Thread(target=_photo_loop, name="raspil_photos", daemon=True).start()
    while not A._stop.is_set():
        try:
            with A.app.app_context():
                if not token():
                    A._stop.wait(20)
                    continue
                poll_once(parallel=True)
                errs = 0
                A.set_settings(raspil_bot_error="") if A.get_setting("raspil_bot_error", "") else None
        except tg.TgError as e:
            errs += 1
            with A.app.app_context():
                A.set_settings(raspil_bot_error=f"{A.now()}: {e.human()}")
            A._stop.wait(min(60, 3 * errs))
        except Exception:
            errs += 1
            A.errlog.exception("raspil bot loop")
            A._stop.wait(min(60, 3 * errs))


def connect(tok):
    """Созламадан: токенни текшириб улаш. -> (ok, матн)"""
    tok = (tok or "").strip()
    if not re.fullmatch(r"\d{5,}:[\w-]{20,}", tok):
        return False, "Токен нотўғри кўринишда"
    if tok == (A.get_setting("tg_token", "") or "").strip():
        return False, "Бу бот бошқа ишда (арендаторлар боти) — распил учун алоҳида бот очинг"
    try:
        me = tg.call(tok, "getMe", timeout=15)
        tg.call(tok, "deleteWebhook", {"drop_pending_updates": True})
        tg.call(tok, "setMyCommands", {"commands": [{"command": "start", "description": "Бошлаш / ёрдам"},
                                                     {"command": "video", "description": "Видео-қўлланма"},
                                                     {"command": "lang", "description": "Тил / Язык / Language"}]})
    except tg.TgError as e:
        return False, e.human()
    A.set_settings(raspil_bot_token=tok, raspil_bot_username=me.get("username", ""), raspil_bot_offset="0", raspil_bot_error="")
    return True, "@" + me.get("username", "")


def register(app_module):
    global A
    A = app_module
    import owner
    owner.init(A, P)
    con = A.connect()
    init_tables(con)
    con.commit(); con.close()
    import epay                                       # Payme/Click тушуми → QR'ни кўрсатган ишчининг ботига
    if on_epay not in epay.NEW_HOOKS:
        epay.NEW_HOOKS.append(on_epay)
        epay.FAST.append(lambda c: bool(pay_open(c)))
    _orig = A.start_background_jobs

    def start_background_jobs():
        first = not A._bg["started"]
        _orig()
        if first:
            threading.Thread(target=_loop, name="raspil_bot", daemon=True).start()
    A.start_background_jobs = start_background_jobs
