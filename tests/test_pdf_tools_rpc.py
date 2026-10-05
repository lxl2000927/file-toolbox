import json
import unittest

import test_security_rpc as fixture


class PdfToolsRpcTests(unittest.TestCase):
    setUp = fixture.SecurityRpcTests.setUp
    tearDown = fixture.SecurityRpcTests.tearDown
    receive = fixture.SecurityRpcTests.receive
    call = fixture.SecurityRpcTests.call

    def params(self, action='inspect'):
        return {'files': [str(self.source)], 'action': action,
                'options': {'output_dir': str(self.root / 'outputs')}, 'task_id': 'pdf-tools-test',
                '_input_identities': {str(self.source): self.identity}}

    def test_inspection_and_export_through_real_queued_rpc(self):
        self.assertTrue(self.call('pdf_tools.run', self.params())['result']['queued'])
        self.assertTrue(self.call('ping', {})['result']['pong'])
        self.call('test.release', {})
        inspection = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertTrue(inspection['ok'])
        signature = inspection['result']['sources'][0]['signature']
        params = self.params('assemble')
        params['options']['signatures'] = [signature]
        self.call('pdf_tools.run', params)
        complete = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertTrue(complete['ok'])
        self.assertEqual(len(complete['result']['output_files']), 1)
        history = self.call('history.get', {})['result']['records']
        self.assertEqual(history[0]['operation_type'], 'pdf_tools')

    def test_invalid_input_shape_and_missing_authorization_are_rejected(self):
        for files in [[[str(self.source)]], {str(self.source): True}, False]:
            params = self.params(); params['files'] = files
            self.assertIn('error', self.call('pdf_tools.run', params))
        params = self.params(); params.pop('_input_identities')
        self.assertIn('error', self.call('pdf_tools.run', params))

    def test_queued_file_replacement_does_not_escape_input_guard(self):
        self.call('pdf_tools.run', self.params('assemble'))
        self.source.write_bytes(b'changed')
        self.call('test.release', {})
        complete = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertFalse(complete['ok'])
        self.assertIn('发生变化', complete['error'])
        self.assertFalse((self.root / 'outputs').exists())

    def test_cancel_queued_operation_produces_no_output(self):
        self.call('pdf_tools.run', self.params('assemble'))
        self.assertTrue(self.call('task.cancel', {'task_id': 'pdf-tools-test'})['result']['cancelled'])
        complete = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertTrue(complete['cancelled'])
        self.assertFalse((self.root / 'outputs').exists())

    def test_presets_and_nested_page_paths_cannot_read_another_file(self):
        saved = self.call('presets.save', {'scope': 'workbench', 'name': '归档', 'settings': {'compression': 'lossless'}})['result']
        self.assertEqual(self.call('presets.list', {'scope': 'workbench'})['result'][0]['id'], saved['id'])
        params = self.params('assemble')
        params['options']['pages'] = [{'source': str(self.root / 'private.pdf'), 'index': 0}]
        self.call('pdf_tools.run', params)
        self.call('test.release', {})
        complete = self.receive(lambda r: r.get('method') == 'task.complete')['params']
        self.assertFalse(complete['ok'])
        self.assertFalse((self.root / 'outputs').exists())


if __name__ == '__main__':
    unittest.main()
