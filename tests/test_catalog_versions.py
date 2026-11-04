import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts import catalog
from scripts import sync_release


def document(version='0.1.9'):
    return {'name': 'fixture', 'owner': {'name': 'Fixture'}, 'metadata': {'version': version},
            'plugins': [{'name': 'web-blink', 'version': '0.11.0',
                         'source': {'source': 'url', 'url': sync_release.SOURCE, 'ref': 'v0.11.0'}}]}


def proof(version='0.11.0'):
    return {'tag': 'v' + version, 'version': version, 'zipSha256': '0' * 64, 'sourceCommit': '1' * 40}


class CatalogVersionTests(unittest.TestCase):
    def test_every_web_blink_version_change_bumps_only_marketplace_patch(self):
        for release in ('0.11.1', '0.12.0', '1.0.0'):
            with self.subTest(release=release), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / '.claude-plugin').mkdir()
                (root / catalog.CATALOG).write_text(json.dumps(document()))
                (root / 'README.md').write_text('# Fixture\n')
                (root / 'release-lock.json').write_text(json.dumps({'web-blink': proof(), 'other': {'tag': 'v2.0.0'}}))
                result = sync_release.apply_verified(root, proof(release))
                current = json.loads((root / catalog.CATALOG).read_text())
                self.assertTrue(result['changed'])
                self.assertEqual(result['marketplaceVersion'], '0.1.10')
                self.assertEqual(current['plugins'][0]['version'], release)
                self.assertEqual(current['plugins'][0]['source']['ref'], 'v' + release)
                self.assertEqual(json.loads((root / 'release-lock.json').read_text())['other'], {'tag': 'v2.0.0'})
                readme = (root / 'README.md').read_text()
                self.assertIn(f'| `web-blink` | {release} |', readme)
                self.assertTrue(readme.endswith(catalog.END + '\n'))
                self.assertFalse(sync_release.apply_verified(root, proof(release))['changed'])
                self.assertEqual(catalog.version(json.loads((root / catalog.CATALOG).read_text())), '0.1.10')

    def test_new_plugin_requires_minor_even_with_another_plugin_update(self):
        base = document()
        current = copy.deepcopy(base)
        current['plugins'].append({'name': 'second', 'version': '1.0.0', 'source': './second'})
        current['plugins'][0]['version'] = '1.0.0'
        current['plugins'][0]['source']['ref'] = 'v1.0.0'
        self.assertEqual(catalog.expected_version(base, current), '0.2.0')
        current['metadata']['version'] = '0.1.10'
        with self.assertRaises(ValueError):
            catalog.check_transition(base, current)
        current['metadata']['version'] = '0.2.0'
        catalog.check_transition(base, current)

    def test_plugin_update_cannot_omit_bump_or_copy_plugin_minor_major(self):
        base = document()
        for incorrect in ('0.1.9', '0.2.0', '1.0.0'):
            current = copy.deepcopy(base)
            current['plugins'][0].update(version='1.0.0', source={'source': 'url', 'url': sync_release.SOURCE, 'ref': 'v1.0.0'})
            current['metadata']['version'] = incorrect
            with self.subTest(version=incorrect), self.assertRaises(ValueError):
                catalog.check_transition(base, current)

    def test_readme_table_stays_last_without_losing_later_prose(self):
        current = document()
        text = catalog.readme(current, '# Fixture\n') + '\n## Later section\nKeep this text.\n'
        updated = catalog.readme(current, text)
        self.assertIn('Keep this text.', updated)
        self.assertLess(updated.index('Keep this text.'), updated.index(catalog.START))
        self.assertTrue(updated.endswith(catalog.END + '\n'))
        self.assertEqual(catalog.readme(current, updated), updated)
        self.assertEqual(updated.count(catalog.START), 1)

    def test_every_plugin_is_in_the_generated_table_and_requires_a_version(self):
        current = document()
        current['plugins'].append({'name': 'second', 'version': '2.3.4', 'source': './second'})
        self.assertIn('| `second` | 2.3.4 |', catalog.table(current))
        del current['plugins'][1]['version']
        with self.assertRaises(ValueError):
            catalog.table(current)

    def test_same_tag_changed_receipt_is_rejected_without_any_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.claude-plugin').mkdir()
            (root / catalog.CATALOG).write_text(json.dumps(document()))
            (root / 'README.md').write_text(catalog.readme(document(), '# Fixture\n'))
            (root / 'release-lock.json').write_text(json.dumps({'web-blink': proof()}))
            files = [root / catalog.CATALOG, root / 'README.md', root / 'release-lock.json']
            before = [path.read_bytes() for path in files]
            with self.assertRaises(ValueError):
                sync_release.apply_verified(root, {**proof(), 'sourceCommit': '2' * 40})
            self.assertEqual([path.read_bytes() for path in files], before)

    def test_manual_prepare_is_idempotent_relative_to_the_base_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.claude-plugin').mkdir()
            (root / 'scripts').mkdir()
            shutil.copyfile(Path(catalog.__file__), root / 'scripts/catalog.py')
            base = document()
            (root / catalog.CATALOG).write_text(json.dumps(base))
            (root / 'README.md').write_text(catalog.readme(base, '# Fixture\n'))
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                            'commit', '-qm', 'Base catalog'], cwd=root, check=True)
            current = copy.deepcopy(base)
            current['plugins'][0].update(version='0.12.0', source={'source': 'url', 'url': sync_release.SOURCE, 'ref': 'v0.12.0'})
            (root / catalog.CATALOG).write_text(json.dumps(current))
            command = [sys.executable, str(root / 'scripts/catalog.py'), 'prepare', '--base-ref', 'HEAD']
            for _ in range(2):
                subprocess.run(command, cwd=root, check=True, capture_output=True)
            self.assertEqual(catalog.version(json.loads((root / catalog.CATALOG).read_text())), '0.1.10')
            catalog.check(root, base)

    def test_bootstrap_is_repeatable_and_moving_plugin_refs_are_rejected(self):
        base = document()
        del base['metadata']['version']
        self.assertEqual(catalog.expected_version(base, document('0.1.0')), '0.1.0')
        current = document()
        current['plugins'][0]['source']['ref'] = 'plugin'
        with self.assertRaises(ValueError):
            catalog.validate(current)


if __name__ == '__main__':
    unittest.main()
