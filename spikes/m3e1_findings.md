# M3E1: pypdf writer feasibility

Recommendation: **pypdf viable with conditions**. This is an experiment, not a save implementation.

## Reproduce

Install pypdf 6.21.4 separately in a development environment, then run
`QT_QPA_PLATFORM=offscreen .venv/bin/python spikes/m3e1_pypdf_writer.py`.
The script writes disposable PDFs under `/tmp/glyph-m3e1-spike` and leaves its
source fixture unchanged. `all-native-annotations.pdf` is available for manual
browser and desktop-viewer inspection; verify appearance, placement, and that
the five annotations can be selected and edited. Those manual checks have not
been performed. No generated PDF belongs in the repository.

## Evidence and limits

- pypdf 6.21.4 is BSD-3-Clause, pure Python, and requires Python 3.9 or later;
  AES requires an extra cryptographic provider. Glyph currently requires Python
  3.10 or later. The spike ran on Linux/Python 3.13; Windows was not tested.
  [pypdf package](https://pypi.org/project/pypdf/)
- A generated three-page fixture with text, links, metadata, a bookmark, a named
  destination, an attachment, an existing Text annotation, an AcroForm text
  field/widget and a custom catalog dictionary survived a full clone/rewrite in
  the tested checks. This is not proof that arbitrary unknown objects, malformed
  files, XFA forms, compressed object streams, or all PDF features survive.
- Text and Highlight used pypdf helpers; Underline, StrikeOut, and Ink used native
  PDF annotation dictionaries. Reopening verified subtypes, page, rectangles,
  QuadPoints, InkList, width, color and note contents as applicable. This proves
  object structure, not cross-viewer editing or standards conformance.
- Sample annotations had no `/AP`. QtPdf rendered all three pages; image hashes
  changed for each subtype only with `QPdfDocumentRenderOptions.Annotations`.
  Poppler's `pdftoppm` also produced changed images for each subtype, and the
  combined image matched the source with annotations hidden. These are pixel
  change probes, not placement or editability validation. Glyph's current page
  renderer requests pages without annotation options. Do not assume all viewers
  synthesize appearances: validate a viewer matrix before deciding whether to
  generate `/AP`. [Qt render flags](https://doc.qt.io/qt-6.8/qpdfdocumentrenderoptions.html)
- The unencrypted incremental output started with the complete original byte
  sequence, appended an xref update with `/Prev`, and reopened with the new
  annotation. pypdf describes appending changed/new objects and an xref stream.
  [Writer source](https://pypdf.readthedocs.io/en/latest/_modules/pypdf/_writer.html)
  This does not prove signed-document validity, certification permissions, or
  signature policy compliance. Signed PDFs need a separate explicit design.
- An RC4-128 sample rejected a wrong password, accepted the user password, and
  pypdf rejected incremental modification of the encrypted source. An explicit
  RC4-128 full rewrite with both passwords retained the tested `/V`, `/R`,
  `/Length`, and `/P` values. RC4 is used here only to exercise the installed
  pure-Python path, not recommended for new encryption. AES, owner-password
  recovery, metadata-encryption flags, and general permission behavior were not
  tested. Do not silently remove encryption or assume the original owner
  password is recoverable. [pypdf encrypted incremental limitation](https://pypdf.readthedocs.io/en/latest/_modules/pypdf/_writer.html)
- A failed encrypted incremental write was closed and the separate partial
  output discarded; the input bytes stayed unchanged. Production needs separate
  source/destination validation, a same-directory temporary output, error
  propagation and cleanup, durable replacement where appropriate, and reopen
  validation. Those mechanisms are not implemented here.

## Mapping considerations

Glyph's immutable records contain `id`, `kind`, `page`, and `generation`;
Markup adds text and polygon regions, Ink adds ordered points and width, and
Note adds anchor and text. Map Note to `/Text` and `/Contents`; map Markup to
`/Highlight`, `/Underline`, or `/StrikeOut` using `/QuadPoints` and `/Rect`;
map Ink to `/Ink` with `/InkList`, `/Rect`, and border width. `/C`, flags,
opacity, and an identifier such as `/NM` require explicit policy. Glyph uses
Qt page coordinates; PDF user-space conversion must account for crop boxes,
rotation, origin, scaling and quadrilateral ordering. The spike's sample values
are already in PDF user space and do not validate that conversion.

## Candidate comparison

| Candidate | Relevant tradeoffs |
| --- | --- |
| pypdf | BSD-3-Clause; pure Python; low-level PDF objects and selected annotation helpers; unencrypted incremental append demonstrated; encrypted increments rejected. Form and structure checks passed for one fixture; broader preservation and signatures remain unverified. |
| pikepdf/qpdf | pikepdf is MPL-2.0 with file-level copyleft; qpdf is Apache-2.0 with Artistic-2.0 as an option. Native qpdf binaries and bundled third-party notices add distribution work. Low-level control and form facilities are strong, but pikepdf saves as a non-incremental file. [pikepdf](https://github.com/pikepdf/pikepdf), [qpdf](https://github.com/qpdf/qpdf), [save behavior](https://pikepdf.readthedocs.io/en/stable/api/main.html) |
| PyMuPDF | AGPL or commercial license; richer annotation APIs and native binaries. Incremental save has same-file and other restrictions, including decrypted/repaired files. Commercial licensing is an option when AGPL obligations cannot be met. [license](https://pymupdf.readthedocs.io/en/latest/about.html), [save](https://pymupdf.readthedocs.io/en/latest/document.html) |

BSD, Apache and MPL distributions require their respective notices and terms;
pikepdf wheels bundle additional components whose notices must be included.
An ordinary distribution satisfying applicable open-source terms does not need a
commercial license for these libraries. PyMuPDF's AGPL obligations must be
evaluated against Glyph's intended distribution. No Glyph license declaration was
found in `pyproject.toml` or a repository LICENSE file, so compatibility with its
eventual distribution terms is not established. This is not legal advice.

## Proposed next slice

M3E2 could introduce a Save As service with a separate annotation conversion
layer and explicit success/error result. It should keep source/output paths
separate, handle temporary output and cleanup, reopen and validate output, and
define dirty-state behavior. Before shipping, settle coordinate conversion,
viewer appearance and duplicate-overlay handling, encryption and signed-file
policy, and preservation coverage on a representative corpus. No production
persistence architecture is implemented in this spike.
