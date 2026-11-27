"""Bounded, restart-safe chunk uploads. The server's offset is authoritative."""
import os
import re
import time
import uuid
import threading
import shutil

from fastapi import HTTPException, Request, Body

CHUNK = 8 * 1024 * 1024


def install(app, store, data, maximum):
    locks = {}
    directory = data / "uploads"
    directory.mkdir(exist_ok=True)

    def get_upload(identifier, owner):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise HTTPException(404, "Upload not found.")
        upload = store.upload(identifier)
        if not upload or upload["owner"] != owner or upload["expires"] <= time.time():
            raise HTTPException(404, "Upload not found or expired. Select the file again.")
        return upload

    def public(upload):
        return {**{k: upload[k] for k in ("id", "name", "size", "offset", "expires")}, "chunk_bytes": CHUNK}

    @app.post("/api/uploads", status_code=201)
    async def begin(request: Request, metadata: dict = Body(...)):
        name, size, fingerprint = metadata.get("name"), metadata.get("size"), metadata.get("fingerprint")
        if not isinstance(name, str) or not 1 <= len(name) <= 255 or not isinstance(size, int) or not 0 < size <= maximum:
            raise HTTPException(422, f"Choose a recording between 1 byte and {maximum // 1024 ** 3} GB.")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise HTTPException(422, "Invalid file fingerprint. Select the file again.")
        for old in store.uploads(request.state.owner):
            if old["fingerprint"] == fingerprint and old["size"] == size and old["expires"] > time.time() and os.path.isfile(old["path"]):
                return public(old)
        if sum(u["expires"] > time.time() for u in store.uploads(request.state.owner)) >= 8:
            raise HTTPException(429, "Too many temporary uploads. Remove an upload or wait for its 24-hour expiry.")
        if size + 512 * 1024 ** 2 > shutil.disk_usage(directory).free:
            raise HTTPException(507, "Not enough free disk space for this recording and analysis. Delete expired projects or use a smaller file.")
        identifier = uuid.uuid4().hex
        path = directory / identifier
        path.touch()
        upload = {"id": identifier, "owner": request.state.owner, "name": name.replace("\\", "/").split("/")[-1],
                  "size": size, "fingerprint": fingerprint, "offset": 0, "path": str(path), "expires": time.time() + 86400}
        store.save_upload(upload)
        return public(upload)

    @app.get("/api/uploads/{identifier}")
    async def status(identifier: str, request: Request):
        return public(get_upload(identifier, request.state.owner))

    @app.put("/api/uploads/{identifier}")
    async def append(identifier: str, request: Request):
        lock = locks.setdefault(identifier, threading.Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Another tab is uploading this file. Resume after it finishes.")
        try:
            upload = get_upload(identifier, request.state.owner)
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", request.headers.get("content-range", ""))
            if not match:
                raise HTTPException(422, "A byte range is required for each upload chunk.")
            start, end, total = map(int, match.groups())
            if start != upload["offset"] or total != upload["size"]:
                raise HTTPException(409, "Upload offset changed. Select the same file to resume from the saved position.")
            if not start <= end < total or end - start + 1 > CHUNK:
                raise HTTPException(422, "Upload chunks must be at most 8 MB and inside the file.")
            received = 0
            with open(upload["path"], "r+b") as output:
                output.seek(start)
                output.truncate(start)  # Discard an interrupted, uncommitted chunk.
                try:
                    async for chunk in request.stream():
                        received += len(chunk)
                        if received > end - start + 1:
                            raise HTTPException(422, "This upload chunk is larger than its declared range.")
                        output.write(chunk)
                    if received != end - start + 1:
                        raise HTTPException(422, "Upload chunk was interrupted. Select the same recording to resume.")
                    output.flush()
                    os.fsync(output.fileno())
                except BaseException:
                    output.truncate(start)
                    raise
            upload["offset"] = end + 1
            store.save_upload(upload)
            return public(upload)
        finally:
            lock.release()

    @app.delete("/api/uploads/{identifier}")
    async def remove(identifier: str, request: Request):
        upload = get_upload(identifier, request.state.owner)
        lock = locks.setdefault(identifier, threading.Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Pause this upload before deleting it.")
        try:
            os.unlink(upload["path"])
            store.delete_upload(identifier)
        finally:
            lock.release()
        return {"deleted": True}

    return get_upload
