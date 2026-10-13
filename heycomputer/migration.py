"""Import persistent settings from the former product name, never queued work."""
import os
from pathlib import Path
import tempfile


def import_legacy_settings(directory: Path) -> None:
    if directory.name != "HeyComputer":
        return
    legacy = directory.with_name("ReaJev")
    # Leave originals intact. Existing HeyComputer settings always win, and
    # session requests/recordings must never be replayed after the rename.
    for name in ("settings.json", "remote-token.json", "ui-memory.json"):
        source, target = legacy / name, directory / name
        if target.exists() or not source.is_file():
            continue
        fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(source.read_bytes())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass  # Another helper completed the same import first.
        finally:
            os.unlink(temporary)
