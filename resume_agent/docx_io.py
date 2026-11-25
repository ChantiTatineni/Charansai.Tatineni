"""Read and edit a .docx resume in place so its formatting survives."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from docx import Document
from docx.text.paragraph import Paragraph


def _all_paragraphs(doc) -> list[Paragraph]:
    """Body paragraphs plus paragraphs inside tables, in reading order, de-duplicated."""
    seen, out = set(), []

    def visit_tables(tables):
        for t in tables:
            for row in t.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        if id(p._p) not in seen:
                            seen.add(id(p._p))
                            out.append(p)
                    visit_tables(cell.tables)

    for p in doc.paragraphs:
        seen.add(id(p._p))
        out.append(p)
    visit_tables(doc.tables)
    return out


def extract(path: str | Path) -> list[dict]:
    """Return [{i, text, style, bullet}] for every non-empty paragraph."""
    doc = Document(str(path))
    rows = []
    for i, p in enumerate(_all_paragraphs(doc)):
        text = p.text.strip()
        if not text:
            continue
        numbered = p._p.pPr is not None and p._p.pPr.numPr is not None
        rows.append({
            "i": i,
            "text": text,
            "style": p.style.name if p.style is not None else "",
            "bullet": numbered or "List" in (p.style.name if p.style is not None else ""),
        })
    return rows


def as_numbered_text(rows: list[dict]) -> str:
    return "\n".join(f"[{r['i']}]{' •' if r['bullet'] else ''} {r['text']}" for r in rows)


def _set_text(p: Paragraph, new: str) -> None:
    """Replace a paragraph's text and keep the formatting of its runs.

    If the paragraph opens with a bold label run (for example "Languages:"), and
    the new text starts with the same label, the label keeps its bold run and the
    rest goes into the following run's formatting.
    """
    runs = [r for r in p.runs if r.text]
    if not runs:
        p.add_run(new)
        return
    first = runs[0]
    label = first.text.strip()
    if len(runs) > 1 and first.bold and label and new.startswith(label):
        first.text = new[: len(label)] + (" " if first.text.endswith(" ") else "")
        rest = new[len(label):].lstrip() if first.text.endswith(" ") else new[len(label):]
        runs[1].text = rest
        for r in runs[2:]:
            r.text = ""
        return
    first.text = new
    for r in runs[1:]:
        r.text = ""


def apply_edits(
    base: str | Path,
    out: str | Path,
    edits: dict[int, str],
    reorders: list[list[int]] = (),
    deletions: set[int] = frozenset(),
) -> None:
    """Copy base to out, rewrite paragraphs by index, reorder bullet groups, drop
    deleted paragraphs. All indices refer to the base document."""
    doc = Document(str(base))
    paras = _all_paragraphs(doc)
    for i, text in edits.items():
        if 0 <= i < len(paras):
            _set_text(paras[i], text)
    for order in reorders:
        _reorder([paras[i]._p for i in order if 0 <= i < len(paras)])
    for i in deletions:
        if 0 <= i < len(paras):
            el = paras[i]._p
            if el.getparent() is not None:
                el.getparent().remove(el)
    doc.save(str(out))


def _reorder(els) -> None:
    """Put sibling elements into the given order, inside the slots they already
    occupy, so bullets move without losing their formatting."""
    if len(els) < 2:
        return
    parent = els[0].getparent()
    if parent is None or any(e.getparent() is not parent for e in els):
        return
    anchors = sorted(parent.index(e) for e in els)
    for e in els:
        parent.remove(e)
    for pos, e in zip(anchors, els):
        parent.insert(pos, e)


def to_pdf(docx_path: str | Path) -> Path | None:
    """Convert with LibreOffice if it is installed; return the PDF path or None."""
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None
    docx_path = Path(docx_path)
    subprocess.run(
        [exe, "--headless", "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path)],
        check=True, capture_output=True, timeout=180,
    )
    pdf = docx_path.with_suffix(".pdf")
    return pdf if pdf.exists() else None
