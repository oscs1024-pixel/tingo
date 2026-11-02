/* 首页：录音表单 → 录音中（计时、电平、实时文字）→ 结束后立刻回到录音表单，转写和生成纪要在后台进行，
   进度显示在玻璃面板下方和侧边栏。第一次使用时玻璃面板里下载识别模型。另含拖拽导入。 */

import { api, type Job } from "../api";
import { $, $$, $maybe, errorMessage, esc, fmt, ICON, main, toast } from "../dom";
import { poll } from "../poll";
import { isBusy, needsModels, S } from "../state";
import { dismissJob, renderTasks } from "../tasks";

type Phase = "idle" | "recording" | "downloading" | "error";

const INIT: Job = { state: "idle" };
const phaseOf = (j: Job): Phase =>
  j.state === "recording" ? "recording" : j.kind === "models" && isBusy(j) ? "downloading" : j.kind === "models" && j.state === "error" ? "error" : "idle";

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
      <div class="home-tasks" id="homeTasks"></div>
      <div class="quick">
        <div class="quick-item" id="qImport"><span class="ico">${ICON.upload}</span><span><b>导入录音</b><small>拖进窗口，或点这里选文件</small></span>${ICON.chevron}</div>
        <a class="quick-item" href="#/meetings"><span class="ico">${ICON.file}</span><span><b>会议记录</b><small id="qCount">查看全部会议</small></span>${ICON.chevron}</a>
        <a class="quick-item" href="#/settings/llm"><span class="ico">${ICON.bot}</span><span><b>大模型</b><small id="qModel">设置纪要用的模型</small></span>${ICON.chevron}</a>
        <a class="quick-item" href="#/settings/push"><span class="ico">${ICON.send}</span><span><b>推送到群</b><small>飞书 · 钉钉 · 企业微信</small></span>${ICON.chevron}</a>
      </div>
    </div>`;
  $("#qImport").onclick = () => $("#fileInput").click();
  if (S.meetings.length) $("#qCount").textContent = `共 ${S.meetings.length} 场会议`;
  if (S.env) $("#qModel").textContent = S.env.llm_ready ? `当前：${S.env.llm_model}` : "还没有配置";
  updateHome(S.job, INIT, true);
  renderTasks();
}

export function updateHome(job: Job, prev: Job, forceRender = false): void {
  const glass = $maybe("#glass");
  if (!glass) return;
  const aurora = $("#aurora");
  const phase = phaseOf(job);
  aurora.classList.toggle("live", phase === "recording");
  if (forceRender || phase !== phaseOf(prev) || glass.dataset.phase !== phase) {
    glass.dataset.phase = phase;
    glass.innerHTML = VIEWS[phase](job);
    bindHome(phase);
  }
  if (phase === "recording") updateRecording(job, aurora);
  if (phase === "downloading") updateDownloading(job);
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

function downloadPanel(): string {
  const mb = S.env?.models_size_mb ?? 330;
  return `
    <div class="live-head" style="color:var(--blue)">第一次使用</div>
    <div class="timer" style="font-size:22px;margin:12px 0 6px;font-family:var(--font)">先下载识别模型（约 ${mb} MB）</div>
    <div class="proc-note">只需下载一次。识别、区分说话人都在这台电脑上完成，录音不会上传。</div>
    <div class="glass-foot">
      <span class="spacer"></span>
      <button class="btn primary" id="dlBtn">下载模型</button>
    </div>`;
}

function idleForm(): string {
  if (needsModels()) return downloadPanel();
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

const downloadingView = () => `
  <div class="live-head" style="color:var(--blue)"><span class="live-dot" style="background:var(--cyan)"></span>下载识别模型</div>
  <div class="timer" id="dlText" style="font-size:22px;margin:12px 0 10px">0 MB</div>
  <div class="steps" style="grid-template-columns:1fr"><div class="step now"><div class="bar"><div id="dlBar" style="position:absolute;inset:0 auto 0 0;width:0;background:linear-gradient(90deg,var(--cyan),var(--blue));border-radius:99px"></div></div></div></div>
  <div class="proc-note">识别模型来自 ModelScope，标点和声纹模型来自 GitHub；网络中断会自动续传</div>`;

function updateDownloading(job: Job): void {
  const m = /([\d.]+)\s*\/\s*([\d.]+)/.exec(job.stage ?? "");
  $("#dlText").textContent = job.stage || "准备下载…";
  if (m) $("#dlBar").style.width = `${Math.min(100, (Number(m[1]) / Number(m[2])) * 100).toFixed(1)}%`;
}

const errorView = (job: Job) => `
  <div class="live-head">模型下载没有完成</div>
  <div class="err-box" style="margin-top:12px">${esc(job.error)}</div>
  <div class="proc-note">已下载的部分会保留，重试时接着下</div>
  <div class="glass-foot">
    <button class="btn" id="againBtn">关闭</button>
    <span class="spacer"></span>
    <button class="btn primary" id="dlBtn">重试</button>
  </div>`;

const VIEWS: Record<Phase, (job: Job) => string> = {
  idle: idleForm,
  recording: recordingView,
  downloading: downloadingView,
  error: errorView,
};

function bindHome(phase: Phase): void {
  const dl = $maybe("#dlBtn");
  if (dl) {
    dl.onclick = async () => {
      (dl as HTMLButtonElement).disabled = true;
      try {
        S.job = await api<Job>("/api/models/download", { method: "POST" });
        poll();
      } catch (e) {
        toast(errorMessage(e), true);
        (dl as HTMLButtonElement).disabled = false;
      }
    };
  }
  if (phase === "idle" && !needsModels()) {
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
  } else if (phase === "error") {
    $("#againBtn").onclick = async () => {
      const id = S.job.id;
      S.job = { state: "idle" };
      updateHome(S.job, INIT, true);
      if (id) await dismissJob(id);
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
    toast("录音已保存，转写和纪要在后台进行，可以开始下一场");
    S.job = { state: "idle" };
    updateHome(S.job, INIT, true);
    poll();
  } catch (e) {
    toast(errorMessage(e), true);
  }
}

/* ---------- 导入 ---------- */

export async function importFile(file: File | undefined): Promise<void> {
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("title", file.name.replace(/\.[^.]+$/, ""));
  fd.append("modified", String(file.lastModified || 0));
  toast(`正在导入 ${file.name}`);
  try {
    await api<Job>("/api/import", { method: "POST", body: fd });
    toast(`已导入 ${file.name}，转写和纪要在后台进行`);
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
