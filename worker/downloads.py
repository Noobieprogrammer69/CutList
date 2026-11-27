"""Resumable, bounded downloads with byte progress and optional pinned checksums."""
import hashlib
import json
from pathlib import Path
import urllib.request


def download(url, target, expected=None, maximum=2*1024**3):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and expected:
        with target.open("rb") as file:
            if hashlib.file_digest(file, "sha256").hexdigest() == expected: return target
    partial = target.with_suffix(target.suffix + ".part")
    previous = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(url, headers={"Range": f"bytes={previous}-"} if previous else {})
    with urllib.request.urlopen(request, timeout=120) as response:
        resumed = previous > 0 and response.status == 206 and response.headers.get("Content-Range", "").startswith(f"bytes {previous}-")
        done = previous if resumed else 0
        total = int(response.headers.get("Content-Length", 0)) + done
        last = -1
        with partial.open("ab" if resumed else "wb") as output:
            while block := response.read(1024*1024):
                done += len(block)
                if done > maximum: raise ValueError("Download exceeded the size budget.")
                output.write(block)
                percent = min(99, round(done/max(total, 1)*100)) if total else 0
                if percent != last:
                    print("CUTLIST_PROGRESS " + json.dumps({"progress": percent, "downloaded_mb": round(done/1024**2, 1), "total_mb": round(total/1024**2, 1), "message": f"Downloading {target.name}"}), flush=True)
                    last = percent
    if not done: raise ValueError("The model download was empty.")
    if expected:
        with partial.open("rb") as file: actual = hashlib.file_digest(file, "sha256").hexdigest()
        if actual != expected:
            partial.unlink(missing_ok=True)
            raise ValueError("The model checksum did not match. Retry the download; the installed model was preserved.")
    partial.replace(target)
    return target
