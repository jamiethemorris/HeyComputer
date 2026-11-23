"""One-time model download. Runtime detection has no network dependency."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from heycomputer.wake import MODEL_FILES, MODEL_NAME, MODEL_SHA256, MODEL_URL


def install(runtime, archive=None):
    destination = runtime / "models" / "kws-en"
    if all((destination / name).is_file() for name in MODEL_FILES):
        print("Local wake-word model is already installed.")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wake-install-", dir=destination.parent) as folder:
        root = Path(folder)
        model_archive = Path(archive) if archive else root / "model.tar.bz2"
        if not archive:
            with urllib.request.urlopen(MODEL_URL, timeout=60) as source, model_archive.open("wb") as output:
                total = 0
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > 25 * 1024 * 1024:
                        raise ValueError("Model download exceeded its expected size.")
                    output.write(chunk)
        if hashlib.sha256(model_archive.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("Model checksum did not match; nothing was installed.")
        unpacked = root / "unpacked"
        unpacked.mkdir()
        with tarfile.open(model_archive) as source:
            for name in (*MODEL_FILES, "README.md"):
                member = source.getmember(MODEL_NAME + "/" + name)
                if not member.isfile() or member.size > 8 * 1024 * 1024:
                    raise ValueError("Unexpected model archive member.")
                with source.extractfile(member) as data, (unpacked / name).open("wb") as output:
                    shutil.copyfileobj(data, output)
        # Never unpack archive paths, links, examples, executables or arbitrary files.
        destination.mkdir(exist_ok=True)
        for source in unpacked.iterdir():
            source.replace(destination / source.name)
        print("Installed the local English wake-word model (about 5.5 MB).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path,
                        default=Path.home() / "Library/Application Support/REAPER/Data/HeyComputer",
                        help="REAPER's Data/HeyComputer folder, for portable/nonstandard installations")
    parser.add_argument("--archive", type=Path, help="Use an already downloaded, checksum-verified model archive")
    args = parser.parse_args()
    install(args.runtime, args.archive)
