/* 轮询本地服务的任务状态，驱动首页进度、详情页流式纪要和侧边栏的状态胶囊。 */

import { api, type Job } from "./api";
import { $, fmt, toast } from "./dom";
import { refreshEnv, refreshMeetings } from "./sidebar";
import { isBusy, S, stateLabel } from "./state";
import { updateDetailJob } from "./views/detail";
import { updateHome } from "./views/home";
import { updateSettingsJob } from "./views/settings";

let timer: number | undefined;

export async function poll(): Promise<void> {
  window.clearTimeout(timer);
  let job: Job;
  try {
    job = await api<Job>("/api/job");
  } catch {
    job = S.job;
  }
  const prev = S.job;
  S.job = job;

  const pill = $<HTMLAnchorElement>("#livePill");
  pill.hidden = !isBusy(job);
  pill.classList.toggle("busy", job.state !== "recording");
  if (isBusy(job)) {
    $("#livePillText").textContent = job.state === "recording" ? `录音中 ${fmt((job.elapsed ?? 0) * 1000)}` : stateLabel(job.state);
    pill.href = job.kind === "summarize" && job.meeting ? `#/m/${encodeURIComponent(job.meeting)}` : "#/";
  }

  const onHome = !location.hash || location.hash === "#/";
  if (onHome) updateHome(job, prev);
  if (S.detail && job.meeting === S.detail.id) updateDetailJob(job, prev);
  updateSettingsJob(job, prev);

  if (prev.state !== job.state && (job.state === "done" || job.state === "error")) {
    refreshMeetings();
    refreshEnv();
    if (job.state === "done" && !onHome && (job.kind === "process" || job.kind === "record")) toast("纪要已生成");
    if (job.state === "done" && job.notice) toast(job.notice, job.notice.startsWith("没能"));
  }
  timer = window.setTimeout(poll, job.state === "recording" ? 160 : isBusy(job) ? 500 : 2500);
}
