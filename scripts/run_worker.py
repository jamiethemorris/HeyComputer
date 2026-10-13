from pathlib import Path
import runpy
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    runpy.run_module("heycomputer.worker", run_name="__main__")
except Exception:
    directory = Path(sys.argv[sys.argv.index("--directory") + 1])
    log = directory.parent / "worker.log"
    log.parent.mkdir(mode=0o700, exist_ok=True)
    # Startup failures only. No request bodies or credentials are logged.
    log.write_text(traceback.format_exc())
    raise
