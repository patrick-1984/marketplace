import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('sync_release', Path(__file__).resolve().parents[1] / 'scripts/sync_release.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def fixture(version='0.10.1'):
    files = {name: b'fixture\n' for name in sync.REQUIRED}
    for name in ('.claude-plugin/plugin.json', '.codex-plugin/plugin.json'):
        files[name] = json.dumps({'name': 'web-blink', 'version': version}).encode()
    receipt = {'plugin': {'version': version}, 'files': [
        {'path': name, 'sha256': hashlib.sha256(body).hexdigest()} for name, body in files.items()]}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, body in files.items():
            archive.writestr(name, body)
        archive.writestr('_build.json', json.dumps(receipt))
    body = output.getvalue()
    digest = hashlib.sha256(body).hexdigest()
    name = f'web-blink-{version}.zip'
    release = {'tag_name': 'v' + version, 'draft': False, 'prerelease': False,
               'assets': [{'name': name, 'digest': 'sha256:' + digest}]}
    return release, body, (digest + '  ' + name + '\n').encode()


class ReleaseSyncTests(unittest.TestCase):
    def test_complete_release_is_verified(self):
        result = sync.verify_release(*fixture())
        self.assertEqual(result['tag'], 'v0.10.1')
        self.assertEqual(result['version'], '0.10.1')
        self.assertTrue(sync.REQUIRED <= set(result['files']))

    def test_bad_checksum_is_rejected(self):
        release, body, checksums = fixture()
        with self.assertRaises(ValueError):
            sync.verify_release(release, body + b'corruption', checksums)

    def test_draft_prerelease_and_known_unsafe_release_are_rejected(self):
        for key in ('draft', 'prerelease'):
            release, body, checksums = fixture()
            release[key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                sync.verify_release(release, body, checksums)
        with self.assertRaises(ValueError):
            sync.verify_release(*fixture('0.10.0'))

    def test_catalog_changes_ref_and_version_without_touching_other_plugins(self):
        document = {'plugins': [{'name': 'web-blink', 'source': {'source': 'url', 'url': sync.SOURCE, 'ref': 'plugin'}},
                                {'name': 'other', 'source': './other'}]}
        self.assertTrue(sync.update_catalog(document, {'tag': 'v0.10.1', 'version': '0.10.1'}))
        self.assertEqual(document['plugins'][0]['source']['ref'], 'v0.10.1')
        self.assertEqual(document['plugins'][0]['version'], '0.10.1')
        self.assertEqual(document['plugins'][1], {'name': 'other', 'source': './other'})
        self.assertFalse(sync.update_catalog(document, {'tag': 'v0.10.1', 'version': '0.10.1'}))

    def test_downgrade_and_wrong_source_are_rejected(self):
        document = {'plugins': [{'name': 'web-blink', 'source': {'url': sync.SOURCE, 'ref': 'v0.11.0'}}]}
        with self.assertRaises(ValueError):
            sync.update_catalog(document, {'tag': 'v0.10.1', 'version': '0.10.1'})
        document['plugins'][0]['source']['url'] = 'https://example.invalid/untrusted.git'
        with self.assertRaises(ValueError):
            sync.update_catalog(document, {'tag': 'v0.12.0', 'version': '0.12.0'})


if __name__ == '__main__':
    unittest.main()
