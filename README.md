# Cutlist

Find moments worth editing in your podcasts, interviews, and streams.

Cutlist helps freelance video editors turn long recordings into a shortlist of short-form video candidates. Add a recording, find clips, review the evidence, refine your cuts, and download the results.

Run it on your own Linux computer with a browser interface and an optional desktop shortcut. No Cutlist account or paid API is required.

## What you can do

- **Find distinct moments:** combine spoken context, audio changes, reaction predictions, and optional replay markers. There is no five-clip cap.
- **Review before cutting:** see exact start/end times, supporting evidence, transcript excerpts, and a preview of each suggestion.
- **Search your recording:** find passages by meaning and create cuts directly from the transcript.
- **Refine your edit:** adjust boundaries, edit captions, remove unwanted passages, and choose original, vertical, or square framing.
- **Export your work:** download individual MP4/M4A clips, batch ZIPs, subtitles, a text shortlist, or an XML timeline for further editing.
- **Pick up where you left off:** reopen projects, resume uploads, and reuse saved transcripts when trying different clip settings.
- **Choose your workspace:** switch between light and dark modes and optionally use Keep / Skip feedback to personalize ranking.

## Get started

Cutlist is currently installed from source on **Linux**. It is designed around local CPU processing on an 8 GB RAM laptop; processing time depends on your recording, enabled models, and hardware.

### Requirements

| Component | Requirement |
| --- | --- |
| Node.js | 22.18 or newer, with npm |
| Python | 3.12; uv can download it during setup |
| uv | Required for the Python environment and desktop launcher |
| FFmpeg | Include FFprobe, H.264/AAC encoding, and subtitle support |
| Ollama | Needed for editorial review, semantic search, and optional visual review |
| Free disk space | Allow space for your recordings, downloaded models, temporary analysis files, and exports |

On Arch Linux / Omarchy, install the system tools with:

```bash
sudo pacman -S --needed nodejs npm uv ffmpeg ollama
```

On other Linux distributions, install those tools using your distribution's package manager or their official installers.

### Install and launch

Download or clone this repository, then open a terminal in the project folder and run:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r worker/requirements.txt
npm ci
npm run build
npm start
```

Open **http://127.0.0.1:3000** in your browser. Keep the terminal running while using Cutlist; press **Ctrl+C** to stop it.

Initial installation and model downloads need internet access. Once the required models are installed, recordings can be processed locally without a paid service.

For later launches, run `npm start`. You can also use `./cutlist` to launch from the project folder, or add Cutlist to your Linux application menu:

```bash
npm run desktop:install
```

### Enable the analysis you need

Open **Setup & model downloads** in Cutlist. If Ollama is installed but not running, click **Start local model service**, then install the components you want:

| Component | What it adds | Approximate download |
| --- | --- | --- |
| English transcription | Creates a transcript when you have no captions | 661 MB |
| Editorial review | Reviews dialogue for an understandable setup and payoff | 1.4 GB |
| Sound-based laughter and reactions | Predicts laughter, applause, cheering, and gasps from audio | 7 MB |
| Semantic search | Finds transcript passages related to a natural-language query | 622 MB |
| Visual sequences | Adds observations from sampled video frames | 1.9 GB |

For spoken-content review, install **Editorial review**, plus **English transcription** if you do not have captions. Visual review is optional and adds processing time. On a computer with limited memory, start with transcription and editorial review.

Without downloaded models, supplied captions and built-in audio-energy analysis remain available. The availability panel shows which signals were used; basic candidates are not labelled as reviewed for meaning or humor. See [model notices](MODEL-NOTICES.md) for model names and terms.

## Find your first clips

1. **Add a recording**, or click **Load sample** to try the included conversation and matching captions.
2. **Add SRT or WebVTT captions** if you have them. Otherwise, enable automatic transcription after installing its model.
3. **Set your maximum clip length.** Optionally add keywords, choose a profile such as Interview or Gaming, and select the kind of moments you want.
4. **Click Find clips.** Watch progress and review suggestions as completed sections become available. Early results can change while analysis continues; editing and export become available when it finishes.
5. **Check each suggestion.** Read its explanation and evidence, then click **Preview clip** to play the selected interval.
6. **Refine and download.** Adjust timing, captions, or framing, save your changes, and click **Download clip**. Use **Batch export clips** to download several cuts together.

The sample uses the same analysis pipeline as your recordings. Its conversation uses synthesized voices. Optional sample replay markers are illustrative, not real audience behavior; leave them off to see what analysis finds without them.

You can also analyze captions without media to create a text shortlist. Matching media is needed to preview and download video or audio clips.

For transcript editing, search, export options, and feedback, see the [user guide](docs/USER-GUIDE.md).

## Your recordings and privacy

When you run Cutlist locally, your recordings, transcripts, and exports are stored on your computer. Model processing uses the local worker and model service. If you use a separately hosted instance, files are stored on that instance's server, where its operator can access them.

Choose automatic deletion after **1, 7, or 30 days**, or delete a project from **Saved projects**. Download anything you want to keep before it expires. Your browser cookie provides access to saved projects; clearing cookies or switching browsers removes that access, and there is no account recovery.

## What to expect

- **Suggestions need your judgment.** Models can miss context, make transcription errors, or pick weak moments. A valid result can contain no clips.
- **Long recordings take time.** Chapter processing shows results progressively, and cached transcripts reduce repeat work. It does not make a first analysis instant.
- **Source quality matters.** Exports use the original recording. Enlarging a low-resolution source cannot recover HD detail.
- **English is the current automatic transcription language.** Other supplied captions can be parsed, but selection quality depends on language and model understanding.
- **Replay markers are optional input.** A local file contains no viewer replay history. Cutlist does not promise YouTube retention access or invent replay scores.

The default limits are **16 hours per recording**, **64 GB per uploaded file**, and a **5–180 second maximum clip length**. Available disk space and processing time still apply. Caption files can be up to 5 MB / 50,000 cues.

## More documentation

- [User guide](docs/USER-GUIDE.md): search, editing, exports, troubleshooting, and saved projects.
- [Development guide](docs/DEVELOPMENT.md): configuration, architecture, and packaging.
- [Testing and benchmarks](docs/TESTING.md): checks, recorded results, and verification limits.
- [Model notices](MODEL-NOTICES.md): model sources, attribution, and terms.
