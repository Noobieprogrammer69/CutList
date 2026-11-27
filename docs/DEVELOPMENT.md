# Developing and configuring Cutlist

For installation and everyday use, see the [README](../README.md) and [user guide](USER-GUIDE.md).

## Run locally

The source installation needs Node 22.18+, Python 3.12, uv, and FFmpeg. Ollama is optional for the local text, search, and vision models. Dependencies and weights are installed on each machine; they are not included in a source checkout.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r worker/requirements.txt
npm ci
npm run dev
```

Development starts Next.js on port 3000 and Uvicorn on port 8000. Both bind loopback by default. For the production build, stop the development process and run:

```bash
npm run build
npm start
```

The [desktop launcher](../scripts/desktop.mjs) checks dependencies, builds changed frontend sources, starts both services, and opens the browser. It expects the system tools to be installed already and does not ask for administrator privileges. Its log is `data/desktop.log`. `npm run desktop:install` creates a user application entry with paths for the current checkout. `node scripts/install-desktop.mjs --portable` regenerates the repository's portable desktop entry with those paths.

## Models

With Ollama running for the text/search/vision features, use the local setup screen or these commands:

| Command | Component |
| --- | --- |
| `npm run speech:download` | Parakeet English transcription |
| `npm run editor:download` | Qwen text proposals and editorial checks |
| `npm run model:download` | CED sound-event classifier |
| `npm run search:download` | EmbeddingGemma transcript retrieval |
| `npm run vision:download` | Qwen visual sequence review |
| `npm run tracking:install` | OpenCV face-following dependency |
| `npm run speakers:download` | Anonymous voice embeddings |

Model attributions and terms are in [MODEL-NOTICES.md](../MODEL-NOTICES.md). Speech/event downloads use pinned revisions and checksums. The event classifier validates the AudioSet class map and explicitly uses CPU inference; missing weights never trigger a volume-based laughter fallback. Caption reactions remain a separate signal.

## Configuration

Copy [`.env.example`](../.env.example) to `.env.local` for local overrides. Launch and download helpers load that file. Leave `CUTLIST_DATA` unset to use the checkout's `data` directory.

| Setting | Purpose / default |
| --- | --- |
| `CUTLIST_DATA` | Project storage; defaults to this checkout's `data` directory |
| `CUTLIST_WORKER_URL` | Internal API proxy target; `http://127.0.0.1:8000` |
| `CUTLIST_WEB_HOST` | Frontend bind address; defaults to `127.0.0.1` |
| `CUTLIST_ORIGINS` | Allowed browser origins for workspace mutations |
| `CUTLIST_ACCESS_KEY` | Optional shared invitation key; no user accounts |
| `CUTLIST_SECURE_COOKIE` | Set to `true` for HTTPS hosting; `false` for local HTTP |
| `CUTLIST_ENABLE_SETUP` | Local setup endpoints; disable with `false` on hosted workers |
| `CUTLIST_MAX_UPLOAD_GB` | File size limit on disk; default 64, allowed range 1–256 |
| `CUTLIST_ASR_THREADS` | CPU speech threads; defaults to up to four |
| `CUTLIST_ASR_BATCH` | Speech batch size; default two, maximum four |
| `CUTLIST_ASR_CHUNK_SECONDS` | Speech chunk size; default 20, accepted range 8–20 |
| `CUTLIST_ASR_MODEL_DIR` | Optional alternate speech-model directory |
| `CUTLIST_OLLAMA_URL` | Local model service; loopback HTTP only |
| `CUTLIST_EDITOR_MODEL` | Default `qwen3:1.7b` |
| `CUTLIST_EDITOR_TIMEOUT` | Per-section text request timeout; default 180 seconds |
| `CUTLIST_ACCELERATION` | `auto`, `cpu`, or requested `gpu` offload |
| `CUTLIST_VISION_MODEL` | Default `qwen3-vl:2b` |
| `CUTLIST_VISION_TIMEOUT` | Visual request timeout; default 240 seconds |
| `CUTLIST_SEARCH_MODEL` | Default `embeddinggemma:300m` |
| `CUTLIST_SPEAKER_MODEL` | Optional alternate voice embedding file |

Changing model or speech settings does not guarantee faster processing. GPU requests depend on Ollama and the actual hardware/backend; device detection alone does not establish acceleration. Speech recognition and H.264 rendering remain bounded CPU paths.

## Architecture and processing

- [Next.js interface](../app/page.tsx): upload, progress, result review, editing, and download controls. `/api` is proxied to the Python worker.
- [Worker](../worker/main.py): FastAPI endpoints, job coordination, cancellation, expiry, and one analysis executor. A separate render executor keeps finished clip exports independent of later analyses.
- [Transcription](../worker/transcription.py) and [chapters](../worker/chapters.py): bounded audio decoding, CPU speech subprocesses, checkpoint reuse, and progressive chapter results. Chapter switching improves time to first results rather than guaranteeing shorter total runtime.
- [Analysis](../worker/analysis.py): relative audio energy, pause/boundary selection, transcript patterns, explicit replay markers, ranking, and non-overlapping source intervals.
- [Semantic review](../worker/semantic.py): bounded overlapping transcript sections, cue/quote-grounded proposals, then a separate editorial acceptance call. Completed sections are cached. Both calls use the same model; agreement is not independent editorial verification.
- [Visual context](../worker/visual.py): ordered low-resolution frame sequences around event/motion anchors. Visual-only candidates require an observed setup/payoff and a separate acceptance pass; motion itself is not a highlight.
- [Retrieval](../worker/retrieval.py): resumable full-transcript embedding indexes with optional text-model checks. Search is over spoken text, not continuous video.
- [Preferences](../worker/preferences.py): opt-in bounded pairwise ranking evaluated on held-out real recordings. Downloaded model weights are unchanged.
- [Composition](../worker/composition.py), [editing](../worker/editing.py), and [exports](../worker/exports.py): retained source portions, caption/timeline mapping, versioned renders, and batch snapshots.
- [Store](../worker/store.py) and [authentication](../worker/auth.py): SQLite project/session ownership and opaque HttpOnly, SameSite browser cookies. IDs alone do not grant access. There is no account recovery or cross-device workspace sharing.

Model inference is serialized across search and analysis using a process lock. Speech memory is released before text review. Review sections use a bounded context and output; remote model URLs, cloud model tags, and remote aliases are rejected. Downloads fetch model files, not recording content.

FFmpeg analyzes mono audio without converting the whole video. Chapter mode can merge cached audio features without another full-source waveform decode; optional sound-event analysis may still require full-source audio decoding. A full 16-hour mono PCM decode uses approximately 1.73 GiB of temporary disk space. Original media and exports require additional space. Completed temporary PCM is removed.

Captions are limited to 5 MB / 50,000 cues. Recordings are limited to 16 hours. Client transfers use saved 8 MB offsets rather than whole-recording JavaScript reads; the upload fingerprint uses file metadata plus the first/last MB. Result paging is a presentation choice, not a candidate cap.

Project media, transcripts, caches and exports expire together after the selected 1/7/30 days. Temporary uploads expire after 24 hours. Cleanup runs periodically or on startup. Interrupted analyses recover saved parameters and reuse completed work. An unfinished batch ZIP needs a retry, reusing finished renders where available.

## Package or host a demo

```bash
npm run deploy:package
```

This writes `data/cutlist-deploy.zip` with source, documentation, and the sample. It excludes local environment files, model weights, recordings, databases, dependencies, and build output. The archive is not an installer or a complete deployment.

For a hosted worker, use [`.env.hosted.example`](../.env.hosted.example), an exact HTTPS origin, secure cookies, a private worker/model service, and disabled remote setup. `CUTLIST_WEB_HOST=0.0.0.0` publishes only the frontend bind address. Files live on the worker machine, and the operator can access them. SQLite/local storage and the executors assume a single worker; there is no distributed queue or multi-server coordination.

The [Lightning deployment notes](../DEPLOY-LIGHTNING.md) apply only to existing accounts: a signup attempt reported new registrations closed on November 27, 2026. Published free-tier pricing is not proof of eligibility. No remote cloud deployment has been verified.

See [testing and benchmarks](TESTING.md) before making performance or quality claims.
