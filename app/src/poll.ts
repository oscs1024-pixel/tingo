/* 轮询本地服务的任务列表，驱动首页（录音 / 首次下载模型）、详情页的生成过程、设置页的模型下载和侧边栏的任务列表。 */

import { api, type Job } from "./api";
import { toast } from "./dom";
import { refreshEnv, refreshMeetings } from "./sidebar";
import { isBusy, jobTitle, needsModels, S } from "./state";
import { renderTasks } from "./tasks";
import { updateDetailJob } from "./views/detail";
import { updateHome } from "./views/home";
import { updateSettingsJob } from "./views/settings";

let timer: number | undefined;
const IDLE: Job = { state: "idle" };

async function fetchJobs(): Promise<Job[]> {
  try {
    return (await api<{ jobs: Job[] }>("/api/jobs")).jobs;
  } catch {
    // 旧版后端只有一个任务
    const j = await api<Job>("/api/job");
    return j.state === "idle" ? [] : [{ ...j, id: j.id ?? "single" }];
  }
}

export async function poll(): Promise<void> {
  window.clearTimeout(timer);
  let jobs: Job[];
  try {
    jobs = await fetchJobs();
  } catch {
    jobs = S.jobs;
  }
  const before = new Map(S.jobs.map((j) => [j.id, j]));
  S.jobs = jobs;

  // 首页玻璃面板只跟随录音，以及第一次使用时的模型下载
  const prevFocus = S.job;
  S.job =
    jobs.find((j) => j.state === "recording") ??
    jobs.find((j) => j.kind === "models" && (isBusy(j) || j.state === "error") && needsModels()) ??
    IDLE;
  const onHome = !location.hash || location.hash === "#/";
  if (onHome) updateHome(S.job, prevFocus);
  renderTasks();

  let changed = false;
  for (const j of jobs) {
    const prev = before.get(j.id) ?? IDLE;
    if (S.detail && j.meeting === S.detail.id && j.kind !== "models") updateDetailJob(j, prev);
    if (j.kind === "models") updateSettingsJob(j, prev);
    if (prev.state === j.state || !(j.state === "done" || j.state === "error") || !isBusy(prev)) continue;
    changed = true;
    const here = S.detail?.id === j.meeting;
    if (j.kind !== "models" && !here) {
      if (j.state === "done") toast(`「${jobTitle(j)}」的纪要已生成`);
      else toast(`「${jobTitle(j)}」没有处理完：${j.error ?? ""}`, true);
    }
    if (j.state === "done" && j.notice) toast(j.notice, j.notice.startsWith("没能"));
  }
  if (changed) {
    refreshMeetings();
    refreshEnv();
  }
  const rec = jobs.some((j) => j.state === "recording");
  timer = window.setTimeout(poll, rec ? 160 : jobs.some(isBusy) ? 500 : 2500);
}
