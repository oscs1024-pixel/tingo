import type { Env, Job, JobState, MeetingDetail, MeetingSummary } from "./api";

export const S: {
  env: Env | null;
  meetings: MeetingSummary[];
  /** 首页玻璃面板跟随的任务：正在录音的那场，或者首次下载模型；没有就是 idle */
  job: Job;
  /** 所有任务：录音之外，转写、生成纪要、下载模型都在后台跑，可以同时有好几个 */
  jobs: Job[];
  form: { title: string; system: boolean; mic: boolean; hotwords: string };
  detail: MeetingDetail | null; // 当前打开的会议
} = {
  env: null,
  meetings: [],
  job: { state: "idle" },
  jobs: [],
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

/** 这场会议正在进行的任务 */
export const jobOf = (meeting: string): Job | undefined => S.jobs.find((j) => j.meeting === meeting && j.kind !== "models" && isBusy(j));

/** 任务标题：会议名（去掉日期时间前缀），或者下载的模型 */
export function jobTitle(j: Job): string {
  if (j.kind === "models") return `下载识别模型${j.meeting && j.meeting !== "paraformer" ? ` · ${j.meeting}` : ""}`;
  return (j.meeting ?? "").replace(/^\d{4}-\d{2}-\d{2}_\d{4}_/, "") || "会议";
}

/* ---------- 纪要生成进度 ----------
   大模型按模板依次写这几节，写到哪一节就能估出大概进度；"原因分析"只有讨论了原因才有，不算在内。 */
export const SECTIONS = ["一句话总结", "议题与讨论", "决议", "待办", "待定问题与风险"];

export interface MinutesProgress {
  /** 0～1 */
  value: number;
  /** 当前在写第几节（-1 表示还没开始写，在读转写） */
  section: number;
  chars: number;
  /** 分段提炼的第几段 / 共几段（长会议） */
  chunk?: [number, number];
  merging?: boolean;
}

export function minutesProgress(j: Job): MinutesProgress {
  const text = j.minutes ?? "";
  let section = -1;
  for (const m of text.matchAll(/^##\s+(.+)$/gm)) {
    const i = SECTIONS.findIndex((t) => m[1].startsWith(t));
    if (i > section) section = i;
  }
  const chars = [...text.replace(/\s/g, "")].length;
  // 动笔前（读转写、思考）看不出进度，按用时缓慢增长到 22%；动笔后按写到第几节算
  const reading = 0.02 + 0.2 * (1 - Math.exp(-(j.elapsed ?? 0) / 60));
  const write = section < 0 ? reading : Math.min(0.97, 0.22 + (0.75 * (section + 0.5)) / SECTIONS.length);
  const chunk = /分段提炼\s*(\d+)\/(\d+)/.exec(j.stage ?? "");
  if (chunk) {
    const [i, n] = [Number(chunk[1]), Number(chunk[2])];
    return { value: (0.65 * (i - 1)) / n + 0.02, section: -1, chars: 0, chunk: [i, n] };
  }
  if ((j.stage ?? "").includes("合并")) return { value: 0.65 + 0.35 * write, section, chars, merging: true };
  return { value: write, section, chars };
}

/** 任务整体进度 0～1：转写按百分比，生成纪要按写到第几节，下载按字节 */
export function progressOf(j: Job): number {
  if (j.state === "done") return 1;
  const pct = (s?: string) => {
    const m = /([\d.]+)%/.exec(s ?? "");
    return m ? Number(m[1]) / 100 : 0;
  };
  if (j.kind === "models") {
    const m = /([\d.]+)\s*\/\s*([\d.]+)/.exec(j.stage ?? "");
    return m ? Number(m[1]) / Number(m[2]) : 0;
  }
  if (j.kind === "summarize") return minutesProgress(j).value;
  // 录音结束后、导入的录音：转写占前一半，纪要占后一半（边录边转的话转写早就做完了，只剩最后一段）
  const live = j.kind === "record";
  switch (j.state) {
    case "saving":
      return 0.03;
    case "finalizing":
      return live ? 0.15 : 0.05;
    case "loading":
      return 0.06;
    case "transcribing":
      return 0.08 + 0.42 * pct(j.stage);
    case "summarizing":
      return (live ? 0.3 : 0.5) + (live ? 0.7 : 0.5) * minutesProgress(j).value;
    default:
      return 0;
  }
}

/** 一句话说清任务现在在干什么 */
export function jobDetail(j: Job): string {
  if (j.state === "error") return j.error || "没有完成";
  if (j.state === "done") return j.kind === "models" ? "下载完成" : "纪要已生成";
  if (j.kind === "models") return j.stage || "准备下载…";
  if (j.state === "transcribing") return `正在转写${j.stage ? ` ${j.stage}` : ""}`;
  if (j.state === "summarizing") {
    const p = minutesProgress(j);
    if (p.chunk) return `长会议分段提炼 ${p.chunk[0]}/${p.chunk[1]}`;
    if (p.section < 0) return p.merging ? "合并成纪要…" : j.thinking ? `大模型在思考 · ${j.thinking} 字` : "大模型在读转写…";
    return `在写「${SECTIONS[p.section]}」· ${p.chars} 字`;
  }
  return stateLabel(j.state);
}
