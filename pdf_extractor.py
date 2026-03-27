"""
Module: pdf_extractor
Responsibility: Extracts content from CTD Module 3 PDFs.
"""
import os
import fitz  # PyMuPDF
import warnings
from collections import Counter
from typing import List, Optional, Dict, Set
from logger_setup import get_logger

warnings.filterwarnings("ignore")

MIN_CONTENT_CHARS = 100
MIN_IMAGE_PX      = 50


class ExtractedContent:
    """Structured result from a single PDF extraction."""
    def __init__(self):
        self.docx_path:       str        = ""
        self.images:          List[bytes] = []
        self.noise_blocklist: Set[str]   = set()


def _build_noise_blocklist(pdf_path: str, logger) -> Set[str]:
    """
    Auto-detects header/footer text by finding text that repeats
    across multiple pages in the top/bottom margins of the PDF.

    Algorithm:
    - For each page, collect normalised text from the top 12% and bottom 10%.
    - Any text appearing on >= min(3, total_pages) pages is treated as noise.

    Returns a set of normalised lowercase strings to suppress during cleaning.
    """
    try:
        doc         = fitz.open(pdf_path)
        total_pages = len(doc)

        if total_pages <= 1:
            doc.close()
            return set()

        # Collect per-page sets so a string appearing in both top AND bottom
        # of the same page is only counted once per page.
        page_texts: List[Set[str]] = []
        for page in doc:
            h        = page.rect.height
            w        = page.rect.width
            page_set: Set[str] = set()
            clips = [
                fitz.Rect(0, 0,        w, h * 0.12),  # top 12 %
                fitz.Rect(0, h * 0.90, w, h),          # bottom 10 %
            ]
            for clip in clips:
                for block in page.get_text("blocks", clip=clip):
                    text = block[4].strip()
                    if not text:
                        continue
                    norm = " ".join(text.lower().split())
                    if len(norm) >= 3:
                        page_set.add(norm)
            page_texts.append(page_set)

        doc.close()

        # Count how many pages each text appears on
        freq: Counter = Counter()
        for page_set in page_texts:
            for text in page_set:
                freq[text] += 1

        # Text on >= threshold pages is noise (header/footer)
        threshold = min(3, total_pages)
        blocklist = {t for t, c in freq.items() if c >= threshold}

        if blocklist:
            logger.info(
                f"{os.path.basename(pdf_path)}: "
                f"auto-detected {len(blocklist)} header/footer noise strings."
            )

        return blocklist

    except Exception as e:
        logger.warning(
            f"Could not build noise blocklist for "
            f"{os.path.basename(pdf_path)}: {e}"
        )
        return set()


def _detect_with_layout(pdf_path: str, logger) -> Optional[List[int]]:
    """
    AI-based header/footer removal using pymupdf-layout + pymupdf4llm.

    Returns 0-based content page indices. Returns None on any failure.
    """
    try:
        import pymupdf.layout
        import pymupdf4llm

        chunks = pymupdf4llm.to_markdown(
            pdf_path,
            page_chunks=True,
            header=False,
            footer=False,
            show_progress=False,
        )

        if not isinstance(chunks, list):
            logger.warning(
                f"{os.path.basename(pdf_path)}: page_chunks ignored "
                f"(got string). Upgrade pymupdf4llm. Using fallback."
            )
            return None

        content_pages = []
        for chunk in chunks:
            page_idx  = chunk.get("metadata", {}).get("page", 0)
            text      = chunk.get("text", "")
            real_text = " ".join(text.split())
            if len(real_text) >= MIN_CONTENT_CHARS:
                content_pages.append(page_idx)

        return content_pages if content_pages else None

    except ImportError as e:
        logger.warning(
            f"Layout import failed ({e}). "
            f"Install: pip install pymupdf-layout. Using fallback."
        )
        return None
    except Exception as e:
        logger.error(
            f"Layout detection failed for "
            f"{os.path.basename(pdf_path)}: {e}"
        )
        return None


def _detect_with_fallback(pdf_path: str, logger) -> Optional[List[int]]:
    """
    Fallback detection using percentage-based margin clipping.
    """
    try:
        doc           = fitz.open(pdf_path)
        content_pages = []
        for i, page in enumerate(doc):
            h    = page.rect.height
            w    = page.rect.width
            clip = fitz.Rect(0, h * 0.22, w, h * 0.90)
            text = page.get_text(clip=clip).strip()
            if len(" ".join(text.split())) >= MIN_CONTENT_CHARS:
                content_pages.append(i)
        doc.close()
        return content_pages if content_pages else None
    except Exception as e:
        logger.error(f"Fallback detection failed for {pdf_path}: {e}")
        return None


def _detect_content_pages(pdf_path: str, logger) -> Optional[List[int]]:
    """
    Detect content pages using layout mode first, then fallback clipping.
    """
    result = _detect_with_layout(pdf_path, logger)
    if result is not None:
        return result
    logger.warning(
        f"{os.path.basename(pdf_path)}: "
        f"Using fallback clip-based page detection."
    )
    return _detect_with_fallback(pdf_path, logger)


def extract_pdf_content(
    pdf_path:            str,
    log_folder:          str,
    section_num:         str             = "",
    section_page_limits: Dict[str, int] = None,
    section_start_pages: Dict[str, int] = None,
) -> ExtractedContent:
    """
    Main extraction entry point.
    """
    logger    = get_logger(log_folder)
    content   = ExtractedContent()
    base_name = os.path.basename(pdf_path)

    if section_page_limits is None:
        section_page_limits = {}
    if section_start_pages is None:
        section_start_pages = {}

    # Build auto-detected noise blocklist FIRST (used later in docx_builder)
    content.noise_blocklist = _build_noise_blocklist(pdf_path, logger)

    content_pages = _detect_content_pages(pdf_path, logger)
    if content_pages:
        logger.info(
            f"{base_name}: {len(content_pages)} content pages "
            f"(0-based: {content_pages})"
        )
    else:
        logger.warning(
            f"{base_name}: no content pages detected — using all pages."
        )

    if section_num and section_num in section_page_limits:
        limit = int(section_page_limits[section_num])
        if content_pages and len(content_pages) > limit:
            original      = len(content_pages)
            content_pages = content_pages[:limit]
            logger.info(
                f"{base_name}: {section_num} limit={limit}, "
                f"trimmed {original}->{len(content_pages)}: {content_pages}"
            )

    logger.info(f"Converting {base_name} via pdf2docx")
    try:
        from pdf2docx import Converter
        temp_docx_path = os.path.join(
            log_folder, f"temp_layout_{base_name}.docx"
        )

        # Determine start page (skip cover/TOC pages if configured)
        start_page = 0
        if section_num and section_num in section_start_pages:
            start_page = int(section_start_pages[section_num])
            logger.info(
                f"{base_name}: skipping first {start_page} pages "
                f"(cover/TOC). Starting at page {start_page + 1}."
            )

        # Determine end page (respect page limits if configured)
        end_page = None
        if section_num and section_num in section_page_limits:
            limit    = int(section_page_limits[section_num])
            end_page = start_page + limit
            logger.info(
                f"{base_name}: converting pages {start_page} to {end_page} only."
            )

        cv = Converter(pdf_path)
        cv.convert(
            temp_docx_path,
            start=start_page,
            end=end_page,          # None = convert to last page
            multi_processing=False
        )
        cv.close()

        if os.path.exists(temp_docx_path):
            content.docx_path = temp_docx_path
            logger.info(f"pdf2docx converted {base_name} OK.")
        else:
            logger.error(f"pdf2docx no output for {base_name}.")
    except Exception as e:
        logger.error(
            f"pdf2docx failed for {base_name}: {e}", exc_info=True
        )

    logger.info(f"Extracting images from {base_name}")
    try:
        doc           = fitz.open(pdf_path)
        pages_to_scan = content_pages if content_pages else range(len(doc))
        img_count     = 0

        for page_idx in pages_to_scan:
            page        = doc[page_idx]
            page_height = page.rect.height

            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    base_image = doc.extract_image(xref)
                    img_bytes  = base_image["image"]
                    width      = base_image.get("width",  0)
                    height     = base_image.get("height", 0)

                    if width < MIN_IMAGE_PX or height < MIN_IMAGE_PX:
                        continue

                    img_rects = page.get_image_rects(xref)
                    if img_rects:
                        r = img_rects[0]
                        if (r.y1 < page_height * 0.22 or
                                r.y0 > page_height * 0.90):
                            continue

                    content.images.append(img_bytes)
                    img_count += 1
                except Exception as img_e:
                    logger.debug(f"Skipped img xref={xref}: {img_e}")

        doc.close()
        logger.info(f"PyMuPDF: {img_count} images from {base_name}.")
    except Exception as e:
        logger.error(f"Image extraction failed: {e}", exc_info=True)

    return content