import type { Env, Job, JobState, MeetingDetail, MeetingSummary } from "./api";

export const S: {
  env: Env | null;
  meetings: MeetingSummary[];
  job: Job;
  form: { title: string; system: boolean; mic: boolean; hotwords: string };
  detail: MeetingDetail | null; // 当前打开的会议
} = {
  env: null,
  meetings: [],
  job: { state: "idle" },
  form: { title: "", system: true, mic: true, hotwords: "" },
  detail: null,
};

const BUSY = new Set<JobState>(["recording", "saving", "finalizing", "loading", "transcribing", "summarizing", "downloading"]);
export const isBusy = (j: Job | undefined): boolean => !!j && BUSY.has(j.state);

const LABELS: Partial<Record<JobState, string>> = {
  saving: "正在保存",
  finalizing: "转写最后一段",
  loading: "加载识别模型",
  transcribing: "正在转写",
  summarizing: "正在生成纪要",
  downloading: "下载识别模型",
};
/** 桌面版还没下载识别模型（Python 版会在首次转写时自动下载，不用这个判断）。 */
export const needsModels = (): boolean => !!S.env?.models_downloadable && !S.env.model_cached;

export const stateLabel = (s: JobState): string => LABELS[s] || "处理中";
