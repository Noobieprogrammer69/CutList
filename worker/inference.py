"""Cross-process lock keeps Cutlist model inference within a laptop RAM budget."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time


@contextmanager
def slot(check=lambda: None):
    root = Path(os.environ.get("CUTLIST_DATA", Path(__file__).resolve().parents[1] / "data"))
    root.mkdir(parents=True, exist_ok=True)
    with (root / "model.lock").open("a") as lock:
        while True:
            check()
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
