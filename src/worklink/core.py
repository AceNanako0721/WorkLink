"""Durable protocol state. Browser effects are confirmed through leased operations."""
from contextlib import contextmanager
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time


class ApiError(Exception):
    def __init__(self, code, description):
        super().__init__(description)
        self.code, self.description = code, description


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, value):
        self.parts.append(value)
    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in {'p', 'div', 'pre'}:
            self.parts.append('\n')


def plain_text(body):
    rich = body.get('rich_message')
    if rich is not None:
        if not isinstance(rich, dict):
            raise ApiError(400, 'Invalid rich_message')
        text = rich.get('markdown', rich.get('html', ''))
        html = 'html' in rich
    else:
        text, html = body.get('text', ''), body.get('parse_mode') == 'HTML'
    if not isinstance(text, str) or len(text) > 16000:
        raise ApiError(400, 'Invalid text')
    if html:
        parser = PlainHTML()
        parser.feed(text)
        text = ''.join(parser.parts).rstrip('\n')
    return text


def parse_input(text):
    """Case-sensitive complete prefix, with whitespace separator; preserve inner command."""
    match = re.match(r'^/chat(?:\s+)([\s\S]+)$', text)
    if match and match.group(1).strip():
        return 'message', match.group(1)
    match = re.fullmatch(r'/choose\s+(k[a-f0-9]{12})\s+([1-9][0-9]*)\s*', text)
    if match:
        return 'choice', (match.group(1), int(match.group(2)))
    return None, None


def integer(value, name, minimum=None, maximum=None):
    try:
        if isinstance(value, bool) or isinstance(value, float):
            raise ValueError()
        result = int(value)
    except (ValueError, TypeError):
        raise ApiError(400, 'Invalid ' + name)
    if minimum is not None and result < minimum or maximum is not None and result > maximum:
        raise ApiError(400, 'Invalid ' + name)
    return result


class Bridge:
    def __init__(self, database, *, owner_id=1001, operation_timeout=35):
        self.db = sqlite3.connect(str(database), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS seen(teams_id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                telegram_id INTEGER, origin TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
                teams_id TEXT UNIQUE, text TEXT NOT NULL, markup TEXT NOT NULL DEFAULT '{}',
                menu TEXT, deleted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS menus(id TEXT PRIMARY KEY, message_id INTEGER NOT NULL,
                options TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL, lease TEXT,
                result TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS callbacks(id TEXT PRIMARY KEY, answered INTEGER NOT NULL DEFAULT 0);
        ''')
        self.db.commit()
        self.cv = threading.Condition(threading.RLock())
        self.poll_lock = threading.Lock()
        self.owner_id = owner_id
        self.operation_timeout = operation_timeout
        self.adapter_owner = None
        self.adapter_last_seen = 0.0
        self.adapter_poll_wait = 0
        # Effects leased to a browser before a restart may have committed: never replay them.
        with self.db:
            self.db.execute("UPDATE jobs SET state='unknown' WHERE state='leased'")

    @contextmanager
    def transaction(self):
        with self.cv:
            try:
                yield
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            finally:
                self.cv.notify_all()

    def close(self):
        with self.cv:
            self.db.close()

    def get_meta(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    @property
    def user(self):
        return {'id': self.owner_id, 'is_bot': False, 'first_name': 'Owner'}

    @property
    def bot(self):
        return {'id': 9000001, 'is_bot': True, 'first_name': 'WorkLink',
                'username': 'worklink_bot', 'has_topics_enabled': False,
                'can_join_groups': False, 'supports_inline_queries': False}

    @property
    def chat(self):
        return {'id': self.owner_id, 'type': 'private', 'first_name': 'Owner'}

    def message(self, row, *, incoming=False, text=None, date=None):
        return {'message_id': row['id'], 'from': self.user if incoming else self.bot,
                'chat': self.chat, 'date': date or int(time.time()),
                'text': row['text'] if text is None else text}

    def add_update(self, kind, payload):
        self.db.execute('INSERT INTO updates(kind,payload) VALUES (?,?)', (kind, json.dumps(payload, ensure_ascii=False)))

    def ingest(self, records, *, baseline=False):
        """Persist observed IDs atomically; only commands newer than first baseline are inputs."""
        if not isinstance(records, list) or len(records) > 1000:
            raise ApiError(400, 'Invalid observation batch')
        count = 0
        with self.transaction():
            initialized = self.get_meta('baseline', False)
            if not initialized and not baseline:
                raise ApiError(409, 'Initial baseline required')
            cutoff = self.get_meta('baseline_cutoff', 0)
            valid = []
            for item in records:
                if not isinstance(item, dict):
                    raise ApiError(400, 'Invalid observation')
                mid, text = item.get('id'), item.get('text')
                if not isinstance(mid, str) or not re.fullmatch(r'[0-9]{10,20}', mid):
                    raise ApiError(400, 'Invalid Teams message ID')
                if not isinstance(text, str) or len(text) > 32000:
                    raise ApiError(400, 'Invalid observed text')
                valid.append((mid, text, bool(item.get('edited')), integer(item.get('date', int(time.time())), 'date', 0)))
            if not initialized:
                # Loading an older page after activation must not replay history.
                cutoff = max(int(time.time() * 1000), max((int(mid) for mid, *_ in valid), default=0))
                self.set_meta('baseline_cutoff', cutoff)
                self.set_meta('baseline', True)
            for mid, text, edited, date in valid:
                digest = hashlib.sha256(text.encode()).hexdigest()
                seen = self.db.execute('SELECT * FROM seen WHERE teams_id=?', (mid,)).fetchone()
                if seen and seen['digest'] == digest:
                    continue
                own = self.db.execute('SELECT id FROM messages WHERE teams_id=?', (mid,)).fetchone()
                if own and (not seen or seen['origin'] == 'service') or text.startswith('omp：'):
                    origin = 'service'
                else:
                    origin = 'user'
                if seen:
                    # v1 consumes new submissions only. Edits never resubmit/cancel tasks.
                    self.db.execute('UPDATE seen SET digest=? WHERE teams_id=?', (digest, mid))
                    continue
                self.db.execute('INSERT INTO seen VALUES (?,?,NULL,?)', (mid, digest, origin))
                if origin == 'service' or int(mid) <= cutoff or edited:
                    continue
                kind, value = parse_input(text)
                if kind == 'message':
                    cursor = self.db.execute('INSERT INTO messages(teams_id,text) VALUES (?,?)', (mid, value))
                    msg = self.message({'id': cursor.lastrowid, 'text': value}, incoming=True, date=date)
                    command = re.match(r'^/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?(?=\s|$)', value)
                    if command:
                        msg['entities'] = [{'type': 'bot_command', 'offset': 0,
                                            'length': len(command.group().encode('utf-16-le')) // 2}]
                    self.db.execute('UPDATE seen SET telegram_id=? WHERE teams_id=?', (cursor.lastrowid, mid))
                    self.add_update('message', msg)
                    count += 1
                elif kind == 'choice':
                    menu_id, index = value
                    menu = self.db.execute('SELECT * FROM menus WHERE id=? AND active=1', (menu_id,)).fetchone()
                    if not menu:
                        continue
                    options = json.loads(menu['options'])
                    if index > len(options) or 'callback_data' not in options[index - 1]:
                        continue
                    row = self.db.execute('SELECT * FROM messages WHERE id=? AND deleted=0', (menu['message_id'],)).fetchone()
                    if not row:
                        continue
                    callback_id = secrets.token_hex(12)
                    self.db.execute('INSERT INTO callbacks(id) VALUES (?)', (callback_id,))
                    self.add_update('callback_query', {'id': callback_id, 'from': self.user,
                        'chat_instance': 'worklink-single-chat', 'message': self.message(row),
                        'data': options[index - 1]['callback_data']})
                    count += 1
        return {'accepted': count}

    def get_updates(self, body):
        offset = integer(body['offset'], 'offset') if 'offset' in body else None
        limit = integer(body.get('limit', 100), 'limit', 1, 100)
        timeout = integer(body.get('timeout', 0), 'timeout', 0, 50)
        allowed = body.get('allowed_updates')
        if isinstance(allowed, str):
            try:
                allowed = json.loads(allowed)
            except ValueError:
                raise ApiError(400, 'Invalid allowed_updates')
        if allowed is not None and (not isinstance(allowed, list) or any(not isinstance(v, str) for v in allowed)):
            raise ApiError(400, 'Invalid allowed_updates')
        if not self.poll_lock.acquire(blocking=False):
            raise ApiError(409, 'Another getUpdates consumer is active')
        try:
            with self.transaction():
                if allowed is not None:
                    self.set_meta('allowed_updates', allowed)
                allowed = self.get_meta('allowed_updates', [])
                if offset is not None and offset < 0:
                    rows = self.db.execute('SELECT id FROM updates ORDER BY id DESC LIMIT ?', (-offset,)).fetchall()
                    if rows:
                        offset = rows[-1]['id']
                if offset is not None and offset >= 0:
                    self.db.execute('DELETE FROM updates WHERE id<?', (offset,))
                deadline = time.monotonic() + timeout
                while True:
                    rows = self.db.execute('SELECT * FROM updates ORDER BY id').fetchall()
                    result = [{'update_id': r['id'], r['kind']: json.loads(r['payload'])}
                              for r in rows if not allowed or r['kind'] in allowed][:limit]
                    if result or time.monotonic() >= deadline:
                        return result
                    # Commit offset confirmation before releasing the lock during long polling.
                    self.db.commit()
                    self.cv.wait(min(1, max(0, deadline - time.monotonic())))
        finally:
            self.poll_lock.release()

    def keyboard(self, markup, message_id):
        if markup is None:
            markup = {}
        if isinstance(markup, str):
            try:
                markup = json.loads(markup)
            except ValueError:
                raise ApiError(400, 'Invalid reply_markup')
        if not isinstance(markup, dict) or set(markup) - {'inline_keyboard'}:
            raise ApiError(400, 'Only inline_keyboard is supported')
        rows = markup.get('inline_keyboard', [])
        if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
            raise ApiError(400, 'Invalid inline_keyboard')
        options = [button for row in rows for button in row]
        if len(options) > 100:
            raise ApiError(400, 'Too many options')
        for button in options:
            if not isinstance(button, dict) or not isinstance(button.get('text'), str):
                raise ApiError(400, 'Invalid option')
            if 'callback_data' not in button and 'url' not in button:
                raise ApiError(400, 'Unsupported button type')
            if 'callback_data' in button and (not isinstance(button['callback_data'], str) or not 1 <= len(button['callback_data'].encode()) <= 64):
                raise ApiError(400, 'Invalid callback_data')
            if 'url' in button and (not isinstance(button['url'], str) or not re.match(r'^https?://', button['url'])):
                raise ApiError(400, 'Invalid button URL')
        menu_id = 'k' + secrets.token_hex(6) if options else None
        if menu_id:
            self.db.execute('INSERT INTO menus VALUES (?,?,?,0)', (menu_id, message_id, json.dumps(options)))
        return markup, menu_id, options

    def render(self, text, menu_id, options):
        lines = ['omp：' + text]
        for index, option in enumerate(options, 1):
            suffix = '/choose ' + menu_id + ' ' + str(index) if 'callback_data' in option else option['url']
            lines.append(option['text'] + '：' + suffix)
        return '\n'.join(lines)

    def check_chat(self, body):
        if integer(body.get('chat_id'), 'chat_id') != self.owner_id:
            raise ApiError(403, 'Only the configured self chat is available')
        if body.get('message_thread_id') is not None:
            raise ApiError(400, 'Threaded mode is not supported')

    def enqueue_effect(self, method, body):
        self.check_chat(body)
        with self.transaction():
            if not self.adapter_owner or time.monotonic() - self.adapter_last_seen > 15:
                raise ApiError(503, 'Teams adapter is offline')
            # A normal final reply and its activity report can arrive together.
            # Wait for confirmed healthy effects instead of rejecting one reply.
            # Unknown effects still fence all new work, without any replay.
            deadline = time.monotonic() + self.operation_timeout
            while self.db.execute("SELECT 1 FROM jobs WHERE state IN ('pending','leased','unknown')").fetchone():
                if self.db.execute("SELECT 1 FROM jobs WHERE state='unknown'").fetchone():
                    raise ApiError(409, 'An operation is unfinished; reconcile it before submitting another')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ApiError(503, 'Reply channel is busy; no new operation was submitted')
                self.cv.wait(min(1, remaining))
            if time.monotonic() - self.adapter_last_seen > 15:
                raise ApiError(503, 'Teams adapter is offline')
            if method in {'sendmessage', 'sendrichmessage'}:
                text = plain_text(body)
                if not text.strip():
                    raise ApiError(400, 'Message text is empty')
                mid = self.db.execute('INSERT INTO messages(text) VALUES (?)', (text,)).lastrowid
                row = None
                kind = 'send'
            else:
                mid = integer(body.get('message_id'), 'message_id', 1)
                row = self.db.execute('SELECT * FROM messages WHERE id=? AND deleted=0', (mid,)).fetchone()
                if not row or not row['teams_id']:
                    raise ApiError(400, 'Message to modify not found')
                seen = self.db.execute('SELECT origin FROM seen WHERE teams_id=?', (row['teams_id'],)).fetchone()
                if not seen or seen['origin'] != 'service':
                    raise ApiError(403, 'Only service replies can be modified')
                text = plain_text(body) if method == 'editmessagetext' else row['text']
                kind = 'delete' if method == 'deletemessage' else 'edit'
            markup = body.get('reply_markup', json.loads(row['markup']) if row else {})
            markup, menu_id, options = self.keyboard(markup, mid) if kind != 'delete' else ({}, None, [])
            if row and kind == 'edit' and text == row['text'] and markup == json.loads(row['markup']):
                raise ApiError(400, 'Bad Request: message is not modified')
            payload = {'message_id': mid, 'teams_id': row['teams_id'] if row else None,
                'text': self.render(text, menu_id, options), 'plain_text': text,
                'markup': markup, 'menu': menu_id, 'old_menu': row['menu'] if row else None}
            if row:
                payload['expected_text'] = self.render(row['text'], row['menu'],
                    json.loads(self.db.execute('SELECT options FROM menus WHERE id=?', (row['menu'],)).fetchone()[0]) if row['menu'] else [])
            job_id = secrets.token_hex(16)
            self.db.execute('INSERT INTO jobs VALUES (?,?,?,\'pending\',NULL,NULL,?)',
                            (job_id, kind, json.dumps(payload, ensure_ascii=False), time.time()))
        return job_id

    def wait_effect(self, job_id):
        deadline = time.monotonic() + self.operation_timeout
        with self.cv:
            while True:
                row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
                if row['state'] == 'succeeded':
                    return json.loads(row['result'])
                if row['state'] in {'failed', 'unknown'}:
                    raise ApiError(409 if row['state'] == 'unknown' else 400,
                                   'Browser operation outcome is unknown; reconcile before retrying' if row['state'] == 'unknown' else 'Browser operation was not completed')
                if time.monotonic() >= deadline:
                    with self.transaction():
                        state = 'failed' if row['state'] == 'pending' else 'unknown'
                        self.db.execute('UPDATE jobs SET state=? WHERE id=?', (state, job_id))
                    raise ApiError(409 if state == 'unknown' else 503, 'Browser operation timed out; do not blindly resend')
                self.cv.wait(min(1, deadline - time.monotonic()))

    def adapter(self, client_id):
        if not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', client_id):
            raise ApiError(400, 'Invalid adapter identity')
        if self.adapter_owner not in {None, client_id} and time.monotonic() - self.adapter_last_seen < 15:
            raise ApiError(409, 'A different Teams adapter is active')
        self.adapter_owner, self.adapter_last_seen = client_id, time.monotonic()

    def lease(self, client_id, wait=0):
        wait = integer(wait, 'wait', 0, 10)
        with self.transaction():
            self.adapter(client_id)
            self.adapter_poll_wait = wait
            deadline = time.monotonic() + wait
            while True:
                row = self.db.execute("SELECT * FROM jobs WHERE state='pending' ORDER BY created LIMIT 1").fetchone()
                if row or time.monotonic() >= deadline:
                    break
                self.cv.wait(min(1, deadline - time.monotonic()))
                # Maintain ownership only while this authenticated poll is alive.
                self.adapter(client_id)
            if not row:
                return None
            nonce = secrets.token_hex(16)
            self.db.execute("UPDATE jobs SET state='leased',lease=? WHERE id=?", (nonce, row['id']))
            return {'id': row['id'], 'lease': nonce, 'kind': row['kind'], **json.loads(row['payload'])}

    def complete(self, client_id, job_id, nonce, state, teams_id=None, failure_code=None):
        if state not in {'succeeded', 'failed', 'unknown'}:
            raise ApiError(400, 'Invalid operation outcome')
        with self.transaction():
            self.adapter(client_id)
            row = self.db.execute('SELECT * FROM jobs WHERE id=? AND lease=?', (job_id, nonce)).fetchone()
            if not row:
                raise ApiError(409, 'Unknown operation lease')
            if row['state'] == 'succeeded':
                return True
            if row['state'] not in {'leased', 'unknown'}:
                raise ApiError(409, 'Operation is not awaiting confirmation')
            payload = json.loads(row['payload'])
            codes = {'draft_present', 'send_unavailable', 'editor_input_rejected', 'editor_text_mismatch',
                     'outcome_unconfirmed', 'reply_changed', 'menu_unavailable', 'identity_changed', 'adapter_error'}
            result = {'failure_code': failure_code} if failure_code in codes and state != 'succeeded' else None
            if state == 'succeeded':
                if row['kind'] != 'delete' and (not isinstance(teams_id, str) or not re.fullmatch(r'[0-9]{10,20}', teams_id)):
                    raise ApiError(400, 'Confirmed Teams message ID required')
                if row['kind'] == 'edit' and teams_id != payload['teams_id']:
                    raise ApiError(409, 'Edited message identity mismatch')
                if row['kind'] == 'send' and self.db.execute('SELECT 1 FROM seen WHERE teams_id=? AND origin!=?', (teams_id, 'service')).fetchone():
                    raise ApiError(409, 'Reply identity conflicts with user input')
                mid = payload['message_id']
                if payload['old_menu']:
                    self.db.execute('UPDATE menus SET active=0 WHERE id=?', (payload['old_menu'],))
                if row['kind'] == 'delete':
                    self.db.execute('UPDATE messages SET deleted=1 WHERE id=?', (mid,))
                    result = True
                else:
                    self.db.execute('UPDATE messages SET teams_id=?,text=?,markup=?,menu=? WHERE id=?',
                        (teams_id, payload['plain_text'], json.dumps(payload['markup']), payload['menu'], mid))
                    self.db.execute('INSERT OR REPLACE INTO seen VALUES (?,?,?,\'service\')',
                        (teams_id, hashlib.sha256(payload['text'].encode()).hexdigest(), mid))
                    if payload['menu']:
                        self.db.execute('UPDATE menus SET active=1 WHERE id=?', (payload['menu'],))
                    result = self.message({'id': mid, 'text': payload['plain_text']})
            self.db.execute('UPDATE jobs SET state=?,result=? WHERE id=?', (state, json.dumps(result), job_id))
        return True

    def call(self, method, body):
        method = method.lower()
        if method == 'getme':
            return self.bot
        if method == 'getupdates':
            return self.get_updates(body)
        if method == 'getchat':
            self.check_chat(body)
            return self.chat
        if method == 'getwebhookinfo':
            with self.cv:
                return {'url': '', 'pending_update_count': self.db.execute('SELECT count(*) FROM updates').fetchone()[0]}
        if method == 'deletewebhook':
            if body.get('drop_pending_updates') in {True, 'true'}:
                with self.transaction():
                    self.db.execute('DELETE FROM updates')
            return True
        if method in {'setmycommands', 'getmycommands', 'deletemycommands'}:
            with self.transaction():
                if method == 'getmycommands':
                    return self.get_meta('commands', [])
                commands = body.get('commands', []) if method == 'setmycommands' else []
                if isinstance(commands, str):
                    commands = json.loads(commands)
                if not isinstance(commands, list):
                    raise ApiError(400, 'Invalid commands')
                self.set_meta('commands', commands)
            return True
        if method == 'answercallbackquery':
            with self.transaction():
                cursor = self.db.execute('UPDATE callbacks SET answered=1 WHERE id=?', (body.get('callback_query_id'),))
                if not cursor.rowcount:
                    raise ApiError(400, 'Unknown callback query')
            return True
        if method == 'sendchataction':
            self.check_chat(body)
            # Accepted as an explicit compatibility no-op: no Teams typing indicator.
            return True
        if method in {'sendmessage', 'sendrichmessage', 'editmessagetext', 'editmessagereplymarkup', 'deletemessage'}:
            return self.wait_effect(self.enqueue_effect(method, body))
        raise ApiError(501, 'Method is not supported by WorkLink v1')
