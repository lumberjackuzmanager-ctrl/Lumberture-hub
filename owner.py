# -*- coding: utf-8 -*-
"""Шериклар учун ҳисобот (цех боти орқали): касса, кунлик савдо, цех, қарздорлар.
Ҳар куни белгиланган соатда (19:00) кун якуни юборилади; кейин касса ўзгарса — нима ўзгаргани ва вақти билан яна хабар."""
import json
from datetime import date, datetime, timedelta

A = None
P = None

GROUPS = {"wage": "ойлик/аванс", "sheet": "лист учун", "edge": "кромка учун", "exp": "харажат", "owner": "шериклар", "svc": "ташқи хизмат"}
WD = ["Душанба", "Сешанба", "Чоршанба", "Пайшанба", "Жума", "Шанба", "Якшанба"]


def init(app_module, raspil_module):
    global A, P
    A, P = app_module, raspil_module


def m(v):
    return f"{(v or 0):,.0f}".replace(",", " ")


def u(v):
    return ("−" if (v or 0) < 0 else "") + f"${abs(v or 0):,.2f}".replace(",", " ")


def pair(p):
    """[сум, $] → «9 435 000 сўм + $9 000»."""
    s = []
    if abs(p[0]) >= 0.5:
        s.append(f"{m(p[0])} сўм")
    if abs(p[1]) >= 0.005:
        s.append(u(p[1]))
    return " + ".join(s) or "0"


def owners():
    return [dict(r) for r in A.q("SELECT * FROM ready_workers WHERE owner=1 AND active=1")]


def hour():
    v = (A.get_setting("owner_hour", "19:00") or "19:00").strip()
    try:
        h, mi = v.split(":")
        return int(h), int(mi)
    except ValueError:
        return 19, 0


# ---------------------------------------------------------------- маълумот
def kassa_day(d):
    import kassa as K
    ym = d.isoformat()[:7]
    if not K.has_data(ym):
        return None
    K._memo.clear()
    mc = K.month_calc(ym)
    o = mc["days"][d.day - 1]
    r = A.q("SELECT updated_at FROM k_days WHERE ym=? AND day=?", (ym, d.day), one=True)
    return dict(o=o, mc=mc, upd=(r["updated_at"] if r else None))


def fingerprint(d):
    """Кассанинг шу кундаги ҳолати (ўзгаришни топиш учун)."""
    k = kassa_day(d)
    if not k or not k["o"]["filled"]:
        return None
    o = k["o"]
    f = dict(inc=[round(x, 2) for x in o["inc"]], out=[round(x, 2) for x in o["out"]], end=[round(x, 2) for x in o["end"]],
             fact=[round(x, 2) for x in o["fact"]] if o["fact"] is not None else None,
             g={g: [round(x, 2) for x in v] for g, v in o["groups"].items() if abs(v[0]) >= 0.5 or abs(v[1]) >= 0.005})
    return f


def kassa_text(d):
    k = kassa_day(d)
    if not k or not k["o"]["filled"]:
        return "💵 Касса: ҳали тўлдирилмаган."
    o = k["o"]
    out = [f"💵 Касса ({d.strftime('%d.%m')}, курс {m(o['rate'])})",
           f"Кун боши: {pair(o['start'])}",
           f"+ Кирим: {pair(o['inc'])}",
           f"− Чиқим: {pair(o['out'])}"]
    for g, v in o["groups"].items():
        if abs(v[0]) >= 0.5 or abs(v[1]) >= 0.005:
            out.append(f"   · {GROUPS.get(g, g)}: {pair(v)}")
    out.append(f"= Кун охири (ҳисоб): {pair(o['end'])}")
    if o["fact"] is not None:
        out.append(f"Факт (кассада): {pair(o['fact'])}")
        df = o["diff"] or 0
        out.append("✓ Фарқ йўқ" if abs(df) < 0.5 else (f"⚠️ Камомад: {u(df)}" if df > 0 else f"Ортиқча: {u(-df)}"))
    else:
        out.append("Факт ҳали киритилмаган.")
    if k["upd"]:
        out.append(f"Охирги ўзгариш: {k['upd'][11:16]}")
    return "\n".join(out)


def sales(d):
    """Шу кундаги проведен накладнойлар (сони, суммаси) ва тўловлар: 1С архиви + платформада проведен қилинганлар
    (26.10.2026 дан 1С ўчди — ҳаммаси платформада). 1С ўқилмаса ҳам платформаникиси ҳисобланади."""
    import kassa as K
    n, usd, uzs, ok1c = 0, 0.0, 0.0, True
    try:
        import c1
        jd = {k: v for k, v in c1.docs(210, d, d).items() if v["posted"] and not v["deleted"]}
        for r in c1.table("DT210"):
            if r["IDDOC"] in jd:
                usd += r.get("SP905") or 0
                uzs += r.get("SP559") or 0
        n = len(jd)
    except Exception:
        ok1c = False
    for r in A.q("SELECT usd, uzs FROM erp_docs WHERE src='pf' AND kind='sale' AND status='posted' AND date=?", (d.isoformat(),)):
        n += 1; usd += r["usd"] or 0; uzs += r["uzs"] or 0
    od = K.onec_day(d) or {}
    return dict(n=n, usd=usd, uzs=uzs, ok1c=ok1c,
                pay=[(od.get("uzs") or 0) + (od.get("s_uzs") or 0), (od.get("usd") or 0) + (od.get("s_usd") or 0)])


def sales_text(d):
    try:
        s = sales(d)
    except Exception:
        A.errlog.exception("owner sales")
        return "🧾 Савдо: ҳисобланмади."
    return (f"🧾 Савдо: {s['n']} та накладной — {pair([s['uzs'], s['usd']])}\n"
            f"Тўлов тушди: {pair(s['pay'])}")


def shop_text(d, short=False):
    day = d.isoformat()
    rows = P.machine_totals(day)
    att = A.q("SELECT a.kw FROM k_att a WHERE a.day=?", (day,))
    att = [r["kw"] for r in att if not P.is_test_kw(r["kw"])]
    out = [f"🏭 Цех: ишга келди {len(att)} киши"]
    by = {}
    for t in rows:
        by.setdefault(t["machine"], []).append(t)
    for mach, ts in by.items():
        t0 = ts[0]
        work = []
        if t0["sheets"]:
            work.append(f"{P._n(t0['sheets'])} лист")
        if t0["m45"]:
            work.append(f"45° {P._n(t0['m45'])} м")
        if t0["pp"]:
            work.append(f"{P._n(t0['pp'])} тешик")
        if t0["wq"]:
            work.append(f"{P._n(t0['wq'])} {t0['unit']}")
        who = ", ".join(P.wname(t["kw"]) + (" ⏳" if t.get("now") else "") for t in ts)
        out.append(f"• {P.MACHINES.get(mach, mach).split(' (')[0]}: {who}" + (f" — {', '.join(work)}" if work else " — иш ёзилмаган"))
    if not rows:
        out.append("Ботда станок иши йўқ.")
    if not short:
        try:
            codes = P.open_codes()
            if codes:
                out.append(f"🔖 Тўланмаган Ц-кодлар: {len(codes)} та")
        except Exception:
            pass
    return "\n".join(out)


def debt_text():
    cb = A.client_balances()
    rate = A.fx_rate() or 1
    try:
        import erp as _e
        km = _e.ckind_map()
    except Exception:
        km = {}
    code = {r["id"]: (r["onec_code"] or "")[5:] for r in A.q("SELECT id, onec_code FROM clients WHERE onec_code LIKE '1c77:%'")}
    kind = lambda k: km.get(code.get(k, ""), "client")
    oth = {}
    for k, v in cb.items():
        if v > 0 and kind(k) != "client":
            oth[kind(k)] = oth.get(kind(k), 0) + v
    tot = sum(v for k, v in cb.items() if v > 0 and kind(k) == "client")
    names = {r["id"]: r["name"] for r in A.q("SELECT id, name FROM clients WHERE active=1 AND sms_enabled<>0")}   # ўз ҳисоблари — чиқмайди
    top = sorted(((k, v) for k, v in cb.items() if v > 0.5 and k in names and kind(k) == "client"), key=lambda x: -x[1])[:8]
    out = [f"📒 Мижозлар қарзи: {u(tot / rate)} ({sum(1 for k, v in cb.items() if v > 0.5 and kind(k) == 'client')} киши)"]
    out += [f"• {names.get(k, '?')}: {u(v / rate)}" for k, v in top]
    lab = {"partner": "Шериклар", "account": "Ҳисоблар (карта, инвестиция)", "supplier": "Заводлар"}
    out += [f"{lab[k]}: {u(v / rate)}" for k, v in oth.items() if v / rate > 0.5 and k in lab]
    deb = A.debtors()
    if deb:
        out.append(f"\n🏢 Аренда қарзи: {u(sum(b for t, b in deb))}")
        out += [f"• {t['name']}: {u(b)}" for t, b in deb[:5]]
    return "\n".join(out)


def month_text(d):
    import kassa as K
    ym = d.isoformat()[:7]
    if not K.has_data(ym):
        return "Бу ой касса маълумоти йўқ."
    K._memo.clear()
    mc = K.month_calc(ym)
    out = [f"📅 {K.ym_title(ym)} — касса итоги",
           f"Кирим: {u(mc['income'])}", f"Чиқим: {u(mc['outcome'])}",
           f"  · ойлик/аванс {u(mc['sum_wage_usd'])}, лист {u(mc['sum_sheet'])}, кромка {u(mc['sum_edge'])}, харажат {u(mc['sum_exp'])}",
           f"Қолдиқ (фойда): {u(mc['profit'])}",
           *[x for x in factory_text(d, short=True).split("\n")[1:4]],
           f"Касса: {u(mc['start_usd'])} → {u(mc['end_usd'])}"]
    if mc.get("month_diff") is not None:
        md = mc["month_diff"]
        out.append("✓ Ойлик камомад йўқ" if abs(md) < 0.5 else (f"⚠️ Ойлик камомад: {u(md)}" if md > 0 else f"Ойлик ортиқча: {u(-md)}"))
    if mc.get("owners"):
        out.append("Шериклар олди: " + ", ".join(f"{o['item']['name']} {u(o['taken'])}" for o in mc["owners"] if abs(o["taken"]) >= 0.5) or "—")
    return "\n".join(out)


def _nkey(name):
    import re
    return re.sub(r"[^a-zа-яёўқғҳ]", "", (name or "").lower())[:5]


def factory(d):
    """Ой бошидан шу кунгача: заводдан келган товар (приход: 1С архиви + платформа) ва кассадан заводга берилган пул (лист/кромка учун), $."""
    import c1, kassa as K
    d1 = d.replace(day=1)
    live = lambda doc: doc["posted"] and not doc["deleted"]
    rec, rec_day, ok1c = {}, 0.0, True
    try:
        for r in c1.receipts(d1, d):
            if live(r["doc"]):
                rec[r["client"]] = rec.get(r["client"], 0) + (r["amount"] or 0)
                if r["doc"]["date"] == d:
                    rec_day += r["amount"] or 0
    except Exception:
        A.errlog.exception("owner factory 1c")
        ok1c = False
    for r in A.q("SELECT d.date, d.usd, COALESCE(c.name, '?') cname FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client "
                 "WHERE d.src='pf' AND d.kind='receipt' AND d.status='posted' AND d.date BETWEEN ? AND ?", (d1.isoformat(), d.isoformat())):
        rec[r["cname"]] = rec.get(r["cname"], 0) + (r["usd"] or 0)       # платформада ёзилган приход (26.10 дан — фақат шу ерда)
        if r["date"] == d.isoformat():
            rec_day += r["usd"] or 0
    paid, paid_day = {}, 0.0
    ym = d.isoformat()[:7]
    if K.has_data(ym):
        K._memo.clear()
        mc = K.month_calc(ym)
        names = {(i["kind"], i["id"]): i["name"] for i in A.q("SELECT id, kind, name FROM k_items WHERE kind IN ('sheet','edge')")}
        for (k, i), v in mc["per_item"].items():
            if k in ("sheet", "edge") and abs(v[2]) >= 0.005:
                paid[names.get((k, i), "?")] = paid.get(names.get((k, i), "?"), 0) + v[2]
        o = mc["days"][d.day - 1]
        paid_day = sum(o["groups"].get(g, [0, 0])[1] for g in ("sheet", "edge"))   # лист/кромка тўлови $ га ўтказилган
    rows = {}
    for nm, v in rec.items():
        rows.setdefault(_nkey(nm) or nm, dict(name=nm, rec=0.0, paid=0.0))["rec"] += v
    for nm, v in paid.items():
        r = rows.setdefault(_nkey(nm) or nm, dict(name=nm, rec=0.0, paid=0.0))
        r["paid"] += v
    rec_tot = sum(rec.values())
    mr = K.month_row(ym)
    if mr is not None and "onec_zav" in mr.keys() and mr["onec_zav"] is not None:
        rec_tot = mr["onec_zav"]                          # «Ой итоги»да қўлда ёзилган бўлса — ўша
    return dict(rows=sorted(rows.values(), key=lambda r: -(r["rec"] + r["paid"])), rec=rec_tot, paid=sum(paid.values()),
                rec_day=rec_day, paid_day=paid_day, d1=d1, ok1c=ok1c)


def factory_text(d, short=False):
    try:
        f = factory(d)
    except Exception:
        A.errlog.exception("owner factory")
        return "🚚 Завод: ҳисобланмади."
    df = f["rec"] - f["paid"]
    out = [f"🚚 Завод ({f['d1'].strftime('%d')}–{d.strftime('%d.%m')})",
           f"Келди (приход): {u(f['rec'])}", f"Берилди (касса): {u(f['paid'])}",
           "Фарқ: 0" if abs(df) < 0.5 else (f"Фарқ: {u(df)} — заводга қарзимиз" if df > 0 else f"Фарқ: {u(-df)} — олдиндан тўланган"),
           f"Бугун: келди {u(f['rec_day'])} · берилди {u(f['paid_day'])}"]
    if not short:
        for r in f["rows"][:15]:
            dd = r["rec"] - r["paid"]
            out.append(f"• {r['name']}: келди {u(r['rec'])} · берилди {u(r['paid'])}" + (f" · фарқ {u(dd)}" if abs(dd) >= 0.5 else ""))
    return "\n".join(out)


def kusok_text():
    st, rs = P.kusok_stock(), P.kusok_reserved()
    out = [f"🧩 Кусок омбори: {len(st)} та, {P._n(sum(k['m2'] for k in st))} м²" + (f" · брон {len(rs)} та" if rs else "")]
    own = {}
    for k in st:
        o = own.setdefault(k["owner"] or "?", [0, 0.0]); o[0] += 1; o[1] += k["m2"]
    out += [f"• {o}: {v[0]} та, {P._n(v[1])} м²" for o, v in sorted(own.items(), key=lambda x: -x[1][1])[:10]]
    return "\n".join(out)


def today_text(d=None):
    d = d or date.today()
    return "\n\n".join([f"📊 {WD[d.weekday()]}, {d.strftime('%d.%m.%Y')} — {datetime.now().strftime('%H:%M')}",
                        kassa_text(d), sales_text(d), factory_text(d, short=True), shop_text(d)])


# ---------------------------------------------------------------- юбориш ва назорат
def _send_all(text):
    import raspil_bot as B
    n = 0
    for o in owners():
        if o.get("bot_uid"):
            if B.send(o["bot_uid"], text):
                n += 1
    return n


def _snap_key(d):
    return f"own_snap_{d.isoformat()}"


def _diff(old, new):
    lab = dict(inc="Кирим", out="Чиқим", end="Кун охири", fact="Факт")
    out = []
    for k in ("inc", "out", "end", "fact"):
        a, b = (old or {}).get(k), (new or {}).get(k)
        if a != b:
            out.append(f"{lab[k]}: {pair(a) if a else '—'} → {pair(b) if b else '—'}")
    ga, gb = (old or {}).get("g", {}), (new or {}).get("g", {})
    for g in sorted(set(ga) | set(gb)):
        if ga.get(g) != gb.get(g):
            out.append(f"  · {GROUPS.get(g, g)}: {pair(ga[g]) if g in ga else '—'} → {pair(gb[g]) if g in gb else '—'}")
    return out


def tick(now=None):
    """Ҳар дақиқада: соат келганда кун якуни; кейин касса ўзгарса — ўзгариш хабари (охирги 3 кун)."""
    now = now or datetime.now()
    if not owners():
        return
    today = now.date()
    h, mi = hour()
    if now >= datetime.combine(today, datetime.min.time()).replace(hour=h, minute=mi) and A.get_setting("own_sent", "") != today.isoformat():
        A.set_settings(own_sent=today.isoformat())
        _send_all("🌙 Кун якуни\n\n" + today_text(today))
        A.set_settings(**{_snap_key(today): json.dumps(fingerprint(today))})
        return
    for d in (today, today - timedelta(days=1), today - timedelta(days=2)):
        raw = A.get_setting(_snap_key(d), None)
        if raw is None:
            continue
        old = json.loads(raw or "null")
        new = fingerprint(d)
        if new == old:
            continue
        A.set_settings(**{_snap_key(d): json.dumps(new)})
        k = kassa_day(d)
        upd = (k or {}).get("upd")
        head = (f"🔄 Касса ўзгартирилди — {d.strftime('%d.%m')} куни\n"
                f"Ўзгарган вақти: {(upd[11:16] + (' (' + upd[8:10] + '.' + upd[5:7] + ')' if upd[:10] != d.isoformat() else '')) if upd else now.strftime('%H:%M')}")
        _send_all(head + "\n\n" + "\n".join(_diff(old, new)) + "\n\n" + kassa_text(d))
