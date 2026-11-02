/* 首页：录音表单 → 录音中（计时、电平、实时文字）→ 处理进度 → 完成 / 出错。另含拖拽导入。 */

import { api, type Job } from "../api";
import { $, $$, $maybe, errorMessage, esc, fmt, ICON, main, toast } from "../dom";
import { renderMarkdown } from "../markdown";
import { poll } from "../poll";
import { isBusy, S, stateLabel } from "../state";

type Phase = "idle" | "recording" | "processing" | "done" | "error" | "__init";

const INIT: Job = { state: "idle" };
const phaseOf = (j: Job, init = false): Phase =>
  init
    ? "__init"
    : j.state === "recording"
      ? "recording"
      : isBusy(j)
        ? "processing"
        : j.state === "done" || j.state === "error"
          ? j.state
          : "idle";

export function renderHome(): void {
  main().innerHTML = `
    <div class="page">
      <div class="hero-head">
        <span class="hero-tag"><b></b>本地转写 · 录音不出本机</span>
        <h1>开个会，纪要交给我</h1>
        <p>录下会议，自动转写、区分说话人，整理出议题、决议和待办</p>
      </div>
      <section class="aurora" id="aurora">
        <i class="blob b1"></i><i class="blob b2"></i><i class="blob b3"></i><i class="blob b4"></i><i class="grain"></i>
        <div class="glass-ring"><div class="glass" id="glass"></div></div>
      </section>
      <div class="quick">
        <div class="quick-item" id="qImport"><span class="ico">${ICON.upload}</span><span><b>导入录音</b><small>拖进窗口，或点这里选文件</small></span>${ICON.chevron}</div>
        <a class="quick-item" href="#/meetings"><span class="ico">${ICON.file}</span><span><b>会议记录</b><small id="qCount">查看全部会议</small></span>${ICON.chevron}</a>
        <a class="quick-item" href="#/settings"><span class="ico">${ICON.bot}</span><span><b>大模型</b><small id="qModel">设置纪要用的模型</small></span>${ICON.chevron}</a>
        <a class="quick-item" href="#/settings"><span class="ico">${ICON.send}</span><span><b>推送到群</b><small>飞书 · 钉钉 · 企业微信</small></span>${ICON.chevron}</a>
      </div>
    </div>`;
  $("#qImport").onclick = () => $("#fileInput").click();
  if (S.meetings.length) $("#qCount").textContent = `共 ${S.meetings.length} 场会议`;
  if (S.env) $("#qModel").textContent = S.env.llm_ready ? `当前：${S.env.llm_model}` : "还没有配置";
  updateHome(S.job, INIT, true);
}

export function updateHome(job: Job, prev: Job, forceRender = false): void {
  const glass = $maybe("#glass");
  if (!glass) return;
  const aurora = $("#aurora");
  const phase = phaseOf(job);
  aurora.classList.toggle("live", phase === "recording");

  if (forceRender || phase !== phaseOf(prev) || glass.dataset.phase !== phase) {
    glass.dataset.phase = phase;
    glass.innerHTML = VIEWS[phase as Exclude<Phase, "__init">](job);
    bindHome(phase);
  }
  if (phase === "recording") updateRecording(job, aurora);
  if (phase === "processing") updateProcessing(job);
}

function updateRecording(job: Job, aurora: HTMLElement): void {
  $("#timer").textContent = fmt((job.elapsed ?? 0) * 1000);
  let peak = 0;
  for (const [k, v] of Object.entries(job.levels ?? {})) {
    peak = Math.max(peak, v);
    const bars = $$(`#meter-${k} i`);
    const on = Math.round(v * bars.length);
    bars.forEach((b, i) => {
      b.classList.toggle("on", i < on);
      b.classList.toggle("hot", i < on && i >= bars.length - 2);
    });
  }
  aurora.style.setProperty("--lv", peak.toFixed(3));
  $("#recNote").textContent = job.model_loaded ? "边录边转中，结束后几秒就出转写" : "正在准备识别模型…";
  const lines = job.live ?? [];
  const box = $("#liveLines");
  const key = lines.map((l) => l.start_ms).join(",");
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    box.innerHTML = lines.length
      ? lines.map((l) => `<div><span class="lt">${fmt(l.start_ms)}</span>${esc(l.text)}</div>`).join("")
      : `<div class="lw">说话内容会在这里实时出现（约十几秒一批）</div>`;
    box.scrollTop = box.scrollHeight;
  }
}

const sourceName = (s: string) => (s === "system" ? "系统声音" : "麦克风");

function idleForm(): string {
  const sys = S.env ? S.env.system_audio : true;
  return `
    <input class="title-input" id="fTitle" placeholder="会议名称，比如：周会、项目评审" value="${esc(S.form.title)}" maxlength="60">
    <input class="hot-input" id="fHot" placeholder="热词（可选）：人名、项目代号、专有名词，用逗号隔开" value="${esc(S.form.hotwords)}">
    <div class="glass-foot">
      <button class="toggle ${S.form.system && sys ? "on" : ""}" id="tSys" ${sys ? "" : "disabled title='需要 macOS 15+'"}>${ICON.monitor}系统声音</button>
      <button class="toggle ${S.form.mic ? "on" : ""}" id="tMic">${ICON.mic}麦克风</button>
      <span class="spacer"></span>
      <span class="rec-label">开始录音</span>
      <button class="rec-btn" id="recBtn" title="开始录音"><span class="core"></span></button>
    </div>`;
}

const recordingView = (job: Job) => `
  <div class="live-head"><span class="live-dot"></span>录音中 · ${esc((job.sources ?? []).map(sourceName).join(" + "))}</div>
  <div class="timer" id="timer">00:00</div>
  <div class="meters">
    ${(job.sources ?? [])
      .map((s) => `<div class="meter" id="meter-${s}"><span>${sourceName(s)}</span><div class="meter-bars">${"<i></i>".repeat(28)}</div></div>`)
      .join("")}
  </div>
  <div class="live-lines" id="liveLines"></div>
  <div class="glass-foot">
    <span class="proc-note" style="margin:0" id="recNote"></span>
    <span class="spacer"></span>
    <span class="rec-label">结束录音</span>
    <button class="rec-btn stop" id="stopBtn" title="结束录音"><span class="core"></span></button>
  </div>`;

const STEPS: [Job["state"][], string][] = [
  [["saving"], "保存录音"],
  [["finalizing", "loading", "transcribing"], "转写"],
  [["summarizing"], "生成纪要"],
];

const processingView = (job: Job) => `
  <div class="live-head" style="color:var(--blue)"><span class="live-dot" style="background:var(--cyan)"></span><span id="procTitle">${esc(stateLabel(job.state))}</span></div>
  <div class="steps" id="steps" style="margin-top:16px">${STEPS.map(([, l]) => `<div class="step"><div class="bar"></div><span>${l}</span></div>`).join("")}</div>
  <div class="proc-note" id="procNote"></div>
  <div class="stream md" id="stream"></div>`;

const NOTES: Partial<Record<Job["state"], string>> = {
  saving: "正在合成录音…",
  finalizing: "录音时已经转好了大部分，正在处理最后几秒并区分说话人",
  loading: "第一次运行要从 ModelScope 下载约 2 GB 模型，之后会快很多",
  transcribing: "本地分段识别中，10 分钟的录音大约 1 分半",
};

function updateProcessing(job: Job): void {
  const idx = job.kind === "summarize" ? 2 : STEPS.findIndex(([states]) => states.includes(job.state));
  $$("#steps .step").forEach((el, i) => {
    el.className = "step " + (i < idx ? "done" : i === idx ? "now" : "");
  });
  $("#procTitle").textContent = job.stage ? `${stateLabel(job.state)} · ${job.stage}` : stateLabel(job.state);
  $("#procNote").textContent = NOTES[job.state] ?? "";
  const st = $("#stream");
  const text = job.state === "summarizing" && !job.minutes ? "__thinking" : (job.minutes ?? "");
  if (st.dataset.text !== text) {
    st.dataset.text = text;
    st.innerHTML = text === "__thinking" ? `<p style="color:var(--muted);margin:0">正在阅读转写…</p>` : text ? renderMarkdown(text) : "";
    st.scrollTop = st.scrollHeight;
  }
}

const meetingLink = (job: Job) => `#/m/${encodeURIComponent(job.meeting ?? "")}`;

const doneView = (job: Job) => `
  <div class="live-head" style="color:var(--green)">✓ 纪要已生成</div>
  <div class="timer" style="font-size:22px;margin:12px 0 4px;font-family:var(--font)">${esc((job.meeting ?? "").replace(/^\d{4}-\d{2}-\d{2}_\d{4}_/, ""))}</div>
  <div class="proc-note">已保存在会议记录里，可以回听、改说话人名字、推送到群</div>
  <div class="glass-foot">
    <button class="btn" id="againBtn">再开一场</button>
    <span class="spacer"></span>
    <a class="btn primary" href="${meetingLink(job)}">查看纪要 ${ICON.chevron}</a>
  </div>`;

const errorView = (job: Job) => `
  <div class="live-head">处理没有完成</div>
  <div class="err-box" style="margin-top:12px">${esc(job.error)}</div>
  <div class="proc-note">录音和已完成的部分都保存在会议记录里，可以稍后重试</div>
  <div class="glass-foot">
    <button class="btn" id="againBtn">关闭</button>
    <span class="spacer"></span>
    <a class="btn primary" href="${meetingLink(job)}">打开这场会议</a>
  </div>`;

const VIEWS: Record<Exclude<Phase, "__init">, (job: Job) => string> = {
  idle: idleForm,
  recording: recordingView,
  processing: processingView,
  done: doneView,
  error: errorView,
};

function bindHome(phase: Phase): void {
  if (phase === "idle") {
    const title = $<HTMLInputElement>("#fTitle");
    title.oninput = () => (S.form.title = title.value);
    title.onkeydown = (e) => {
      if (e.key === "Enter") startRecording();
    };
    const hot = $<HTMLInputElement>("#fHot");
    hot.oninput = () => (S.form.hotwords = hot.value);
    $("#tSys").onclick = (e) => {
      S.form.system = !S.form.system;
      (e.currentTarget as HTMLElement).classList.toggle("on", S.form.system);
    };
    $("#tMic").onclick = (e) => {
      S.form.mic = !S.form.mic;
      (e.currentTarget as HTMLElement).classList.toggle("on", S.form.mic);
    };
    $("#recBtn").onclick = startRecording;
  } else if (phase === "recording") {
    $("#stopBtn").onclick = stopRecording;
  } else if (phase === "done" || phase === "error") {
    $("#againBtn").onclick = async () => {
      await api("/api/job/dismiss", { method: "POST" });
      S.job = { state: "idle" };
      updateHome(S.job, INIT, true);
    };
  }
}

async function startRecording(): Promise<void> {
  const sys = S.form.system && (!S.env || S.env.system_audio);
  if (!sys && !S.form.mic) return toast("系统声音和麦克风至少开一个", true);
  const btn = $maybe<HTMLButtonElement>("#recBtn");
  if (btn) btn.disabled = true;
  try {
    S.job = await api<Job>("/api/record/start", {
      json: {
        title: S.form.title.trim() || "会议",
        system: sys,
        mic: S.form.mic,
        hotwords: S.form.hotwords.split(/[,，、\s]+/).filter(Boolean),
      },
    });
    S.form.title = "";
    poll();
  } catch (e) {
    toast(errorMessage(e), true);
    if (btn) btn.disabled = false;
  }
}

async function stopRecording(): Promise<void> {
  $<HTMLButtonElement>("#stopBtn").disabled = true;
  try {
    await api("/api/record/stop", { method: "POST" });
    poll();
  } catch (e) {
    toast(errorMessage(e), true);
  }
}

/* ---------- 导入 ---------- */

export async function importFile(file: File | undefined): Promise<void> {
  if (!file) return;
  if (isBusy(S.job)) return toast("还有任务在进行，请稍等", true);
  const fd = new FormData();
  fd.append("file", file);
  fd.append("title", file.name.replace(/\.[^.]+$/, ""));
  fd.append("modified", String(file.lastModified || 0));
  toast(`正在导入 ${file.name}`);
  try {
    S.job = await api<Job>("/api/import", { method: "POST", body: fd });
    if (location.hash && location.hash !== "#/") location.hash = "#/";
    else updateHome(S.job, INIT, true);
    poll();
  } catch (e) {
    toast(errorMessage(e), true);
  }
}

export function setupImport(): void {
  const input = $<HTMLInputElement>("#fileInput");
  input.onchange = () => {
    importFile(input.files?.[0]);
    input.value = "";
  };
  const overlay = $("#dropOverlay");
  let depth = 0;
  window.addEventListener("dragenter", (e) => {
    if (e.dataTransfer?.types?.includes("Files")) {
      depth++;
      overlay.classList.add("show");
    }
  });
  window.addEventListener("dragleave", () => {
    if (--depth <= 0) {
      depth = 0;
      overlay.classList.remove("show");
    }
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    overlay.classList.remove("show");
    importFile(e.dataTransfer?.files[0]);
  });
}
