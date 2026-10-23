"""Фирменный бланк Lumberture (PDF): шапка, пастки қатор ва саҳифа рақами.

Уч хил ҳужжат шу бланкда чиқади:
  render_text(...)   — платформада ёзилган матн (договор, акт, КП, хат): сарлавҳа, рақам/сана, «Кому», матн, имзо жойи;
  render_pdf(...)    — юкланган PDF (ёки Word'дан ўгирилган PDF): ҳар бир саҳифа бланкнинг бўш жойига сиғдирилади;
  render_image(...)  — юкланган расм (скан, фото).
Кутубхоналар: reportlab (чизиш), pypdf (юкланган PDF'ни бланк устига қўйиш)."""
import io, os, re
from datetime import date

GREEN = (0x3E / 255, 0x64 / 255, 0x3C / 255)
CREAM = (0xF0 / 255, 0xE4 / 255, 0xD5 / 255)
INK = (0.12, 0.16, 0.15)
MUTED = (0.42, 0.47, 0.46)
W, H = 595.28, 841.89            # A4, pt
ML, MR = 48, 48                  # чап/ўнг чегара
TOP = H - 112                    # матн шу ердан бошланади (шапка остида)
BOTTOM = 70                      # матн шу ергача (пастки қатор устида)

DEFAULT_CFG = dict(name="Lumberture", tagline="Полный спектр мебельных услуг",
                   phone="+998 33 409-60-70", phone2="",
                   site="lumberture.com", tg="t.me/Lumberture", address="г. Ташкент, ул. Жомий, 5",
                   legal="ООО «LUMBERTURE»", requisites="", signer_title="Директор", signer_name="", city="г. Ташкент",
                   signer_name_cyr="", signer_name_ru="")

# Директор Ф.И.О. ҳужжат тилида (эгаси айтди): signer_name — лотин, signer_name_cyr — ўзбек кирилл, signer_name_ru — рус.
_UZ_ONLY = re.compile("[ЎўҚқҒғҲҳ]")
_LAT2CYR = [("o'", "ў"), ("g'", "ғ"), ("sh", "ш"), ("ch", "ч"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("ye", "е"), ("ts", "ц")]
_LAT1 = dict(zip("abdefghijklmnopqrstuvxyz", "абдефгҳижклмнопқрстувхйз"))


def lat2cyr(s):
    """Ўзбек лотин -> кирилл (исм-фамилия учун етарли)."""
    out, i, s = [], 0, (s or "").replace("ʻ", "'").replace("’", "'").replace("`", "'").replace("ʼ", "'")
    while i < len(s):
        if s[i:i + 3].lower() == "yo'":                # Yo'ldosh -> Йўлдош
            out.append("Йў" if s[i].isupper() else "йў"); i += 3; continue
        two = s[i:i + 2]
        hit = next((c for l, c in _LAT2CYR if two.lower() == l), None)
        if hit:
            out.append(hit.upper() if two[0].isupper() else hit); i += 2; continue
        ch = s[i]
        if ch == "'":
            out.append("ъ" if out and out[-1].isalpha() and i + 1 < len(s) and s[i + 1].isalpha() else ""); i += 1; continue
        c = _LAT1.get(ch.lower())
        if c and ch.lower() == "e" and (i == 0 or not s[i - 1].isalpha()):
            c = "э"                                      # сўз бошида e -> э
        out.append((c.upper() if ch.isupper() else c) if c else ch)
        i += 1
    return "".join(out)


def doc_lang(text):
    """Ҳужжат тили матндан: 'lat' (ўзбек лотин), 'uzc' (ўзбек кирилл), 'ru'."""
    t = text or ""
    cy = len(re.findall("[А-Яа-яЁёЎўҚқҒғҲҳ]", t))
    la = len(re.findall("[A-Za-z]", t))
    if la > cy:
        return "lat"
    if _UZ_ONLY.search(t) or re.search(r"\b(ва|билан|учун|бўйича|шартнома|тўлов)\b", t, re.I):
        return "uzc"
    return "ru"


MP = {"lat": ("M.O'.", "imzo · M.O'."), "uzc": ("М.Ў.", "имзо · М.Ў."), "ru": ("М.П.", "подпись · М.П.")}


def signer(cfg, text):
    """(лавозим, Ф.И.О.) ҳужжат тилида."""
    lg = doc_lang(text)
    lat = (cfg.get("signer_name") or "").strip()
    cyr = (cfg.get("signer_name_cyr") or "").strip() or (lat2cyr(lat) if lat and not re.search("[А-Яа-я]", lat) else lat)
    ru = (cfg.get("signer_name_ru") or "").strip() or cyr
    title = (cfg.get("signer_title") or "Директор").strip()
    if lg == "lat":
        return ("Direktor" if title.casefold() == "директор" else title), lat or cyr
    return title, (cyr if lg == "uzc" else ru) or lat

_font = {}


BRAND_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fonts")


def _brand_font():
    """data/fonts/Montserrat-Regular.ttf (+ -Bold / -SemiBold) — бланк шрифти, ўзбек ҳарфлари бўлса."""
    r = os.path.join(BRAND_FONT_DIR, "Montserrat-Regular.ttf")
    if not os.path.isfile(r):
        return None
    b = next((os.path.join(BRAND_FONT_DIR, n) for n in ("Montserrat-Bold.ttf", "Montserrat-SemiBold.ttf")
              if os.path.isfile(os.path.join(BRAND_FONT_DIR, n))), r)
    try:
        import pdfgen
        if not pdfgen.TTF(r).has(pdfgen.NEED):
            return None
    except Exception:
        return None
    return r, b


def fonts():
    """(regular, bold) — reportlab'да рўйхатдан ўтган TTF номлари (кирилл ва ўзбек ҳарфлари бор шрифт).
    data/fonts ичида Montserrat бўлса — шу (фирма шрифти), бўлмаса — Windows шрифтлари (Arial ...)."""
    bf = _brand_font()
    if _font and (_font.get("brand") or not bf):
        return _font["r"], _font["b"]
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import pdfgen
    if bf:
        try:
            pdfmetrics.registerFont(TTFont("LBMR", bf[0]))
            pdfmetrics.registerFont(TTFont("LBMB", bf[1]))
            _font.clear(); _font.update(r="LBMR", b="LBMB", brand=True)
            return _font["r"], _font["b"]
        except Exception:
            pass
    if _font:
        return _font["r"], _font["b"]
    for r, b in pdfgen.FONT_CANDIDATES:
        if os.path.exists(r):
            try:
                pdfmetrics.registerFont(TTFont("LBR", r))
                pdfmetrics.registerFont(TTFont("LBB", b if os.path.exists(b) else r))
                _font.update(r="LBR", b="LBB")
                break
            except Exception:
                continue
    if not _font:
        _font.update(r="Helvetica", b="Helvetica-Bold")
    return _font["r"], _font["b"]


def _logo_reader(logo_path):
    if not logo_path or not os.path.isfile(logo_path):
        return None
    try:
        from reportlab.lib.utils import ImageReader
        return ImageReader(logo_path)
    except Exception:
        return None


def _tag_lines(text, n=3):
    """Шиорни n қаторга: сўзлар бўйича, энг узун қатор энг қисқа бўлсин («Полный спектр / мебельных / услуг»)."""
    ws = (text or "").split()
    if len(ws) <= n:
        return ws
    best = None

    def rec(i, k, cur):
        nonlocal best
        if k == 1:
            parts = cur + [" ".join(ws[i:])]
            m = max(len(x) for x in parts)
            if best is None or m < best[0]:
                best = (m, parts)
            return
        for j in range(i + 1, len(ws) - k + 2):
            rec(j, k - 1, cur + [" ".join(ws[i:j])])
    rec(0, n, [])
    return best[1]


def _tagline_3(c, text, x, y_bottom, h, font):
    """Шиор логотип баландлигида, 3 қатор, яшил, бош ҳарфлар билан (эгаси айтди)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines = [ln.upper() for ln in _tag_lines(text, 3)]
    lead = h / len(lines)
    size = lead * 0.80
    maxw = W - MR - 230 - x                      # ўнгдаги реквизитларга тегмасин
    widest = max(stringWidth(ln, font, size) for ln in lines)
    if widest > maxw > 40:
        size *= maxw / widest
    c.setFillColorRGB(*GREEN)
    c.setFont(font, size)
    for i, ln in enumerate(lines):
        base = y_bottom + h - lead * (i + 1) + (lead - size * 0.72) / 2
        c.drawString(x, base, ln, charSpace=size * 0.06)


def draw_blank(c, cfg, page, total, logo_path=None):
    """Битта саҳифага бланкни чизади (reportlab canvas)."""
    R, B = fonts()
    cfg = dict(DEFAULT_CFG, **{k: v for k, v in (cfg or {}).items() if v is not None})
    c.saveState()
    # юқориги ингичка яшил чизиқ (саҳифа чети бўйлаб)
    c.setFillColorRGB(*GREEN)
    c.rect(0, H - 6, W, 6, stroke=0, fill=1)
    # логотип ёки фирма номи
    lg = _logo_reader(logo_path)
    y0 = H - 58
    if lg:
        iw, ih = lg.getSize()
        tall = iw / ih < 1.3                      # тик логотип (белги + ном остида) — баландроқ
        h = 64.0 if tall else 54.0
        w = iw * h / ih
        if w > 210:
            w, h = 210.0, ih * 210.0 / iw
        c.drawImage(lg, ML, H - 18 - h, width=w, height=h, mask="auto")
        if tall and cfg.get("tagline"):
            _tagline_3(c, cfg["tagline"], ML + w + 12, H - 18 - h, h, B)
    else:
        c.setFillColorRGB(*GREEN)
        c.setFont(B, 22)
        name = (cfg["name"] or "Lumberture").upper()
        c.drawString(ML, y0 + 8, name, charSpace=2.2)
        c.setFillColorRGB(*MUTED)
        c.setFont(R, 8.5)
        c.drawString(ML, y0 - 5, cfg["tagline"] or "")
    # ўнгда — фирма ва реквизитлар (ўқилишига кўра йирикроқ)
    x = W - MR
    req = [s.strip() for s in re.split(r"[\n;]+", cfg["requisites"] or "") if s.strip()]
    c.setFont(B, 11.5)
    c.setFillColorRGB(*INK)
    c.drawRightString(x, H - 32, cfg["legal"] or cfg["name"] or "")
    c.setFont(R, 9.2)
    yy = H - 46
    for ln in req[:4]:
        c.drawRightString(x, yy, ln[:70])
        yy -= 11.5
    # шапка остидаги чизиқ
    c.setStrokeColorRGB(*GREEN)
    c.setLineWidth(1.1)
    c.line(ML, H - 92, W - MR, H - 92)
    # пастки қатор: крем фон, реквизитлар, саҳифа рақами
    c.setFillColorRGB(*CREAM)
    c.rect(0, 0, W, 50, stroke=0, fill=1)
    c.setFillColorRGB(*GREEN)
    c.rect(0, 50, W, 1.2, stroke=0, fill=1)
    # пастда — алоқа
    c.setFont(B, 9.5)
    c.setFillColorRGB(*INK)
    c.drawString(ML, 31, "   ·   ".join(v for v in (cfg["phone"], cfg["phone2"]) if v))
    c.setFont(R, 8.6)
    c.setFillColorRGB(*MUTED)
    c.drawString(ML, 17, "   ·   ".join(v for v in (cfg["address"], cfg["site"], cfg["tg"]) if v)[:150])
    if total > 1:
        c.setFont(R, 7.5)
        c.drawRightString(W - MR, 22, f"{page} / {total}")
    c.restoreState()


def _blank_pages(n, cfg, logo):
    """n та бўш бланк саҳифаси (PDF байтлари)."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    for i in range(n):
        draw_blank(c, cfg, i + 1, n, logo)
        c.showPage()
    c.save()
    return buf.getvalue()


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(s):
    """**қалин** ва _курсив_ белгилари."""
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", s)
    return s


def render_text(title, body, no="", ddate=None, to="", cfg=None, logo=None, sign=True):
    """Платформада ёзилган ҳужжат -> PDF байтлари. body: оддий матн; «# » — бўлим сарлавҳаси, «- » — рўйхат,
    «|» билан ажратилган қаторлар — жадвал, бўш қатор — янги абзац."""
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_RIGHT
    from reportlab.lib import colors
    R, B = fonts()
    cfg = dict(DEFAULT_CFG, **(cfg or {}))
    from reportlab.pdfbase import pdfmetrics
    try:
        pdfmetrics.registerFontFamily(R, normal=R, bold=B, italic=R, boldItalic=B)
    except Exception:
        pass
    st = dict(
        p=ParagraphStyle("p", fontName=R, fontSize=10.5, leading=15, alignment=TA_JUSTIFY, textColor=colors.Color(*INK), spaceAfter=6),
        h=ParagraphStyle("h", fontName=B, fontSize=11, leading=15, textColor=colors.Color(*INK), spaceBefore=8, spaceAfter=4),
        t=ParagraphStyle("t", fontName=B, fontSize=15, leading=20, alignment=TA_CENTER, textColor=colors.Color(*INK), spaceAfter=4),
        meta=ParagraphStyle("m", fontName=R, fontSize=9.5, leading=13, textColor=colors.Color(*MUTED)),
        metar=ParagraphStyle("mr", fontName=R, fontSize=9.5, leading=13, alignment=TA_RIGHT, textColor=colors.Color(*INK)),
        li=ParagraphStyle("li", fontName=R, fontSize=10.5, leading=15, leftIndent=14, bulletIndent=2, textColor=colors.Color(*INK), spaceAfter=2),
        td=ParagraphStyle("td", fontName=R, fontSize=9.5, leading=12, textColor=colors.Color(*INK)),
        th=ParagraphStyle("th", fontName=B, fontSize=9.5, leading=12, textColor=colors.white))
    ddate = ddate or date.today()
    story = []
    left = Paragraph(_esc(cfg.get("city") or ""), st["meta"])
    right = Paragraph((f"№ <b>{_esc(no)}</b><br/>" if no else "") + f"от {ddate:%d.%m.%Y}", st["metar"])
    tb = Table([[left, right]], colWidths=[(W - ML - MR) / 2] * 2)
    tb.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [tb, Spacer(1, 8)]
    if to.strip():
        tbl = Table([["", Paragraph("<br/>".join(_inline(x) for x in to.strip().splitlines()), st["metar"])]],
                    colWidths=[(W - ML - MR) * 0.45, (W - ML - MR) * 0.55])
        tbl.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        story += [tbl, Spacer(1, 10)]
    if title.strip():
        story += [Paragraph(_inline(title.strip()), st["t"]), Spacer(1, 8)]
    rows = []

    def flush_table():
        if not rows:
            return
        n = max(len(r) for r in rows)
        data = [[Paragraph(_inline(c), st["th"] if i == 0 else st["td"]) for c in r + [""] * (n - len(r))] for i, r in enumerate(rows)]
        cw = W - ML - MR
        widths = [cw / n] * n if n < 3 else [cw * 0.46] + [cw * 0.54 / (n - 1)] * (n - 1)
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.Color(*GREEN)),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.Color(*CREAM, alpha=0.45)]),
                               ("GRID", (0, 0), (-1, -1), 0.4, colors.Color(0.82, 0.84, 0.83)),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story.extend([t, Spacer(1, 8)])
        rows.clear()

    para = []

    def flush_para():
        if para:
            story.append(Paragraph(_inline(" ".join(para)), st["p"]))
            para.clear()

    for raw in (body or "").replace("\r", "").split("\n"):
        ln = raw.strip()
        if "|" in ln and ln.count("|") >= 1:
            flush_para()
            cells = [x.strip() for x in ln.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", x) for x in cells if x):
                continue
            rows.append(cells)
            continue
        flush_table()
        if not ln:
            flush_para()
        elif ln.startswith("# "):
            flush_para()
            story.append(Paragraph(_inline(ln[2:]), st["h"]))
        elif re.match(r"^[-•*]\s+", ln):
            flush_para()
            story.append(Paragraph(_inline(re.sub(r"^[-•*]\s+", "", ln)), st["li"], bulletText="•"))
        elif re.match(r"^\d+[.)]\s+", ln):
            flush_para()
            m = re.match(r"^(\d+[.)])\s+(.*)", ln)
            story.append(Paragraph(_inline(m.group(2)), st["li"], bulletText=m.group(1)))
        else:
            para.append(ln)
    flush_para()
    flush_table()
    if sign:
        s_title, s_name = signer(cfg, f"{title}\n{body}")
        mp = MP[doc_lang(f"{title}\n{body}")][0]
        sg = Table([[Paragraph(_esc(s_title), st["p"]), Paragraph("_______________________", st["p"]),
                     Paragraph(_esc(s_name), ParagraphStyle("sn", parent=st["p"], alignment=TA_RIGHT))],
                    ["", Paragraph(_esc(mp), st["meta"]), ""]],
                   colWidths=[(W - ML - MR) * 0.22, (W - ML - MR) * 0.33, (W - ML - MR) * 0.45])
        sg.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
        story += [Spacer(1, 26), KeepTogether([sg])]

    def build(total):
        buf = io.BytesIO()
        state = {"n": 0}

        def on_page(c, d):
            state["n"] += 1
            draw_blank(c, cfg, state["n"], total, logo)
        doc = BaseDocTemplate(buf, pagesize=(W, H), leftMargin=ML, rightMargin=MR, topMargin=H - TOP, bottomMargin=BOTTOM,
                              title=title or no, author=cfg.get("name") or "Lumberture")
        doc.addPageTemplates([PageTemplate(id="b", frames=[Frame(ML, BOTTOM, W - ML - MR, TOP - BOTTOM, id="f", leftPadding=0,
                                                                  rightPadding=0, topPadding=0, bottomPadding=0)], onPage=on_page)])
        import copy
        doc.build(copy.deepcopy(story))
        return buf.getvalue(), state["n"]
    out, n = build(1)
    if n > 1:
        out, _ = build(n)
    return out


def fit_docx(data):
    """Word (.docx): саҳифани A4 қилиб, чегараларни бланкнинг бўш жойига мослайди — матн кичраймайди, асл ўлчамда
    бланк шапкаси ва пастки қатори орасига жойлашади (Word ўзи қайта тахлайди). -> янги .docx байтлари."""
    import zipfile
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    zout = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED)
    tw = lambda pt: str(int(round(pt * 20)))
    for it in zin.infolist():
        b = zin.read(it.filename)
        if it.filename == "word/document.xml":
            x = b.decode("utf-8")
            x = re.sub(r"<w:pgSz\b[^>]*/>", lambda m: '<w:pgSz w:w="11906" w:h="16838"/>' if "w:orient" not in m.group(0)
                       else m.group(0), x)
            mar = (f'<w:pgMar w:top="{tw(H - TOP + 6)}" w:right="{tw(MR)}" w:bottom="{tw(BOTTOM + 10)}" w:left="{tw(ML)}" '
                   f'w:header="{tw(H - TOP - 20)}" w:footer="{tw(BOTTOM)}" w:gutter="0"/>')
            x = re.sub(r"<w:pgMar\b[^>]*/>", mar, x)
            b = x.encode("utf-8")
        zout.writestr(it, b)
    zout.close()
    return out.getvalue()


def render_pdf(src_bytes, cfg=None, logo=None, exact=False):
    """Юкланган PDF -> ҳар бир саҳифаси бланкнинг матн жойига сиғдирилган PDF.
    exact=True (Word'дан fit_docx билан тайёрланган A4): саҳифа кичрайтирилмайди, ўз жойида қўйилади."""
    from pypdf import PdfReader, PdfWriter, Transformation
    src = PdfReader(io.BytesIO(src_bytes))
    n = len(src.pages)
    if not n:
        raise ValueError("PDF бўш")
    blank = PdfReader(io.BytesIO(_blank_pages(n, cfg, logo)))
    out = PdfWriter()
    bw, bh = W - ML - MR + 16, TOP - BOTTOM + 14       # PDF саҳифасининг ўз чегараси бор — бироз кенгроқ жой
    bx, by = ML - 8, BOTTOM - 4
    for i, sp in enumerate(src.pages):
        page = blank.pages[i]
        box = sp.cropbox
        sw, sh = float(box.width), float(box.height)
        rot = (sp.get("/Rotate") or 0) % 360
        if rot in (90, 270):
            sp.transfer_rotation_to_content()
            box = sp.cropbox
            sw, sh = float(box.width), float(box.height)
        if exact and abs(sw - W) < 6 and abs(sh - H) < 6:
            page.merge_transformed_page(sp, Transformation().translate(-float(box.left), -float(box.bottom)))
            out.add_page(page)
            continue
        s = min(bw / sw, bh / sh)
        tx = bx + (bw - sw * s) / 2 - float(box.left) * s
        ty = by + (bh - sh * s) - float(box.bottom) * s           # юқорига ёпиштирилади
        page.merge_transformed_page(sp, Transformation().scale(s, s).translate(tx, ty))
        out.add_page(page)
    buf = io.BytesIO()
    out.write(buf)
    return buf.getvalue()


def render_image(img_bytes, cfg=None, logo=None):
    """Юкланган расм (JPG/PNG) -> бланкда битта саҳифа."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    im = ImageReader(io.BytesIO(img_bytes))
    iw, ih = im.getSize()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    draw_blank(c, cfg, 1, 1, logo)
    bw, bh = W - ML - MR, TOP - BOTTOM
    s = min(bw / iw, bh / ih)
    c.drawImage(im, ML + (bw - iw * s) / 2, BOTTOM + (bh - ih * s), width=iw * s, height=ih * s, mask="auto")
    c.showPage()
    c.save()
    return buf.getvalue()


SIGN_EVERY_PAGE = ("contract", "act")         # шартнома, акт — ҳар бир саҳифа остида имзо жойи


def _lowest_text(pdf, i):
    """Саҳифадаги энг пастки матн (шапка ва пастки қатордан ташқари) -> y (pt), матн бўлмаса None."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    try:
        tp = doc[i].get_textpage()
        low = None
        for k in range(tp.count_chars()):
            l, b, r, t = tp.get_charbox(k)
            if b > 52 and t < H - 95 and r > l:
                low = b if low is None else min(low, b)
        return low
    finally:
        doc.close()


def _pdf_text(pdf, limit=4000):
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(pdf)
        try:
            out = ""
            for i in range(min(len(doc), 3)):
                out += doc[i].get_textpage().get_text_range() + "\n"
                if len(out) > limit:
                    break
            return out
        finally:
            doc.close()
    except Exception:
        return ""


def _sign_block(c, cfg, y, R, B):
    """«Директор  ________  Ф.И.О.» + М.П. — y: қатор асоси. cfg['_sig'] — (лавозим, Ф.И.О.) ҳужжат тилида."""
    s_title, s_name = cfg.get("_sig") or (cfg.get("signer_title") or "Директор", cfg.get("signer_name") or "")
    c.setFillColorRGB(*INK)
    c.setFont(B, 10.5)
    c.drawString(ML, y, s_title)
    c.setStrokeColorRGB(*INK)
    c.setLineWidth(0.6)
    c.line(ML + 150, y - 2, ML + 290, y - 2)
    c.setFont(R, 10.5)
    c.drawRightString(W - MR, y, s_name)
    c.setFont(R, 7.5)
    c.setFillColorRGB(*MUTED)
    c.drawCentredString(ML + 220, y - 12, cfg.get("_mp") or "подпись · М.П.")


def add_signature(pdf, kind, cfg=None, logo=None):
    """Юкланган ҳужжатга директор Ф.И.О.ни ҳужжат турига кўра қўяди:
    шартнома/акт — ҳар бир саҳифа остида (кичик); хат, КП, счёт ва бошқалар — охирги саҳифада матн тагида (йўқ жой бўлса — янги варақ)."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    cfg = dict(DEFAULT_CFG, **{k: v for k, v in (cfg or {}).items() if v is not None})
    if not (cfg.get("signer_name") or cfg.get("signer_name_cyr") or cfg.get("signer_name_ru") or "").strip():
        return pdf
    _tx = _pdf_text(pdf)
    cfg["_sig"] = signer(cfg, _tx)
    cfg["_mp"] = MP[doc_lang(_tx)][1]
    R, B = fonts()
    rd = PdfReader(io.BytesIO(pdf))
    n = len(rd.pages)
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=(W, H))
    extra = False
    for i in range(n):
        if kind in SIGN_EVERY_PAGE:
            cv.setFont(R, 8.5)
            cv.setFillColorRGB(*INK)
            cv.drawRightString(W - MR, 57, f"{cfg['_sig'][0]}  ____________  {cfg['_sig'][1]}")
        elif i == n - 1:
            low = _lowest_text(pdf, i)
            y = (low if low is not None else TOP) - 40
            if y < BOTTOM:
                extra = True
            else:
                _sign_block(cv, cfg, y, R, B)
        cv.showPage()
    if extra:                                  # охирги варақда жой йўқ — имзо янги бланк варағида
        draw_blank(cv, cfg, n + 1, n + 1, logo)
        _sign_block(cv, cfg, TOP - 30, R, B)
        cv.showPage()
    cv.save()
    ov = PdfReader(io.BytesIO(buf.getvalue()))
    out = PdfWriter()
    for i, pg in enumerate(rd.pages):
        pg.merge_page(ov.pages[i])
        out.add_page(pg)
    if extra:
        out.add_page(ov.pages[n])
    b = io.BytesIO()
    out.write(b)
    return b.getvalue()
