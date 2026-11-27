"""Local-only setup tasks with fixed actions, progress, logs, and recovery."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
import signal
import ipaddress
from urllib.parse import urlparse

import httpx
from fastapi import Body, HTTPException, Request
from fastapi.responses import PlainTextResponse

from worker import transcription, visual, speakers, tracking, retrieval
from worker.events import EventClassifier
from worker.semantic import MODEL, URL, local_url

FEATURES = {
    "speech": {"name": "English transcription", "download_mb": 661, "script": "download_speech_model.py"},
    "laughter": {"name": "Sound-based laughter and reactions", "download_mb": 7, "script": "download_model.py"},
    "editor": {"name": "Editorial review", "download_mb": 1400, "model": MODEL},
    "vision": {"name": "Visual sequences", "download_mb": 1900, "model": visual.MODEL},
    "search": {"name": "Semantic search", "download_mb": 622, "model": retrieval.MODEL},
    "speakers": {"name": "Anonymous voice labels", "download_mb": 300, "script": "setup_optional.py", "args": ["speakers"]},
    "tracking": {"name": "Face-following dependency", "download_mb": 70, "script": "setup_optional.py", "args": ["tracking"]},
}


class SetupManager:
    def __init__(self, root, data):
        self.root, self.data = Path(root), Path(data)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.tasks, self.lock = {}, threading.RLock()
        self.model_process = None

    def snapshot(self):
        installed = set()
        service = False
        error = None
        try:
            with httpx.Client(trust_env=False) as client:
                response = client.get(local_url(URL) + "/api/tags", timeout=2)
                response.raise_for_status()
                installed = {m.get("name", m.get("model")) for m in response.json().get("models", []) if not m.get("remote_host") and not m.get("remote_model") and m.get("size", 0) > 100_000_000}
                service = True
        except Exception as exc: error = f"Local model service unavailable ({type(exc).__name__})."
        ready = {"speech": transcription.available(), "laughter": EventClassifier().tagger is not None, "speakers": speakers.available(), "tracking": tracking.available()}
        for key in ("editor", "vision", "search"):
            model = FEATURES[key]["model"]
            ready[key] = model in installed or (model + ":latest") in installed
        free = shutil.disk_usage(self.data).free
        return {"features": [{"id": key, **{k: v for k, v in value.items() if k in {"name", "download_mb", "model"}}, "ready": bool(ready[key])} for key, value in FEATURES.items()],
                "service_ready": service, "service_installed": bool(shutil.which("ollama")), "service_error": error,
                "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")), "uv": bool(shutil.which("uv")),
                "free_disk_gb": round(free/1024**3, 2), "cpu_threads": os.cpu_count(), "storage": "Files and installed models live on the worker machine.",
                "recovery": "Retry a failed task to reuse completed model layers/download parts. Missing system packages need installation outside this app. Setup never asks for administrator access."}

    def start(self, owner, feature):
        if not isinstance(feature, str) or feature not in FEATURES: raise ValueError("Choose a supported setup feature.")
        if "cloud" in FEATURES[feature].get("model", "").casefold(): raise ValueError("Cloud models are disabled.")
        if shutil.disk_usage(self.data).free < (FEATURES[feature]["download_mb"]+256)*1024**2:
            raise ValueError("Not enough free disk space for this download and its temporary files.")
        with self.lock:
            if any(t["status"] in {"queued", "running"} for t in self.tasks.values()): raise ValueError("Another installation is active. Finish or cancel it first.")
            task_id = uuid.uuid4().hex
            self.tasks[task_id] = {"id": task_id, "owner": owner, "feature": feature, "status": "queued", "progress": 0, "message": "Installation queued", "log": []}
        self.executor.submit(self.install, task_id)
        return self.public(task_id, owner)

    def public(self, task_id, owner):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or task["owner"] != owner: raise ValueError("Setup task not found in this workspace.")
            return {k: v for k, v in task.items() if k not in {"owner", "process"}}

    def update(self, task_id, **values):
        with self.lock:
            self.tasks[task_id].update(values)

    @staticmethod
    def stop(process):
        if process and process.poll() is None:
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass

    def install(self, task_id):
        task = self.tasks[task_id]
        feature = FEATURES[task["feature"]]
        try:
            self.update(task_id, status="running", message="Downloading optional local component")
            if "model" in feature:
                with httpx.Client(trust_env=False) as client, client.stream("POST", local_url(URL) + "/api/pull", json={"model": feature["model"], "stream": True}, timeout=180) as response:
                    response.raise_for_status()
                    layers = {}
                    for line in response.iter_lines():
                        if task["status"] == "cancelled": return
                        if not line: continue
                        row = json.loads(line)
                        if row.get("error"): raise ValueError(row["error"])
                        if row.get("digest") and row.get("total"):
                            layers[row["digest"]] = (row.get("completed", 0), row["total"])
                        total = sum(v[1] for v in layers.values())
                        done = sum(v[0] for v in layers.values())
                        self.update(task_id, message=row.get("status", "Downloading"), progress=min(99, round(done/total*100)) if total else 0,
                                    downloaded_mb=round(done/1024**2, 1), total_mb=round(total/1024**2, 1))
            else:
                env = {**os.environ, "PYTHONUNBUFFERED": "1", "UV_CACHE_DIR": str(self.data / "package-cache")}
                args = [sys.executable, str(self.root / "scripts" / feature["script"]), *feature.get("args", [])]
                process = subprocess.Popen(args, cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
                self.update(task_id, process=process)
                for line in process.stdout:
                    if task["status"] == "cancelled": self.stop(process); break
                    line = line.strip()[:500]
                    with self.lock:
                        task["log"] = (task["log"] + [line])[-30:]
                        task["message"] = line or task["message"]
                    if line.startswith("CUTLIST_PROGRESS "):
                        info = json.loads(line[len("CUTLIST_PROGRESS "):])
                        self.update(task_id, **info)
                if process.wait() and task["status"] != "cancelled": raise ValueError("Installation failed. Check the log, connectivity and free disk, then retry.")
                process.stdout.close()
            if task["status"] != "cancelled": self.update(task_id, status="complete", progress=100, message="Installed. Refresh availability to verify this component.")
        except Exception as exc:
            if task["status"] != "cancelled": self.update(task_id, status="error", error=f"{exc} Retry when online; completed model layers are retained.")
        finally:
            task.pop("process", None)

    def start_service(self):
        if self.snapshot()["service_ready"]: return {"status": "already_running"}
        if not shutil.which("ollama"): raise ValueError("Ollama is missing. Install it using your system package manager, then refresh Setup.")
        if self.model_process and self.model_process.poll() is None: return {"status": "starting"}
        local_url(URL)
        folder = self.root / "models/ollama"
        folder.mkdir(parents=True, exist_ok=True)
        log = (self.data / "ollama.log").open("a")
        try:
            self.model_process = subprocess.Popen(["ollama", "serve"], cwd=self.root, stdout=log, stderr=log,
                env={**os.environ, "OLLAMA_HOST": URL, "OLLAMA_MODELS": str(folder), "OLLAMA_NO_CLOUD": "1"}, start_new_session=True)
        finally: log.close()
        return {"status": "starting", "message": "Starting a local-only model service. Its downloads are stored in models/ollama. Refresh availability shortly."}

    def close(self):
        for t in self.tasks.values():
            t["status"] = "cancelled" if t["status"] in {"running", "queued"} else t["status"]
            process = t.get("process")
            self.stop(process)
        if self.model_process and self.model_process.poll() is None: self.model_process.terminate()
        self.executor.shutdown(wait=False, cancel_futures=True)


def install(app, manager):
    def local(request):
        if os.environ.get("CUTLIST_ENABLE_SETUP", "true") != "true": raise HTTPException(403, "Local setup is disabled on this worker.")
        addresses = [request.client.host if request.client else ""]
        addresses.extend(request.headers.get("x-forwarded-for", "").split(",") if request.headers.get("x-forwarded-for") else [])
        try:
            if not all(ipaddress.ip_address(address.strip()).is_loopback for address in addresses): raise ValueError("remote client")
        except ValueError:
            raise HTTPException(403, "Setup is available only on the worker machine.") from None
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}: raise HTTPException(403, "Setup is available only from a local browser.")
        if request.method == "POST" and request.headers.get("x-cutlist-setup") != "1": raise HTTPException(403, "Use the local Setup screen to install components.")

    @app.get("/api/setup")
    def status(request: Request):
        local(request)
        return manager.snapshot()

    @app.get("/api/setup/launcher")
    def launcher(request: Request):
        local(request)
        target = manager.root / "Cutlist.desktop"
        if not target.is_file(): raise HTTPException(404, "Generate the portable launcher with npm run desktop:install -- --portable.")
        return PlainTextResponse(target.read_text(), headers={"Content-Disposition": 'attachment; filename="Cutlist.desktop"'})

    @app.post("/api/setup/service")
    def service(request: Request):
        local(request)
        try: return manager.start_service()
        except ValueError as exc: raise HTTPException(422, str(exc)) from None

    @app.post("/api/setup/tasks", status_code=202)
    def task(request: Request, body: dict = Body(...)):
        local(request)
        try: return manager.start(request.state.owner, body.get("feature"))
        except ValueError as exc: raise HTTPException(422, str(exc)) from None

    @app.get("/api/setup/tasks/{task_id}")
    def task_status(request: Request, task_id: str):
        local(request)
        try: return manager.public(task_id, request.state.owner)
        except ValueError as exc: raise HTTPException(404, str(exc)) from None

    @app.post("/api/setup/tasks/{task_id}/cancel")
    def cancel(request: Request, task_id: str):
        local(request)
        try:
            manager.public(task_id, request.state.owner)
            manager.update(task_id, status="cancelled", message="Cancelled; retry to resume retained download layers.")
            process = manager.tasks[task_id].get("process")
            manager.stop(process)
            return {"status": "cancelled"}
        except ValueError as exc: raise HTTPException(404, str(exc)) from None
