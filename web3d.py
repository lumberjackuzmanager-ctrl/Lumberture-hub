# -*- coding: utf-8 -*-
"""3D бўлими (lumberture.com/m/): Telegram орқали рўйхат, .b3d моделларни сақлаш, ҳисоблагич (эгаси, 24.10; ҳужжат «3d-royxat-bot-api»).

Оқим: сайт тасодифий код (32 hex) яратади → https://t.me/Lumberture_bot?start=web_<код> → бот рақам сўрайди (request_contact)
→ мижоз платформа базасига ёзилади ёки топилади (телефон бўйича; манба «3D сайт») → кодга сессия токени (180 кун) →
сайт ҳар 2 сонияда GET /auth/<код> → {token, name} (бир марта, кейин код ўчади; 10 дақиқада эскиради).

API (/api/online, CORS ва ташқи эшик — online.py дагидек):
  GET  /auth/ping      — {ok:1}: рўйхат ёқилган
  GET  /auth/<код>     — тасдиқланган бўлса {token, name}, бўлмаса {}
  POST /model          — multipart: file (.b3d, «BZ» сигнатура, ≤30 МБ), meta (JSON {name, order, parts, hw}) → {id}; 401 — токен эскирган
  GET  /stats          — {projects, parts} (фақат сонлар)
  GET  /models         — мижознинг ўз проектлари
Файллар фақат ўзимизнинг серверда (data/models). Токенлар логга ёзилмайди."""
import json, os, re, secrets, time
from datetime import datetime

from flask import request, jsonify, abort, send_file, render_template

A = None
CODE_TTL = 10 * 60
SESSION_DAYS = 180
MODEL_MAX = 30 * 1024 * 1024
MODELS_PER_DAY = 20
SRC_NOTE = "Манба: 3D сайт (Telegram орқали рўйхат)"
# мижозга огоҳлантириш — рақам базага, файллар ўз серверимизда (эгаси тасдиқлаган, 24.10)
ASK_3D = ("Lumberture 3D бўлимидан фойдаланиш учун рақамингизни юборинг 👇\n"
          "Рақамингиз ва Telegram исмингиз Lumberture мижозлари базасига ёзилади. Сайтда очган .b3d файлларингиз ўзимизнинг серверда сақланади. Уларни фақат Lumberture ходимлари буюртмангиз учун кўради, бошқаларга берилмайди.")

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_auth(code TEXT PRIMARY KEY, chat_id INTEGER, phone TEXT, name TEXT DEFAULT '', tok TEXT, created REAL, done REAL);
CREATE TABLE IF NOT EXISTS online_models(id INTEGER PRIMARY KEY, phone TEXT NOT NULL, client TEXT, name TEXT DEFAULT '', ord TEXT DEFAULT '',
  parts INTEGER DEFAULT 0, hw INTEGER DEFAULT 0, meta TEXT DEFAULT '{}', size INTEGER DEFAULT 0, path TEXT, at TEXT, ip TEXT DEFAULT '',
  deleted INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_online_models_ph ON online_models(phone);
"""
CODE_RE = re.compile(r"^[0-9a-f]{16,64}$")


def _ol():
    import online
    return online


def init_tables(con):
    con.executescript(SCHEMA)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ бот
def bot_start(con, cid, code, frm):
    """/start web_<код>. -> True"""
    O = _ol()
    init_tables(con)
    code = (code or "").strip().lower()
    if not CODE_RE.match(code):
        O.bot_send(cid, "Ҳавола нотўғри. Сайтда «Рўйхатдан ўтиш» тугмасини қайта босинг.")
        return True
    t = time.time()
    con.execute("DELETE FROM web_auth WHERE created<?", (t - 86400,))
    old = con.execute("SELECT * FROM web_auth WHERE code=?", (code,)).fetchone()
    if old and old["done"]:
        O.bot_send(cid, "✅ Сиз аллақачон рўйхатдан ўтгансиз. Сайтга қайтинг.", O.NO_KB)
        return True
    con.execute("INSERT INTO web_auth(code, chat_id, created) VALUES(?,?,?) ON CONFLICT(code) DO UPDATE SET chat_id=excluded.chat_id",
                (code, cid, t))
    con.commit()
    known = con.execute("SELECT phone, name FROM online_chats WHERE chat_id=? AND active=1 ORDER BY linked_at DESC LIMIT 1", (cid,)).fetchone()
    if known:                                          # рақами аввал шу бот орқали тасдиқланган — қайта сўралмайди
        confirm(con, code, known["phone"], known["name"] or _tg_name(frm), frm)
        return True
    O.bot_send(cid, ASK_3D, O.CONTACT_KB)
    return True


def _tg_name(frm):
    return " ".join(x for x in ((frm or {}).get("first_name"), (frm or {}).get("last_name")) if x)[:80]


def pending_for(con, cid):
    init_tables(con)
    return con.execute("SELECT * FROM web_auth WHERE chat_id=? AND done IS NULL AND created>? ORDER BY created DESC LIMIT 1",
                       (cid, time.time() - CODE_TTL)).fetchone()


def on_contact(con, cid, contact, frm):
    """Рақам келди ва шу чатда 3D рўйхат кутилмоқда. -> True агар ишланди"""
    O = _ol()
    w = pending_for(con, cid)
    if not w:
        return False
    if contact.get("user_id") != frm.get("id"):
        O.bot_send(cid, "Ўз рақамингизни пастдаги тугма орқали юборинг.", O.CONTACT_KB)
        return True
    ph = O.norm_phone(contact.get("phone_number"))
    if not ph:
        O.bot_send(cid, f"Бу рақамни боғлаб бўлмади. Савол бўлса: {O.contact_phone()}", O.NO_KB)
        return True
    name = _tg_name(frm)
    con.execute("INSERT INTO online_chats(phone, chat_id, name, username, linked_at, active) VALUES(?,?,?,?,?,1) "
                "ON CONFLICT(phone) DO UPDATE SET chat_id=excluded.chat_id, name=excluded.name, username=excluded.username, "
                "linked_at=excluded.linked_at, active=1", (ph, cid, name, (frm.get("username") or "")[:60], _now()))
    con.commit()
    confirm(con, w["code"], ph, name, frm)
    return True


def confirm(con, code, ph, name, frm):
    """Мижоз базада (топилади ёки «3D сайт» манбаси билан ёзилади) + сессия токени кодга."""
    O = _ol()
    cl = O.find_client(con, ph)
    if not cl:
        try:
            cl = make_client(con, ph, name, (frm or {}).get("username"))
        except ValueError as e:
            A.errlog.warning("web3d client: %s", e)
            cl = None
    cname = ""
    if cl:
        r = con.execute("SELECT name FROM erp_clients WHERE ext=?", (cl,)).fetchone()
        cname = (r["name"] if r else "") or ""
    tok = secrets.token_urlsafe(32)                     # 256 бит
    con.execute("DELETE FROM online_sessions WHERE exp<?", (time.time(),))
    con.execute("INSERT INTO online_sessions(tok, phone, exp, created_at, seen_at, ip) VALUES(?,?,?,?,?,?)",
                (O._sha(tok), ph, time.time() + SESSION_DAYS * 86400, _now(), _now(), "3d"))
    con.execute("UPDATE web_auth SET phone=?, name=?, tok=?, done=? WHERE code=?", (ph, cname or name, tok, time.time(), code))
    con.commit()
    w = con.execute("SELECT chat_id FROM web_auth WHERE code=?", (code,)).fetchone()
    if w and w["chat_id"]:
        O.bot_send(w["chat_id"], "✅ Рўйхатдан ўтдингиз. Сайтга қайтинг — 3D бўлими очилди.", O.NO_KB)


def make_client(con, phone, name, username=None):
    import erp as E
    O = _ol()
    base = (name or "").strip() or "3D сайт мижози"
    nm = base if len(base) >= 2 else "3D сайт мижози"
    taken = {(r["name"] or "").casefold().strip() for r in con.execute("SELECT name FROM erp_clients WHERE folder=0 AND deleted=0")}
    if nm.casefold() in taken:
        nm = f"{base} ({O.fmt_phone(phone)})"
    i = 2
    while nm.casefold() in taken:
        nm, i = f"{base} ({O.fmt_phone(phone)}) {i}", i + 1
    f = {"name": nm, "phones": O.fmt_phone(phone), "cnote": SRC_NOTE, "ckind": "client"}
    if username:
        f["tg"] = "@" + str(username).lstrip("@")[:40]
    with A.app.test_request_context():                # бот поллери сўровдан ташқарида ишлайди — save_client current_user() сўрайди
        return E.save_client(con, None, f)


# ------------------------------------------------------------------ API
def stats(con):
    r = con.execute("SELECT COUNT(*), COALESCE(SUM(parts),0) FROM online_models WHERE deleted=0").fetchone()
    return dict(projects=int(r[0] or 0), parts=int(r[1] or 0))


def _int(v, hi=100000):
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, hi))


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        init_tables(con); con.commit()
    finally:
        con.close()
    O = _ol()

    def guard(kind, limit, per):
        if not O.rate_ok(kind, O.client_ip_real(), limit, per):
            return O.err("rate_limited", "Кўп сўров. Бироздан кейин қайта уриниб кўринг.", 429)
        return None

    @app.route("/api/online/auth/ping", methods=["GET"])
    def api_web_ping():
        return jsonify(dict(ok=1, bot=O.bot_username() or None))

    @app.route("/api/online/auth/<code>", methods=["GET"])
    def api_web_auth(code):
        x = guard("wauth", 120, 60)
        if x:
            return x
        code = (code or "").strip().lower()
        if not CODE_RE.match(code):
            return jsonify({})
        con = A.db()
        init_tables(con)
        t = time.time()
        con.execute("DELETE FROM web_auth WHERE (done IS NOT NULL AND done<?) OR created<?", (t - CODE_TTL, t - 86400))   # олинмаган токен сақланмайди
        con.commit()
        w = con.execute("SELECT * FROM web_auth WHERE code=?", (code,)).fetchone()
        if not w or not w["done"] or not w["tok"]:
            return jsonify({})
        con.execute("DELETE FROM web_auth WHERE code=?", (code,))      # бир марта берилади
        con.commit()
        if time.time() - (w["done"] or 0) > CODE_TTL:
            return jsonify({})
        return jsonify(dict(token=w["tok"], name=w["name"] or "", phone=O.mask_phone(w["phone"]), expires_days=SESSION_DAYS))

    @app.route("/api/online/model", methods=["POST"])
    def api_web_model():
        try:
            request.max_content_length = MODEL_MAX + 256 * 1024
        except Exception:
            pass
        ph = O.auth_phone()
        if not ph:
            return O.err("auth", "Кириш керак", 401)
        if not O.rate_ok("model_day", ph, MODELS_PER_DAY, 86400):
            return O.err("rate_limited", f"Бир кунда {MODELS_PER_DAY} тадан кўп файл бўлмайди", 429)
        f = request.files.get("file")
        if not f:
            return O.err("no_file", "Файл йўқ")
        raw = f.read(MODEL_MAX + 1)
        if not raw or len(raw) > MODEL_MAX:
            return O.err("too_large" if raw else "no_file", "Файл йўқ ёки 30 МБ дан катта")
        if not raw.startswith(b"BZ"):
            return O.err("bad_file", "Бу .b3d файл эмас")
        try:
            meta = json.loads(request.form.get("meta") or "{}")
        except ValueError:
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        name = re.sub(r"\s+", " ", str(meta.get("name") or f.filename or "")).strip()[:120]
        ordn = re.sub(r"\s+", " ", str(meta.get("order") or "")).strip()[:60]
        hw = meta.get("hw")
        hw_n = _int(sum(_int(v) for v in hw.values()) if isinstance(hw, dict) else (len(hw) if isinstance(hw, list) else hw))
        con = A.db()
        init_tables(con)
        cl = O.find_client(con, ph)
        mid = con.execute("INSERT INTO online_models(phone, client, name, ord, parts, hw, meta, size, at, ip) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (ph, cl, name, ordn, _int(meta.get("parts")), hw_n, json.dumps(meta, ensure_ascii=False)[:8000], len(raw), _now(),
                           O.client_ip_real())).lastrowid
        folder = os.path.join(A.DATA_DIR, "models", datetime.now().strftime("%Y-%m"))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{mid}_{secrets.token_hex(4)}.b3d")
        with open(path, "wb") as fh:
            fh.write(raw)
        con.execute("UPDATE online_models SET path=? WHERE id=?", (path, mid))
        con.commit()
        A.errlog.warning("3d model: №%s — %s КБ, %s деталь", mid, len(raw) // 1024, _int(meta.get("parts")))
        return jsonify(dict(ok=True, id=mid))

    @app.route("/api/online/stats", methods=["GET"])
    def api_web_stats():
        x = guard("wstats", 60, 60)
        if x:
            return x
        con = A.db()
        init_tables(con)
        return jsonify(stats(con))

    @app.route("/api/online/models", methods=["GET"])
    def api_web_models():
        ph = O.auth_phone()
        if not ph:
            return O.err("auth", "Кириш керак", 401)
        con = A.db()
        init_tables(con)
        rows = con.execute("SELECT id, name, ord, parts, hw, size, at FROM online_models WHERE phone=? AND deleted=0 ORDER BY id DESC LIMIT 100", (ph,))
        return jsonify(dict(ok=True, models=[dict(id=r["id"], name=r["name"], order=r["ord"], parts=r["parts"], hw=r["hw"], size=r["size"],
                                                  at=r["at"]) for r in rows]))

    # ---------------------------------------------------------- офис: юкланган моделлар (фақат админ, ички тармоқ)
    @app.route("/erp/online/models")
    @A.admin_required
    def erp_online_models():
        con = A.db()
        init_tables(con)
        rows = con.execute("SELECT m.*, c.name cname FROM online_models m LEFT JOIN erp_clients c ON c.ext=m.client WHERE m.deleted=0 "
                           "ORDER BY m.id DESC LIMIT 300").fetchall()
        return render_template("erp_online_models.html", rows=rows, st=stats(con), fmt_phone=O.fmt_phone)

    @app.route("/erp/online/models/<int:mid>/file")
    @A.admin_required
    def erp_online_model_file(mid):
        con = A.db()
        r = con.execute("SELECT * FROM online_models WHERE id=?", (mid,)).fetchone()
        p = r["path"] if r else ""
        root = os.path.realpath(A.DATA_DIR)
        if not p or not os.path.realpath(p).startswith(root + os.sep) or not os.path.isfile(p):
            abort(404)
        nm = re.sub(r"[^\w .,()-]+", "_", r["name"] or f"model_{mid}").strip() or f"model_{mid}"
        return send_file(p, as_attachment=True, download_name=(nm if nm.lower().endswith(".b3d") else nm + ".b3d"),
                         mimetype="application/octet-stream")
