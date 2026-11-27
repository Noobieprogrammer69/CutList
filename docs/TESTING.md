# Testing and benchmarks

Run commands from the repository root after installing project dependencies.

## Automated checks

```bash
npm test
npm run test:preview
npm run test:quality
npm run typecheck
npm run build
```

The Python/API suite uses real FFmpeg and in-process requests. Coverage includes captions-only, audio-only and combined input; sample with/without markers; silence, steady tones and spikes; source bounds, maximum duration and non-overlap; actual rendered interval audio/duration; captions, crops and timeline structure; byte ranges and versioned exports; private workspaces; retry, cancellation, restart, deletion and expiry; progressive chapters and cached speech; model-unavailable and grounding behavior; semantic-index contracts; held-out feedback evaluation; and setup/download recovery.

Fixture responses test model integration and validation. They do not establish the judgment quality of a real language or vision model.

## Optional model checks

Download the relevant models and start Ollama before running:

```bash
npm run test:speech
npm run test:editor
CUTLIST_TEST_SPEECH=1 CUTLIST_TEST_EDITOR=1 CUTLIST_TEST_VISION=1 CUTLIST_TEST_SEARCH=1 npm test
```

Model-dependent checks can skip when the required component or service is unavailable. Inspect the reported skips rather than treating them as exercised features.

## Browser workflow

Keep the ordinary app running in another terminal, then run:

```bash
npm run test:browser
```

To include actual sample transcription:

```bash
CUTLIST_BROWSER_ASR=1 npm run test:browser
```

The Playwright runner uses `/usr/bin/chromium` when available. Otherwise install Chromium with `npx playwright install chromium`. Override `PLAYWRIGHT_BASE_URL` and `PLAYWRIGHT_CHROMIUM_EXECUTABLE` when needed. Artifacts are written to `test-results/`.

The scripted workflow covers themes, sample/input combinations, bounded playback, downloads, clip edits and reload, feedback, subtitle/XML exports, independent quality labels, batch ZIPs, search/paging, decode errors, and mobile screenshots. It is authored coverage, not evidence that the complete workflow has been run in every environment.

## Quality and timing measurements

`npm run test:quality` evaluates seven authored English regression cases using temporal precision/recall at IoU ≥ 0.5. These [fixtures](../benchmarks/clip_quality.json) are not measured human editorial accuracy.

Label source intervals in the app's Real-footage quality benchmark, export its JSON, and evaluate it with:

```bash
npm run test:quality -- /path/to/cutlist-real-footage-benchmark.json
```

For reproducible timings:

```bash
npm run benchmark
npm run benchmark -- --skip-speech
npm run benchmark:hardware -- --captions /path/to/stream.vtt --output /tmp/cutlist-hardware.json
npm run benchmark:hardware -- --captions /path/to/stream.vtt --media /path/to/stream.mp4
```

The [recorded local benchmark](../benchmarks/local-performance.json) used the original 115-second synthetic sample. Speech took approximately 12 seconds and 1.6 GB peak subprocess memory; checkpoint reuse took approximately 0.01 seconds. A synthetic 15-hour transcript/ranking case exercised 10,800 cues and 900 events. Those numbers do not measure 15-hour media decoding, model review, actual stream quality, or a general speedup on other hardware.

The hardware benchmark compares real text-model calls on identical captions and reports unavailable devices/models explicitly. It does not infer speedup from hardware detection alone.

To regenerate the sample, install `espeak-ng`, then run `.venv/bin/python scripts/make_sample.py`. The synthetic conversation has matching SRT/VTT and no recorded human laughter; illustrative replay markers are not audience data.

## Previous recorded validation: November 26, 2026

The configured suite ran 92 tests: 87 passed and five optional model-service checks were skipped (CED, text review, still-image vision, visual-sequence inference, and semantic embeddings). Actual CPU speech recognition, anonymous voice embeddings, and the OpenCV no-face fallback were exercised. Four preview checks, seven authored quality fixtures, TypeScript, a production build, and desktop-entry validation passed.

The implementation environment blocked local socket binding and access to a local model server, and had no connected browser. The ordinary browser workflow, desktop launch/install, real search/text/vision judgment, remote cloud deployment, and actual Premiere/Resolve imports were not verified there. No GPU speedup or human editorial accuracy was measured. Real 7–15-hour HD media processing was not benchmarked; long-duration transcript/range tests used synthetic data.

These are historical results, not a fresh test run on every subsequent documentation edit. Review the limitations when reporting coverage.
