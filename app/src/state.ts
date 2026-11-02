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

const BUSY = new Set<JobState>(["recording", "saving", "finalizing", "loading", "transcribing", "summarizing"]);
export const isBusy = (j: Job | undefined): boolean => !!j && BUSY.has(j.state);

const LABELS: Partial<Record<JobState, string>> = {
  saving: "正在保存",
  finalizing: "转写最后一段",
  loading: "加载识别模型",
  transcribing: "正在转写",
  summarizing: "正在生成纪要",
};
export const stateLabel = (s: JobState): string => LABELS[s] || "处理中";
