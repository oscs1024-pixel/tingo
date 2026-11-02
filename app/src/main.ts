import "./style.css";
import { api, initApi, type Job } from "./api";
import { $, setupModal } from "./dom";
import { poll } from "./poll";
import { route } from "./router";
import { refreshEnv, refreshMeetings } from "./sidebar";
import { S } from "./state";
import { setupPlayerKeys } from "./views/detail";
import { setupImport } from "./views/home";

async function boot(): Promise<void> {
  try {
    await initApi();
  } catch (e) {
    $("#main").innerHTML = `<div class="page"><div class="empty"><h3>本地服务没有启动</h3><div>${String(e)}</div></div></div>`;
    return;
  }
  setupModal();
  setupImport();
  setupPlayerKeys();
  window.addEventListener("hashchange", route);
  await Promise.all([refreshEnv(), refreshMeetings()]);
  try {
    S.job = await api<Job>("/api/job");
  } catch {
    /* 服务刚起来 */
  }
  route();
  poll();
}

boot();
