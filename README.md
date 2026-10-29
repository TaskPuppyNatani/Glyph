# Glyph

Glyph is a local-first desktop PDF application in development for Windows and Linux.
Glyph opens local PDFs in a continuous, vertically scrolling reader with page
navigation, zoom, and a toggleable thumbnail sidebar. Document editing and
password-protected PDFs are not yet supported. QtPdf is installed with the
application dependencies.

Glyph keeps recent document paths and reading positions locally. It restores the
last document on startup unless a PDF path is supplied as an argument. Clearing
recent documents keeps saved reading positions and the last session.

Embedded PDF text can be selected and copied. Selection and Select All are
limited to one page at a time; image-only pages have no selectable text.
Press Ctrl+F to search the open document, move to the next or previous match,
and view transient highlights.

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
The original branding asset is retained in `assets/`. The packaged welcome/About
resource uses a 2048×2048 transparent canvas with the source artwork centered and
unchanged, so it is available to the application after installation.
The separate canonical application icon is `assets/icon.ico`, also included unchanged
in the package and retained for future Windows executable branding.
