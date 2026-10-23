"""Excel (.xlsx) — оддий, замонавий, минималистик кўриниш (битта варақ):
сарлавҳа → асосий кўрсаткичлар (KPI) → жадвал → қўшимча жадваллар ва уларнинг ёнида диаграмма.
Платформадаги барча «Excel» юклашлари шу ердан ўтади."""
import io
import re
from datetime import date, datetime

from flask import send_file

GREEN = "3E643C"          # фирма ранги — фақат урғу учун (чизиқ, диаграмма)
INK = "1F2A27"            # асосий матн
MUTED = "7A8783"          # иккинчи даражали матн
RULE = "E6E9E7"           # ингичка ажратувчи чизиқ
COMPANY = "Lumberture · ул. Жомий, 5, Ташкент · +998 33 409-60-70"
COMPANY_I18N = {"uz": "Lumberture · Jomiy koʻchasi, 5, Toshkent · +998 33 409-60-70", "en": "Lumberture · 5 Jomiy St, Tashkent · +998 33 409-60-70"}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FONT = "Calibri"

FMT = {"money": '#,##0.00;[Red]-#,##0.00;"–"', "som": '#,##0;[Red]-#,##0;"–"', "qty": '#,##0.###;[Red]-#,##0.###;"–"', "int": "0",
       "date": "DD.MM.YYYY", "pct": "0.0%"}
NUM = ("money", "som", "qty", "int")


def _lang():
    try:
        import i18n
        return i18n.lang()
    except Exception:
        return "ru"


def _tr(s):
    try:
        import i18n
        return i18n.tr(s) if isinstance(s, str) else s
    except Exception:
        return s


def _trv(v):
    """Катакдаги тизим ёзувлари («Нач. сальдо», «Накладная № 6114») — тилга қараб; исм ва номлар ўзгармайди."""
    if not isinstance(v, str) or not v:
        return v
    try:
        import i18n
        if i18n.lang() == "src":
            return v
        if v in i18n._D:
            return i18n.phrase(v, i18n.lang())
        if " № " in v:
            a, b = v.split(" № ", 1)
            if a in i18n._D:
                return i18n.phrase(a, i18n.lang()) + " № " + b
    except Exception:
        pass
    return v


def _kind(v):
    if isinstance(v, bool):
        return "text"
    if isinstance(v, (int, float)):
        return "money" if isinstance(v, float) else "int"
    if isinstance(v, (date, datetime)):
        return "date"
    if isinstance(v, str) and _ISO.match(v):
        return "date"
    return "text"


def _val(v, k):
    if v is None or v == "":
        return None
    if k == "date" and isinstance(v, str) and _ISO.match(v):
        return date(int(v[:4]), int(v[5:7]), int(v[8:10]))
    if k in NUM and isinstance(v, str):
        try:
            return float(v.replace(" ", "").replace(",", "."))
        except ValueError:
            return v
    return v


def _kinds(head, rows):
    out = []
    for i in range(len(head)):
        ks = {_kind(r[i]) for r in rows if i < len(r) and r[i] not in (None, "")}
        out.append(ks.pop() if len(ks) == 1 else ("money" if ks and ks <= {"money", "int"} else "text"))
    return out


def style_header(ws, row, ncol, col0=1):
    """Устун сарлавҳаси: кичик, қалин, кулранг матн, остида фирма рангидаги ингичка чизиқ (фон йўқ)."""
    from openpyxl.styles import Alignment, Border, Font, Side
    for i in range(col0, col0 + ncol):
        c = ws.cell(row=row, column=i)
        c.font = Font(name=FONT, bold=True, size=9, color=MUTED)
        c.alignment = Alignment(wrap_text=True, vertical="bottom")
        c.border = Border(bottom=Side(style="medium", color=GREEN))


class Sheet:
    """Битта варақни юқоридан пастга тўлдириш."""

    def __init__(self, ws):
        from openpyxl.styles import Alignment, Border, Font, Side
        self.ws, self.r = ws, 1
        self.F, self.A, self.B, self.S = Font, Alignment, Border, Side
        self.widths = {}

    def _w(self, col, n):
        self.widths[col] = max(self.widths.get(col, 0), min(n, 60))

    def title(self, title, subtitle):
        ws = self.ws
        c = ws.cell(row=1, column=1, value=_tr(title))
        c.font = self.F(name=FONT, bold=True, size=16, color=INK)
        ws.row_dimensions[1].height = 24
        c = ws.cell(row=2, column=1, value=subtitle)
        c.font = self.F(name=FONT, size=9, color=MUTED)
        self.r = 4

    def kpis(self, items):
        """[(ёзув, қиймат, тур)] — бир қаторда: устида кичик ёзув, тагида катта рақам."""
        if not items:
            return
        col = 1
        for label, val, k in items:
            a = self.ws.cell(row=self.r, column=col, value=_tr(label))
            a.font = self.F(name=FONT, size=9, color=MUTED)
            b = self.ws.cell(row=self.r + 1, column=col, value=val)
            b.font = self.F(name=FONT, bold=True, size=14, color=("B3261E" if isinstance(val, (int, float)) and k == "debt" and val > 0.005 else INK))
            b.number_format = FMT["money" if k in ("debt", "money") else k] if k in FMT or k == "debt" else "General"
            b.alignment = self.A(horizontal="left")
            self._w(col, 16)
            col += 2
        self.ws.row_dimensions[self.r + 1].height = 22
        self.r += 3

    def section(self, text, col=1):
        c = self.ws.cell(row=self.r, column=col, value=_tr(text))
        c.font = self.F(name=FONT, bold=True, size=11, color=INK)
        self.r += 1

    def table(self, head, rows, kinds=None, totals=None, col0=1, filt=False):
        """-> (биринчи маълумот қатори, охирги маълумот қатори)"""
        ws, side = self.ws, self.S(style="thin", color=RULE)
        head = [_tr(h) for h in head]
        rows = [list(r) for r in rows]
        n = len(head)
        kinds = kinds or _kinds(head, rows)
        h = self.r
        for i, t in enumerate(head):
            ws.cell(row=h, column=col0 + i, value=t)
            self._w(col0 + i, len(str(t)) + 2)
            if kinds[i] in NUM:
                ws.cell(row=h, column=col0 + i).alignment = self.A(horizontal="right", wrap_text=True, vertical="bottom")
        style_header(ws, h, n, col0)
        for i in range(n):
            if kinds[i] == "seq":
                ws.cell(row=h, column=col0 + i).alignment = self.A(horizontal="center", vertical="bottom")
        for i in range(n):
            if kinds[i] in NUM:
                ws.cell(row=h, column=col0 + i).alignment = self.A(horizontal="right", wrap_text=True, vertical="bottom")
        ws.row_dimensions[h].height = 26
        r = h
        for row in rows:
            r += 1
            for i in range(n):
                k = kinds[i]
                v = _val(row[i] if i < len(row) else None, k)
                if k == "text":
                    v = _trv(v)
                c = ws.cell(row=r, column=col0 + i, value=v)
                c.font = self.F(name=FONT, size=10, color=INK)
                c.border = self.B(bottom=side)
                if k in FMT and not isinstance(v, str):
                    c.number_format = FMT[k]
                    if k == "qty" and isinstance(v, (int, float)) and abs(v - round(v)) < 1e-9:
                        c.number_format = '#,##0;[Red]-#,##0;"–"'
                    elif k == "qty":
                        c.number_format = '#,##0.0##;[Red]-#,##0.0##;"–"'
                if k == "text":
                    c.alignment = self.A(vertical="top", wrap_text=len(str(v or "")) > 60)
                if k in NUM:
                    c.alignment = self.A(horizontal="right")
                if k == "seq":                       # тартиб рақами
                    c.font = self.F(name=FONT, size=9, color=MUTED)
                    c.alignment = self.A(horizontal="center", vertical="top")
                    self._w(col0 + i, 5)
                    continue
                ln = len(f"{v:,.2f}") if isinstance(v, float) else (11 if k == "date" else len(str(v or "")))
                self._w(col0 + i, ln + 2)
        first, last = h + 1, r
        if totals:
            r += 1
            if totals is True:
                from openpyxl.utils import get_column_letter
                totals = [_tr("Жами")] + [None] * (n - 1)
                for i in range(1, n):
                    if kinds[i] in ("money", "som", "qty") and rows:
                        L = get_column_letter(col0 + i)
                        totals[i] = f"=SUM({L}{first}:{L}{last})"
            for i in range(n):
                v = totals[i] if i < len(totals) else None
                c = ws.cell(row=r, column=col0 + i, value=_tr(v) if isinstance(v, str) and not v.startswith("=") else v)
                c.font = self.F(name=FONT, bold=True, size=10, color=INK)
                c.border = self.B(top=self.S(style="thin", color=INK))
                if kinds[i] in FMT:
                    c.number_format = FMT[kinds[i]]
                if kinds[i] in NUM:
                    c.alignment = self.A(horizontal="right")
        if filt and rows:
            from openpyxl.utils import get_column_letter
            ws.auto_filter.ref = f"{get_column_letter(col0)}{h}:{get_column_letter(col0 + n - 1)}{last}"
        self.r = r + 2
        return h, first, last

    def chart(self, kind, title, cat_col, val_cols, first, last, anchor, names=None, height=None):
        """Диаграмма шу варақдаги жадвалдан. kind: bar (ётиқ устун, энг каттаси тепада) | col (тик устунлар)."""
        from openpyxl.chart import BarChart, Reference
        from openpyxl.chart.label import DataLabelList
        if last < first:
            return
        ch = BarChart()
        ch.type = "bar" if kind == "bar" else "col"
        ch.grouping = "clustered"
        ch.title = _tr(title)
        ch.style = 2
        n = last - first + 1
        ch.height = height or (max(6, min(16, 0.6 * n + 3)) if kind == "bar" else 7.5)
        ch.width = 16
        for vc in val_cols:
            ch.add_data(Reference(self.ws, min_col=vc, min_row=first - 1, max_row=last), titles_from_data=True)
        ch.set_categories(Reference(self.ws, min_col=cat_col, min_row=first, max_row=last))
        colors = [GREEN, "C9A66B", "8A1C1C"]
        for j, s in enumerate(ch.series):
            s.graphicalProperties.solidFill = colors[j % len(colors)]
            s.graphicalProperties.line.noFill = True
        ch.gapWidth = 60
        ch.y_axis.majorGridlines = None
        ch.y_axis.delete = True
        ch.x_axis.delete = False
        ch.dataLabels = DataLabelList()
        ch.dataLabels.showVal = True
        ch.dataLabels.showSerName = False
        ch.dataLabels.showCatName = False
        ch.dataLabels.showLegendKey = False
        ch.dataLabels.showPercent = False
        ch.dataLabels.numFmt = "#,##0"
        if kind == "bar":
            ch.x_axis.scaling.orientation = "maxMin"
        if len(val_cols) == 1:
            ch.legend = None
        else:
            ch.legend.position = "b"
        self.ws.add_chart(ch, anchor)

    def finish(self, landscape=True):
        from openpyxl.utils import get_column_letter
        ws = self.ws
        c = ws.cell(row=self.r + 1, column=1, value=COMPANY_I18N.get(_lang(), COMPANY))
        c.font = self.F(name=FONT, size=8, italic=True, color=MUTED)
        for col, w in self.widths.items():
            ws.column_dimensions[get_column_letter(col)].width = max(9, w)
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape" if landscape else "portrait"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True


def _top(rows, cat, val, n=10):
    agg = {}
    for r in rows:
        k = r[cat]
        if k in (None, ""):
            continue
        try:
            agg[k] = agg.get(k, 0.0) + float(_val(r[val], "money") or 0)
        except (TypeError, ValueError):
            pass
    return sorted(agg.items(), key=lambda x: -abs(x[1]))[:n]


def shift_right(ws, n=1, margin=2.5):
    """Бутун варақни n устун ўнгга суриш: A устуни — тор бўш чекка (Excel'нинг рақамлари ёнига ёпишиб турмасин)."""
    from openpyxl.utils import get_column_letter
    merged = [(m.min_row, m.min_col, m.max_row, m.max_col) for m in ws.merged_cells.ranges]
    for m in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(m))
    if ws.max_row and ws.max_column:
        ws.move_range(f"A1:{get_column_letter(ws.max_column)}{ws.max_row}", rows=0, cols=n, translate=True)
    for r1, c1, r2, c2 in merged:
        ws.merge_cells(start_row=r1, start_column=c1 + n, end_row=r2, end_column=c2 + n)
    widths = {k: v.width for k, v in ws.column_dimensions.items() if v.width}
    for k in list(widths):
        del ws.column_dimensions[k]
    from openpyxl.utils import column_index_from_string
    for k, w in widths.items():
        ws.column_dimensions[get_column_letter(column_index_from_string(k) + n)].width = w
    for i in range(1, n + 1):
        ws.column_dimensions[get_column_letter(i)].width = margin
    for im in getattr(ws, "_images", []):
        try:
            im.anchor._from.col += n
        except Exception:
            pass
    for ch in getattr(ws, "_charts", []):
        try:
            ch.anchor._from.col += n
            ch.anchor.to.col += n
        except Exception:
            pass
    if ws.print_area:
        try:
            pa = ws.print_area if isinstance(ws.print_area, str) else ws.print_area[0]
            rng = pa.split("!")[-1].replace("$", "")
            a, b = rng.split(":")
            import re as _re
            ca, ra = _re.match(r"([A-Z]+)(\d+)", a).groups(); cb, rb = _re.match(r"([A-Z]+)(\d+)", b).groups()
            ws.print_area = f"A{ra}:{get_column_letter(column_index_from_string(cb) + n)}{rb}"
        except Exception:
            pass


def respond(wb, name):
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{name}_{date.today():%Y%m%d}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _firm():
    try:
        import app as A
        return A.firm_info()
    except Exception:
        return {}


def letterhead(ws, ncol, title, subtitle, firm=None):
    """Накладной/мижоз ҳисоботидаги шапка: логотип, фирма номи, телефон, манзил; чизиқ; ҳисобот номи ва давр. -> кейинги бўш қатор."""
    from openpyxl.styles import Border, Font, Side
    firm = firm or _firm()
    for r in (1, 2, 3):
        ws.row_dimensions[r].height = 18
    lf = _logo_file()
    tc = 1
    if lf:
        try:
            from openpyxl.drawing.image import Image as XI
            im = XI(lf)
            k = 52 / max(im.height, 1)
            im.height, im.width = 52, int(im.width * k)
            ws.add_image(im, "A1")
            tc = 2
        except Exception:
            tc = 1
    ws.cell(row=1, column=tc, value=firm.get("company") or "Lumberture").font = Font(name=FONT, bold=True, size=16, color=INK)
    ws.cell(row=2, column=tc, value=firm.get("phone") or "+998 33 409-60-70").font = Font(name=FONT, size=9, color=MUTED)
    ws.cell(row=3, column=tc, value=firm.get("address") or "").font = Font(name=FONT, size=9, color=MUTED)
    for c in range(1, max(ncol, 2) + 1):
        ws.cell(row=4, column=c).border = Border(top=Side(style="medium", color=INK))
    ws.row_dimensions[4].height = 8
    ws.cell(row=5, column=1, value=_tr(title)).font = Font(name=FONT, bold=True, size=14, color=INK)
    ws.row_dimensions[5].height = 22
    ws.cell(row=6, column=1, value=subtitle).font = Font(name=FONT, size=9, color=MUTED)
    return 8, tc


def _kpi_boxes(ws, r, ncol, kpis):
    """Асосий рақамлар — ромкали катаклар (накладнойдаги қарз блокидек). Устунлар етмаса — бир нечта қаторга."""
    from openpyxl.styles import Alignment, Border, Font, Side
    if not kpis:
        return r
    thin = Side(style="thin", color=INK)
    per = max(1, min(len(kpis), ncol))
    span = max(1, ncol // per)
    for idx, (label, val, k) in enumerate(kpis):
        row = r + (idx // per) * 3
        c1 = 1 + (idx % per) * span
        c2 = c1 + span - 1 if (idx % per) < per - 1 else max(ncol, c1 + span - 1)
        a = ws.cell(row=row, column=c1, value=_tr(label))
        a.font = Font(name=FONT, size=9, color=MUTED)
        a.alignment = Alignment(horizontal="left", indent=1)
        b = ws.cell(row=row + 1, column=c1, value=val)
        b.font = Font(name=FONT, bold=True, size=13, color=("B3261E" if isinstance(val, (int, float)) and k == "debt" and val > 0.005 else INK))
        b.number_format = FMT["money" if k in ("debt", "money") else k] if (k in FMT or k == "debt") else "General"
        b.alignment = Alignment(horizontal="left", indent=1)
        if c2 > c1:
            ws.merge_cells(start_row=row, start_column=c1, end_row=row, end_column=c2)
            ws.merge_cells(start_row=row + 1, start_column=c1, end_row=row + 1, end_column=c2)
        for rr in (row, row + 1):
            for cc in range(c1, c2 + 1):
                ws.cell(row=rr, column=cc).border = Border(left=thin if cc == c1 else None, right=thin if cc == c2 else None,
                                                          top=thin if rr == row else None, bottom=thin if rr == row + 1 else None)
        ws.row_dimensions[row + 1].height = 22
    return r + ((len(kpis) - 1) // per + 1) * 3


def book(name, title, head, rows, subtitle="", kinds=None, totals=None, chart=None, sheet="Ҳисобот", kpis=None, sections=None, main_title=None):
    """Мижозга юбориладиган ҳисобот услубида битта варақ: логотипли шапка, асосий рақамлар (ромкада), жадвал, фирма маълумотлари.
    Диаграмма ва фильтр йўқ (chart/sections диаграммалари эътиборга олинмайди, sections жадвал сифатида чиқади)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = _tr(sheet)[:31]
    ws.sheet_view.showGridLines = False
    n = len(head) + 1                           # + № устуни
    sub = (_tr(subtitle) + " · " if subtitle else "") + _tr("Тузилди") + ": " + datetime.now().strftime("%d.%m.%Y %H:%M")
    r, tc = letterhead(ws, n, title, sub)
    r = _kpi_boxes(ws, r, n, kpis)
    S = Sheet(ws)
    S.r = r
    rows = [list(x) for x in rows]
    kinds = kinds or _kinds(head, rows)
    # тартиб рақами (№) — биринчи устун
    head = ["№"] + list(head)
    rows = [[i] + r for i, r in enumerate(rows, 1)]
    kinds = ["seq"] + list(kinds)
    if isinstance(totals, (list, tuple)):
        totals = [None] + list(totals)
    for sec in (sections or []):
        S.section(sec["title"])
        sh = ["№"] + list(sec["head"])
        sr = [[i] + list(r) for i, r in enumerate(sec["rows"], 1)]
        sk = ["seq"] + list(sec.get("kinds") or _kinds(sec["head"], [list(r) for r in sec["rows"]]))
        st = sec.get("totals")
        st = [None] + list(st) if isinstance(st, (list, tuple)) else st
        S.table(sh, sr, sk, st)
    if sections or main_title:
        S.section(main_title or "Батафсил")
    S.table(head, rows, kinds, totals, filt=False)
    # фирма маълумотлари ва чоп этиш
    f = _firm()
    c = ws.cell(row=S.r, column=1, value=_tr("Савол бўлса:") + " " + (f.get("phone") or "+998 33 409-60-70") + " · Telegram: @Lumberture_bot")
    c.font = Font(name=FONT, size=9, color=MUTED)
    S.r += 1
    S.finish(landscape=n > 7)
    from openpyxl.styles import Alignment
    from openpyxl.utils import get_column_letter
    if tc == 2:
        a = ws.column_dimensions["A"]
        if (a.width or 0) >= 18:                # А кенг: фирма ёзуви А'да, логотипдан кейин (бўшлиқ қолмасин)
            for rr in (1, 2, 3):
                src = ws.cell(row=rr, column=2)
                dst = ws.cell(row=rr, column=1, value=src.value)
                dst.font = src.font.copy()
                dst.alignment = Alignment(horizontal="left", indent=6)
                src.value = None
        else:
            a.width = max(a.width or 0, 10)
    if kpis:                                    # рақам ромкаси тор бўлса «###» чиқмасин
        per = max(1, min(len(kpis), n))
        span = max(1, n // per)
        if span == 1:
            for i in range(per):
                d = ws.column_dimensions[get_column_letter(1 + i)]
                d.width = max(d.width or 0, 14)
    shift_right(ws)
    return respond(wb, name)


# ---------------------------------------------------------------- мижозга юбориладиган ҳисобот (бир мижоз)
def _logo_file():
    """Расмий логотип: «Кўриниш»да юкланган бўлса — шу, бўлмаса платформа белгиси."""
    import os
    try:
        import app as A
        ui = A.ui_settings()
        if ui.get("logo"):
            p = A.ui_logo_path(ui["logo"])
            if os.path.exists(p):
                return p
    except Exception:
        pass
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app256.png")
    return p if os.path.exists(p) else None


def client_statement(name, client, d1, d2, beg, taken, paid, end, goods, docs, firm=None):
    """Мижозга юбориладиган «Взаиморасчеты» (акт сверки): A4 тик варақ, логотип ва фирма маълумотлари, кўрсаткичлар,
    олган товар/хизматлари (диаграммасиз), ҳужжатлар бўйича ҳаракат, якуний қарз. Устунлар қатъий тўрда — ёзувлар устма-уст чиқмайди.
    goods=[(номи, ед, сони, сумма$)], docs=[(сана iso, ҳужжат, олди, тўлади, қарз)]."""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.chart.label import DataLabelList
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as CL
    firm = firm or {}
    wb = Workbook()
    ws = wb.active
    ws.title = _tr("Взаиморасчеты")[:31]
    ws.sheet_view.showGridLines = False
    W = {"A": 5, "B": 12, "C": 44, "D": 9, "E": 13, "F": 13, "G": 14}       # A №, B сана, C номи, D ед, E-G рақамлар
    for k, v in W.items():
        ws.column_dimensions[k].width = v
    thin, rule = Side(style="thin", color=INK), Side(style="thin", color=RULE)
    F = lambda **k: Font(name=FONT, color=k.pop("color", INK), **k)
    money = '#,##0.00;[Red]-#,##0.00;"–"'

    def put(r, c, v, font=None, fmt=None, al=None, border=None, merge_to=None):
        x = ws.cell(row=r, column=c, value=v)
        if font:
            x.font = font
        if fmt:
            x.number_format = fmt
        if al:
            x.alignment = al
        if border:
            x.border = border
        if merge_to:
            ws.merge_cells(start_row=r, start_column=c, end_row=r, end_column=merge_to)
        return x

    def fmt_dt(s):
        return f"{s[8:10]}.{s[5:7]}.{s[:4]}" if isinstance(s, str) and len(s) >= 10 else (s or "")

    # 1) шапка: логотип + фирма | ҳужжат номи
    for r in (1, 2, 3):
        ws.row_dimensions[r].height = 18
    lf = _logo_file()
    if lf:
        try:
            from openpyxl.drawing.image import Image as XI
            im = XI(lf)
            k = 52 / max(im.height, 1)
            im.height, im.width = 52, int(im.width * k)
            ws.add_image(im, "A1")
        except Exception:
            pass
    put(1, 3, firm.get("company") or "Lumberture", F(bold=True, size=16))
    put(2, 3, firm.get("phone") or "+998 33 409-60-70", F(size=9, color=MUTED))
    put(3, 3, firm.get("address") or "", F(size=9, color=MUTED))
    R = Alignment(horizontal="right", vertical="center")
    put(1, 5, _tr("Взаиморасчеты"), F(bold=True, size=14), al=R, merge_to=7)
    put(2, 5, f"{d1:%d.%m.%Y} — {d2:%d.%m.%Y}", F(size=9, color=MUTED), al=R, merge_to=7)
    put(3, 5, _tr("Тузилди") + ": " + datetime.now().strftime("%d.%m.%Y"), F(size=9, color=MUTED), al=R, merge_to=7)
    for c in range(1, 8):
        ws.cell(row=4, column=c).border = Border(top=Side(style="medium", color=INK))
    ws.row_dimensions[4].height = 8
    # 2) мижоз
    put(5, 1, _tr("Мижоз") + ":", F(size=10, color=MUTED), merge_to=2)
    put(5, 3, client, F(bold=True, size=12), merge_to=7)
    ws.row_dimensions[5].height = 20
    # 3) кўрсаткичлар — 4 та ромка: B, C, D:E, F:G
    boxes = [(1, 2, "Нач. сальдо", beg), (3, 3, "Олди (товар)", taken), (4, 5, "Тўлади", paid), (6, 7, "Кон. сальдо", end)]
    r0 = 7
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c1_, c2_, lbl, v in boxes:
        put(r0, c1_, _tr(lbl), F(size=9, color=MUTED), al=Alignment(horizontal="left", indent=1), merge_to=c2_ if c2_ > c1_ else None)
        put(r0 + 1, c1_, v, F(bold=True, size=13, color=("B3261E" if lbl == "Кон. сальдо" and v > 0.005 else INK)), fmt=money,
            al=Alignment(horizontal="left", indent=1), merge_to=c2_ if c2_ > c1_ else None)
        for rr in (r0, r0 + 1):
            for cc in range(c1_, c2_ + 1):
                ws.cell(row=rr, column=cc).border = Border(left=thin if cc == c1_ else None, right=thin if cc == c2_ else None,
                                                          top=thin if rr == r0 else None, bottom=thin if rr == r0 + 1 else None)
    ws.row_dimensions[r0 + 1].height = 22
    r = r0 + 3

    def head(r, cols):
        """cols=[(устун, охирги устун, ёзув, ўнгга)]"""
        for c1_, c2_, t, right in cols:
            put(r, c1_, _tr(t), F(bold=True, size=9, color=MUTED), al=Alignment(horizontal="right" if right else "left", vertical="bottom", wrap_text=True),
                merge_to=c2_ if c2_ > c1_ else None)
            for cc in range(c1_, c2_ + 1):
                ws.cell(row=r, column=cc).border = Border(bottom=Side(style="medium", color=GREEN))
        ws.row_dimensions[r].height = 18

    def line(r, last=7):
        for cc in range(1, last + 1):
            ws.cell(row=r, column=cc).border = Border(bottom=rule)

    def qfmt(v):
        return "#,##0" if isinstance(v, (int, float)) and abs(v - round(v)) < 1e-9 else "#,##0.###"

    # 4) товар ва хизматлар
    if goods:
        put(r, 1, _tr("Нима олди — товар ва хизматлар"), F(bold=True, size=11), merge_to=7); r += 1
        head(r, [(1, 1, "№", False), (2, 3, "Товар / хизмат", False), (4, 4, "Ед", False), (5, 5, "Сони", True), (6, 6, "Сумма $", True)]); r += 1
        g1 = r
        for i, (nm, un, q, u) in enumerate(goods, 1):
            put(r, 1, i, F(size=10, color=MUTED), al=Alignment(vertical="top"))
            put(r, 2, nm, F(size=10), al=Alignment(wrap_text=True, vertical="top"), merge_to=3)
            put(r, 4, un, F(size=10, color=MUTED), al=Alignment(vertical="top"))
            put(r, 5, q, F(size=10), fmt=qfmt(q), al=Alignment(horizontal="right", vertical="top"))
            put(r, 6, u, F(size=10), fmt=money, al=Alignment(horizontal="right", vertical="top"))
            if len(str(nm or "")) > 66:
                ws.row_dimensions[r].height = 27
            line(r, 6); r += 1
        g2 = r - 1
        put(r, 2, _tr("Жами"), F(bold=True, size=10), merge_to=3)
        put(r, 6, round(sum(g[3] for g in goods), 2), F(bold=True, size=10), fmt=money, al=Alignment(horizontal="right"))
        for cc in range(1, 7):
            ws.cell(row=r, column=cc).border = Border(top=thin)
        r += 2
    # 5) ҳужжатлар бўйича ҳаракат
    put(r, 1, _tr("Ҳужжатлар бўйича ҳаракат"), F(bold=True, size=11), merge_to=7); r += 1
    head(r, [(1, 1, "№", False), (2, 2, "Сана", False), (3, 4, "Ҳужжат", False), (5, 5, "Олди $", True), (6, 6, "Тўлади $", True), (7, 7, "Қарз $", True)]); r += 1
    put(r, 3, _tr("Нач. сальдо"), F(size=10, color=MUTED), merge_to=4)
    put(r, 7, beg, F(size=10), fmt=money, al=Alignment(horizontal="right"))
    line(r); r += 1
    for i, (dt, doc, a, p, bal) in enumerate(docs, 1):
        put(r, 1, i, F(size=10, color=MUTED))
        put(r, 2, fmt_dt(dt), F(size=10))
        put(r, 3, _trv(doc), F(size=10), merge_to=4)
        put(r, 5, a, F(size=10), fmt=money, al=Alignment(horizontal="right"))
        put(r, 6, p, F(size=10), fmt=money, al=Alignment(horizontal="right"))
        put(r, 7, bal, F(size=10), fmt=money, al=Alignment(horizontal="right"))
        line(r); r += 1
    if not docs:
        put(r, 3, _tr("Бу даврда ҳаракат йўқ"), F(size=10, color=MUTED, italic=True), merge_to=6); line(r); r += 1
    put(r, 3, _tr("Жами"), F(bold=True, size=10), merge_to=4)
    put(r, 5, taken, F(bold=True, size=10), fmt=money, al=Alignment(horizontal="right"))
    put(r, 6, paid, F(bold=True, size=10), fmt=money, al=Alignment(horizontal="right"))
    put(r, 7, end, F(bold=True, size=10), fmt=money, al=Alignment(horizontal="right"))
    for cc in range(1, 8):
        ws.cell(row=r, column=cc).border = Border(top=thin)
    r += 2
    # 6) якуний сатр
    if end > 0.005:
        t, v, col = _tr("Қарзингиз") + f" ({d2:%d.%m.%Y}):", end, "B3261E"
    elif end < -0.005:
        t, v, col = _tr("Аванс (сизнинг ҳисобингизда)") + f" ({d2:%d.%m.%Y}):", -end, GREEN
    else:
        t, v, col = _tr("Қарз йўқ") + f" ({d2:%d.%m.%Y})", None, GREEN
    put(r, 1, t, F(bold=True, size=12), merge_to=5)
    if v is not None:
        put(r, 6, v, F(bold=True, size=13, color=col), fmt='"$"#,##0.00', al=Alignment(horizontal="right"), merge_to=7)
    ws.row_dimensions[r].height = 22
    r += 2
    put(r, 1, _tr("Савол бўлса:") + " " + (firm.get("phone") or "+998 33 409-60-70") + " · Telegram: @Lumberture_bot", F(size=9, color=MUTED), merge_to=7)
    r += 1
    put(r, 1, COMPANY_I18N.get(_lang(), COMPANY), F(size=8, italic=True, color=MUTED), merge_to=7)
    # чоп этиш: A4 тик, кенглиги бир варақ
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.5
    ws.print_area = f"A1:G{r}"
    shift_right(ws)
    return respond(wb, name)
