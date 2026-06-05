"""Synthetic PDF fixtures for the Phase 6 ``ingest`` Docling-edge tests.

Every fixture is built into ``tmp_path`` from code -- NO real corpus is touched
(the Simpsonville corpus is wired in at Task 11.2 behind an env var, never
committed). Two builders:

  * fpdf2 renders a NATIVE text layer (``native_pdf`` / ``garbled_pdf`` /
    ``mixed_pdf`` / ``bates_pdf``) -- a PDF Docling reads WITHOUT OCR.
  * Pillow renders an IMAGE-ONLY page wrapped as a PDF (``scan_pdf`` /
    ``degraded_pdf``) -- no text layer, so Docling must OCR it.

The ``garbled_pdf`` string and ``degraded_pdf`` faint/noisy recipe were tuned
EMPIRICALLY at the research gate so the gate's ``char_density_ok`` trip and the
low-``ocr_score`` flag are deterministic, not threshold-fragile.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

# Phase 7 redaction-check fixtures (synthetic, PyMuPDF-free; built with
# pikepdf + fpdf2 + crafted bytes). Star-imported here so pytest discovers them
# for tests/test_redaction_check.py. noqa: the names are used as fixtures.
from tests.conftest_redaction import (  # noqa: F401
    acroform_pdf,
    annotation_text_pdf,
    bad_redaction_pdf,
    clean_pdf,
    clean_single_rev_pdf,
    embedded_file_pdf,
    incremental_save_pdf,
    metadata_pdf,
    redact_annot_pdf,
    redact_annot_text_pdf,
)

# A latin-1-safe garbled text-layer line the gate must diagnose as garbled_text:
# gibberish non-dictionary LETTER tokens (so the page clears the alphabetic-token
# floor with a ~0 wordlist hit-rate -- a present text layer Docling would TRUST)
# CO-OCCURRING with long identical-symbol runs + a long digit run (the
# char_density_ok anomaly). The co-occurrence is exactly what diagnose_page
# requires for garbled_text (a low hit-rate ALONE never garbles); mirrors the
# committed Task-2 garbled golden. fpdf2's Helvetica renders it (latin-1 safe).
# Tuned TOGETHER with ingest_gate._GARBLED_HIT_RATE / _MIN_LETTER_RATIO /
# _MAX_NONLETTER_RUN.
_GARBLED_LINE = (
    "xqzklmn zzzzqv vvvbb bbbww ;;;;;;;;;;;;;;;; ################ "
    "%%%%%%%%%%%% 8492037184920371 @@@@@@@@@@@@ jjjjwq lkjhgf nnnnmq "
)


# --------------------------------------------------------------------------- #
# Low-level builders (no pytest plumbing -- callable from any fixture).
# --------------------------------------------------------------------------- #


def _native_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Render ``pages`` (a list of pages, each a list of text lines) as a native
    fpdf2 text PDF -- a real, OCR-free text layer Docling reads directly."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    for lines in pages:
        pdf.add_page()
        for line in lines:
            pdf.multi_cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))
    return path


def _scan_page_image(
    lines: list[str],
    *,
    width: int,
    height: int,
    dpi: float,
    fill: tuple[int, int, int] = (0, 0, 0),
    bg: tuple[int, int, int] = (255, 255, 255),
    font_size: int = 36,
    blur: float = 0.0,
    noise_frac: float = 0.0,
    seed: int = 0,
):
    """Build ONE image-only page (a Pillow ``Image``): rendered text, optionally
    faint / blurred / speckled to drive OCR confidence down deterministically."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:  # pragma: no cover - font availability is environment-specific
        font = ImageFont.load_default()
    y = max(40, height // 12)
    for line in lines:
        draw.text((max(40, width // 12), y), line, fill=fill, font=font)
        y += font_size + 24
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if noise_frac:
        rng = random.Random(seed)
        px = img.load()
        for _ in range(int(width * height * noise_frac)):
            x = rng.randint(0, width - 1)
            yy = rng.randint(0, height - 1)
            v = rng.randint(0, 255)
            px[x, yy] = (v, v, v)
    return img, dpi


def _scan_pdf(path: Path, image, dpi: float) -> Path:
    """Wrap a Pillow image as a single-page, image-only PDF (no text layer)."""
    image.save(str(path), "PDF", resolution=dpi)
    return path


# --------------------------------------------------------------------------- #
# Fixtures (each builds its PDF into tmp_path and returns the Path).
# --------------------------------------------------------------------------- #


@pytest.fixture
def native_pdf(tmp_path) -> Path:
    """Clean digital PDF, real English text layer -> gate decides ``native``."""
    return _native_pdf(
        tmp_path / "native.pdf",
        [[
            "The police department search reason vehicle record.",
            "The officer requested this record which contains data.",
            "FOIA RESPONSE CONFIDENTIAL RECORDS DIVISION CASE NUMBER.",
            "The search reason and the vehicle record were documented.",
        ]],
    )


@pytest.fixture
def scan_pdf(tmp_path) -> Path:
    """Image-only scan (no text layer), CLEAN -> ``ocr_images`` (not flagged)."""
    img, dpi = _scan_page_image(
        [
            "POLICE DEPARTMENT INCIDENT REPORT",
            "The officer requested this record.",
            "Vehicle search reason documented here.",
            "Case number 2026 redacted for review.",
        ],
        width=1240, height=1754, dpi=150.0,
    )
    return _scan_pdf(tmp_path / "scan.pdf", img, dpi)


@pytest.fixture
def garbled_pdf(tmp_path) -> Path:
    """Present-but-GARBLED native text layer (no wordlist hits + density anomaly)
    -> gate diagnoses ``garbled_text`` -> ``force_full_doc_ocr``. The text layer
    is real (Docling would TRUST it), so only the gate catches it."""
    return _native_pdf(
        tmp_path / "garbled.pdf",
        [[_GARBLED_LINE for _ in range(12)]],
    )


@pytest.fixture
def mixed_pdf(tmp_path) -> Path:
    """Mostly-native multi-page doc with 1-2 garbage/blank pages -> conservative
    rollup keeps ``native`` and FLAGS the bad pages (the load-bearing rule)."""
    clean = [
        "The police department search reason vehicle record.",
        "The officer requested this record which contains data.",
        "The search reason and the vehicle record were documented.",
    ]
    garbage = [_GARBLED_LINE for _ in range(12)]
    blank = [" "]
    return _native_pdf(
        tmp_path / "mixed.pdf",
        [clean, clean, clean, clean, clean, clean, garbage, blank],
    )


@pytest.fixture
def bates_pdf(tmp_path) -> Path:
    """Native page(s) stamped with an ``SVPD-000123``-style Bates number -> the
    Bates post-pass captures it SEPARATELY with {page_no, bbox}."""
    return _native_pdf(
        tmp_path / "bates.pdf",
        [
            [
                "The police department search reason vehicle record.",
                "The officer requested this record which contains data.",
                "SVPD-000123",
            ],
            [
                "The search reason and the vehicle record were documented.",
                "The officer requested this record which contains data.",
                "SVPD-000124",
            ],
        ],
    )


@pytest.fixture
def corrupt_pdf(tmp_path) -> Path:
    """A file with a ``.pdf`` name whose bytes are NOT a valid PDF (a ``%PDF``
    header followed by garbage). Docling's pdfium backend fails to load it ->
    ``ConversionStatus.FAILURE`` (with ``raises_on_error=False``), exercising the
    ugly-PDF skip-and-flag failure contract (design sec 2.6): ``ingest`` must return
    a flagged ``IngestResult`` rather than crash or dereference a bad document.
    Not threshold-fragile: any non-PDF byte stream drives the same FAILURE."""
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(
        b"%PDF-1.4 not really a pdf\n"
        + b"garbage bytes that no PDF backend can parse " * 40
        + bytes(range(256))
    )
    return path


@pytest.fixture
def degraded_pdf(tmp_path) -> Path:
    """A FAINT, blurred, speckled image-only scan whose OCR confidence is
    genuinely low (ocr_score ~0.54 vs a clean scan's ~0.98 -- verified at the
    research gate) -> the page is flagged for human review. Deterministic
    (fixed noise seed)."""
    img, dpi = _scan_page_image(
        [
            "police department incident report record",
            "the officer requested this vehicle search reason",
        ],
        width=700, height=990, dpi=96.0,
        fill=(195, 195, 195), font_size=20, blur=2.8, noise_frac=0.20, seed=0,
    )
    return _scan_pdf(tmp_path / "degraded.pdf", img, dpi)
