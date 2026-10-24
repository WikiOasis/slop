# slop

WikiOasis's deployment tool, in the spirit of Wikimedia's scap. slop builds MediaWiki (and, later, other services) on
the deploy master, rolls it out host by host with smoke tests on every host, records exactly what was deployed in a
git-tracked statefile, logs every deploy to the Server Admin Log (and through it to Phorge), and rolls back
automatically when something breaks.

```
slop deploy -m 'Enable TemplateStyles on all wikis' -t T123
```

1. **Pull** the config repo (`config/`, [WikiOasis/slop-conf](https://github.com/WikiOasis/slop-conf)) and check its
   locks. If the pull fails, or someone else has locked deploys, nothing happens.
2. **Build** in `/srv/mediawiki-build`: every repo is fetched in parallel, patches are re-applied, composer runs only
   when its inputs changed. A no-op build takes a couple of seconds.
3. **Show** what changed since the last deploy (commits, patches, pins) and ask to go ahead.
4. **Lint** the build's PHP config with `php -l`, and check every host is reachable.
5. **Roll out**: staging (the canary) first, then app servers in batches, then job runners. On each host, after the
   sync: l10n if needed, php-fpm reload, then the **smoke tests** against `test.wikioasis.org` on that host. The
   next batch only starts once every host in the current one passes. Staging also runs the canary-only tests, then
   **soaks** (waits and tests again) before any production host is touched.
6. **On success**: write `state/mediawiki.yaml` (every repo's exact commit, every patch, the `composer.lock`), commit
   and push the config repo, and log to the SAL with the Phorge tasks.
7. **On failure**: rebuild the last deployed state from the statefile (no network needed: the commits are still
   local) and re-sync every host the deploy touched. The SAL records the failure and the rollback, and your config
   changes stay uncommitted so you can fix and retry.

## Smoke tests

Configured under `deploy.checks` in `slop.yaml`. Each test is a request made on the host itself (to
`http://127.0.0.1` with `Host: test.wikioasis.org`), so it tests that host's code and not the load balancer:

```yaml
checks:
  host: test.wikioasis.org
  retries: 3
  tests:
    - name: api.php
      path: /w/api.php?action=query&meta=siteinfo&format=json
      expect: '"sitename"'
    - name: Special:Version
      path: /wiki/Special:Version
      expect: MediaWiki
      roles: [canary]
```

A test fails on the wrong status (`status:`, default 200), when `expect` is missing from the page, or when the page
contains a PHP fatal, parse error or MediaWiki exception (`forbid:` overrides that list). `roles:` limits a test to
some roles; the canary runs the deeper ones. Run them any time without deploying:

```
slop check                      every host, its role's tests
slop check --hosts mw-us-east-011 --all-tests
```

A role's `soak:` in `inventory.yaml` (30 seconds for the canary) waits after the role is done and runs its tests
again, to catch things that only break once caches warm up or jobs run.

## Commands

| Command                                                       | What it does                                                                       |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `slop deploy [WHAT [NAME...]] -m MSG [-t T123]`               | Pull, build, test, roll out. `WHAT` limits it: `config`, `core`, `extensions Echo` |
| `slop rollback [REV] -m MSG`                                  | Rebuild a previously deployed state and roll it out (default: the one before)      |
| `slop check`                                                  | Run the smoke tests on every host                                                  |
| `slop build` / `slop update WHAT [NAME...]`                   | Build without deploying                                                            |
| `slop diff`                                                   | Build tree versus the last deploy, plus uncommitted config                         |
| `slop state [--history] [REV]`                                | What is deployed, from the statefile; `--history` lists deploys                    |
| `slop status`                                                 | What each host runs and whether it matches the last deploy                         |
| `slop repo list\|pin\|unpin\|branch\|add\|remove\|import`     | Repos: pin to exact commits, switch branches, add or import extensions             |
| `slop patch list\|add\|update\|remove`                        | Patches: files, Gerrit changes, GitHub PRs, cherry-picked commits                  |
| `slop config status\|pull\|push\|commit\|diff\|log\|validate` | The config repo                                                                    |
| `slop lock [PROJECT...]` / `slop unlock` / `slop locks`       | Stop other people deploying; the lock is pushed to the config repo                 |
| `slop restart SERVICE [--role R]`                             | Rolling restart or reload, one host at a time                                      |
| `slop log [-l] [-f] [RUN]`                                    | Every run's structured log: commands, timings, errors                              |
| `slop hosts` / `slop projects` / `slop modules`               | Inventory, projects and build modules                                              |

Every command that changes something takes `-m` (recorded in the run log and the SAL), `-n` for a dry run and `-v`
to see every command. `deploy` and `rollback` also take `--hosts`, `--role`, `--batch` and `-y`, and
`deploy` takes `--no-rollback`, `--resync` and `--skip-build`.

### Locks

`slop lock tsportal jobrunner -m 'why'` writes `locks/<project>.yaml` for each project in the config repo (`--all`
locks everything, `--for 2h` makes it expire), commits and pushes it; if the push fails, the lock is undone. Every
deploy and rollback, from any host, pulls the config repo before checking locks and refuses to run if it cannot
pull, so a lock is never skipped by a stale checkout. The locks are checked again, against the remote, after the
build and just before the rollout, so a lock taken during a long build still stops it. `slop restart` refuses too if
a project that runs on the service's roles is locked. Your own locks don't block you. `slop unlock` releases locks,
and `--force` breaks someone else's. Locking and unlocking are logged to the SAL and sent to Discord and Slack.

### Phorge tasks and the SAL

A deploy, rollback or restart writes separate SAL entries, never edits one: a start statement when the rollout begins
("Deploying tsportal: ... to 1 hosts"), then, if something breaks, the reason it failed, and finally an end summary
(what was deployed and how long it took, or what was rolled back). Each carries the run id (`slop log RUN`).

Tasks come from `-t T123`, from the `-m` message, and from patches and pins that carry a `task:`. They go into the
SAL entry, and the SAL server comments on each task in Phorge, so a task shows when its fix went out, or when a
deploy for it failed and was rolled back. Upstream Wikimedia `Bug: T…` footers are deliberately ignored: those are
Phabricator tasks, not ours.

### Patches

```
slop patch add 1234567 -t T123                     Gerrit change, latest patch set, pinned
slop patch add WikiOasis/WikiOasisMagic#42         GitHub pull request, pinned to its head commit
slop patch add fix.patch --path extensions/Echo    a patch file
slop patch add commit:3f2a1bc --path skins/Citizen --url https://github.com/...
slop patch update gerrit-1234567                   move to the latest patch set
```

Patches live in the config repo and are re-applied after every sync, so they survive core and extension updates.
When one no longer applies and you are on a terminal, slop stops and offers to open a shell in the checkout, run
`git mergetool`, show the diff, skip the patch for this build, or abort. Once it's resolved, slop offers to save
the result into the config so the next build applies cleanly. Without a terminal (or with `--no-interaction`) the
build fails before anything is deployed.

### Pins and branches

```
slop repo pin CheckUser 3f2a1bc -t T200    exact commit (a tag or branch name also works)
slop repo pin core 1.46.1
slop repo unpin CheckUser
slop repo branch WikiOasisMagic feature/x
slop repo import /srv/mediawiki-staging/1.46 --mw-version 1.46
```

### Config as code

The config repo is the source of truth, and Salt installs it on the deploy master as slop's `config/` checkout with a
write-enabled deploy key.

- **Pull:** every command pulls it first (rebasing any local edits), so what you see and deploy is what is on the
  remote. Commands that change things refuse to run if the pull fails; read-only ones warn and use what they have.
- **Edits:** `slop repo` and `slop patch` commands commit and push their change as soon as it is made, with your `-m`
  and `-t` in the commit message. Set `config-repo.commit-edits: false` in `slop.yaml` to keep them uncommitted.
- **Deploys:** a successful deploy commits and pushes `state/<project>.yaml` (the exact commits, patches and lockfile
  that are live). A failed one leaves your config as it was, so you can fix it and retry.
- **IDE:** editing the files and pushing works the same way; the next command pulls them.
- **Private settings:** a mediawiki project's `private-settings: <path>` copies that file into
  `config/PrivateSettings.php` on every build; `private-settings: false` turns the step off.
- **OAuth key:** likewise `oauth-key: <path>` (default `/srv/OAuth.key`) copies that file into `config/OAuth.key`;
  `oauth-key: false` turns the step off.

### Running it

slop runs on the deploy master (`staging-us-east-021`) as `mwdeploy`. On any other host the `slop` wrapper forwards
the command there over `ssh -t`, so output, prompts and Ctrl-C behave as if local, and the run log records who you
are. `--local` skips forwarding. Salt installs slop and its config (`salt/slop` in the salt repo).

## Development

```sh
git submodule update --init
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp config/slop.local.example.yaml config/slop.local.yaml
.venv/bin/slop build && .venv/bin/slop deploy -y -m test
scripts/check.sh
```

`slop.local.yaml` builds into `.dev/srv/mediawiki` and "deploys" to `.dev/srv/mediawiki-live` on `localhost`, with
the SAL, pulling and pushing turned off. CI runs ruff, prettier, a check that the code has no comments, and the
test suite, which builds and deploys a small farm from local git repos and exercises smoke tests, failure, rollback,
pins, patches, conflict resolution and locks.

### Layout

One class per file, folders as namespaces; commands, project types and build modules are autoloaded.

```
src/Slop/
├── Cli.py, Runner.py, Context.py
├── Command/            one file per `slop` command
├── Config/             ConfigLoader, ConfigRepo, StateFile, Inventory, Repo, Patch, YamlFile
├── Deploy/             Deployment (deploy, rollback), Rollout, Health (smoke tests), Lock, StateDiff
├── Git/                Git (sync, pins, patches), ConflictResolver
├── Forge/              Gerrit, GitHub
├── Remote/             Ssh (commands, rsync), Master (forwarding)
├── Sal/, Notify/       SAL entries, Discord/Slack webhooks
└── Project/            MediaWiki/ and Service/ types with their build modules
```
