from pathlib import Path
import subprocess

OSASCRIPT_PATH = Path("/usr/bin/osascript")


class MacosPhotosClient:
    def import_png(self, png_path: Path, *, album_name: str) -> None:
        escaped_path = str(png_path.resolve()).replace("\\", "\\\\").replace('"', '\\"')
        script = (
            'tell application "Photos"\n'
            f'if not (exists album "{album_name}") then make new album named "{album_name}"\n'
            f'import POSIX file "{escaped_path}" into album "{album_name}" skip check duplicates true\n'
            "end tell"
        )
        subprocess.run([OSASCRIPT_PATH, "-e", script], check=True, capture_output=True)  # noqa: S603 - fixed binary; the only user path is escaped
