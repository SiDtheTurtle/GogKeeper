# GogKeeper

Keeps a backup of your GOG offline installers current on a NAS. It downloads new and updated installers,
DLC and extras, and it never deletes anything: old versions are kept for you to review.

**Unofficial.** GogKeeper isn't affiliated with or endorsed by GOG. Use it only for games you own.

## Status

Early, and command line only. It packages [lgogdownloader](https://github.com/Sude-/lgogdownloader) in a
Docker image with safety checks around it. Library checks work, but a full download run hasn't been
tested yet. Scheduling and a web UI are planned.

## Requirements

- Docker with Compose
- A GOG account
- An SMB share for the backup, with a `library` folder in it

## Setup

```bash
cp .env.example .env          # then fill in your NAS address, share name and SMB login
docker compose pull
docker compose run --rm gogkeeper --browser-login
docker compose run --rm gogkeeper --check-login-status
docker compose run --rm gogkeeper --update-cache
```

- `--browser-login` needs a real terminal. It prints a URL, then asks you to paste back the address you land
  on after logging in. That address contains a login code, so don't share it.
- A comma in the SMB password breaks the mount, because Docker splits its mount options on commas.
- After changing `.env` or the mount options, run `docker volume rm gogkeeper_library` to apply them. This
  only removes the mount settings, not anything on your NAS.

## Usage

```bash
# Download one game: Windows, English, no patches
docker compose run --rm gogkeeper --download --use-cache --automatic-xml-creation \
  --directory /library/library --platform w --language en \
  --exclude bp,dp --game '^some_game$'

# Compare your files with GOG's checksums, hashing every file
docker compose run --rm gogkeeper --status --no-fast-status-check --use-cache \
  --directory /library/library --platform w --language en
```

Status codes: `OK`, `ND` (not found at the expected path), `MD5` (contents differ), `FS` (size differs). GOG has
no checksums for extras, so `OK` on an extra doesn't confirm it matches GOG. `--automatic-xml-creation` saves a
checksum for each extra when it downloads, so later checks can catch corruption.

All other options come from lgogdownloader: run `docker compose run --rm gogkeeper --help` to list them.

## Safety checks

The image's entrypoint refuses to run:

- `--download`, `--status`, `--repair`, `--check-orphans` or `--galaxy-install` without `--directory`,
  `--platform` and `--language`
- `--download` without `--include` or `--exclude`
- with a `--directory` outside `/library`, because a forgotten `--directory` would otherwise fill the local
  disk rather than the NAS
- `--delete-orphans`, at any time

Running with `--entrypoint` skips these checks.

## Sensitive data

These Docker volumes hold secrets, so treat them, and any backups of them, with care:

- `gogkeeper_config` has your GOG login tokens
- `gogkeeper_cache` has `gamedetails.json`, which includes your game serials and CD keys
- `gogkeeper_library` has your SMB password in its Docker volume options, which `docker volume inspect` and
  plain `docker compose config` will both show

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

Files are matched by exact path, so don't rename them by hand.

## Credits and license

Built on [lgogdownloader](https://github.com/Sude-/lgogdownloader). The repo holds no lgogdownloader code: the
`Dockerfile` fetches a pinned commit at build time, and published images contain the compiled program.

GogKeeper is licensed under the GNU General Public License v3.0.
