import { t } from "./i18n";
import { api, type Env, type MeetingSummary } from "./api";
import { $, esc } from "./dom";
import { S } from "./state";

export async function refreshMeetings(): Promise<void> {
  try {
    S.meetings = await api<MeetingSummary[]>("/api/meetings");
  } catch {
    S.meetings = [];
  }
  const h = decodeURIComponent(location.hash.slice(1));
  $("#recentList").innerHTML = S.meetings.length
    ? S.meetings
        .slice(0, 8)
        .map(
          (m) => `
        <a href="#/m/${encodeURIComponent(m.id)}" data-id="${esc(m.id)}" class="${h === "/m/" + m.id ? "active" : ""}">
          <div class="r-title">${esc(m.title)}</div>
          <div class="r-date">${esc(m.date.slice(5).replace("-", "/"))} ${esc(m.time)}${m.duration ? " · " + esc(m.duration) : ""}</div>
        </a>`,
        )
        .join("")
    : `<div class="r-empty">${t("还没有会议", "No meetings yet")}</div>`;
}

export async function refreshEnv(): Promise<void> {
  try {
    S.env = await api<Env>("/api/env");
  } catch {
    return;
  }
  const e = S.env;
  const rows: [string, string][] = [
    [
      e.model_loaded || e.model_cached ? "" : "warn",
      e.model_loaded
        ? t("识别模型已加载", "Speech model loaded")
        : e.model_cached
          ? t("识别模型已就绪", "Speech model ready")
          : e.models_downloadable
            ? t("识别模型未下载", "Speech model not downloaded")
            : t("首次转写会下载模型", "Model downloads on first use"),
    ],
    [e.llm_ready ? "" : "bad", e.llm_ready ? e.llm_model : t("未配置大模型", "No LLM configured")],
    [e.system_audio ? "" : "warn", e.system_audio ? t("可录系统声音", "System audio available") : t("仅能录麦克风", "Microphone only")],
  ];
  $("#envBox").innerHTML =
    rows.map(([c, t]) => `<div class="env-row"><span class="env-dot ${c}"></span><span>${esc(t)}</span></div>`).join("") +
    `<div class="env-row" style="color:var(--muted-2)">v${esc(e.version)}</div>`;
}
