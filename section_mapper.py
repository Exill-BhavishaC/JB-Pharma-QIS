"""
Module: section_mapper
Responsibility: Scans source PDF folder, maps CTD section numbers from filenames.
Builds { section_num -> pdf_path } from the source folder.
"""

import os
import re
from typing import Dict, Optional, Set
from logger_setup import get_logger

SECTION_PATTERN = re.compile(r'\b(\d+\.\d+(?:\.[a-zA-Z0-9]+)*)\b')

MANUAL_ENTRY_SECTIONS = {'1.4', '1.5', '1.5.1', '1.5.2', '1.6', '1.2', '1.3'}


def _extract_section_from_filename(filename: str) -> Optional[str]:
    """
    Extracts the FIRST valid CTD section number from a PDF filename.
    Strips extension first to avoid false matches.

    Examples:
        '3.2.P.3.1-Manufacturer.pdf'  -> '3.2.P.3.1'
        '3.2.S.2.1.pdf'               -> '3.2.S.2.1'
        '3.2.P.7. Container.pdf'      -> '3.2.P.7'
    """
    name_no_ext = os.path.splitext(filename)[0].strip('. ')
    match = SECTION_PATTERN.search(name_no_ext)
    return match.group(1) if match else None


def build_section_map(
    source_folder: str,
    mapping_doc_path: str,
    log_folder: str
) -> Dict[str, str]:
    """
    Scans source_folder recursively for PDFs.
    Returns dict: { '3.2.P.3.1': 'D:/full/path/to/file.pdf' }

    mapping_doc_path is ignored when empty.
    """
    logger = get_logger(log_folder)
    logger.info(f"Scanning source directory for PDFs recursively: {source_folder}")

    section_map: Dict[str, str] = {}

    try:
        for root, _, files in os.walk(source_folder):
            for filename in files:
                if not filename.lower().endswith('.pdf'):
                    continue

                section_num = _extract_section_from_filename(filename)
                if not section_num:
                    logger.debug(f"No CTD section found in filename: {filename}")
                    continue

                full_path = os.path.join(root, filename)

                if section_num in section_map:
                    logger.warning(
                        f"Duplicate section {section_num}: keeping "
                        f"'{section_map[section_num]}', ignoring '{full_path}'"
                    )
                    continue

                section_map[section_num] = full_path
                logger.debug(f"Mapped: {section_num} -> {full_path}")

    except Exception as e:
        logger.error(f"Failed to scan source folder {source_folder}: {e}")
        return {}

    logger.info(f"Successfully mapped {len(section_map)} CTD sections from source folder.")

    if not mapping_doc_path or not mapping_doc_path.strip():
        logger.info(
            "No separate mapping doc configured. "
            "Placeholders will be detected directly from template by docx_builder."
        )
        return section_map

    needed_sections: Set[str] = set()
    try:
        if mapping_doc_path.lower().endswith('.docx'):
            import docx as _docx
            mdoc = _docx.Document(mapping_doc_path)
            for para in mdoc.paragraphs:
                for m in SECTION_PATTERN.finditer(para.text):
                    s = m.group(1)
                    parts = s.split('.')
                    if len(parts) <= 6 and not all(p.isdigit() for p in parts):
                        needed_sections.add(s)

        elif mapping_doc_path.lower().endswith('.pdf'):
            import fitz
            mdoc = fitz.open(mapping_doc_path)
            for page in mdoc:
                for m in SECTION_PATTERN.finditer(page.get_text()):
                    s = m.group(1)
                    parts = s.split('.')
                    if len(parts) <= 6 and not all(p.isdigit() for p in parts):
                        needed_sections.add(s)
            mdoc.close()

        if needed_sections:
            logger.info(
                f"Extracted {len(needed_sections)} required sections "
                f"from mapping logic document."
            )
            manual = needed_sections & MANUAL_ENTRY_SECTIONS
            truly_missing = (
                needed_sections - set(section_map.keys()) - MANUAL_ENTRY_SECTIONS
            )
            if manual:
                logger.info(f"Manual entry sections (no PDF expected): {sorted(manual)}")
            if truly_missing:
                logger.warning(f"Missing PDFs for sections: {sorted(truly_missing)}")

    except Exception as e:
        logger.warning(f"Could not read mapping logic document (non-fatal): {e}")

    return section_map