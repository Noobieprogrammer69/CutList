# Cutlist

A local workspace for freelance video editors. Transcribe a recording, review its spoken context, find distinct clip candidates, adjust the cuts, and export video. **There is no five-clip cap.** No accounts, paid APIs, subscriptions, or YouTube access required. Readable Noto Sans and persistent light/dark modes are bundled locally.

## Launch

This checkout already has Node dependencies, Python `.venv`, the playable sample, and the optional English speech model. Restart an older running worker after these changes.

```bash
cd /home/jover/CutList
npm run dev
```

Open **http://127.0.0.1:3000**. The command starts Next.js on port 3000 and the Python worker on port 8000, bound to loopback. Ctrl+C stops both. For less overhead while processing long recordings:

```bash
npm run build
npm start
```

For a fresh checkout, use Node **22.18+**, Python **3.12**, FFmpeg and uv:

```bash
sudo pacman -S --needed ffmpeg uv nodejs npm
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r worker/requirements.txt
npm ci
```

FFmpeg needs libx264, AAC and libass/subtitles support for captioned exports. The Arch/Omarchy package includes these. Internet is needed for initial installation/model downloads; processing then runs locally.

## Cloud demo packaging

Run `npm run deploy:package` to create `data/cutlist-deploy.zip`, containing source and the sample without local recordings, models or `.env.local`. The hosted configuration exposes only the frontend and disables remote setup. The [Lightning guide](DEPLOY-LIGHTNING.md) now applies to existing accounts only: an actual signup attempt on November 27 reported that new signups are closed. Do not register with Lightning for this deployment. Its documented free CPU sessions also require restarting after four hours and include only 10 GB of storage. No remote cloud deployment has been verified; published free-tier pricing does not establish account eligibility or availability.

## Search, transcript editing, chapters and desktop setup

The latest six improvements run through the ordinary workspace:

1. **Search the complete spoken transcript by meaning.** Open a completed project → **Search the recording & create your own cuts** → enter a natural-language search brief. Cutlist builds a local, caption-aligned embedding index over every cue, including moments outside the automatic shortlist. Index batches persist and are reused for later queries. The top semantic matches have source times and can populate a manual selection. If the local editorial model is available, a separate call checks the brief and exclusions; otherwise results explicitly say similarity only, with exclusions unverified. Similarity is not a highlight probability. This search covers spoken text; silent visual events are not in its transcript index. First indexing can take time on a long recording. Install **Semantic search** in Setup or run `npm run search:download` once (EmbeddingGemma 300M, approximately 622 MB; see model terms in MODEL-NOTICES.md).
2. **Create and edit cuts through the transcript.** Click a cue time to seek, drag-select words/exchanges, or use Start here / End here. ASR words use estimated recognition times; supplied captions select actual caption-cue boundaries without inventing word timings. Create a manual cut even when the detector missed it. In the clip editor, **Remove pauses or mistakes from this cut** removes a source interval or a sentence, or tightens caption gaps with padding. Preview skips removed portions; export concatenates only retained intervals. SRT/VTT/ASS and XML use the same retained-time mapping. The maximum applies to retained output duration; retained source intervals cannot overlap another cut. Up to 16 retained portions are supported. Draft cuts can be undone, saved edits have ten undo steps, and removed shortlist cuts can be restored. All changes are non-destructive and revision checked. Caption-gap trimming is opt-in because pauses can carry meaning.
3. **True chapter-by-chapter processing.** For uncaptained recordings longer than the selected 1/5/10/15-minute chapter size (default 5 minutes), the coordinator decodes just that section, transcribes it in a subprocess, releases speech memory, then reviews it before advancing to the next chapter. Maximum-length lookback/lookahead protects exchanges spanning chapter boundaries. Results publish after each chapter, with source coverage, before later chapters are transcribed. Speech/review checkpoints survive retries; final transcript and ranking merge source-relative intervals and remove duplicate moments. Model inference is serialized across search and analysis with a local process lock. Supplied/cached full transcripts skip speech and use ordinary bounded editorial sections. Chapter switching may add total runtime; it improves time to first results, not guaranteed throughput. Chapter audio features are merged and reused for final ranking/waveforms without a second full-source waveform decode; optional full-source sound-event classification can still require audio decoding. Incompatible containers need their prepared clip previews after completion; native browser formats can preview provisional cuts directly.
4. **Ordered visual sequences and visual-only candidates.** Visual review now samples six 384-wide ordered frames over a short event interval, instead of relying only on widely separated stills. Audio/replay anchors or low-resolution motion changes propose where to inspect. A local vision model must show a concrete setup and visible payoff and pass a separate skeptical check before creating a visual-only candidate. Motion by itself never becomes a highlight, and no audio/laughter is inferred from images. Clips without speech can use this path when visual review is enabled and its model is installed. Sampled sequences can still miss intervening action; this is not continuous-video understanding. Visual scanning/review is optional and adds CPU work. Predictions and their frame times remain visible on result cards.
5. **Measured preference ranking.** **Learn from my Keep / Skip decisions** trains a small local pairwise ranker from real feedback; it does not fine-tune the language model. Requires at least 20 reviewed clips across 3 real recordings, with at least 5 kept and 5 skipped. Sample/manual examples are excluded, recording fingerprints group re-analyses, and entire recordings are held out of training for evaluation. Only a demonstrated held-out improvement enables a bounded score adjustment (at most ±0.8); otherwise original ranking is retained. Enable it explicitly, re-evaluate new feedback, and apply it to a saved project or future analyses. Original scores are preserved. An evaluation on synthetic test labels verifies the mechanics, not personal improvement on your footage.
6. **Desktop launcher and setup screen.** Double-click the executable `Cutlist.desktop` in this repository, or run `./cutlist`. The launcher checks/installs project dependencies, builds changed frontend sources, starts the local worker/app, and opens the browser. It reuses an already running current version; older workers must be closed first. Status/errors go to `data/desktop.log` with desktop notifications. **Setup & model downloads** shows free disk space, per-feature download estimates and installed components, can start a local Ollama service, downloads whitelisted optional components with progress, and offers cancellation/retry/recovery logs. Speech/event downloads resume partial bytes and verify pinned hashes; Ollama resumes model layers. An app-started Ollama service uses `models/ollama`; an existing local service keeps its configured model store. No recordings are sent to model download servers. Existing system packages (Node 22.18+, uv, FFmpeg, and Ollama for model features) remain prerequisites; the app does not request administrator privileges.

To install the desktop menu shortcut on your own machine (the portable launcher already exists here):

```bash
cd /home/jover/CutList
npm run desktop:install
```

The menu installer writes only your user application entry. It was not run in this restricted workspace because that directory is outside its writable roots. The generated portable entry passes `desktop-file-validate`. The launcher and full browser flow remain unverified here because local socket binding is blocked and no browser is connected.

You can also launch normally and use the new Setup screen:

```bash
cd /home/jover/CutList
npm run build
npm start
# Open http://127.0.0.1:3000 → Setup & model downloads
```

For full model/runtime tests after installing components:

```bash
CUTLIST_TEST_SPEECH=1 CUTLIST_TEST_EDITOR=1 CUTLIST_TEST_VISION=1 CUTLIST_TEST_SEARCH=1 npm test
npm run test:preview
npm run test:browser  # with the ordinary app running
```

## Editorial upgrade: quality, visual context and batch delivery

The six improvements are connected to the normal workspace:

1. **Real-footage evaluation.** Open a completed project → **Real-footage quality benchmark**. Independently label source intervals as worth editing or boring/contextless, including good moments the detector missed. The table compares the current shortlist with the saved basic-detector baseline using one-to-one matches at temporal IoU ≥ 0.5, missed moments, boring-section hits, Keep rate and Keep-without-trimming rate. Signed start/end errors are included in the JSON. Labels persist across restart and saved-recording re-analysis. Download the benchmark for comparisons; without labels/reviews the corresponding metrics remain unknown. Partial labels do not establish precision. The bundled synthetic sample is explicitly identified.
2. **Separate editorial acceptance pass.** Choose a **General, Gaming, Interview, Tutorial, or Storytelling** profile. The local text model first proposes grounded setups/payoffs, then a separate skeptical call checks the exact intervals against surrounding dialogue for complete context, a complete payoff and a concrete reason to watch. Missing/duplicate decisions and incomplete output fail validation. Rejected proposals are excluded. The same model performs both calls; agreement is not independent human verification. Profiles, both passes and completed windows are cached. Actual humor/quality gains still need your real-footage labels.
3. **Optional visual context.** Enable **Use sampled visual context** after installing the local vision model. For each bounded transcript section, the worker reviews short ordered frame sequences around supporting anchors or detected motion changes. It checks the model supports images, validates its frame references, caches descriptions, and supplies observations to both editorial passes. Results show timestamped observations as model predictions. No full-resolution video conversion is performed. Sampled sequences may miss intervening action; separate visual-only proposals require a visible payoff and a final visual acceptance pass. If visual review fails, it is disabled explicitly and transcript/audio review continues.
4. **Progressive results.** During model review, completed sections publish playable, provisionally ranked clips and section coverage. Later sections continue processing. Editing/export unlock once the final non-overlapping shortlist is ready because ranks and IDs can change. First-reviewed-results latency is measured separately from total time. Chapter mode alternates speech and editorial review; the standard mode still finishes transcription first. Sequential processing bounds model memory on an 8 GB laptop. Final status survives reload/restart, and cached windows resume work.
5. **Hardware-aware model processing.** `CUTLIST_ACCELERATION=auto` requests Ollama GPU offload when a local render/NVIDIA device is accessible, `cpu` forces CPU and `gpu` explicitly requests offload. Ollama chooses supported layers/backends; device presence does not prove acceleration. Speech recognition and H.264 exports remain bounded CPU paths. `npm run benchmark:hardware` compares real CPU/offload text-model calls on identical captions and records accepted intervals and model timings; it reports missing models explicitly. No GPU device is exposed in this implementation environment, so no GPU speedup has been measured or claimed. [Ollama hardware support](https://docs.ollama.com/gpu).
6. **Repeatable delivery.** In each clip editor, choose caption presets, colors, outline and anonymous/manual speaker labels; save a workspace default. WebVTT voice tags are preserved as speaker metadata. Optional voice clustering labels saved clip cues as Speaker 1/2/etc.; numbers apply only within that clip and require review. Optional face following interpolates a crop along detected frontal faces, with a clear error when no face is found; manual framing remains available. It does not select the active speaker. **Batch export clips** selects up to 100 saved cuts, can apply the saved caption style to all selected cuts atomically, then renders serially and produces a ZIP containing MP4/M4A, SRT, VTT and an exact revision manifest. Existing finished renders are reused. Edits made after batch start do not alter that batch snapshot; start a new batch for newer edits. A worker restart interrupts an unfinished ZIP; starting again reuses finished clips.

Install optional features once, with Ollama running in another terminal:

```bash
cd /home/jover/CutList
npm run editor:download     # text proposal + editorial check
npm run vision:download     # optional ~1.9 GB Qwen3-VL:2b, Ollama >= 0.12.7
npm run tracking:install    # optional CPU OpenCV face detector
npm run speakers:download   # optional voice embedding model
npm run build
npm start
```

None of these features requires a paid API or an account. Downloads require internet; media processing uses local files and a loopback model server. The default workflow remains usable without the optional visual, face or voice components, with availability stated explicitly. For 8 GB RAM, visual review is optional and models run sequentially; it adds work and is not advertised as a speed improvement.

Evaluate a downloaded real-footage benchmark and benchmark your own input:

```bash
npm run test:quality -- /path/to/cutlist-real-footage-benchmark.json
npm run benchmark:hardware -- --captions /path/to/stream.vtt --output /tmp/cutlist-hardware.json
# Also measure a fresh audio decode and speech pass (can take a long time):
npm run benchmark:hardware -- --captions /path/to/stream.vtt --media /path/to/stream.mp4
```

## Enable meaning and payoff review

**Transcription and content review are separate steps.** The previous version transcribed the speech, then used English word-pattern rules. A routine question with an ordinary answer could qualify without having a useful or funny payoff. The basic candidate detector is now stricter, and its results explicitly say they have not been reviewed for meaning or humor.

For actual language-model review, install the free local content model once. Ollama is already installed on this laptop. If it is not running, start it in another terminal:

```bash
ollama serve
```

Then, from this repository:

```bash
npm run editor:download
```

This downloads **qwen3:1.7b** (about 1.4 GB) into Ollama's model store. It does not upload a recording or transcript. See [the Ollama model package](https://ollama.com/library/qwen3:1.7b), [structured output documentation](https://docs.ollama.com/capabilities/structured-outputs), and [model notices](MODEL-NOTICES.md). The setup helper and launch command load `.env.local` consistently.

In Cutlist, leave **Review meaning, setup and payoff** enabled. Choose **Funny exchanges & stories**, **Stories, explanations & strong opinions**, or **A mix of strong moments**. Open an existing project and click **Re-analyze saved recording · reuse transcript** to use the new review without uploading/transcribing again. Use **Transcribe this recording again** if its old transcript is inaccurate; that deliberately performs a fresh, slower speech pass. The original project remains available.

The local model sees overlapping, bounded transcript sections. It must identify an understandable setup and a specific payoff, supply real cue IDs, and copy exact setup/payoff quotes. Invalid IDs, invented quotes, weak scores, missing payoff, and exchanges exceeding your clip length are rejected. No requested quota forces weak clips. Returned cuts show both quotes and the model's editorial explanation. All distinct qualifying cuts are returned, with overlapping alternatives removed.

Inference uses a local loopback Ollama server, conservative CPU or requested local GPU offload, a 4096-token context, bounded output, no thinking pass and at most four CPU threads. Cloud tags, remote endpoints and remote aliases are rejected. Completed reviews are cached by transcript, model digest, goal, keywords and length limit. Model failure is shown explicitly; fallback candidates are never labelled model-reviewed.

**Limits:** a small language model can misunderstand humor or select a weak moment. Grounding quotes proves the words exist, not that the editorial judgment is correct. The text model reads speech. Optional sampled-frame observations add limited visual context; action between frames and context-dependent jokes may be missed. Reviewing every section of a long transcript adds CPU time. Actual content-model inference on this user's stream has **not** been verified in this restricted implementation environment; the model cannot be downloaded/reached here.

## Fast local workflow

The interface highlights the actual optimizations:

- **Audio-first analysis:** FFmpeg decodes mono audio for analysis; HD video is not re-encoded during Find clips. Native MP4/WebM can play directly. For incompatible containers/codecs, **Preview clip** prepares only the selected interval as a browser-compatible MP4/M4A. That prepared file can also be downloaded. Save draft timing before previewing an incompatible source.
- **Batched CPU speech recognition:** Parakeet uses two streams per batch and up to four CPU threads by default, bounded chunks and a short decoding tail. Local gain normalization helps a quiet setup survive a later loud reaction. This normalization is applied only to recognition input; measured energy and exported media use the unmodified source.
- **Resume completed work:** atomic per-chunk speech checkpoints survive cancellation or restart. Retry rebuilds temporary PCM and decodes only unfinished speech chunks. Completed model-review sections are also cached.
- **Reuse saved transcripts:** changing clip goals/lengths can reuse source media, transcript and audio features. A new goal or length may need a fresh model review; it does not need another speech pass. Sources on the same filesystem share safe hard links; cross-filesystem reuse copies them.
- **Bounded processing and lighter loading:** vectorized ten-second waveform blocks, indexed caption/event lookups, small status payloads, metadata-only project lists, and 20 cards per page. Search/filter/source-order sorting works across all candidates. Paging is a display choice, not a result limit.
- **Responsive exports:** one separate, bounded FFmpeg render worker lets saved clip previews/downloads finish while another long recording is being analyzed. Analysis remains one job at a time.
- **Resumable uploads:** saved offsets, 8 MB chunks, fewer transfer/flush round trips, and no whole-recording JavaScript read. Reselect the same file after a reload to resume. A first/last-MB SHA-256 fingerprint plus file metadata identifies the upload.

**Measured locally:** see [benchmarks/local-performance.json](benchmarks/local-performance.json), generated with `npm run benchmark`. On the original 115-second synthetic sample, the updated speech pass took about **12 seconds**, peaked around **1.6 GB** in its subprocess, and recognized 265 words. A fresh subprocess reused all nine speech checkpoints in about **0.01 seconds**, peaking around **41 MB**. Audio feature measurement took about 0.005 seconds. A synthetic 15-hour transcript/ranking case (10,800 cues, 900 events) returned 900 non-overlapping candidates in about 0.009 seconds.

These are sample and synthetic tests, not measured 15-hour media processing or real-world quality results. Sequential first-pass comparisons varied and showed only modest speed changes; **no large first-pass speedup is promised**. The clear gains are avoiding full-video conversions and avoiding repeated speech work. The user's previous hour-long run recorded about 377 seconds for speech recognition. Reducing a video to 360p does not remove an hour of speech. A first-time 7–15-hour recording can still take a long time on an 8 GB CPU laptop, and content-model review adds time. Progress shows processed source time and an estimated remaining **speech** time at the current rate.

To reproduce current measurements:

```bash
npm run benchmark
# Fast feature/ranking test without loading speech weights:
npm run benchmark -- --skip-speech
```

## Recording length and HD exports

Recordings up to **16 hours** and **64 GB** are supported by default; `CUTLIST_MAX_UPLOAD_GB` can set a different disk limit (1–256 GB). This is an on-disk limit, not a RAM allocation. Captions support SRT/WebVTT up to 5 MB / 50,000 cues. Decoding needs about **1.73 GiB** temporary PCM for 16 hours, plus the original recording and requested exports; insufficient free disk produces a useful error. Completed PCM is removed, while small checkpoints remain until project deletion/expiry.

Analysis leaves original video pixels untouched. **Original framing** exports keep source dimensions, including HD/4K. Optional vertical or square crops can retain original crop pixels, or explicitly resize to 720/1080 wide. A 360p source cannot contain HD detail; resizing it adds pixels without recovering detail. **High** exports use H.264 CRF 18; **Maximum** uses CRF 14 and larger files. Neither is a bit-for-bit lossless copy. Audio exports are AAC M4A. Only the requested clip is rendered, with accurate transcoded in/out points rather than keyframe-only stream-copy cuts.

## Editing and the sample

1. **Load sample** loads the ordinary 115-second *Studio Notes* MP4. It is an original scripted conversation with synthesized voices, measured SRT/VTT timings and no recorded human laughter. It is not an editorial or human-speech accuracy benchmark.
2. Leave matching captions checked, or uncheck them to try automatic transcription. Turn transcription off too to compare audio-only candidates. **Illustrative sample replay markers** are off by default, optional and clearly labelled; they are not audience data. All modes use the same pipeline as uploads.
3. **Find clips**, inspect the actual evidence and review status, search/filter every candidate, and use **Preview clip**. Playback stops at the selected end. Native sources also offer **Play full source**.
4. **Edit timing, captions & framing** provides start/end handles, exact seconds, previous/next caption sentence, draft timing preview, title, caption text/times, crop position/resolution and export quality. Arrow keys nudge 0.1 seconds; Shift + arrow moves one second. Saves reject source overflow, excessive duration, overlapping cuts and stale revisions from another tab.
5. **Download clip** creates the saved cut as MP4/M4A, with optional burned captions. Downloads stream directly rather than buffering whole clips in JavaScript. Duplicate requests share a render; edited revisions use separate files and reject stale download links; failed renders can retry.
6. Download clip-relative **SRT/VTT**, the full text shortlist, or **Premiere / Resolve XML** (FCP 7 `xmeml`). XML uses current source in/out frames, linked audio/video, and shortlist rank order. Relink the original file in the editor. Crop/burn-ins apply to rendered MP4s; XML references the original media. Check variable-frame-rate media. XML structure/timing is tested; actual Premiere/Resolve import is not verified here.
7. **Keep / Skip** records optional reasons and timing adjustments in your private workspace. Skipped clips remain reviewable but are excluded from XML. Saved projects shows counts and offers feedback JSON export. These are your editorial decisions, not a virality score. Optional evaluated preference ranking can use eligible real feedback; language-model weights stay unchanged.

To regenerate the sample (optional, espeak-ng required):

```bash
sudo pacman -S --needed espeak-ng
.venv/bin/python scripts/make_sample.py
```

## What the availability panel means

| Analysis | What it actually establishes | Setup |
| --- | --- | --- |
| Spoken content / transcript | Words recognized by Parakeet, or supplied timed captions | `npm run speech:download` if absent; already installed here |
| Meaning & payoff | A separate local model's opinion about complete context, wit and usefulness | `npm run editor:download` and local Ollama |
| Audio energy | A measured volume change relative to surrounding active conversation | Built in |
| Laughter & reactions | Sound-event classifier predictions; separate from caption `[laughter]` | `npm run model:download` |
| Replay markers | Timestamps explicitly supplied by the editor; no history inferred from a file | Optional; no YouTube access required |

The English Parakeet TDT 0.6b v2 int8 speech model is about 661 MB, with pinned revision/checksums and CPU inference through sherpa-onnx. Silence, steady tones and isolated impulses are rejected before loading its weights. It does not identify speakers or translate. Recognition/word timing can be wrong and remains editable. The speech subprocess exits before content review, releasing its model memory.

The optional **CED-tiny int8** sound classifier is a separate ~6.13 MB download. The downloader pins upstream revision `efe3a5c`, verifies SHA-256, checks the real 527-class AudioSet map, and runs a CPU smoke test. Exact labels used include Laughter, Baby laughter, Giggle, Snicker, Belly laugh, `Chuckle, chortle`, Cheering, Applause, Gasp, Music and Speech. Laughter requires a classifier score ≥0.45; other reactions ≥0.55. Scores are not calibrated probabilities. Detection uses overlapping three-second windows and says **near**; timing is approximate. Missing or failed inference is shown as unavailable. Volume alone never becomes confirmed laughter, and caption reactions explicitly say they are not audio-confirmed.

References: [speech model](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8), [official CPU speech instructions](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html), [CED package/class map](https://huggingface.co/k2-fsa/sherpa-onnx-ced-tiny-audio-tagging-2024-04-19), [official CPU event example](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/audio-tagging-from-a-file-ced.py). Attribution is in [MODEL-NOTICES.md](MODEL-NOTICES.md).

## Privacy, storage and hosting

Files live on the machine running the Python worker. This local launch stores them on this laptop; a future hosted worker would store them on that server. Browser-only processing, deployment, billing and subscriptions are not added.

An opaque HttpOnly, SameSite cookie authorizes only its browser workspace's project/media/edit/export/feedback routes. IDs alone do not grant access. Sessions/ownership and job state persist in SQLite; media is outside the public directory. Clearing cookies, using another browser or cookie expiry removes access. No account recovery or cross-device sharing is provided; download work before expiry.

Choose **1, 7 (default), or 30 days** per project. Original media, transcript, checkpoints and renders expire together. Access stops at expiry; a worker sweep removes files within about a minute while running, or on next start. Temporary uploads expire after 24 hours. Deleting one project does not remove another project's shared source. The public sample and older untracked files are preserved. Queued/running jobs restart from saved state and reuse completed chunks; cancellation stops active decoding/model subprocesses. One analysis worker and one render worker limit concurrency and RAM; saved exports do not sit behind a multi-hour analysis.

Optional settings:

```bash
cp .env.example .env.local
# Edit settings, then restart the app.
```

`CUTLIST_DATA` changes storage; `CUTLIST_WORKER_URL` changes the internal same-origin API proxy; `CUTLIST_ORIGINS` sets allowed frontend origins; `CUTLIST_ACCESS_KEY` optionally gates new browser sessions without accounts. For HTTPS hosting, use an explicit HTTPS origin and `CUTLIST_SECURE_COOKIE=true`, keep the worker private and place an HTTPS reverse proxy in front. Launch scripts default to loopback; `CUTLIST_WEB_HOST=0.0.0.0` exposes the frontend for a cloud port proxy while the worker stays on loopback. There is no distributed queue or multi-server coordination.

Speech options: `CUTLIST_ASR_THREADS` (default up to 4), `CUTLIST_ASR_BATCH` (default 2, maximum 4), `CUTLIST_ASR_CHUNK_SECONDS` (default 20, 8–20), `CUTLIST_ASR_MODEL_DIR`. Larger batches/shorter chunks are not automatically faster or more accurate. Content options: `CUTLIST_EDITOR_MODEL` (default qwen3:1.7b), `CUTLIST_OLLAMA_URL` (loopback HTTP only), `CUTLIST_EDITOR_TIMEOUT` (180 seconds per section). Keep models within the laptop memory budget.

## Verification

```bash
npm test
npm run test:quality
npm run test:preview
npm run test:speech
npm run test:editor
# Include optional visual/voice/tracking runtime checks after installation:
CUTLIST_TEST_SPEECH=1 CUTLIST_TEST_EDITOR=1 CUTLIST_TEST_VISION=1 npm test
npm run typecheck
npm run build
```

The Python/API suite uses real FFmpeg and in-process requests: input combinations, sample ± markers, silence/tone/spike rejection, substantive quiet answers, routine-gameplay negatives, all distinct candidates beyond five, source/max bounds and non-overlap, real interval audio alignment, MP4/M4A, caption/crop rendering, XML frame references, byte ranges, versioned downloads, model unavailable states, grounded review contracts and goal plumbing, 15-hour transcript coverage, bounded dense-section overlap, cancelled-chunk resumption, transcript/audio cache reuse, cross-workspace denial, cancel/retry/restart/deletion/expiry, and clip-only conversion for incompatible video. API checks of model response handling use controlled fixtures; they do not establish model judgment quality.

`npm run test:quality` reports temporal precision/recall at IoU ≥0.5 on seven authored regression cases. These English text fixtures are not measured human editorial accuracy. Use `.venv/bin/python scripts/evaluate_quality.py your-labels.json --output report.json` with independent labels for a better dataset. The default authored fixture set covers basic transcript selection; downloaded project benchmark JSON evaluates the actual saved shortlist against independent labels and its basic-detector baseline.

For the ordinary browser flow, keep the app running in another terminal:

```bash
npm run test:browser
# Include genuine sample transcription too:
CUTLIST_BROWSER_ASR=1 npm run test:browser
```

The Playwright test uses `/usr/bin/chromium` when available, otherwise `npx playwright install chromium` once. Override `PLAYWRIGHT_BASE_URL` / `PLAYWRIGHT_CHROMIUM_EXECUTABLE` as needed. It exercises themes, sample ± markers, all input combinations, actual bounded playback, downloads, edit/reload, feedback, SRT/XML, independent quality labels and benchmark download, batch ZIP download, search/paging beyond 20 clips, decode errors, and mobile screenshots without mocking analysis. Output is in `test-results/`.

Implementation-environment results (November 26): the full configured suite runs 92 tests. The latest run passed 87 and skipped five optional model-service checks (CED, text review, still-image vision, visual sequence inference and semantic embeddings). Actual CPU speech, anonymous voice-model execution and OpenCV no-face fallback were exercised. New integration checks verify true early chapter publication through the ordinary API, speech checkpoint reuse, transcript/manual cuts, remove/restore/undo, composed MP4 duration and selected source audio frequencies, retimed subtitles/XML, private search tasks, full-transcript embedding-index/cache contracts, query/frame grounding, held-out preference evaluation, resumable checksum-verified downloads and setup progress/error handling. Four preview checks, seven authored quality fixtures, TypeScript, production build and desktop-entry validation pass.

The local model server cannot be reached from this restricted environment (socket operation not permitted), so actual search/text/vision judgment quality remains unverified. Browser discovery found no connected browser, localhost app binding is blocked, and the ordinary browser test could not run. Desktop launch/install and Premiere/Resolve import remain unverified at runtime. No human editorial accuracy or GPU speedup is claimed. Real 7–15-hour HD media processing was not benchmarked; long transcript/range tests are synthetic. The seven authored quality fixtures and synthetic preference-training tests verify regression behavior, not performance on your recordings.
