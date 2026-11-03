import "./style.css";
import { api, type Config, initApi, type Job } from "./api";
import { applyLang, renderShell, t } from "./i18n";
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
    $("#main").innerHTML = `<div class="page"><div class="empty"><h3>${t("本地服务没有启动", "The local service didn't start")}</h3><div>${String(e)}</div></div></div>`;
    return;
  }
  // 界面语言存在配置里；启动时先用上次记住的语言渲染，拿到配置后不一致再改
  try {
    applyLang(((await api<Config>("/api/config")).ui_language ?? "auto") as "auto");
  } catch {
    /* 拿不到配置就按系统语言 */
  }
  renderShell();
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
