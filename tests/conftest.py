import os

# web.py reads these when it's imported, so they must be set before any test imports it.
os.environ.setdefault("GOGKEEPER_PLATFORM", "w")
os.environ.setdefault("GOGKEEPER_LANGUAGE", "en")
