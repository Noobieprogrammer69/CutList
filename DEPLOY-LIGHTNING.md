# Lightning AI demo: existing accounts only

> **New-account deployment is blocked.** On November 27, 2026, signup displayed: “We aren't accepting new sign-ups right now. Existing accounts can still sign in.” This was reported during the actual signup attempt; the public pricing documentation did not disclose this restriction. Do not create a new account for this guide. Continue only if you already have a working Lightning account and can open a Studio. No remote deployment has been verified.

Checked against the provider's public documentation on November 27, 2026. This is a CPU demo deployment, not a promise of uninterrupted hosting for multi-hour streams. No cloud account was created and no remote deployment was run here.

Lightning documents one free 4-CPU Studio with no credit card required. Account/email/phone verification and regional availability still apply. Free CPU sessions have a **four-hour session limit**; restart the Studio when required. Only the first **10 GB** of storage is free, across Drive and container images. The 200 GB figure is a storage limit, not a free allowance. Keep only one Studio running, select hardware explicitly marked **Free**, and monitor total storage including models and uploads. Optional GPUs, larger machines, and excess storage consume credits.

Sources: [Studio/account setup](https://lightning.ai/docs/platform/build/ai-studio), [pricing](https://lightning.ai/pricing), [storage billing](https://lightning.ai/docs/platform/overview/faq/billing), [public app links](https://lightning.ai/docs/platform/build/host-web-apps).

All media and project files live on the Lightning workspace in this setup, not on the app owner's laptop. Browser-cookie workspaces remain separate; the shared invitation key does not give visitors access to each other's projects. The cloud workspace operator can access the underlying files. Cutlist's project deletion/expiry still applies. Nothing in this guide sends recordings to a paid model API.

## 1. Open a Studio with an existing account

Sign in with an existing account at <https://lightning.ai/>, then create a Studio from scratch. Name it `cutlist`. Use the default 4-CPU instance marked **Free**; do not select a GPU or a paid Job deployment. If the service refuses new signups, stop here; changing Cutlist's configuration cannot enable account registration.

## 2. Upload the prepared source archive

On your laptop, the archive is `data/cutlist-deploy.zip`. Recreate it after editing source with:

```bash
cd /home/jover/CutList
npm run deploy:package
```

It includes source, the synthetic sample and the hosted example configuration. Local recordings, feedback, databases, downloaded models, dependencies and `.env.local` are excluded. No GitHub repository is required.

Upload this ZIP into the Studio's file explorer, or use Lightning Drive's Upload action and locate the uploaded file in the Studio. Open the Studio terminal. In the directory containing the uploaded ZIP, run:

```bash
unzip cutlist-deploy.zip
cd CutList
```

If `unzip` is missing, install it with `sudo apt-get update` and `sudo apt-get install -y unzip`. Use a persistent Studio directory; the default home is `/teamspace/studios/this_studio`. The exact ZIP upload location depends on where you uploaded it.

## 3. Install system dependencies

These commands run **in the cloud Studio terminal**, not on Omarchy:

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates ffmpeg
curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/cutlist-node-setup.sh
sudo bash /tmp/cutlist-node-setup.sh
sudo apt-get install -y nodejs
node --version
```

Node must be at least 22.18. If a suitable Node is already installed, skip its installation. Install uv if the Studio does not already provide it:

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/cutlist-uv-install.sh
sh /tmp/cutlist-uv-install.sh
export PATH="$HOME/.local/bin:$PATH"
```

Install Ollama using the [official Linux installer](https://docs.ollama.com/linux):

```bash
curl -fsSL https://ollama.com/install.sh -o /tmp/cutlist-ollama-install.sh
sh /tmp/cutlist-ollama-install.sh
```

Studios may not run systemd; start Ollama directly as described below rather than depending on a system service. Keep port 11434 private.

## 4. Install Cutlist

Inside the extracted `CutList` directory:

```bash
uv venv --python 3.12 .venv
uv pip install --no-cache --python .venv/bin/python -r worker/requirements.txt
npm ci
mkdir -p data models
cp .env.hosted.example .env.local
chmod 600 .env.local
```

Open `.env.local` in the Studio editor and replace `CUTLIST_ACCESS_KEY` with a long private passphrase. Visitors enter this shared invitation key, without creating accounts. Leave the origin placeholder until the public URL is available. The example limits each uploaded recording to 1 GB to suit a small demo; this is not a total storage quota. Several smaller uploads can still exhaust the allowance. Choose one-day retention and delete test projects when finished.

## 5. Start the local model server

In one Studio terminal, from the `CutList` directory:

```bash
export OLLAMA_MODELS="$PWD/models/ollama"
export OLLAMA_NO_CLOUD=1
export OLLAMA_HOST=127.0.0.1:11434
ollama serve
```

Leave this terminal running. If port 11434 is already in use, check `curl -fsS http://127.0.0.1:11434/api/tags`; a successful response means an existing Ollama service can be reused. It keeps its own configured model directory.

In a **second Studio terminal**, change into the same `CutList` directory, then download the main models:

```bash
npm run speech:download
npm run editor:download
npm run model:download
```

These provide English transcription, grounded text review and sound-based laughter/reactions. Semantic transcript search is optional: `npm run search:download` adds an approximately 622 MB model. Optional vision adds approximately 1.9 GB and CPU work; leave it off for the initial free demo. Models may make mistakes; this setup does not guarantee funny or useful clips.

## 6. Build and start the app

In the second terminal:

```bash
npm run build
npm start
```

The hosted example exposes the Next.js interface on port 3000. The Python worker remains on loopback port 8000, and Next.js proxies `/api` internally. Expose only port 3000.

## 7. Create the public link and configure its origin

Open Lightning's **Ports / Port viewer** plugin, add port **3000**, and use its public sharing link. See [host web apps](https://lightning.ai/docs/platform/build/host-web-apps) for the current UI. Open the HTTPS link in a new tab rather than inside the Studio preview iframe.

The page can render before the origin is configured; workspace changes will correctly be rejected until you complete this step:

1. Copy the exact public app origin, for example `https://your-generated-app-host`, without a path or trailing slash. Do not use the Studio editor URL.
2. Stop `npm start` with Ctrl+C.
3. In `.env.local`, replace `CUTLIST_ORIGINS` with that exact origin.
4. Keep `CUTLIST_SECURE_COOKIE=true`, `CUTLIST_ENABLE_SETUP=false`, and `CUTLIST_WEB_HOST=0.0.0.0`.
5. Run `npm start` again. No frontend rebuild is required for the worker's origin setting.

The local launcher still defaults to loopback on your laptop. The public hostname setting affects only the web interface; neither the worker nor the model server is publicly bound.

## 8. Verify the actual workflow

Open the public URL, enter the invitation key, and:

1. Load the sample, disable illustrative replay markers, and run Find clips.
2. Preview a cut; check that playback stops at its end.
3. Download a clip and the text shortlist.
4. Upload a short MP4 without captions to check real transcription.
5. Upload matching SRT/VTT and check that supplied captions are used.
6. Check `/api/health` at the same public hostname for model/FFmpeg availability.

If changes return 403, compare the actual browser origin with `CUTLIST_ORIGINS` and restart the worker. If sessions do not persist, use the direct public HTTPS URL in a new tab. If the app is unreachable, check the Studio is active, both terminal commands are running, and port 3000 is public. A 1 GB demo upload limit is deliberate; it does not reduce export resolution.

## Restarts and limits

The free four-hour session limit can interrupt an analysis, upload, preview preparation or export. After restarting the Studio, start Ollama and `npm start` again from the same persistent directory. Queued/running analyses recover from saved parameters and reuse completed checkpoints; unfinished uploads or exports can need a retry. Do not repeatedly start parallel copies of the worker or rely on automatic uninterrupted processing across every provider restart.

For a future manual restart, repeat only the commands in steps 5 and 6 (`npm start`, without rebuilding unchanged source). The Studio's on-start action can launch those same processes once the manual flow has been verified; provider session limits still apply.

This option is intended for the playable sample and small cloud demos. There is no benchmark of Cutlist on Lightning, no measured cloud speedup, and no promise that 7–15-hour HD streams will finish within one free session or fit the included storage. Running the distributed local app on each editor's own device remains the option for processing without shared cloud compute/storage quotas.
