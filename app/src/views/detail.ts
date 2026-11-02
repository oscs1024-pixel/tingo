/* 会议详情：纪要 / 转写两个视图、回听播放器、说话人改名、编辑纪要、重新生成。 */

import { api, type Job, type MeetingDetail, meetingPath, url } from "../api";
import { $, $$, $maybe, closeModal, color, errorMessage, esc, fmt, ICON, initial, main, openModal, toast, tsToMs } from "../dom";
import { fishboneToText, turnResolver } from "../fishbone";
import { renderMarkdown } from "../markdown";
import { poll } from "../poll";
import { refreshMeetings } from "../sidebar";
import { isBusy, S } from "../state";

let audioEl: HTMLAudioElement | null = null;
let detailTab: "minutes" | "transcript" = "minutes";

export function stopAudio(): void {
  if (audioEl) {
    audioEl.pause();
    audioEl.src = "";
    audioEl = null;
  }
}

const backLink = `<a class="back" href="#/meetings">${ICON.back}会议记录</a>`;

export async function renderDetail(id: string, keepTab = false): Promise<void> {
  if (!keepTab) detailTab = "minutes";
  main().innerHTML = `<div class="page">${backLink}</div>`;
  let d: MeetingDetail;
  try {
    d = await api<MeetingDetail>(meetingPath(id));
  } catch (e) {
    main().innerHTML = `<div class="page">${backLink}<div class="empty"><h3>打不开这场会议</h3><div>${esc(errorMessage(e))}</div></div></div>`;
    return;
  }
  if (decodeURIComponent(location.hash.slice(1)) !== `/m/${id}`) return; // 加载期间已经切走
  S.detail = d;
  const jobHere = isBusy(S.job) && S.job.meeting === id;
  main().innerHTML = `
    <div class="page">
      ${backLink}
      <div class="detail-head">
        <h1>${esc(d.title)}</h1>
        <div class="detail-actions">
          ${d.minutes ? `<button class="btn" id="aCopy">${ICON.copy}复制纪要</button><button class="btn" id="aPush">${ICON.send}推送到群</button><button class="btn" id="aObsidian" title="存进 Obsidian 库并打开（在设置里选库）">${ICON.vault}Obsidian</button>` : ""}
          ${d.has_transcript ? `<button class="btn" id="aRegen">${ICON.redo}${d.minutes ? "重新生成" : "生成纪要"}</button>` : ""}
          <button class="btn ghost" id="aFolder" title="打开文件夹">${ICON.folder}</button>
        </div>
      </div>
      <div class="meta">
        <span>${esc(d.date)} ${esc(d.time)}</span>
        ${d.duration ? `<span class="sep"></span><span>时长 ${esc(d.duration)}</span>` : ""}
        ${
          d.speaker_list.length
            ? `<span class="sep"></span>
          <span class="speakers">${d.speaker_list
            .map((s) => `<span class="chip"><span class="dot" style="background:${color(s.index)}"></span>${esc(s.name)}</span>`)
            .join("")}
          <button class="btn" id="aRename">${ICON.people}改名</button></span>`
            : ""
        }
      </div>
      ${
        d.has_transcript
          ? `
        <div class="tabs-row">
          <div class="seg"><button data-tab="minutes">纪要</button><button data-tab="transcript">转写</button></div>
          <span class="grow"></span>
          <button class="btn ghost" id="aEdit" ${detailTab === "minutes" && d.minutes ? "" : "hidden"}>${ICON.edit}编辑</button>
        </div>
        <div class="card" id="pane"></div>`
          : `
        <div class="card"><div class="empty"><h3>${jobHere ? "正在处理…" : "录音还没有转写"}</h3><div>${jobHere ? "进度在首页可以看到" : "可能是上次处理被打断了"}</div>
          ${jobHere ? `<a class="btn" href="#/">查看进度</a>` : d.audio ? `<button class="btn primary" id="aProcess">开始转写</button>` : ""}</div></div>`
      }
      ${d.audio ? playerHTML : ""}
    </div>`;

  $$<HTMLButtonElement>(".seg button").forEach(
    (b) =>
      (b.onclick = () => {
        detailTab = b.dataset.tab as typeof detailTab;
        drawPane();
      }),
  );
  const on = (sel: string, fn: () => void) => {
    const el = $maybe(sel);
    if (el) el.onclick = fn;
  };
  on("#aCopy", () => copyText(fishboneToText(d.minutes)));
  on("#aPush", pushMinutes);
  on("#aObsidian", toObsidian);
  on("#aRegen", regenerate);
  on("#aRename", renameSpeakers);
  on("#aEdit", editMinutes);
  on("#aFolder", () => api(meetingPath(id, "/reveal"), { method: "POST" }));
  on("#aProcess", async () => {
    try {
      S.job = await api<Job>(meetingPath(id, "/process"), { method: "POST" });
      location.hash = "#/";
      poll();
    } catch (e) {
      toast(errorMessage(e), true);
    }
  });
  if (d.audio) bindPlayer(id);
  if (d.has_transcript) drawPane();
  if (jobHere) updateDetailJob(S.job, { state: "idle" });
}

function drawPane(): void {
  const d = S.detail;
  const pane = $maybe("#pane");
  if (!d || !pane) return;
  $$<HTMLButtonElement>(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.tab === detailTab));
  const edit = $maybe("#aEdit");
  if (edit) edit.hidden = !(detailTab === "minutes" && d.minutes);
  if (detailTab === "minutes") {
    pane.className = "card doc";
    pane.innerHTML = d.minutes
      ? `<div class="md">${renderMarkdown(d.minutes, { skipHead: true, resolve: turnResolver(d.turns, d.duration ? tsToMs(d.duration) : Infinity) })}</div>`
      : `<div class="minutes-empty">还没有纪要。<br><br><button class="btn primary" id="paneGen">${ICON.spark}生成纪要</button></div>`;
    const gen = $maybe("#paneGen");
    if (gen) gen.onclick = regenerate;
  } else {
    pane.className = "card turns";
    pane.innerHTML = d.turns
      .map(
        (t, i) => `
      <div class="turn" data-i="${i}" data-ms="${t.start_ms}">
        <span class="av" style="background:${color(t.color)}">${esc(initial(t.speaker))}</span>
        <div><div class="who">${esc(t.speaker)}<button class="ts" data-ms="${t.start_ms}">${esc(t.ts)}</button></div><div class="text">${esc(t.text)}</div></div>
      </div>`,
      )
      .join("");
  }
  $$<HTMLButtonElement>(".ts", pane).forEach(
    (b) =>
      (b.onclick = (e) => {
        e.stopPropagation(); // 鱼骨图分支里的时间戳：只跳转，不收起分支
        seek(Number(b.dataset.ms));
      }),
  );
  $$<HTMLButtonElement>(".fb-draw", pane).forEach((b) => (b.onclick = toExcalidraw));
  // 鱼骨图：点分支展开它引用的原话
  $$<HTMLElement>(".fb-branch[role=button]", pane).forEach((br) => {
    const toggle = () => {
      const q = $maybe(".fb-quotes", br);
      if (q) {
        q.hidden = !q.hidden;
        br.classList.toggle("open", !q.hidden);
      }
    };
    br.onclick = toggle;
    br.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggle();
      }
    };
  });
}

export function updateDetailJob(job: Job, prev: Job): void {
  if (job.kind !== "summarize" || !S.detail) return;
  const pane = $maybe("#pane");
  if (isBusy(job) && pane) {
    detailTab = "minutes";
    $$<HTMLButtonElement>(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.tab === "minutes"));
    pane.className = "card doc";
    if (!$maybe("#genBox")) {
      pane.innerHTML = `<div id="genBox"><div class="live-head" style="color:var(--blue);margin-bottom:18px"><span class="live-dot" style="background:var(--cyan)"></span><span id="genTitle">正在生成纪要</span></div><div class="md" id="genStream"></div></div>`;
    }
    $("#genTitle").textContent = job.stage
      ? `正在生成纪要 · ${job.stage}`
      : `正在用 ${S.env ? S.env.llm_model : "大模型"} 生成纪要`;
    const gs = $("#genStream");
    const minutes = job.minutes ?? "";
    if (gs.dataset.len !== String(minutes.length)) {
      gs.dataset.len = String(minutes.length);
      gs.innerHTML = minutes ? renderMarkdown(minutes) : `<p style="color:var(--muted)">正在阅读转写…</p>`;
    }
  } else if (prev.state !== job.state && (job.state === "done" || job.state === "error")) {
    if (job.state === "done") toast("纪要已更新");
    else toast(job.error ?? "生成失败", true);
    api("/api/job/dismiss", { method: "POST" });
    renderDetail(S.detail.id, true);
  }
}

async function regenerate(): Promise<void> {
  if (!S.detail) return;
  try {
    S.job = await api<Job>(meetingPath(S.detail.id, "/summarize"), { method: "POST" });
    updateDetailJob(S.job, { state: "idle" });
    poll();
  } catch (e) {
    toast(errorMessage(e), true);
  }
}

async function pushMinutes(): Promise<void> {
  if (!S.detail) return;
  try {
    toast((await api<{ message: string }>(meetingPath(S.detail.id, "/push"), { method: "POST" })).message);
  } catch (e) {
    toast(errorMessage(e), true);
  }
}

async function toObsidian(): Promise<void> {
  if (!S.detail) return;
  try {
    await api(meetingPath(S.detail.id, "/obsidian"), { json: { open: true } });
    toast("已存到 Obsidian");
  } catch (e) {
    toast(errorMessage(e), true);
  }
}

async function toExcalidraw(): Promise<void> {
  if (!S.detail) return;
  try {
    await api(meetingPath(S.detail.id, "/excalidraw"), { method: "POST" });
    toast("已导出鱼骨图.excalidraw，拖进 excalidraw.com 即可编辑");
  } catch (e) {
    toast(errorMessage(e), true);
  }
}

async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast("已复制纪要 Markdown");
}

function editMinutes(): void {
  const d = S.detail;
  if (!d) return;
  const pane = $("#pane");
  $("#aEdit").hidden = true;
  pane.className = "card doc";
  pane.innerHTML = `<textarea class="textarea" id="mdEdit" style="min-height:520px;font-size:14px">${esc(d.minutes)}</textarea>
    <div class="modal-foot"><button class="btn" id="mdCancel">取消</button><button class="btn primary" id="mdSave">保存</button></div>`;
  $("#mdCancel").onclick = drawPane;
  $("#mdSave").onclick = async () => {
    const text = $<HTMLTextAreaElement>("#mdEdit").value;
    try {
      await api(meetingPath(d.id, "/minutes"), { method: "PUT", json: { text } });
      toast("已保存");
      renderDetail(d.id, true);
      refreshMeetings();
    } catch (e) {
      toast(errorMessage(e), true);
    }
  };
}

function renameSpeakers(): void {
  const d = S.detail;
  if (!d) return;
  openModal(`
    <h3>给说话人改名</h3>
    <p class="hint">按第一次发言的顺序排列。点转写里的时间戳可以回听确认是谁。</p>
    ${d.speaker_list
      .map(
        (s) =>
          `<div class="sp-row"><span class="av" style="background:${color(s.index)}">${esc(initial(s.name))}</span><input class="input" data-i="${s.index}" value="${s.custom ? esc(s.name) : ""}" placeholder="${esc(`说话人${s.index + 1}`)}"></div>`,
      )
      .join("")}
    <div class="modal-foot"><button class="btn" data-close>取消</button><button class="btn" id="spSave">仅保存</button>${d.minutes ? `<button class="btn primary" id="spRegen">保存并重新生成纪要</button>` : ""}</div>`);
  const save = async (regen: boolean) => {
    const names = $$<HTMLInputElement>("#modalBody input").map((x) => x.value.trim());
    try {
      S.detail = await api<MeetingDetail>(meetingPath(d.id, "/speakers"), { method: "PUT", json: { names } });
      closeModal();
      await renderDetail(d.id, true);
      refreshMeetings();
      if (regen) regenerate();
      else toast("已更新说话人");
    } catch (e) {
      toast(errorMessage(e), true);
    }
  };
  $("#spSave").onclick = () => save(false);
  const regen = $maybe("#spRegen");
  if (regen) regen.onclick = () => save(true);
}

/* ---------- 播放器 ---------- */

const playerHTML = `
  <div class="player" id="player">
    <button class="play" id="pPlay" title="播放（空格）">${ICON.play}</button>
    <span class="p-time" id="pTime">00:00 / --:--</span>
    <div class="track" id="pTrack"><div class="rail"></div><div class="fill" id="pFill"></div><div class="knob" id="pKnob"></div></div>
    <button class="speed" id="pSpeed">1×</button>
    <span></span>
  </div>`;

function bindPlayer(id: string): void {
  stopAudio();
  const audio = new Audio(url(meetingPath(id, "/audio")));
  audioEl = audio;
  audio.preload = "metadata";
  const speeds = [1, 1.25, 1.5, 2];
  let si = 0;
  const draw = () => {
    if (audioEl !== audio) return;
    const dur = audio.duration || 0;
    const t = audio.currentTime;
    const pct = dur ? (t / dur) * 100 : 0;
    $("#pFill").style.width = pct + "%";
    $("#pKnob").style.left = pct + "%";
    $("#pTime").textContent = `${fmt(t * 1000)} / ${dur ? fmt(dur * 1000) : "--:--"}`;
    $("#pPlay").innerHTML = audio.paused ? ICON.play : ICON.pause;
    highlightTurn(t * 1000);
  };
  audio.ontimeupdate = draw;
  audio.onloadedmetadata = draw;
  audio.onplay = draw;
  audio.onpause = draw;
  $("#pPlay").onclick = () => (audio.paused ? audio.play() : audio.pause());
  const speed = $("#pSpeed");
  speed.onclick = () => {
    si = (si + 1) % speeds.length;
    audio.playbackRate = speeds[si];
    speed.textContent = speeds[si] + "×";
  };
  $("#pTrack").onclick = (e) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    if (audio.duration) audio.currentTime = ((e.clientX - r.left) / r.width) * audio.duration;
  };
}

function seek(ms: number): void {
  if (!audioEl) return toast("这场会议没有录音文件", true);
  audioEl.currentTime = ms / 1000;
  audioEl.play();
}

let lastHi = -1;
function highlightTurn(ms: number): void {
  const turns = $$(".turn");
  if (!turns.length) return;
  let idx = -1;
  for (const t of turns) {
    if (Number(t.dataset.ms) <= ms + 200) idx = Number(t.dataset.i);
    else break;
  }
  if (idx === lastHi) return;
  lastHi = idx;
  turns.forEach((t) => t.classList.toggle("playing", Number(t.dataset.i) === idx && !!audioEl && !audioEl.paused));
}

export function setupPlayerKeys(): void {
  document.addEventListener("keydown", (e) => {
    const tag = (document.activeElement as HTMLElement | null)?.tagName ?? "";
    if (e.code !== "Space" || !audioEl || /INPUT|TEXTAREA/.test(tag)) return;
    e.preventDefault();
    if (audioEl.paused) audioEl.play();
    else audioEl.pause();
  });
}
