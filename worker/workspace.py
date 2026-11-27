"""Transcript workbench, semantic search tasks, manual cuts, and preference controls."""
import copy
import json
from pathlib import Path
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import Body, HTTPException, Request

from worker import composition, preferences, retrieval
from worker.editing import DEFAULT_STYLE, clip_captions, edit_clip


def remember_edit(state, clip):
    history = state.setdefault("edit_history", {}).setdefault(str(clip["id"]), [])
    history.append(copy.deepcopy(clip))
    del history[:-10]


def install(app, store, data, get_job, guard, persist):
    executor = ThreadPoolExecutor(max_workers=1)
    tasks, task_guard = {}, threading.Lock()

    def complete(job):
        state = get_job(job)
        if state["status"] != "complete" or not state.get("result"):
            raise HTTPException(409, "Finish analysis before editing/searching the full transcript.")
        return state

    @app.get("/api/jobs/{job}/transcript")
    def transcript(job: str, offset: int = 0, limit: int = 60, query: str = ""):
        state = complete(job)
        if not 0 <= offset <= 50000 or not 1 <= limit <= 100 or len(query) > 200:
            raise HTTPException(422, "Use a valid transcript page and a search up to 200 characters.")
        result = state["result"]
        cues = [{**c, "index": i} for i, c in enumerate(result.get("cues", [])) if not query or query.casefold() in c["text"].casefold()]
        page = cues[offset:offset+limit]
        selected = [(c["start"], c["end"]) for c in page]
        words = [w for w in result.get("words", []) if any(a <= w["start"] < b for a, b in selected)]
        return {"cues": page, "words": words, "offset": offset, "total": len(cues), "word_timing": "automatic estimates" if result.get("words") else "caption cue boundaries; word timing unavailable"}

    @app.post("/api/jobs/{job}/clips", status_code=201)
    def create_clip(job: str, body: dict = Body(...)):
        with guard:
            state = complete(job)
            result = state["result"]
            try:
                parts = composition.validate(body.get("segments", [{"start": body.get("start"), "end": body.get("end")}]), result["duration"], result["maximum"])
                if composition.duration({"segments": parts}) < .25:
                    raise ValueError("Keep at least a quarter second in a manual clip.")
                title = body.get("title", "Editor-selected moment")
                if not isinstance(title, str) or not title.strip() or len(title) > 200: raise ValueError("Use a clip title of 1–200 characters.")
                for c in result["clips"]:
                    if composition.overlaps({"segments": parts}, c):
                        raise ValueError(f"This selection overlaps clip {c['id']}. Edit that clip, or remove it from the shortlist first.")
                clip_id = max(state.get("next_clip_id", 1), max((c["id"] for c in result["clips"]), default=0)+1)
                a, b = parts[0]["start"], parts[-1]["end"]
                cues = clip_captions(result.get("cues", []), a, b)
                clip = {"id": clip_id, "revision": 0, "start": a, "end": b, "segments": parts, "output_duration": composition.duration({"segments": parts}),
                        "anchor": a, "score": 0, "title": title.strip(), "reason": "Selected by the editor from the original recording.", "boundary_note": "Editor-selected source intervals",
                        "transcript_used": bool(cues), "captions": cues, "caption_source": result.get("caption_source"), "excerpt": " ".join(c["text"] for c in composition.captions_for_output({"start": a, "end": b, "segments": parts, "captions": cues})),
                        "evidence": ["Manual editor selection; no automated highlight or viewer score claimed."], "selection_method": "manual", "category": "manual",
                        "crop": {"format": "source", "x": .5, "y": .5, "resolution": "source"}, "quality": "high", "burn_captions": False,
                        "caption_style": store.preferences(state["owner"]).get("caption_style", DEFAULT_STYLE), "feedback": "unrated"}
                result["clips"].append(clip)
                state["next_clip_id"] = clip_id + 1
                persist(state)
                return clip
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc)) from None

    @app.delete("/api/jobs/{job}/clips/{clip_id}")
    def delete_clip(job: str, clip_id: int, revision: int):
        with guard:
            state = complete(job)
            c = next((c for c in state["result"]["clips"] if c["id"] == clip_id), None)
            if not c: raise HTTPException(404, "Clip not found.")
            if c.get("revision", 0) != revision: raise HTTPException(409, "The clip changed. Reopen it before removing it.")
            state.setdefault("removed_clips", {})[str(clip_id)] = copy.deepcopy(c)
            state["next_clip_id"] = max(state.get("next_clip_id", 1), clip_id + 1)
            state["result"]["clips"] = [x for x in state["result"]["clips"] if x["id"] != clip_id]
            persist(state)
        return {"removed": clip_id}

    @app.post("/api/jobs/{job}/clips/{clip_id}/restore")
    def restore_clip(job: str, clip_id: int):
        with guard:
            state = complete(job)
            c = state.get("removed_clips", {}).get(str(clip_id))
            if not c: raise HTTPException(404, "No removed clip to restore.")
            if any(composition.overlaps(c, old) for old in state["result"]["clips"]): raise HTTPException(409, "Restoring would overlap a current clip. Remove the replacement first.")
            restored = {**c, "revision": c.get("revision", 0)+1}
            state["result"]["clips"].append(restored)
            del state["removed_clips"][str(clip_id)]
            persist(state)
        return restored

    @app.post("/api/jobs/{job}/clips/{clip_id}/undo")
    def undo_edit(job: str, clip_id: int, body: dict = Body(...)):
        with guard:
            state = complete(job)
            history = state.get("edit_history", {}).get(str(clip_id), [])
            if not history: raise HTTPException(409, "There is no saved edit to undo.")
            try:
                restored = edit_clip(state["result"], clip_id, {**history[-1], "revision": body.get("revision")})
            except RuntimeError as exc: raise HTTPException(409, str(exc)) from None
            except ValueError as exc: raise HTTPException(422, str(exc)) from None
            history.pop()
            state["result"]["clips"] = [restored if c["id"] == clip_id else c for c in state["result"]["clips"]]
            persist(state)
        return restored

    def search_task(job, task_id, query):
        folder = Path(data) / job
        target = folder / ("search-" + task_id + ".json")
        def check():
            state = tasks[(job, task_id)]
            saved = store.get(job)
            if state.get("status") == "cancelled" or not saved or saved.get("expires", 0) <= time.time():
                raise ValueError("Search cancelled or project expired.")
        def update(**fields):
            tasks[(job, task_id)].update(fields)
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(tasks[(job, task_id)]))
            temporary.replace(target)
        try:
            check()
            state = complete(job)
            update(status="running", message="Waiting for the local model; completed index batches are reused")
            result = retrieval.search(state["result"].get("cues", []), query, state["result"]["maximum"], folder / "search-index",
                                      progress=lambda p, m: update(progress=p, message=m), check=check)
            check()
            update(status="complete", progress=100, result=result)
        except Exception as exc:
            if folder.is_dir():
                update(status="cancelled" if tasks[(job, task_id)].get("status") == "cancelled" else "error", error=str(exc))

    @app.post("/api/jobs/{job}/search", status_code=202)
    def start_search(job: str, body: dict = Body(...)):
        complete(job)
        query = body.get("query")
        if not isinstance(query, str) or not 3 <= len(query.strip()) <= 600:
            raise HTTPException(422, "Describe the moments you want in 3–600 characters.")
        with task_guard:
            if sum(t["status"] in {"queued", "running"} for t in tasks.values()) >= 2: raise HTTPException(429, "Two searches are pending. Finish or cancel one first.")
            task_id = uuid.uuid4().hex
            task = {"id": task_id, "status": "queued", "query": query.strip(), "progress": 0, "message": "Search queued"}
            tasks[(job, task_id)] = task
        executor.submit(search_task, job, task_id, query.strip())
        return task

    @app.get("/api/jobs/{job}/search/{task_id}")
    def search_status(job: str, task_id: str):
        if len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id): raise HTTPException(404, "Search not found.")
        if (job, task_id) in tasks: return dict(tasks[(job, task_id)])
        target = Path(data) / job / ("search-" + task_id + ".json")
        if not target.is_file(): raise HTTPException(404, "Search not found.")
        saved = json.loads(target.read_text())
        if saved["status"] in {"queued", "running"}: saved.update(status="error", error="Search interrupted by restart. Search again to reuse index checkpoints.")
        return saved

    @app.post("/api/jobs/{job}/search/{task_id}/cancel")
    def cancel_search(job: str, task_id: str):
        search_status(job, task_id)
        if (job, task_id) not in tasks: raise HTTPException(409, "This search is no longer running.")
        tasks[(job, task_id)]["status"] = "cancelled"
        return {"status": "cancelled"}

    @app.get("/api/personalization")
    def preference_status(request: Request):
        report, _ = preferences.learn(store.all(request.state.owner))
        return {**report, "enabled": bool(store.preferences(request.state.owner).get("personalization", False))}

    @app.post("/api/personalization")
    def set_personalization(request: Request, body: dict = Body(...)):
        if type(body.get("enabled")) is not bool: raise HTTPException(422, "Enable personalization with true or false.")
        settings = store.preferences(request.state.owner)
        settings["personalization"] = body["enabled"]
        store.preferences(request.state.owner, settings)
        return preference_status(request)

    @app.post("/api/jobs/{job}/personalize")
    def personalize(job: str):
        with guard:
            state = complete(job)
            report, weights = preferences.learn(store.all(state["owner"]))
            if not store.preferences(state["owner"]).get("personalization"):
                raise HTTPException(409, "Enable preference ranking first.")
            if weights is None: raise HTTPException(409, report["message"])
            state["result"]["clips"] = preferences.rerank(state["result"]["clips"], weights)
            state["result"]["personalization"] = {**report, "applied": True}
            persist(state)
            return {"clips": state["result"]["clips"], "personalization": state["result"]["personalization"]}

    return executor
