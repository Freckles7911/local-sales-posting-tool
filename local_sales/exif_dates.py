"""Pure-Python photo timestamp extraction and oldest-photo detection.

Supports a zero-dependency cascade:
1. Binary JPEG/TIFF EXIF (`DateTimeOriginal`, `DateTimeDigitized`, `DateTime`)
2. Embedded XMP metadata (`exif:DateTimeOriginal`, `photoshop:DateCreated`, `xmp:CreateDate`)
3. Google Takeout JSON sidecar (`photoTakenTime.timestamp`, `creationTime.timestamp`)
4. Common camera/smartphone filenames (`PXL_YYYYMMDD_HHMMSS`, `IMG_YYYYMMDD_HHMMSS`, `YYYY-MM-DD`)
5. Filesystem modification timestamp (`st_mtime`)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import struct
from typing import Dict, List, Optional, Sequence, Tuple


HEADER_READ_BYTES = 512 * 1024  # 512 KB covers APP1 EXIF/XMP headers in almost all photos.

_EXIF_DT_RE = re.compile(
    r"^\s*(\d{4})[:\-](\d{2})[:\-](\d{2})[T\s](\d{2}):(\d{2}):(\d{2})"
)

_XMP_PATTERNS = (
    re.compile(
        r"""<(?:exif:DateTimeOriginal|photoshop:DateCreated|xmp:CreateDate)>([^<]+)</(?:exif:DateTimeOriginal|photoshop:DateCreated|xmp:CreateDate)>""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""(?:exif:DateTimeOriginal|photoshop:DateCreated|xmp:CreateDate)\s*=\s*["']([^"']+)["']""",
        re.IGNORECASE,
    ),
)

_FILENAME_DT_PATTERNS = (
    re.compile(
        r"(?:PXL|IMG|VID|MVIMG|Screenshot)[_-]?(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\d{4})[-_](\d{2})[-_](\d{2})[T _-](\d{2})[:._-](\d{2})[:._-](\d{2})"
    ),
    re.compile(r"(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})"),
)

_FILENAME_DATE_ONLY_PATTERN = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


@dataclass(frozen=True)
class PhotoMeta:
    """Resolved capture timestamp metadata for a single photo."""

    path: Path
    taken_at: datetime
    source: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "path": str(self.path),
            "filename": self.path.name,
            "taken_at": self.taken_at.isoformat(timespec="seconds"),
            "date": self.taken_at.strftime("%Y-%m-%d"),
            "source": self.source,
        }


def _normalize_naive_local(dt: datetime) -> datetime:
    """Normalize aware datetimes to local naive so all timestamps compare safely."""
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _parse_exif_datetime_str(raw: str) -> Optional[datetime]:
    """Parse standard EXIF timestamp strings such as '2026:03:14 09:30:00'."""
    cleaned = raw.strip("\x00 \t\r\n")
    if not cleaned or cleaned.startswith("0000"):
        return None
    match = _EXIF_DT_RE.match(cleaned)
    if not match:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _read_ifd_entries(
    tiff: bytes, offset: int, endian: str
) -> List[Tuple[int, int, int, bytes]]:
    """Read raw IFD entries as (tag, field_type, count, raw_value_bytes)."""
    if offset <= 0 or offset + 2 > len(tiff):
        return []
    entry_count = struct.unpack_from(endian + "H", tiff, offset)[0]
    entries: List[Tuple[int, int, int, bytes]] = []
    pos = offset + 2
    for _ in range(entry_count):
        if pos + 12 > len(tiff):
            break
        tag, field_type, count = struct.unpack_from(endian + "HHI", tiff, pos)
        raw_val = tiff[pos + 8 : pos + 12]
        entries.append((tag, field_type, count, raw_val))
        pos += 12
    return entries


def _read_ascii_tag(
    tiff: bytes, endian: str, field_type: int, count: int, raw_val: bytes
) -> Optional[str]:
    """Extract an ASCII string tag value (type 2) from an IFD entry."""
    if field_type != 2 or count <= 0:
        return None
    if count <= 4:
        raw_bytes = raw_val[:count]
    else:
        val_offset = struct.unpack(endian + "I", raw_val)[0]
        if val_offset + count > len(tiff):
            return None
        raw_bytes = tiff[val_offset : val_offset + count]
    return raw_bytes.decode("ascii", errors="ignore").rstrip("\x00 ")


def _parse_tiff_exif_datetime(tiff: bytes) -> Optional[datetime]:
    """Parse EXIF capture timestamps from a TIFF header buffer."""
    if len(tiff) < 8:
        return None
    byte_order = tiff[:2]
    if byte_order == b"II":
        endian = "<"
    elif byte_order == b"MM":
        endian = ">"
    else:
        return None

    magic = struct.unpack_from(endian + "H", tiff, 2)[0]
    if magic != 42:
        return None

    ifd0_offset = struct.unpack_from(endian + "I", tiff, 4)[0]
    ifd0_entries = _read_ifd_entries(tiff, ifd0_offset, endian)

    dt_modify: Optional[datetime] = None
    exif_ifd_offset: Optional[int] = None

    for tag, field_type, count, raw_val in ifd0_entries:
        if tag == 0x0132:  # DateTime
            val = _read_ascii_tag(tiff, endian, field_type, count, raw_val)
            if val:
                dt_modify = _parse_exif_datetime_str(val)
        elif tag == 0x8769 and count >= 1:  # ExifIFD pointer
            exif_ifd_offset = struct.unpack(endian + "I", raw_val)[0]

    dt_original: Optional[datetime] = None
    dt_digitized: Optional[datetime] = None

    if exif_ifd_offset is not None:
        for tag, field_type, count, raw_val in _read_ifd_entries(
            tiff, exif_ifd_offset, endian
        ):
            if tag == 0x9003:  # DateTimeOriginal
                val = _read_ascii_tag(tiff, endian, field_type, count, raw_val)
                if val:
                    dt_original = _parse_exif_datetime_str(val)
            elif tag == 0x9004:  # DateTimeDigitized
                val = _read_ascii_tag(tiff, endian, field_type, count, raw_val)
                if val:
                    dt_digitized = _parse_exif_datetime_str(val)

    return dt_original or dt_digitized or dt_modify


def parse_jpeg_exif_datetime(data: bytes) -> Optional[datetime]:
    """Extract EXIF capture timestamp from JPEG APP1 or raw TIFF bytes."""
    if len(data) < 4:
        return None

    if data[:4] in (b"II\x2a\x00", b"MM\x00\x2a"):
        return _parse_tiff_exif_datetime(data)

    if data[:2] != b"\xff\xd8":
        return None

    pos = 2
    n = len(data)
    while pos + 2 <= n:
        while pos < n and data[pos] == 0xFF:
            pos += 1
        if pos >= n:
            break
        marker = data[pos]
        pos += 1

        # Standalone markers without a 2-byte length (RSTn, SOI, EOI, TEM).
        if marker in (0x00, 0x01, 0xD8, 0xD9) or (0xD0 <= marker <= 0xD7):
            if marker == 0xD9:
                break
            continue

        # Start of Scan marks image entropy data; EXIF APP1 headers precede SOS.
        if marker == 0xDA:
            break

        if pos + 2 > n:
            break
        seg_len = struct.unpack_from(">H", data, pos)[0]
        if seg_len < 2 or pos + seg_len > n:
            break

        payload = data[pos + 2 : pos + seg_len]
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            dt = _parse_tiff_exif_datetime(payload[6:])
            if dt is not None:
                return dt

        pos += seg_len

    return None


def parse_xmp_datetime(data: bytes) -> Optional[datetime]:
    """Scan header bytes for embedded XMP creation timestamps."""
    text = data.decode("utf-8", errors="ignore")
    for pattern in _XMP_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = match.group(1).strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            return _normalize_naive_local(datetime.fromisoformat(candidate))
        except ValueError:
            parsed = _parse_exif_datetime_str(candidate)
            if parsed is not None:
                return parsed
    return None


def parse_sidecar_json_datetime(image_path: Path) -> Optional[datetime]:
    """Extract Google Takeout JSON sidecar timestamp (`photoTakenTime.timestamp`)."""
    candidates = [
        Path(f"{image_path}.json"),
        image_path.with_suffix(".json"),
        Path(f"{image_path}.supplemental-metadata.json"),
    ]
    for sidecar in candidates:
        if not sidecar.is_file():
            continue
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("photoTakenTime", "creationTime"):
            block = payload.get(key)
            if isinstance(block, dict) and "timestamp" in block:
                try:
                    ts = float(block["timestamp"])
                    return datetime.fromtimestamp(ts)
                except (ValueError, TypeError, OSError):
                    continue
    return None


def parse_filename_datetime(image_path: Path) -> Optional[datetime]:
    """Infer capture timestamp from standard Pixel/Android/iOS/ISO filenames."""
    name = image_path.stem
    for pattern in _FILENAME_DT_PATTERNS:
        match = pattern.search(name)
        if not match:
            continue
        year, month, day, hour, minute, second = (int(g) for g in match.groups())
        try:
            return datetime(year, month, day, hour, minute, second)
        except ValueError:
            continue

    date_only = _FILENAME_DATE_ONLY_PATTERN.search(name)
    if date_only:
        year, month, day = (int(g) for g in date_only.groups())
        try:
            return datetime(year, month, day, 0, 0, 0)
        except ValueError:
            return None
    return None


def extract_photo_timestamp(image_path: Path) -> PhotoMeta:
    """Resolve the capture timestamp for an image via EXIF -> XMP -> JSON -> filename -> mtime."""
    resolved = image_path.expanduser().resolve()
    header_bytes = b""
    try:
        with resolved.open("rb") as fh:
            header_bytes = fh.read(HEADER_READ_BYTES)
    except OSError:
        header_bytes = b""

    exif_dt = parse_jpeg_exif_datetime(header_bytes)
    if exif_dt is not None:
        return PhotoMeta(path=resolved, taken_at=exif_dt, source="exif")

    xmp_dt = parse_xmp_datetime(header_bytes)
    if xmp_dt is not None:
        return PhotoMeta(path=resolved, taken_at=xmp_dt, source="xmp")

    sidecar_dt = parse_sidecar_json_datetime(resolved)
    if sidecar_dt is not None:
        return PhotoMeta(path=resolved, taken_at=sidecar_dt, source="sidecar_json")

    filename_dt = parse_filename_datetime(resolved)
    if filename_dt is not None:
        return PhotoMeta(path=resolved, taken_at=filename_dt, source="filename")

    mtime_dt = datetime.fromtimestamp(resolved.stat().st_mtime)
    return PhotoMeta(path=resolved, taken_at=mtime_dt, source="mtime")


def find_oldest_photo(
    image_paths: Sequence[Path],
) -> Tuple[PhotoMeta, List[PhotoMeta]]:
    """Determine the oldest photo in a batch and return (oldest, sorted_photos)."""
    if not image_paths:
        raise ValueError("Cannot determine oldest photo from an empty image batch.")
    metas = [extract_photo_timestamp(Path(p)) for p in image_paths]
    sorted_metas = sorted(metas, key=lambda m: (m.taken_at, str(m.path)))
    return sorted_metas[0], sorted_metas
