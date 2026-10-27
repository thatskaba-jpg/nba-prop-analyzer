"""
xlsx_min.py — write a good-looking .xlsx workbook with only the standard library.

(openpyxl/xlsxwriter aren't installed and can't be added offline.)

Each sheet: optional title + subtitle rows, a dark header row with white bold
text, frozen header, filter dropdowns on every column, zebra rows, thin grey
borders, centered short columns, wrapped long text, green/red/amber cells for
✅ / ❌ / ⏳, percent formatting, hidden gridlines and a colored tab.

write_xlsx([{"name": "Tab", "rows": [ {col: value, ...} ], "title": "...",
             "subtitle": "...", "tab_color": "FF1F3864",
             "percent": ["Win %"]}, ...]) -> bytes
"""
import io
import re
import zipfile
from xml.sax.saxutils import escape

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# ---- palette ----
NAVY, WHITE, ZEBRA = "FF1F3864", "FFFFFFFF", "FFF3F6FA"
GREEN, RED, AMBER = "FFDDF0DD", "FFF9D6D5", "FFFFF1C9"
GRID = "FFD0D7E2"
TAB_COLORS = {"Summary": "FF1F3864", "All": "FF2E75B6", "default": "FF8FAADC"}


def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def clean_sheet_name(name: str, used: set) -> str:
    n = _BAD_SHEET.sub("-", str(name)).strip("'") or "Sheet"
    n = n[:31]
    base, k = n, 2
    while n.lower() in used:
        suffix = f" ({k})"
        n = base[:31 - len(suffix)] + suffix
        k += 1
    used.add(n.lower())
    return n


def _text(v) -> str:
    return escape(_CTRL.sub("", str(v)))


class Styles:
    """Registry that builds styles.xml from the (font, fill, align, fmt) combos
    the cells actually use."""
    FONTS = {  # name -> xml
        "base": '<font><sz val="11"/><name val="Calibri"/></font>',
        "head": f'<font><b/><sz val="11"/><color rgb="{WHITE}"/><name val="Calibri"/></font>',
        "title": f'<font><b/><sz val="16"/><color rgb="{NAVY}"/><name val="Calibri"/></font>',
        "sub": '<font><i/><sz val="10"/><color rgb="FF6B7280"/><name val="Calibri"/></font>',
        "bold": '<font><b/><sz val="11"/><name val="Calibri"/></font>',
        "foot": f'<font><b/><sz val="11"/><color rgb="{NAVY}"/><name val="Calibri"/></font>',
    }
    FILLS = {  # name -> xml (index 0/1 reserved by the spec)
        "none": '<fill><patternFill patternType="none"/></fill>',
        "gray125": '<fill><patternFill patternType="gray125"/></fill>',
        **{n: f'<fill><patternFill patternType="solid"><fgColor rgb="{c}"/>'
              '<bgColor indexed="64"/></patternFill></fill>'
           for n, c in (("navy", NAVY), ("zebra", ZEBRA), ("green", GREEN),
                        ("red", RED), ("amber", AMBER), ("white", WHITE),
                        ("foot", "FFD9E2F3"))},
    }
    NUMFMTS = {"general": 0, "pct": 164}

    def __init__(self):
        self.fonts, self.fills = list(self.FONTS), list(self.FILLS)
        self.combos = []
        self.get("base", "none", "left", "general", False)    # style 0 = default

    def get(self, font, fill, align, fmt, border=True, wrap=False):
        key = (font, fill, align, fmt, border, wrap)
        if key not in self.combos:
            self.combos.append(key)
        return self.combos.index(key)

    def xml(self):
        xfs = []
        for font, fill, align, fmt, border, wrap in self.combos:
            al = (f'<alignment horizontal="{align}" vertical="center"'
                  + (' wrapText="1"' if wrap else "") + "/>")
            xfs.append(
                f'<xf numFmtId="{self.NUMFMTS[fmt]}" fontId="{self.fonts.index(font)}" '
                f'fillId="{self.fills.index(fill)}" borderId="{1 if border else 0}" xfId="0" '
                'applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1" '
                f'applyNumberFormat="1">{al}</xf>')
        side = f'<{{0}} style="thin"><color rgb="{GRID}"/></{{0}}>'
        border = ("<border>" + "".join(side.format(s) for s in
                                       ("left", "right", "top", "bottom")) + "<diagonal/></border>")
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<styleSheet xmlns="{NS}">'
            '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0%"/></numFmts>'
            f'<fonts count="{len(self.fonts)}">' + "".join(self.FONTS[f] for f in self.fonts) + "</fonts>"
            f'<fills count="{len(self.fills)}">' + "".join(self.FILLS[f] for f in self.fills) + "</fills>"
            '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
            + border + "</borders>"
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            f'<cellXfs count="{len(xfs)}">' + "".join(xfs) + "</cellXfs>"
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            "</styleSheet>")


def _is_status(v):
    s = str(v)
    if len(s) > 22:
        return None
    if "✅" in s:
        return "green"
    if "❌" in s:
        return "red"
    if "⏳" in s or "➖" in s:
        return "amber"
    return None


def _cell(ref, v, style):
    if v is None or v == "":
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(v, bool):
        v = "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
        return f'<c r="{ref}" s="{style}"><v>{v!r}</v></c>'
    return (f'<c r="{ref}" s="{style}" t="inlineStr"><is>'
            f'<t xml:space="preserve">{_text(v)}</t></is></c>')


def _color_scale(sqref, prio):
    """Red -> yellow -> green fill for a percent column (40% / 50% / 65%)."""
    return (f'<conditionalFormatting sqref="{sqref}"><cfRule type="colorScale" '
            f'priority="{prio}"><colorScale><cfvo type="num" val="0.4"/>'
            '<cfvo type="num" val="0.5"/><cfvo type="num" val="0.65"/>'
            '<color rgb="FFF4B6B3"/><color rgb="FFFFF2B3"/><color rgb="FFA9D8A9"/>'
            "</colorScale></cfRule></conditionalFormatting>")


def _sheet_xml(sh, headers, rows, st: Styles, tab_color):
    ncols = max(len(headers), 1)
    title, subtitle = sh.get("title"), sh.get("subtitle")
    hdr_row = 1 + (2 if title else 0)
    pct_cols = set(sh.get("percent", []))
    bold_cols = set(sh.get("bold_cols", []))
    scale_cols = list(sh.get("color_scale", []))
    freeze_cols = int(sh.get("freeze_cols", 0) or 0)
    footer = list(sh.get("footer") or [])
    # ---- column widths + alignment per column ----
    widths, center, wrap = [], [], []
    for h in headers:
        vals = [str(r.get(h)) for r in rows + footer if r.get(h) is not None]
        longest = max([len(str(h))] + [len(v) for v in vals] or [8])
        widths.append(min(max(longest + 3, 9), 46))
        short = longest <= 16 or h in pct_cols
        center.append(short)
        wrap.append(longest > 46)
    cols = "".join(f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
                   for i, w in enumerate(widths))
    s_title = st.get("title", "none", "left", "general", False)
    s_sub = st.get("sub", "none", "left", "general", False)
    s_head = st.get("head", "navy", "center", "general", True, True)

    if freeze_cols:
        pane = (f'<pane xSplit="{freeze_cols}" ySplit="{hdr_row}" '
                f'topLeftCell="{col_letter(freeze_cols)}{hdr_row + 1}" '
                'activePane="bottomRight" state="frozen"/>'
                '<selection pane="topRight"/><selection pane="bottomLeft"/>'
                '<selection pane="bottomRight"/>')
    else:
        pane = (f'<pane ySplit="{hdr_row}" topLeftCell="A{hdr_row + 1}" '
                'activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft"/>')
    out = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<worksheet xmlns="{NS}" xmlns:r="{RNS}">',
           f'<sheetPr><tabColor rgb="{tab_color}"/><pageSetUpPr fitToPage="1"/></sheetPr>',
           f'<sheetViews><sheetView showGridLines="0" workbookViewId="0">{pane}</sheetView></sheetViews>',
           '<sheetFormatPr defaultRowHeight="18"/>', f"<cols>{cols}</cols>", "<sheetData>"]
    merges = []
    if title:
        out.append(f'<row r="1" ht="30" customHeight="1">{_cell("A1", title, s_title)}</row>')
        out.append(f'<row r="2" ht="18" customHeight="1">{_cell("A2", subtitle or "", s_sub)}</row>')
        if ncols > 1:
            merges += [f"A1:{col_letter(ncols - 1)}1", f"A2:{col_letter(ncols - 1)}2"]
    out.append(f'<row r="{hdr_row}" ht="28" customHeight="1">' + "".join(
        _cell(f"{col_letter(i)}{hdr_row}", h, s_head) for i, h in enumerate(headers)) + "</row>")
    for k, r in enumerate(rows):
        ri = hdr_row + 1 + k
        zebra = "zebra" if k % 2 else "white"
        cells = []
        for i, h in enumerate(headers):
            v = r.get(h)
            fill, font, fmt = zebra, "base", "general"
            kind = _is_status(v) if isinstance(v, str) else None
            if kind:
                fill = kind
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if h in pct_cols:
                    fmt = "pct"
                align = "center"
            else:
                align = "center" if (center[i] or kind) else "left"
            if h in bold_cols:
                font = "bold"
            cells.append(_cell(f"{col_letter(i)}{ri}", v,
                               st.get(font, fill, align, fmt, True, wrap[i] and align == "left")))
        out.append(f'<row r="{ri}" ht="21" customHeight="1">{"".join(cells)}</row>')
    last_data = hdr_row + len(rows)
    # ---- totals UNDER the table (outside the filter range) ----
    for j, r in enumerate(footer):
        ri = last_data + 2 + j                      # one blank row of space
        cells = []
        for i, h in enumerate(headers):
            v = r.get(h)
            cells.append(_cell(f"{col_letter(i)}{ri}", v, st.get(
                "foot", "foot", "left" if i < 2 else "center", "general", True, False)))
        out.append(f'<row r="{ri}" ht="24" customHeight="1">{"".join(cells)}</row>')
    out.append("</sheetData>")
    last = f"{col_letter(ncols - 1)}{max(last_data, hdr_row)}"
    out.append(f'<autoFilter ref="A{hdr_row}:{last}"/>')
    if merges:
        out.append(f'<mergeCells count="{len(merges)}">'
                   + "".join(f'<mergeCell ref="{m}"/>' for m in merges) + "</mergeCells>")
    prio = 1
    for h in scale_cols:
        if h in headers and rows:
            c = col_letter(headers.index(h))
            out.append(_color_scale(f"{c}{hdr_row + 1}:{c}{last_data}", prio))
            prio += 1
    out.append('<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>')
    out.append('<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>')
    out.append("</worksheet>")
    return "".join(out), f"A{hdr_row}:{last}"


def write_xlsx(sheets) -> bytes:
    used, parts, names = set(), [], []
    st = Styles()
    for sh in sheets:
        rows = list(sh.get("rows") or [])
        headers = list(rows[0].keys()) if rows else [""]
        for r in rows[1:]:
            for k in r:
                if k not in headers:
                    headers.append(k)
        nm = clean_sheet_name(sh.get("name", "Sheet"), used)
        color = sh.get("tab_color") or TAB_COLORS.get(nm, TAB_COLORS["default"])
        xml, rng = _sheet_xml(sh, headers, rows, st, color)
        names.append((nm, rng))
        parts.append(xml)
    if not parts:
        xml, rng = _sheet_xml({}, [""], [], st, TAB_COLORS["default"])
        names.append((clean_sheet_name("Sheet1", used), rng))
        parts.append(xml)
    n = len(parts)
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                    for i in range(1, n + 1))
          + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
          "</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>")
    sheets_xml = "".join(
        '<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(nm, {'"': "&quot;"}), i, i)
        for i, (nm, _) in enumerate(names, start=1))

    def _abs(rng):                       # "A3:H20" -> "$A$3:$H$20"
        return ":".join("$%s$%s" % re.match(r"([A-Z]+)(\d+)$", p).groups()
                        for p in rng.split(":"))
    defined = "".join(
        '<definedName name="_xlnm._FilterDatabase" localSheetId="%d" hidden="1">'
        "'%s'!%s</definedName>" % (i - 1, escape(nm), _abs(rng))
        for i, (nm, rng) in enumerate(names, start=1))
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          f'<workbook xmlns="{NS}" xmlns:r="{RNS}"><bookViews><workbookView/></bookViews>'
          f"<sheets>{sheets_xml}</sheets><definedNames>{defined}</definedNames></workbook>")
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                         for i in range(1, n + 1))
               + f'<Relationship Id="rId{n+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               "</Relationships>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", st.xml())
        for i, xml in enumerate(parts, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    return buf.getvalue()
