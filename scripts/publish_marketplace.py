"""Publish the versioned catalog as an idempotent GitHub marketplace release."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

if __package__:
    from .catalog import CATALOG, END, START, SEMVER, check, parts, readme, table, version
    from .commit_catalog import annotated_tag
else:
    from catalog import CATALOG, END, START, SEMVER, check, parts, readme, table, version
    from commit_catalog import annotated_tag

REPOSITORY = 'patrick-1984/marketplace'


def run(*args, root, required=True):
    result = subprocess.run(args, cwd=root, text=True, capture_output=True)
    if required and result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Marketplace publication command failed')
    return result


def check_commit(root, ref, document):
    commit = run('git', 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}', root=root).stdout.strip()
    published = json.loads(run('git', 'show', commit + ':' + CATALOG, root=root).stdout)
    text = run('git', 'show', commit + ':README.md', root=root).stdout
    if published != document or readme(published, text) != text:
        raise ValueError('Release tag/target does not contain this catalog and its matching final README table')
    return commit


def publish(root):
    document = check(root)
    # Publish committed files, never an uncommitted version number or README table.
    run('git', 'diff', '--exit-code', 'HEAD', '--', CATALOG, 'README.md', 'release-lock.json', root=root)
    tag = 'v' + version(document)
    commit = run('git', 'rev-parse', 'HEAD', root=root).stdout.strip()
    existing_tag = run('git', 'ls-remote', '--exit-code', '--tags', 'origin', 'refs/tags/' + tag,
                       root=root, required=False)
    if existing_tag.returncode not in (0, 2):
        raise RuntimeError('Could not inspect the marketplace release tag')
    if existing_tag.returncode == 0:
        run('git', 'fetch', '--quiet', 'origin', 'tag', tag, root=root)
        check_commit(root, 'refs/tags/' + tag, document)
    release = run('gh', 'release', 'view', tag, '--repo', REPOSITORY,
                  '--json', 'isDraft,targetCommitish', root=root, required=False)
    if release.returncode == 0:
        info = json.loads(release.stdout)
        if not info['isDraft']:
            if existing_tag.returncode != 0:
                raise ValueError('Published marketplace release has no matching source tag')
            return {'tag': tag, 'published': False, 'alreadyPublished': True}
        target = 'refs/tags/' + tag if existing_tag.returncode == 0 else info['targetCommitish']
        commit = check_commit(root, target, document)
    else:
        check_commit(root, 'HEAD', document)
    if existing_tag.returncode != 0:
        annotated_tag(root, tag, commit)
        run('git', 'fetch', '--quiet', 'origin', 'tag', tag, root=root)
    if release.returncode != 0:
        notes = ('Versioned catalog release. Linked plugin updates bump marketplace patch; adding a plugin bumps minor.\n\n'
                 + table(document).replace(START + '\n', '').replace('\n' + END, '') + '\n')
        with tempfile.TemporaryDirectory(prefix='marketplace-release-notes-') as tmp:
            path = Path(tmp) / 'notes.md'
            path.write_text(notes, encoding='utf-8')
            run('gh', 'release', 'create', tag, '--repo', REPOSITORY, '--target', commit,
                '--verify-tag', '--title', 'patrick-1984 marketplace ' + tag,
                '--notes-file', str(path), '--draft', root=root)
    # Recover a failed prior draft publication; never move latest backwards for a queued older catalog.
    tags = run('git', 'tag', '--list', 'v*', root=root).stdout.splitlines()
    newer = any(t.startswith('v') and SEMVER.fullmatch(t[1:]) and parts(t[1:]) > parts(version(document)) for t in tags)
    run('gh', 'release', 'edit', tag, '--repo', REPOSITORY, '--draft=false',
        '--latest=' + ('false' if newer else 'true'), root=root)
    return {'tag': tag, 'published': True, 'alreadyPublished': False}


if __name__ == '__main__':
    print(json.dumps(publish(Path(__file__).resolve().parents[1])))
