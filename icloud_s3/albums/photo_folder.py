from pathlib import Path
import shutil

from icloud_s3.albums.types import PhotoFile

ALBUM_NAME_PREFIX = "icloud-s3-"


class PhotoFolder:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def write_photo(self, png_bytes: bytes, *, album_name: str, photo_id: str) -> Path:
        album_path = self.directory / album_name
        album_path.mkdir(exist_ok=True)
        png_path = album_path / f"{photo_id}.png"
        png_path.write_bytes(png_bytes)
        return png_path

    def delete_photos(self, photo_ids: tuple[str, ...], *, album_name: str) -> None:
        for photo_id in photo_ids:
            (self.directory / album_name / f"{photo_id}.png").unlink(missing_ok=True)

    def delete_albums(self, album_names: list[str]) -> None:
        for album_name in album_names:
            shutil.rmtree(self.directory / album_name, ignore_errors=True)

    def list_photo_files(self) -> list[PhotoFile]:
        return [
            PhotoFile(album_name=png_path.parent.name, photo_id=png_path.stem, png_bytes=png_path.read_bytes())
            for png_path in sorted(self.directory.glob(f"{ALBUM_NAME_PREFIX}*/*.png"))
        ]
