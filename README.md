# patrick-1984 Claude Code plugins

A [Claude Code](https://code.claude.com) plugin marketplace. It is a catalog only: each plugin lives in its own
repository and is linked from [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json).

## Install

```text
/plugin marketplace add patrick-1984/marketplace
/plugin install web-blink@patrick-1984
```

From a shell: `claude plugin marketplace add patrick-1984/marketplace` and
`claude plugin install web-blink@patrick-1984` (add `--scope project` to enable it for one project only).
Update later with `claude plugin marketplace update patrick-1984`.

## Plugins

| Plugin | What it does | Source |
|---|---|---|
| `web-blink` | It takes just a blink to control the web. Web Blink learns how web services and web apps work, then hands them to you as reviewed, ready-to-run actions, so you can automate your work from Claude Code or the command line (`web-blink`, or just `blink`). | [patrick-1984/web-blink](https://github.com/patrick-1984/web-blink), branch `plugin` (the unpacked latest release) |

## Adding a plugin

Add an entry to `plugins` in `.claude-plugin/marketplace.json`: a `github` source for a plugin with its own
repository, or `"./plugins/<name>"` for a small plugin kept in this repository. The entry `name` must equal the
plugin's `plugin.json` name. Check with `claude plugin validate .` before pushing.
