/* 检查更新（桌面版）：GitHub 上有新版本时侧栏版本号旁边亮起提示，点开看更新说明，
   一键下载安装包、校验 SHA-256 后在访达里打开。Python 版没有这个接口，什么都不显示。 */

import { api, type Job } from "./api";
import { closeModal, errorMessage, esc, openModal, toast } from "./dom";
import { t } from "./i18n";
import { renderMarkdown } from "./markdown";
import { S } from "./state";

export interface UpdateInfo {
  current: string;
  latest?: string;
  newer: boolean;
  url?: string;
  notes?: string;
  dmg?: string;
  size_mb?: number;
  sha256?: string | null;
  error?: string;
}

let info: UpdateInfo | null = null;
let checked = 0;

/** 6 小时最多查一次（后端也有缓存） */
export async function checkUpdate(force = false): Promise<UpdateInfo | null> {
  if (!force && info && Date.now() - checked < 6 * 3600_000) return info;
  try {
    info = await api<UpdateInfo>("/api/update");
    checked = Date.now();
  } catch {
    info = null; // 旧版后端或 Python 版没有这个接口
  }
  return info;
}

export const updateInfo = (): UpdateInfo | null => info;

export function showUpdate(): void {
  const u = info;
  if (!u?.newer) return;
  openModal(`
    <h3>${t(`Tingo ${u.latest} 可以更新了`, `Tingo ${u.latest} is available`)}</h3>
    <p class="hint">${t(`当前版本 ${u.current}`, `You have ${u.current}`)}${u.size_mb ? ` · ${u.size_mb} MB` : ""}</p>
    <div class="md update-notes">${renderMarkdown(u.notes ?? "")}</div>
    <p class="hint">${t(
      `会下载到「下载」文件夹${u.sha256 ? "，并核对 SHA-256" : ""}，然后打开安装包，把 Tingo 拖进「应用程序」替换旧版即可。配置、会议和识别模型都会保留。`,
      `It downloads to your Downloads folder${u.sha256 ? ", checks the SHA-256" : ""} and opens the installer — drag Tingo into Applications to replace the old version. Settings, meetings and models are kept.`,
    )}</p>
    <div class="modal-foot"><a class="btn" href="${esc(u.url ?? "#")}" target="_blank" rel="noopener">${t("在 GitHub 查看", "View on GitHub")}</a><span class="grow"></span>
      <button class="btn" data-close>${t("以后再说", "Later")}</button><button class="btn primary" id="upGo">${t("下载并打开", "Download & open")}</button></div>`);
  const go = document.getElementById("upGo") as HTMLButtonElement;
  go.onclick = async () => {
    go.disabled = true;
    try {
      const job = await api<Job>("/api/update/download", { method: "POST" });
      S.jobs = [...S.jobs.filter((j) => j.id !== job.id), job];
      closeModal();
      toast(t("正在下载新版，进度在左侧「进行中」", "Downloading the update — see In progress on the left"));
    } catch (e) {
      toast(errorMessage(e), true);
      go.disabled = false;
    }
  };
}
