# Glyph

Glyph is a local-first desktop PDF application in development for Windows and Linux.
Glyph opens local PDFs one page at a time, with page navigation and zoom. Document
editing and password-protected PDFs are not yet supported. QtPdf is installed with
the application dependencies.

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
