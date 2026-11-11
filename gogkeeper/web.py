"""GogKeeper's web page."""

import html
import os
import re
import select
import sqlite3
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import nh3
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response

from gogkeeper.catalogue import share_dir
from gogkeeper.sync import setting

DB_PATH = "/data/gogkeeper.db"

# The engine's codes and bits, from its globalconstants.h. Languages are in bit order.
PLATFORM_BITS = {"w": 1, "win": 1, "m": 2, "mac": 2, "l": 4, "linux": 4}
LANGUAGE_BITS = {
    code: 1 << bit
    for bit, code in enumerate(
        "en de fr pl ru cn cz es hu it jp tr pt ko nl sv no da fi br sk bl uk es_mx ar ro he th".split()
    )
}


def setting_bits(name, bits):
    """A setting such as w+l as the engine's bit mask. Refuses a value it doesn't know."""
    mask = 0
    for code in re.split(r"[,+]", setting(name).lower()):
        if code not in bits:
            raise RuntimeError(f"{name} has a value the web page doesn't know: {code!r}")
        mask |= bits[code]
    return mask


# Files the backup is meant to hold: current ones, no patches, on the platform and language settings.
WANTED = (
    "f.status NOT IN ('expired', 'purged') AND f.file_type != 'patch'"
    f" AND f.platforms & {setting_bits('GOGKEEPER_PLATFORM', PLATFORM_BITS)}"
    f" AND f.languages & {setting_bits('GOGKEEPER_LANGUAGE', LANGUAGE_BITS)}"
)

GAMES_SQL = f"""
    SELECT g.product_id, g.title, MAX(f.downloaded_at), c.changelog,
           SUM({WANTED}),
           SUM({WANTED} AND f.status = 'not_downloaded'),
           SUM({WANTED} AND f.checksum_result = 'failed'),
           g.base_product_id, g.store_url
    FROM games g
    LEFT JOIN files f ON f.product_id = g.product_id
    LEFT JOIN changelogs c ON c.id = (SELECT MAX(id) FROM changelogs WHERE product_id = g.product_id)
    WHERE g.product_id != -1
    GROUP BY g.product_id
    ORDER BY g.title"""

FILES_SQL = f"""
    SELECT f.product_id, f.file_type, f.path, f.status, f.checksum_result, f.checksum_at, f.gog_md5
    FROM files f
    WHERE {WANTED}
    ORDER BY f.path"""

# Old copies on the share, as the last sync found them, with each one's game folder.
OLD_SQL = """
    SELECT o.product_id, o.path, o.size, g.folder, b.folder
    FROM old_files o
    JOIN games g ON g.product_id = o.product_id
    LEFT JOIN games b ON b.product_id = g.base_product_id AND g.base_product_id != -1
    ORDER BY o.path"""

# In the order they're shown. Patches aren't wanted, so they never appear.
TYPE_LABELS = {"installer": "Installers", "extra": "Extras", "languagepack": "Language packs"}

# The only formatting kept from GOG's changelogs.
NOTES_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "hr", "br", "b", "strong", "i", "em"}

STYLE = """
    :root {
        color-scheme: dark;
        --purple: #A524AB; --purple-text: #CA90D6; --green: #9DD630; --red: #F2645A;
        --background: #212121; --surface: #424242; --text: #F2F2F2; --quiet: #BDBDBD; --line: #3A3A3A;
    }
    * { box-sizing: border-box; }
    body {
        margin: 0; background: var(--background); color: var(--text);
        font: 15px/1.5 system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    a { color: var(--purple-text); }
    :focus-visible { outline: 2px solid var(--purple-text); outline-offset: 2px; }
    button {
        background: var(--purple); color: #FFF; border: 0; border-radius: 4px;
        padding: 6px 14px; font: inherit; cursor: pointer;
    }
    button:hover { filter: brightness(1.15); }
    button:disabled { opacity: 0.6; cursor: default; filter: none; }
    input {
        background: var(--surface); color: var(--text); border: 1px solid #666;
        border-radius: 4px; padding: 6px 8px; font: inherit;
    }
    input[name=address] { width: 40em; max-width: 100%; margin-top: 4px; }

    .top {
        display: flex; justify-content: space-between; align-items: center;
        min-height: 58px;  /* the same with or without the button */
        padding: 10px 24px; border-bottom: 3px solid var(--purple);
    }
    .top form { margin: 0; }
    .actions { display: flex; gap: 10px; }
    .job { min-width: 10.5em; }  /* doesn't change size when it asks "Are you sure?" */
    .job[data-armed] { background: var(--red); color: #212121; font-weight: 600; }
    .brand { color: var(--text); font-size: 22px; font-weight: 600; text-decoration: none;
             display: inline-flex; align-items: center; gap: 10px; }
    .brand img { width: 40px; height: 40px; }
    main { max-width: 1280px; margin: 0 auto; padding: 16px 24px; }
    footer { max-width: 1280px; margin: 0 auto; padding: 8px 24px 24px; color: var(--quiet); font-size: 13px; }

    .row {
        display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 1fr) minmax(0, 1fr) minmax(0, 3fr) 16px;
        gap: 1em; padding: 7px 10px;
    }
    .store { display: inline-flex; vertical-align: middle; color: var(--quiet); }
    .store:hover { color: var(--purple-text); }
    .head {
        position: sticky; top: 0; z-index: 1; background: var(--surface);
        font-weight: 600; border-radius: 4px 4px 0 0;
    }
    .stats { margin: 0 0 12px; font-size: 16px; }
    .sort { background: none; color: inherit; padding: 0; font-weight: 600; text-align: left; }
    .sort:hover { color: var(--purple-text); filter: none; }
    .sort[data-dir=asc]::after { content: " \\25B2"; color: var(--purple-text); font-size: 11px; }
    .sort[data-dir=desc]::after { content: " \\25BC"; color: var(--purple-text); font-size: 11px; }
    main > details { border-bottom: 1px solid var(--line); }
    summary.row { cursor: pointer; }
    summary.row:hover { background: #2E2E2E; }
    summary.row > :first-child::before { content: "\\25B8  "; color: var(--purple-text); }
    details[open] > summary.row > :first-child::before { content: "\\25BE  "; }
    details[open] > summary.row { background: #2E2E2E; }
    details details { margin: 2px 0 2px 28px; }
    details details > summary { cursor: pointer; padding: 3px 0; color: var(--purple-text); }
    details details:last-child { margin-bottom: 10px; }

    .old { color: var(--quiet); font-size: 13px; margin-left: 0.6em; }
    .filter { background: none; color: var(--purple-text); padding: 0; text-decoration: underline; }
    .filter:hover { filter: none; color: var(--text); }
    .good { color: var(--green); }
    .bad { color: var(--red); font-weight: 600; }

    table { border-collapse: collapse; margin: 4px 0 10px 18px; width: calc(100% - 18px); }
    td, th { padding: 3px 1.5em 3px 0; text-align: left; vertical-align: top; }
    th { color: var(--quiet); font-weight: 600; border-bottom: 1px solid var(--line); }
    td { overflow-wrap: anywhere; }

    .notes { margin: 4px 0 10px 18px; max-width: 60em; }
    .notes h1, .notes h2, .notes h3, .notes h4, .notes h5, .notes h6 { font-size: 15px; margin: 12px 0 4px; }
    .notes h1, .notes h2, .notes h3, .notes h4 { font-size: 16px; }
    .notes p, .notes ul, .notes ol { margin: 4px 0; }
    .release + .release { border-top: 1px solid var(--line); margin-top: 14px; padding-top: 2px; }
    .more { margin: 0 0 12px 18px; }
    .more button { margin-right: 8px; padding: 3px 12px; }

    @media (max-width: 720px) {
        .head { display: none; }
        .row { grid-template-columns: 1fr; gap: 0; }
        main, .top, footer { padding-left: 16px; padding-right: 16px; }
    }
"""

# Notes start at the newest release. More adds the next one, All adds the rest.
# Clicking a column heading sorts by it, and clicking again reverses. Blanks always go last.
PAGE_SCRIPT = """
function trim(notes) {
    const releases = [...notes.children];
    if (releases.length < 2) return;

    const bar = document.createElement("div");
    bar.className = "more";
    const more = bar.appendChild(document.createElement("button"));
    const all = bar.appendChild(document.createElement("button"));
    more.type = all.type = "button";
    more.textContent = "More";
    all.textContent = "All";
    notes.after(bar);

    let shown = 1;
    function show() {
        releases.forEach((release, i) => { release.hidden = i >= shown; });
        bar.hidden = shown >= releases.length;
    }
    more.addEventListener("click", () => { shown += 1; show(); });
    all.addEventListener("click", () => { shown = releases.length; show(); });
    show();
}

function sortable() {
    const main = document.querySelector("main");
    const buttons = [...main.querySelectorAll("button.sort")];
    const games = [...main.querySelectorAll(":scope > details")];
    const { compare } = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
    const cell = (game, column) => game.querySelector("summary").children[column];
    const value = (game, column) => cell(game, column).dataset.sort ?? cell(game, column).textContent;

    function sortBy(button, direction) {
        const column = buttons.indexOf(button);
        games.sort((a, b) => {
            const [x, y] = [value(a, column), value(b, column)];
            if (!x !== !y) return x ? -1 : 1;
            const order = cell(a, column).dataset.sort ? x - y : compare(x, y);
            return direction * order || compare(value(a, 0), value(b, 0));
        });
        games.forEach(game => main.appendChild(game));
        buttons.forEach(other => { delete other.dataset.dir; });
        button.dataset.dir = direction > 0 ? "asc" : "desc";
    }

    buttons.forEach(button => button.addEventListener("click", () => {
        sortBy(button, button.dataset.dir === "asc" ? -1 : 1);
    }));
    if (buttons.length) sortBy(buttons[0], 1);
}

// The Sync and Purge buttons. Like a safety cover: the first click arms it, and it closes
// again after 5 seconds. The second click starts the job. While it runs or waits, the page
// checks every 5 seconds, and reloads when it's finished. A refusal is shown on the button.
function jobButton(job, name) {
    const button = document.getElementById(job);
    if (!button) return;
    const idle = button.textContent;
    let timer;
    function disarm() {
        clearTimeout(timer);
        delete button.dataset.armed;
        button.textContent = idle;
    }
    function busy(text) {
        button.disabled = true;
        button.dataset.busy = "yes";
        button.textContent = text;
    }
    function show(jobs) {
        if (jobs.running === job) busy(name + " running\u2026");
        else if (jobs.waiting.includes(job)) busy(name + " waiting\u2026");
        else if (button.dataset.busy) location.reload();
    }
    async function check() {
        try { show(await (await fetch("/jobs")).json()); } catch { /* try again next time */ }
        if (button.dataset.busy) setTimeout(check, 5000);
    }
    async function start() {
        busy("Starting\u2026");
        try {
            const response = await fetch("/" + job, { method: "POST", body: new URLSearchParams(button.dataset) });
            if (response.ok) show(await response.json());
            else { button.textContent = await response.text(); delete button.dataset.busy; return; }
        } catch { /* the next check shows what happened */ }
        if (button.dataset.busy) setTimeout(check, 5000);
    }
    button.addEventListener("click", () => {
        if (!button.dataset.armed) {
            button.dataset.armed = "yes";
            button.textContent = "Are you sure?";
            timer = setTimeout(disarm, 5000);
            return;
        }
        disarm();
        start();
    });
    if (button.dataset.busy) setTimeout(check, 5000);
}

// Shows only the games with old copies, or all of them again.
function oldFilter() {
    const button = document.getElementById("only-old");
    if (!button) return;
    button.addEventListener("click", () => {
        const only = button.textContent.startsWith("Show only");
        document.querySelectorAll("main > details:not([data-old])").forEach(game => { game.hidden = only; });
        button.textContent = only ? "Show all games" : "Show only these";
    });
}

document.querySelectorAll(".notes").forEach(trim);
oldFilter();
sortable();
jobButton("sync", "Sync");
jobButton("purge", "Purge");
"""

# The browser's safety net: only this site's own stylesheet and script may run.
CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)

# The big logo is for dashboards like Homepage; the favicon is a simpler version that reads at 16px.
LOGO = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="18" fill="#A524AB"/>
  <rect x="12" y="12" width="76" height="76" rx="6" fill="none" stroke="#F2F2F2" stroke-width="5"/>
  <g fill="#F2F2F2" font-family="Arial Black, Arial, Helvetica, sans-serif" font-weight="900" font-size="27" text-anchor="middle">
    <text x="50" y="46" textLength="56" lengthAdjust="spacingAndGlyphs">GOG</text>
    <text x="50" y="76" textLength="56" lengthAdjust="spacingAndGlyphs">KPR</text>
  </g>
</svg>
"""

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="5" fill="#A524AB"/>
  <rect x="2.5" y="2.5" width="27" height="27" rx="2" fill="none" stroke="#F2F2F2" stroke-width="1.6"/>
  <text x="16" y="22.5" fill="#F2F2F2" font-family="Arial Black, Arial, Helvetica, sans-serif" font-weight="900" font-size="16" text-anchor="middle" textLength="21" lengthAdjust="spacingAndGlyphs">GK</text>
</svg>
"""

GOG_LOGIN_ADDRESS = re.compile(rb"https://auth\.gog\.com/auth\?[^\s\"'<>]+")
LOGIN_WAIT_SECONDS = 20  # for the engine to print GOG's address
LOGIN_SESSION_SECONDS = 600  # for the person to finish logging in
LOGIN_FINISH_SECONDS = 60  # for the engine to swap the code for tokens
LOGIN_CODE = re.compile(r"[A-Za-z0-9._~-]{1,2048}")

login_lock = threading.Lock()
login_process = None
login_address = None

# Set by the scheduler when it serves this page. Run on its own, the page has no Sync button.
sync_now = None  # brings the scheduled sync forward to now
purge_now = None  # queues a one-off purge
job_status = None  # {"running": job name or None, "waiting": [job names]}
SYNC_START_SECONDS = 5  # for the scheduler to pick up the sync

app = FastAPI()


def read_login_address(process):
    """GOG's login address from the engine's output, or None. The output is never logged."""
    output = b""
    deadline = time.monotonic() + LOGIN_WAIT_SECONDS
    while (remaining := deadline - time.monotonic()) > 0:
        ready, _, _ = select.select([process.stdout], [], [], remaining)
        chunk = os.read(process.stdout.fileno(), 4096) if ready else b""
        if not chunk:
            return None
        output += chunk
        # The address is complete once the line after it has started.
        found = GOG_LOGIN_ADDRESS.search(output)
        if found and found.end() < len(output):
            return found.group().decode("ascii")
    return None


def start_login():
    """Start the engine's browser login and return GOG's login address, or None.

    It runs with no other options, because a successful login saves them to the engine's
    config. Its input stays open, as it waits there for the address you land on.
    Starting again replaces any login still waiting.
    """
    global login_process, login_address
    with login_lock:
        if login_process:
            login_process.kill()
            login_process.wait()
        login_process = subprocess.Popen(
            ["lgogdownloader", "--browser-login"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        timer = threading.Timer(LOGIN_SESSION_SECONDS, login_process.kill)
        timer.daemon = True
        timer.start()
        login_address = read_login_address(login_process)
        if not login_address:
            login_process.kill()
        return login_address


def login_code(address):
    """The code from the address GOG lands you on, or None if it isn't that address."""
    try:
        parts = urlsplit(address.strip())
    except ValueError:
        return None
    if (parts.scheme, parts.hostname, parts.path) != ("https", "embed.gog.com", "/on_login_success"):
        return None
    codes = parse_qs(parts.query).get("code", [])
    return codes[0] if len(codes) == 1 and LOGIN_CODE.fullmatch(codes[0]) else None


def finish_login(code):
    """Give the waiting engine the code. True if it logged in, None if no login was waiting.

    The address is rebuilt from the code alone, because the engine fetches whatever it's given.
    The engine's output is dropped, and the code is never logged or stored.
    """
    global login_process, login_address
    with login_lock:
        process, login_process, login_address = login_process, None, None
        if not process or process.poll() is not None:
            return None
        try:
            process.communicate(
                f"https://embed.gog.com/on_login_success?origin=client&code={code}\n".encode("ascii"),
                timeout=LOGIN_FINISH_SECONDS,
            )
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return False
        return process.returncode == 0


def from_this_site(request):
    """False for a form sent from another website. Browsers always say where those came from."""
    origin = request.headers.get("origin")
    return not origin or urlsplit(origin).netloc == request.headers.get("host")


def page(body, actions=""):
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'>"
        "<title>GogKeeper</title><link rel=icon href=/favicon.svg type=image/svg+xml>"
        "<link rel=stylesheet href=/style.css><script src=/page.js defer></script>"
        f"<header class=top><a class=brand href=/><img src=/logo.svg alt=''>GogKeeper</a>{actions}</header>"
        f"<main>{body}</main>"
        "<footer>Unofficial tool. Not affiliated with GOG.</footer>"
    )


@app.middleware("http")
async def add_security_policy(request, call_next):
    response = await call_next(request)
    # Not on the icons: Chromium won't draw an SVG favicon sent with this policy.
    if request.url.path not in ("/favicon.svg", "/logo.svg"):
        response.headers["Content-Security-Policy"] = CSP
    return response


@app.get("/style.css")
def style():
    return Response(STYLE, media_type="text/css")


@app.get("/page.js")
def page_script():
    return Response(PAGE_SCRIPT, media_type="text/javascript")


@app.get("/favicon.svg")
def favicon():
    return Response(FAVICON, media_type="image/svg+xml")


@app.get("/logo.svg")
def logo():
    return Response(LOGO, media_type="image/svg+xml")


def show_date(timestamp):
    """A Unix time as a date, blank if it was never recorded (0, or a game with no files)."""
    if not timestamp:
        return ""
    day = datetime.fromtimestamp(timestamp)
    return f"{day.day} {day:%b %Y}"


def latest_change(changelog):
    """The heading of the newest entry in GOG's changelog, as plain text. Escape it before display."""
    if not changelog:
        return ""
    heading = re.search(r"<h4>(.*?)</h4>", changelog, re.S)
    text = heading.group(1) if heading else changelog
    text = html.unescape(re.sub(r"<[^>]*>", " ", text))
    return " ".join(text.split())[:80]


def game_status(wanted, missing, failed):
    """Worst first. The counts are None for a game with no files at all."""
    if failed:
        return "Failed check"
    if missing:
        return "Missing files"
    return "OK" if wanted else "No files"


def file_state(status, result, checked_at, gog_md5):
    if status == "not_downloaded":
        return "Missing"
    if result == "failed":
        return "Failed check"
    if result == "passed":
        return f"Checksum passed {show_date(checked_at)}"
    return "No checksum from GOG" if gog_md5 == "none" else "Not checked"


def show_size(size):
    return f"{size / 1e9:.1f} GB" if size >= 1e8 else f"{size / 1e6:.1f} MB"


def stats_line(games, last_sync, latest_result, old):
    """Games, not counting DLC, then the last successful sync, then the old copies on the share,
    if any, with the button that filters to them. Then any problems: games with missing or failed
    files, their DLC's included, and the newest finished sync if it failed or was interrupted."""
    count = sum(1 for game in games if game[7] == -1)
    parts = [f"{count} game{'' if count == 1 else 's'}"]
    if last_sync:
        parts.append(f"Last sync {show_date(last_sync)}")
    old_files = [f for files in old.values() for f in files]
    previous = f"{len(old_files)} previous version{'' if len(old_files) == 1 else 's'}"
    if old_files:
        previous += (f" ({show_size(sum(size for _path, size in old_files))})"
                     " <button id=only-old class=filter type=button>Show only these</button>")
    parts.append(previous)
    issues = sum(1 for game in games if game[5] or game[6])
    problems = []
    if issues:
        problems.append(f"<span class=bad>{issues} issue{'' if issues == 1 else 's'}</span>")
    if latest_result in ("failed", "interrupted"):
        problems.append(f"<span class=bad>Last sync {latest_result}</span>")
    parts += problems or ["<span class=good>No issues</span>"]
    return f"<p class=stats>{' | '.join(parts)}</p>"


def coloured(state):
    """A status or file state, in green or red when it's good or bad news. Only for our own labels."""
    if state in ("Missing files", "Missing", "Failed check"):
        return f"<span class=bad>{state}</span>"
    if state == "OK" or state.startswith("Checksum passed"):
        return f"<span class=good>{state}</span>"
    return state


def files_table(files):
    rows = "".join(
        f"<tr><td>{html.escape(path)}</td><td>{coloured(file_state(status, result, checked_at, gog_md5))}</td></tr>"
        for path, status, result, checked_at, gog_md5 in files
    )
    return f"<table><tr><th>File</th><th>State</th></tr>{rows}</table>"


def type_groups(files):
    """One concertina per file type that the game has, headed with its file count."""
    groups = ""
    for file_type, label in TYPE_LABELS.items():
        of_type = [details for kind, *details in files if kind == file_type]
        if of_type:
            groups += f"<details><summary>{label} ({len(of_type)})</summary>{files_table(of_type)}</details>"
    return groups


def old_group(old_files):
    """The game's old copies, with their sizes, or nothing."""
    if not old_files:
        return ""
    rows = "".join(f"<tr><td>{html.escape(path)}</td><td>{show_size(size)}</td></tr>" for path, size in old_files)
    return (f"<details><summary>Previous versions ({len(old_files)})</summary>"
            f"<table><tr><th>File</th><th>Size</th></tr>{rows}</table></details>")


def old_mark(old_files):
    return f"<span class=old>{len(old_files)} previous</span>" if old_files else ""


def with_dlc(game, dlc):
    """The game's row with its DLC's files counted in: latest download, wanted, missing, failed.
    So a broken DLC makes its game broken."""
    family = [game, *dlc]
    product_id, title, _last, changelog, *_counts, base, store_url = game
    return (product_id, title, max(g[2] or 0 for g in family), changelog,
            *(sum(g[i] or 0 for g in family) for i in (4, 5, 6)), base, store_url)


def dlc_group(dlc, files, old):
    """The game's DLC, each one opening to its own files and previous versions, or nothing."""
    if not dlc:
        return ""
    items = "".join(
        f"<details><summary>{html.escape(title)} – {coloured(game_status(wanted, missing, failed))}"
        f"{old_mark(old[product_id])}</summary>{type_groups(files[product_id])}{old_group(old[product_id])}</details>"
        for product_id, title, _last, _changelog, wanted, missing, failed, _base, _store_url in dlc
    )
    return f"<details><summary>DLC ({len(dlc)})</summary>{items}</details>"


def clean_notes(changelog):
    """GOG's changelog HTML with basic formatting only: no scripts, links, images or attributes."""
    return nh3.clean(changelog, tags=NOTES_TAGS, attributes={}, link_rel=None)


def release_notes(changelog):
    """One block per release: GOG separates them with a horizontal line."""
    releases = [clean_notes(part) for part in re.split(r"<hr\b[^>]*>", changelog or "", flags=re.I)]
    blocks = "".join(f"<div class=release>{release}</div>" for release in releases if release.strip())
    if not blocks:
        return ""
    return f"<details><summary>Release notes</summary><div class=notes>{blocks}</div></details>"


STORE_ICON = (  # a box with an arrow out of it: opens in a new tab
    "<svg viewBox='0 0 24 24' width=16 height=16 fill=none stroke=currentColor stroke-width=2"
    " stroke-linecap=round stroke-linejoin=round aria-hidden=true>"
    "<path d='M14 4h6v6'/><path d='M20 4l-9 9'/><path d='M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'/></svg>"
)


def store_link(title, store_url):
    """An icon linking to the game's GOG store page, or nothing ('unknown' and 'none' aren't links)."""
    if not store_url.startswith("https://www.gog.com/"):
        return ""
    return (f"<a class=store href='{html.escape(store_url, quote=True)}' target=_blank rel='noopener noreferrer'"
            f" title='GOG store page' aria-label='{html.escape(title, quote=True)} on GOG'>{STORE_ICON}</a>")


@app.get("/", response_class=HTMLResponse)
def library():
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        games = db.execute(GAMES_SQL).fetchall()
        last_sync = db.execute("SELECT MAX(finished_at) FROM sync_runs WHERE result = 'succeeded'").fetchone()[0]
        latest = db.execute(
            "SELECT result FROM sync_runs WHERE result != 'running' ORDER BY id DESC LIMIT 1").fetchone()
        files = defaultdict(list)
        for product_id, *details in db.execute(FILES_SQL):
            files[product_id].append(details)
        old = defaultdict(list)  # product_id -> (path within the game's folder, size)
        for product_id, path, size, folder, base_folder in db.execute(OLD_SQL):
            prefix = share_dir(folder, base_folder) + "/" if product_id != -1 else ""
            old[product_id].append((path.removeprefix(prefix), size))
    finally:
        db.close()
    dlc = defaultdict(list)  # base product_id -> its DLC's rows
    for game in games:
        if game[7] != -1:
            dlc[game[7]].append(game)
    games = [with_dlc(game, dlc[game[0]]) for game in games if game[7] == -1]
    # A game's "N previous" counts its DLC's too, so showing only those finds them.
    family_old = {product_id: old[product_id] + [f for d in dlc[product_id] for f in old[d[0]]]
                  for product_id, *_ in games}
    rows = "".join(
        f"<details{' data-old' if family_old[product_id] else ''}><summary class=row>"
        f"<span>{html.escape(title)}{old_mark(family_old[product_id])}</span><span>{coloured(game_status(wanted, missing, failed))}</span>"
        f"<span data-sort='{last or ''}'>{show_date(last)}</span><span>{html.escape(latest_change(changelog))}</span>"
        f"<span>{store_link(title, store_url)}</span></summary>{type_groups(files[product_id])}"
        f"{old_group(old[product_id])}{dlc_group(dlc[product_id], files, old)}{release_notes(changelog)}</details>"
        for product_id, title, last, changelog, wanted, missing, failed, _base, store_url in games
    )
    if old[-1]:  # in folders that belong to no game
        rows += ("<details data-old><summary class=row><span>Not in a game's folder"
                 f"{old_mark(old[-1])}</span><span></span><span></span><span></span><span></span></summary>"
                 f"{old_group(old[-1])}</details>")
    return page(
        f"{stats_line(games, last_sync, latest and latest[0], old)}"
        "<div class='row head'><span><button class=sort type=button>Game</button></span>"
        "<span><button class=sort type=button>Status</button></span>"
        "<span><button class=sort type=button>Last downloaded</button></span>"
        f"<span>Latest change</span><span></span></div>{rows}",
        actions=f"<div class=actions>{purge_button(sum(len(f) for f in old.values()))}{sync_button()}"
                "<form method=post action=/login><button>Log in to GOG</button></form></div>",
    )


def job_label(jobs, job):
    """What a job's button says while the job is running or waiting, else None."""
    if jobs["running"] == job:
        return f"{job.title()} running\u2026"
    if job in jobs["waiting"]:
        return f"{job.title()} waiting\u2026"
    return None


def sync_button():
    if not sync_now:
        return ""
    label = job_label(job_status(), "sync")
    if label:
        return f"<button id=sync class=job type=button disabled data-busy>{label}</button>"
    return "<button id=sync class=job type=button>Sync now</button>"


def purge_button(count):
    """Purge (N), where N is how many files it deletes. The page sends N back, so a purge of a
    list that has changed since is refused. Greyed out with nothing to delete."""
    if not purge_now:
        return ""
    label = job_label(job_status(), "purge")
    if label:
        return f"<button id=purge class=job type=button disabled data-busy>{label}</button>"
    return (f"<button id=purge class=job type=button data-count={count}{' disabled' if not count else ''}"
            f" title='Delete every previous version'>Purge ({count})</button>")


@app.get("/jobs")
def jobs():
    return job_status() if job_status else {"running": None, "waiting": []}


@app.post("/sync")
def sync(request: Request):
    """Start a sync, then wait briefly until the scheduler has it, so the page can show it."""
    if not from_this_site(request):
        return Response("Refused: that came from another website.", status_code=403)
    if not sync_now:
        return Response("Sync isn't available: the scheduler isn't running.", status_code=404)
    sync_now()
    return wait_for_job("sync")


def wait_for_job(job):
    """Wait briefly until the scheduler has the job, so the page can show it."""
    deadline = time.monotonic() + SYNC_START_SECONDS
    while not job_label(job_status(), job) and time.monotonic() < deadline:
        time.sleep(0.1)
    return job_status()


@app.post("/purge")
async def purge(request: Request):
    """Queue a purge of the previous versions, if the page's count still matches the list."""
    if not from_this_site(request):
        return Response("Refused: another website", status_code=403)
    if not purge_now:
        return Response("Scheduler not running", status_code=404)
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        count = db.execute("SELECT count(*) FROM old_files").fetchone()[0]
    finally:
        db.close()
    if form.get("count", [""])[0] != str(count):
        return Response("List changed: reload", status_code=409)
    if count == 0:
        return Response("Nothing to purge", status_code=409)
    purge_now()
    return await run_in_threadpool(wait_for_job, "purge")


def login_page(message=""):
    link = html.escape(login_address, quote=True)
    return page(
        f"<p>{message}</p>"
        f"<ol><li><a href='{link}' target=_blank rel='noopener noreferrer'>Open GOG login</a> (opens in a new tab).</li>"
        "<li>Log in. You'll land on a mostly blank GOG page.</li>"
        "<li>Copy that page's full address and paste it here:"
        "<form method=post action=/login/code autocomplete=off>"
        "<input name=address required> <button>Finish login</button></form></li></ol>"
        "<p><a href=/>Back</a></p>"
    )


def message_page(message, status_code=200):
    return HTMLResponse(page(f"<p>{message} <a href=/>Back</a></p>"), status_code=status_code)


@app.post("/login", response_class=HTMLResponse)
def login(request: Request):
    if not from_this_site(request):
        return message_page("Refused: that came from another website.", 403)
    if not start_login():
        return message_page("Couldn't start the GOG login.", 502)
    return login_page()


@app.post("/login/code", response_class=HTMLResponse)
async def login_finish(request: Request):
    if not from_this_site(request):
        return message_page("Refused: that came from another website.", 403)
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    code = login_code(form.get("address", [""])[0])
    if not code:
        if not login_address:
            return message_page("That login has ended. Please start again.", 400)
        return HTMLResponse(login_page("That isn't the address GOG lands you on. Please try again."), status_code=400)
    result = await run_in_threadpool(finish_login, code)
    if result is None:
        return message_page("That login has ended. Please start again.", 400)
    if not result:
        return message_page("GOG didn't accept that login. Please start again.", 502)
    return message_page("Logged in to GOG.")
