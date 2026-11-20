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
import zlib

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from local_sales.album import GooglePhotosClient, collect_images, create_dated_album
from local_sales.enrichment import (
    estimate_condition,
    extract_manufacturer_description,
    extract_model_and_brand,
    resolve_manufacturer_links,
    suggest_price,
)
from local_sales.exif_dates import (
    find_oldest_photo,
    parse_jpeg_exif_datetime,
    parse_xmp_datetime,
)
from local_sales.image_pipeline import (
    PNG_SIGNATURE,
    has_exif_or_xmp_metadata,
    strip_png_metadata,
)
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

    def test_exif_and_xmp_stripped_from_album_photos_and_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / "photos"
            src_dir.mkdir()
            exif_pic = src_dir / "old_exif.jpg"
            xmp_pic = src_dir / "newer_xmp.jpg"
            exif_pic.write_bytes(build_jpeg_with_exif("2026:03:14 09:30:00"))
            xmp_pic.write_bytes(build_plain_jpeg_with_xmp("2026-04-20T12:00:00"))

            self.assertTrue(has_exif_or_xmp_metadata(exif_pic))
            self.assertTrue(has_exif_or_xmp_metadata(xmp_pic))

            album = create_dated_album(
                inputs=[src_dir],
                item_title="Clean Photo Test",
                output_root=root / "albums",
                strip_exif=True,
            )
            # Oldest date stamp must be read BEFORE stripping metadata.
            self.assertEqual(album.date_stamp, "2026-03-14")
            self.assertTrue(album.exif_stripped)

            for staged in album.photos:
                data = staged.read_bytes()
                self.assertIsNone(parse_jpeg_exif_datetime(data))
                self.assertIsNone(parse_xmp_datetime(data))
                self.assertFalse(has_exif_or_xmp_metadata(staged))
                self.assertTrue(data.startswith(b"\xff\xd8"))
                self.assertTrue(data.endswith(b"\xff\xd9"))

            # Verify PNG metadata chunk stripping (eXIf and tEXt removed)
            def _chunk(ctype: bytes, payload: bytes) -> bytes:
                crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
                return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)

            ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
            raw_scan = b"\x00" + (b"\xff\xff\xff" * 2) + b"\x00" + (b"\xff\xff\xff" * 2)
            dirty_png = (
                PNG_SIGNATURE
                + _chunk(b"IHDR", ihdr)
                + _chunk(b"tEXt", b"Comment\x00Secret GPS")
                + _chunk(b"eXIf", b"II\x2a\x00\x00\x00\x00\x00")
                + _chunk(b"IDAT", zlib.compress(raw_scan))
                + _chunk(b"IEND", b"")
            )
            png_path = root / "dirty.png"
            png_path.write_bytes(dirty_png)
            self.assertTrue(has_exif_or_xmp_metadata(png_path))
            clean_png = strip_png_metadata(dirty_png)
            clean_path = root / "clean.png"
            clean_path.write_bytes(clean_png)
            self.assertFalse(has_exif_or_xmp_metadata(clean_path))
            self.assertNotIn(b"tEXt", clean_png)
            self.assertNotIn(b"eXIf", clean_png)

    def test_optional_background_removal_pipeline(self) -> None:
        def _chunk(ctype: bytes, payload: bytes) -> bytes:
            crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
            return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)

        w, h = 10, 10
        raw_rows = bytearray()
        for y in range(h):
            raw_rows.append(0)
            for x in range(w):
                if 3 <= x <= 6 and 3 <= y <= 6:
                    raw_rows.extend((20, 40, 220))  # foreground object
                else:
                    raw_rows.extend((250, 250, 250))  # uniform background border

        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        png_bytes = (
            PNG_SIGNATURE
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"tEXt", b"Software\x00Camera")
            + _chunk(b"IDAT", zlib.compress(bytes(raw_rows)))
            + _chunk(b"IEND", b"")
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_png = root / "2026-02-01_product.png"
            src_png.write_bytes(png_bytes)

            album = create_dated_album(
                inputs=[src_png],
                item_title="Blue Widget",
                output_root=root / "albums",
                strip_exif=True,
                remove_bg=True,
                remove_bg_backend="simple",
            )
            self.assertEqual(album.date_stamp, "2026-02-01")
            self.assertEqual(len(album.background_removal), 1)
            self.assertTrue(album.background_removal[0]["applied"])
            self.assertEqual(album.background_removal[0]["backend"], "simple")

            staged_png = album.photos[0]
            self.assertFalse(has_exif_or_xmp_metadata(staged_png))

            # Decode staged PNG and verify corner became transparent while center stayed opaque.
            out_bytes = staged_png.read_bytes()
            self.assertEqual(out_bytes[:8], PNG_SIGNATURE)
            # Color type at IHDR byte index 8+4(len)+4(IHDR)+9 = byte 25 should be 6 (RGBA)
            self.assertEqual(out_bytes[25], 6)
            pos = 8
            idat = bytearray()
            while pos + 12 <= len(out_bytes):
                length = struct.unpack_from(">I", out_bytes, pos)[0]
                ctype = out_bytes[pos + 4 : pos + 8]
                if ctype == b"IDAT":
                    idat.extend(out_bytes[pos + 8 : pos + 8 + length])
                pos += 12 + length
            decompressed = zlib.decompress(bytes(idat))
            # Corner pixel (0,0) alpha byte at offset 1 + 3
            self.assertEqual(decompressed[4], 0)
            # Center pixel (5,5) alpha byte at row 5 * (1 + 40) + 1 + 5*4 + 3
            center_alpha_idx = 5 * (1 + w * 4) + 1 + 5 * 4 + 3
            self.assertEqual(decompressed[center_alpha_idx], 255)

    def test_model_number_manufacturer_link_and_description_extraction(self) -> None:
        title = "Sony WH-1000XM5 Wireless Noise Canceling Headphones"
        desc = "Barely used, like new in original carrying case."
        mfr_raw = (
            "Add to cart\n"
            "Industry-leading noise cancellation powered by dual processors and 8 microphones.\n"
            "- Battery Life: Up to 30 hours with quick charge\n"
            "- Multipoint Bluetooth 5.2 connection\n"
            "MSRP $399.99\n"
        )
        brand, model = extract_model_and_brand(f"{title}\n{desc}")
        self.assertEqual(brand, "Sony")
        self.assertEqual(model, "WH-1000XM5")

        links = resolve_manufacturer_links(brand, model)
        self.assertIsNotNone(links["product_url"])
        self.assertIn("sony.com", links["product_url"])
        self.assertIn("WH-1000XM5", links["product_url"])

        parsed = extract_manufacturer_description(mfr_text=mfr_raw)
        self.assertIn("dual processors", parsed["summary"])
        self.assertGreaterEqual(len(parsed["bullets"]), 2)
        self.assertAlmostEqual(parsed["detected_msrp"], 399.99, places=2)

        html_sample = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Garmin Forerunner 945", "brand": {"name": "Garmin"},
         "model": "010-02063-00", "description": "GPS running smartwatch with music.",
         "offers": {"price": "499.99"}}
        </script></head><body><li>Up to 2 weeks battery life</li></body></html>
        """
        html_parsed = extract_manufacturer_description(mfr_text=html_sample)
        self.assertEqual(html_parsed["brand"], "Garmin")
        self.assertEqual(html_parsed["model_number"], "010-02063-00")
        self.assertIn("GPS running smartwatch", html_parsed["summary"])
        self.assertAlmostEqual(html_parsed["detected_msrp"], 499.99, places=2)

    def test_condition_estimation_dropdown_mapping_and_price_suggestion(self) -> None:
        c_new = estimate_condition("Brand new sealed in box microwave")
        self.assertEqual(c_new.canonical, "new")
        self.assertEqual(c_new.dropdown_values["facebook"], "New")

        c_like = estimate_condition("Barely used, like new in open box")
        self.assertEqual(c_like.canonical, "like_new")
        self.assertEqual(c_like.dropdown_values["craigslist"], "like new")
        self.assertEqual(c_like.dropdown_values["facebook"], "Used - Like New")
        self.assertEqual(c_like.dropdown_values["freecycle"], "Like New")

        c_fair = estimate_condition("Works fine, some scratches and cosmetic wear")
        self.assertEqual(c_fair.canonical, "fair")
        self.assertEqual(c_fair.dropdown_values["craigslist"], "fair")

        p_like = suggest_price(
            "Sony WH-1000XM5 Headphones",
            "Like new. MSRP $400.",
            condition_canonical="like_new",
        )
        p_fair = suggest_price(
            "Sony WH-1000XM5 Headphones",
            "Fair condition. MSRP $400.",
            condition_canonical="fair",
        )
        self.assertLess(p_fair.recommended_price, p_like.recommended_price)
        self.assertEqual(p_like.reference_retail_price, 400)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pic_dir = root / "pics"
            pic_dir.mkdir()
            (pic_dir / "item.jpg").write_bytes(build_jpeg_with_exif("2026:01:05 11:00:00"))
            cli = str(PROJECT_ROOT / "local_sales_poster.py")

            analyze_proc = subprocess.run(
                [
                    sys.executable,
                    cli,
                    "analyze",
                    "--title",
                    "Sony WH-1000XM5 Headphones",
                    "--description",
                    "Barely used, like new in open box. Originally $399.",
                    "--mfr-text",
                    "Dual processor noise cancellation with 30-hour battery.",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            analyzed = json.loads(analyze_proc.stdout)
            self.assertEqual(analyzed["brand"], "Sony")
            self.assertEqual(analyzed["model_number"], "WH-1000XM5")
            self.assertEqual(analyzed["condition"]["canonical"], "like_new")
            self.assertGreater(analyzed["price_suggestion"]["recommended_price"], 0)

            prep_proc = subprocess.run(
                [
                    sys.executable,
                    cli,
                    "prepare",
                    str(pic_dir),
                    "--title",
                    "Sony WH-1000XM5 Headphones",
                    "--description",
                    "Barely used, like new in open box. Originally $400.",
                    "--mfr-text",
                    "Dual processor noise cancellation.",
                    "--condition",
                    "auto",
                    "--price",
                    "auto",
                    "--targets",
                    "craigslist",
                    "facebook",
                    "freecycle.com",
                    "--output-dir",
                    str(root / "out"),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            prep_data = json.loads(prep_proc.stdout)
            bundle = load_listing_bundle(Path(prep_data["bundle_json_path"]))
            self.assertTrue(bundle["album"]["exif_stripped"])
            self.assertFalse(
                has_exif_or_xmp_metadata(Path(bundle["album"]["photos"][0]))
            )
            self.assertEqual(
                bundle["listings"]["craigslist"]["condition_dropdown"], "like new"
            )
            self.assertEqual(
                bundle["listings"]["facebook"]["condition_dropdown"], "Used - Like New"
            )
            self.assertEqual(
                bundle["listings"]["craigslist"]["price"],
                str(bundle["item"]["price_suggestion"]["recommended_price"]),
            )
            self.assertEqual(bundle["listings"]["freecycle"]["price"], "0")
            self.assertIn(
                "WH-1000XM5", bundle["listings"]["craigslist"]["description"]
            )
            self.assertIn(
                "sony.com", bundle["listings"]["craigslist"]["description"]
            )
            self.assertIn(
                "Dual processor noise cancellation",
                bundle["listings"]["craigslist"]["description"],
            )


if __name__ == "__main__":
    unittest.main()
