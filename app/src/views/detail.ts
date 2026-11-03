/* 会议详情：纪要 / 转写两个视图、回听播放器、说话人改名、编辑纪要、重新生成。 */

import { api, type Job, type MeetingDetail, meetingPath, url } from "../api";
import { $, $$, $maybe, closeModal, color, errorMessage, esc, fmt, ICON, initial, main, openModal, toast, tsToMs } from "../dom";
import { fishboneToText, turnResolver } from "../fishbone";
import { renderMarkdown } from "../markdown";
import { minutesDocument } from "../pdf";
import { poll } from "../poll";
import { refreshMeetings } from "../sidebar";
import { isBusy, jobOf, minutesProgress, progressOf, S, SECTIONS } from "../state";
import { dismissJob } from "../tasks";

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
  const jobHere = jobOf(id);
  // 打开会议就算看过结果了：收起这场会议已结束的任务
  S.jobs.filter((j) => j.meeting === id && j.kind !== "models" && !isBusy(j) && j.id).forEach((j) => dismissJob(j.id!));
  main().innerHTML = `
    <div class="page">
      ${backLink}
      <div class="detail-head">
        <h1>${esc(d.title)}</h1>
        <div class="detail-actions">
          ${
            d.minutes
              ? `<div class="menu-wrap"><button class="btn" id="aExport">${ICON.download}导出${ICON.chevron}</button>
                  <div class="menu" id="exportMenu" hidden>
                    <button data-act="pdf">${ICON.file}<span><b>导出 PDF</b><small>A4 排版，存进会议文件夹</small></span></button>
                    <button data-act="obsidian">${ICON.vault}<span><b>存到 Obsidian</b><small>存进笔记库并打开</small></span></button>
                    <button data-act="copy">${ICON.copy}<span><b>复制为 Markdown</b><small>粘贴到文档、邮件里</small></span></button>
                  </div></div><button class="btn" id="aPush">${ICON.send}推送到群</button>`
              : ""
          }
          ${d.has_transcript && !jobHere ? `<button class="btn" id="aRegen">${ICON.redo}${d.minutes ? "重新生成" : "生成纪要"}</button>` : ""}
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
        ${
          jobHere
            ? `<div class="card doc" id="pane"></div>`
            : `<div class="card"><div class="empty"><h3>录音还没有转写</h3><div>可能是上次处理被打断了</div>
          ${d.audio ? `<button class="btn primary" id="aProcess">开始转写</button>` : ""}</div></div>`
        }`
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
  on("#aPush", pushMinutes);
  bindExportMenu(d);
  on("#aRegen", regenerate);
  on("#aRename", renameSpeakers);
  on("#aEdit", editMinutes);
  on("#aFolder", () => api(meetingPath(id, "/reveal"), { method: "POST" }));
  on("#aProcess", async () => {
    try {
      const job = await api<Job>(meetingPath(id, "/process"), { method: "POST" });
      S.jobs = [...S.jobs.filter((j) => j.id !== job.id), job];
      renderDetail(id, true);
      poll();
    } catch (e) {
      toast(errorMessage(e), true);
    }
  });
  if (d.audio) bindPlayer(id);
  if (d.has_transcript) drawPane();
  if (jobHere) updateDetailJob(jobHere, { state: "idle" });
}

function drawPane(): void {
  const d = S.detail;
  const pane = $maybe("#pane");
  if (!d || !pane) return;
  $$<HTMLButtonElement>(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.tab === detailTab));
  const edit = $maybe("#aEdit");
  if (edit) edit.hidden = !(detailTab === "minutes" && d.minutes) || !!jobOf(d.id); // 生成中改了也会被新纪要覆盖
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

/* 生成过程：转写 → 生成纪要（按模板一节一节写），实时显示写到哪、写了多少、用了多久，纪要边写边显示。 */
const genSkeleton = () => `
  <div class="gen" id="genBox">
    <div class="gen-head"><span class="live-dot"></span><b id="genTitle"></b><span class="grow"></span><span class="gen-meta" id="genMeta"></span></div>
    <div class="gen-bar"><i id="genBar"></i></div>
    <div class="gen-steps" id="genSteps"></div>
    <div class="md gen-stream" id="genStream"></div>
  </div>`;

function stepChip(label: string, st: "done" | "now" | "todo"): string {
  return `<span class="gen-step ${st}">${st === "done" ? "✓" : st === "now" ? "<i></i>" : ""}${esc(label)}</span>`;
}

function drawGen(job: Job): void {
  const summarizing = job.state === "summarizing";
  const mp = minutesProgress(job);
  const llm = S.env?.llm_model ?? "大模型";
  $("#genTitle").textContent = summarizing
    ? mp.chunk
      ? `会议较长，先分段提炼（${mp.chunk[0]}/${mp.chunk[1]}）`
      : mp.merging
        ? "把各段要点合并成纪要"
        : mp.section < 0
          ? job.thinking
            ? `${llm} 正在思考`
            : `${llm} 正在读转写`
          : `${llm} 正在写纪要`
    : job.state === "transcribing"
      ? `正在本地转写${job.stage ? ` · ${job.stage}` : ""}`
      : job.state === "finalizing"
        ? "转写最后一段，区分说话人"
        : job.state === "loading"
          ? "加载识别模型"
          : "正在保存录音";
  const pct = Math.round(progressOf(job) * 100);
  const elapsed = fmt((job.elapsed ?? 0) * 1000);
  const wrote = summarizing && mp.chars ? ` · 已写 ${mp.chars} 字` : summarizing && job.thinking ? ` · 已思考 ${job.thinking} 字` : "";
  $("#genMeta").textContent = `用时 ${elapsed}${wrote} · 约 ${pct}%`;
  $("#genBar").style.width = `${Math.max(2, pct)}%`;

  // 步骤：转写 → 纪要的各节
  const transcribed = summarizing;
  const steps = [
    stepChip("转写", transcribed ? "done" : "now"),
    stepChip("通读与思考", !summarizing ? "todo" : mp.section >= 0 ? "done" : "now"),
  ];
  SECTIONS.forEach((t, i) => {
    const st = !summarizing || mp.chunk ? "todo" : i < mp.section ? "done" : i === mp.section ? "now" : "todo";
    steps.push(stepChip(t, st));
  });
  const html = steps.join(`<span class="gen-arrow"></span>`);
  const box = $("#genSteps");
  if (box.dataset.html !== html) {
    box.dataset.html = html;
    box.innerHTML = html;
  }

  const gs = $("#genStream");
  const minutes = summarizing && !mp.chunk ? (job.minutes ?? "") : "";
  const key = `${job.state}:${minutes.length}:${minutes ? 0 : (job.thinking ?? 0)}`;
  if (gs.dataset.key !== key) {
    gs.dataset.key = key;
    gs.innerHTML = minutes
      ? renderMarkdown(minutes) + `<span class="gen-caret"></span>`
      : `<p class="gen-wait">${
          summarizing
            ? mp.chunk
              ? "长会议分成几段，每段先提炼要点，最后合并成一份纪要。"
              : job.thinking
                ? `大模型在动笔前先思考（已思考 ${job.thinking} 字），长会议这一步可能要一两分钟，开始写后内容会一节一节出现在这里。`
                : "大模型先通读整场转写，开始动笔后内容会一节一节出现在这里。"
            : "识别在这台电脑上进行，录音不会上传。转写完成后接着生成纪要，不用守着，可以去开下一场会。"
        }</p>`;
    const near = main().scrollHeight - main().scrollTop - main().clientHeight < 160;
    if (near && minutes) main().scrollTop = main().scrollHeight; // 停在底部时跟着往下滚
  }
}

export function updateDetailJob(job: Job, prev: Job): void {
  if (!S.detail || job.meeting !== S.detail.id) return;
  if (isBusy(job)) {
    const pane = $maybe("#pane");
    if (!pane) return;
    detailTab = "minutes";
    $$<HTMLButtonElement>(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.tab === "minutes"));
    pane.className = "card doc";
    if (!$maybe("#genBox")) pane.innerHTML = genSkeleton();
    const edit = $maybe("#aEdit");
    if (edit) edit.hidden = true;
    drawGen(job);
  } else if (isBusy(prev) && (job.state === "done" || job.state === "error")) {
    if (job.state === "done") toast("纪要已生成");
    else toast(job.error || "生成失败", true);
    if (job.id) dismissJob(job.id);
    renderDetail(S.detail.id, true);
  }
}

async function regenerate(): Promise<void> {
  if (!S.detail) return;
  try {
    const job = await api<Job>(meetingPath(S.detail.id, "/summarize"), { method: "POST" });
    S.jobs = [...S.jobs.filter((j) => j.id !== job.id), job];
    updateDetailJob(job, { state: "idle" });
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

function bindExportMenu(d: MeetingDetail): void {
  const btn = $maybe("#aExport");
  const menu = $maybe("#exportMenu");
  if (!btn || !menu) return;
  const close = () => {
    menu.hidden = true;
    document.removeEventListener("mousedown", outside);
  };
  const outside = (e: MouseEvent) => {
    if (!menu.contains(e.target as Node) && !btn.contains(e.target as Node)) close();
  };
  btn.onclick = () => {
    menu.hidden = !menu.hidden;
    if (!menu.hidden) document.addEventListener("mousedown", outside);
  };
  $$<HTMLButtonElement>("button", menu).forEach(
    (b) =>
      (b.onclick = () => {
        close();
        if (b.dataset.act === "pdf") toPdf(d);
        else if (b.dataset.act === "obsidian") toObsidian();
        else copyText(fishboneToText(d.minutes));
      }),
  );
}

async function toPdf(d: MeetingDetail): Promise<void> {
  toast("正在排版 PDF…");
  try {
    const r = await api<{ path: string }>(meetingPath(d.id, "/pdf"), { json: { html: minutesDocument(d) } });
    toast(`已导出 ${r.path.split("/").pop()}，已在访达中选中`);
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
