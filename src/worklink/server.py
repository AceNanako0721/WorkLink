"""Loopback-only authenticated HTTP boundary. Never log URL paths or request data."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, urlsplit

from .core import ApiError, Bridge

ROOT = Path(__file__).resolve().parents[2]


def outside_checkout(path):
    path = Path(path).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError('Private configuration and runtime state must be outside the checkout')
    return path


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def create_server(config_path, *, port_override=None):
    config_path = outside_checkout(config_path)
    config = json.loads(config_path.read_text(encoding='utf-8'))
    settings = config.get('service', {})
    if settings.get('host', '127.0.0.1') != '127.0.0.1':
        raise ValueError('v1 only supports a loopback listener')
    private = outside_checkout(config['privateDataDirectory'])
    private.mkdir(parents=True, exist_ok=True)
    keys = config.get('keys', {})
    bot_key = outside_checkout(keys['botTokenFile']).read_text(encoding='utf-8').strip()
    adapter_key = outside_checkout(keys['adapterTokenFile']).read_text(encoding='utf-8').strip()
    if len(bot_key) < 24 or len(adapter_key) < 24 or hmac.compare_digest(bot_key, adapter_key):
        raise ValueError('Separate strong bot and adapter keys are required')
    expected_title = config['teams']['selfChatDisplayName']
    if not expected_title or expected_title == 'SELF_CHAT_DISPLAY_NAME':
        raise ValueError('Configure the actual self-chat title outside the checkout')
    bridge = Bridge(outside_checkout(private / 'worklink.sqlite'), owner_id=int(config.get('ownerId', 1001)))

    class Handler(BaseHTTPRequestHandler):
        server_version = 'WorkLink'
        sys_version = ''

        def log_message(self, *_):
            pass

        def reply(self, code, payload):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def body(self):
            query = {key: values[-1] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if self.command == 'GET':
                return query
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 256000:
                raise ApiError(413, 'Request body exceeds limit')
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            kind = self.headers.get('Content-Type', '').split(';')[0].strip().lower()
            if kind == 'application/json':
                data = json.loads(raw or b'{}')
            elif kind == 'application/x-www-form-urlencoded':
                data = {key: values[-1] for key, values in parse_qs(raw.decode()).items()}
            else:
                raise ApiError(415, 'Use JSON or form-urlencoded parameters')
            if not isinstance(data, dict):
                raise ApiError(400, 'Request body must be an object')
            return {**query, **data}

        def process(self):
            try:
                host = self.headers.get('Host', '')
                if host not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
                    raise ApiError(403, 'Invalid Host')
                origin = self.headers.get('Origin')
                if origin and not origin.startswith('chrome-extension://'):
                    raise ApiError(403, 'Web origins are not accepted')
                path = urlsplit(self.path).path
                if path == '/health' and self.command == 'GET':
                    result = {'status': 'running', 'adapterOnline': bridge.adapter_owner is not None and time.monotonic() - bridge.adapter_last_seen <= 15}
                elif path.startswith('/adapter/'):
                    auth = self.headers.get('Authorization', '')
                    if not hmac.compare_digest(auth, 'Bearer ' + adapter_key):
                        raise ApiError(401, 'Unauthorized')
                    data = self.body()
                    if path == '/adapter/settings':
                        result = {'selfChatDisplayName': expected_title, 'ownerId': bridge.owner_id}
                    elif path == '/adapter/status':
                        with bridge.cv:
                            result = {'adapterOnline': bridge.adapter_owner is not None and time.monotonic() - bridge.adapter_last_seen <= 15,
                                      'unfinished': [dict(row) for row in bridge.db.execute("SELECT id,kind,state FROM jobs WHERE state IN ('pending','leased','unknown')")]}
                    else:
                        if data.get('chatTitle') != expected_title:
                            raise ApiError(403, 'Self-chat identity mismatch')
                        client = data.get('clientId')
                        if path == '/adapter/observe':
                            with bridge.transaction():
                                bridge.adapter(client)
                            result = bridge.ingest(data.get('messages'), baseline=bool(data.get('baseline')))
                        elif path == '/adapter/next':
                            result = bridge.lease(client)
                        elif path == '/adapter/complete':
                            result = bridge.complete(client, data.get('id'), data.get('lease'), data.get('state'), data.get('teamsId'))
                        else:
                            raise ApiError(404, 'Not found')
                else:
                    segments = path.strip('/').split('/')
                    if len(segments) != 2 or not segments[0].startswith('bot') or not hmac.compare_digest(segments[0][3:], bot_key):
                        raise ApiError(401, 'Unauthorized')
                    result = bridge.call(segments[1], self.body())
                self.reply(200, {'ok': True, 'result': result})
            except ApiError as error:
                self.reply(error.code, {'ok': False, 'error_code': error.code, 'description': error.description})
            except (ValueError, TypeError, KeyError):
                self.reply(400, {'ok': False, 'error_code': 400, 'description': 'Invalid request'})
            except Exception:
                # Do not echo database contents, file paths, request URLs, or keys.
                self.reply(500, {'ok': False, 'error_code': 500, 'description': 'Internal bridge error'})

        do_GET = process
        do_POST = process

    server = Server(('127.0.0.1', port_override if port_override is not None else int(settings.get('port', 8765))), Handler)
    server.bridge = bridge
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        server = create_server(args.config)
    except Exception:
        print('Startup failed: check private configuration, keys, and port.', file=sys.stderr)
        return 1
    print('WorkLink is listening on loopback. Request logging is disabled.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.bridge.close()
    return 0
