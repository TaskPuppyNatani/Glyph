# PDF annotation persistence

M3E1B introduced the internal pypdf writer boundary. File → Save As writes the
current Glyph session annotations to a separate PDF copy, leaving the source
open and unchanged. Replacing an existing Save As destination requires explicit
confirmation; cancel leaves it untouched. Replacement is an opt-in writer
argument, and callers that do not provide a prepared confirmation target retain
the no-overwrite default.

File → Save (Ctrl+S) writes the current Glyph session annotations into the open
PDF. It uses the source identity and content snapshot captured when the document
was opened, validates inherited annotations and the complete staged output,
then publishes those exact staged bytes through the platform-specific in-place
replacement path. The reader and renderer release their PDF handles before
publication; after success Glyph reopens the PDF and restores the reading
position and zoom. Saved annotations become native PDF annotations, so Glyph
clears their session overlays and associated undo/redo history. Save with no
session annotations is a no-op. A source that changed since opening is rejected.

Save As continues to ask for explicit confirmation before replacing an
existing destination. Glyph displays supported native PDF annotations when
reopening a saved PDF, including annotations previously written by Glyph, but
does not import them as editable Glyph records. The writer refuses encrypted
PDFs and known signature markers, writes into a private staging directory, and
validates the retained output file with pypdf and QtPdf before publication.

## Coordinates and markup appearance

Glyph stores annotation coordinates in the page space reported by QtPdf: points
from the visible page's top-left, after page rotation. PDF annotation geometry
uses default user space: bottom-left origin before page rotation. Conversion
intersects CropBox with MediaBox, supports rotations 0, 90, 180 and 270 degrees,
and checks the QtPdf page size against those PDF boxes. QtPdf does not scale
`pagePointSize()` for non-default `/UserUnit`, so such pages are rejected.
Unsupported or inconsistent geometry fails before output is installed.
Markup regions must be four-corner, axis-aligned quads because Glyph records
do not retain arbitrary text-direction metadata.

`/QuadPoints` use ISO 32000-1 counterclockwise order (lower-left, lower-right,
upper-right, upper-left). iText documents that Acrobat and other viewers often
expect the non-standard Z order instead. Glyph therefore writes a constrained
`/AP` normal appearance for Highlight, Underline, and StrikeOut, which takes
precedence over viewer-generated QuadPoints styling. The appearance draws only
the supported rectangular fill or line geometry; it is not a general PDF
appearance engine. The serialized geometry and raster placement were checked
against independent target rectangles on cropped pages at rotations 0, 90,
180, and 270. QtPdf offscreen checks and Poppler 25.03.0 diagnostics matched
those targets. Other viewers and arbitrary/non-rectangular text geometry remain
unverified. See [ISO 32000-1, Table 179](https://developer.adobe.com/document-services/docs/assets/35e4369068f86065372c18787171a17e/PDF_ISO_32000-1.pdf)
and [iText 7.2.2's QuadPoints interoperability note](https://api.itextpdf.com/iText/java/7.2.2/com/itextpdf/kernel/pdf/annot/PdfTextMarkupAnnotation.html).

Markup and Ink colors match the current reader overlay colors. Ink width is
stored in Glyph's page points and is written as PDF stroke width. Markup stroke
width is 1.5 points. Notes retain their stored anchor and text; because the
Note record has no size field, the native Text annotation uses an 18-point
square centered on that anchor.

## Staging and publication

The writer creates a unique sibling staging directory on the destination
volume. On Linux it is mode `0700`; on Windows it has a protected DACL granting
access to the current user, SYSTEM, and Builtin Administrators. Staging uses a
retained open file descriptor/handle through validation and publication. Linux
publishes by linking that descriptor into a pinned destination directory with
no-replace semantics by default. Windows opens the staging file without delete
sharing and renames that same handle to an absolute destination path with
replacement disabled by default. The published file keeps the staging file's
restrictive permissions (mode `0600` on Linux and the staging DACL on Windows).
Unsupported platforms fail closed.

For confirmed replacement, Glyph pins the existing regular destination and
checks that it is still the same file immediately before publication. Linux
uses `renameat2(RENAME_EXCHANGE)` against the pinned destination directory,
then verifies the displaced entry before discarding it; if the target changed
in the final publication window, the writer attempts to restore that entry and
fails. If rollback itself fails, both displaced and validated files are
retained in the private staging directory and the result reports an ambiguous
publication state. Filesystems without exchange support fail closed. Windows
holds the confirmed target against new writes and deletes, rechecks its file
identity, then uses `SetFileInformationByHandle` with `FileRenameInfoEx` replace
and POSIX-semantics flags on the validated staging handle. This replaces the
directory entry atomically without deleting the old file before validation.
The source PDF, including hard links to it, is never accepted as a replacement
target. Existing-target replacement is implemented for Linux and Windows only.

On the user's Linux `fuseblk` filesystem, in-place Save failed with `Errno 22`;
Save As to a new file on that filesystem succeeded, and in-place Save succeeded
on a native Linux filesystem. Lack of `renameat2(RENAME_EXCHANGE)` support is a
suspected cause, not an independently confirmed diagnosis. Safe in-place
publication on NTFS/FUSE remains a separate future task; Glyph currently fails
closed when atomic exchange is unavailable.

Both validators read from the retained, unbuffered staging descriptor without
reopening its pathname. QtPdf loads a seekable `QFile` device wrapping that
descriptor with `DontCloseHandle`; the document and device are closed before
publication, leaving the descriptor owned by the writer. This avoids Windows
sharing conflicts and a whole-file memory copy. The descriptor remains open
through publication; no PDF-size limit is introduced by this validation path.

This boundary prevents another user from substituting a staged pathname.
Linux fails closed if the validated file is unlinked before descriptor-based
publication can link it. Windows sharing rules prevent ordinary replacement
while the handle is open. A process running as the same user is outside the
isolation boundary: it can inspect or interfere with that user's files and
process. The writer also cannot stop a process with write/rename permission on
the destination directory from changing or removing the published file after
the call returns. Linux pins the destination directory during publication;
replacement checks the target both before and after exchange and attempts
rollback if it detects a changed target. Windows resolves the absolute
destination path again at rename: a process able to replace a destination
directory or ancestor, including through a junction or symlink, can redirect
publication between checks and rename.
Windows callers must use a trusted local destination and its trusted ancestors;
UNC network and device namespace destinations are rejected; mapped drives and
filesystem properties are not independently verified. This API does not
provide adversarial shared-directory safety. Path checks do not eliminate that
race. For default publication the final entry is created without replacement;
the explicit replacement path only replaces the identity that was confirmed
and rechecked. Callers should not treat the file as immutable afterward.

On Linux, output bytes are flushed and `fsync`ed before validation/publication;
after publication, the destination directory is `fsync`ed before the writer
reports success. Replacement also synchronizes the staging directory before
discarding its retained prior destination. A directory-sync failure returns
`success=False`, `published=True`, and `crash_durable=False`; the
already-published destination is left in place and the error is reported. Linux
documents that syncing the file alone does not necessarily persist its
directory entry, which is why the directory is synchronized too
([`fsync(2)`](https://man7.org/linux/man-pages/man2/fsync.2.html)).

On Windows, file contents are flushed and publication uses a pinned handle with
no-replace rename semantics ([`SetFileInformationByHandle`](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle)).
The writer reports successful publication but sets `crash_durable=False` and
returns a warning because this implementation does not have a supported
parent-directory flush guarantee. A Linux-style directory `fsync` is not used
on Windows.

`WriteResult` distinguishes a published file from a guaranteed crash-durable
directory entry. Cleanup failures are returned through the same result API;
they do not erase an earlier error or turn a failed save into a reported
success. If cleanup fails after publication, `published` and `output_path`
remain set so callers can distinguish that state.

After a Windows rename succeeds, the writer opens the intended destination for
attributes and compares its volume and 128-bit file ID to the retained validated
handle. If that check fails, it reports `success=False`, `published=True`,
`output_path=None`, and an unknown actual location; it does not delete the
published file. This check does not eliminate the directory-redirection race.

In-place Save uses the prepared-output publication API. It pins the original
file against new writes and deletes, checks the previously captured source
snapshot and staged content digest, and renames the retained staged handle. It
still resolves the absolute destination at rename, so callers must use a
trusted local directory and trusted ancestors. It does not provide adversarial
directory safety or Windows parent-directory crash durability; the GUI releases
its Qt document and renderer before publication.

## Rendering and preservation limits

Glyph requests QtPdf's annotation render flag for both reader pages and
thumbnails. QtPdf displays the native annotation types and appearance streams it
supports, including saved highlights, underline, strikeout, ink, rectangles,
and text notes. Glyph still draws newly created session annotations
using its overlay system. On document open, the native annotations are not
converted into Glyph records: they are view-only and are not available to
Glyph's selection, movement, edit, delete, undo, or redo controls. This keeps
native annotations from being drawn a second time as Glyph overlays and avoids
claiming edit support for PDF annotation types Glyph cannot represent. Glyph
saves rectangles as native `/Square` annotations. In-place Save verifies the
rectangle structure Glyph writes and rejects inherited `/Square` annotations
whose structure it cannot verify. Save As writes only the current Glyph
annotation snapshot while preserving the source PDF's native annotations.

Encrypted PDFs are rejected; no password or re-encryption policy is provided.
Known signatures are detected from signature fields, signature dictionaries,
byte-range markers, and DocMDP dictionaries reachable from the PDF catalog.
Detection is structural, not cryptographic validation, and cannot establish
that every malformed or nonstandard signed PDF is recognized. Signed documents
are outside this writer's supported input set.

The writer does not validate signatures, preserve certification permissions,
support arbitrary tilted text quads, or promise preservation for every PDF
feature. Successfully reopening a result with pypdf and QtPdf does not prove
that all document features were preserved. AES handling, arbitrary document
preservation, other PDF viewers, and annotation import remain outside this
foundation.
