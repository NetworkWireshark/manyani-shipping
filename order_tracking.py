"""Persistent article state, optimistic concurrency and append-only audit history."""
import json
import math
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from order_excel import parse_order

SCHEMA_VERSION = 2
MONEY_FIELDS = ('price', 'discount', 'amount', 'net_total')


def migrate(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='article_tracking_migrations'").fetchone()
        if exists and conn.execute("SELECT 1 FROM article_tracking_migrations WHERE version=?", (SCHEMA_VERSION,)).fetchone():
            ensure_load_columns(conn)
            ensure_availability_column(conn)
            conn.commit()
            return
        tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if tables:
            folder = os.path.join(os.path.dirname(path), 'backups')
            os.makedirs(folder, exist_ok=True)
            backup = os.path.join(folder, 'before-article-tracking-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite')
            with closing(sqlite3.connect(backup)) as target:
                conn.backup(target)
        conn.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS article_tracking_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS order_item_state (
                file_id INTEGER NOT NULL REFERENCES order_files(id) ON DELETE CASCADE,
                item_index INTEGER NOT NULL,
                original_quantity REAL NOT NULL,
                quantity REAL NOT NULL,
                modified INTEGER NOT NULL DEFAULT 0,
                quantity_modified_by TEXT,
                quantity_modified_at TEXT,
                loaded INTEGER NOT NULL DEFAULT 0,
                loaded_by TEXT,
                loaded_at TEXT,
                delivered INTEGER NOT NULL DEFAULT 0,
                delivered_by TEXT,
                delivered_at TEXT,
                price REAL,
                discount REAL,
                net_price REAL,
                money_modified INTEGER NOT NULL DEFAULT 0,
                money_modified_by TEXT,
                money_modified_at TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(file_id, item_index)
            );
            CREATE TABLE IF NOT EXISTS order_item_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL,
                item_index INTEGER NOT NULL,
                delivery_id INTEGER NOT NULL,
                article TEXT NOT NULL,
                reference TEXT NOT NULL,
                original_quantity REAL NOT NULL,
                before_quantity REAL NOT NULL,
                after_quantity REAL NOT NULL,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                before_value TEXT NOT NULL,
                after_value TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                version INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS order_history_item ON order_item_history(file_id,item_index,id);
            CREATE INDEX IF NOT EXISTS order_state_loaded ON order_item_state(loaded,file_id);
            CREATE TRIGGER IF NOT EXISTS order_history_no_update BEFORE UPDATE ON order_item_history
                BEGIN SELECT RAISE(ABORT, 'Article history is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS order_history_no_delete BEFORE DELETE ON order_item_history
                BEGIN SELECT RAISE(ABORT, 'Article history is append-only'); END;
            COMMIT;
        """)
        ensure_money_columns(conn)
        ensure_load_columns(conn)
        ensure_availability_column(conn)
        conn.execute("INSERT OR IGNORE INTO article_tracking_migrations VALUES(?,?)", (SCHEMA_VERSION, datetime.now().astimezone().isoformat(timespec='microseconds')))
        conn.commit()


def ensure_money_columns(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='order_item_state'").fetchone():
        return
    present = {row[1] for row in conn.execute('PRAGMA table_info(order_item_state)')}
    for name, declaration in (
        ('price', 'REAL'), ('discount', 'REAL'), ('net_price', 'REAL'),
        ('money_modified', 'INTEGER NOT NULL DEFAULT 0'),
        ('money_modified_by', 'TEXT'), ('money_modified_at', 'TEXT'),
    ):
        if name not in present:
            conn.execute(f'ALTER TABLE order_item_state ADD COLUMN {name} {declaration}')


def ensure_availability_column(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='order_item_state'").fetchone():
        return
    present = {row[1] for row in conn.execute('PRAGMA table_info(order_item_state)')}
    if 'availability' not in present:
        conn.execute("ALTER TABLE order_item_state ADD COLUMN availability TEXT DEFAULT 'check'")
    conn.execute("UPDATE order_item_state SET availability='check' WHERE availability IS NULL OR availability=''")


def ensure_load_columns(conn):
    # A previous Chargé flag is kept as a full load of the working quantity.
    # original_quantity is the Excel value and is never written here.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='order_item_state'").fetchone():
        return
    present = {row[1] for row in conn.execute('PRAGMA table_info(order_item_state)')}
    if 'loaded_quantity' not in present:
        conn.execute('ALTER TABLE order_item_state ADD COLUMN loaded_quantity REAL')
    conn.execute("""UPDATE order_item_state SET loaded_quantity = quantity
        WHERE loaded = 1 AND loaded_quantity IS NULL""")


def _stored_price_missing(order):
    items = order.get('items') if isinstance(order, dict) else None
    if not items:
        return False
    for item in items:
        qty = item.get('quantity')
        if isinstance(qty, bool) or not isinstance(qty, (int, float)) or qty == 0:
            continue
        price = item.get('price')
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            return True
    return False


def _same_articles(stored, fresh):
    old = stored.get('items') or []
    new = fresh.get('items') or []
    if len(old) != len(new):
        return False
    for left, right in zip(old, new):
        if str(left.get('description') or '').strip() != str(right.get('description') or '').strip():
            return False
    return True


def read_order(row):
    # Older Excel imports stored the quantity and left the unit price empty.
    stored = json.loads(row['parsed_json']) if row['parsed_json'] else None
    if stored is not None and not _stored_price_missing(stored):
        return stored
    content = row['content'] if hasattr(row, 'keys') and 'content' in row.keys() else None
    if content:
        try:
            fresh = parse_order(content)
        except Exception:
            if stored is not None:
                return stored
            raise
        if stored is not None and not _same_articles(stored, fresh):
            return stored
        if stored is not None:
            metadata = dict(stored.get('metadata') or {})
            metadata.update({key: value for key, value in (fresh.get('metadata') or {}).items() if value not in (None, '')})
            fresh['metadata'] = metadata
        return fresh
    if stored is not None:
        return stored
    return parse_order(row['content'])


def seed_items(conn, file_id, order):
    for index, item in enumerate(order['items']):
        quantity = float(item.get('original_quantity', item['quantity']))
        conn.execute("""INSERT OR IGNORE INTO order_item_state
            (file_id,item_index,original_quantity,quantity,delivered,delivered_at)
            VALUES(?,?,?,?,?,?)""", (file_id,index,quantity,float(item['quantity']),int(bool(item.get('delivered'))),item.get('delivered_at')))


def prime(conn):
    for row in conn.execute('SELECT id,parsed_json,content FROM order_files').fetchall():
        try:
            order = read_order(row)
        except Exception:
            continue
        seed_items(conn,row['id'],order)


def _number(value, default=0.0):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        return default
    return float(value)


def enrich(conn, file_id, order):
    # New imports and existing imports use the same stable file/index identity.
    seed_items(conn,file_id,order)
    states = {row['item_index']: dict(row) for row in conn.execute('SELECT * FROM order_item_state WHERE file_id=?',(file_id,))}
    for index,item in enumerate(order['items']):
        state = states[index]
        item['original_price'] = item.get('price')
        item['original_discount'] = item.get('discount')
        item['original_amount'] = item.get('amount')
        item['original_net_total'] = item.get('net_total')
        for key,value in state.items():
            if key in ('file_id','item_index'):
                continue
            if key in ('price','discount','net_price') and value is None:
                continue
            item[key] = value
        # A failed import stored price 0. Keep a remise typed afterwards and show the Excel unit price.
        excel_price = item.get('original_price')
        state_price = state.get('price')
        excel_price = excel_price if isinstance(excel_price,(int,float)) and not isinstance(excel_price,bool) and math.isfinite(excel_price) else None
        state_price = state_price if isinstance(state_price,(int,float)) and not isinstance(state_price,bool) and math.isfinite(state_price) else None
        if excel_price is not None and excel_price > 0 and (state_price is None or abs(state_price) < 1e-9):
            item['price'] = excel_price
        for key in ('loaded','delivered','modified','money_modified'):
            item[key] = bool(item.get(key))
        item['originalIndex'] = index
        quantity = _number(item.get('quantity'))
        raw_loaded = item.get('loaded_quantity')
        loaded_qty = quantity if raw_loaded is None and item['loaded'] else _number(raw_loaded)
        item['loaded_quantity'] = loaded_qty
        item['remaining'] = max(0.0, quantity - loaded_qty)
        if loaded_qty <= 1e-9:
            item['load_status'] = 'unloaded'
            item['loaded'] = False
        elif quantity > 0 and loaded_qty + 1e-6 < quantity:
            item['load_status'] = 'partial'
            item['loaded'] = True
        else:
            item['load_status'] = 'full'
            item['loaded'] = True
        if item['money_modified']:
            price = _number(item.get('price'))
            discount = _number(item.get('discount'))
            quantity = _number(item.get('quantity'))
            if state.get('net_price') is None:
                item['net_price'] = price*(1-discount/100)
            item['amount'] = round(price*quantity,6)
            item['net_total'] = round(_number(item.get('net_price'))*quantity,6)
        elif item['modified']:
            item['amount'] = round(_number(item.get('price'))*item['quantity'],6)
            unit_net = item.get('net_price')
            if not isinstance(unit_net,(int,float)) or isinstance(unit_net,bool):
                unit_net = _number(item.get('price'))*(1-_number(item.get('discount'))/100)
            item['net_total'] = round(unit_net*item['quantity'],6)
    return order


def reference(item, filename):
    return str(item.get('code') or item.get('group') or filename)


class ItemError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def change(conn, delivery_id, file_id, index, payload, username):
    if not isinstance(payload,dict) or type(payload.get('expected_version')) is not int or payload['expected_version'] < 0:
        raise ItemError('Version requise. Actualisez la liste avant de modifier.',409)
    fields = [key for key in ('quantity','loaded','delivered','load_add','load_set',*MONEY_FIELDS) if key in payload]
    if len(fields) != 1:
        raise ItemError('Une seule action est autorisee par confirmation.')
    field = fields[0]
    value = payload[field]
    if field == 'quantity':
        if type(value) not in (int,float) or not math.isfinite(value) or value < 0 or value > 1000000000:
            raise ItemError('Quantite invalide : entre 0 et 1 000 000 000.')
    elif field == 'discount':
        if type(value) not in (int,float) or not math.isfinite(value) or value < 0 or value > 100:
            raise ItemError('Remise invalide : entre 0 et 100 %.')
    elif field in MONEY_FIELDS:
        if type(value) not in (int,float) or not math.isfinite(value) or value < 0 or value > 1000000000000:
            raise ItemError('Montant invalide : entre 0 et 1 000 000 000 000.')
    elif field in ('load_add','load_set'):
        if type(value) not in (int,float) or not math.isfinite(value) or value > 1000000000 or (field == 'load_add' and value <= 0) or (field == 'load_set' and value < 0):
            raise ItemError('Quantite chargee invalide.')
    elif field == 'loaded':
        raise ItemError('Indiquez la quantite chargee.')
    elif type(value) is not bool:
        raise ItemError('Le statut doit etre vrai ou faux.')
    # Serializes writes while the version prevents stale clients from overwriting.
    conn.execute('BEGIN IMMEDIATE')
    row = conn.execute("""SELECT f.* FROM order_files f JOIN deliveries d ON d.id=f.delivery_id
        WHERE f.delivery_id=? AND f.id=?""",(delivery_id,file_id)).fetchone()
    if not row:
        raise ItemError('Commande introuvable',404)
    order = enrich(conn,file_id,read_order(row))
    if index < 0 or index >= len(order['items']):
        raise ItemError('Article introuvable',404)
    item = order['items'][index]
    if item['version'] != payload['expected_version']:
        raise ItemError('Cet article a ete modifie par un autre utilisateur. Liste actualisee : verifiez puis confirmez a nouveau.',409)
    if field not in MONEY_FIELDS and field not in ('load_add','load_set') and item[field] == value:
        return {'index':index,'item':item,'changed':False}
    stamp = datetime.now().astimezone().isoformat(timespec='microseconds')
    before = item.get(field)
    version = item['version']+1
    if field == 'discount':
        # One remise applies to every article in this order, then each total net follows.
        changed_any = False
        for item_index, each in enumerate(order['items']):
            each_price = _number(each.get('price'))
            each_qty = _number(each.get('quantity'))
            expected_net = round(each_price*(1-float(value)/100)*each_qty,6)
            current_net = round(_number(each.get('net_total') if each.get('net_total') is not None else each.get('amount')),6)
            if abs(_number(each.get('discount'))-float(value)) < 1e-6 and abs(current_net-expected_net) < 1e-4:
                continue
            each_version = int(each['version'])+1
            conn.execute("""UPDATE order_item_state SET price=?,discount=?,net_price=NULL,money_modified=1,money_modified_by=?,money_modified_at=?,version=?
                WHERE file_id=? AND item_index=?""",(each_price,float(value),username,stamp,each_version,file_id,item_index))
            conn.execute("""INSERT INTO order_item_history
                (file_id,item_index,delivery_id,article,reference,original_quantity,before_quantity,after_quantity,username,
                 action,before_value,after_value,occurred_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (file_id,item_index,delivery_id,each['description'],reference(each,row['filename']),each['original_quantity'],each['quantity'],
                 each['quantity'],username,'DISCOUNT_MODIFIED',json.dumps(_number(each.get('discount'))),json.dumps(float(value)),stamp,each_version))
            changed_any = True
        if not changed_any:
            return {'index':index,'item':item,'changed':False}
        result = enrich(conn,file_id,read_order(row))['items'][index]
        return {'index':index,'item':result,'changed':True}
    if field in ('load_add','load_set'):
        ordered = _number(item.get('quantity'))
        current = _number(item.get('loaded_quantity'))
        if field == 'load_add':
            remaining = max(0.0, ordered - current)
            if float(value) - remaining > 1e-6:
                raise ItemError('La quantite depasse le reste a charger.')
            new_loaded = current + float(value)
            action = 'LOAD_RECORDED'
        else:
            if float(value) - max(ordered, current) > 1e-6:
                raise ItemError('La quantite chargee ne peut pas depasser la quantite commandee.')
            new_loaded = float(value)
            action = 'LOAD_ADJUSTED'
        if abs(new_loaded - current) < 1e-9:
            return {'index':index,'item':item,'changed':False}
        loaded_flag = 1 if new_loaded > 1e-9 else 0
        conn.execute("""UPDATE order_item_state SET loaded_quantity=?,loaded=?,loaded_by=?,loaded_at=?,version=?
            WHERE file_id=? AND item_index=?""",(new_loaded,loaded_flag,username,stamp,version,file_id,index))
        conn.execute("""INSERT INTO order_item_history
            (file_id,item_index,delivery_id,article,reference,original_quantity,before_quantity,after_quantity,username,
             action,before_value,after_value,occurred_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (file_id,index,delivery_id,item['description'],reference(item,row['filename']),item['original_quantity'],item['quantity'],
             item['quantity'],username,action,json.dumps(current),json.dumps(new_loaded),stamp,version))
        result = enrich(conn,file_id,read_order(row))['items'][index]
        return {'index':index,'item':result,'changed':True}
    if field in MONEY_FIELDS:
        price = _number(item.get('price'))
        discount = _number(item.get('discount'))
        quantity = _number(item.get('quantity'))
        net_price = item.get('net_price') if isinstance(item.get('net_price'),(int,float)) and not isinstance(item.get('net_price'),bool) else None
        unit_net = net_price if net_price is not None else price*(1-discount/100)
        if field == 'price':
            price = float(value)
            net_price = unit_net
            if price:
                discount = (1-unit_net/price)*100
        elif field == 'amount':
            if quantity == 0:
                raise ItemError('Quantite nulle : modifiez la quantite avant le montant.')
            price = float(value)/quantity
            net_price = unit_net
            if price:
                discount = (1-unit_net/price)*100
        else:
            if quantity == 0:
                raise ItemError('Quantite nulle : modifiez la quantite avant le total net.')
            net_price = float(value)/quantity
            if price:
                discount = (1-net_price/price)*100
        if isinstance(before,(int,float)) and not isinstance(before,bool) and math.isfinite(before) and abs(float(before)-float(value)) < 1e-6:
            return {'index':index,'item':item,'changed':False}
        stored_net = None if net_price is None else net_price
        conn.execute("""UPDATE order_item_state SET price=?,discount=?,net_price=?,money_modified=1,money_modified_by=?,money_modified_at=?,version=?
            WHERE file_id=? AND item_index=?""",(price,discount,stored_net,username,stamp,version,file_id,index))
        action = {'price':'PRICE_MODIFIED','discount':'DISCOUNT_MODIFIED','amount':'AMOUNT_MODIFIED','net_total':'NET_TOTAL_MODIFIED'}[field]
    elif field == 'quantity':
        conn.execute("""UPDATE order_item_state SET quantity=?,modified=1,quantity_modified_by=?,quantity_modified_at=?,version=?
            WHERE file_id=? AND item_index=?""",(value,username,stamp,version,file_id,index))
        action='QUANTITY_MODIFIED'
    else:
        if field != 'delivered':
            raise ItemError('Indiquez la quantite chargee.')
        conn.execute('UPDATE order_item_state SET delivered=?,delivered_by=?,delivered_at=?,version=? WHERE file_id=? AND item_index=?',
            (int(value),username,stamp,version,file_id,index))
        action = 'DELIVERED' if value else 'DELIVERY_UNDONE'
    conn.execute("""INSERT INTO order_item_history
        (file_id,item_index,delivery_id,article,reference,original_quantity,before_quantity,after_quantity,username,
         action,before_value,after_value,occurred_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (file_id,index,delivery_id,item['description'],reference(item,row['filename']),item['original_quantity'],item['quantity'],
         value if field=='quantity' else item['quantity'],username,action,json.dumps(before),json.dumps(value),stamp,version))
    result = enrich(conn,file_id,read_order(row))['items'][index]
    return {'index':index,'item':result,'changed':True}


AVAILABILITY = ('check', 'available', 'unavailable')


def availability_board(conn):
    ensure_availability_column(conn)
    prime(conn)
    rows = conn.execute("""SELECT f.id AS file_id, f.filename, f.parsed_json, f.content,
        d.id AS delivery_id, d.vessel
        FROM order_files f JOIN deliveries d ON d.id=f.delivery_id
        ORDER BY d.vessel COLLATE NOCASE, f.id""").fetchall()
    articles = []
    for row in rows:
        try:
            order = enrich(conn, row['file_id'], read_order(row))
        except Exception:
            continue
        for index, item in enumerate(order['items']):
            status = item.get('availability') or 'check'
            if status not in AVAILABILITY:
                status = 'check'
            articles.append({
                'key': f"{row['file_id']}:{index}",
                'delivery_id': row['delivery_id'],
                'file_id': row['file_id'],
                'index': index,
                'vessel': row['vessel'] or '',
                'name': item.get('description') or '',
                'reference': reference(item, row['filename']),
                'availability': status,
                'version': int(item.get('version') or 0),
            })
    return articles


def set_availability(conn, file_id, index, value, expected_version, username):
    ensure_availability_column(conn)
    if value not in AVAILABILITY:
        raise ItemError('Statut invalide.')
    if type(expected_version) is not int or expected_version < 0:
        raise ItemError('Version requise. Actualisez la liste avant de modifier.', 409)
    row = conn.execute("SELECT id, delivery_id, filename, parsed_json, content FROM order_files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise ItemError('Article introuvable.', 404)
    order = enrich(conn, file_id, read_order(row))
    if index < 0 or index >= len(order['items']):
        raise ItemError('Article introuvable.', 404)
    item = order['items'][index]
    if int(item.get('version') or 0) != expected_version:
        raise ItemError('Un autre utilisateur a modifie cet article. Actualisez la liste.', 409)
    previous = item.get('availability') or 'check'
    if previous == value:
        return {'changed': False, 'item': _availability_item(row, index, item)}
    version = expected_version + 1
    stamp = datetime.now().astimezone().isoformat(timespec='microseconds')
    conn.execute("UPDATE order_item_state SET availability=?, version=? WHERE file_id=? AND item_index=?",
        (value, version, file_id, index))
    conn.execute("""INSERT INTO order_item_history
        (file_id,item_index,delivery_id,article,reference,original_quantity,before_quantity,after_quantity,username,
         action,before_value,after_value,occurred_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (file_id, index, row['delivery_id'], item.get('description') or '', reference(item, row['filename']),
         item['original_quantity'], item['quantity'], item['quantity'], username, 'AVAILABILITY',
         json.dumps(previous), json.dumps(value), stamp, version))
    item['availability'] = value
    item['version'] = version
    return {'changed': True, 'item': _availability_item(row, index, item)}


def _availability_item(row, index, item):
    return {
        'key': f"{row['id']}:{index}",
        'delivery_id': row['delivery_id'],
        'file_id': row['id'],
        'index': index,
        'vessel': '',
        'name': item.get('description') or '',
        'reference': reference(item, row['filename']),
        'availability': item.get('availability') or 'check',
        'version': int(item.get('version') or 0),
    }


def loaded_articles(conn):
    files = conn.execute("""SELECT DISTINCT f.*,d.vessel FROM order_files f JOIN deliveries d ON d.id=f.delivery_id
        JOIN order_item_state s ON s.file_id=f.id WHERE s.loaded=1 ORDER BY f.id DESC""").fetchall()
    articles=[]
    for row in files:
        order=enrich(conn,row['id'],read_order(row))
        for index,item in enumerate(order['items']):
            if item['loaded']:
                articles.append(dict(item,delivery_id=row['delivery_id'],file_id=row['id'],item_index=index,
                    vessel=row['vessel'],filename=row['filename'],reference=reference(item,row['filename'])))
    return articles


LOAD_ACTIONS = ('LOAD_RECORDED', 'LOAD_ADJUSTED', 'LOADED', 'UNLOADED')


def load_events(conn, file_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='order_item_history'").fetchone():
        return []
    rows = conn.execute(
        f"SELECT * FROM order_item_history WHERE file_id=? AND action IN ({','.join('?' * len(LOAD_ACTIONS))}) ORDER BY id",
        (file_id, *LOAD_ACTIONS)).fetchall()
    events = []
    for row in rows:
        action = row['action']
        try:
            before = json.loads(row['before_value'])
            after = json.loads(row['after_value'])
        except (TypeError, json.JSONDecodeError):
            before, after = 0, 0
        if action == 'LOADED':
            before_qty, after_qty = 0.0, _number(row['after_quantity'])
        elif action == 'UNLOADED':
            before_qty, after_qty = _number(row['before_quantity']), 0.0
        else:
            before_qty, after_qty = _number(before), _number(after)
        ordered_now = _number(row['after_quantity'])
        events.append({
            'id': row['id'],
            'item_index': row['item_index'],
            'article': row['article'],
            'reference': row['reference'],
            'original_quantity': _number(row['original_quantity']),
            'ordered_quantity': ordered_now,
            'before_loaded': before_qty,
            'after_loaded': after_qty,
            'delta': after_qty - before_qty,
            'remaining': max(0.0, ordered_now - after_qty),
            'username': row['username'],
            'occurred_at': row['occurred_at'],
            'action': action,
            'version': row['version'],
        })
    return events


def in_period(occurred_at, period, now=None):
    if period in (None, '', 'all'):
        return True
    now = now or datetime.now().astimezone()
    if period == 'today':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == '7d':
        start = now - timedelta(days=7)
    elif period == 'week':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    else:
        return True
    try:
        moment = datetime.fromisoformat(occurred_at)
    except (TypeError, ValueError):
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=now.tzinfo)
    return moment.astimezone(now.tzinfo) >= start
