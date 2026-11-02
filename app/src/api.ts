/* 和本地服务通信。浏览器 / `jiyao ui` 里是同源；Tauri 窗口里服务地址由外壳告诉我们。 */

export interface Env {
  version: string;
  root: string;
  ffmpeg: boolean;
  system_audio: boolean;
  model_cached: boolean;
  model_loaded: boolean;
  llm_ready: boolean;
  llm_model: string;
}

export interface MeetingSummary {
  id: string;
  title: string;
  date: string;
  time: string;
  summary: string;
  has_minutes: boolean;
  has_transcript: boolean;
  duration: string;
  speakers: string[];
}

export interface Turn {
  speaker: string;
  color: number;
  start_ms: number;
  ts: string;
  text: string;
}

export interface SpeakerInfo {
  index: number;
  name: string;
  custom: boolean;
}

export interface MeetingDetail extends MeetingSummary {
  minutes: string;
  turns: Turn[];
  speaker_list: SpeakerInfo[];
  audio: boolean;
  path: string;
}

export type JobState =
  | "idle"
  | "recording"
  | "saving"
  | "finalizing"
  | "loading"
  | "transcribing"
  | "summarizing"
  | "done"
  | "error";

export interface Job {
  state: JobState;
  kind?: "record" | "process" | "summarize";
  meeting?: string;
  error?: string;
  minutes?: string;
  stage?: string;
  sources?: string[];
  elapsed?: number;
  model_loaded?: boolean;
  levels?: Record<string, number>;
  live?: { start_ms: number; text: string }[];
}

export interface Config {
  base_url: string;
  model: string;
  api_key_set: boolean;
  api_key_hint: string;
  hotwords: string[];
  webhook: string;
  secret_set: boolean;
  path: string;
}

export interface LocalProvider {
  id: string;
  name: string;
  base_url: string;
  api_key: string;
  recommended: string;
  hint: string;
  running: boolean;
  models: string[];
}

let base = "";

/** Tauri 窗口里向外壳要本地服务地址；外壳会等服务起来再返回。 */
export async function initApi(): Promise<void> {
  if ("__TAURI_INTERNALS__" in window) {
    const { invoke } = await import("@tauri-apps/api/core");
    base = await invoke<string>("backend_url");
  }
}

export const url = (path: string): string => base + path;

interface ApiOptions extends RequestInit {
  json?: unknown;
}

export async function api<T = Record<string, unknown>>(path: string, opts: ApiOptions = {}): Promise<T> {
  const { json, ...init } = opts;
  if (json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(json);
  }
  const res = await fetch(url(path), init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（${res.status}）`);
  return data as T;
}

export const meetingPath = (id: string, sub = ""): string => `/api/meetings/${encodeURIComponent(id)}${sub}`;
