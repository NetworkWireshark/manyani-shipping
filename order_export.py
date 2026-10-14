"""Excel and PDF export of one delivery's orders and loading history."""
import io
from datetime import datetime
from xml.sax.saxutils import escape

import order_tracking
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

PAGE = None
ORANGE = "F18700"
INK = "1D1D1B"
PERIODS = {
    "all": "Tous les chargements",
    "today": "Aujourd'hui",
    "7d": "7 derniers jours",
    "week": "Semaine en cours",
}
STATUSES = {
    "all": "Tous les statuts",
    "partial": "Partiellement chargés",
    "full": "Entièrement chargés",
    "remaining": "Reste à charger",
    "loaded": "Déjà chargés",
    "unloaded": "Non chargés",
}
STATUS_LABEL = {"unloaded": "Non chargé", "partial": "Partiel", "full": "Chargé"}
ARTICLE_HEADERS = [
    "Fichier", "Article", "N° commande", "Qté commandée", "Qté chargée", "Reste",
    "Statut", "Utilisateur", "Date de chargement", "Montant", "Total net",
]
HISTORY_HEADERS = [
    "Fichier", "Article", "N° commande", "Qté commandée", "Qté chargée", "Reste",
    "Date", "Utilisateur", "Modifications",
]

_fonts_ready = False


def fonts():
    global _fonts_ready, PAGE
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    PAGE = landscape(A4)
    if not _fonts_ready:
        pdfmetrics.registerFont(TTFont("Arial", r"C:\Windows\Fonts\arial.ttf"))
        pdfmetrics.registerFont(TTFont("Arial-Bold", r"C:\Windows\Fonts\arialbd.ttf"))
        _fonts_ready = True


def fr_number(value, money=False):
    number = float(value or 0)
    raw = f"{number:,.2f}" if money else f"{number:,.4f}".rstrip("0").rstrip(".")
    if raw in ("", "-"):
        raw = "0"
    sign = "-" if raw.startswith("-") else ""
    raw = raw.lstrip("-")
    whole, dot, frac = raw.partition(".")
    return sign + whole.replace(",", " ") + (("," + frac) if dot else "")


def fr_when(value):
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return str(value)
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%d/%m/%Y %H:%M:%S")


def status_allows(load_status, wanted):
    if wanted in (None, "", "all"):
        return True
    if wanted == "loaded":
        return load_status in ("partial", "full")
    if wanted == "remaining":
        return load_status in ("unloaded", "partial")
    return load_status == wanted


def name_allows(item, query):
    query = (query or "").strip().casefold()
    if not query:
        return True
    haystack = " ".join(str(item.get(key) or "") for key in ("description", "code", "group", "remark", "no"))
    return query in haystack.casefold()


def trail_text(events, item_index, upto_id=None):
    parts = []
    for event in events:
        if event["item_index"] != item_index:
            continue
        if upto_id is not None and event["id"] > upto_id:
            continue
        when = fr_when(event["occurred_at"])
        if event["action"] == "LOAD_ADJUSTED":
            change = f"corrigé {fr_number(event['before_loaded'])} → {fr_number(event['after_loaded'])}"
        elif event["action"] == "UNLOADED":
            change = "annulé"
        elif event["action"] == "LOADED":
            change = f"chargé {fr_number(event['after_loaded'])}"
        else:
            change = f"+{fr_number(event['delta'])} ({fr_number(event['before_loaded'])} → {fr_number(event['after_loaded'])})"
        parts.append(f"{event['username']} {when} : {change}")
    return " | ".join(parts)


def filter_label(filters):
    period = PERIODS.get(filters.get("period") or "all", "Tous les chargements")
    status = STATUSES.get(filters.get("status") or "all", "Tous les statuts")
    user = filters.get("user") or "Tous les utilisateurs"
    query = filters.get("q") or "Tous les articles"
    return f"Filtre historique : {period} · {status} · {user} · {query}"


def collect(conn, delivery_id, filters):
    rows = conn.execute(
        "SELECT id, filename, created_at, parsed_json, content, chargeur FROM order_files WHERE delivery_id=? ORDER BY id",
        (delivery_id,),
    ).fetchall()
    blocks = []
    for row in rows:
        try:
            order = order_tracking.enrich(conn, row["id"], order_tracking.read_order(row))
        except Exception:
            continue
        events = order_tracking.load_events(conn, row["id"])
        articles = [item for item in order["items"] if name_allows(item, filters.get("q")) and status_allows(item.get("load_status"), filters.get("status"))]
        visible = {item["originalIndex"] for item in articles}
        records = []
        for event in events:
            if event["item_index"] not in visible:
                continue
            if filters.get("user") and event["username"] != filters["user"]:
                continue
            if not order_tracking.in_period(event["occurred_at"], filters.get("period") or "all"):
                continue
            records.append(event)
        blocks.append({
            "filename": row["filename"],
            "chargeur": row["chargeur"] or "",
            "created_at": row["created_at"],
            "order": order,
            "articles": articles,
            "events": events,
            "records": records,
        })
    return blocks


def order_ref(block):
    meta = block["order"]["metadata"]
    return str(meta.get("reference") or block["filename"])


def article_line(block, item):
    meta = block["order"]["metadata"]
    ref = order_ref(block)
    line = item.get("no")
    number = f"{ref} · {line}" if line not in (None, "") else ref
    return [
        block["filename"],
        item.get("description") or "",
        number,
        fr_number(item.get("original_quantity")),
        fr_number(item.get("loaded_quantity")),
        fr_number(item.get("remaining")),
        STATUS_LABEL.get(item.get("load_status"), ""),
        item.get("loaded_by") or "",
        fr_when(item.get("loaded_at")),
        fr_number(item.get("amount"), True),
        fr_number(item.get("net_total") if item.get("net_total") is not None else item.get("amount"), True),
    ]


def history_line(block, event):
    item = block["order"]["items"][event["item_index"]]
    ref = order_ref(block)
    line = item.get("no")
    number = f"{ref} · {line}" if line not in (None, "") else ref
    loaded = event["after_loaded"] if event["action"] == "LOAD_ADJUSTED" else event["delta"]
    return [
        block["filename"],
        event["article"],
        number,
        fr_number(event["original_quantity"]),
        fr_number(loaded),
        fr_number(event["remaining"]),
        fr_when(event["occurred_at"]),
        event["username"],
        trail_text(block["events"], event["item_index"], event["id"]),
    ]


def heading_lines(delivery, blocks, filters):
    meta = blocks[0]["order"]["metadata"] if blocks else {}
    vessel = meta.get("vessel") or delivery["vessel"] or ""
    refs = ", ".join(dict.fromkeys(order_ref(block) for block in blocks)) or "—"
    dates = ", ".join(dict.fromkeys(str(block["order"]["metadata"].get("delivery_date") or "") for block in blocks if block["order"]["metadata"].get("delivery_date"))) or "—"
    currency = meta.get("currency") or ""
    return [
        "MANYANI SHIPPING",
        f"Navire : {vessel}",
        f"Commande : {refs}",
        f"Date : {dates}",
        f"Devise : {currency or '—'}",
        filter_label(filters),
    ]


def totals(blocks):
    gross = net = 0.0
    currency = ""
    for block in blocks:
        currency = currency or (block["order"]["metadata"].get("currency") or "")
        for item in block["articles"]:
            gross += float(item.get("amount") or 0)
            net += float(item.get("net_total") if item.get("net_total") is not None else item.get("amount") or 0)
    return gross, net, currency


def xlsx_bytes(delivery, blocks, filters):
    book = Workbook()
    articles = book.active
    articles.title = "Articles"
    history = book.create_sheet("Historique")
    fill = PatternFill("solid", fgColor=ORANGE)
    ink = Font(bold=True, color="FFFFFF", name="Calibri")
    black = Font(bold=True, color=INK, name="Calibri")
    thin = Border(
        left=Side(style="thin", color="E5DED3"),
        right=Side(style="thin", color="E5DED3"),
        top=Side(style="thin", color="E5DED3"),
        bottom=Side(style="thin", color="E5DED3"),
    )
    wrap = Alignment(wrap_text=True, vertical="center")

    def banner(sheet, last_col):
        for line in heading_lines(delivery, blocks, filters):
            sheet.append([line])
            sheet.merge_cells(start_row=sheet.max_row, start_column=1, end_row=sheet.max_row, end_column=last_col)
            sheet.cell(sheet.max_row, 1).font = Font(bold=True, color=INK, name="Calibri", size=12 if line == "MANYANI SHIPPING" else 11)
        sheet.append([])

    def write_table(sheet, headers, rows):
        sheet.append(headers)
        header_row = sheet.max_row
        for cell in sheet[header_row]:
            cell.fill = fill
            cell.font = ink
            cell.alignment = wrap
            cell.border = thin
        for row in rows:
            sheet.append(row)
            for cell in sheet[sheet.max_row]:
                cell.alignment = wrap
                cell.border = thin
                cell.font = Font(name="Calibri", size=10)
        sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{sheet.max_row}"
        sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{max(sheet.max_row, header_row)}"
        sheet.freeze_panes = f"A{header_row + 1}"
        for index, header in enumerate(headers, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = 18 if header != "Modifications" else 55
            if header == "Article":
                sheet.column_dimensions[get_column_letter(index)].width = 36

    banner(articles, len(ARTICLE_HEADERS))
    write_table(articles, ARTICLE_HEADERS, [article_line(block, item) for block in blocks for item in block["articles"]])
    gross, net, currency = totals(blocks)
    articles.append([])
    articles.append(["Total montant", fr_number(gross, True), currency])
    articles.append(["Total net", fr_number(net, True), currency])
    for row in articles.iter_rows(min_row=articles.max_row - 1, max_row=articles.max_row, max_col=2):
        for cell in row:
            cell.font = black

    banner(history, len(HISTORY_HEADERS))
    write_table(history, HISTORY_HEADERS, [history_line(block, event) for block in blocks for event in block["records"]])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def pdf_bytes(delivery, blocks, filters):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    fonts()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=PAGE, leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=18 * mm, bottomMargin=12 * mm, title="MANYANI SHIPPING", author="MANYANI SHIPPING",
    )
    body = ParagraphStyle("Body", fontName="Arial", fontSize=8, leading=11, textColor=colors.HexColor("#" + INK))
    title = ParagraphStyle("TitleM", fontName="Arial-Bold", fontSize=14, leading=18, textColor=colors.HexColor("#" + INK))
    head = ParagraphStyle("Head", fontName="Arial-Bold", fontSize=7.5, leading=9, textColor=colors.white)
    cell = ParagraphStyle("Cell", fontName="Arial", fontSize=7.5, leading=9, textColor=colors.HexColor("#" + INK))
    story = [Paragraph("MANYANI SHIPPING", title), Spacer(1, 4)]
    for line in heading_lines(delivery, blocks, filters)[1:]:
        story.append(Paragraph(escape(line), body))
    story.append(Spacer(1, 8))

    def flow_table(headers, rows, widths):
        data = [[Paragraph(escape(header), head) for header in headers]]
        for row in rows:
            data.append([Paragraph(escape(str(value)), cell) for value in row])
        if len(data) == 1:
            data.append([Paragraph("—", cell) for _ in headers])
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + ORANGE)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E5DED3")),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return table

    usable = PAGE[0] - 24 * mm
    article_widths = [usable * part for part in (0.12, 0.18, 0.12, 0.08, 0.08, 0.07, 0.08, 0.09, 0.1, 0.08)]
    # 10 columns: drop the filename from the PDF article row to keep the page readable, keep it in the section title.
    story.append(Paragraph("Articles", title))
    pdf_article_headers = ARTICLE_HEADERS[1:]
    pdf_rows = []
    for block in blocks:
        for item in block["articles"]:
            pdf_rows.append(article_line(block, item)[1:])
    story.append(Spacer(1, 4))
    story.append(flow_table(pdf_article_headers, pdf_rows, article_widths))
    gross, net, currency = totals(blocks)
    story.append(Spacer(1, 6))
    story.append(Paragraph(escape(f"Total montant : {fr_number(gross, True)} {currency}"), body))
    story.append(Paragraph(escape(f"Total net : {fr_number(net, True)} {currency}"), body))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Historique des chargements", title))
    story.append(Spacer(1, 4))
    history_widths = [usable * part for part in (0.16, 0.12, 0.08, 0.08, 0.07, 0.12, 0.09, 0.28)]
    history_rows = [history_line(block, event)[1:] for block in blocks for event in block["records"]]
    story.append(flow_table(HISTORY_HEADERS[1:], history_rows, history_widths))

    def paint(canvas, doc_):
        canvas.saveState()
        width, height = PAGE
        canvas.setFillColor(colors.HexColor("#" + ORANGE))
        canvas.rect(0, height - 12 * mm, width, 12 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#" + INK))
        canvas.setFont("Arial-Bold", 11)
        canvas.drawString(12 * mm, height - 8 * mm, "MANYANI SHIPPING")
        canvas.setFont("Arial", 8)
        canvas.drawRightString(width - 12 * mm, 6 * mm, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=paint, onLaterPages=paint)
    return buffer.getvalue()


def build(conn, delivery, filters, kind):
    blocks = collect(conn, delivery["id"], filters)
    if kind == "pdf":
        return pdf_bytes(delivery, blocks, filters), "application/pdf", "manyani-commande.pdf"
    return xlsx_bytes(delivery, blocks, filters), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "manyani-commande.xlsx"
