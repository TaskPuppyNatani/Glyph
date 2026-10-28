# Glyph

Glyph is a local-first desktop PDF application in development for Windows and Linux.
The M0 foundation provides an application window, appearance preferences, and an
About dialog. PDF opening and editing are future work and are not yet available.

## Development

Python 3.10 or newer is required. Create and activate a virtual environment:

```sh
python -m venv .venv
```

On Linux, run `source .venv/bin/activate`; on Windows, run
`.venv\Scripts\activate` in Command Prompt or `.venv\Scripts\Activate.ps1`
in PowerShell. Then install and launch:

```sh
python -m pip install -e .
glyph
```

`python -m glyph_desktop` is also supported. Run the focused tests with:

```sh
python -m unittest discover -s tests -v
```

For headless Linux testing, set `QT_QPA_PLATFORM=offscreen` before running tests.
The original branding asset is retained in `assets/`; an unchanged copy is included
in the Python package so it is available to the application after installation.
The separate canonical application icon is `assets/icon.ico`, also included unchanged
in the package and retained for future Windows executable branding.
