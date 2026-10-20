"""Standalone receipt-loss/forgery controls; requires explicit valid evidence."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from verify_evidence import verify

BASELINE = None
PLATFORM = None


class VerifierControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='readonly-verifier-unit-')
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'evidence'
        shutil.copytree(BASELINE, self.output)

    def mutate(self, filename, mutation):
        path = self.output / filename
        value = json.loads(path.read_text())
        mutation(value)
        path.write_text(json.dumps(value))

    def refused(self):
        with self.assertRaises((RuntimeError, FileNotFoundError, KeyError, ValueError)):
            verify(self.output, 'all', PLATFORM)

    def admission_refused(self, mode, mutation):
        # Leave the green JUnit and store-comparison receipts unchanged: each
        # failure must come from this admission receipt's own mechanism fields.
        untouched = {name: (self.output / name).read_bytes()
                     for name in ('junit.xml', 'reports.json', 'store-close-comparison.json')}
        self.mutate(mode + '.json', mutation)
        self.refused()
        for name, contents in untouched.items():
            self.assertEqual((self.output / name).read_bytes(), contents)

    def test_valid_exact_evidence_passes(self):
        self.assertTrue(verify(self.output, 'all', PLATFORM)['verified'])

    def test_missing_receipt_refuses(self):
        (self.output / 'vfs-cleanup.json').unlink()
        self.refused()

    def test_duplicate_json_key_refuses(self):
        (self.output / 'network.json').write_text('{"attempts":[],"exit":1,"exit":0}')
        self.refused()

    def test_lost_vfs_restoration_refuses(self):
        self.mutate('vfs-cleanup.json', lambda v: v.update(defaultRestored=False))
        self.refused()

    def test_remaining_native_handle_refuses(self):
        self.mutate('vfs-cleanup.json', lambda v: v.update(remainingFiles=1))
        self.refused()

    def test_missing_collected_id_refuses(self):
        self.mutate('collection.json', lambda v: v['selected'].pop())
        self.refused()

    def test_invented_same_count_id_refuses(self):
        self.mutate('collection.json', lambda v: v['selected'].__setitem__(0, 'invented.py::test_invented'))
        self.refused()

    def test_unexpected_deselection_refuses(self):
        self.mutate('collection.json', lambda v: v['deselected'].append('invented.py::test_deselected'))
        self.refused()

    def test_retried_execution_refuses(self):
        self.mutate('reports.json', lambda v: v.append(dict(v[0])))
        self.refused()

    def test_junit_skip_refuses(self):
        path = self.output / 'junit.xml'
        tree = ET.parse(path)
        ET.SubElement(next(tree.iter('testcase')), 'skipped')
        tree.write(path)
        self.refused()

    def test_missing_positive_lock_refuses(self):
        self.mutate('store-close-comparison.json',
                    lambda v: v['results'][0].update(positiveExclusiveObserved=False))
        self.refused()

    def test_readonly_file_flag_loss_refuses(self):
        self.mutate('store-close-comparison.json',
                    lambda v: v['results'][1]['openFlags'].update(returnedReadonly=False))
        self.refused()

    def test_permission_restoration_loss_refuses(self):
        path = self.output / 'junit.xml'
        tree = ET.parse(path)
        prop = next(prop for prop in tree.iter('property')
                    if prop.get('name') == 'native_permission_restoration')
        value = json.loads(prop.get('value'))
        value[0]['exact'] = False
        prop.set('value', json.dumps(value))
        tree.write(path)
        self.refused()

    def test_source_change_refuses(self):
        self.mutate('source-after.json', lambda v: v.update(storeSha256='0' * 64))
        self.refused()

    def test_process_timeout_refuses(self):
        self.mutate('process.json', lambda v: v.update(timedOut=True))
        self.refused()

    def test_network_attempt_refuses(self):
        self.mutate('network.json', lambda v: v['attempts'].append('socket.connect'))
        self.refused()


def register_admission_controls():
    """Every mode/field has its own unittest ID, never a count-only subtest."""
    modes = ('uncoordinated_close', 'coordinated_close', 'coordinated_stale_owner',
             'ordinary_open_reader', 'released_close', 'readonly_close', 'readonly_stale_owner')

    def add(label, mode, mutation):
        def control(self):
            self.admission_refused(mode, mutation)
        name = 'test_admission_' + mode + '_' + label
        control.__name__ = name
        assert not hasattr(VerifierControls, name)
        setattr(VerifierControls, name, control)

    for mode in modes:
        corruptions = {
            'locks': [{'phase': 'inside_connection_close', 'lock': 'exclusive', 'result': 5}],
            'readErrors': ([{'line': 289, 'class': 'OperationalError', 'code': 5, 'name': 'SQLITE_BUSY'}]
                           if mode == 'uncoordinated_close' else
                           [{'line': 290, 'class': 'OperationalError', 'code': 5, 'name': 'SQLITE_BUSY'}]),
            'closeLocksAtGuard': [1 if mode.startswith('readonly_') or mode == 'ordinary_open_reader' else 0],
            'readonlyEvidence': ({} if mode.startswith('readonly_') else {'queryOnlyFalse': True}),
            'readonlyRefusalPauses': 0 if mode.startswith('readonly_') else 1,
            'coordinatorWaits': 0 if mode.startswith('coordinated_') else 1,
            'connections': 2,
        }
        for field, corrupt in corruptions.items():
            add(field + '_missing', mode, lambda v, field=field: v.pop(field))
            add(field + '_corrupt', mode, lambda v, field=field, corrupt=corrupt: v.update({field: corrupt}))
        for field in ('readonlyRefusalPauses', 'coordinatorWaits', 'connections'):
            add(field + '_boolean_count', mode,
                lambda v, field=field: v.update({field: bool(v[field])}))
        add('closeLocksAtGuard_boolean_count', mode,
            lambda v: v.update(closeLocksAtGuard=[bool(v['closeLocksAtGuard'][0])]))
        add('locks_float_code', mode,
            lambda v: v['locks'][0].update(result=float(v['locks'][0]['result'])))
        add('locks_boolean_code', mode,
            lambda v: v['locks'][0].update(result=bool(v['locks'][0]['result'])))

    for mode in ('readonly_close', 'readonly_stale_owner'):
        corruptions = {'queryOnlyFalse': False, 'dmlError': 'SQLITE_BUSY',
                       'requestedReadonly': False, 'requestedReadwrite': True,
                       'returnedReadonly': False, 'returnedFlagsPresent': False, 'methodVersion': 2}
        for field, corrupt in corruptions.items():
            add('readonlyEvidence_' + field + '_missing', mode,
                lambda v, field=field: v['readonlyEvidence'].pop(field))
            add('readonlyEvidence_' + field + '_corrupt', mode,
                lambda v, field=field, corrupt=corrupt: v['readonlyEvidence'].update({field: corrupt}))
            if isinstance(corrupt, bool):
                add('readonlyEvidence_' + field + '_numeric_boolean', mode,
                    lambda v, field=field: v['readonlyEvidence'].update({field: int(v['readonlyEvidence'][field])}))
        add('readonlyEvidence_float_methodVersion', mode,
            lambda v: v['readonlyEvidence'].update(methodVersion=3.0))

    for field in ('line', 'code'):
        add('readErrors_float_' + field, 'uncoordinated_close',
            lambda v, field=field: v['readErrors'][0].update({field: float(v['readErrors'][0][field])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--platform', required=True, choices=('linux', 'darwin', 'win32'))
    args = parser.parse_args()
    BASELINE, PLATFORM = Path(args.evidence).resolve(strict=True), args.platform
    verify(BASELINE, 'all', PLATFORM)
    if PLATFORM != 'win32':
        register_admission_controls()
    unittest.main(argv=['test_verifier.py'], verbosity=2)
