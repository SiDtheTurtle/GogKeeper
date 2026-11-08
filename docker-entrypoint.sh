#!/usr/bin/env bash
# Makes the core parameters required. lgogdownloader falls back to its own defaults when
# they're missing, and a default --directory of "." fills the local disk, not the NAS.
#
# Commands that work on files need:   --directory --platform --language
# --download also needs:              --include or --exclude
# --directory must be under /library.
# --delete-orphans is always refused.
# Everything else, such as --help, --list or --update-cache, passes straight through.
set -u

needs_files=no download=no directory="" have_platform=no have_language=no have_filter=no
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    arg=${args[i]}
    # Patterns, not exact names: lgogdownloader accepts abbreviations such as --down.
    case $arg in
        --del*) echo "gogkeeper: --delete-orphans is blocked: this project never auto-deletes" >&2; exit 2 ;;
    esac
    case $arg in
        --do*) needs_files=yes; download=yes ;;
        --repa*|--stat*|--check-o*|--galaxy-i*) needs_files=yes ;;
    esac
    case $arg in
        --directory=*) directory=${arg#*=} ;;
        --directory) directory=${args[i + 1]:-} ;;
        --platform|--platform=*) have_platform=yes ;;
        --language|--language=*) have_language=yes ;;
        --include|--include=*|--exclude|--exclude=*) have_filter=yes ;;
    esac
done

if [[ $needs_files == yes ]]; then
    missing=()
    [[ -n $directory ]] || missing+=("--directory")
    [[ $have_platform == yes ]] || missing+=("--platform")
    [[ $have_language == yes ]] || missing+=("--language")
    [[ $download == yes && $have_filter == no ]] && missing+=("--include or --exclude")
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "gogkeeper: refusing to run, missing: ${missing[*]}" >&2
        exit 2
    fi
    case $directory in
        */../*|*/..) echo "gogkeeper: --directory can't contain '..': $directory" >&2; exit 2 ;;
        /library/*|/library) ;;
        *) echo "gogkeeper: --directory must be under /library: $directory" >&2; exit 2 ;;
    esac
fi

# Creates GogKeeper's database on first start, and leaves it alone after that.
python3 -m gogkeeper.db /data/gogkeeper.db || exit 1

# One job at a time: sync and maintenance wait here for any other job, scheduled or manual.
# The lock ends with the process holding it, so a crash can't leave it stuck.
wait_for_turn() {
    local lock=/data/gogkeeper.lock holder started=$SECONDS waited
    exec 9>>"$lock"
    if ! flock -n 9; then
        holder=$(cat "$lock")
        echo "gogkeeper: $(date '+%F %T'): $1 is waiting for ${holder:-another job} to finish" >&2
        flock 9 || exit 1
        waited=$((SECONDS - started))
        printf 'gogkeeper: %s: %s waited %d:%02d:%02d\n' "$(date '+%F %T')" "$1" \
            $((waited / 3600)) $((waited % 3600 / 60)) $((waited % 60)) >&2
    fi
    echo "$1" > "$lock"
}

# GogKeeper's own commands. Anything else goes to the engine, as before.
case ${1:-} in
    schedule)
        exec python3 -m gogkeeper.schedule ;;
    backup)
        # Doesn't wait its turn: SQLite's backup is safe while a job is writing.
        exec python3 -m gogkeeper.backup ;;
    sync)
        wait_for_turn sync
        # Sync, then check what it downloaded. The check runs even if the sync failed
        # part-way, as the files that did finish still need checking.
        shift
        python3 -m gogkeeper.sync "$@"; sync_status=$?
        python3 -m gogkeeper.checksums; check_status=$?
        (( sync_status == 0 && check_status == 0 )) || exit 1
        exit 0 ;;
    maintenance)
        wait_for_turn maintenance
        exec python3 -m gogkeeper.checksums --maintenance ;;
esac

exec lgogdownloader "$@"
