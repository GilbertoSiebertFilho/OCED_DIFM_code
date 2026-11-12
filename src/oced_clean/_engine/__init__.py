"""OCED engine: the decoders of the raw monitor formats (formats), the cleaning (clean: the SMS 3-sigma sequence of
harvest files, settings method = "sms3sigma", and the 3-sigma of planter, sprayer and other point files, applied3s),
the cleaning reports (report) and the run orchestration (run)."""
import os as _os
import time as _time


def source_stamp() -> dict:
    """The engine's source files as {path relative to engine/: (mtime_ns, size)}."""
    root = _os.path.dirname(_os.path.abspath(__file__))
    out = {}
    for dirpath, dirnames, filenames in _os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                p = _os.path.join(dirpath, fn)
                try:
                    st = _os.stat(p)
                except OSError:
                    continue
                out[_os.path.relpath(p, root)] = (st.st_mtime_ns, st.st_size)
    return out


# When this package was imported and the source files it was imported from. Streamlit keeps imported modules across
# runs and browser sessions: app/oced.py compares source_stamp() with SOURCE_STAMP and reloads the package when a file
# was added, removed or changed (see _drop_stale_engine there), so that an update of engine/ reaches a running app
# without a restart. A changed file counts also when its time is OLDER: a restore of a backup keeps the backup's times.
LOADED_AT = _time.time()
SOURCE_STAMP = source_stamp()
