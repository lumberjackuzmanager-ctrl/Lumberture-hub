"""Платформанинг ўз «Печать» ойнаси: браузернинг чоп этиш ойнаси ўрнига.

Қандай ишлайди:
  1) /api/print/prepare — чоп этиладиган нарсадан PDF тайёрлайди:
       • url (PDF қайтарадиган саҳифа, масалан бланкдаги ҳужжат) — платформанинг ўзидан олинади (фойдаланувчининг ҳуқуқи билан);
       • html (ҳозир экранда турган саҳифа — таҳрир, масштаб, ўрин ҳолати билан) — компьютердаги Edge/Chrome (headless) PDF қилади.
     Олдиндан кўриш учун саҳифалар расмини беради.
  2) /api/print/send — танланган принтерга юборади (Windows: pypdfium2 -> GDI). Нусха, саҳифалар.
Принтерлар рўйхати — /api/print/printers."""
import io, json, os, re, secrets, shutil, subprocess, tempfile, threading, time

A = None
EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
JOB_TTL = 24 * 3600
_jobs_lock = threading.Lock()


def jobs_dir():
    d = os.path.join(A.DATA_DIR, "printjobs")
    os.makedirs(d, exist_ok=True)
    return d


def _cleanup():
    now = time.time()
    try:
        for n in os.listdir(jobs_dir()):
            p = os.path.join(jobs_dir(), n)
            if now - os.path.getmtime(p) > JOB_TTL:
                os.remove(p)
    except OSError:
        pass


def browser():
    p = os.environ.get("RENT_HEADLESS_BROWSER")
    if p:
        return p
    for c in EDGE:
        if os.path.isfile(c):
            return c
    for n in ("chromium", "chromium-browser", "google-chrome", "msedge"):
        w = shutil.which(n)
        if w:
            return w
    return None


def html_to_pdf(html, base_url):
    """Экрандаги саҳифа HTML'ини PDF қилади (Edge/Chrome headless). base_url — /static, расм ва ҳ.к. учун."""
    exe = browser()
    if not exe:
        raise RuntimeError("Edge ёки Chrome топилмади")
    head = (f'<base href="{base_url}"><script>window.__LB_HEADLESS=1</script>'
            '<style>@page{size:A4;margin:10mm}</style>')
    html = re.sub(r"(<head[^>]*>)", r"\1" + head.replace("\\", "\\\\"), html, count=1, flags=re.I) if re.search(r"<head", html, re.I) else head + html
    html = re.sub(r"<script>/\* ойна ёпилса.*?</script>", "", html, flags=re.S)          # «ойна ёпилса — чиқиш» текшируви бу ерда керак эмас
    with tempfile.TemporaryDirectory() as td:
        src, out = os.path.join(td, "page.html"), os.path.join(td, "out.pdf")
        with open(src, "w", encoding="utf-8") as f:
            f.write(html)
        url = "file:///" + src.replace("\\", "/").lstrip("/")
        cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--no-pdf-header-footer",
               "--hide-scrollbars", "--run-all-compositor-stages-before-draw", "--virtual-time-budget=4000",
               f"--user-data-dir={os.path.join(td, 'prof')}", f"--print-to-pdf={out}", url]
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            cmd.insert(1, "--no-sandbox")
        r = subprocess.run(cmd, capture_output=True, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not os.path.isfile(out) or os.path.getsize(out) < 100:
            raise RuntimeError("PDF тайёрланмади: " + (r.stderr or b"").decode("utf-8", "replace")[-300:])
        return open(out, "rb").read()


def inline_assets(html, base_path, fetch):
    """Саҳифадаги расм (<img src>) ва стилларни (<link rel=stylesheet>) PDF'га ичига жойлайди (data: URI / <style>).
    Сабаб: headless браузер сервердан логотип ва ҳ.к.ни ола олмаслиги мумкин (сессия йўқ, тармоқ чеклови).
    fetch(path) -> (bytes, mimetype) ёки None — платформанинг ўзидан (фойдаланувчи ҳуқуқи билан)."""
    import base64
    from urllib.parse import urljoin, urlsplit
    cache = {}

    def get(u):
        if not u or u.startswith(("data:", "blob:", "#")):
            return None
        sp = urlsplit(u)
        if sp.scheme in ("http", "https") and sp.netloc:
            path = sp.path + ("?" + sp.query if sp.query else "")
        elif sp.scheme:
            return None
        else:
            path = urljoin(base_path, u)
        if path not in cache:
            try:
                cache[path] = fetch(path)
            except Exception:
                cache[path] = None
        return cache[path]

    def img(m):
        r = get(m.group(2))
        if not r:
            return m.group(0)
        data, mt = r
        return m.group(1) + "data:" + (mt or "image/png") + ";base64," + base64.b64encode(data).decode() + m.group(3)
    html = re.sub(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', img, html, flags=re.I)

    def css(m):
        tag = m.group(0)
        if not re.search(r'rel=["\']?stylesheet', tag, re.I):
            return tag
        h = re.search(r'href="([^"]+)"', tag)
        r = get(h.group(1)) if h else None
        if not r:
            return tag
        return "<style>" + r[0].decode("utf-8", "replace") + "</style>"
    html = re.sub(r"<link\b[^>]*>", css, html, flags=re.I)
    return html


def page_count(pdf):
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(pdf)).pages)


def render_png(pdf, i, width=620):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    try:
        pg = doc[i]
        scale = width / pg.get_width()
        img = pg.render(scale=scale).to_pil().convert("RGB")
        b = io.BytesIO()
        img.save(b, "PNG", optimize=True)
        return b.getvalue()
    finally:
        doc.close()


def printers():
    """-> (рўйхат, стандарт принтер). Windows бўлмаса — бўш."""
    try:
        import win32print
    except ImportError:
        return [], ""
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    names = [p[2] for p in win32print.EnumPrinters(flags)]
    try:
        d = win32print.GetDefaultPrinter()
    except Exception:
        d = names[0] if names else ""
    return names, d


def parse_pages(s, n):
    s = (s or "").strip()
    if not s:
        return list(range(n))
    out = []
    for part in re.split(r"[,\s;]+", s):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        for k in range(min(a, b), max(a, b) + 1):
            if 1 <= k <= n and k - 1 not in out:
                out.append(k - 1)
    return out


def print_pdf(pdf, printer, copies=1, pages=None, title="Lumberture"):
    """PDF'ни Windows принтерига юборади: ҳар бир саҳифа принтер ўлчамида расмга айлантирилиб, қоғозга сиғдирилади."""
    import pypdfium2 as pdfium
    import win32print, win32ui, win32con
    from PIL import ImageWin
    doc = pdfium.PdfDocument(pdf)
    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(printer)
    try:
        pw, ph = hdc.GetDeviceCaps(win32con.HORZRES), hdc.GetDeviceCaps(win32con.VERTRES)
        dpi = min(hdc.GetDeviceCaps(win32con.LOGPIXELSX), 300)
        idx = pages if pages is not None else list(range(len(doc)))
        hdc.StartDoc(title[:60])
        for _ in range(max(1, min(int(copies or 1), 50))):
            for i in idx:
                pg = doc[i]
                img = pg.render(scale=dpi / 72).to_pil().convert("RGB")
                s = min(pw / img.width, ph / img.height)
                w, h = int(img.width * s), int(img.height * s)
                x, y = (pw - w) // 2, 0
                hdc.StartPage()
                ImageWin.Dib(img).draw(hdc.GetHandleOutput(), (x, y, x + w, y + h))
                hdc.EndPage()
        hdc.EndDoc()
    finally:
        hdc.DeleteDC()
        doc.close()


def register(app_module):
    global A
    A = app_module
    app = A.app
    from flask import request, jsonify, send_file, abort, session

    def _job(jid):
        if not re.fullmatch(r"[0-9a-f]{24}", jid or ""):
            abort(404)
        p = os.path.join(jobs_dir(), jid + ".pdf")
        if not os.path.isfile(p):
            abort(404)
        meta = {}
        try:
            meta = json.load(open(p + ".json", encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if meta.get("uid") != A.current_user()["id"]:
            abort(404)
        return p, meta

    def client():
        c = app.test_client()
        with c.session_transaction() as s:                    # ўша фойдаланувчи ҳуқуқи билан (сессия нусхаси)
            s.update(dict(session))
        return c

    def fetcher():
        c = client()

        def f(path):
            r = c.get(path, environ_base={"REMOTE_ADDR": request.remote_addr or "127.0.0.1"})
            return (r.data, r.mimetype) if r.status_code == 200 else None
        return f

    @app.route("/api/print/printers")
    @A.login_required
    def print_printers():
        names, d = printers()
        return jsonify(printers=names, default=d, can=bool(names))

    @app.route("/api/print/prepare", methods=["POST"])
    @A.login_required
    def print_prepare():
        _cleanup()
        data = request.get_json(silent=True) or {}
        url, html = (data.get("url") or "").strip(), data.get("html") or ""
        title = (data.get("title") or "Lumberture")[:120]
        try:
            if url:
                if not url.startswith("/") or url.startswith("//") or url.startswith("/api/"):
                    return jsonify(error="bad url"), 400
                c = client()
                r = c.get(url, environ_base={"REMOTE_ADDR": request.remote_addr or "127.0.0.1"})
                if r.status_code != 200:
                    return jsonify(error=f"HTTP {r.status_code}"), 400
                if (r.mimetype or "") == "application/pdf":
                    pdf = r.data
                else:
                    bp = "/" + url.lstrip("/").split("?")[0].rsplit("/", 1)[0] + "/"
                    pdf = html_to_pdf(inline_assets(r.get_data(as_text=True), bp, fetcher()), request.host_url.rstrip("/") + bp)
            elif html:
                if len(html) > 8 * 1024 * 1024:
                    return jsonify(error="too big"), 400
                base = (data.get("base") or "/")
                base = base if base.startswith("/") else "/"
                pdf = html_to_pdf(inline_assets(html, base, fetcher()), request.host_url.rstrip("/") + base)
            else:
                return jsonify(error="nothing to print"), 400
            n = page_count(pdf)
        except Exception as e:
            A.errlog.exception("print prepare")
            return jsonify(error=str(e)[:300]), 500
        jid = secrets.token_hex(12)
        p = os.path.join(jobs_dir(), jid + ".pdf")
        with open(p, "wb") as f:
            f.write(pdf)
        json.dump({"uid": A.current_user()["id"], "title": title, "pages": n}, open(p + ".json", "w", encoding="utf-8"))
        names, d = printers()
        return jsonify(id=jid, pages=n, printers=names, default=d, can_print=bool(names))

    @app.route("/api/print/<jid>/p/<int:n>.png")
    @A.login_required
    def print_preview(jid, n):
        p, meta = _job(jid)
        pdf = open(p, "rb").read()
        if n < 1 or n > meta.get("pages", 1):
            abort(404)
        return send_file(io.BytesIO(render_png(pdf, n - 1)), mimetype="image/png", max_age=600)

    @app.route("/api/print/<jid>.pdf")
    @A.login_required
    def print_pdf_file(jid):
        p, meta = _job(jid)
        nm = re.sub(r"[^\w\-. ]+", "_", meta.get("title") or "document")[:60] + ".pdf"
        return send_file(p, mimetype="application/pdf", as_attachment=request.args.get("dl") == "1", download_name=nm)

    @app.route("/api/print/send", methods=["POST"])
    @A.login_required
    def print_send():
        data = request.get_json(silent=True) or {}
        p, meta = _job(data.get("id"))
        names, d = printers()
        pr = data.get("printer") or d
        if pr not in names:
            return jsonify(error="printer"), 400
        pdf = open(p, "rb").read()
        idx = parse_pages(data.get("pages"), meta.get("pages", 1))
        if not idx:
            return jsonify(error="pages"), 400
        try:
            print_pdf(pdf, pr, int(A.parse_float(data.get("copies"), 1)), idx, meta.get("title") or "Lumberture")
        except Exception as e:
            A.errlog.exception("print send")
            return jsonify(error=str(e)[:300]), 500
        A.set_settings(print_last_printer=pr)
        return jsonify(ok=True, sheets=len(idx) * max(1, int(A.parse_float(data.get("copies"), 1))))
