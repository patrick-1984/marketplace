"""Publish only the verified catalog files with explicit repository-owner attribution."""
from __future__ import annotations

import base64
import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

if __package__:
    from .catalog import CATALOG, base_catalog, check, entries, version
else:
    from catalog import CATALOG, base_catalog, check, entries, version

REPOSITORY = 'patrick-1984/marketplace'
FILES = (CATALOG, 'release-lock.json', 'README.md')


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Local Git operation failed')
    return result.stdout


def api(root, path, method='GET', data=None):
    args = ['gh', 'api', path, '--method', method]
    if data is not None:
        args += ['--input', '-']
    result = subprocess.run(args, cwd=root, input=json.dumps(data) if data is not None else None,
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'GitHub publication request failed')
    value = json.loads(result.stdout)
    if isinstance(value, dict) and value.get('errors'):
        raise RuntimeError('GitHub rejected the atomic publication request')
    return value


def owner(root):
    repository = api(root, 'repos/' + REPOSITORY)
    account = repository['owner']
    if (repository.get('full_name') != REPOSITORY or account.get('login') != REPOSITORY.split('/')[0]
            or type(account.get('id')) is not int or account['id'] <= 0):
        raise RuntimeError('Repository owner identity could not be verified')
    identity = {'name': account['login'],
                'email': f"{account['id']}+{account['login']}@users.noreply.github.com",
                'date': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}
    return repository, identity


def annotated_tag(root, tag, commit):
    _, identity = owner(root)
    prefix = 'repos/' + REPOSITORY
    result = api(root, prefix + '/git/tags', 'POST', {
        'tag': tag, 'message': 'Marketplace ' + tag, 'object': commit, 'type': 'commit', 'tagger': identity})
    if any(result.get('tagger', {}).get(key) != identity[key] for key in ('name', 'email')):
        raise RuntimeError('Annotated release tag has an unexpected identity')
    api(root, prefix + '/git/refs', 'POST', {'ref': 'refs/tags/' + tag, 'sha': result['sha']})
    return result['sha']


def changed_files(root):
    # The union includes staged-only changes even if the worktree has been reverted.
    changed = set()
    for args in (('diff', '--name-only', '-z', '--no-renames'),
                 ('diff', '--cached', '--name-only', '-z', '--no-renames')):
        changed.update(name for name in git(root, *args).split('\0') if name)
    return changed


def confirm_snapshot(root, expected, payloads):
    if git(root, 'rev-parse', 'HEAD').strip() != expected or changed_files(root) - set(FILES):
        raise RuntimeError('Local publication context changed; preserve and review the current edits')
    for name, content in payloads.items():
        path = root / name
        if (path.is_symlink() or not path.is_file() or path.read_bytes() != content
                or os.name != 'nt' and path.stat().st_mode & 0o111):
            raise RuntimeError('Catalog files changed during publication; preserve and review the current edits')


def publish(root):
    root = Path(root)
    expected = git(root, 'rev-parse', 'HEAD').strip()
    changed = changed_files(root)
    if changed - set(FILES):
        raise ValueError('Catalog publication refuses unrelated tracked changes')
    if not changed:
        return {'changed': False}
    payloads = {}
    for name in FILES:
        path = root / name
        if (path.is_symlink() or not path.is_file()
                or os.name != 'nt' and path.stat().st_mode & 0o111):
            raise ValueError('Catalog publication requires regular non-executable files')
        payloads[name] = path.read_bytes()
    # Validation and upload consume this one frozen snapshot. Local edits made
    # during later API calls can neither change the approved bytes nor be lost.
    with tempfile.TemporaryDirectory(prefix='marketplace-catalog-snapshot-') as directory:
        snapshot = Path(directory)
        for name, content in payloads.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        document = check(snapshot, base_catalog(root, expected))
    repository, identity = owner(root)
    confirm_snapshot(root, expected, payloads)
    prefix = 'repos/' + REPOSITORY
    before = api(root, prefix + '/git/ref/heads/main')['object']['sha']
    if before != expected:
        raise RuntimeError('Marketplace main moved; synchronize and verify again')
    parent = api(root, prefix + '/git/commits/' + expected)
    tree = []
    for name, content in sorted(payloads.items()):
        blob = api(root, prefix + '/git/blobs', 'POST', {
            'encoding': 'base64', 'content': base64.b64encode(content).decode('ascii')})
        tree.append({'path': name, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
    result = api(root, prefix + '/git/trees', 'POST', {'base_tree': parent['tree']['sha'], 'tree': tree})
    message = (f"Release marketplace v{version(document)} for web-blink "
               f"v{entries(document)['web-blink']['version']}")
    commit = api(root, prefix + '/git/commits', 'POST', {
        'message': message, 'tree': result['sha'], 'parents': [expected],
        'author': identity, 'committer': identity})
    if ([row['sha'] for row in commit.get('parents', [])] != [expected]
            or commit.get('tree', {}).get('sha') != result['sha']
            or any(commit.get(who, {}).get(key) != identity[key]
                   for who in ('author', 'committer') for key in ('name', 'email'))):
        raise RuntimeError('Created publication commit does not match the verified parent and owner')
    # GitHub performs the expected-parent comparison atomically with the ref update.
    confirm_snapshot(root, expected, payloads)
    acknowledgement = api(root, 'graphql', 'POST', {
        'query': 'mutation($input:UpdateRefsInput!){updateRefs(input:$input){clientMutationId}}',
        'variables': {'input': {'repositoryId': repository['node_id'], 'refUpdates': [{
            'name': 'refs/heads/main', 'beforeOid': expected, 'afterOid': commit['sha'], 'force': False}]}}})
    if acknowledgement.get('data', {}).get('updateRefs') is None:
        raise RuntimeError('GitHub did not acknowledge the atomic ref update')
    # Ref reads can briefly serve a stale cached result after a successful write.
    # Each verification has a distinct query key and a bounded replication wait.
    for attempt in range(4):
        observed = api(root, prefix + '/git/ref/heads/main?publication=' + commit['sha']
                       + '&attempt=' + str(attempt))['object']['sha']
        if observed == commit['sha']:
            break
        if attempt < 3:
            time.sleep(0.1 * 2 ** attempt)
    else:
        raise RuntimeError('Marketplace ref read did not confirm the acknowledged publication; retry verification')
    # Fetch is read-only. Updating the index/HEAD preserves all working file bytes.
    git(root, 'fetch', '--quiet', 'origin', commit['sha'])
    git(root, 'reset', '--mixed', commit['sha'])
    git(root, 'diff', '--exit-code', 'HEAD', '--', *FILES)
    return {'changed': True, 'commit': commit['sha'], 'owner': identity['name']}


if __name__ == '__main__':
    print(json.dumps(publish(Path(__file__).resolve().parents[1])))
