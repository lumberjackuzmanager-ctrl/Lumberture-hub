# -*- coding: utf-8 -*-
"""Click ва Payme тушумлари: Telegram гуруҳларидаги бот хабарларини (CLICKUZ, PaymeBusinessNotifier) хабар аккаунти
орқали ўқиб, ҳар бир тўловни базага ёзади (ID бўйича такрорсиз). Платформа накладнойидаги Click/Payme тўловлари билан
ўзи боғланади; боғланмаганлари рўйхатда туради — мижозга қўлда бириктирилади. Гуруҳга ҳеч нарса ёзилмайди."""
import re, time
from datetime import datetime, date

from flask import request, render_template, redirect, url_for, flash

A = None
E = None   # erp модули

SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_epay(id INTEGER PRIMARY KEY, src TEXT NOT NULL, ext_id TEXT NOT NULL, amount REAL NOT NULL, at TEXT,
  who TEXT DEFAULT '', receipt TEXT DEFAULT '', status TEXT DEFAULT 'ok', chat TEXT DEFAULT '', msg_id INTEGER, raw TEXT DEFAULT '',
  pay_id INTEGER, client TEXT, note TEXT DEFAULT '', linked_by TEXT, created_at TEXT, UNIQUE(src, ext_id));
CREATE INDEX IF NOT EXISTS erp_epay_at ON erp_epay(at);
"""

SRC = {"click": "Click", "payme": "Payme"}
NEW_HOOKS = []     # f(con, [янги erp_epay қаторлари]) — масалан ишчилар боти: QR кўрсатган ишчига «тушди» хабари
FAST = []          # f(con) -> True: кимдир тўлов кутяпти — 5 эмас, 2 дақиқада ўқиймиз
DEF_CHATS = {"click": "CLICK", "payme": "Payme"}


def _num(s):
    """«40,000.00» / «611 000,00» / «500000» -> float."""
    s = re.sub(r"[\s  ]", "", s or "")
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse(text, src=None):
    """Бот хабаридан тўлов: dict(src, ext_id, amount, at, who, receipt, status) ёки None (тўлов хабари эмас)."""
    t = text or ""
    m_amt = re.search(r"([\d][\d\s  .,]*\d)\s*(?:сум|сўм|so['ʻ’]?m|uzs)\b", t, re.I)
    m_ts = re.search(r"(\d{2}:\d{2}:\d{2})\s+(\d{2})\.(\d{2})\.(\d{4})", t)
    if not m_amt or not m_ts:
        return None
    amt = _num(m_amt.group(1))
    if not amt:
        return None
    at = f"{m_ts.group(4)}-{m_ts.group(3)}-{m_ts.group(2)} {m_ts.group(1)}"
    low = t.casefold()
    status = "ok" if ("успешно" in low or "muvaffaqiyat" in low) else ("cancel" if ("отмен" in low or "bekor" in low or "возврат" in low) else "other")
    lines = [re.sub(r"^[^\w+*]+", "", l).strip() for l in t.splitlines()]
    src = src or ("payme" if "payme" in low or re.search(r"^[0-9a-f]{24}$", t, re.M) else "click")
    who = next((l for l in lines if re.fullmatch(r"\+?998[\d*]{6,}|\d{6}\*+\d{4}", l.replace(" ", ""))), "")
    if src == "payme":
        ext = next((l for l in lines if re.fullmatch(r"[0-9a-f]{20,32}", l)), "")
        receipt = next((l for l in lines if re.fullmatch(r"\d{1,8}", l)), "")
    else:
        ext = next((l for l in lines if re.fullmatch(r"\d{7,14}", l)), "")
        receipt = ""
    if not ext:
        ext = f"{at}|{amt:.2f}|{who}"
    return dict(src=src, ext_id=ext, amount=round(amt, 2), at=at, who=who, receipt=receipt, status=status)


def store(con, p, chat="", msg_id=None, raw=""):
    """Ёзиш (такрорсиз). Бекор қилинган хабар келса — мавжудини 'cancel' қилади. -> True агар янги."""
    old = con.execute("SELECT id, status FROM erp_epay WHERE src=? AND ext_id=?", (p["src"], p["ext_id"])).fetchone()
    if old:
        if p["status"] == "cancel" and old["status"] != "cancel":
            con.execute("UPDATE erp_epay SET status='cancel' WHERE id=?", (old["id"],))
        return False
    con.execute("INSERT INTO erp_epay(src,ext_id,amount,at,who,receipt,status,chat,msg_id,raw,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (p["src"], p["ext_id"], p["amount"], p["at"], p["who"], p["receipt"], p["status"], chat, msg_id, (raw or "")[:1000],
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    return True


def method_id(con, src):
    r = con.execute("SELECT id FROM erp_methods WHERE lower(name)=?", (SRC[src].lower(),)).fetchone()
    return r[0] if r else None


def auto_link(con):
    """Платформа накладнойидаги Click/Payme тўлов қатори (шу кун, шу сумма) билан боғлаш."""
    n = 0
    for e in con.execute("SELECT * FROM erp_epay WHERE pay_id IS NULL AND status='ok'").fetchall():
        mid = method_id(con, e["src"])
        if not mid:
            continue
        p = con.execute("SELECT p.id, d.client FROM erp_pays p JOIN erp_docs d ON d.id=p.doc_id WHERE p.method_id=? AND p.cur='uzs' "
                        "AND abs(p.amount-?)<0.5 AND d.date=? AND d.status='posted' AND p.id NOT IN (SELECT pay_id FROM erp_epay WHERE pay_id IS NOT NULL) "
                        "ORDER BY d.time LIMIT 1", (mid, e["amount"], (e["at"] or "")[:10])).fetchone()
        if p:
            con.execute("UPDATE erp_epay SET pay_id=?, client=?, linked_by='автомат' WHERE id=?", (p["id"], p["client"], e["id"]))
            n += 1
    return n


def chats():
    return {k: (A.get_setting(f"epay_{k}_chat", v) or v).strip() for k, v in DEF_CHATS.items()}


async def _fetch(c, want, last):
    """Гуруҳлар номи бўйича (ичида шу сўз бор) — янги хабарлар. -> [(src, chat_title, msg_id, text)], {src: max_id}"""
    out, mx = [], dict(last)
    async for d in c.iter_dialogs(limit=400):
        title = (getattr(d, "name", "") or "")
        for src, key in want.items():
            if key and key.casefold() in title.casefold() and (d.is_group or d.is_channel):
                kw = dict(limit=300)
                if last.get(src):
                    kw["min_id"] = last[src]
                async for m in c.iter_messages(d.entity, **kw):
                    if m.out:
                        continue
                    out.append((src, title, m.id, m.raw_text or ""))
                    mx[src] = max(mx.get(src) or 0, m.id)
    return out, mx


def poll(force=False):
    """Хабарларни ўқиш (ҳар 5 дақиқада). -> dict(new, seen) ёки None."""
    if not A.tgu_ready():
        return None
    every = 300
    try:
        if any(f(A.db()) for f in FAST):
            every = 120
    except Exception:
        A.errlog.exception("epay fast")
    if not force and time.time() - float(A.get_setting("epay_at", "0") or 0) < every:
        return None
    A.set_settings(epay_at=str(time.time()))
    last = {k: int(A.get_setting(f"epay_{k}_last", "0") or 0) for k in DEF_CHATS}
    api_id, api_hash = A.tgu_cfg()
    want = chats()                         # базани шу потокда ўқиймиз (Telegram потоки SQLite'га тегмасин)
    res = A.tgu_sender().call(api_id, api_hash, lambda cl: _fetch(cl, want, last), timeout=120)
    if not res:
        return None
    msgs, mx = res
    con = A.db()
    new, fresh = 0, []
    for src, title, mid, text in msgs:
        p = parse(text, src)
        if p and store(con, p, title, mid, text):
            new += 1
            fresh.append((p["src"], p["ext_id"]))
    for k, v in mx.items():
        if v:
            A.set_settings(commit=False, **{f"epay_{k}_last": str(v)})
    linked = auto_link(con)
    con.commit()
    A.set_settings(epay_last_ok=A.now(), epay_last_n=str(new))
    if fresh and NEW_HOOKS:
        rows = [con.execute("SELECT * FROM erp_epay WHERE src=? AND ext_id=?", k).fetchone() for k in fresh]
        for f in NEW_HOOKS:
            try:
                f(con, [r for r in rows if r])
            except Exception:
                A.errlog.exception("epay hook")
    return dict(new=new, seen=len(msgs), linked=linked)


def register(app_module, erp_module):
    global A, E
    A, E = app_module, erp_module
    app = A.app
    con = A.connect(); con.executescript(SCHEMA); con.commit(); con.close()

    _orig = A.scheduler_tick
    def tick(dt=None):
        res = _orig(dt)
        if A.get_setting("epay_on", "1") == "1":
            try:
                poll()
            except Exception:
                A.errlog.exception("epay poll")
        return res
    A.scheduler_tick = tick

    @app.route("/erp/epay", methods=["GET", "POST"])
    @A.login_required
    def erp_epay():
        con = A.db()
        if request.method == "POST":
            f, act = request.form, request.form.get("act")
            if act == "poll":
                try:
                    r = poll(force=True)
                    flash(f"Ўқилди: янги {r['new']} та (кўрилди {r['seen']}), боғланди {r['linked']}" if r else
                          "Telegram аккаунти уланмаган ёки жавоб йўқ", "ok" if r else "error")
                except Exception as e:
                    A.errlog.exception("epay poll")
                    flash(f"Хато: {e}", "error")
            elif act == "client":
                ext = (f.get("client") or "").strip()
                if not con.execute("SELECT 1 FROM erp_clients WHERE ext=?", (ext,)).fetchone():
                    flash("Мижозни рўйхатдан танланг", "error")
                else:
                    con.execute("UPDATE erp_epay SET client=?, note=?, linked_by=? WHERE id=?",
                                (ext, (f.get("note") or "")[:200], E._user(), f.get("id")))
                    E.log(con, None, "epay_link", f"{f.get('id')} → {ext}"); con.commit(); flash("Бириктирилди", "ok")
            elif act == "chats" and A.is_admin():
                A.set_settings(epay_click_chat=(f.get("click") or "CLICK").strip(), epay_payme_chat=(f.get("payme") or "Payme").strip())
                flash("Сақланди", "ok")
            return redirect(url_for("erp_epay", **request.args))
        today = date.today()
        d1 = A.parse_date(request.args.get("d1")) or today.replace(day=1)
        d2 = A.parse_date(request.args.get("d2")) or today
        show = request.args.get("show", "")
        sql = ("SELECT e.*, c.name cname, d.no dno, d.id did FROM erp_epay e LEFT JOIN erp_clients c ON c.ext=e.client "
               "LEFT JOIN erp_pays p ON p.id=e.pay_id LEFT JOIN erp_docs d ON d.id=p.doc_id WHERE substr(e.at,1,10) BETWEEN ? AND ?")
        args = [d1.isoformat(), d2.isoformat()]
        if show == "free":
            sql += " AND e.pay_id IS NULL AND e.client IS NULL AND e.status='ok'"
        rows = A.q(sql + " ORDER BY e.at DESC LIMIT 1000", args)
        tot = {}
        for r in rows:
            if r["status"] != "ok":
                continue
            mid = method_id(con, r["src"])
            pct = E.fee_pct(con, mid, r["at"][:10]) if mid else 0
            t = tot.setdefault(r["src"], dict(n=0, amount=0.0, fee=0.0))
            t["n"] += 1; t["amount"] += r["amount"]; t["fee"] += r["amount"] * pct / 100
        return render_template("erp_epay.html", rows=rows, tot=tot, d1=d1, d2=d2, show=show, SRC=SRC, ch=chats(),
                               last=A.get_setting("epay_last_ok", ""), tgu=A.tgu_ready())
