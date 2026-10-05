import unittest
import test_security_rpc as fixture


class HistoryRpcTests(unittest.TestCase):
    setUp = fixture.SecurityRpcTests.setUp
    tearDown = fixture.SecurityRpcTests.tearDown
    call = fixture.SecurityRpcTests.call
    receive = fixture.SecurityRpcTests.receive
    server_setup = """
from pathlib import Path
def seed_history(params):
    if params.get('unreadable'):
        path = Path(server._HISTORY_MANAGER.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * (10 * 1024 * 1024 + 1))
        server._HISTORY_MANAGER._load_from_file()
        return {}
    server._HISTORY_MANAGER.add_record('pdf_tools', 'retained record', {'output_files': ['completed.pdf']})
    return {}
server.ROUTES['test.seed_history'] = seed_history
"""

    def test_zero_count_returns_zero_records(self):
        self.call('test.seed_history', {})
        result = self.call('history.get', {'count': 0, 'current_session': False})
        self.assertEqual(result['result']['records'], [])

    def test_shutdown_preserves_oversized_history_when_session_made_no_changes(self):
        self.call('test.seed_history', {'unreadable': True})
        result = self.call('history.get', {'current_session': False})
        self.assertIn('过大', result['result']['storage_error'])
        self.call('shutdown', {})
        self.proc.wait(timeout=5)
        self.assertEqual((self.root / 'FileToolbox' / 'history.json').stat().st_size, 10 * 1024 * 1024 + 1)
