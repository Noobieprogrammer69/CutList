# Using Cutlist

Start with the [installation and first-clip workflow](../README.md#get-started).

## Understand a suggestion

Every suggestion includes source start/end times, a reason, and the evidence available for that recording. A transcript badge identifies supplied or automatically generated captions. Model-reviewed dialogue can include quoted setup/payoff lines and the final editorial check. Audio-only results are labelled separately.

Open **Analysis available for this recording** to check what ran:

| Signal | What it means |
| --- | --- |
| Spoken content | Supplied captions or automatically recognized words |
| Meaning & payoff | A local model's review of context and the proposed exchange |
| Audio energy | A measured change relative to the surrounding conversation |
| Laughter & reactions | Predictions from the optional sound-event model |
| Visual context | Model predictions from short, sampled frame sequences |
| Replay markers | Timestamps explicitly supplied by the editor |

Loud audio alone is not confirmed laughter. Caption text such as `[laughter]` is a separate signal from sound-based detection. Visual observations cover sampled frames rather than everything on screen. Review the evidence and watch the cut before deciding to use it.

There is no fixed number of results. Search and filters cover the entire shortlist, even though cards are paginated. Sort by rank or source order to review them your way.

## Search and make your own cuts

In a completed project, open **Search the recording & create your own cuts**. With Semantic search installed, describe what you want to find, such as “the guest explaining a mistake they learned from.” Search covers the full spoken transcript, including passages outside the automatic shortlist. First-time indexing can take time; later queries reuse the index.

Search matches represent similarity to your request. When the editorial model is available, it also checks the brief and exclusions. Otherwise, results say similarity only. Search does not cover silent visual events.

Use transcript cue times, word selection, or **Start here / End here** to select an interval and create a manual cut. Supplied captions use cue boundaries; automatic word timings are estimates and may need adjustment.

## Refine a clip

Open **Edit timing, captions & framing** to change the title, start/end, caption text and timings, crop, and export quality. Preview the draft before saving. Timing controls support arrow-key nudges of 0.1 seconds, or one second with Shift.

**Remove pauses or mistakes from this cut** lets you remove a passage or sentence, or tighten caption gaps with padding. Pauses can carry meaning, so trimming them is optional. Preview and export use the same retained portions. Edits do not modify the original file.

Your selected maximum applies to the retained output duration. Retained source portions cannot overlap another shortlisted cut. A cut can contain up to 16 retained portions. Draft edits can be undone; saved clip edits retain ten undo steps. Removed shortlist cuts can be restored.

Choose original framing, vertical, or square output. Manual framing is available without extra models. Optional face following requires the Face-following dependency and a detectable face; it does not identify the active speaker. Optional anonymous voice labels use numbers within that clip, rather than identifying a person. Review labels before exporting.

## Export

| Export | Use it for |
| --- | --- |
| MP4 | A rendered video cut, with optional framing and burned-in captions |
| M4A | A rendered audio cut |
| Batch ZIP | Multiple saved clips with SRT, VTT, and an export manifest |
| SRT / WebVTT | Clip-relative subtitles for another editor or publishing tool |
| Text shortlist | A portable list of suggested moments and their reasons |
| Premiere / Resolve XML | A timeline referencing the original recording |

**Download clip** prepares the saved revision and starts the download when ready. If a format cannot play directly in the browser, preparing its clip preview converts just that interval. Save draft timing first for these sources.

Original framing preserves source dimensions. Vertical/square framing can retain the original crop pixels or resize to the selected output width. High quality uses H.264 CRF 18; Maximum uses CRF 14 and larger files. These are re-encoded exports, not lossless copies.

Batch export supports up to 100 saved cuts per batch. Existing finished renders can be reused. A batch is a snapshot of the saved revisions; create another batch to include later edits.

XML is FCP 7 `xmeml`. Relink the original recording in your editor. Framing and burned-in captions apply to rendered MP4s; the XML references source media. Check timing with variable-frame-rate recordings. XML structure is covered by tests, but import into Premiere/Resolve has not been verified in the recorded test environment.

## Keep, skip, and personalize

Use **Keep / Skip** to record your decisions and optional reasons. Skipped clips remain available for review but are excluded from XML exports.

**Learn from my Keep / Skip decisions** is optional. It needs at least 20 reviewed clips across three real recordings, including at least five kept and five skipped clips. Sample and manual cuts do not count. Cutlist checks performance on held-out recordings before enabling a small ranking adjustment; it may decide there is not enough evidence to change ranking. This does not retrain the downloaded language model.

For a more detailed evaluation, **Real-footage quality benchmark** lets you label worthwhile and unhelpful source intervals, then compare the shortlist with those labels. A partial set of labels is not a complete accuracy measurement. Export feedback and benchmark results from the workspace when you need a copy.

## Long recordings and saved projects

Choose a chapter size of 1, 5, 10, or 15 minutes for recordings that need transcription. Completed chapters can produce early results while later chapters continue. Overlap around chapter boundaries helps preserve context, but chapter processing can add work; it is not a throughput guarantee.

Native browser formats can preview early results directly. Incompatible media needs its prepared clip previews after analysis. Final ranking and clip IDs can change until the project finishes.

Use **Saved projects** to reopen, retry, cancel, or delete a project. **Re-analyze saved recording · reuse transcript** lets you try new goals or clip lengths without another upload and speech pass. Choose **Transcribe this recording again** when you need to replace an inaccurate transcript.

Uploads use resumable chunks. After a reload or interruption, reselect the same source file to resume transfer. Completed transcription and editorial checkpoints can be reused after a worker restart. Interrupted exports can need a retry.

Project expiry deletes its original media, transcript, checkpoints, and renders together. Cleanup runs while the worker is running, or on its next start. Temporary incomplete uploads expire after 24 hours. Keep using the same browser profile to retain access, and download your work before expiry.

## Troubleshooting

| Problem | What to try |
| --- | --- |
| Worker unavailable | Start Cutlist from the project folder with `npm start`, then keep that terminal open. |
| Model unavailable | Open Setup, install the component, and refresh availability. Ollama must be running for editorial review, semantic search, or visual review. |
| No suggestions | Check transcript accuracy and available evidence; try a different goal or keyword, or search the transcript and create a manual cut. |
| Slow analysis | Leave visual review off initially, use captions when available, and reuse saved transcripts for subsequent attempts. |
| Cannot decode the file | Try a different export from your recording software; an extension alone does not guarantee a supported codec. |
| Not enough storage | Delete unwanted projects and leave space for temporary audio, models, and rendered clips. |
| Preview cannot play the source | Use Preview clip to prepare a browser-compatible interval once analysis finishes. |
| Old results after an update | Close the running Cutlist process, rebuild with `npm run build`, and start it again. |

The [development guide](DEVELOPMENT.md) covers configuration. The [testing notes](TESTING.md) describe what has and has not been verified.
