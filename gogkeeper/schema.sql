-- GogKeeper database schema (SQLite).
--
-- No column allows NULL and none has a default, so every insert must state
-- every value. A missing value is an error, never a silent gap.
--
-- SQLite only enforces foreign keys when each connection runs:
--     PRAGMA foreign_keys = ON;

CREATE TABLE games (
    product_id       INTEGER PRIMARY KEY,           -- GOG's ID
    gamename         TEXT NOT NULL UNIQUE,          -- engine's name, e.g. the_witcher_3_wild_hunt
    title            TEXT NOT NULL,                 -- display name
    folder           TEXT NOT NULL,                 -- base game: relative to the library, e.g. the_witcher_3
                                                    -- DLC: relative to its base game, e.g. dlc/hearts_of_stone
    base_product_id  INTEGER NOT NULL REFERENCES games(product_id)  -- -1 for a base game
);

-- The "no parent" row that base games point to. Not a real game:
-- leave it out of any query that lists or counts games.
INSERT INTO games (product_id, gamename, title, folder, base_product_id)
VALUES (-1, '_none', '(no parent)', '', -1);

CREATE TABLE files (
    id               INTEGER PRIMARY KEY,
    product_id       INTEGER NOT NULL REFERENCES games(product_id)
                     CHECK (product_id != -1),
    path             TEXT NOT NULL,                 -- relative to the game's folder, e.g. extras/manual.zip
    -- Every file GOG lists is stored. Queries filter on these three to match the settings.
    file_type        TEXT NOT NULL
                     CHECK (file_type IN ('installer', 'extra', 'patch', 'languagepack')),
    platforms        INTEGER NOT NULL,              -- engine's bits: 1 Windows, 2 Mac, 4 Linux
    languages        INTEGER NOT NULL,              -- engine's bits: 1 English, ...
                                                    -- the engine labels every extra Windows and English
    gog_md5         TEXT NOT NULL                  -- 'none' when GOG has no checksum, e.g. extras
                     CHECK (gog_md5 = 'none' OR length(gog_md5) = 32),
    status           TEXT NOT NULL
                     CHECK (status IN ('not_downloaded', 'downloaded', 'expired')),
    downloaded_at    INTEGER NOT NULL,              -- Unix time, 0 = never
    checksum_at      INTEGER NOT NULL,              -- Unix time of the last real check, 0 = never
    checksum_result  TEXT NOT NULL
                     CHECK (checksum_result IN ('not_checked', 'passed', 'failed'))
);

-- Only one live row per file. Expired rows are kept as history.
CREATE UNIQUE INDEX files_one_live
    ON files (product_id, path) WHERE status != 'expired';
