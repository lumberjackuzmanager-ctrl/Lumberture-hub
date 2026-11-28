# -*- coding: utf-8 -*-
"""Кастамону (KS Group) каталоги — @ksgroup_agent_bot. Eman'дан АЛОҲИДА модул (эгаси, 28.11: «Eman билан Кастамону
аралашиб кетмасин»): ўз файллари (data/ks_*), кейин ўз жадваллари (ks_*) ва каталогда ўз папкаси. eman.py га тегилмайди.

1-босқич — бот тузилишини ўрганиш (эгаси рухсати билан, 28.11). data/ks_probe.req (матн — функция номи, бўш бўлса
«explore») қўйилса, платформанинг Telegram-аккаунти ботга /start юборади, тугмаларни (фақат каталог/папка) бир неча марта
босади ва кўрганини data/ks_probe.json га ёзади. Буюртма, сотиб олиш, телефон/контакт юбориш тугмалари БОСИЛМАЙДИ.
Ҳар сафар шу файл қайта ўқилади — сервер қайта ёқилмасдан янги текширув функцияси қўшиш мумкин.
"""
import asyncio, json, os, re, time

A = None
BOT = "ksgroup_agent_bot"
PAUSE = 2.5                      # ҳар босишдан кейин (FloodWait'дан сақланиш)
MAX_ACTIONS = 14
# босилмайдиган тугмалар: буюртма, сават, тўлов, контакт, админ билан алоқа, тил ўзгартириш ва ҳ.к.
DENY_RE = re.compile(r"(заказ|оформ|корзин|сават|buyurtma|savat|order|cart|buy|купить|оплат|to'lov|tolov|тўлов|pay|"
                     r"контакт|kontakt|contact|телефон|telefon|phone|номер|raqam|рақам|менеджер|manager|оператор|operator|"
                     r"связ|aloqa|алоқа|жалоб|отзыв|feedback|удал|delete|отмен|cancel|выход|logout|язык|til|тил|lang|"
                     r"регистр|ro'yxat|рўйхат|register|sign)", re.I)


def _btns(markup):
    """Тугмалар: [{text, kind, data (bytes|None), url, row, col}] — Telethon турларидан мустақил (хом to_dict)."""
    if not markup:
        return []
    try:
        d = markup.to_dict()
    except Exception:
        return []
    out = []
    for ri, row in enumerate(d.get("rows") or []):
        for ci, b in enumerate(row.get("buttons") or []):
            t = b.get("type") if isinstance(b.get("type"), dict) else b
            kind = str(t.get("_") or b.get("_") or "")
            data = t.get("data")
            if isinstance(data, str):
                try:
                    data = bytes.fromhex(data)
                except ValueError:
                    data = data.encode()
            out.append(dict(text=(b.get("text") or "").strip(), kind=kind, data=data if isinstance(data, (bytes, bytearray)) else None,
                            url=t.get("url") or "", row=ri, col=ci, markup=str(d.get("_") or "")))
    return out


def _desc(m):
    bs = _btns(getattr(m, "reply_markup", None))
    return dict(id=m.id, out=bool(getattr(m, "out", False)), text=(getattr(m, "message", "") or "")[:3000],
                media=type(m.media).__name__ if getattr(m, "media", None) else None,
                buttons=[dict(text=b["text"], kind=b["kind"], data=(b["data"] or b"").decode("utf-8", "replace")[:80] if b["data"] else None,
                              url=b["url"][:200]) for b in bs])


def allowed(b):
    """Фақат каталогни очадиган оддий тугмалар: callback ёки оддий матнли (reply keyboard). Контакт/жойлашув/ҳавола/тўлов — йўқ."""
    k = b["kind"]
    if any(x in k for x in ("RequestPhone", "RequestGeo", "RequestPeer", "Url", "Buy", "Game", "WebView", "SimpleWebView", "Auth", "Copy")):
        return False
    if DENY_RE.search(b["text"] or ""):
        return False
    return ("Callback" in k and b["data"] is not None) or k in ("KeyboardButton", "") and bool(b["text"])


async def explore(c, out, bot=BOT, max_actions=MAX_ACTIONS):
    """/start → жавоб хабарлари → рухсат этилган тугмаларни кенглик бўйича (BFS) босиш, ҳар қадамда нима келганини ёзиш."""
    from telethon import functions, errors
    ent = await c.get_entity(bot)
    out.update(bot=bot, steps=[], started=time.strftime("%Y-%m-%d %H:%M:%S"))
    seen_sig, queue = set(), []

    async def fresh(after):
        await asyncio.sleep(PAUSE)
        return sorted([m for m in (await c.get_messages(ent, min_id=after, limit=20) or []) if not getattr(m, "out", False)],
                      key=lambda m: m.id)

    def enqueue(m, depth):
        sig = ((getattr(m, "message", "") or "")[:200], tuple(b["text"] for b in _btns(getattr(m, "reply_markup", None))))
        if sig in seen_sig:
            return
        seen_sig.add(sig)
        for b in _btns(getattr(m, "reply_markup", None)):
            if allowed(b):
                queue.append((m.id, b, depth + 1))

    async def last_id():
        ms = await c.get_messages(ent, limit=1) or []
        return max([m.id for m in ms] or [0])

    sent = await c.send_message(ent, "/start")
    got = await fresh(sent.id)
    out["steps"].append(dict(action="/start", got=[_desc(m) for m in got]))
    for m in got:
        enqueue(m, 0)
    n = 0
    while queue and n < max_actions:
        mid, b, depth = queue.pop(0)
        if depth > 3:
            continue
        n += 1
        after = await last_id()
        step = dict(action="click" if b["data"] is not None else "text", button=b["text"], depth=depth)
        try:
            if b["data"] is not None:
                try:
                    ans = await c(functions.messages.GetBotCallbackAnswerRequest(peer=ent, msg_id=mid, data=bytes(b["data"])))
                    step["answer"] = (getattr(ans, "message", None) or "")[:300] if ans else ""
                except errors.FloodWaitError as e:
                    step["flood"] = int(getattr(e, "seconds", 0) or 0)
                    out["steps"].append(step)
                    break
                got = await fresh(after)
                edited = await c.get_messages(ent, ids=mid)
                if edited is not None:
                    step["edited"] = _desc(edited)
                    enqueue(edited, depth)
            else:
                await c.send_message(ent, b["text"])
                got = await fresh(after)
        except Exception as e:                       # битта тугма хатоси — текширув давом этади
            step["error"] = f"{e.__class__.__name__}: {e}"[:300]
            got = []
        step["got"] = [_desc(m) for m in got]
        out["steps"].append(step)
        for m in got:
            enqueue(m, depth)
    out["actions"] = n
    out["left"] = [dict(text=b["text"], depth=d) for _, b, d in queue[:40]]
    return out


def probe_due():
    req = os.path.join(A.DATA_DIR, "ks_probe.req")
    if not os.path.exists(req):
        return None
    if not A.tgu_ready():
        return None                                   # аккаунт уланмаган — сўров кейинга қолади
    try:
        mode = open(req, encoding="utf-8").read().strip() or "explore"
    except OSError:
        mode = "explore"
    os.remove(req)
    src = os.path.abspath(__file__)
    ns = {"__name__": "ksgroup_live", "__file__": src}
    res, t0 = {}, time.time()
    try:
        exec(compile(open(src, encoding="utf-8").read(), src, "exec"), ns)
        ns["A"] = A
        fx = ns.get(mode)
        if fx and mode in ("explore",):
            api_id, api_hash = A.tgu_cfg()
            A.tgu_sender().call(api_id, api_hash, lambda cl: fx(cl, res), timeout=600)
        else:
            res = dict(error=f"функция йўқ: {mode}")
    except Exception as e:
        import traceback
        res = dict(res, error=repr(e), tb=traceback.format_exc()[-3000:])
    res["took"] = round(time.time() - t0, 1)
    with open(os.path.join(A.DATA_DIR, "ks_probe.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    return res


def register(app_module):
    global A
    A = app_module
    _orig = A.scheduler_tick

    def tick(dt=None):
        r = _orig(dt)
        try:
            probe_due()
        except Exception:
            A.errlog.exception("ksgroup probe")
        return r
    A.scheduler_tick = tick
