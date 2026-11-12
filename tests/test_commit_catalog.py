"""Owner-attributed publication through a simulated GitHub API and real Git objects."""
import base64
import copy
import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import catalog, commit_catalog, publish_marketplace


class CatalogCommitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='marketplace-publication-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root, self.remote = self.directory / 'work', self.directory / 'remote.git'
        self.root.mkdir()
        self.run_git(self.root, 'init', '-q', '-b', 'main')
        self.run_git(self.directory, 'init', '--bare', '-q', str(self.remote))
        (self.root / '.claude-plugin').mkdir()
        self.document = {'name': 'fixture', 'owner': {'name': 'Fixture'}, 'metadata': {'version': '0.1.0'},
                         'plugins': [{'name': 'web-blink', 'version': '0.11.0', 'source': {
                             'source': 'url', 'url': 'https://github.com/patrick-1984/web-blink.git', 'ref': 'v0.11.0'}}]}
        self.write_catalog()
        (self.root / 'keep.txt').write_text('Unrelated tracked content\n')
        self.run_git(self.root, 'add', '.')
        self.run_git(self.root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     'commit', '-qm', 'Initial fixture')
        self.before = self.run_git(self.root, 'rev-parse', 'HEAD')
        self.run_git(self.remote, 'fetch', '-q', str(self.root), 'main:main')
        self.run_git(self.root, 'remote', 'add', 'origin', str(self.remote))
        self.calls, self.race, self.tamper_identity = [], False, False
        self.stale_reads = 0
        self.mutate_during = None
        self.owner_login = 'patrick-1984'
        self.document['metadata']['version'] = '0.1.1'
        self.document['plugins'][0]['version'] = '0.12.0'
        self.document['plugins'][0]['source']['ref'] = 'v0.12.0'
        self.write_catalog()

    def run_git(self, root, *args, content=None, identity=None, index=None):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('GIT_AUTHOR_', 'GIT_COMMITTER_'))}
        if identity:
            for who in ('AUTHOR', 'COMMITTER'):
                env.update({f'GIT_{who}_NAME': identity['name'], f'GIT_{who}_EMAIL': identity['email'],
                            f'GIT_{who}_DATE': identity['date']})
        if index:
            env['GIT_INDEX_FILE'] = str(index)
        result = subprocess.run(['git', *args], cwd=root, env=env, input=content,
                                text=True, capture_output=True, check=True)
        return result.stdout.strip()

    def write_catalog(self):
        (self.root / catalog.CATALOG).write_text(json.dumps(self.document))
        (self.root / 'README.md').write_text(catalog.readme(self.document, '# Fixture\n'))
        (self.root / 'release-lock.json').write_text(json.dumps({'version': catalog.version(self.document)}))

    def api(self, root, path, method='GET', data=None):
        self.calls.append((path, method, copy.deepcopy(data)))
        prefix = 'repos/' + commit_catalog.REPOSITORY
        if self.mutate_during == (path, method):
            (root / 'README.md').write_text('Unvalidated concurrent README bytes\n')
            self.mutate_during = None
        if path == prefix:
            return {'full_name': commit_catalog.REPOSITORY, 'node_id': 'fixture-repository',
                    'owner': {'login': self.owner_login, 'id': 235861494}}
        if path.startswith(prefix + '/git/ref/heads/main'):
            if '?publication=' in path and self.stale_reads:
                self.stale_reads -= 1
                return {'object': {'sha': self.before}}
            return {'object': {'sha': self.run_git(self.remote, 'rev-parse', 'main')}}
        if method == 'GET' and path.startswith(prefix + '/git/commits/'):
            sha = path.rsplit('/', 1)[-1]
            return {'tree': {'sha': self.run_git(self.remote, 'rev-parse', sha + '^{tree}')}}
        if path == prefix + '/git/blobs':
            content = base64.b64decode(data['content']).decode()
            return {'sha': self.run_git(self.remote, 'hash-object', '-w', '--stdin', content=content)}
        if path == prefix + '/git/trees':
            index = self.directory / 'api-tree-index'
            self.run_git(self.remote, 'read-tree', data['base_tree'], index=index)
            for entry in data['tree']:
                self.run_git(self.remote, 'update-index', '--add', '--cacheinfo',
                             entry['mode'], entry['sha'], entry['path'], index=index)
            return {'sha': self.run_git(self.remote, 'write-tree', index=index)}
        if path == prefix + '/git/commits':
            self.assertEqual(data['author'], data['committer'])
            sha = self.run_git(self.remote, 'commit-tree', data['tree'], '-p', data['parents'][0],
                               content=data['message'] + '\n', identity=data['author'])
            result = {'sha': sha, 'tree': {'sha': data['tree']},
                      'parents': [{'sha': value} for value in data['parents']],
                      'author': data['author'], 'committer': data['committer']}
            if self.tamper_identity:
                result['author'] = {**result['author'], 'name': 'unexpected'}
            return result
        if path == prefix + '/git/tags':
            tagger = data['tagger']
            timestamp = int(datetime.datetime.fromisoformat(tagger['date']).timestamp())
            content = (f"object {data['object']}\ntype commit\ntag {data['tag']}\n"
                       f"tagger {tagger['name']} <{tagger['email']}> {timestamp} +0000\n\n"
                       + data['message'] + '\n')
            return {'sha': self.run_git(self.remote, 'mktag', content=content), 'tagger': tagger}
        if path == prefix + '/git/refs':
            self.run_git(self.remote, 'update-ref', data['ref'], data['sha'], '0' * 40)
            return {'ref': data['ref']}
        if path == 'graphql':
            update = data['variables']['input']['refUpdates'][0]
            self.assertFalse(update['force'])
            self.assertEqual(update['name'], 'refs/heads/main')
            if self.race:
                tree = self.run_git(self.remote, 'rev-parse', 'main^{tree}')
                other = self.run_git(self.remote, 'commit-tree', tree, '-p', self.before,
                                     content='Concurrent update\n', identity={
                                         'name': 'Fixture', 'email': 'fixture@example.invalid',
                                         'date': '2026-11-12T00:00:00+00:00'})
                self.run_git(self.remote, 'update-ref', 'refs/heads/main', other, self.before)
                raise RuntimeError('GitHub rejected the stale atomic ref update')
            self.run_git(self.remote, 'update-ref', update['name'], update['afterOid'], update['beforeOid'])
            return {'data': {'updateRefs': {'clientMutationId': None}}}
        raise AssertionError('Unexpected GitHub API operation ' + path)

    def publish(self):
        with mock.patch.object(commit_catalog, 'api', side_effect=self.api):
            return commit_catalog.publish(self.root)

    def test_owner_commit_preserves_parent_tree_and_working_bytes(self):
        files = {name: (self.root / name).read_bytes() for name in commit_catalog.FILES}
        with mock.patch.dict(os.environ, {'GIT_AUTHOR_NAME': 'Wrong', 'GIT_AUTHOR_EMAIL': 'wrong@example.invalid',
                                          'GIT_COMMITTER_NAME': 'Wrong', 'GIT_COMMITTER_EMAIL': 'wrong@example.invalid'}):
            result = self.publish()
        self.assertTrue(result['changed'])
        self.assertEqual(self.run_git(self.root, 'rev-parse', 'HEAD^'), self.before)
        self.assertEqual(self.run_git(self.root, 'rev-parse', 'HEAD'), result['commit'])
        identity = self.run_git(self.root, 'log', '-1', '--format=%an|%ae|%cn|%ce')
        expected = 'patrick-1984|235861494+patrick-1984@users.noreply.github.com'
        self.assertEqual(identity, expected + '|' + expected)
        self.assertEqual((self.root / 'keep.txt').read_text(), 'Unrelated tracked content\n')
        self.assertEqual({name: (self.root / name).read_bytes() for name in files}, files)
        self.assertEqual(self.run_git(self.root, 'diff', '--name-only', 'HEAD'), '')
        self.calls.clear()
        self.assertEqual(self.publish(), {'changed': False})
        self.assertEqual(self.calls, [])

    def test_stale_post_update_read_is_retried_with_a_fresh_cache_key(self):
        self.stale_reads = 1
        result = self.publish()
        reads = [path for path, method, _ in self.calls if '?publication=' in path]
        self.assertEqual(len(reads), 2)
        self.assertNotEqual(reads[0], reads[1])
        self.assertEqual(self.run_git(self.root, 'rev-parse', 'HEAD'), result['commit'])

    def test_unrelated_staged_path_is_refused_before_github(self):
        (self.root / ' README.md').write_text('Do not publish\n')
        self.run_git(self.root, 'add', ' README.md')
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.run_git(self.root, 'rev-parse', 'HEAD'), self.before)

    def test_repository_owner_must_match_expected_repository(self):
        self.owner_login = 'unexpected'
        with self.assertRaises(RuntimeError):
            self.publish()
        self.assertTrue(all(method == 'GET' for _, method, _ in self.calls))

    def test_unexpected_commit_identity_cannot_advance_remote_or_local_head(self):
        self.tamper_identity = True
        with self.assertRaises(RuntimeError):
            self.publish()
        self.assertFalse(any(path == 'graphql' for path, _, _ in self.calls))
        self.assertEqual(self.run_git(self.remote, 'rev-parse', 'main'), self.before)
        self.assertEqual(self.run_git(self.root, 'rev-parse', 'HEAD'), self.before)

    def test_atomic_race_preserves_concurrent_remote_and_local_work(self):
        self.race = True
        with self.assertRaises(RuntimeError):
            self.publish()
        self.assertNotEqual(self.run_git(self.remote, 'rev-parse', 'main'), self.before)
        self.assertEqual(self.run_git(self.root, 'rev-parse', 'HEAD'), self.before)
        self.assertEqual(json.loads((self.root / catalog.CATALOG).read_text()), self.document)

    def test_api_time_working_file_mutation_never_publishes_unvalidated_bytes(self):
        for during in (('repos/' + commit_catalog.REPOSITORY, 'GET'),
                       ('repos/' + commit_catalog.REPOSITORY + '/git/blobs', 'POST')):
            with self.subTest(during=during):
                self.write_catalog()
                self.calls.clear()
                self.mutate_during = during
                with self.assertRaisesRegex(RuntimeError, 'changed during publication'):
                    self.publish()
                self.assertFalse(any(path == 'graphql' for path, _, _ in self.calls))
                self.assertEqual(self.run_git(self.remote, 'rev-parse', 'main'), self.before)
                self.assertEqual(self.run_git(self.root, 'rev-parse', 'HEAD'), self.before)
                self.assertEqual((self.root / 'README.md').read_text(), 'Unvalidated concurrent README bytes\n')
                for path, method, data in self.calls:
                    if path.endswith('/git/blobs'):
                        self.assertNotIn(b'Unvalidated concurrent', base64.b64decode(data['content']))

    def test_current_main_checkout_repairs_release_after_successful_ref_update(self):
        # The first attempt advances remote main but every confirmation read is stale.
        self.stale_reads = 4
        with self.assertRaisesRegex(RuntimeError, 'retry verification'):
            self.publish()
        self.assertEqual(self.run_git(self.root, 'rev-parse', 'HEAD'), self.before)
        published = self.run_git(self.remote, 'rev-parse', 'main')
        self.assertNotEqual(published, self.before)
        fresh = self.directory / 'retry-work'
        self.run_git(self.directory, 'clone', '-q', '--branch', 'main', str(self.remote), str(fresh))
        self.root = fresh
        self.calls.clear()
        self.assertEqual(self.publish(), {'changed': False})
        self.assertEqual(self.calls, [])

        real_run = publish_marketplace.run
        def command(*args, root, required=True):
            if args[:3] == ('gh', 'release', 'view'):
                return subprocess.CompletedProcess(args, 1, '', '')
            if args[:3] == ('gh', 'release', 'create'):
                self.assertIn('--verify-tag', args)
                return subprocess.CompletedProcess(args, 0, '', '')
            if args[:3] == ('gh', 'release', 'edit'):
                return subprocess.CompletedProcess(args, 0, '', '')
            return real_run(*args, root=root, required=required)
        with mock.patch.object(commit_catalog, 'api', side_effect=self.api), \
                mock.patch.object(publish_marketplace, 'run', side_effect=command):
            self.assertTrue(publish_marketplace.publish(fresh)['published'])
        self.assertEqual(self.run_git(fresh, 'rev-parse', 'refs/tags/v0.1.1^{commit}'), published)
        self.assertEqual(self.run_git(fresh, 'cat-file', '-t', 'refs/tags/v0.1.1'), 'tag')

        root = Path(__file__).resolve().parents[1]
        sync = (root / '.github/workflows/sync-release.yml').read_text()
        self.assertRegex(sync, r'uses: actions/checkout@v4\s+with:\s+ref: main\s+fetch-depth: 0')
        publish = (root / '.github/workflows/publish.yml').read_text()
        self.assertNotIn('ref: main', publish)
