"""Накладная расм кўринишида (1С'даги босма шакл каби) — мижозга Telegram'да расм қилиб юбориш учун.

layout() — чизиш буйруқлари (тўртбурчак/матн/чизиқ) ва ўлчами; шрифт ўлчамлари pdfgen'даги TTF'дан (Arial),
шунинг учун Windows'да (System.Drawing, PowerShell) ва тестда (Pillow) бир хил чиқади.
"""
import json
import os
import subprocess
import tempfile

import pdfgen

MONTHS_GEN = ["", "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября",
              "Ноября", "Декабря"]
SCALE = 2          # расм аниқ бўлиши учун ҳамма ўлчам ×2
W = 900            # мантиқий кенглик (px)


def _num(v, dec=2):
    s = f"{abs(v):,.{dec}f}"
    return ("-" if v < 0 else "") + s


def _qty(v):
    if isinstance(v, (int, float)):
        return f"{v:g}" if abs(v - round(v)) > 1e-9 else str(int(round(v)))
    return str(v)


class _M:
    """Матн ўлчаш (Arial метрикаси)."""
    def __init__(self):
        self.r, self.b, self.fb = pdfgen.fonts()

    def tw(self, s, size, bold=False):
        if self.fb:
            s = s.translate(pdfgen.FALLBACK)
        return (self.b if bold else self.r).width(s, size)

    def fit(self, s, width, size, bold=False):
        """Сиғмаса — кичрайтирамиз (энг ками 70%), барибир сиғмаса — қисқартирамиз."""
        sz = size
        while self.tw(s, sz, bold) > width and sz > size * 0.7:
            sz -= 0.25
        while self.tw(s, sz, bold) > width and len(s) > 3:
            s = s[:-2] + "…"
        return s, sz


T = {
    "ru": dict(title="Накладная", frm="от", buyer="Покупатель", cols=["№", "Наименование", "Ед.", "Кол-во", "Цена, сум", "Сумма, сум", "Сумма, $"],
               total="ИТОГО", note="Примечание", prev="Долг до накладной", this="По этой накладной", after="Долг после накладной",
               adv="На вашем счёте:", nodebt="Долга нет", thanks="Спасибо за заказ!", tel="Тел.", rate="Курс", rnote="Примечание", som="сум/$"),
    "uz": dict(title="Nakladnoy", frm="sana:", buyer="Mijoz", cols=["№", "Nomi", "O'lch.", "Soni", "Narxi, so'm", "Summa, so'm", "Summa, $"],
               total="JAMI", note="Izoh", prev="Nakladnoygacha qarz", this="Shu nakladnoy bo'yicha", after="Nakladnoydan keyingi qarz",
               adv="Hisobingizda:", nodebt="Qarz yo'q", thanks="Buyurtmangiz uchun rahmat!", tel="Tel.", rate="Kurs", rnote="Izoh", som="so'm/$"),
}
GREEN, CREAM, INK, MUT, LINE, NEG = "#3E643C", "#f0e4d5", "#1f2a27", "#6b7b76", "#cfd8d5", "#b3261e"


def layout(doc, prev=None, after=None, lang="ru", company="Lumberture", phone="", address="", logo=None,
           client_phone="", rate=None, rate_date="", style="green"):
    """doc = c1.sale_doc(); prev/after — қарз ($) накладнойдан олдин/кейин. -> (w, h, ops)"""
    w_ = T.get(lang, T["ru"])
    m = _M()
    ops = []
    x0 = 18
    j, cl = doc["doc"], doc["client"]
    d = j["date"]
    light = style == "light"
    HB, HT, HS = (CREAM, GREEN, MUT) if light else (GREEN, CREAM, CREAM)
    # --- шапка
    hb = 70
    ops.append(["rect", 0, 0, W, hb, HB, None, 0])
    if light:
        ops.append(["rect", 0, hb - 4, W, 4, GREEN, None, 0])
    tx = x0
    if logo and logo.get("path"):
        lh = 54
        lw = lh * logo["w"] / logo["h"]
        ops.append(["img", logo["path"], x0, (hb - lh) / 2, lw, lh])
        tx = x0 + lw + 16
    ops.append(["text", tx, 14, company, 20, True, HT, "l"])
    ops.append(["text", tx, 42, "  ·  ".join(p for p in (phone, address) if p), 10.5, False, HS, "l"])
    no = str(j["no"]).strip()
    dd = f"{d:%d.%m.%Y}"
    ops.append(["text", W - x0, 14, f"{w_['title']} №{no}", 18, True, HT, "r"])
    ops.append(["text", W - x0, 42, (w_["frm"] + " " + dd).strip(), 11, False, HS, "l" if False else "r"])
    y = hb + 14
    # мижоз, телефон, курс
    buyer = (cl.get("name") or "").strip()
    ops.append(["text", x0, y + 1, f"{w_['buyer']}:", 10.5, False, MUT, "l"])
    bx = x0 + m.tw(w_["buyer"] + ": ", 10.5) + 2
    ops.append(["text", bx, y - 1, buyer, 13, True, INK, "l"])
    if client_phone:
        ops.append(["text", bx + m.tw(buyer, 13, True) + 14, y + 1, f"{w_['tel']} {client_phone}", 11, False, INK, "l"])
    if rate:
        rt = f"{w_['rate']}: {_num(rate, 0).replace(',', ' ')} {w_['som']}" + (f" ({rate_date})" if rate_date else "")
        ops.append(["text", W - x0, y + 1, rt, 11, True, GREEN, "r"])
    y += 26
    # Примечание1 — тепада
    n1 = (doc.get("note1") or "").strip()
    if n1:
        lbl = w_["note"] + ": "
        lw_ = m.tw(lbl, 10.5, True)
        lines = _wrap(m, n1, W - 2 * x0 - 24 - lw_, 10.5)[:4]
        bh = 8 + 15 * len(lines)
        ops.append(["rect", x0, y, W - 2 * x0, bh, "#fbf7f0", None, 0])
        ops.append(["rect", x0, y, 3, bh, GREEN, None, 0])
        ops.append(["text", x0 + 10, y + 4, lbl, 10.5, True, GREEN, "l"])
        for q, part in enumerate(lines):
            ops.append(["text", x0 + 10 + lw_, y + 4 + q * 15, part, 10.5, True, INK, "l"])
        y += bh + 8
    else:
        y += 2
    # --- жадвал
    names = w_["cols"]
    widths = [30, 0, 46, 62, 88, 100, 80]
    al = ["c", "l", "c", "r", "r", "r", "r"]
    widths[1] = W - 2 * x0 - sum(widths)
    xs = [x0]
    for wd in widths:
        xs.append(xs[-1] + wd)
    hh, fs = 26, 10.5
    if light:
        ops.append(["line", x0, y + hh, W - x0, y + hh, GREEN, 1.5])
    else:
        ops.append(["rect", x0, y, W - 2 * x0, hh, GREEN, None, 0])
    for i, nm in enumerate(names):
        s, sz = m.fit(nm, widths[i] - 6, 9.5, True)
        tx = xs[i] + 4 if al[i] == "l" else (xs[i] + widths[i] / 2 if al[i] == "c" else xs[i + 1] - 5)
        ops.append(["text", tx, y + (hh - sz * 1.15) / 2, s, sz, True, GREEN if light else CREAM, al[i]])
    y += hh
    tot_uzs = tot_usd = 0.0
    for k, ln in enumerate(doc["lines"]):
        note = (ln.get("note") or "").strip()
        nm_lines = _wrap(m, ln["name"].strip(), widths[1] - 8, fs) or [""]
        nm_lines = nm_lines[:3]
        rh = 8 + 14 * len(nm_lines) + (14 if note else 0)
        if k % 2 and not light:
            ops.append(["rect", x0, y, W - 2 * x0, rh, "#f7f3ec", None, 0])
        tot_uzs += ln["uzs"] or 0
        tot_usd += ln["usd"] or 0
        cells = [str(ln["no"]), None, ln["unit"], _qty(ln["qty"]), _num(ln["price_uzs"] or 0, 0) if ln["price_uzs"] else "",
                 _num(ln["uzs"] or 0, 0) if ln["uzs"] else "", _num(ln["usd"] or 0)]
        for i, c in enumerate(cells):
            if i == 1:
                for q, part in enumerate(nm_lines):
                    ops.append(["text", xs[1] + 4, y + 4 + q * 14, part, fs, False, INK, "l"])
                if note:
                    s, sz = m.fit(f"{w_['rnote']}: {note}", widths[1] - 10, 9.5, True)
                    ops.append(["text", xs[1] + 8, y + 5 + len(nm_lines) * 14, s, sz, True, "#9a5b13", "l"])
                continue
            s, sz = m.fit(c, widths[i] - 8, fs, i == 6)
            tx = xs[i] + widths[i] / 2 if al[i] == "c" else xs[i + 1] - 5
            ops.append(["text", tx, y + 4, s, sz, i == 6, INK if i != 6 else GREEN, al[i]])
        ops.append(["line", x0, y + rh, W - x0, y + rh, LINE, 0.6])
        y += rh
    # итог
    th = 28
    ops.append(["rect", x0, y, W - 2 * x0, th, CREAM, None, 0])
    ops.append(["text", xs[1] + 4, y + 7, w_["total"], 11.5, True, INK, "l"])
    ops.append(["text", xs[6] - 5, y + 7, _num(tot_uzs, 0) if tot_uzs else "", 11.5, True, INK, "r"])
    ops.append(["text", xs[7] - 5, y + 6, "$" + _num(tot_usd), 12.5, True, GREEN, "r"])
    y += th + 12
    # қарз блоки
    if prev is not None and after is not None:
        def dt(v):
            return ("$" + _num(v)) if v >= 0.005 else ((w_["adv"] + " $" + _num(-v)) if v <= -0.005 else w_["nodebt"])
        cards = [(w_["prev"], dt(prev), INK, None), (w_["this"], "$" + _num(tot_usd), INK, None),
                 (w_["after"], dt(after), NEG if after >= 0.005 else GREEN, CREAM)]
        gap = 10
        cw = (W - 2 * x0 - 2 * gap) / 3
        for i, (lbl, v, col, fill) in enumerate(cards):
            x = x0 + i * (cw + gap)
            ops.append(["rect", x, y, cw, 50, fill, LINE, 1])
            ops.append(["text", x + 10, y + 8, lbl, 9.5, False, MUT, "l"])
            s, sz = m.fit(v, cw - 20, 15, True)
            ops.append(["text", x + 10, y + 24, s, sz, True, col, "l"])
        y += 62
    ops.append(["text", W / 2, y + 2, w_["thanks"], 10.5, False, MUT, "c"])
    y += 26
    return W, int(y), ops


def _wrap(m, s, width, size):
    out, cur = [], ""
    for w in s.split():
        t = (cur + " " + w).strip()
        if m.tw(t, size) > width and cur:
            out.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- чизиш
def render_pil(w, h, ops, path):
    from PIL import Image, ImageDraw, ImageFont
    S = SCALE
    img = Image.new("RGB", (w * S, h * S), "white")
    dr = ImageDraw.Draw(img)
    cand = [c for c in pdfgen.FONT_CANDIDATES if os.path.exists(c[0])][0]
    cache = {}

    def font(sz, bold):
        k = (round(sz * S * 1.0), bold)
        if k not in cache:
            cache[k] = ImageFont.truetype(cand[1] if bold and os.path.exists(cand[1]) else cand[0], k[0])
        return cache[k]
    for op in ops:
        if op[0] == "rect":
            _, x, y, rw, rh, fill, stroke, lw = op
            dr.rectangle([x * S, y * S, (x + rw) * S, (y + rh) * S], fill=fill, outline=stroke, width=max(1, int(lw * S)) if stroke else 0)
        elif op[0] == "line":
            _, x1, y1, x2, y2, col, lw = op
            dr.line([x1 * S, y1 * S, x2 * S, y2 * S], fill=col, width=max(1, int(round(lw * S))))
        elif op[0] == "img":
            _, p, x, y, iw, ih = op
            im = Image.open(p).convert("RGBA").resize((int(iw * S), int(ih * S)), Image.LANCZOS)
            img.paste(im, (int(x * S), int(y * S)), im)
        elif op[0] == "text":
            _, x, y, s, sz, bold, col, al = op
            f = font(sz, bold)
            tw = dr.textlength(s, font=f)
            xx = x * S - (tw if al == "r" else tw / 2 if al == "c" else 0)
            dr.text((xx, y * S), s, font=f, fill=col)
    img.save(path, "PNG", optimize=True)
    return path


PS = r'''
param([string]$spec, [string]$out)
Add-Type -AssemblyName System.Drawing
$j = Get-Content -Raw -Encoding UTF8 $spec | ConvertFrom-Json
$S = [double]$j.scale
$bmp = New-Object System.Drawing.Bitmap ([int]($j.w * $S)), ([int]($j.h * $S))
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = 'AntiAlias'; $g.TextRenderingHint = 'AntiAliasGridFit'
$g.Clear([System.Drawing.Color]::White)
$fmt = [System.Drawing.StringFormat]::GenericTypographic
$fmt.FormatFlags = $fmt.FormatFlags -bor [System.Drawing.StringFormatFlags]::MeasureTrailingSpaces
function Col($c) { [System.Drawing.ColorTranslator]::FromHtml($c) }
foreach ($o in $j.ops) {
  switch ($o[0]) {
    'img'  { $im = [System.Drawing.Image]::FromFile([string]$o[1])
             $g.DrawImage($im, [single]($o[2]*$S), [single]($o[3]*$S), [single]($o[4]*$S), [single]($o[5]*$S)); $im.Dispose() }
    'rect' { if ($o[5]) { $b = New-Object System.Drawing.SolidBrush (Col $o[5]); $g.FillRectangle($b, [single]($o[1]*$S), [single]($o[2]*$S), [single]($o[3]*$S), [single]($o[4]*$S)) }
             if ($o[6]) { $p = New-Object System.Drawing.Pen (Col $o[6]), ([single]([Math]::Max(1, $o[7] * $S)))
               $g.DrawRectangle($p, [single]($o[1]*$S), [single]($o[2]*$S), [single]($o[3]*$S), [single]($o[4]*$S)) } }
    'line' { $p = New-Object System.Drawing.Pen (Col $o[5]), ([single]([Math]::Max(1, $o[6] * $S)))
             $g.DrawLine($p, [single]($o[1]*$S), [single]($o[2]*$S), [single]($o[3]*$S), [single]($o[4]*$S)) }
    'text' { $st = if ($o[5]) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular }
             $f = New-Object System.Drawing.Font 'Arial', ([single]($o[4] * $S)), $st, ([System.Drawing.GraphicsUnit]::Pixel)
             $b = New-Object System.Drawing.SolidBrush (Col $o[6])
             $sz = $g.MeasureString([string]$o[3], $f, 100000, $fmt)
             $x = $o[1] * $S
             if ($o[7] -eq 'r') { $x -= $sz.Width } elseif ($o[7] -eq 'c') { $x -= $sz.Width / 2 }
             $g.DrawString([string]$o[3], $f, $b, [single]$x, [single]($o[2] * $S), $fmt); $f.Dispose() }
  }
}
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
'''


def render_windows(w, h, ops, path):
    tmp = tempfile.mkdtemp()
    spec, ps = os.path.join(tmp, "spec.json"), os.path.join(tmp, "nakl.ps1")
    with open(spec, "w", encoding="utf-8") as f:
        json.dump(dict(w=w, h=h, scale=SCALE, ops=ops), f, ensure_ascii=False)
    with open(ps, "w", encoding="utf-8-sig") as f:
        f.write(PS)
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps, "-spec", spec, "-out", path],
                   check=True, timeout=60, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return path


def render(doc, path, **kw):
    w, h, ops = layout(doc, **kw)
    if os.name == "nt":
        return render_windows(w, h, ops, path)
    return render_pil(w, h, ops, path)
