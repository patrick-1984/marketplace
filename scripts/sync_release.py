"""Pin the catalog to a verified published Web Blink release using only public source reads."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import zipfile

REPOSITORY = 'patrick-1984/web-blink'
SOURCE = 'https://github.com/' + REPOSITORY + '.git'
BLOCKED = {'v0.10.0'}  # Unsafe shared-profile restart; never promote it again.
TAG = re.compile(r'v([0-9]+)\.([0-9]+)\.([0-9]+)')
REQUIRED = {'.claude-plugin/plugin.json', '.codex-plugin/plugin.json', '.mcp.json',
            'mcp/server.mjs', 'src/blink/runtime.py', 'requirements.lock', 'schemas/index.json'}


def fetch(url, limit=32 * 1024 * 1024):
    headers = {'User-Agent': 'patrick-1984-marketplace-release-sync', 'Accept': 'application/vnd.github+json'}
    if url.startswith('https://api.github.com/') and os.environ.get('GH_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError('Release resource exceeds the size limit')
    return body


def verify_release(release, archive, checksums):
    tag = release.get('tag_name', '')
    match = TAG.fullmatch(tag)
    if not match or tag in BLOCKED or release.get('draft') or release.get('prerelease'):
        raise ValueError('Only non-blocked, published stable releases may enter the catalog')
    version = tag[1:]
    name = f'web-blink-{version}.zip'
    expected = {}
    for line in checksums.decode('utf-8').splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch('[a-f0-9]{64}', parts[0]):
            expected[parts[1].lstrip('*')] = parts[0]
    digest = hashlib.sha256(archive).hexdigest()
    if expected.get(name) != digest:
        raise ValueError('Published archive checksum does not match SHA256SUMS')
    assets = [a for a in release.get('assets', []) if a.get('name') == name]
    if len(assets) != 1 or assets[0].get('digest') != 'sha256:' + digest:
        raise ValueError('Published asset digest does not match the archive')
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = bundle.namelist()
        if len(names) > 10000 or len(set(names)) != len(names):
            raise ValueError('Invalid release file list')
        if any(Path(n).is_absolute() or '..' in Path(n).parts for n in names):
            raise ValueError('Unsafe archive path')
        if not REQUIRED <= set(names):
            raise ValueError('Release is not a complete plugin package')
        receipt = json.loads(bundle.read('_build.json'))
        if receipt['plugin']['version'] != version:
            raise ValueError('Build receipt does not match the release version')
        for path in ('.claude-plugin/plugin.json', '.codex-plugin/plugin.json'):
            manifest = json.loads(bundle.read(path))
            if manifest.get('name') != 'web-blink' or manifest.get('version') != version:
                raise ValueError('Plugin manifest does not match the release version')
        files = {}
        for item in receipt['files']:
            path = item['path']
            if Path(path).is_absolute() or '..' in Path(path).parts:
                raise ValueError('Unsafe build receipt path')
            data = bundle.read(path)
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Build receipt file checksum mismatch')
            files[path] = data
        if not REQUIRED <= set(files):
            raise ValueError('Required plugin files are missing from the receipt')
    return {'tag': tag, 'version': version, 'zipSha256': digest, 'files': files}


def update_catalog(document, verified):
    matches = [p for p in document.get('plugins', []) if p.get('name') == 'web-blink']
    if len(matches) != 1:
        raise ValueError('Catalog must contain exactly one Web Blink entry')
    plugin = matches[0]
    if plugin.get('source', {}).get('url') != SOURCE:
        raise ValueError('Unexpected Web Blink source URL')
    old = TAG.fullmatch(plugin['source'].get('ref', ''))
    new = TAG.fullmatch(verified['tag'])
    if old and tuple(map(int, old.groups())) > tuple(map(int, new.groups())):
        raise ValueError('Refusing a catalog downgrade')
    changed = plugin['source'].get('ref') != verified['tag'] or plugin.get('version') != verified['version']
    plugin['source']['ref'] = verified['tag']
    plugin['version'] = verified['version']
    return changed


def sync(root, tag=None):
    if tag and not TAG.fullmatch(tag):
        raise ValueError('Use a stable release tag such as v0.10.1')
    endpoint = 'tags/' + tag if tag else 'latest'
    release = json.loads(fetch(f'https://api.github.com/repos/{REPOSITORY}/releases/{endpoint}'))
    release_tag = release.get('tag_name', '')
    if not TAG.fullmatch(release_tag) or release_tag in BLOCKED:
        raise ValueError('The latest release is blocked or not stable; catalog was not changed')
    version = release_tag[1:]
    assets = {a['name']: a for a in release.get('assets', [])}
    resources = {}
    for name in (f'web-blink-{version}.zip', 'SHA256SUMS'):
        url = assets[name]['browser_download_url']
        prefix = f'https://github.com/{REPOSITORY}/releases/download/{release_tag}/'
        if url != prefix + name:
            raise ValueError('Unexpected release asset URL')
        resources[name] = fetch(url)
    verified = verify_release(release, resources[f'web-blink-{version}.zip'], resources['SHA256SUMS'])
    # The version tag is the client source. Prove it contains every byte shipped in the clean plugin ZIP.
    with tempfile.TemporaryDirectory(prefix='verify-web-blink-release-') as tmp:
        checkout = Path(tmp) / 'source'
        subprocess.run(['git', 'clone', '--quiet', '--depth', '1', '--branch', release_tag, SOURCE, str(checkout)],
                       check=True, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for name, data in verified.pop('files').items():
            path = checkout / name
            if path.is_symlink() or not path.resolve().is_relative_to(checkout.resolve()) or path.read_bytes() != data:
                raise ValueError('The version tag is incomplete or differs from the published plugin ZIP')
        verified['sourceCommit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout, text=True).strip()
    catalog = root / '.claude-plugin/marketplace.json'
    document = json.loads(catalog.read_text(encoding='utf-8'))
    changed = update_catalog(document, verified)
    lock = root / 'release-lock.json'
    expected_lock = {'web-blink': verified}
    changed = changed or not lock.is_file() or json.loads(lock.read_text()) != expected_lock
    if changed:
        catalog.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
        lock.write_text(json.dumps(expected_lock, indent=2) + '\n', encoding='utf-8')
    return {'changed': changed, **verified}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag')
    args = parser.parse_args()
    result = sync(Path(__file__).resolve().parents[1], args.tag)
    print(json.dumps(result))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write(f'changed={str(result["changed"]).lower()}\nversion={result["version"]}\n')


if __name__ == '__main__':
    main()
