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
    "f.status != 'expired' AND f.file_type != 'patch'"
    f" AND f.platforms & {setting_bits('GOGKEEPER_PLATFORM', PLATFORM_BITS)}"
    f" AND f.languages & {setting_bits('GOGKEEPER_LANGUAGE', LANGUAGE_BITS)}"
)

GAMES_SQL = f"""
    SELECT g.product_id, g.title, MAX(f.downloaded_at), c.changelog,
           SUM({WANTED}),
           SUM({WANTED} AND f.status = 'not_downloaded'),
           SUM({WANTED} AND f.checksum_result = 'failed'),
           g.base_product_id
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
    .brand { color: var(--text); font-size: 20px; font-weight: 600; text-decoration: none; }
    main { max-width: 1280px; margin: 0 auto; padding: 16px 24px; }
    footer { max-width: 1280px; margin: 0 auto; padding: 8px 24px 24px; color: var(--quiet); font-size: 13px; }

    .row {
        display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 1fr) minmax(0, 1fr) minmax(0, 3fr);
        gap: 1em; padding: 7px 10px;
    }
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

document.querySelectorAll(".notes").forEach(trim);
sortable();
"""

# The browser's safety net: only this site's own stylesheet and script may run.
CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)

GOG_LOGIN_ADDRESS = re.compile(rb"https://auth\.gog\.com/auth\?[^\s\"'<>]+")
LOGIN_WAIT_SECONDS = 20  # for the engine to print GOG's address
LOGIN_SESSION_SECONDS = 600  # for the person to finish logging in
LOGIN_FINISH_SECONDS = 60  # for the engine to swap the code for tokens
LOGIN_CODE = re.compile(r"[A-Za-z0-9._~-]{1,2048}")

login_lock = threading.Lock()
login_process = None
login_address = None

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
        "<title>GogKeeper</title>"
        "<link rel=stylesheet href=/style.css><script src=/page.js defer></script>"
        f"<header class=top><a class=brand href=/>GogKeeper</a>{actions}</header>"
        f"<main>{body}</main>"
        "<footer>Unofficial tool. Not affiliated with GOG.</footer>"
    )


@app.middleware("http")
async def add_security_policy(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    return response


@app.get("/style.css")
def style():
    return Response(STYLE, media_type="text/css")


@app.get("/page.js")
def page_script():
    return Response(PAGE_SCRIPT, media_type="text/javascript")


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


def stats_line(games, last_sync):
    """Games, not counting DLC, then the last successful sync, then games or DLC with a problem."""
    count = sum(1 for game in games if game[7] == -1)
    issues = sum(1 for game in games if game[5] or game[6])
    if issues:
        problem = f"<span class=bad>{issues} issue{'' if issues == 1 else 's'}</span>"
    else:
        problem = "<span class=good>No issues</span>"
    synced = f"Last sync {show_date(last_sync)} - " if last_sync else ""
    return f"<p class=stats>{count} game{'' if count == 1 else 's'} - {synced}{problem}</p>"


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


@app.get("/", response_class=HTMLResponse)
def library():
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        games = db.execute(GAMES_SQL).fetchall()
        last_sync = db.execute("SELECT MAX(finished_at) FROM sync_runs WHERE result = 'succeeded'").fetchone()[0]
        files = defaultdict(list)
        for product_id, *details in db.execute(FILES_SQL):
            files[product_id].append(details)
    finally:
        db.close()
    rows = "".join(
        "<details><summary class=row>"
        f"<span>{html.escape(title)}</span><span>{coloured(game_status(wanted, missing, failed))}</span>"
        f"<span data-sort='{last or ''}'>{show_date(last)}</span><span>{html.escape(latest_change(changelog))}</span>"
        f"</summary>{type_groups(files[product_id])}{release_notes(changelog)}</details>"
        for product_id, title, last, changelog, wanted, missing, failed, _base in games
    )
    return page(
        f"{stats_line(games, last_sync)}"
        "<div class='row head'><span><button class=sort type=button>Game</button></span>"
        "<span><button class=sort type=button>Status</button></span>"
        "<span><button class=sort type=button>Last downloaded</button></span>"
        f"<span>Latest change</span></div>{rows}",
        actions="<form method=post action=/login><button>Log in to GOG</button></form>",
    )


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
