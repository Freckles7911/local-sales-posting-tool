"""Date-stamped album creation and optional Google Photos API integration."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Sequence
import urllib.error
import urllib.request
import zipfile

from .exif_dates import PhotoMeta, find_oldest_photo
from .image_pipeline import remove_photo_background, strip_image_metadata_file


SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
    ".tiff",
    ".tif",
    ".bmp",
    ".gif",
}

GOOGLE_PHOTOS_LIBRARY_BASE = "https://photoslibrary.googleapis.com/v1"
GOOGLE_PHOTOS_PICKER_BASE = "https://photospicker.googleapis.com/v1"


@dataclass
class AlbumBundle:
    """Represents a locally assembled, oldest-photo date-stamped album."""

    album_dir: Path
    album_title: str
    date_stamp: str
    oldest_photo: PhotoMeta
    photos: List[Path]
    photo_metas: List[PhotoMeta]
    manifest_path: Path
    google_photos_album_id: Optional[str] = None
    exif_stripped: bool = True
    background_removal: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "album_dir": str(self.album_dir),
            "album_title": self.album_title,
            "date_stamp": self.date_stamp,
            "oldest_photo": self.oldest_photo.to_dict(),
            "photos": [str(p) for p in self.photos],
            "photo_metas": [m.to_dict() for m in self.photo_metas],
            "manifest_path": str(self.manifest_path),
            "google_photos_album_id": self.google_photos_album_id,
            "exif_stripped": self.exif_stripped,
            "background_removal": list(self.background_removal),
        }


def slugify(text: str, max_len: int = 64) -> str:
    """Convert an item title into a clean filesystem-safe slug."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    if not cleaned:
        return "untitled-item"
    return cleaned[:max_len].strip("-") or "untitled-item"


def _is_supported_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def _safe_extract_zip(zip_path: Path, dest_dir: Path) -> List[Path]:
    """Safely extract images and Google Takeout JSON sidecars from a ZIP archive."""
    dest_root = dest_dir.resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    extracted_images: List[Path] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            member_path = Path(member.filename)
            suffix = member_path.suffix.lower()
            if suffix not in SUPPORTED_IMAGE_EXTENSIONS and suffix != ".json":
                continue

            target = (dest_root / member_path).resolve()
            if dest_root not in target.parents and target != dest_root:
                continue  # Prevent Zip Slip traversal.

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            if suffix in SUPPORTED_IMAGE_EXTENSIONS:
                extracted_images.append(target)

    return sorted(extracted_images)


def collect_images(
    inputs: Sequence[Path], staging_dir: Optional[Path] = None
) -> List[Path]:
    """Expand files, directories, and Google Takeout ZIP archives into image paths."""
    collected: List[Path] = []
    seen = set()

    def _add(p: Path) -> None:
        resolved = p.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            collected.append(resolved)

    for raw_input in inputs:
        path = Path(raw_input).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Input path does not exist: {raw_input}")

        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if _is_supported_image(child):
                    _add(child)
        elif path.is_file() and path.suffix.lower() == ".zip":
            extract_root = (
                staging_dir
                if staging_dir is not None
                else Path(tempfile.mkdtemp(prefix="local-sales-zip-"))
            )
            for extracted in _safe_extract_zip(path, extract_root / path.stem):
                _add(extracted)
        elif _is_supported_image(path):
            _add(path)
        else:
            raise ValueError(f"Unsupported image or archive input: {raw_input}")

    if not collected:
        raise ValueError("No supported image files found in the provided inputs.")

    return collected


def _place_photo(src: Path, dst: Path, copy_mode: str) -> None:
    """Place a photo into the album directory via copy, hardlink, or symlink."""
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if copy_mode == "copy":
        shutil.copy2(src, dst)
    elif copy_mode == "hardlink":
        os.link(src, dst)
    elif copy_mode == "symlink":
        os.symlink(src, dst)
    else:
        raise ValueError(
            f"Unsupported copy_mode '{copy_mode}'. Expected 'copy', 'hardlink', or 'symlink'."
        )


def create_dated_album(
    inputs: Sequence[Path],
    item_title: str,
    output_root: Path,
    date_format: str = "%Y-%m-%d",
    copy_mode: str = "copy",
    strip_exif: bool = True,
    remove_bg: bool = False,
    remove_bg_backend: str = "auto",
    remove_bg_command: Optional[str] = None,
) -> AlbumBundle:
    """Create a local date-stamped album named from the oldest photo in the batch."""
    images = collect_images([Path(p) for p in inputs])
    # Always inspect capture dates BEFORE stripping EXIF from staged copies.
    oldest, sorted_metas = find_oldest_photo(images)

    date_stamp = oldest.taken_at.strftime(date_format)
    album_title = f"{date_stamp} - {item_title.strip()}"
    dir_name = f"{date_stamp}_{slugify(item_title)}"

    album_dir = Path(output_root).expanduser().resolve() / dir_name
    photos_dir = album_dir / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    placed_photos: List[Path] = []
    bg_results: List[Dict[str, Any]] = []
    width = max(2, len(str(len(sorted_metas))))
    for idx, meta in enumerate(sorted_metas, start=1):
        target_name = f"{idx:0{width}d}_{meta.path.name}"
        target_path = photos_dir / target_name
        if target_path.exists() or target_path.is_symlink():
            target_path.unlink()

        if remove_bg:
            bg_res = remove_photo_background(
                meta.path,
                target_path,
                backend=remove_bg_backend,
                custom_command=remove_bg_command,
            )
            bg_results.append(bg_res.to_dict())
            if strip_exif:
                strip_image_metadata_file(target_path, target_path)
        elif strip_exif:
            strip_image_metadata_file(meta.path, target_path)
        else:
            _place_photo(meta.path, target_path, copy_mode=copy_mode)

        placed_photos.append(target_path)

    manifest_path = album_dir / "album_manifest.json"
    bundle = AlbumBundle(
        album_dir=album_dir,
        album_title=album_title,
        date_stamp=date_stamp,
        oldest_photo=oldest,
        photos=placed_photos,
        photo_metas=sorted_metas,
        manifest_path=manifest_path,
        exif_stripped=strip_exif,
        background_removal=bg_results,
    )

    manifest_payload = {
        **bundle.to_dict(),
        "copy_mode": copy_mode,
        "photo_count": len(placed_photos),
        "remove_bg": remove_bg,
        "remove_bg_backend": remove_bg_backend if remove_bg else None,
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8"
    )
    return bundle


class GooglePhotosClient:
    """Minimal stdlib HTTP client for Google Photos Picker & Library APIs."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        token_file: Optional[Path] = None,
    ) -> None:
        self.access_token = access_token or self._load_token(token_file)

    @staticmethod
    def _load_token(token_file: Optional[Path]) -> Optional[str]:
        env_token = os.environ.get("GOOGLE_PHOTOS_ACCESS_TOKEN")
        if env_token:
            return env_token.strip()
        if token_file is not None:
            resolved = Path(token_file).expanduser()
            if resolved.is_file():
                try:
                    data = json.loads(resolved.read_text(encoding="utf-8"))
                    return data.get("token") or data.get("access_token")
                except (OSError, json.JSONDecodeError):
                    return None
        return None

    @staticmethod
    def _json_request(
        method: str,
        url: str,
        access_token: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload_bytes = (
            json.dumps(body).encode("utf-8") if body is not None else None
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(
            url, data=payload_bytes, headers=headers, method=method
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def create_picker_session(self, access_token: Optional[str] = None) -> Dict[str, Any]:
        """Start a Google Photos Picker API session (returns pickerUri for selection)."""
        token = access_token or self.access_token
        if not token:
            raise RuntimeError(
                "Google Photos access token is required to create a Picker session."
            )
        return self._json_request(
            "POST", f"{GOOGLE_PHOTOS_PICKER_BASE}/sessions", token, body={}
        )

    def list_picker_media_items(
        self, session_id: str, access_token: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List selected media items from a completed Google Photos Picker session."""
        token = access_token or self.access_token
        if not token:
            raise RuntimeError(
                "Google Photos access token is required to read Picker media items."
            )
        data = self._json_request(
            "GET",
            f"{GOOGLE_PHOTOS_PICKER_BASE}/mediaItems?sessionId={session_id}",
            token,
        )
        return list(data.get("mediaItems", []))

    def create_dated_google_album(
        self,
        album_bundle: AlbumBundle,
        access_token: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Create an app-owned Google Photos album named with the oldest-photo date stamp."""
        token = access_token or self.access_token
        planned_requests = {
            "create_album_endpoint": f"{GOOGLE_PHOTOS_LIBRARY_BASE}/albums",
            "upload_endpoint": f"{GOOGLE_PHOTOS_LIBRARY_BASE}/uploads",
            "batch_create_endpoint": f"{GOOGLE_PHOTOS_LIBRARY_BASE}/mediaItems:batchCreate",
            "album_payload": {"album": {"title": album_bundle.album_title}},
            "photos": [p.name for p in album_bundle.photos],
        }

        if dry_run or not token:
            return {
                "status": "dry_run" if dry_run else "token_missing",
                "album_title": album_bundle.album_title,
                "date_stamp": album_bundle.date_stamp,
                "planned_requests": planned_requests,
                "instructions": (
                    "Set GOOGLE_PHOTOS_ACCESS_TOKEN or pass --google-photos-token with "
                    "scope https://www.googleapis.com/auth/photoslibrary.appendonly "
                    "to create and populate this album in Google Photos."
                ),
            }

        album_resp = self._json_request(
            "POST",
            f"{GOOGLE_PHOTOS_LIBRARY_BASE}/albums",
            token,
            body={"album": {"title": album_bundle.album_title}},
        )
        album_id = album_resp["id"]

        new_media_items = []
        for photo in album_bundle.photos:
            mime_type, _ = mimetypes.guess_type(str(photo))
            upload_headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
                "X-Goog-Upload-Content-Type": mime_type or "image/jpeg",
                "X-Goog-Upload-Protocol": "raw",
            }
            req = urllib.request.Request(
                f"{GOOGLE_PHOTOS_LIBRARY_BASE}/uploads",
                data=photo.read_bytes(),
                headers=upload_headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                upload_token = resp.read().decode("utf-8").strip()
            new_media_items.append(
                {
                    "description": f"{album_bundle.album_title} ({photo.name})",
                    "simpleMediaItem": {
                        "fileName": photo.name,
                        "uploadToken": upload_token,
                    },
                }
            )

        batch_resp = self._json_request(
            "POST",
            f"{GOOGLE_PHOTOS_LIBRARY_BASE}/mediaItems:batchCreate",
            token,
            body={"albumId": album_id, "newMediaItems": new_media_items},
        )
        album_bundle.google_photos_album_id = album_id

        # Persist updated Google Photos album ID back to album_manifest.json
        manifest = json.loads(album_bundle.manifest_path.read_text(encoding="utf-8"))
        manifest["google_photos_album_id"] = album_id
        manifest["google_photos_product_url"] = album_resp.get("productUrl")
        album_bundle.manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        return {
            "status": "uploaded",
            "album_id": album_id,
            "album_title": album_bundle.album_title,
            "product_url": album_resp.get("productUrl"),
            "results": batch_resp.get("newMediaItemResults", []),
        }
