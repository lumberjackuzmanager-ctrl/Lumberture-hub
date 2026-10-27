# -*- coding: utf-8 -*-
"""Мижозга ҳисобот (акт сверки) PDF: охирги N кун — накладнойлар (иш/товар), тўловлар (касса ва реализацияда), қарз қолдиғи.
Мижоз платформа Telegram-аккаунтига «hisobot» / «отчет» деб ёзади → PDF юборилади. Админ мижоз саҳифасидан ҳам юбора олади."""
import os, re, time, random, logging, threading
from collections import defaultdict, OrderedDict
from datetime import date, datetime, timedelta

from flask import request, redirect, url_for, flash, abort, send_file

A = None
log = logging.getLogger("telegram")
REPORT_RE = re.compile(r"^\W*(?:hisobot|xisobot|ҳисобот|хисобот|хисоб[- ]?китоб|hisob[- ]?kitob|отч[её]т|otchet|report|"
                       r"(?:акт\s*)?сверк[аи]|(?:akt\s*)?sverk[ai]|акт)\W*$", re.I)

# Лотин/кирилл ҳарфлари аралаш ёзилса ҳам («Xисобот» — X лотинча, «Хиcобот» — c лотинча) — бир хил кўринадиган ҳарфларни алмаштириб текширамиз
_LAT, _CYR = "AaBCcEeHKMOoPpTXxYy", "АаВСсЕеНКМОоРрТХхУу"
_TO_CYR, _TO_LAT = str.maketrans(_LAT, _CYR), str.maketrans(_CYR, _LAT)


def is_request(text):
    t = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", text or "").strip()
    return any(REPORT_RE.match(v) for v in (t, t.translate(_TO_CYR), t.translate(_TO_LAT)))


T = {
    "uz": dict(title="Hisobot (akt sverki)", client="Mijoz", period="Davr", made="Tuzildi", open="Davr boshiga qarz",
               taken="Olingan tovar va xizmatlar", paid="To'langan", paid_cash="kassaga", paid_real="nakladnoyda (realizatsiya)",
               close="Hozirgi qarz", adv="Hisobingizda", none="Qarz yo'q",
               cols=("Sana", "Hujjat", "Olindi, $", "To'landi, so'm", "To'landi, $", "To'lov jami, $", "Qarz qoldig'i, $"),
               inv="Nakladnoy №", pay="Kassaga to'lov", ret="Kassadan qaytarildi", other="Boshqa", rate="kurs",
               empty="Bu davrda harakat bo'lmagan", note="Qarz qoldig'i: musbat — sizning qarzingiz, minus — hisobingizdagi pul. Barcha summalar $ da (so'm — kun kursi bo'yicha).",
               caption="{name}, so'nggi {days} kunlik hisobotingiz (PDF). Hozirgi qarz: {debt}.", start="Davr boshi"),
    "ru": dict(title="Отчёт (акт сверки)", client="Клиент", period="Период", made="Сформирован", open="Долг на начало периода",
               taken="Получено товаров и услуг", paid="Оплачено", paid_cash="в кассу", paid_real="в накладной (реализация)",
               close="Текущий долг", adv="На вашем счёте", none="Долга нет",
               cols=("Дата", "Документ", "Получено, $", "Оплата, сум", "Оплата, $", "Оплата всего, $", "Остаток долга, $"),
               inv="Накладная №", pay="Оплата в кассу", ret="Возврат из кассы", other="Прочее", rate="курс",
               empty="За период движений нет", note="Остаток долга: плюс — ваш долг, минус — деньги на вашем счёте. Все суммы в $ (сумы — по курсу дня).",
               caption="{name}, ваш отчёт за последние {days} дней (PDF). Текущий долг: {debt}.", start="Начало периода"),
}


def days_default():
    return max(1, min(365, int(A.parse_float(A.get_setting("report_days", "45"), 45))))


def _pf_moves(cid):
    """Платформада проведен қилинган ҳужжатлар (26.10.2026 дан 1С ўчди — янгилари фақат шу ерда) — 1С client_moves кўринишида.
    -> (moves, info): moves=[dict(doc, iddoc, amount)], info={iddoc: dict(taken, uzs, usd)} (накладной: олинди ва нақд тўлов)."""
    moves, info = [], {}
    con = A.db()
    for d in con.execute("SELECT id, no, date, time, usd, COALESCE(paid,0) paid, pay_usd, pay_uzs FROM erp_docs WHERE src='pf' AND kind='sale' "
                         "AND status='posted' AND COALESCE(nal,1)=1 AND client=?", (cid,)):
        iddoc = f"pf:{d['id']}"
        doc = dict(id=iddoc, kind=210, date=date.fromisoformat(d["date"][:10]), time=d["time"] or "", no=str(d["no"] or ""), posted=True, deleted=False)
        moves.append(dict(doc=doc, iddoc=iddoc, amount=round(-(d["usd"] or 0) + (d["paid"] or 0), 2)))
        info[iddoc] = dict(taken=d["usd"] or 0, uzs=d["pay_uzs"] or 0, usd=d["pay_usd"] or 0)
    for d in con.execute("SELECT x.id, x.no, x.date, x.time, SUM(l.debt) debt, SUM(l.uzs) uzs, SUM(l.usd) usd FROM erp_lines l "
                         "JOIN erp_docs x ON x.id=l.doc_id WHERE x.src='pf' AND x.kind='pay' AND x.status='posted' AND l.client=? GROUP BY x.id", (cid,)):
        iddoc = f"pfp:{d['id']}"
        doc = dict(id=iddoc, kind=1195, date=date.fromisoformat(d["date"][:10]), time=d["time"] or "", no=str(d["no"] or ""), posted=True, deleted=False)
        moves.append(dict(doc=doc, iddoc=iddoc, amount=round(d["debt"] or 0, 2)))
        info[iddoc] = dict(taken=0.0, uzs=d["uzs"] or 0, usd=d["usd"] or 0)
    return moves, info


def statement(cid, days=None, today=None):
    """Мижоз коди (erp_clients.ext — 1С'дан келганда 1С коди) бўйича ҳисобот маълумоти: 1С архиви + платформа ҳужжатлари.
    Қарз: + мижоз қарздор (1С белгиси тескари)."""
    import c1
    days = days or days_default()
    today = today or date.today()
    d1 = today - timedelta(days=days - 1)
    try:
        moves = [dict(doc=m["doc"], iddoc=m["iddoc"], amount=m["amount"], balance=m["balance"]) for m in c1.client_moves(cid)]
    except Exception:
        moves = []                                     # 1С ўқилмаса — платформа ҳужжатлари бўйича
    pfm, pfi = _pf_moves(cid)
    if pfm:
        moves = sorted(moves + pfm, key=lambda m: ((m["doc"]["date"] or date.min), m["doc"]["time"] or "") if m["doc"] else (date.min, ""))
        bal = 0.0
        for m in moves:
            bal += m["amount"]; m["balance"] = bal
    opening = 0.0
    per = OrderedDict()
    for m in moves:
        d = m["doc"]
        if not d or not d["date"]:
            continue
        if d["date"] < d1:
            opening = -m["balance"]
            continue
        if d["date"] > today:
            continue
        x = per.setdefault(m["iddoc"], dict(doc=d, amt=0.0))
        x["amt"] += m["amount"]
    c1docs = [x for k, x in per.items() if not str(k).startswith("pf")]
    sale_h = {h["IDDOC"]: h for h in c1.table("DH210")} if any(x["doc"]["kind"] == 210 for x in c1docs) else {}
    pay_lines = defaultdict(lambda: [0.0, 0.0])
    for k, v in pfi.items():
        if k.startswith("pfp:"):
            pay_lines[k] = [v["uzs"], v["usd"]]
    if any(x["doc"]["kind"] == 1195 for x in c1docs):
        for r in c1.table("DT1195"):
            if r["SP1198"] == cid and r["IDDOC"] in per:
                pay_lines[r["IDDOC"]][0] += r["SP1202"] or 0
                pay_lines[r["IDDOC"]][1] += r["SP1199"] or 0
    rows, bal = [], opening
    tot = dict(taken=0.0, paid=0.0, cash=0.0, real=0.0, uzs=0.0, usd=0.0)
    for iddoc, x in sorted(per.items(), key=lambda kv: (kv[1]["doc"]["date"], kv[1]["doc"]["time"])):
        d, amt = x["doc"], round(x["amt"], 2)
        bal -= amt
        r = dict(date=d["date"], no=d["no"], kind=d["kind"], iddoc=iddoc, taken=0.0, uzs=0.0, usd=0.0, paid=0.0, bal=round(bal, 2), lines=[])
        if d["kind"] == 210 and (iddoc in sale_h or iddoc in pfi):
            if iddoc in pfi:
                r["taken"], r["uzs"], r["usd"] = pfi[iddoc]["taken"], pfi[iddoc]["uzs"], pfi[iddoc]["usd"]
            else:
                h = sale_h[iddoc]
                r["taken"] = h["SP905"] or 0
                r["uzs"], r["usd"] = h.get("SP1164") or 0, h.get("SP1163") or 0
            r["paid"] = round(r["taken"] + amt, 2)
            try:
                sd = c1.sale_doc(iddoc)
                r["lines"] = sd["lines"] if sd else []
                r["note"] = sd.get("note1", "") if sd else ""
            except Exception:
                r["lines"], r["note"] = [], ""
            tot["real"] += r["paid"]
        elif d["kind"] == 1195:
            r["uzs"], r["usd"] = pay_lines[iddoc]
            r["paid"] = amt
            tot["cash"] += amt
        else:
            r["paid"] = amt
        tot["taken"] += r["taken"]; tot["paid"] += r["paid"]; tot["uzs"] += r["uzs"]; tot["usd"] += r["usd"]
        rows.append(r)
    return dict(d1=d1, d2=today, days=days, opening=round(opening, 2), closing=round(bal, 2), rows=rows, tot=tot)


def build_pdf(client, st, lang=None):
    import pdfgen, c1
    lang = lang if lang in T else A.lang_of(client)
    w = T.get(lang, T["ru"])
    usd, som = A.usd, A.som
    neg, pos, mut = "#b3261e", "#3E643C", "#6b7b76"
    doc = pdfgen.Doc(footer=f"Lumberture · {w['title']} · {client['name']}")
    ui = A.ui_settings()
    logo = pdfgen.load_image(A.ui_logo_path(ui["logo"])) if ui.get("logo") else None
    x0 = doc.m
    if logo:
        h = 52
        wd = h * logo["w"] / logo["h"]
        doc.image(logo, doc.m, doc.y, wd, h)
        x0 = doc.m + wd + 12
    doc.text(x0, doc.y + 2, w["title"], 17, True, pos)
    doc.text(x0, doc.y + 24, f"{w['client']}: {client['name']}", 12, True)
    doc.text(x0, doc.y + 41, f"{w['period']}: {st['d1']:%d.%m.%Y} — {st['d2']:%d.%m.%Y} · {w['made']} {datetime.now():%d.%m.%Y %H:%M} · "
             f"{A.get_setting('client_company_name') or 'Lumberture'}", 8, False, mut)
    doc.y += 64

    def debt_txt(v):
        return usd(v) if v >= 0.005 else (f"{w['adv']}: {usd(-v)}" if v <= -0.005 else w["none"])
    t = st["tot"]
    cards = [(w["open"], debt_txt(st["opening"]), None), (w["taken"], usd(t["taken"]), None),
             (w["paid"], usd(t["paid"]), pos),
             (w["close"], debt_txt(st["closing"]), neg if st["closing"] >= 0.005 else pos)]
    gap = 8
    cw = (doc.cw - gap * 3) / 4
    for i, (lbl, v, col) in enumerate(cards):
        x = doc.m + i * (cw + gap)
        doc.rect(x, doc.y, cw, 46, fill="#f0e4d5" if i == 3 else None, stroke="#cfd8d5")
        doc.text(x + 7, doc.y + 7, lbl, 7.5, False, mut)
        doc.text(x + 7, doc.y + 22, v, 12 if len(v) < 16 else 9.5, True, col or "#1f2a27")
    doc.y += 54
    sub = f"{w['paid']}: {w['paid_cash']} {usd(t['cash'])} · {w['paid_real']} {usd(t['real'])}"
    if abs(t["paid"] - t["cash"] - t["real"]) >= 0.01:
        sub += f" · {w['other']} {usd(t['paid'] - t['cash'] - t['real'])}"
    doc.para(sub, 8.5, mut)

    cols = [(w["cols"][0], 0.85, "l"), (w["cols"][1], 3.4, "l"), (w["cols"][2], 0.95, "r"), (w["cols"][3], 1.1, "r"),
            (w["cols"][4], 0.85, "r"), (w["cols"][5], 1.0, "r"), (w["cols"][6], 1.05, "r")]
    rows = [dict(cells=["", w["start"], "", "", "", "", usd(st["opening"])], style="sub")]
    for r in st["rows"]:
        if r["kind"] == 210:
            name = f"{w['inv']}{r['no']}"
        elif r["kind"] == 1195:
            name = (w["pay"] if r["paid"] >= 0 else w["ret"]) + f" №{r['no']}"
        else:
            name = f"{c1.DOC_NAMES.get(r['kind'], w['other'])} №{r['no']}"
        if r.get("note"):
            name += f" — {r['note']}"
        rows.append(dict(cells=[r["date"].strftime("%d.%m.%Y"), name, usd(r["taken"]) if r["taken"] else "",
                                som(r["uzs"]) if abs(r["uzs"]) >= 1 else "", usd(r["usd"]) if abs(r["usd"]) >= 0.01 else "",
                                usd(r["paid"]) if abs(r["paid"]) >= 0.01 else "", usd(r["bal"])],
                         color=[None, None, None, pos, pos, pos, neg if r["bal"] >= 0.005 else pos], bold_cols=(6,)))
        for l in r["lines"][:30]:
            q = f"{l['qty']:g}" if isinstance(l["qty"], (int, float)) else str(l["qty"])
            nt = f" ({l['note']})" if l.get("note") else ""
            rows.append(dict(cells=["", f"• {l['name']} — {q} {l['unit']}{nt}".replace("  ", " "), usd(l["usd"]), "", "", "", ""],
                             style="muted", indent=8, indent_col=1))
        if len(r["lines"]) > 30:
            rows.append(dict(cells=["", f"• … +{len(r['lines']) - 30}", "", "", "", "", ""], style="muted", indent=8, indent_col=1))
    if not st["rows"]:
        rows.append(dict(cells=["", w["empty"], "", "", "", "", ""], style="muted"))
    rows.append(dict(cells=["", w["close"], usd(t["taken"]), som(t["uzs"]) if t["uzs"] else "", usd(t["usd"]) if t["usd"] else "",
                            usd(t["paid"]), usd(st["closing"])], style="tot"))
    doc.table(cols, rows, size=8)
    doc.para(w["note"], 8, mut)
    return doc.output()


def client_cid(c):
    code = (c["onec_code"] or "") if "onec_code" in c.keys() else ""
    return code[5:] if code.startswith("1c77:") else None


def make(c, days=None, lang=None):
    """-> (pdf bytes, statement, filename). lang — берилмаса мижоз тили (Telegram'га юбориш); платформадан юклаганда — интерфейс тили."""
    cid = client_cid(c)
    if not cid:
        raise ValueError("мижоз 1С билан боғланмаган")
    st = statement(cid, days)
    lang = lang if lang in T else A.lang_of(c)
    fn = f"hisobot_{re.sub(r'[^0-9A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ_-]+', '_', c['name'])[:40]}_{st['d2']:%Y%m%d}.pdf"
    return build_pdf(c, st, lang), st, fn


def caption(c, st):
    lang = A.lang_of(c)
    w = T.get(lang, T["ru"])
    v = st["closing"]
    debt = A.usd(v) if v >= 0.005 else (f"{w['none'].lower()}, {w['adv'].lower()} {A.usd(-v)}" if v <= -0.005 else w["none"].lower())
    return w["caption"].format(name=c["name"], days=st["days"], debt=debt)


def save_pdf(data, fn):
    d = os.path.join(A.DATA_DIR, "reports")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, fn)
    with open(p, "wb") as f:
        f.write(data)
    lim = time.time() - 30 * 86400
    for x in os.listdir(d):
        try:
            if os.path.getmtime(os.path.join(d, x)) < lim:
                os.remove(os.path.join(d, x))
        except OSError:
            pass
    return p


# ---------------------------------------------------------------- мижоз «hisobot» деб ёзса
_state = {"last": 0.0, "lock": threading.Lock()}
POLL_SEC = 30


def init_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS report_peers(peer TEXT PRIMARY KEY, last_id INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS report_log(id INTEGER PRIMARY KEY, at TEXT, client_id INTEGER, peer TEXT, via TEXT, status TEXT, info TEXT DEFAULT '');
    """)


async def _scan(c, since_ts, seen, skip_users):
    """Охирги диалоглардан «hisobot» сўровлари. -> [(peer_key, kind, entity_id, phone, title, msg_id, max_id)]"""
    out = []
    async for d in c.iter_dialogs(limit=60):
        m = d.message
        if m is None or not m.date or m.date.timestamp() < since_ts:
            continue
        key = str(d.id)
        last = seen.get(key, 0)
        if m.id <= last:
            continue
        if d.is_user and (d.entity is None or getattr(d.entity, "bot", False) or d.entity.id in skip_users):
            continue
        if not (d.is_user or d.is_group):
            continue
        msgs = await c.get_messages(d.entity, min_id=last, limit=15)
        hit = None
        for x in sorted(msgs, key=lambda x: x.id):
            if x.out or not x.date or x.date.timestamp() < since_ts:
                continue
            if is_request(x.raw_text):
                hit = x.id
        out.append(dict(key=key, user=d.is_user, eid=d.entity.id, phone=getattr(d.entity, "phone", "") or "",
                        title=d.name or "", hit=hit, max_id=m.id))
    return out


async def _send(c, eid, path, cap, reply_to=None, text=None):
    ent = await c.get_input_entity(eid)
    if text:
        return await c.send_message(ent, text, reply_to=reply_to)
    return await c.send_file(ent, path, caption=cap, reply_to=reply_to, force_document=True)


def find_client(item):
    if item["user"]:
        # бир Telegram'да бир нечта мижоз бўлса — хабарлари ёқилгани (эгаси, 26.10: «Азиз ака Тошкент мебель» эмас, «Азиз ака 21-62»)
        c = A.q("SELECT * FROM clients WHERE tg_user_id=? AND active=1 ORDER BY sms_enabled DESC, COALESCE(c1_doc,1) DESC, id",
                (item["eid"],), one=True)
        if c is None and item["phone"]:
            ph = re.sub(r"\D", "", item["phone"])[-9:]
            for r in A.q("SELECT * FROM clients WHERE active=1 AND phone<>'' ORDER BY sms_enabled DESC, COALESCE(c1_doc,1) DESC, id"):
                if re.sub(r"\D", "", r["phone"])[-9:] == ph:
                    return r
        return c
    t = item["title"].strip().casefold()
    for r in A.q("SELECT * FROM clients WHERE active=1 AND tg_chat<>''"):
        ch = r["tg_chat"].strip()
        if ch.casefold() == t or ch.lstrip("-") == str(item["eid"]).lstrip("-") or ch == item["key"]:
            return r
    return None


def poll(force=False):
    if A.get_setting("report_on", "1") != "1" or not A.tgu_ready():
        return 0
    if not force and time.time() - _state["last"] < POLL_SEC:
        return 0
    with _state["lock"]:
        _state["last"] = time.time()
        since = A.parse_float(A.get_setting("report_since", "0"), 0)
        if not since:
            since = time.time()
            A.set_settings(report_since=str(since))
        seen = {r["peer"]: r["last_id"] for r in A.q("SELECT * FROM report_peers")}
        try:
            skip = {int(r["user_id"]) for r in A.q("SELECT user_id FROM ready_workers WHERE user_id IS NOT NULL")}
        except Exception:
            skip = set()
        api_id, api_hash = A.tgu_cfg()
        snd = A.tgu_sender()
        found = snd.call(api_id, api_hash, lambda c: _scan(c, since, seen, skip)) or []
        n = 0
        def mark(it):
            A.db().execute("INSERT INTO report_peers(peer,last_id) VALUES(?,?) ON CONFLICT(peer) DO UPDATE SET last_id=excluded.last_id",
                           (it["key"], it["max_id"]))
        for it in found:
            if not it["hit"]:
                mark(it)
                continue
            c = find_client(it)
            st_, info = "sent", ""
            try:
                if (c is None or not client_cid(c)) and not it["user"]:
                    st_ = "unknown"                      # бегона гуруҳ — жавоб бермаймиз
                elif c is None or not client_cid(c):
                    st_ = "unknown"
                    txt = ("Kechirasiz, raqamingiz bazamizda topilmadi. Hisobot uchun qo'ng'iroq qiling: " +
                           (A.get_setting("client_company_name") or "+998 33 409-60-70"))
                    snd.call(api_id, api_hash, lambda cl: _send(cl, it["eid"], None, None, it["hit"], txt))
                else:
                    today = date.today().isoformat()
                    cnt = A.scalar("SELECT COUNT(*) FROM report_log WHERE client_id=? AND status='sent' AND at>=?", (c["id"], today)) or 0
                    if cnt >= int(A.parse_float(A.get_setting("report_daily_max", "5"), 5)):
                        st_, info = "limit", f"бугун {cnt} та юборилган"
                    else:
                        data, stt, fn = make(c)
                        p = save_pdf(data, fn)
                        cap = caption(c, stt)            # БД — шу оқимда (Telegram оқимида эмас)
                        snd.call(api_id, api_hash, lambda cl: _send(cl, it["eid"], p, cap, it["hit"]))
                        info = fn
                n += 1
            except Exception as e:
                A.errlog.exception("report send")
                st_, info = "error", repr(e)[:300]
            # «кўрилди» белгиси — жавоб юборилгандан (ёки хато ёзилгандан) кейин; олдин қўйилса, сервер шу пайт тўхтаса сўров йўқолиб қоларди
            mark(it)
            A.db().execute("INSERT INTO report_log(at,client_id,peer,via,status,info) VALUES(?,?,?,?,?,?)",
                           (A.now(), c["id"] if c else None, it["key"] + " " + it["title"][:40], "telegram", st_, info))
            A.db().commit()
        A.db().commit()
        return n


def _loop():
    A._stop.wait(25)
    while not A._stop.is_set():
        try:
            with A.app.app_context():
                poll()
        except Exception:
            A.errlog.exception("report poll")
        A._stop.wait(POLL_SEC)


def register(app_module):
    global A
    A = app_module
    app, lr, ar = A.app, A.login_required, A.admin_required
    con = A.connect()
    init_tables(con)
    con.commit(); con.close()

    _orig_start = A.start_background_jobs

    def start_background_jobs():
        first = not A._bg["started"]
        _orig_start()
        if first:
            threading.Thread(target=_loop, name="report", daemon=True).start()
    A.start_background_jobs = start_background_jobs

    @app.route("/clients/<int:cid>/report.pdf")
    @lr
    def client_report_pdf(cid):
        c = A.q("SELECT * FROM clients WHERE id=?", (cid,), one=True) or abort(404)
        try:
            import i18n
            data, st, fn = make(c, int(A.parse_float(request.args.get("days"), 0)) or None, lang=i18n.lang())
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("client_card", cid=cid))
        resp = A.app.response_class(data, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = f"inline; filename=\"report_{cid}.pdf\""
        return resp

    @app.route("/clients/<int:cid>/report/send", methods=["POST"])
    @lr
    def client_report_send(cid):
        c = A.q("SELECT * FROM clients WHERE id=?", (cid,), one=True) or abort(404)
        try:
            data, st, fn = make(c)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("client_card", cid=cid))
        p = save_pdf(data, fn)
        ch, why = A.deliver("report", caption(c, st), client=c, via="tg", file=p)
        A.db().execute("INSERT INTO report_log(at,client_id,peer,via,status,info) VALUES(?,?,?,?,?,?)",
                       (A.now(), cid, "", "admin:" + A.current_user()["username"], "queued" if ch else "error", fn if ch else why))
        A.db().commit()
        flash("Ҳисобот (PDF) Telegram навбатига қўйилди" if ch else f"Юбориб бўлмади: {why}", "ok" if ch else "error")
        return redirect(url_for("client_card", cid=cid))
