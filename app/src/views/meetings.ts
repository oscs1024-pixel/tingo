import type { MeetingSummary } from "../api";
import { isEn, t } from "../i18n";
import { $, $maybe, color, esc, ICON, initial, main } from "../dom";
import { refreshMeetings } from "../sidebar";
import { S } from "../state";

export function renderMeetings(): void {
  main().innerHTML = `
    <div class="page">
      <div class="page-head">
        <div><h1>${t("会议<em>.</em>记录", "Meetings<em>.</em>")}</h1><div class="sub" id="mCount"></div></div>
        <span class="grow"></span>
        <label class="search">${ICON.search}<input class="input" id="mSearch" placeholder="${t("搜索标题或内容", "Search titles and content")}"></label>
      </div>
      <div id="mList"></div>
    </div>`;
  const search = $<HTMLInputElement>("#mSearch");
  search.oninput = () => drawMeetings(search.value.trim());
  refreshMeetings().then(() => drawMeetings(search.value.trim()));
  drawMeetings("");
}

function drawMeetings(q: string): void {
  const box = $maybe("#mList");
  if (!box) return;
  const items = S.meetings.filter((m) => !q || (m.title + m.summary + m.speakers.join("")).includes(q));
  const root = S.env ? S.env.root.replace(/^\/Users\/[^/]+/, "~") : "~/Documents/tingo";
  $("#mCount").textContent = S.meetings.length ? t(`共 ${S.meetings.length} 场，存放在 ${root}`, `${S.meetings.length} meetings, stored in ${root}`) : "";
  if (!S.meetings.length) {
    box.innerHTML = `<div class="empty"><h3>${t("还没有会议", "No meetings yet")}</h3><div>${t("开一场会，或者把已有的录音拖进窗口", "Start a meeting, or drop an existing recording into the window")}</div><a class="btn primary" href="#/">${t("开始录音", "Start recording")}</a></div>`;
    return;
  }
  if (!items.length) {
    box.innerHTML = `<div class="empty"><h3>${t("没有找到", "Nothing found")}</h3><div>${t("换个关键词试试", "Try another keyword")}</div></div>`;
    return;
  }
  const groups: Record<string, MeetingSummary[]> = {};
  for (const m of items) (groups[m.date.slice(0, 7)] ||= []).push(m);
  box.innerHTML = Object.entries(groups)
    .map(
      ([ym, ms]) => `
    <div class="month">${isEn() ? new Date(Number(ym.slice(0, 4)), Number(ym.slice(5)) - 1, 1).toLocaleDateString("en-US", { year: "numeric", month: "long" }) : `${ym.slice(0, 4)} 年 ${Number(ym.slice(5))} 月`}</div>
    <div class="grid">${ms.map(card).join("")}</div>`,
    )
    .join("");
}

const card = (m: MeetingSummary, i: number) => `
  <a class="card m-card" href="#/m/${encodeURIComponent(m.id)}" style="animation-delay:${Math.min(i, 8) * 30}ms">
    <div class="when"><span>${isEn() ? esc(new Date(`${m.date}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" })) : `${esc(m.date.slice(5).replace("-", " 月 "))} 日`} ${esc(m.time)}</span><span>${esc(m.duration)}</span></div>
    <h3>${esc(m.title)}</h3>
    <p>${esc(m.summary || (m.has_transcript ? t("已转写，还没有生成纪要", "Transcribed — no minutes yet") : t("录音还没有处理", "Recording not processed yet")))}</p>
    <div class="foot">
      <span class="avatars">${m.speakers
        .slice(0, 4)
        .map((s, k) => `<span style="background:${color(k)}">${esc(initial(s))}</span>`)
        .join("")}</span>
      ${m.speakers.length ? `<span>${t(`${m.speakers.length} 位说话人`, `${m.speakers.length} speaker${m.speakers.length > 1 ? "s" : ""}`)}</span>` : ""}
      <span class="status ${m.has_minutes ? "ok" : m.has_transcript ? "half" : ""}">${m.has_minutes ? t("已出纪要", "Minutes") : m.has_transcript ? t("仅转写", "Transcript only") : t("未处理", "Not processed")}</span>
    </div>
  </a>`;
