# Marketplace maintenance

The catalog is `.claude-plugin/marketplace.json`. Plugins live in their linked repositories; a change to a
linked repository alone does not complete a marketplace update.

## Required version and release rules

- The marketplace's own semantic version is `metadata.version` in the catalog. Keep it independent of plugin versions.
- Every published Web Blink update requires a marketplace **patch** bump, whether Web Blink's change is
  patch, minor or major. For example, marketplace `0.1.7` becomes `0.1.8` for any Web Blink version update.
- Adding a new plugin requires a marketplace **minor** bump, resetting patch to zero: `0.1.7` becomes `0.2.0`.
  A change that adds a plugin and updates another plugin still uses the minor bump.
- Other catalog maintenance uses a patch bump. Re-running an already synchronized release must not bump again.
- For each linked plugin, update its entry's `version` and `source.ref` to the published release tag. The plugin
  version must agree with its own manifest; updating only the moving `plugin` branch is insufficient.
- Every marketplace version must have a GitHub release/tag `v<metadata.version>` in this repository.
- The **last section of README.md** is a generated table listing all plugins, their versions and pinned sources,
  together with the marketplace version. Keep new prose above this section. Never leave the table stale.

## Workflow

For Web Blink releases, run `Sync verified Web Blink release` (`sync-release.yml`) with the published tag.
It verifies the plugin package/source, updates the catalog and release lock, bumps the marketplace patch,
regenerates the README table, commits the change and publishes the marketplace release. Scheduled checks
are a fallback; dispatch the workflow during Web Blink publication.

For a new plugin or a manual catalog edit, edit its entry, then run:

```sh
python scripts/catalog.py prepare --base-ref origin/main
python scripts/catalog.py check --base-ref origin/main
python -m unittest discover -s tests -v
claude plugin validate .
```

`prepare` chooses minor for additions and patch for updates, relative to the base catalog, and regenerates
the README table. CI checks both the version rule and table. The publish workflow creates the GitHub release
after merging to main; the sync workflow also publishes directly so bot updates do not depend on another push run.
Confirm the catalog pin, plugin version, README table, marketplace version and published release agree before
calling the update complete. Preserve release verification and known-unsafe-release exclusions.
