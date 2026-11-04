"""Maintain the marketplace version and the final README plugin-version table."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import subprocess

CATALOG = '.claude-plugin/marketplace.json'
START = '<!-- BEGIN plugin versions -->'
END = '<!-- END plugin versions -->'
SEMVER = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
INITIAL_VERSION = '0.1.0'


def parts(version):
    match = SEMVER.fullmatch(version) if isinstance(version, str) else None
    if not match:
        raise ValueError('Use a stable semantic version such as 0.1.0')
    return tuple(map(int, match.groups()))


def bump(version, kind):
    major, minor, patch = parts(version)
    if kind == 'minor':
        return f'{major}.{minor + 1}.0'
    if kind == 'patch':
        return f'{major}.{minor}.{patch + 1}'
    raise ValueError('Marketplace updates use patch, or minor when adding a plugin')


def version(document):
    value = document.get('metadata', {}).get('version')
    parts(value)
    if 'version' in document and document['version'] != value:
        raise ValueError('Top-level version disagrees with metadata.version')
    return value


def entries(document):
    plugins = document.get('plugins')
    if not isinstance(plugins, list):
        raise ValueError('The catalog requires a plugin list')
    result = {}
    for item in plugins:
        name = item.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name) or name in result:
            raise ValueError('Plugin names must be valid and unique')
        result[name] = item
    return result


def without_version(document):
    result = copy.deepcopy(document)
    result.get('metadata', {}).pop('version', None)
    result.pop('version', None)
    return result


def expected_version(base, current):
    old = base.get('metadata', {}).get('version')
    if old is None:
        return INITIAL_VERSION
    added = set(entries(current)) - set(entries(base))
    if added:
        return bump(old, 'minor')
    if without_version(base) != without_version(current):
        return bump(old, 'patch')
    return old


def check_transition(base, current):
    actual = version(current)
    expected = expected_version(base, current)
    # An explicit maintenance release may bump patch even when plugin entries are unchanged.
    unchanged = without_version(base) == without_version(current)
    old = base.get('metadata', {}).get('version')
    if actual != expected and not (old and unchanged and actual == bump(old, 'patch')):
        raise ValueError(f'Marketplace version must be {expected}: plugin additions use minor; linked-plugin updates use patch')


def validate(document):
    version(document)
    for item in entries(document).values():
        parts(item.get('version'))
        source = item.get('source')
        if isinstance(source, dict) and source.get('source') in ('url', 'github', 'git-subdir'):
            ref, sha = source.get('ref'), source.get('sha')
            if not ((isinstance(ref, str) and ref and ref not in ('main', 'master', 'plugin', 'HEAD', 'latest'))
                    or (isinstance(sha, str) and re.fullmatch('[a-fA-F0-9]{40}', sha))):
                raise ValueError('Linked plugins require a pinned release ref or commit sha, plus their own version')
        elif not isinstance(source, (str, dict)):
            raise ValueError('Plugin source is missing')


def cell(value):
    return str(value).replace('\n', ' ').replace('\r', ' ').replace('|', '\\|')


def table(document):
    validate(document)
    lines = [START, '## Plugin versions', '', f'Marketplace version: **{version(document)}**', '',
             '| Plugin | Version | Pinned source |', '|---|---|---|']
    for name, item in sorted(entries(document).items()):
        source = item['source']
        if isinstance(source, dict):
            url = source.get('url') or ('https://github.com/' + source['repo'] if source.get('repo') else '')
            ref = source.get('ref') or source.get('sha') or item['version']
            label = url.removesuffix('.git')
            location = f'[{cell(label)}]({label}) · `{cell(ref)}`' if label.startswith('https://') else f'`{cell(ref)}`'
        else:
            location = f'`{cell(source)}`'
        lines.append(f'| `{name}` | {item["version"]} | {location} |')
    return '\n'.join([*lines, END])


def readme(document, text):
    if START in text or END in text:
        if text.count(START) != 1 or text.count(END) != 1 or text.index(END) < text.index(START):
            raise ValueError('README version-table markers are malformed')
        start, end = text.index(START), text.index(END) + len(END)
        # Keep later prose, but move the generated version table to the end as required.
        text = text[:start].rstrip() + '\n\n' + text[end:].strip()
    return text.rstrip() + '\n\n' + table(document) + '\n'


def base_catalog(root, ref):
    commit = subprocess.check_output(['git', 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}'],
                                     cwd=root, text=True).strip()
    return json.loads(subprocess.check_output(['git', 'show', commit + ':' + CATALOG], cwd=root, text=True))


def check(root, base=None):
    document = json.loads((root / CATALOG).read_text(encoding='utf-8'))
    validate(document)
    if base is not None:
        check_transition(base, document)
    text = (root / 'README.md').read_text(encoding='utf-8')
    if readme(document, text) != text:
        raise ValueError('Regenerate the final README version table with catalog.py prepare')
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check'))
    parser.add_argument('--base-ref')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    base = base_catalog(root, args.base_ref) if args.base_ref else None
    if args.action == 'prepare':
        if base is None:
            parser.error('prepare requires --base-ref, so repeating preparation does not double-bump')
        path = root / CATALOG
        document = json.loads(path.read_text(encoding='utf-8'))
        desired = expected_version(base, document)
        current = document.get('metadata', {}).get('version')
        old = base.get('metadata', {}).get('version')
        if not (old and current and current == bump(old, 'patch')
                and without_version(base) == without_version(document)):
            document.setdefault('metadata', {})['version'] = desired
        validate(document)
        text = readme(document, (root / 'README.md').read_text(encoding='utf-8'))
        path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
        (root / 'README.md').write_text(text, encoding='utf-8')
    document = check(root, base)
    print(json.dumps({'marketplaceVersion': version(document), 'plugins': len(document['plugins'])}))


if __name__ == '__main__':
    main()
