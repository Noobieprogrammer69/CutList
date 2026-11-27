export type Cue = { start: number; end: number; text: string; speaker?: string; speaker_source?: string };
export type CaptionStyle = { size: number; color: string; outline: number; position: "top" | "bottom"; show_speakers: boolean; speaker_colors: boolean };
export type Segment = { start: number; end: number };
export type Crop = { format: "source" | "vertical" | "square"; x: number; y: number; resolution?: "source" | "720" | "1080"; tracking?: "manual" | "face" };
export type Clip = { segments?: Segment[]; output_duration?: number; rank?: number; base_score?: number; preference_adjustment?: number; id: number; start: number; end: number; anchor: number; score: number; title: string; evidence: string[]; transcript_used: boolean; excerpt: string; reason: string; boundary_note: string; revision: number; captions: Cue[]; crop: Crop; burn_captions: boolean; feedback: string; feedback_reason?: string; caption_source?: string; selection_method?: string; category?: string; setup_quote?: string; payoff_quote?: string; quality?: "high" | "maximum"; caption_style?: CaptionStyle; editorial_review?: { status: string; reason: string }; visual_context?: { time: number; description: string; source: string }[] };
export type Result = { id: string; source_name: string; duration: number; clips: Clip[]; media_url: string | null; media_kind: string; capabilities: Record<string, string>; warnings: string[]; waveform: number[]; sample: boolean; replays: number[]; maximum: number; caption_count: number; cues: Cue[]; caption_source?: string; expires: number; media_info: { width?: number; height?: number }; preview_mode?: string; selection_method?: string; timings?: { total_seconds: number; transcription_reused?: boolean; first_results_seconds?: number | null }; provisional?: boolean; profile?: string; coverage?: { sections_complete: number; sections_total: number; through_seconds: number; chaptered?: boolean; selection_method?: string }; personalization?: { applied?: boolean; message: string }; goal?: string };
export type Job = { id: string; status: string; progress: number; message: string; source_name?: string; expires?: number; error?: string; processed_seconds?: number; total_seconds?: number; eta_seconds?: number | null; stage?: string; partial_result?: Result; result?: Result };

export async function readResponse(response: Response) {
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "The request failed. Check your settings and try again.");
  return body;
}
