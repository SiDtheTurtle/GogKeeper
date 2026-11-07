# GogKeeper

Keeps a backup of your GOG offline installers current on a NAS. It downloads new and updated installers,
DLC and extras, checks each download against GOG's checksum, and never deletes anything: old versions stay
on the share for you to review.

**Unofficial.** GogKeeper isn't affiliated with or endorsed by GOG. Use it only for games you own.

## Status

Early, and command line only: you run each command yourself, and the container exits when it's done. It
wraps [lgogdownloader](https://github.com/Sude-/lgogdownloader) in a Docker image, with safety checks and a
small database that records every game and file, when each was downloaded, and when it last passed a
checksum. Scheduling, moving old versions to quarantine, alerts and a web UI are planned.

## Requirements

- Docker with Compose
- A GOG account
- An SMB share for the backup, with a `library` folder in it

## Setup

```bash
cp .env.example .env          # then fill in your NAS, SMB login, platform and language
docker compose pull
docker compose run --rm gogkeeper --browser-login
docker compose run --rm gogkeeper --check-login-status
```

- `GOGKEEPER_PLATFORM` and `GOGKEEPER_LANGUAGE` have no defaults, so Compose refuses to run until both are
  set. They use lgogdownloader's format, such as `w` and `en`. Join several with `+`, such as `w+l`.
- `--browser-login` needs a real terminal. It prints a URL, then asks you to paste back the address you land
  on after logging in. That address contains a login code, so don't share it.
- A comma in the SMB password breaks the mount, because Docker splits its mount options on commas.
- After changing `.env` or the mount options, run `docker volume rm gogkeeper_library` to apply them. This
  only removes the mount settings, not anything on your NAS.

## Usage

```bash
# Fetch GOG's list, download anything new or changed, then check what was downloaded
docker compose run --rm gogkeeper sync

# Re-check every downloaded file against GOG's checksum, to catch files that change on the NAS
docker compose run --rm gogkeeper maintenance
```

**`sync`** fetches your whole library's details from GOG, which takes a few minutes, and updates the
database: new games and files are added, and files GOG no longer lists are marked expired. It then downloads
what's missing for your platform and language, without patches, and checks each file it downloaded against
GOG's checksum.

The first `sync` builds the database from what's already on the share. It also checks every file there,
because none has been checked yet, so it reads your whole library once. After that, a `sync` only checks
what it downloads.

**`maintenance`** reads every file again. Run it now and then, such as monthly.

Both exit with an error if a file fails its check or is missing, and they print each one.

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
| `gogkeeper_data` | GogKeeper's database, on local disk because SQLite is unreliable over SMB. No secrets: games, files, dates and GOG's changelogs. If it's lost, the next `sync` rebuilds it from the share, but download and check dates start again |

Treat the secret volumes, and any backups of them, with care.

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
