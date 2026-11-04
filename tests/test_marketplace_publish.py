import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import catalog, publish_marketplace


class MarketplacePublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.claude-plugin').mkdir()
        self.document = {'name': 'fixture', 'owner': {'name': 'Fixture'}, 'metadata': {'version': '0.1.0'},
                         'plugins': [{'name': 'web-blink', 'version': '0.11.0', 'source': {
                             'source': 'url', 'url': 'https://github.com/patrick-1984/web-blink.git', 'ref': 'v0.11.0'}}]}
        (self.root / catalog.CATALOG).write_text(json.dumps(self.document))
        (self.root / 'README.md').write_text(catalog.readme(self.document, '# Fixture\n'))
        (self.root / 'release-lock.json').write_text('{}\n')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.commit()
        self.tag_exists, self.release_exists, self.draft, self.fail_publish = False, False, False, False
        self.calls = []
        self.real_run = publish_marketplace.run

    def commit(self):
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'Catalog fixture'], cwd=self.root, check=True)

    def command(self, *args, root, required=True):
        self.calls.append(args)
        def result(code=0, output=''):
            return subprocess.CompletedProcess(args, code, output, '')
        if args[:2] == ('git', 'ls-remote'):
            return result(0 if self.tag_exists else 2, 'present' if self.tag_exists else '')
        if args[:2] == ('git', 'fetch'):
            return result()
        if args[:3] == ('gh', 'release', 'view'):
            if not self.release_exists:
                return result(1)
            target = self.real_run('git', 'rev-parse', 'HEAD', root=root).stdout.strip()
            return result(output=json.dumps({'isDraft': self.draft, 'targetCommitish': target}))
        if args[:3] == ('gh', 'release', 'create'):
            self.release_exists, self.draft = True, True
            notes = Path(args[args.index('--notes-file') + 1]).read_text()
            self.assertIn('| `web-blink` | 0.11.0 |', notes)
            self.assertIn('--draft', args)
            return result()
        if args[:3] == ('gh', 'release', 'edit'):
            if self.fail_publish:
                raise RuntimeError('Simulated publication interruption')
            if not self.tag_exists:
                self.real_run('git', 'tag', 'v0.1.0', root=root)
            self.tag_exists, self.draft = True, False
            return result()
        return self.real_run(*args, root=root, required=required)

    def test_release_is_published_once_and_repeating_it_is_a_no_op(self):
        with mock.patch.object(publish_marketplace, 'run', side_effect=self.command):
            first = publish_marketplace.publish(self.root)
            second = publish_marketplace.publish(self.root)
        self.assertTrue(first['published'])
        self.assertTrue(second['alreadyPublished'])
        self.assertEqual(sum(c[:3] == ('gh', 'release', 'create') for c in self.calls), 1)
        self.assertEqual(sum(c[:3] == ('gh', 'release', 'edit') for c in self.calls), 1)

    def test_existing_draft_is_recovered_without_a_duplicate_release(self):
        self.fail_publish = True
        with mock.patch.object(publish_marketplace, 'run', side_effect=self.command):
            with self.assertRaises(RuntimeError):
                publish_marketplace.publish(self.root)
            self.fail_publish = False
            self.assertTrue(publish_marketplace.publish(self.root)['published'])
        self.assertEqual(sum(c[:3] == ('gh', 'release', 'create') for c in self.calls), 1)

    def test_uncommitted_catalog_is_not_published(self):
        changed = {**self.document, 'metadata': {'version': '0.1.1'}}
        (self.root / catalog.CATALOG).write_text(json.dumps(changed))
        (self.root / 'README.md').write_text(catalog.readme(changed, '# Changed\n'))
        with mock.patch.object(publish_marketplace, 'run', side_effect=self.command), self.assertRaises(RuntimeError):
            publish_marketplace.publish(self.root)
        self.assertFalse(any(c[0] == 'gh' for c in self.calls))

    def test_an_existing_tag_cannot_be_replaced_with_different_plugin_pins(self):
        self.real_run('git', 'tag', 'v0.1.0', root=self.root)
        self.tag_exists = True
        self.document['plugins'][0]['version'] = '0.12.0'
        self.document['plugins'][0]['source']['ref'] = 'v0.12.0'
        (self.root / catalog.CATALOG).write_text(json.dumps(self.document))
        (self.root / 'README.md').write_text(catalog.readme(self.document, '# Changed\n'))
        self.commit()
        with mock.patch.object(publish_marketplace, 'run', side_effect=self.command), self.assertRaises(ValueError):
            publish_marketplace.publish(self.root)
        self.assertFalse(any(c[0] == 'gh' for c in self.calls))

    def test_historical_publication_does_not_move_latest_backwards(self):
        self.real_run('git', 'tag', 'v0.2.0', root=self.root)
        with mock.patch.object(publish_marketplace, 'run', side_effect=self.command):
            publish_marketplace.publish(self.root)
        edited = next(c for c in self.calls if c[:3] == ('gh', 'release', 'edit'))
        self.assertIn('--latest=false', edited)


if __name__ == '__main__':
    unittest.main()
