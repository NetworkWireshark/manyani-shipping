"""Import selectable-text purchase-order tables from PDF files."""
import io
import math
import re
import unicodedata

from order_excel import parse_sheets


ALIASES = {
    'N°': 'NO', 'Nº': 'NO', 'N': 'NO', 'NUMBER': 'NO',
    'ARTICLE': 'DESCRIPTION', 'ARTICLE / DESCRIPTION': 'DESCRIPTION',
    'DESIGNATION': 'DESCRIPTION', 'QTE': 'QTY', 'QUANTITE': 'QTY',
    'UNITE': 'UNIT', 'PRIX UNITAIRE': 'PRICE', 'UNIT PRICE': 'PRICE',
    'REMISE': 'DISC%', 'DISCOUNT': 'DISC%', 'DISCOUNT %': 'DISC%',
    'MONTANT': 'AMOUNT', 'TOTAL NET': 'NETTOTAL', 'NET TOTAL': 'NETTOTAL',
    'MARQUE': 'MAKER/BRAND', 'BRAND': 'MAKER/BRAND',
}
NUMERIC = {'NO', 'QTY', 'QUANTITY', 'PRICE', 'DISC%', 'AMOUNT', 'NET PRICE', 'NETTOTAL'}


def normalized(value):
    text = ' '.join(str(value or '').split()).upper()
    text = ''.join(c for c in unicodedata.normalize('NFD', text) if not unicodedata.combining(c))
    return ALIASES.get(text, text)


def number(value):
    text = re.sub(r'[\s\u00a0\u202f%]', '', str(value or ''))
    if ',' in text and '.' in text:
        text = text.replace(',', '') if text.rfind('.') > text.rfind(',') else text.replace('.', '').replace(',', '.')
    elif ',' in text:
        text = text.replace(',', '.')
    if not re.fullmatch(r'\d+(?:\.\d+)?', text):
        raise ValueError('Nombre invalide dans le PDF : ' + str(value))
    result = float(text)
    if not math.isfinite(result):
        raise ValueError('Nombre invalide dans le PDF')
    return result


def table_rows(table, previous_header=None):
    header = previous_header
    result = []
    for row in table:
        labels = [normalized(cell) for cell in row]
        if 'DESCRIPTION' in labels and ('QTY' in labels or 'QUANTITY' in labels):
            header = labels
            result.append(header)
            continue
        if header is None or len(row) != len(header):
            continue
        if not result:
            result.append(header)
        cleaned = [' '.join(str(cell or '').split()) for cell in row]
        desc = cleaned[header.index('DESCRIPTION')]
        qty_index = header.index('QTY') if 'QTY' in header else header.index('QUANTITY')
        if not desc:
            continue
        if not cleaned[qty_index]:
            # Ignore totals and section headings, never turn them into articles.
            continue
        for index, key in enumerate(header):
            if key in NUMERIC and cleaned[index] and not (key == 'NO' and cleaned[index].upper() == 'G'):
                cleaned[index] = number(cleaned[index])
        result.append(cleaned)
    return result, header


def parse_pdf_order(content):
    import pdfplumber
    sheets = []
    header = None
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        if len(pdf.pages) > 100:
            raise ValueError('Le PDF doit contenir au maximum 100 pages.')
        for page_index, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            if not tables:
                tables = page.extract_tables({'vertical_strategy': 'text', 'horizontal_strategy': 'text'})
            for table_index, table in enumerate(tables, 1):
                rows, header = table_rows(table, header)
                if len(rows) > 1:
                    sheets.append((f'PDF page {page_index}, tableau {table_index}', rows))
    if not sheets:
        raise ValueError('Aucun tableau d\u2019articles lisible. Utilisez un PDF avec du texte et des colonnes Description et Quantite, ou importez le fichier Excel original.')
    order = parse_sheets(sheets)
    if all(isinstance(item.get('amount'), (int, float)) for item in order['items']):
        order['metadata']['total'] = sum(item['amount'] for item in order['items'])
    if all(isinstance(item.get('net_total'), (int, float)) for item in order['items']):
        order['metadata']['grand_total'] = sum(item['net_total'] for item in order['items'])
    return order
