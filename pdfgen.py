"""Минимальный генератор PDF без внешних библиотек: TrueType-шрифт (кириллица), таблицы, PNG/JPEG-логотип.
Используется для «Ой итоги» → PDF. Шрифт берётся из системы (Windows: Arial)."""
import os, struct, zlib, threading

FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]
NEED = "ўқғҳЎҚҒҲ"
FALLBACK = str.maketrans({"ў": "у", "қ": "к", "ғ": "г", "ҳ": "х", "Ў": "У", "Қ": "К", "Ғ": "Г", "Ҳ": "Х", "−": "-", "→": "->", "≈": "~"})


class TTF:
    def __init__(self, path):
        self.path = path
        d = self.data = open(path, "rb").read()
        n = struct.unpack(">H", d[4:6])[0]
        self.tab = {}
        for i in range(n):
            tag, _, off, ln = struct.unpack(">4sIII", d[12 + 16 * i: 28 + 16 * i])
            self.tab[tag.decode("latin1")] = (off, ln)
        o = self.tab["head"][0]
        self.upem = struct.unpack(">H", d[o + 18:o + 20])[0]
        self.bbox = struct.unpack(">hhhh", d[o + 36:o + 44])
        o = self.tab["hhea"][0]
        self.ascent, self.descent = struct.unpack(">hh", d[o + 4:o + 8])
        nhm = struct.unpack(">H", d[o + 34:o + 36])[0]
        o = self.tab["maxp"][0]
        ng = struct.unpack(">H", d[o + 4:o + 6])[0]
        o = self.tab["hmtx"][0]
        adv = [struct.unpack(">H", d[o + 4 * i:o + 4 * i + 2])[0] for i in range(nhm)]
        self.adv = adv + [adv[-1]] * max(0, ng - nhm)
        self.cap = self.ascent
        if "OS/2" in self.tab:
            o, ln = self.tab["OS/2"]
            if ln >= 90 and struct.unpack(">H", d[o:o + 2])[0] >= 2:
                self.cap = struct.unpack(">h", d[o + 88:o + 90])[0]
        self.cmap = self._cmap()

    def _cmap(self):
        d = self.data
        o = self.tab["cmap"][0]
        n = struct.unpack(">H", d[o + 2:o + 4])[0]
        best = None
        for i in range(n):
            pid, eid, off = struct.unpack(">HHI", d[o + 4 + 8 * i: o + 12 + 8 * i])
            fmt = struct.unpack(">H", d[o + off:o + off + 2])[0]
            if (pid, eid) in ((3, 10), (0, 4), (0, 6)) and fmt == 12:
                best = (o + off, 12)
                break
            if (pid, eid) in ((3, 1), (0, 3), (0, 1), (0, 2)) and fmt == 4 and not best:
                best = (o + off, 4)
        m = {}
        if not best:
            return m
        t, fmt = best
        if fmt == 4:
            segx2 = struct.unpack(">H", d[t + 6:t + 8])[0]
            ends = t + 14
            starts = ends + segx2 + 2
            deltas = starts + segx2
            ros = deltas + segx2
            for s in range(segx2 // 2):
                end, = struct.unpack(">H", d[ends + 2 * s: ends + 2 * s + 2])
                st, = struct.unpack(">H", d[starts + 2 * s: starts + 2 * s + 2])
                de, = struct.unpack(">h", d[deltas + 2 * s: deltas + 2 * s + 2])
                ro, = struct.unpack(">H", d[ros + 2 * s: ros + 2 * s + 2])
                if st == 0xFFFF:
                    continue
                for c in range(st, end + 1):
                    if ro == 0:
                        g = (c + de) & 0xFFFF
                    else:
                        p = ros + 2 * s + ro + 2 * (c - st)
                        g, = struct.unpack(">H", d[p:p + 2])
                        if g:
                            g = (g + de) & 0xFFFF
                    if g:
                        m[c] = g
        else:
            ng, = struct.unpack(">I", d[t + 12:t + 16])
            for i in range(ng):
                a, b, g0 = struct.unpack(">III", d[t + 16 + 12 * i: t + 28 + 12 * i])
                for c in range(a, b + 1):
                    m[c] = g0 + (c - a)
        return m

    def has(self, s):
        return all(ord(ch) in self.cmap for ch in s)

    def width(self, s, size):
        return sum(self.adv[self.cmap.get(ord(ch), 0)] for ch in s) * size / self.upem


_fonts = None
_flock = threading.Lock()


def fonts():
    """(regular, bold, need_fallback) — первый шрифт, где есть ўқғҳ; иначе первый попавшийся."""
    global _fonts
    with _flock:
        if _fonts is None:
            found = None
            for r, b in FONT_CANDIDATES:
                if os.path.exists(r):
                    f = (TTF(r), TTF(b) if os.path.exists(b) else None)
                    if f[0].has(NEED):
                        found = (f[0], f[1] or f[0], False)
                        break
                    found = found or (f[0], f[1] or f[0], True)
            if not found:
                raise RuntimeError("PDF: системный шрифт не найден")
            _fonts = found
        return _fonts


# ---------------------------------------------------------------- изображения
_img_cache = {}


def _png(raw):
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    p, idat, w = 8, [], None
    while p < len(raw):
        ln, = struct.unpack(">I", raw[p:p + 4])
        typ = raw[p + 4:p + 8]
        body = raw[p + 8:p + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", body)
        elif typ == b"IDAT":
            idat.append(body)
        p += 12 + ln
    if not w or bd != 8 or il or ct not in (0, 2, 4, 6):
        return None
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    data = zlib.decompress(b"".join(idat))
    stride = w * ch
    out = bytearray(stride * h)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        ft = data[pos]
        line = bytearray(data[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ft == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif ft == 2:
            line = bytearray((a + b) & 255 for a, b in zip(line, prev))
        elif ft == 3:
            for i in range(stride):
                line[i] = (line[i] + ((line[i - ch] if i >= ch else 0) + prev[i]) // 2) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    if ch in (1, 3):
        return dict(w=w, h=h, cs="/DeviceGray" if ch == 1 else "/DeviceRGB", data=zlib.compress(bytes(out)), alpha=None)
    col = ch - 1
    color = bytearray(w * h * col)
    for k in range(col):
        color[k::col] = out[k::ch]
    alpha = bytes(out[col::ch])
    return dict(w=w, h=h, cs="/DeviceGray" if col == 1 else "/DeviceRGB", data=zlib.compress(bytes(color)), alpha=zlib.compress(alpha))


def _jpg(raw):
    if raw[:3] != b"\xff\xd8\xff":
        return None
    p = 2
    while p < len(raw):
        if raw[p] != 0xFF:
            return None
        mk = raw[p + 1]
        ln, = struct.unpack(">H", raw[p + 2:p + 4])
        if mk in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", raw[p + 5:p + 9])
            nc = raw[p + 9]
            return dict(w=w, h=h, cs={1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(nc, "/DeviceRGB"), data=raw, alpha=None, dct=True)
        p += 2 + ln
    return None


def load_image(path):
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    key = (path, mt)
    if key not in _img_cache:
        raw = open(path, "rb").read()
        try:
            _img_cache.clear()
            _img_cache[key] = _png(raw) or _jpg(raw)
        except Exception:
            _img_cache[key] = None
    return _img_cache[key]


# ---------------------------------------------------------------- документ
def _hexcol(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


class Doc:
    W, H = 595.28, 841.89          # A4, pt

    def __init__(self, margin=36, footer=""):
        self.reg, self.bold, self.fb = fonts()
        self.m = margin
        self.pages = []
        self.used = {0: set(), 1: set()}
        self.images = []
        self.footer = footer
        self.new_page()

    # --- примитивы
    def new_page(self):
        self.pages.append([])
        self.y = self.m

    def _c(self, s):
        self.pages[-1].append(s)

    def font(self, bold):
        return self.bold if bold else self.reg

    def tw(self, s, size, bold=False):
        s = self.fix(s)
        return self.font(bold).width(s, size)

    def fix(self, s):
        s = str(s)
        return s.translate(FALLBACK) if self.fb else s

    def text(self, x, y, s, size=9, bold=False, color="#1f2a27", align="l"):
        """y — верхняя граница строки (сверху вниз)."""
        s = self.fix(s)
        if not s:
            return
        f = self.font(bold)
        w = f.width(s, size)
        if align == "r":
            x -= w
        elif align == "c":
            x -= w / 2
        base = self.H - (y + size * f.ascent / f.upem)
        gids = []
        for ch in s:
            g = f.cmap.get(ord(ch), 0)
            gids.append(g)
            self.used[1 if bold else 0].add((g, ord(ch)))
        r, g_, b = _hexcol(color)
        self._c(f"BT {r:.3f} {g_:.3f} {b:.3f} rg /F{2 if bold else 1} {size:.2f} Tf {x:.2f} {base:.2f} Td <{''.join('%04X' % g for g in gids)}> Tj ET")

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=0.5):
        ops = []
        if fill:
            ops.append("%.3f %.3f %.3f rg" % _hexcol(fill))
        if stroke:
            ops.append("%.3f %.3f %.3f RG %.2f w" % (_hexcol(stroke) + (lw,)))
        ops.append(f"{x:.2f} {self.H - y - h:.2f} {w:.2f} {h:.2f} re " + ("B" if fill and stroke else "f" if fill else "S"))
        self._c(" ".join(ops))

    def hline(self, x1, x2, y, color="#cfd8d5", lw=0.5):
        self._c("%.3f %.3f %.3f RG %.2f w " % (_hexcol(color) + (lw,)) + f"{x1:.2f} {self.H - y:.2f} m {x2:.2f} {self.H - y:.2f} l S")

    def image(self, img, x, y, w, h):
        if not img:
            return
        if img not in self.images:
            self.images.append(img)
        i = self.images.index(img) + 1
        self._c(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {self.H - y - h:.2f} cm /Im{i} Do Q")

    # --- верстка
    @property
    def cw(self):
        return self.W - 2 * self.m

    def need(self, h):
        if self.y + h > self.H - self.m - 14:
            self.new_page()
            return True
        return False

    def wrap(self, s, width, size, bold=False):
        out = []
        for para in str(s).split("\n"):
            words, cur = para.split(" "), ""
            for wd in words:
                t = (cur + " " + wd).strip()
                if cur and self.tw(t, size, bold) > width:
                    out.append(cur)
                    cur = wd
                else:
                    cur = t
            out.append(cur)
        return out

    def para(self, s, size=8.5, color="#6b7b76", bold=False, gap=4):
        for ln in self.wrap(s, self.cw, size, bold):
            self.need(size * 1.3)
            self.text(self.m, self.y, ln, size, bold, color)
            self.y += size * 1.3
        self.y += gap

    def h1(self, s, size=13, color="#3E643C"):
        self.need(size * 2.5)
        self.text(self.m, self.y, s, size, True, color)
        self.y += size * 1.45

    def table(self, cols, rows, size=8.5, head=True, zebra=False, keep=6):
        """cols: [(заголовок, доля ширины, 'l'|'r')]; rows: [dict(cells=[...], style='tot'|'sub'|'muted'|'note', color=[..])]
        В ячейке можно '\\n' — вторая строка выводится мелким серым шрифтом."""
        tot = sum(c[1] for c in cols)
        ws = [self.cw * c[1] / tot for c in cols]
        pad = 3.5
        lh = size * 1.25

        def draw_head():
            if not head:
                return
            h = lh + 2 * pad - 1
            self.rect(self.m, self.y, self.cw, h, fill="#eef3ee")
            x = self.m
            for (t, _, al), w in zip(cols, ws):
                self.text(x + (w - pad if al == "r" else pad), self.y + pad - 0.5, t, size - 1, True, "#3E643C", al)
                x += w
            self.y += h

        def span(r):
            return all(c in (None, "") for c in r["cells"][1:])

        def row_h(r):
            n = 1
            for i, (cval, w) in enumerate(zip(r["cells"], ws)):
                if cval is None:
                    continue
                parts = str(cval).split("\n")
                al = cols[i][2]
                ww = (self.cw if i == 0 and span(r) else w) - 2 * pad - (r.get("indent", 0) if i == r.get("indent_col", 0) else 0)
                main = self.wrap(parts[0], ww, size, r.get("style") in ("tot", "sub")) if al == "l" else [parts[0]]
                n = max(n, len(main) + (len(parts) - 1) * 0.9)
            return n * lh + 2 * pad

        self.need(min(len(rows), keep) * (lh + 2 * pad) + (lh + 2 * pad if head else 0))
        draw_head()
        for k, r in enumerate(rows):
            h = row_h(r)
            if self.need(h):
                draw_head()
            st = r.get("style")
            if st == "tot":
                self.rect(self.m, self.y, self.cw, h, fill="#f0e4d5")
            elif st == "sub":
                self.rect(self.m, self.y, self.cw, h, fill="#f6f8f7")
            elif zebra and k % 2:
                self.rect(self.m, self.y, self.cw, h, fill="#fafbfb")
            if st == "note":
                self.text(self.m + pad, self.y + pad, r["cells"][0], size - 1, False, "#6b7b76")
                self.y += h
                continue
            x = self.m
            bold = st in ("tot", "sub")
            colors = r.get("color") or [None] * len(cols)
            for i, ((_, _, al), w, cval) in enumerate(zip(cols, ws, r["cells"])):
                if cval is not None and cval != "":
                    parts = str(cval).split("\n")
                    col = colors[i] if i < len(colors) and colors[i] else ("#6b7b76" if st == "muted" else "#1f2a27")
                    ind = r.get("indent", 0) if i == r.get("indent_col", 0) else 0
                    tx = x + (w - pad if al == "r" else pad + ind)
                    yy = self.y + pad
                    ww = (self.cw if i == 0 and span(r) else w) - 2 * pad - ind
                    lines = self.wrap(parts[0], ww, size, bold) if al == "l" else [parts[0]]
                    for ln in lines:
                        self.text(tx, yy, ln, size, bold or r.get("bold_cols", ()) and i in r.get("bold_cols", ()), col, al)
                        yy += lh
                    for sub in parts[1:]:
                        self.text(tx, yy - 1, sub, size - 1.5, False, "#6b7b76", al)
                        yy += lh * 0.9
                x += w
            self.y += h
            self.hline(self.m, self.m + self.cw, self.y, "#e2e7e5")
        self.y += 8

    # --- сборка файла
    def _font_objs(self, add, f, used, name):
        data = f.data
        ff = add(b"<< /Length %d /Length1 %d /Filter /FlateDecode >>\nstream\n" % (0, len(data)), stream=zlib.compress(data))
        sc = 1000 / f.upem
        fd = add(("<< /Type /FontDescriptor /FontName /%s /Flags 32 /FontBBox [%d %d %d %d] /ItalicAngle 0 /Ascent %d /Descent %d "
                  "/CapHeight %d /StemV 80 /FontFile2 %d 0 R >>" % (name, *(v * sc for v in f.bbox), f.ascent * sc, f.descent * sc,
                                                                     f.cap * sc, ff)).encode())
        gl = sorted({g for g, _ in used})
        w = " ".join("%d [%d]" % (g, round(f.adv[g] * sc)) for g in gl)
        cid = add(("<< /Type /Font /Subtype /CIDFontType2 /BaseFont /%s /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
                   "/FontDescriptor %d 0 R /W [%s] /CIDToGIDMap /Identity >>" % (name, fd, w)).encode())
        m = {}
        for g, u in used:
            m.setdefault(g, u)
        bf = "\n".join("<%04X> <%s>" % (g, "".join("%04X" % x for x in struct.unpack(">%dH" % (len(chr(u).encode("utf-16-be")) // 2),
                                                                                      chr(u).encode("utf-16-be")))) for g, u in sorted(m.items()))
        cm = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
              "/CMapName /Adobe-Identity-UCS def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
              "%d beginbfchar\n%s\nendbfchar endcmap CMapName currentdict /CMap defineresource pop end end" % (len(m), bf)).encode()
        tu = add(b"<< /Length 0 /Filter /FlateDecode >>\nstream\n", stream=zlib.compress(cm))
        return add(("<< /Type /Font /Subtype /Type0 /BaseFont /%s /Encoding /Identity-H /DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>"
                    % (name, cid, tu)).encode())

    def output(self):
        # подвал с номерами страниц
        n = len(self.pages)
        for i in range(n):
            self.pages[i].append("")
            save = self.pages
            self.pages = save[:i + 1]
            self.text(self.m, self.H - self.m + 4, self.footer, 7, False, "#6b7b76")
            self.text(self.W - self.m, self.H - self.m + 4, f"{i + 1} / {n}", 7, False, "#6b7b76", "r")
            self.pages = save
        objs = []

        def add(head, stream=None):
            if stream is not None:
                head = head.replace(b"/Length 0", b"/Length %d" % len(stream), 1)
                objs.append(head + stream + b"\nendstream")
            else:
                objs.append(head)
            return len(objs)

        f1 = self._font_objs(add, self.reg, self.used[0] or {(0, 32)}, "F1base")
        f2 = self._font_objs(add, self.bold, self.used[1] or {(0, 32)}, "F2base")
        ims = []
        for img in self.images:
            sm = ""
            if img.get("alpha"):
                a = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 0 /Filter /FlateDecode >>\nstream\n"
                        % (img["w"], img["h"]), stream=img["alpha"])
                sm = " /SMask %d 0 R" % a
            flt = "/DCTDecode" if img.get("dct") else "/FlateDecode"
            ims.append(add(("<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s /BitsPerComponent 8 /Length 0 /Filter %s%s >>\nstream\n"
                            % (img["w"], img["h"], img["cs"], flt, sm)).encode(), stream=img["data"]))
        res = "<< /Font << /F1 %d 0 R /F2 %d 0 R >> /XObject << %s >> >>" % (f1, f2, " ".join("/Im%d %d 0 R" % (i + 1, o) for i, o in enumerate(ims)))
        pages_id = len(objs) + 2 * n + 1
        kids = []
        for pg in self.pages:
            c = add(b"<< /Length 0 /Filter /FlateDecode >>\nstream\n", stream=zlib.compress("\n".join(pg).encode("latin1")))
            kids.append(add(("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Resources %s /Contents %d 0 R >>"
                             % (pages_id, self.W, self.H, res, c)).encode()))
        assert add(("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in kids), n)).encode()) == pages_id
        cat = add(("<< /Type /Catalog /Pages %d 0 R >>" % pages_id).encode())
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offs = []
        for i, o in enumerate(objs):
            offs.append(len(out))
            out += b"%d 0 obj\n" % (i + 1) + o + b"\nendobj\n"
        x = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for o in offs:
            out += b"%010d 00000 n \n" % o
        out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, cat, x)
        return bytes(out)
