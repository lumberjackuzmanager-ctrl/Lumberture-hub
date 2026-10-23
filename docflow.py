"""Документы → Документация: договор, акт, КП, хат ва бошқа ҳужжатлар фирменный бланкда (PDF) + тарих.

Ҳужжат икки усулда яратилади:
  • платформада ёзиш (сарлавҳа, кимга, матн) — blank.render_text;
  • файл юклаш: PDF, расм (JPG/PNG) ёки Word (.docx/.doc/.rtf — компьютердаги Word орқали PDF'га ўгирилади).
Оригинал файл ва тайёр PDF data/docs/<йил>/ ичида сақланади, ҳар бирига рақам берилади (КП-2026-001)."""
import json, os, re, subprocess, tempfile, time
from datetime import date

import blank

A = None
KINDS = {"contract": ("Договор", "Д"), "act": ("Акт", "А"), "kp": ("Коммерческое предложение", "КП"),
         "letter": ("Письмо", "П"), "invoice": ("Счёт", "С"), "other": ("Другой документ", "Док")}
UP_EXT = {".pdf": "pdf", ".jpg": "img", ".jpeg": "img", ".png": "img", ".docx": "word", ".doc": "word", ".rtf": "word", ".odt": "word"}
MAX_UP = 20 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_files(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, no TEXT NOT NULL, date TEXT NOT NULL,
  title TEXT DEFAULT '', client_ext TEXT DEFAULT '', client_name TEXT DEFAULT '', to_text TEXT DEFAULT '', body TEXT DEFAULT '',
  mode TEXT NOT NULL DEFAULT 'text', orig_name TEXT DEFAULT '', orig_path TEXT DEFAULT '', pdf_path TEXT DEFAULT '', sign INTEGER NOT NULL DEFAULT 1,
  note TEXT DEFAULT '', created_by TEXT DEFAULT '', created_at TEXT, updated_at TEXT, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_doc_files_date ON doc_files(date);
"""


def docs_dir(year):
    d = os.path.join(A.DATA_DIR, "docs", str(year))
    os.makedirs(d, exist_ok=True)
    return d


def cfg():
    try:
        v = json.loads(A.get_setting("blank_cfg", "") or "{}")
    except ValueError:
        v = {}
    return dict(blank.DEFAULT_CFG, **{k: v[k] for k in blank.DEFAULT_CFG if k in v})


def logo_path():
    p = os.path.join(A.DATA_DIR, "blank_logo.png")
    if os.path.isfile(p):
        return p
    try:
        ui = A.ui_settings()
        if ui.get("logo"):
            return A.ui_logo_path(ui["logo"])
    except Exception:
        pass
    return None


def next_no(con, kind, d):
    """Ҳужжат рақами — ҳужжат санаси: «22-10» (эгаси айтди). Шу куни шу турдан яна бўлса — «22-10-2», «22-10-3»."""
    base = f"{d[8:10]}-{d[5:7]}"
    used = {no for (no,) in con.execute("SELECT no FROM doc_files WHERE kind=? AND date=? AND deleted=0", (kind, d))}
    if base not in used:
        return base
    k = 2
    while f"{base}-{k}" in used:
        k += 1
    return f"{base}-{k}"


def word_to_pdf(src, dst):
    """Word ҳужжатини PDF'га — компьютердаги Microsoft Word орқали (PowerShell COM). Тестларда RENT_DOCX_CMD."""
    cmd = os.environ.get("RENT_DOCX_CMD")
    if cmd:
        subprocess.run(cmd.split() + [src, dst], check=True, timeout=120)
        return
    if os.name != "nt":
        raise RuntimeError("Word ҳужжатини ўгириш фақат Windows'да (Microsoft Word керак)")
    ps = ("$ErrorActionPreference='Stop'; $w=New-Object -ComObject Word.Application; $w.Visible=$false; $w.DisplayAlerts=0; "
          "try { $d=$w.Documents.Open($env:LB_SRC, $false, $true); $d.ExportAsFixedFormat($env:LB_DST, 17); $d.Close(0) } finally { $w.Quit() }")
    env = dict(os.environ, LB_SRC=src, LB_DST=dst)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, capture_output=True, timeout=180,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not os.path.isfile(dst):
        raise RuntimeError("Word орқали PDF қилиб бўлмади: " + (r.stderr or b"").decode("cp866", "replace").strip()[-300:])


def build_pdf(row, orig_bytes=None):
    """Ҳужжатнинг тайёр PDF'ини қуради ва сақлайди -> pdf йўли."""
    c, lg = cfg(), logo_path()
    d = date.fromisoformat(row["date"])
    if row["mode"] == "text":
        pdf = blank.render_text(row["title"], row["body"], no=row["no"], ddate=d, to=row["to_text"] or "", cfg=c, logo=lg, sign=bool(row["sign"]))
    else:
        src = orig_bytes if orig_bytes is not None else open(row["orig_path"], "rb").read()
        kind = UP_EXT.get(os.path.splitext(row["orig_name"] or "")[1].lower())
        if kind == "img":
            pdf = blank.render_image(src, cfg=c, logo=lg)
        elif kind == "word":
            with tempfile.TemporaryDirectory() as td:
                ext = os.path.splitext(row["orig_name"])[1].lower()
                a, b = os.path.join(td, "src" + ext), os.path.join(td, "out.pdf")
                exact = False
                if ext == ".docx":
                    try:
                        src, exact = blank.fit_docx(src), True      # матн асл ўлчамда, бланк орасига қайта тахланади
                    except Exception:
                        A.errlog.exception("fit_docx")
                open(a, "wb").write(src)
                word_to_pdf(a, b)
                pdf = blank.render_pdf(open(b, "rb").read(), cfg=c, logo=lg, exact=exact)
        else:
            pdf = blank.render_pdf(src, cfg=c, logo=lg)
        if row["sign"]:
            pdf = blank.add_signature(pdf, row["kind"], c, lg)
    if row["mode"] == "text" and row["sign"] and row["kind"] in blank.SIGN_EVERY_PAGE:
        pdf = blank.add_signature(pdf, row["kind"], c, lg)
    safe = re.sub(r"[^\w\-]+", "_", row["no"])
    path = os.path.join(docs_dir(row["date"][:4]), f"{row['id']}_{safe}.pdf")
    with open(path, "wb") as f:
        f.write(pdf)
    return path


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        con.executescript(SCHEMA); con.commit()
        if not con.execute("SELECT 1 FROM settings WHERE key='docs_sign_v2'").fetchone():   # юкланган файлларда ҳам директор имзоси
            con.execute("UPDATE doc_files SET sign=1 WHERE mode='file'")
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('docs_sign_v2', '1')"); con.commit()
    finally:
        con.close()
    from flask import request, render_template, redirect, url_for, flash, abort, send_file

    def frameable(resp):
        """PDF ўз саҳифамиздаги iframe'да кўрилсин (бошқа сайтлар ичига қўйилмасин)."""
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'self'"
        return resp

    def clients():
        return A.q("SELECT ext, name FROM erp_clients WHERE COALESCE(deleted,0)=0 AND COALESCE(folder,0)=0 ORDER BY name COLLATE NOCASE")

    @app.route("/docs")
    @A.login_required
    def docs_list():
        kind = request.args.get("kind") if request.args.get("kind") in KINDS else ""
        q = (request.args.get("q") or "").strip()
        year = request.args.get("year") or ""
        sql, args = "SELECT * FROM doc_files WHERE deleted=0", []
        if kind:
            sql += " AND kind=?"; args.append(kind)
        if re.fullmatch(r"\d{4}", year):
            sql += " AND substr(date,1,4)=?"; args.append(year)
        if q:
            sql += " AND (lower(no||' '||title||' '||client_name||' '||to_text||' '||orig_name) LIKE ?)"; args.append(f"%{q.lower()}%")
        rows = A.q(sql + " ORDER BY date DESC, id DESC LIMIT 500", tuple(args))
        years = [r["y"] for r in A.q("SELECT DISTINCT substr(date,1,4) y FROM doc_files WHERE deleted=0 ORDER BY y DESC")]
        return render_template("docs_list.html", rows=rows, KINDS=KINDS, kind=kind, q=q, year=year, years=years)

    @app.route("/docs/new", methods=["GET", "POST"])
    @app.route("/docs/<int:did>/edit", methods=["GET", "POST"])
    @A.login_required
    def docs_form(did=None):
        row = A.q("SELECT * FROM doc_files WHERE id=? AND deleted=0", (did,), one=True) if did else None
        if did and not row:
            abort(404)
        mode = (row["mode"] if row else request.values.get("mode")) or "text"
        if request.method == "POST":
            f, db = request.form, A.db()
            kind = f.get("kind") if f.get("kind") in KINDS else "other"
            d = f.get("date") or date.today().isoformat()
            try:
                date.fromisoformat(d)
            except ValueError:
                d = date.today().isoformat()
            cext = (f.get("client") or "").strip()
            cl = A.q("SELECT name FROM erp_clients WHERE ext=?", (cext,), one=True) if cext else None
            vals = dict(kind=kind, date=d, title=(f.get("title") or "").strip()[:200], client_ext=cext if cl else "",
                        client_name=cl["name"] if cl else "", to_text=(f.get("to_text") or "").strip()[:600],
                        body=(f.get("body") or "")[:60000], sign=1 if f.get("sign") else 0, note=(f.get("note") or "").strip()[:300])
            up = request.files.get("file")
            data = None
            if mode == "file" and up and up.filename:
                ext = os.path.splitext(up.filename)[1].lower()
                data = up.read(MAX_UP + 1)
                if ext not in UP_EXT:
                    flash("Этот тип файла не поддерживается. Можно: PDF, Word (.docx, .doc, .rtf), JPG, PNG", "error")
                    return redirect(request.full_path)
                if len(data) > MAX_UP:
                    flash("Файл больше 20 МБ", "error")
                    return redirect(request.full_path)
            elif mode == "file" and not row:
                flash("Выберите файл", "error")
                return redirect(request.full_path)
            if mode == "text" and not (vals["title"] or vals["body"].strip()):
                flash("Напишите заголовок или текст", "error")
                return redirect(request.full_path)
            now = A.now()
            if row:
                if row["kind"] != kind or row["date"] != d:
                    vals["no"] = next_no(db, kind, d)
                db.execute("UPDATE doc_files SET " + ", ".join(f"{k}=?" for k in vals) + ", updated_at=? WHERE id=?", tuple(vals.values()) + (now, did))
            else:
                vals.update(no=(f.get("no") or "").strip()[:40] or next_no(db, kind, d), mode=mode, created_by=A.current_user()["username"], created_at=now, updated_at=now)
                did = db.execute("INSERT INTO doc_files(" + ", ".join(vals) + ") VALUES(" + ",".join("?" * len(vals)) + ")", tuple(vals.values())).lastrowid
            if data is not None:
                ext = os.path.splitext(up.filename)[1].lower()
                op = os.path.join(docs_dir(d[:4]), f"{did}_orig{ext}")
                with open(op, "wb") as fh:
                    fh.write(data)
                db.execute("UPDATE doc_files SET orig_name=?, orig_path=? WHERE id=?", (os.path.basename(up.filename)[:200], op, did))
            db.commit()
            r = A.q("SELECT * FROM doc_files WHERE id=?", (did,), one=True)
            try:
                p = build_pdf(r, data)
                old = r["pdf_path"]
                db.execute("UPDATE doc_files SET pdf_path=? WHERE id=?", (p, did))
                db.commit()
                if old and old != p and os.path.isfile(old):
                    try:
                        os.remove(old)
                    except OSError:
                        pass
                flash(f"Готово: {r['no']}", "ok")
            except Exception as e:
                A.errlog.exception("docs pdf")
                flash(f"Документ сохранён, но PDF не получился: {e}", "error")
            return redirect(url_for("docs_card", did=did))
        defaults = dict(kind=request.args.get("kind") if request.args.get("kind") in KINDS else "kp", date=date.today().isoformat(), sign=1,
                        title="", to_text="", body="", client_ext=request.args.get("client") or "", note="")
        if not row and defaults["client_ext"]:
            cl = A.q("SELECT name FROM erp_clients WHERE ext=?", (defaults["client_ext"],), one=True)
            if cl:
                defaults["to_text"] = cl["name"]
        return render_template("docs_form.html", r=row or defaults, new=not row, mode=mode, KINDS=KINDS, clients=clients(),
                               back=url_for("docs_card", did=did) if row else url_for("docs_list"))

    @app.route("/docs/<int:did>")
    @A.login_required
    def docs_card(did):
        r = A.q("SELECT * FROM doc_files WHERE id=? AND deleted=0", (did,), one=True) or abort(404)
        return render_template("docs_card.html", r=r, KINDS=KINDS, back=request.args.get("back") or url_for("docs_list"))

    @app.route("/docs/<int:did>/file/<what>")
    @A.login_required
    def docs_file(did, what):
        r = A.q("SELECT * FROM doc_files WHERE id=? AND deleted=0", (did,), one=True) or abort(404)
        p = r["pdf_path"] if what == "pdf" else r["orig_path"] if what == "orig" else ""
        base = os.path.abspath(os.path.join(A.DATA_DIR, "docs"))
        if not p or not os.path.isfile(p) or not os.path.abspath(p).startswith(base):
            abort(404)
        safe = re.sub(r"[^\w\-.]+", "_", r["no"])
        name = f"{safe}.pdf" if what == "pdf" else (r["orig_name"] or os.path.basename(p))
        return frameable(send_file(p, mimetype="application/pdf" if what == "pdf" else None, as_attachment=request.args.get("dl") == "1", download_name=name))

    @app.route("/docs/<int:did>/delete", methods=["POST"])
    @A.admin_required
    def docs_delete(did):
        A.db().execute("UPDATE doc_files SET deleted=1, updated_at=? WHERE id=?", (A.now(), did))
        A.db().commit()
        flash("Документ удалён из журнала (файлы сохранены)", "ok")
        return redirect(url_for("docs_list"))

    @app.route("/docs/<int:did>/rebuild", methods=["POST"])
    @A.login_required
    def docs_rebuild(did):
        r = A.q("SELECT * FROM doc_files WHERE id=? AND deleted=0", (did,), one=True) or abort(404)
        try:
            p = build_pdf(r)
            A.db().execute("UPDATE doc_files SET pdf_path=?, updated_at=? WHERE id=?", (p, A.now(), did))
            A.db().commit()
            flash("PDF пересобран на текущем бланке", "ok")
        except Exception as e:
            A.errlog.exception("docs rebuild")
            flash(f"PDF не получился: {e}", "error")
        return redirect(url_for("docs_card", did=did))

    @app.route("/docs/blank", methods=["GET", "POST"])
    @A.admin_required
    def docs_blank():
        if request.method == "POST":
            f = request.form
            if f.get("action") == "logo_del":
                try:
                    os.remove(os.path.join(A.DATA_DIR, "blank_logo.png"))
                except OSError:
                    pass
                flash("Логотип бланка удалён", "ok")
                return redirect(url_for("docs_blank"))
            v = {k: (f.get(k) or "").strip()[:600] for k in blank.DEFAULT_CFG}
            A.set_settings(blank_cfg=json.dumps(v, ensure_ascii=False))
            up = request.files.get("logo")
            if up and up.filename:
                raw = up.read(3 * 1024 * 1024 + 1)
                if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) > 3 * 1024 * 1024:
                    flash("Логотип — PNG до 3 МБ (лучше с прозрачным фоном)", "error")
                else:
                    with open(os.path.join(A.DATA_DIR, "blank_logo.png"), "wb") as fh:
                        fh.write(raw)
            flash("Бланк сохранён", "ok")
            return redirect(url_for("docs_blank"))
        return render_template("docs_blank.html", c=cfg(), has_logo=os.path.isfile(os.path.join(A.DATA_DIR, "blank_logo.png")),
                               ui_logo=bool(logo_path()))

    @app.route("/docs/blank/sample.pdf")
    @A.admin_required
    def docs_blank_sample():
        import io
        pdf = blank.render_text("Образец документа", "Так выглядит фирменный бланк Lumberture.\n\n# Раздел\nТекст документа. **Жирный** и обычный текст.\n\n"
                                "Наименование | Ед. | Кол-во\nЛДСП 16 мм | лист | 10\n\n- Пункт списка\n- Ещё пункт",
                                no="ОБРАЗЕЦ", to="Кому: Клиент", cfg=cfg(), logo=logo_path())
        return frameable(send_file(io.BytesIO(pdf), mimetype="application/pdf", download_name="blank_sample.pdf"))
