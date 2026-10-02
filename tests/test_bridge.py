"""Synthetic protocol integration: input persistence and confirmed browser effects."""
import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from worklink.core import ApiError, Bridge
from worklink.server import create_server, outside_checkout


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite'
        self.bridge = Bridge(self.path, operation_timeout=.15)
        self.clock = int(time.time() * 1000) + 10000
        self.bridge.ingest([{'id': str(self.clock - 20000), 'text': '/chat old'}], baseline=True)
        with self.bridge.transaction():
            self.bridge.adapter('synthetic_adapter')

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def observe(self, text, **extra):
        self.clock += 1
        item = {'id': str(self.clock), 'text': text, **extra}
        self.bridge.ingest([item])
        return item

    def finish(self, method, data):
        job_id = self.bridge.enqueue_effect(method, {'chat_id': 1001, **data})
        job = self.bridge.lease('synthetic_adapter')
        self.clock += 1
        teams_id = job['teams_id'] or str(self.clock)
        self.bridge.complete('synthetic_adapter', job_id, job['lease'], 'succeeded', teams_id)
        return self.bridge.wait_effect(job_id), job

    def test_gate_original_commands_and_dedup_survive_restart(self):
        for text in ['normal', '/chat', '/chatter bad', 'omp：/chat reply']:
            self.observe(text)
        message = self.observe('/chat /model fixture-model')
        self.bridge.ingest([message, {**message, 'text': '/chat changed', 'edited': True}])
        updates = self.bridge.get_updates({})
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['message']['text'], '/model fixture-model')
        self.assertEqual(updates[0]['message']['entities'][0]['length'], 6)
        self.bridge.close()
        self.bridge = Bridge(self.path)
        self.bridge.ingest([message], baseline=True)
        self.assertEqual(self.bridge.get_updates({}), updates)
        self.assertEqual(self.bridge.get_updates({'offset': updates[0]['update_id'] + 1}), [])

    def test_late_loaded_history_is_not_a_new_task(self):
        self.bridge.ingest([{'id': str(int(time.time()*1000)-1000), 'text': '/chat old loaded later'}])
        self.assertEqual(self.bridge.get_updates({}), [])

    def test_adapter_long_poll_wakes_for_reply_and_has_bounded_wait(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            waiting = pool.submit(self.bridge.lease, 'synthetic_adapter', 1)
            with self.assertRaises(concurrent.futures.TimeoutError):
                waiting.result(timeout=.03)
            job_id = self.bridge.enqueue_effect('sendmessage', {'chat_id': 1001, 'text': 'Wake browser'})
            job = waiting.result(timeout=.5)
            self.assertEqual(job['id'], job_id)
            self.assertEqual(job['text'], 'omp：Wake browser')
        started = time.monotonic()
        self.assertIsNone(self.bridge.lease('synthetic_adapter', 1))
        self.assertLess(time.monotonic() - started, 1.5)
        with self.assertRaises(ApiError):
            self.bridge.lease('synthetic_adapter', 11)

    def test_open_menu_does_not_block_new_commands(self):
        self.finish('sendmessage', {'text': 'Menu', 'reply_markup': {'inline_keyboard': [[{'text': 'Option', 'callback_data': 'fixture:option'}]]}})
        self.observe('/chat /model')
        self.observe('/chat /help')
        self.assertEqual([u['message']['text'] for u in self.bridge.get_updates({})], ['/model', '/help'])

    def test_keyboard_callback_then_edit_invalidates_old_choice(self):
        markup = {'inline_keyboard': [[{'text': 'Accept', 'callback_data': 'approve:test'}, {'text': 'Reject', 'callback_data': 'reject:test'}]]}
        result, job = self.finish('sendmessage', {'text': 'Select', 'reply_markup': markup})
        self.assertTrue(job['text'].startswith('omp：Select\n'))
        self.assertIn('/choose ' + job['menu'] + ' 1', job['text'])
        self.observe('/choose ' + job['menu'] + ' 1')
        update = self.bridge.get_updates({})[0]
        self.assertEqual(update['callback_query']['data'], 'approve:test')
        self.assertTrue(self.bridge.call('answerCallbackQuery', {'callback_query_id': update['callback_query']['id']}))
        edited, replacement = self.finish('editmessagereplymarkup', {'message_id': result['message_id'], 'reply_markup': {}})
        self.assertEqual(edited['message_id'], result['message_id'])
        self.assertEqual(replacement['text'], 'omp：Select')
        self.bridge.get_updates({'offset': update['update_id']+1})
        self.observe('/choose ' + job['menu'] + ' 1')
        self.assertEqual(self.bridge.get_updates({}), [])
        deleted, _ = self.finish('deletemessage', {'message_id': result['message_id']})
        self.assertIs(deleted, True)

    def test_service_echo_never_enters_input_queue(self):
        _, job = self.finish('sendrichmessage', {'rich_message': {'html': '<b>Hello</b><br>World'}})
        self.assertEqual(job['text'], 'omp：Hello\nWorld')
        row = self.bridge.db.execute('SELECT teams_id FROM messages').fetchone()
        self.bridge.ingest([{'id': row['teams_id'], 'text': job['text']}])
        self.assertEqual(self.bridge.get_updates({}), [])

    def test_concurrent_final_reply_and_activity_report_are_serialized(self):
        first = self.bridge.enqueue_effect('sendmessage', {'chat_id': 1001, 'text': 'Synthetic final answer'})
        lease = self.bridge.lease('synthetic_adapter')
        with concurrent.futures.ThreadPoolExecutor() as pool:
            second = pool.submit(self.bridge.enqueue_effect, 'sendmessage', {'chat_id': 1001, 'text': 'Synthetic activity report'})
            time.sleep(.02)
            self.assertFalse(second.done())
            self.bridge.complete('synthetic_adapter', first, lease['lease'], 'succeeded', str(self.clock+1))
            second_id = second.result(timeout=1)
        second_lease = self.bridge.lease('synthetic_adapter')
        self.assertEqual(second_lease['id'], second_id)
        self.assertEqual(second_lease['plain_text'], 'Synthetic activity report')
        self.bridge.complete('synthetic_adapter', second_id, second_lease['lease'], 'succeeded', str(self.clock+2))
        self.assertNotEqual(self.bridge.wait_effect(first)['message_id'], self.bridge.wait_effect(second_id)['message_id'])

    def test_user_messages_cannot_be_edited_by_bot(self):
        self.observe('/chat question')
        mid = self.bridge.get_updates({})[0]['message']['message_id']
        with self.assertRaises(ApiError) as caught:
            self.bridge.enqueue_effect('deletemessage', {'chat_id': 1001, 'message_id': mid})
        self.assertEqual(caught.exception.code, 403)

    def test_uncertain_operation_not_replayed_after_restart(self):
        jid = self.bridge.enqueue_effect('sendmessage', {'chat_id': 1001, 'text': 'once'})
        job = self.bridge.lease('synthetic_adapter')
        with self.assertRaises(ApiError) as caught:
            self.bridge.wait_effect(jid)
        self.assertEqual(caught.exception.code, 409)
        self.bridge.close()
        self.bridge = Bridge(self.path)
        self.assertIsNone(self.bridge.lease('synthetic_adapter'))
        with self.assertRaises(ApiError):
            self.bridge.enqueue_effect('sendmessage', {'chat_id': 1001, 'text': 'duplicate'})
        self.bridge.complete('synthetic_adapter', jid, job['lease'], 'succeeded', str(self.clock + 1))
        self.assertEqual(self.bridge.wait_effect(jid)['text'], 'once')

    def test_long_poll_wakes_on_input_and_rejects_second_consumer(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            waiting = pool.submit(self.bridge.get_updates, {'timeout': 2})
            end = time.monotonic()+1
            while not self.bridge.poll_lock.locked() and time.monotonic()<end:
                time.sleep(.01)
            with self.assertRaises(ApiError) as caught:
                self.bridge.get_updates({})
            self.assertEqual(caught.exception.code, 409)
            self.observe('/chat wake')
            self.assertEqual(waiting.result(timeout=1)[0]['message']['text'], 'wake')

    def test_cross_chat_and_unimplemented_methods_fail(self):
        with self.assertRaises(ApiError) as caught:
            self.bridge.enqueue_effect('sendmessage', {'chat_id': 777, 'text': 'bad'})
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(ApiError) as caught:
            self.bridge.call('sendDocument', {})
        self.assertEqual(caught.exception.code, 501)

    def test_failure_diagnostics_never_echo_arbitrary_browser_error(self):
        for code, expected in [('editor_input_rejected', {'failure_code': 'editor_input_rejected'}), ('synthetic private error text', None)]:
            jid = self.bridge.enqueue_effect('sendmessage', {'chat_id': 1001, 'text': 'fixture'})
            job = self.bridge.lease('synthetic_adapter')
            self.bridge.complete('synthetic_adapter', jid, job['lease'], 'failed', failure_code=code)
            row = self.bridge.db.execute('SELECT result FROM jobs WHERE id=?', (jid,)).fetchone()
            self.assertEqual(json.loads(row[0]), expected)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        private = Path(self.temp.name)
        self.bot = 'synthetic_bot_key_' + 'b'*32
        self.adapter = 'synthetic_adapter_key_' + 'a'*32
        (private/'bot').write_text(self.bot)
        (private/'adapter').write_text(self.adapter)
        config = {'teams': {'selfChatDisplayName': 'Synthetic Owner (you)'}, 'privateDataDirectory': str(private/'data'),
                  'keys': {'botTokenFile': str(private/'bot'), 'adapterTokenFile': str(private/'adapter')}}
        (private/'config.json').write_text(json.dumps(config))
        self.server = create_server(private/'config.json', port_override=0)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.server.bridge.close()
        self.temp.cleanup()

    def request(self, path, data=None, **headers):
        body = json.dumps(data).encode() if data is not None else None
        req = Request(self.base+path, body, {'Content-Type': 'application/json', **headers})
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_http_roles_origin_host_and_format(self):
        status, result = self.request('/bot'+self.bot+'/getMe', {})
        self.assertEqual(status, 200)
        self.assertFalse(result['result']['has_topics_enabled'])
        self.assertEqual(self.request('/adapter/settings', {}, Authorization='Bearer '+self.bot)[0], 401)
        self.assertEqual(self.request('/bot'+self.adapter+'/getMe', {})[0], 401)
        self.assertEqual(self.request('/health', Origin='https://example.com')[0], 403)
        self.assertEqual(self.request('/health', Host='example.com')[0], 403)
        status, result = self.request('/adapter/settings', {}, Authorization='Bearer '+self.adapter)
        self.assertEqual(result['result']['ownerId'], 1001)
        self.assertEqual(self.request('/adapter/observe', {'chatTitle': 'Wrong', 'clientId': 'synthetic_adapter', 'messages': []}, Authorization='Bearer '+self.adapter)[0], 403)
        self.assertEqual(self.request('/bot'+self.bot+'/sendDocument', {})[0], 501)
        with self.assertRaises(ValueError):
            outside_checkout(Path(__file__).resolve().parents[1]/'private.json')

    def test_http_request_waits_for_confirmed_adapter_effect(self):
        common = {'chatTitle': 'Synthetic Owner (you)', 'clientId': 'synthetic_adapter'}
        auth = {'Authorization': 'Bearer '+self.adapter}
        self.request('/adapter/observe', {**common, 'baseline': True, 'messages': []}, **auth)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            sending = pool.submit(self.request, '/bot'+self.bot+'/sendMessage', {'chat_id': 1001, 'text': 'HTTP fixture'})
            deadline = time.monotonic()+2
            job = None
            while not job and time.monotonic()<deadline:
                job = self.request('/adapter/next', {**common, 'wait': 1}, **auth)[1]['result']
                if not job:
                    time.sleep(.01)
            self.assertIsNotNone(job)
            self.assertFalse(sending.done())
            self.assertEqual(job['text'], 'omp：HTTP fixture')
            self.request('/adapter/complete', {**common, 'id': job['id'], 'lease': job['lease'], 'state': 'succeeded', 'teamsId': '1900000000001'}, **auth)
            status, result = sending.result(timeout=2)
            self.assertEqual(status, 200)
            self.assertEqual(result['result']['text'], 'HTTP fixture')
            self.assertTrue(self.request('/adapter/status', {}, **auth)[1]['result']['longPolling'])

    def test_status_reports_unleased_timeout_without_exposing_payload(self):
        with self.server.bridge.transaction():
            self.server.bridge.db.execute(
                "INSERT INTO jobs VALUES ('synthetic_timeout','send',?, 'failed',NULL,NULL,?)",
                (json.dumps({'text': 'Synthetic private draft'}), time.time()))
        status, result = self.request('/adapter/status', {}, Authorization='Bearer '+self.adapter)
        self.assertEqual(status, 200)
        self.assertEqual(result['result']['recentFailures'], [{'kind': 'send', 'state': 'failed', 'code': None}])
        self.assertNotIn('Synthetic private draft', json.dumps(result))
