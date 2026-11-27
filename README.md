# patrick-1984 Claude Code plugins

A plugin marketplace for [Claude Code](https://code.claude.com) and Codex. It is a catalog only: each plugin lives in its own
repository and is linked from [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json).

## Install

```text
/plugin marketplace add patrick-1984/marketplace
/plugin install web-blink@patrick-1984
```

From a shell: `claude plugin marketplace add patrick-1984/marketplace` and
`claude plugin install web-blink@patrick-1984` (add `--scope project` to enable it for one project only).
Update both the catalog and the installed plugin, then restart Claude Code:

```sh
claude plugin marketplace update patrick-1984
claude plugin update web-blink@patrick-1984
```

For Codex, explicitly refresh the catalog and the plugin installation, then restart Codex:

```sh
codex plugin marketplace upgrade patrick-1984
codex plugin add web-blink@patrick-1984
```

“Marketplace already up to date” describes the catalog snapshot. It does not prove the installed plugin
files or the MCP server loaded in an existing conversation are current. `plugin add` ensures the referenced
plugin is installed; a host restart reloads its running component. `blink_doctor` reports its loaded version.

## Plugins

| Plugin | What it does | Source |
|---|---|---|
| `web-blink` | Use reviewed web-service actions from a coding session or the CLI and check compatibility with Blink Debugger. | [Web Blink README](https://github.com/patrick-1984/web-blink) |
| `blink-debugger` | Preflight Linux workflows, read Orange Pi runbooks, summarize saved observations and retain local software lessons. | [Blink Debugger runbooks](https://github.com/patrick-1984/blink-debugger#preflight-and-device-runbooks) |
| `usb-checker` | Run a guided, direct read-only USB safety check on a prepared Linux host. | [USB Checker installation and workflow](https://github.com/patrick-1984/usb-checker) |

## Release synchronization

The Sync verified Web Blink release workflow checks for published releases every 15 minutes and can be
dispatched with a release tag during publication. It verifies the ZIP against SHA256SUMS, GitHub's asset
digest and its per-file build receipt; validates both plugin manifests; and confirms the source tag
contains every shipped file with identical bytes. For each new Web Blink version it updates the catalog's
exact plugin ref/version, bumps the marketplace patch, refreshes the version table at the end of this README,
commits the release lock and publishes a marketplace GitHub release. Drafts, prereleases, downgrades and
known unsafe v0.10.0 are rejected. Repeating the same update does not bump again.

The workflow runs in this marketplace repository and writes only here. It uses its own contents-write
GITHUB_TOKEN and public reads of Web Blink; no cross-repository PAT or new GitHub App secret is required.
Scheduled runs can be delayed by GitHub, so publication should also dispatch the workflow for an immediate
sync. A catalog pin makes the release visible; clients should still run both update commands above.

## Adding a plugin

Add an entry to `plugins` in `.claude-plugin/marketplace.json`: a `github` source for a plugin with its own
repository, or `"./plugins/<name>"` for a small plugin kept in this repository. The entry `name` must equal the
plugin's `plugin.json` name. Check with `claude plugin validate .` before pushing.

The marketplace has its own version at `metadata.version` in the catalog. Any Web Blink release—patch,
minor or major—requires a marketplace **patch** bump. Adding a plugin requires a marketplace **minor** bump
with patch reset to zero. Plugin versions remain in their individual catalog entries alongside their pinned
source refs. Run `python scripts/catalog.py prepare --base-ref origin/main` after manual catalog changes;
it applies the rule and regenerates the table below. CI checks both. See [AGENTS.md](AGENTS.md) for the full
release procedure. The marketplace publish workflow creates a GitHub release/tag for each catalog version.

<!-- BEGIN plugin versions -->
## Plugin versions

Marketplace version: **0.4.3**

| Plugin | Version | Pinned source |
|---|---|---|
| `blink-debugger` | 0.3.0 | [https://github.com/patrick-1984/blink-debugger](https://github.com/patrick-1984/blink-debugger) · `v0.3.0` |
| `minion-control` | 0.3.0 | [https://github.com/patrick-1984/ai-minion-plugin](https://github.com/patrick-1984/ai-minion-plugin) · `v0.3.0` |
| `usb-checker` | 0.1.1 | [https://github.com/patrick-1984/usb-checker](https://github.com/patrick-1984/usb-checker) · `v0.1.1` |
| `web-blink` | 0.21.3 | [https://github.com/patrick-1984/web-blink](https://github.com/patrick-1984/web-blink) · `v0.21.3` |
<!-- END plugin versions -->
