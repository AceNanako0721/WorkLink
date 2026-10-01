"""Regression checks for concrete publication leaks and approved examples."""
import importlib.util
from pathlib import Path
import unittest
import contextlib
import io
import subprocess
import tempfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('privacy', Path(__file__).parents[1] / 'scripts' / 'check-public.py')
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)

class PrivacyTests(unittest.TestCase):
    def test_runtime_and_binary_paths_are_rejected(self):
        for path in ['artifacts/result.png', 'doc/log.sqlite', 'src/config.json', 'private/note.md', 'other/file.txt']:
            with self.subTest(path=path):
                self.assertIsNotNone(privacy.path_problem(path))

    def test_public_text_paths_are_allowed(self):
        for path in ['README.md', 'config.example.json', 'src/main.ts', 'doc/design.md']:
            self.assertIsNone(privacy.path_problem(path))

    def test_real_targets_and_absolute_paths_are_rejected(self):
        samples = ['https://' + 'tenant-test' + '.sharepoint.com/sites/x', 'person@' + 'company.invalid', 'C:' + '/' + 'Users/private/file', 'ghp_' + 'A' * 36]
        for text in samples:
            with self.subTest(text=text):
                self.assertTrue(privacy.content_problems(text.encode()))

    def test_placeholders_and_commit_noreply_are_allowed(self):
        text = 'https://example.sharepoint.com/sites/SITE_ID user@example.invalid user@users.noreply.github.com'
        self.assertEqual(privacy.content_problems(text.encode()), [])

    def test_local_deny_and_binary_detection(self):
        self.assertIn('local private deny value', privacy.content_problems(b'Customer Example', ['customer example']))
        self.assertTrue(privacy.content_problems(b'\x00binary'))

    def test_staged_snapshot_and_removed_history_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                subprocess.run(['git', *args], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            git('init')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            file = root / 'README.md'
            file.write_text('Safe example', encoding='utf-8')
            git('add', 'README.md')
            # A later working-tree change must not substitute for staged content.
            private_text = 'https://' + 'fixture-tenant' + '.sharepoint.com/sites/x'
            file.write_text(private_text, encoding='utf-8')
            with patch.object(privacy, 'ROOT', root), patch('sys.argv', ['check']), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(privacy.main(), 0)
            git('add', 'README.md')
            with patch.object(privacy, 'ROOT', root), patch('sys.argv', ['check']), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(privacy.main(), 1)
            git('commit', '-m', 'Synthetic leak fixture')
            file.write_text('Clean again', encoding='utf-8')
            git('add', 'README.md')
            git('commit', '-m', 'Remove synthetic leak')
            with patch.object(privacy, 'ROOT', root), patch('sys.argv', ['check', '--history']), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(privacy.main(), 1)

if __name__ == '__main__':
    unittest.main()
