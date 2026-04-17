from __future__ import annotations

import os
import struct
import sys
from typing import List, Optional, Tuple

from cquarry.config import CALIBRE_RATING_SCALE, DEFAULT_DB_PATHS, get_db_path, set_db_path


def get_jpeg_size(filepath: str) -> Optional[Tuple[int, int]]:
    """Parse JPEG header to extract (width, height) without external dependencies."""
    try:
        with open(filepath, 'rb') as f:
            data = f.read(1024) # only need the header
            if not data or data[0:2] != b'\xff\xd8':
                return None
            
            i = 2
            while i < len(data):
                # Ensure we are at a marker
                while i < len(data) and data[i] != 0xff:
                    i += 1
                while i < len(data) and data[i] == 0xff:
                    i += 1
                
                if i >= len(data):
                    return None
                
                marker = data[i]
                i += 1
                
                if marker in (0xc0, 0xc2): # SOF0 or SOF2
                    if i + 7 <= len(data):
                        height, width = struct.unpack(">HH", data[i+3:i+7])
                        return width, height
                
                if i + 1 >= len(data):
                    return None
                length = struct.unpack(">H", data[i:i+2])[0]
                i += length
    except Exception:
        pass
    return None


def calibre_rating_to_stars(rating: Optional[int]) -> Optional[float]:
    """Convert Calibre's internal rating (0-10) to stars (0-5)."""
    if rating is None or rating == 0:
        return None
    return rating / CALIBRE_RATING_SCALE


def format_stars(rating: Optional[float]) -> str:
    if rating is None:
        return ""
    rating = max(0.0, min(5.0, rating))
    full = int(rating)
    half = rating - full >= 0.5
    empty = 5 - full - (1 if half else 0)
    s = "\u2605" * full
    if half:
        s += "\u2606"
    s += "\u2606" * empty
    return f" [{s} {rating:.1f}/5]"


def normalize_author_display(authors: Optional[str], primary_only: bool = False) -> str:
    """Format author string for display."""
    if not authors:
        return "Unknown Author"
    parts = [a.strip() for a in authors.split(',')]
    if primary_only:
        return parts[0]
    return " & ".join(parts)


def author_sort_key(author_sort: Optional[str], primary_only: bool = False) -> str:
    key = (author_sort or "").lower()
    if primary_only:
        key = key.split('&')[0].strip()
    return key


def detect_series_gaps(indices_str: str, max_index: Optional[float]) -> List[int]:
    """Detect missing entries in a series based on index numbers."""
    if not indices_str or max_index is None:
        return []
    indices = set()
    for s in indices_str.split(','):
        try:
            idx = float(s)
            if idx == int(idx):
                indices.add(int(idx))
        except ValueError:
            continue
    expected = set(range(1, int(max_index) + 1))
    return sorted(expected - indices)


def _resolve_path(path: str) -> Optional[str]:
    """Expand and validate a path to metadata.db. Returns None if not found."""
    path = os.path.expanduser(path)
    if os.path.isdir(path):
        path = os.path.join(path, "metadata.db")
    if os.path.exists(path):
        return os.path.abspath(path)
    return None


def find_db(explicit: Optional[str] = None) -> str:
    """Locate metadata.db.

    Resolution order:
      1. Explicit --db argument
      2. Saved config (~/.config/cquarry/config.json)
      3. Default paths (./metadata.db, ~/Calibre Library/metadata.db, etc.)
      4. Interactive prompt (if stdin is a TTY)
    """
    # 1. Explicit argument
    if explicit:
        resolved = _resolve_path(explicit)
        if resolved:
            return resolved
        raise FileNotFoundError(f"Database not found: {explicit}")

    # 2. Saved config
    saved = get_db_path()
    if saved and os.path.exists(saved):
        return saved

    # 3. Default paths
    for p in DEFAULT_DB_PATHS:
        if os.path.exists(p):
            path = os.path.abspath(p)
            set_db_path(path)
            return path

    # 4. Interactive prompt (TTY only)
    if sys.stdin.isatty():
        print("First run: no Calibre database configured.")
        try:
            raw = input("  Path to metadata.db (or directory containing it): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise FileNotFoundError(
                "Could not find metadata.db. Specify with --db /path/to/metadata.db"
            )
        if raw:
            resolved = _resolve_path(raw)
            if resolved:
                set_db_path(resolved)
                print(f"  Saved: {resolved}")
                return resolved
            raise FileNotFoundError(f"Database not found: {raw}")

    raise FileNotFoundError(
        "Could not find metadata.db. Specify with --db /path/to/metadata.db"
    )
