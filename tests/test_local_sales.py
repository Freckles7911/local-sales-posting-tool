"""Unit and CLI integration tests for local-sales-posting-tool."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from local_sales.album import GooglePhotosClient, collect_images, create_dated_album
from local_sales.exif_dates import find_oldest_photo, parse_jpeg_exif_datetime
from local_sales.posters import load_listing_bundle
from local_sales.templates import (
    ItemDetails,
    ListingConfig,
    normalize_services,
    render_all_listings,
)


def build_jpeg_with_exif(
    dt_original: str,
    endian: str = "<",
    dt_modify: str | None = None,
) -> bytes:
    """Create a minimal valid JPEG containing an APP1 EXIF DateTimeOriginal tag."""
    byte_order = b"II" if endian == "<" else b"MM"
    dt_orig_bytes = (dt_original + "\x00").encode("ascii")

    ifd0_entries = []
    extra_data = bytearray()

    # Optional DateTime (0x0132) in IFD0
    if dt_modify is not None:
        dt_mod_bytes = (dt_modify + "\x00").encode("ascii")
        ifd0_entries.append((0x0132, 2, len(dt_mod_bytes), dt_mod_bytes))

    # Reserve IFD0 table size: 2 (count) + 12*N + 4 (next IFD=0)
    num_ifd0 = len(ifd0_entries) + 1  # +1 for ExifIFD pointer (0x8769)
    ifd0_offset = 8
    ifd0_size = 2 + 12 * num_ifd0 + 4

    # ExifIFD starts after IFD0 + IFD0 string payloads
    ifd0_payload_cursor = ifd0_offset + ifd0_size
    ifd0_packed_entries = bytearray()

    for tag, ftype, count, val_bytes in ifd0_entries:
        offset = ifd0_payload_cursor + len(extra_data)
        extra_data.extend(val_bytes)
        ifd0_packed_entries.extend(
            struct.pack(endian + "HHII", tag, ftype, count, offset)
        )

    exif_ifd_offset = ifd0_payload_cursor + len(extra_data)
    # ExifIFD pointer tag (0x8769, LONG=4, count=1)
    ifd0_packed_entries.extend(
        struct.pack(endian + "HHII", 0x8769, 4, 1, exif_ifd_offset)
    )

    ifd0_block = (
        struct.pack(endian + "H", num_ifd0)
        + bytes(ifd0_packed_entries)
        + struct.pack(endian + "I", 0)
        + bytes(extra_data)
    )

    # ExifIFD: 1 entry (0x9003 DateTimeOriginal)
    exif_ifd_size = 2 + 12 * 1 + 4
    dt_orig_offset = exif_ifd_offset + exif_ifd_size
    exif_ifd_block = (
        struct.pack(endian + "H", 1)
        + struct.pack(endian + "HHII", 0x9003, 2, len(dt_orig_bytes), dt_orig_offset)
        + struct.pack(endian + "I", 0)
        + dt_orig_bytes
    )

    tiff = (
        byte_order
        + struct.pack(endian + "HI", 42, ifd0_offset)
        + ifd0_block
        + exif_ifd_block
    )
    app1_payload = b"Exif\x00\x00" + tiff
    app1_segment = (
        b"\xff\xe1" + struct.pack(">H", len(app1_payload) + 2) + app1_payload
    )
    return b"\xff\xd8" + app1_segment + b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xff\xd9"


def build_plain_jpeg_with_xmp(xmp_iso_date: str) -> bytes:
    """Create a minimal JPEG containing an APP1 XMP packet without EXIF."""
    xmp = (
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        f"<x:xmpmeta><rdf:RDF><rdf:Description><exif:DateTimeOriginal>{xmp_iso_date}</exif:DateTimeOriginal>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    ).encode("utf-8")
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return b"\xff\xd8" + app1 + b"\xff\xd9"


class LocalSalesTests(unittest.TestCase):
    def test_binary_exif_parser_little_and_big_endian(self) -> None:
        le_bytes = build_jpeg_with_exif("2025:11:04 08:15:30", endian="<")
        be_bytes = build_jpeg_with_exif("2024:07:19 17:45:05", endian=">")
        self.assertEqual(
            parse_jpeg_exif_datetime(le_bytes),
            datetime(2025, 11, 4, 8, 15, 30),
        )
        self.assertEqual(
            parse_jpeg_exif_datetime(be_bytes),
            datetime(2024, 7, 19, 17, 45, 5),
        )

    def test_oldest_photo_and_dated_album_and_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / "batch"
            out_dir = root / "albums"
            src_dir.mkdir()

            p_old = src_dir / "IMG_9999.jpg"
            p_old.write_bytes(
                build_jpeg_with_exif(
                    "2026:03:14 09:30:00",
                    endian="<",
                    dt_modify="2026:06:01 12:00:00",
                )
            )
            p_new = src_dir / "IMG_0001.jpg"
            p_new.write_bytes(build_jpeg_with_exif("2026:05:20 15:45:10", endian=">"))
            p_mid = src_dir / "PXL_20260401_120000.jpg"
            p_mid.write_bytes(b"\xff\xd8\xff\xd9")

            # Set p_old mtime to the NEWEST filesystem timestamp to ensure EXIF wins.
            newest_epoch = datetime(2026, 10, 1, 10, 0, 0).timestamp()
            older_epoch = datetime(2026, 1, 1, 10, 0, 0).timestamp()
            os.utime(p_old, (newest_epoch, newest_epoch))
            os.utime(p_new, (older_epoch, older_epoch))
            os.utime(p_mid, (older_epoch, older_epoch))

            oldest, ordered = find_oldest_photo([p_new, p_mid, p_old])
            self.assertEqual(oldest.path, p_old.resolve())
            self.assertEqual(oldest.source, "exif")
            self.assertEqual(oldest.taken_at, datetime(2026, 3, 14, 9, 30, 0))
            self.assertEqual(
                [m.path.name for m in ordered],
                ["IMG_9999.jpg", "PXL_20260401_120000.jpg", "IMG_0001.jpg"],
            )

            album = create_dated_album(
                inputs=[src_dir],
                item_title="Solid Oak Coffee Table",
                output_root=out_dir,
            )
            self.assertEqual(album.date_stamp, "2026-03-14")
            self.assertEqual(album.album_title, "2026-03-14 - Solid Oak Coffee Table")
            self.assertEqual(album.album_dir.name, "2026-03-14_solid-oak-coffee-table")
            self.assertTrue(album.manifest_path.is_file())
            self.assertEqual(
                [p.name for p in album.photos],
                [
                    "01_IMG_9999.jpg",
                    "02_PXL_20260401_120000.jpg",
                    "03_IMG_0001.jpg",
                ],
            )

            cfg = ListingConfig.from_file(PROJECT_ROOT / "config.example.yaml")
            item = ItemDetails(
                title="Solid Oak Coffee Table",
                body="Sturdy solid oak living room table in great shape.",
                price="65",
                condition="Very Good",
            )
            rendered = render_all_listings(
                item=item,
                services=["craigslist", "facebook", "freecycle.com"],
                album=album,
                config=cfg,
            )

            cl_text = Path(rendered["listings"]["craigslist"]["text_file"]).read_text(
                encoding="utf-8"
            )
            fb_text = Path(rendered["listings"]["facebook"]["text_file"]).read_text(
                encoding="utf-8"
            )
            fc_text = Path(rendered["listings"]["freecycle"]["text_file"]).read_text(
                encoding="utf-8"
            )

            # Common text appears in all selected services.
            for desc in (cl_text, fb_text, fc_text):
                self.assertIn("Cash only", desc)
                self.assertIn("No shipping available", desc)
                self.assertIn("no checks", desc)
                self.assertIn("Arlington", desc)
                self.assertIn("2026-03-14", desc)

            # Service-unique text is isolated strictly to its own service.
            self.assertIn("Craigslist chat or Craigslist email relay only", cl_text)
            self.assertNotIn("Facebook Messenger", cl_text)
            self.assertNotIn("Freecycle OFFER", cl_text)

            self.assertIn("Facebook Messenger / Marketplace chat only", fb_text)
            self.assertNotIn("Craigslist chat or Craigslist email relay", fb_text)
            self.assertNotIn("Freecycle OFFER", fb_text)

            self.assertIn("Freecycle OFFER", fc_text)
            self.assertIn("FREE", fc_text)
            self.assertNotIn("Craigslist chat or Craigslist email relay", fc_text)
            self.assertNotIn("Facebook Messenger", fc_text)

            bundle = load_listing_bundle(album.album_dir)
            self.assertEqual(
                bundle["selected_services"], ["craigslist", "facebook", "freecycle"]
            )
            self.assertTrue((album.album_dir / "launchpad.html").is_file())

    def test_takeout_zip_sidecar_and_xmp_and_google_photos_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / "takeout.zip"
            sidecar_ts = int(datetime(2022, 8, 5, 14, 0, 0).timestamp())

            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("Takeout/photo1.jpg", b"\xff\xd8\xff\xd9")
                zf.writestr(
                    "Takeout/photo1.jpg.json",
                    json.dumps({"photoTakenTime": {"timestamp": str(sidecar_ts)}}),
                )
                zf.writestr(
                    "Takeout/photo2.jpg",
                    build_plain_jpeg_with_xmp("2024-01-10T11:22:33"),
                )

            staging = root / "staging"
            imgs = collect_images([zip_path], staging_dir=staging)
            self.assertEqual(len(imgs), 2)

            album = create_dated_album(
                inputs=[zip_path],
                item_title="Camping Lantern",
                output_root=root / "albums",
            )
            self.assertEqual(album.date_stamp, "2022-08-05")
            self.assertEqual(album.oldest_photo.source, "sidecar_json")

            gp_client = GooglePhotosClient()
            gp_dry = gp_client.create_dated_google_album(album, dry_run=True)
            self.assertEqual(gp_dry["status"], "dry_run")
            self.assertEqual(gp_dry["album_title"], "2022-08-05 - Camping Lantern")

    def test_service_normalization_and_cli_subprocess(self) -> None:
        self.assertEqual(
            normalize_services(["freecycle.com", "fb", "craigslist", "freecycle"]),
            ["freecycle", "facebook", "craigslist"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pic_dir = root / "pics"
            pic_dir.mkdir()
            (pic_dir / "a.jpg").write_bytes(
                build_jpeg_with_exif("2023:02:11 10:00:00")
            )
            (pic_dir / "b.jpg").write_bytes(
                build_jpeg_with_exif("2025:06:15 16:20:00")
            )

            cli = str(PROJECT_ROOT / "local_sales_poster.py")
            inspect_proc = subprocess.run(
                [sys.executable, cli, "inspect-dates", str(pic_dir), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            inspect_data = json.loads(inspect_proc.stdout)
            self.assertEqual(inspect_data["date_stamp"], "2023-02-11")
            self.assertEqual(inspect_data["oldest_photo"]["filename"], "a.jpg")

            out_dir = root / "out"
            prep_proc = subprocess.run(
                [
                    sys.executable,
                    cli,
                    "prepare",
                    str(pic_dir),
                    "--title",
                    "Desk Lamp",
                    "--description",
                    "Adjustable metal desk lamp.",
                    "--price",
                    "25",
                    "--targets",
                    "craigslist",
                    "freecycle.com",
                    "--output-dir",
                    str(out_dir),
                    "--google-photos-dry-run",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            prep_data = json.loads(prep_proc.stdout)
            self.assertEqual(
                prep_data["album"]["album_title"], "2023-02-11 - Desk Lamp"
            )
            self.assertEqual(
                prep_data["selected_services"], ["craigslist", "freecycle"]
            )
            self.assertEqual(prep_data["google_photos"]["status"], "dry_run")
            self.assertTrue(Path(prep_data["bundle_json_path"]).is_file())
            self.assertTrue(Path(prep_data["launchpad_html_path"]).is_file())

            post_proc = subprocess.run(
                [
                    sys.executable,
                    cli,
                    "post",
                    prep_data["bundle_json_path"],
                    "--no-pause",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            post_data = json.loads(post_proc.stdout)
            self.assertIn(post_data["status"], ("playwright_not_installed", "completed"))


if __name__ == "__main__":
    unittest.main()
