"""Lossless image metadata stripping and optional background removal pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
from typing import Dict, List, Optional, Tuple
import zlib

from .exif_dates import parse_jpeg_exif_datetime, parse_xmp_datetime


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_METADATA_CHUNKS = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME", b"iCCP"}


@dataclass
class BackgroundRemovalResult:
    """Outcome of running optional background removal on a single photo."""

    input_path: Path
    output_path: Path
    applied: bool
    backend: str
    message: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "applied": self.applied,
            "backend": self.backend,
            "message": self.message,
        }


def strip_jpeg_metadata(data: bytes) -> bytes:
    """Strip EXIF, XMP, IPTC, ICC, Photoshop, and comment segments from JPEG bytes."""
    if len(data) < 2 or data[:2] != b"\xff\xd8":
        return data

    out = bytearray(b"\xff\xd8")
    pos = 2
    n = len(data)

    while pos < n:
        if data[pos] != 0xFF:
            # Unexpected non-marker bytes before SOS; append remainder safely.
            out.extend(data[pos:])
            break
        while pos < n and data[pos] == 0xFF:
            pos += 1
        if pos >= n:
            break
        marker = data[pos]
        pos += 1

        if marker == 0x00:
            continue
        if marker == 0xD9:  # EOI
            out.extend((0xFF, 0xD9))
            break
        if marker == 0x01 or (0xD0 <= marker <= 0xD7):  # Standalone markers
            out.extend((0xFF, marker))
            continue
        if marker == 0xDA:  # SOS: entropy-coded scan follows up to EOI
            out.extend((0xFF, 0xDA))
            out.extend(data[pos:])
            break

        if pos + 2 > n:
            out.extend((0xFF, marker))
            out.extend(data[pos:])
            break

        seg_len = struct.unpack_from(">H", data, pos)[0]
        if seg_len < 2 or pos + seg_len > n:
            out.extend((0xFF, marker))
            out.extend(data[pos:])
            break

        # Drop APP1..APP15 (EXIF, XMP, ICC, IPTC, vendor tags) and COM (comments).
        if (0xE1 <= marker <= 0xEF) or marker == 0xFE:
            pos += seg_len
            continue

        out.extend((0xFF, marker))
        out.extend(data[pos : pos + seg_len])
        pos += seg_len

    return bytes(out)


def _iter_png_chunks(data: bytes) -> List[Tuple[bytes, bytes, bytes]]:
    """Parse raw PNG chunks as (chunk_type, chunk_data, raw_chunk_bytes)."""
    if len(data) < 8 or data[:8] != PNG_SIGNATURE:
        raise ValueError("Not a valid PNG byte stream.")
    chunks: List[Tuple[bytes, bytes, bytes]] = []
    pos = 8
    n = len(data)
    while pos + 12 <= n:
        length = struct.unpack_from(">I", data, pos)[0]
        chunk_type = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > n:
            break
        chunk_data = data[pos + 8 : pos + 8 + length]
        chunks.append((chunk_type, chunk_data, data[pos:end]))
        pos = end
        if chunk_type == b"IEND":
            break
    return chunks


def strip_png_metadata(data: bytes) -> bytes:
    """Strip eXIf, tEXt, zTXt, iTXt, tIME, and iCCP chunks from PNG bytes."""
    if len(data) < 8 or data[:8] != PNG_SIGNATURE:
        return data

    out = bytearray(PNG_SIGNATURE)
    for chunk_type, _chunk_data, raw_chunk in _iter_png_chunks(data):
        if chunk_type in PNG_METADATA_CHUNKS:
            continue
        out.extend(raw_chunk)
    return bytes(out)


def strip_image_metadata_file(src: Path, dst: Path) -> bool:
    """Write a metadata-stripped copy of `src` to `dst`. Returns True if stripped."""
    src_path = Path(src).expanduser().resolve()
    dst_path = Path(dst).expanduser().resolve()
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    raw = src_path.read_bytes()
    if raw[:2] == b"\xff\xd8":
        dst_path.write_bytes(strip_jpeg_metadata(raw))
        return True
    if raw[:8] == PNG_SIGNATURE:
        dst_path.write_bytes(strip_png_metadata(raw))
        return True

    # Optional Pillow fallback for non-JPEG/non-PNG formats (WEBP/TIFF/etc.)
    try:
        from PIL import Image  # type: ignore

        with Image.open(src_path) as img:
            clean = Image.new(img.mode, img.size)
            clean.putdata(list(img.getdata()))
            clean.save(dst_path)
        return True
    except ImportError:
        if src_path != dst_path:
            shutil.copy2(src_path, dst_path)
        return False


def has_exif_or_xmp_metadata(path: Path) -> bool:
    """Check whether an image still contains EXIF, XMP, or PNG text/EXIF chunks."""
    resolved = Path(path).expanduser().resolve()
    raw = resolved.read_bytes()

    if raw[:2] == b"\xff\xd8":
        if parse_jpeg_exif_datetime(raw) is not None:
            return True
        if parse_xmp_datetime(raw) is not None:
            return True
        if b"Exif\x00\x00" in raw or b"<x:xmpmeta" in raw or b"http://ns.adobe.com/xap/" in raw:
            return True
        return False

    if raw[:8] == PNG_SIGNATURE:
        for chunk_type, _data, _raw in _iter_png_chunks(raw):
            if chunk_type in PNG_METADATA_CHUNKS:
                return True
        return False

    return parse_jpeg_exif_datetime(raw) is not None or parse_xmp_datetime(raw) is not None


def _make_png_chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
    return struct.pack(">I", len(chunk_data)) + chunk_type + chunk_data + struct.pack(">I", crc)


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _simple_png_corner_key(src: Path, dst: Path, tolerance: int = 38) -> bool:
    """Pure-Python border flood-fill background remover for 8-bit RGB/RGBA PNGs."""
    raw = src.read_bytes()
    if raw[:8] != PNG_SIGNATURE:
        return False

    chunks = _iter_png_chunks(raw)
    ihdr = next((d for t, d, _ in chunks if t == b"IHDR"), None)
    if ihdr is None or len(ihdr) != 13:
        return False

    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if bit_depth != 8 or color_type not in (2, 6) or compression != 0 or filter_method != 0 or interlace != 0:
        return False

    bpp = 3 if color_type == 2 else 4
    idat_bytes = b"".join(d for t, d, _ in chunks if t == b"IDAT")
    decompressed = zlib.decompress(idat_bytes)
    stride = width * bpp
    expected = height * (1 + stride)
    if len(decompressed) < expected:
        return False

    rows: List[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ftype = decompressed[pos]
        pos += 1
        scan = bytearray(decompressed[pos : pos + stride])
        pos += stride
        recon = bytearray(stride)
        for i in range(stride):
            a = recon[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ftype == 0:
                recon[i] = scan[i]
            elif ftype == 1:
                recon[i] = (scan[i] + a) & 0xFF
            elif ftype == 2:
                recon[i] = (scan[i] + b) & 0xFF
            elif ftype == 3:
                recon[i] = (scan[i] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                recon[i] = (scan[i] + _paeth_predictor(a, b, c)) & 0xFF
            else:
                return False
        rows.append(recon)
        prev = recon

    # Build mutable RGBA pixel array
    rgba = bytearray(width * height * 4)
    for y in range(height):
        src_row = rows[y]
        for x in range(width):
            si = x * bpp
            di = (y * width + x) * 4
            rgba[di] = src_row[si]
            rgba[di + 1] = src_row[si + 1]
            rgba[di + 2] = src_row[si + 2]
            rgba[di + 3] = src_row[si + 3] if bpp == 4 else 255

    # Estimate uniform background from image corners
    corners = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
    ]
    bg_samples = [
        (
            rgba[(y * width + x) * 4],
            rgba[(y * width + x) * 4 + 1],
            rgba[(y * width + x) * 4 + 2],
        )
        for x, y in corners
    ]
    bg_r = sum(c[0] for c in bg_samples) // len(bg_samples)
    bg_g = sum(c[1] for c in bg_samples) // len(bg_samples)
    bg_b = sum(c[2] for c in bg_samples) // len(bg_samples)

    def _matches_bg(px_idx: int) -> bool:
        dr = rgba[px_idx] - bg_r
        dg = rgba[px_idx + 1] - bg_g
        db = rgba[px_idx + 2] - bg_b
        return math.sqrt(dr * dr + dg * dg + db * db) <= tolerance

    # Edge-seeded BFS flood-fill so interior colors matching background stay intact.
    visited = bytearray(width * height)
    stack: List[Tuple[int, int]] = []
    for x in range(width):
        stack.append((x, 0))
        if height > 1:
            stack.append((x, height - 1))
    for y in range(1, height - 1):
        stack.append((0, y))
        if width > 1:
            stack.append((width - 1, y))

    while stack:
        x, y = stack.pop()
        flat = y * width + x
        if visited[flat]:
            continue
        visited[flat] = 1
        px_idx = flat * 4
        if not _matches_bg(px_idx):
            continue
        rgba[px_idx + 3] = 0
        if x > 0:
            stack.append((x - 1, y))
        if x + 1 < width:
            stack.append((x + 1, y))
        if y > 0:
            stack.append((x, y - 1))
        if y + 1 < height:
            stack.append((x, y + 1))

    # Encode clean RGBA PNG (zero metadata chunks)
    raw_scanlines = bytearray()
    row_bytes = width * 4
    for y in range(height):
        raw_scanlines.append(0)
        start = y * row_bytes
        raw_scanlines.extend(rgba[start : start + row_bytes])

    new_ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    compressed = zlib.compress(bytes(raw_scanlines), level=6)
    png_bytes = (
        PNG_SIGNATURE
        + _make_png_chunk(b"IHDR", new_ihdr)
        + _make_png_chunk(b"IDAT", compressed)
        + _make_png_chunk(b"IEND", b"")
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(png_bytes)
    return True


def _pillow_corner_key(src: Path, dst: Path, tolerance: int = 38) -> bool:
    """Pillow corner-flood background removal supporting JPEG/PNG/WEBP."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return False

    with Image.open(src) as img:
        rgba = img.convert("RGBA")
        w, h = rgba.size
        px = rgba.load()
        corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
        bg_r = sum(c[0] for c in corners) // 4
        bg_g = sum(c[1] for c in corners) // 4
        bg_b = sum(c[2] for c in corners) // 4

        visited = bytearray(w * h)
        stack: List[Tuple[int, int]] = []
        for x in range(w):
            stack.append((x, 0))
            if h > 1:
                stack.append((x, h - 1))
        for y in range(1, h - 1):
            stack.append((0, y))
            if w > 1:
                stack.append((w - 1, y))

        while stack:
            x, y = stack.pop()
            flat = y * w + x
            if visited[flat]:
                continue
            visited[flat] = 1
            r, g, b, _a = px[x, y]
            if math.sqrt((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2) > tolerance:
                continue
            px[x, y] = (255, 255, 255, 0)
            if x > 0:
                stack.append((x - 1, y))
            if x + 1 < w:
                stack.append((x + 1, y))
            if y > 0:
                stack.append((x, y - 1))
            if y + 1 < h:
                stack.append((x, y + 1))

        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.suffix.lower() in (".jpg", ".jpeg"):
            white_bg = Image.new("RGB", (w, h), (255, 255, 255))
            white_bg.paste(rgba, mask=rgba.split()[3])
            white_bg.save(dst, quality=92)
        else:
            rgba.save(dst)
    return True


def _run_rembg(src: Path, dst: Path) -> bool:
    """Attempt background removal via installed `rembg` package or CLI binary."""
    try:
        from rembg import remove  # type: ignore

        dst.parent.mkdir(parents=True, exist_ok=True)
        output_bytes = remove(src.read_bytes())
        dst.write_bytes(output_bytes)
        return True
    except ImportError:
        pass

    rembg_bin = shutil.which("rembg")
    if rembg_bin:
        dst.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [rembg_bin, "i", str(src), str(dst)],
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0 and dst.is_file()
    return False


def _run_custom_command(src: Path, dst: Path, command_template: str) -> bool:
    """Run user-provided background removal command template with {input} and {output}."""
    parts = shlex.split(command_template)
    argv = [
        p.replace("{input}", str(src)).replace("{output}", str(dst)) for p in parts
    ]
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode == 0 and dst.is_file()


def remove_photo_background(
    src: Path,
    dst: Optional[Path] = None,
    backend: str = "auto",
    custom_command: Optional[str] = None,
) -> BackgroundRemovalResult:
    """Optionally remove image background using configured backend with local fallbacks."""
    src_path = Path(src).expanduser().resolve()
    dst_path = (
        Path(dst).expanduser().resolve()
        if dst is not None
        else src_path.with_name(f"{src_path.stem}_nobg.png")
    )

    if backend == "command":
        if not custom_command:
            raise ValueError("custom_command is required when backend='command'.")
        if _run_custom_command(src_path, dst_path, custom_command):
            return BackgroundRemovalResult(
                input_path=src_path,
                output_path=dst_path,
                applied=True,
                backend="command",
                message="Background removed via custom command.",
            )
        shutil.copy2(src_path, dst_path)
        return BackgroundRemovalResult(
            input_path=src_path,
            output_path=dst_path,
            applied=False,
            backend="command",
            message="Custom background removal command failed; kept original photo.",
        )

    if backend in ("rembg", "auto"):
        if _run_rembg(src_path, dst_path):
            return BackgroundRemovalResult(
                input_path=src_path,
                output_path=dst_path,
                applied=True,
                backend="rembg",
                message="Background removed with rembg.",
            )
        if backend == "rembg":
            shutil.copy2(src_path, dst_path)
            return BackgroundRemovalResult(
                input_path=src_path,
                output_path=dst_path,
                applied=False,
                backend="rembg",
                message="rembg not installed (pip install rembg); kept original photo.",
            )

    if backend in ("pillow", "auto"):
        if _pillow_corner_key(src_path, dst_path):
            return BackgroundRemovalResult(
                input_path=src_path,
                output_path=dst_path,
                applied=True,
                backend="pillow",
                message="Background keyed with Pillow edge-flood filter.",
            )
        if backend == "pillow":
            shutil.copy2(src_path, dst_path)
            return BackgroundRemovalResult(
                input_path=src_path,
                output_path=dst_path,
                applied=False,
                backend="pillow",
                message="Pillow not installed; kept original photo.",
            )

    if backend in ("simple", "auto"):
        if _simple_png_corner_key(src_path, dst_path):
            return BackgroundRemovalResult(
                input_path=src_path,
                output_path=dst_path,
                applied=True,
                backend="simple",
                message="Background removed with pure-Python PNG edge-flood keyer.",
            )

    if src_path != dst_path:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)
    return BackgroundRemovalResult(
        input_path=src_path,
        output_path=dst_path,
        applied=False,
        backend=backend,
        message=(
            "Optional background removal skipped (install `rembg` or `Pillow` for JPEG/AI cutout; "
            "kept clean photo)."
        ),
    )
