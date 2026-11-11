-- GogKeeper database schema (SQLite).
--
-- No column allows NULL and none has a default, so every insert must state
-- every value. A missing value is an error, never a silent gap.
--
-- SQLite only enforces foreign keys when each connection runs:
--     PRAGMA foreign_keys = ON;

-- Allowed values. A column that points at one of these can only hold a value listed there,
-- so adding a value is one INSERT, with no table rebuild. Keep these in step with UPGRADES in db.py.
CREATE TABLE file_statuses (value TEXT PRIMARY KEY) WITHOUT ROWID;
INSERT INTO file_statuses (value) VALUES ('not_downloaded'), ('downloaded'), ('expired'), ('purged');
CREATE TABLE file_types (value TEXT PRIMARY KEY) WITHOUT ROWID;
INSERT INTO file_types (value) VALUES ('installer'), ('extra'), ('patch'), ('languagepack');
CREATE TABLE checksum_results (value TEXT PRIMARY KEY) WITHOUT ROWID;
INSERT INTO checksum_results (value) VALUES ('not_checked'), ('passed'), ('failed');
CREATE TABLE sync_results (value TEXT PRIMARY KEY) WITHOUT ROWID;
INSERT INTO sync_results (value) VALUES ('running'), ('succeeded'), ('failed'), ('interrupted');

CREATE TABLE games (
    product_id       INTEGER PRIMARY KEY,           -- GOG's ID
    gamename         TEXT NOT NULL UNIQUE,          -- engine's name, e.g. the_witcher_3_wild_hunt
    title            TEXT NOT NULL,                 -- display name
    folder           TEXT NOT NULL,                 -- base game: relative to the library, e.g. the_witcher_3
                                                    -- DLC: relative to its base game, e.g. dlc/hearts_of_stone
    base_product_id  INTEGER NOT NULL REFERENCES games(product_id), -- -1 for a base game
    store_url        TEXT NOT NULL                  -- GOG store page, e.g. https://www.gog.com/game/the_witcher_2
                                                    -- 'unknown': not fetched yet; 'none': GOG has no page
                                                    -- Databases upgraded from version 2 also have DEFAULT 'unknown'
);

-- The "no parent" row that base games point to. Not a real game:
-- leave it out of any query that lists or counts games.
INSERT INTO games (product_id, gamename, title, folder, base_product_id, store_url)
VALUES (-1, '_none', '(no parent)', '', -1, 'none');

CREATE TABLE files (
    id               INTEGER PRIMARY KEY,
    product_id       INTEGER NOT NULL REFERENCES games(product_id)
                     CHECK (product_id != -1),
    path             TEXT NOT NULL,                 -- relative to the game's folder, e.g. extras/manual.zip
    -- Every file GOG lists is stored. Queries filter on these three to match the settings.
    file_type        TEXT NOT NULL REFERENCES file_types(value),
    platforms        INTEGER NOT NULL,              -- engine's bits: 1 Windows, 2 Mac, 4 Linux
    languages        INTEGER NOT NULL,              -- engine's bits: 1 English, ...
                                                    -- the engine labels every extra Windows and English
    version          TEXT NOT NULL,                 -- GOG's version text, e.g. 5.01; '' when GOG gives none
    gog_md5          TEXT NOT NULL                  -- 'none': GOG has no checksum, e.g. extras
                                                    -- 'unknown': not fetched from GOG yet
                     CHECK (gog_md5 IN ('none', 'unknown') OR length(gog_md5) = 32),
    status           TEXT NOT NULL REFERENCES file_statuses(value),
                                                    -- 'expired': GOG stopped listing it
                                                    -- 'purged': expired, then deleted from the share
    downloaded_at    INTEGER NOT NULL,              -- Unix time, 0 = never
    checksum_at      INTEGER NOT NULL,              -- Unix time of the last real check, 0 = never
    checksum_result  TEXT NOT NULL REFERENCES checksum_results(value)
);

-- Only one live row per file. Expired and purged rows are kept as history.
CREATE UNIQUE INDEX files_one_live
    ON files (product_id, path) WHERE status NOT IN ('expired', 'purged');

-- GOG's changelog for a game: one block of HTML holding its whole history, newest first.
-- A row is added only when the text differs from the game's latest row, and none is
-- ever changed or removed, so nothing is lost if GOG edits or trims old entries.
-- To see why a file appeared, read the copy that was current when it was downloaded.
CREATE TABLE changelogs (
    id               INTEGER PRIMARY KEY,
    product_id       INTEGER NOT NULL REFERENCES games(product_id)
                     CHECK (product_id != -1),
    fetched_at       INTEGER NOT NULL,              -- Unix time this text was first seen
    changelog        TEXT NOT NULL                  -- GOG's HTML, as is
);

-- One row per sync, added when it starts and updated when it ends. Test runs limited
-- to one game aren't recorded. Keep this in step with UPGRADES in db.py.
-- 'interrupted': still 'running' when the next sync began, so it was killed part-way.
CREATE TABLE sync_runs (
    id                  INTEGER PRIMARY KEY,
    started_at          INTEGER NOT NULL,           -- Unix time
    finished_at         INTEGER NOT NULL,           -- Unix time, 0 = hasn't finished
    result              TEXT NOT NULL REFERENCES sync_results(value),
    downloaded          INTEGER NOT NULL,           -- files downloaded
    new_games           INTEGER NOT NULL,           -- games or DLC GOG newly lists
    new_files           INTEGER NOT NULL,           -- files GOG newly lists
    removed_files       INTEGER NOT NULL,           -- files GOG stopped listing
    changed_changelogs  INTEGER NOT NULL
);

-- Old copies on the share: every file there that isn't a live file. That's expired files,
-- files the database has never had, such as builds from before the first run, and the
-- engine's .old files. Each sync clears this and fills it again from a walk of the library.
CREATE TABLE old_files (
    id               INTEGER PRIMARY KEY,
    product_id       INTEGER NOT NULL REFERENCES games(product_id), -- game whose folder it's in, -1 = none
    path             TEXT NOT NULL UNIQUE,          -- relative to the library, e.g. wing_commander/setup_old.exe
    size             INTEGER NOT NULL,              -- bytes
    found_at         INTEGER NOT NULL               -- Unix time of the walk that found it
);
