# iam_bdfilter

GUI tool to filter Instagram user accounts stored in a SQLite database
(table `Users`, schema dumped by InstAccountsManager).

Filters by:
- account privacy (open / private / any)
- avatar presence (with / without / any)
- ASCII-only `full_name` (latin alphabet + at least one letter)
- stop-words in `full_name` and/or `username`
- country (`country_account`) with multilingual mapping for top-30 countries
  (Ukrainian, Latvian, Dutch, English, French, German, Italian, Spanish,
  Portuguese, Polish, Russian, Japanese, Chinese, Korean, Turkish)
- a custom country field for any other latin-named country

Empty countries and the placeholder `Не поширюється` are always rejected.

Output: one `user_id` per line, ASCII.

## Quick start (Linux / macOS, for testing)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python filter_gui.py
```

## Build Windows executable

On a Windows machine with Python 3.11+ installed:

```bat
build.bat
```

Produces `dist\FilterGUI.exe` (one-file, no console window).

## Performance

On a 15M-row database the worker processes ~430k–950k rows/sec
(depends on stop-words list size). The full scan finishes in 15–40 seconds
with a 50k stop-words list.

## Files

| File | Purpose |
|------|---------|
| `filter_gui.py` | Tkinter GUI + background worker thread |
| `countries.py`  | Top-30 country mapping (canonical key → variants) |
| `requirements.txt` | Python dependencies (`pyahocorasick`) |
| `build.bat` | Windows one-file build script (PyInstaller) |
