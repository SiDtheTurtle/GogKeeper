# GogKeeper

Keeps a backup of your GOG offline installers current on a NAS. It downloads new and updated installers,
DLC and extras, checks each download against GOG's checksum, and never deletes anything: old versions stay
on the share for you to review.

**Unofficial.** GogKeeper isn't affiliated with or endorsed by GOG. Use it only for games you own.

## Status

Early, with no web UI: a container stays up and runs the sync and maintenance on a schedule you set. It
wraps [lgogdownloader](https://github.com/Sude-/lgogdownloader) in a Docker image, with safety checks and a
small database that records every game and file, when each was downloaded, and when it last passed a
checksum. Moving old versions to quarantine, alerts and a web UI are planned.

## Requirements

- Docker with Compose
- A GOG account
- An SMB share for the backup, with a `library` folder in it

## Setup

```bash
cp .env.example .env          # then fill in your NAS, SMB login, platform, language and schedules
docker compose pull
docker compose run --rm gogkeeper --browser-login
docker compose run --rm gogkeeper --check-login-status
docker compose up -d          # starts the scheduler
```

- `GOGKEEPER_PLATFORM` and `GOGKEEPER_LANGUAGE` have no defaults, so Compose refuses to run until both are
  set. They use lgogdownloader's format, such as `w` and `en`. Join several with `+`, such as `w+l`.
- `GOGKEEPER_SYNC_SCHEDULE` and `GOGKEEPER_MAINTENANCE_SCHEDULE` have no defaults either: see
  [The schedule](#the-schedule).
- `--browser-login` needs a real terminal. It prints a URL, then asks you to paste back the address you land
  on after logging in. That address contains a login code, so don't share it.
- A comma in the SMB password breaks the mount, because Docker splits its mount options on commas.
- After changing `.env` or the mount options, run `docker volume rm gogkeeper_library` to apply them. This
  only removes the mount settings, not anything on your NAS.

## Usage

### The schedule

`docker compose up -d` starts a container that stays up and runs `sync` and `maintenance` at the times you
set in `.env`:

| Setting | Example | Meaning |
|---|---|---|
| `GOGKEEPER_SYNC_SCHEDULE` | `"0 0 * * *"` | Every day at midnight |
| `GOGKEEPER_MAINTENANCE_SCHEDULE` | `"0 12 1 * *"` | At noon on the 1st of each month |

The format is cron's: minute, hour, day of the month, month, weekday. Times follow `TZ` in `compose.yml`.
There are two limits, because the scheduler reads these differently from cron and would otherwise run on the
wrong days. Both are refused at startup:

- Weekdays must be names, such as `mon` or `sun`, not numbers.
- Set a day of the month or a weekday, not both.

An invalid schedule stops the scheduler with an error, and Docker keeps restarting it until you fix `.env`.

Starting the container never triggers a run, and a run missed while the container was down waits for its next
time. A run that fails is logged, and the next scheduled run tries again. There are no alerts yet, so check
the log now and then with `docker compose logs gogkeeper`.

### Running a job yourself

```bash
# Fetch GOG's list, download anything new or changed, then check what was downloaded
docker compose run --rm gogkeeper sync

# Re-check every downloaded file against GOG's checksum, to catch files that change on the NAS
docker compose run --rm gogkeeper maintenance
```

These work whether the scheduled container is up or not.

**`sync`** first backs up the database. It then fetches your whole library's details from GOG, which takes a
few minutes, and updates the database: new games and files are added, and files GOG no longer lists are
marked expired. It then downloads what's missing for your platform and language, without patches, and checks
each file it downloaded against GOG's checksum.

The first `sync` builds the database from what's already on the share. It also checks every file there,
because none has been checked yet, so it reads your whole library once. After that, a `sync` only checks
what it downloads.

**`maintenance`** reads every file again, so it's best run on a long schedule, such as monthly.

Both exit with an error if a file fails its check or is missing, and they print each one. If one is stopped
part-way, the next `sync` finishes what's left.

### One job at a time

`sync` and `maintenance` never run together, whether scheduled or started by hand. A job that finds another
running waits for it to finish, and logs what it waited for and how long. Press Ctrl-C to give up on a manual
job while it's waiting. If a scheduled job comes due while the same job is already running or waiting, that
run is skipped and logged.

Direct lgogdownloader commands don't wait, so logging in or checking your login works at any time. Don't run
ones that change files, such as `--download` or `--update-cache`, while a job is running.

**Extras can't be checked.** GOG publishes no checksums for extras, so they're downloaded but never marked as
passed. The language setting doesn't filter extras either: lgogdownloader labels every extra Windows and
English.

### Running lgogdownloader directly

Any other arguments go straight to lgogdownloader, as before. Run `docker compose run --rm gogkeeper --help`
to list its options.

```bash
# Compare your files with GOG's checksums, hashing every file
docker compose run --rm gogkeeper --status --no-fast-status-check --use-cache \
  --directory /library/library --platform w --language en
```

Status codes: `OK`, `ND` (not found at the expected path), `MD5` (contents differ), `FS` (size differs). An
`OK` doesn't always mean a file was compared: lgogdownloader reports `OK` when it has no checksum to compare
with, as for extras.

## Safety checks

The image's entrypoint refuses to run:

- `--download`, `--status`, `--repair`, `--check-orphans` or `--galaxy-install` without `--directory`,
  `--platform` and `--language`
- `--download` without `--include` or `--exclude`
- with a `--directory` outside `/library`, because a forgotten `--directory` would otherwise fill the local
  disk rather than the NAS
- `--delete-orphans`, at any time

Running with `--entrypoint` skips these checks. `sync` builds its own lgogdownloader arguments, always with
`--directory /library/library`.

## Volumes

| Volume | Holds |
|---|---|
| `gogkeeper_config` | Your GOG login tokens. **Secret** |
| `gogkeeper_cache` | lgogdownloader's cache, including `gamedetails.json` with your game serials and CD keys. **Secret** |
| `gogkeeper_library` | The SMB share. Your SMB password is in its Docker volume options, which `docker volume inspect` and plain `docker compose config` both show. **Secret** |
| `gogkeeper_data` | GogKeeper's database, on local disk because SQLite is unreliable over SMB. No secrets: games, files, dates, GOG's changelogs and the database's backups. If it's lost, the next `sync` rebuilds it from the share, but download and check dates start again |

Treat the secret volumes, and any backups of them, with care.

## Backup

| What | Back up? | If it's lost |
|---|---|---|
| `backups/` in `gogkeeper_data` | **Yes** | The next `sync` rebuilds the list of games and files from the share, but all history is gone: download dates, check dates and results, expired files, and old changelogs |
| `.env` | **Yes**. **Secret** | You re-enter your NAS address, SMB login, platform, language and schedules |
| `gogkeeper_cache` | Ideally. **Secret** | The next `sync` rebuilds it, but it re-reads every installer on the share first, which takes an hour or more on a large library |
| `gogkeeper_config` | No. **Secret** | You log in again with `--browser-login` |
| The share | Your NAS's own backup | It's the backup itself |

A plain copy of the live database, `gogkeeper.db`, isn't guaranteed to be usable. The container is always up,
and a copy taken while SQLite is writing can be corrupt. Back up the `backups/` folder inside `gogkeeper_data`
instead, and keep those copies off this disk.

Every `sync` makes a copy there before it changes anything, one per day, and the newest 7 are kept. Each file
named `gogkeeper-<date>.db` is complete, so it's safe to copy at any time, even during a job. If a backup
fails, the sync logs a warning and carries on. To make one yourself, at any time:

```bash
docker compose run --rm gogkeeper backup
```

`gogkeeper_cache` and `gogkeeper_config` are ordinary files, so plain copies are fine. A cache copied during a
sync may hold a half-written file, which the next `sync` replaces.

To restore a backup, stop the container, put the copy back, and start it again. Replace the date with the copy
you want:

```bash
docker compose stop gogkeeper
docker run --rm -v gogkeeper_data:/data --entrypoint sh ghcr.io/sidtheturtle/gogkeeper:latest \
  -c 'rm -f /data/gogkeeper.db-journal && cp /data/backups/gogkeeper-2026-01-31.db /data/gogkeeper.db'
docker compose start gogkeeper
```

## Library layout

```
<share>/
  library/      lgogdownloader's --directory
    <game>/         installers
      extras/
      patches/
      languagepacks/
      dlc/<dlc>/    DLC installers, with extras/ inside
  quarantine/   old or unwanted files, kept for review
```

Files are matched by exact path, so don't rename them by hand. When GOG replaces a file with one under a new
name, the old one stays where it is and is marked expired in the database. Moving those to `quarantine/` is
planned.

## Credits and license

Built on [lgogdownloader](https://github.com/Sude-/lgogdownloader). The repo holds no lgogdownloader code: the
`Dockerfile` fetches a pinned commit at build time, and published images contain the compiled program.

GogKeeper is licensed under the GNU General Public License v3.0.
