"use strict";

/* ---------- 基础 ---------- */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const main = $("#main");
const COLORS = ["#1F4BFF", "#12B3A8", "#F79009", "#E04F9C", "#7A5AF8", "#0BA5EC", "#66C61C", "#EF6820"];
const color = (i) => COLORS[i % COLORS.length];

const ICON = {
  chevron: '<svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="m15 6-6 6 6 6"/></svg>',
  monitor: '<svg viewBox="0 0 24 24"><rect x="3" y="4.5" width="18" height="12" rx="2"/><path d="M8.5 20h7M12 16.5V20"/></svg>',
  mic: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></svg>',
  upload: '<svg viewBox="0 0 24 24"><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5M9 13h6M9 16.5h4"/></svg>',
  spark: '<svg viewBox="0 0 24 24"><path d="M12 3.5c.6 3.8 2.7 5.9 6.5 6.5-3.8.6-5.9 2.7-6.5 6.5-.6-3.8-2.7-5.9-6.5-6.5 3.8-.6 5.9-2.7 6.5-6.5z"/><path d="M18.5 16.5c.25 1.4 1 2.15 2.5 2.5-1.5.35-2.25 1.1-2.5 2.5-.25-1.4-1-2.15-2.5-2.5 1.5-.35 2.25-1.1 2.5-2.5z"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M20.5 3.5 10 14M20.5 3.5 14 20.5l-4-6.5-6.5-4z"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></svg>',
  redo: '<svg viewBox="0 0 24 24"><path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><rect x="6.5" y="5" width="4" height="14" rx="1.2"/><rect x="13.5" y="5" width="4" height="14" rx="1.2"/></svg>',
  people: '<svg viewBox="0 0 24 24"><circle cx="9" cy="8.5" r="3.2"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0M16 5.6a3.2 3.2 0 0 1 0 5.8M18 13.8c1.6.8 2.6 2.6 2.6 5.2"/></svg>',
  bot: '<svg viewBox="0 0 24 24"><rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 8V4.5M9 13h.01M15 13h.01M9.5 16h5"/></svg>',
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function fmt(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = String(m).padStart(2, "0"), ss = String(sec).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function tsToMs(t) {
  const p = t.split(":").map(Number);
  return (p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1]) * 1000;
}

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（${res.status}）`);
  return data;
}

let toastTimer;
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", error);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), error ? 4200 : 2400);
}

function initial(name) {
  const m = /^说话人(\d+)$/.exec(name);
  return m ? m[1] : [...name][0] || "?";
}

/* ---------- 全局状态 ---------- */

const S = {
  env: null,
  meetings: [],
  job: { state: "idle" },
  form: { title: "", system: true, mic: true, hotwords: "" },
  detail: null, // 当前打开的会议
  pollTimer: null,
  lastJobState: "idle",
};

const BUSY = new Set(["recording", "saving", "finalizing", "loading", "transcribing", "summarizing"]);
const isBusy = (j) => j && BUSY.has(j.state);

/* ---------- 路由 ---------- */

function route() {
  const h = decodeURIComponent(location.hash.slice(1) || "/");
  stopAudio();
  S.detail = null;
  let nav = "home";
  if (h.startsWith("/m/")) { nav = "meetings"; renderDetail(h.slice(3)); }
  else if (h === "/meetings") { nav = "meetings"; renderMeetings(); }
  else if (h === "/settings") { nav = "settings"; renderSettings(); }
  else renderHome();
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
  $$("#recentList a").forEach((a) => a.classList.toggle("active", h === `/m/${a.dataset.id}`));
  main.scrollTop = 0;
}

/* ---------- 侧边栏 ---------- */

async function refreshMeetings() {
  try { S.meetings = await api("/api/meetings"); } catch { S.meetings = []; }
  const list = $("#recentList");
  const h = decodeURIComponent(location.hash.slice(1));
  list.innerHTML = S.meetings.length
    ? S.meetings.slice(0, 8).map((m) => `
        <a href="#/m/${encodeURIComponent(m.id)}" data-id="${esc(m.id)}" class="${h === "/m/" + m.id ? "active" : ""}">
          <div class="r-title">${esc(m.title)}</div>
          <div class="r-date">${esc(m.date.slice(5).replace("-", "/"))} ${esc(m.time)}${m.duration ? " · " + esc(m.duration) : ""}</div>
        </a>`).join("")
    : `<div class="r-empty">还没有会议</div>`;
}

async function refreshEnv() {
  try { S.env = await api("/api/env"); } catch { return; }
  const e = S.env;
  const rows = [
    [e.model_loaded ? "" : e.model_cached ? "" : "warn", e.model_loaded ? "识别模型已加载" : e.model_cached ? "识别模型已就绪" : "首次转写会下载模型"],
    [e.llm_ready ? "" : "bad", e.llm_ready ? e.llm_model : "未配置大模型"],
    [e.system_audio ? "" : "warn", e.system_audio ? "可录系统声音" : "仅能录麦克风"],
  ];
  $("#envBox").innerHTML = rows.map(([c, t]) => `<div class="env-row"><span class="env-dot ${c}"></span><span>${esc(t)}</span></div>`).join("")
    + `<div class="env-row" style="color:var(--muted-2)">v${esc(e.version)}</div>`;
}

/* ---------- 任务轮询 ---------- */

async function poll() {
  clearTimeout(S.pollTimer);
  let job;
  try { job = await api("/api/job"); } catch { job = S.job; }
  const prev = S.job;
  S.job = job;

  const pill = $("#livePill");
  pill.hidden = !isBusy(job);
  pill.classList.toggle("busy", job.state !== "recording");
  if (isBusy(job)) {
    $("#livePillText").textContent = job.state === "recording" ? `录音中 ${fmt(job.elapsed * 1000)}` : stateLabel(job.state);
    pill.href = job.kind === "summarize" ? `#/m/${encodeURIComponent(job.meeting)}` : "#/";
  }

  const onHome = !location.hash || location.hash === "#/";
  if (onHome) updateHome(job, prev);
  if (S.detail && job.meeting === S.detail.id) updateDetailJob(job, prev);

  if (prev.state !== job.state && (job.state === "done" || job.state === "error")) {
    refreshMeetings();
    refreshEnv();
    if (job.state === "done" && !onHome && job.kind !== "summarize") toast("纪要已生成");
  }
  S.pollTimer = setTimeout(poll, job.state === "recording" ? 160 : isBusy(job) ? 500 : 2500);
}

function stateLabel(s) {
  return { saving: "正在保存", finalizing: "转写最后一段", loading: "加载识别模型", transcribing: "正在转写", summarizing: "正在生成纪要" }[s] || "处理中";
}

/* ---------- 首页 ---------- */

function renderHome() {
  main.innerHTML = `
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
  updateHome(S.job, { state: "__init" });
}

function updateHome(job, prev) {
  const glass = $("#glass");
  if (!glass) return;
  const aurora = $("#aurora");
  const phase = job.state === "recording" ? "recording" : isBusy(job) ? "processing" : job.state === "done" || job.state === "error" ? job.state : "idle";
  const prevPhase = prev.state === "recording" ? "recording" : isBusy(prev) ? "processing" : prev.state === "done" || prev.state === "error" ? prev.state : prev.state === "__init" ? "__init" : "idle";
  aurora.classList.toggle("live", phase === "recording");

  if (phase !== prevPhase || glass.dataset.phase !== phase) {
    glass.dataset.phase = phase;
    glass.innerHTML = { idle: idleForm, recording: recordingView, processing: processingView, done: doneView, error: errorView }[phase](job);
    bindHome(phase, job);
  }
  if (phase === "recording") {
    $("#timer").textContent = fmt(job.elapsed * 1000);
    const lv = job.levels || {};
    let peak = 0;
    for (const [k, v] of Object.entries(lv)) {
      peak = Math.max(peak, v);
      const bars = $$(`#meter-${k} i`);
      const on = Math.round(v * bars.length);
      bars.forEach((b, i) => { b.classList.toggle("on", i < on); b.classList.toggle("hot", i < on && i >= bars.length - 2); });
    }
    aurora.style.setProperty("--lv", peak.toFixed(3));
    $("#recNote").textContent = job.model_loaded ? "边录边转中，结束后几秒就出转写" : "正在准备识别模型…";
    const lines = job.live || [];
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
  if (phase === "processing") updateProcessing(job);
}

const idleForm = () => {
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
};

const recordingView = (job) => `
  <div class="live-head"><span class="live-dot"></span>录音中 · ${esc(job.sources.map((s) => (s === "system" ? "系统声音" : "麦克风")).join(" + "))}</div>
  <div class="timer" id="timer">00:00</div>
  <div class="meters">
    ${job.sources.map((s) => `<div class="meter" id="meter-${s}"><span>${s === "system" ? "系统声音" : "麦克风"}</span><div class="meter-bars">${"<i></i>".repeat(28)}</div></div>`).join("")}
  </div>
  <div class="live-lines" id="liveLines"></div>
  <div class="glass-foot">
    <span class="proc-note" style="margin:0" id="recNote"></span>
    <span class="spacer"></span>
    <span class="rec-label">结束录音</span>
    <button class="rec-btn stop" id="stopBtn" title="结束录音"><span class="core"></span></button>
  </div>`;

const STEPS = [[["saving"], "保存录音"], [["finalizing", "loading", "transcribing"], "转写"], [["summarizing"], "生成纪要"]];

const processingView = (job) => `
  <div class="live-head" style="color:var(--blue)"><span class="live-dot" style="background:var(--cyan)"></span><span id="procTitle">${esc(stateLabel(job.state))}</span></div>
  <div class="steps" id="steps" style="margin-top:16px">${STEPS.map(([, l]) => `<div class="step"><div class="bar"></div><span>${l}</span></div>`).join("")}</div>
  <div class="proc-note" id="procNote"></div>
  <div class="stream md" id="stream"></div>`;

function updateProcessing(job) {
  const idx = job.kind === "summarize" ? 2 : STEPS.findIndex(([states]) => states.includes(job.state));
  $$("#steps .step").forEach((el, i) => { el.className = "step " + (i < idx ? "done" : i === idx ? "now" : ""); });
  $("#procTitle").textContent = job.stage ? `${stateLabel(job.state)} · ${job.stage}` : stateLabel(job.state);
  const note = {
    saving: "正在合成录音…",
    finalizing: "录音时已经转好了大部分，正在处理最后几秒并区分说话人",
    loading: "第一次运行要从 ModelScope 下载约 2 GB 模型，之后会快很多",
    transcribing: "本地分段识别中，10 分钟的录音大约 1 分半",
    summarizing: "",
  }[job.state];
  $("#procNote").textContent = note || "";
  const st = $("#stream");
  const text = job.state === "summarizing" && !job.minutes ? "__thinking" : job.minutes;
  if (st.dataset.text !== text) {
    st.dataset.text = text;
    st.innerHTML = text === "__thinking" ? `<p style="color:var(--muted);margin:0">正在阅读转写…</p>` : text ? renderMarkdown(text) : "";
    st.scrollTop = st.scrollHeight;
  }
}

const doneView = (job) => `
  <div class="live-head" style="color:var(--green)">✓ 纪要已生成</div>
  <div class="timer" style="font-size:22px;margin:12px 0 4px;font-family:var(--font)">${esc(job.meeting.replace(/^\d{4}-\d{2}-\d{2}_\d{4}_/, ""))}</div>
  <div class="proc-note">已保存在会议记录里，可以回听、改说话人名字、推送到群</div>
  <div class="glass-foot">
    <button class="btn" id="againBtn">再开一场</button>
    <span class="spacer"></span>
    <a class="btn primary" href="#/m/${encodeURIComponent(job.meeting)}">查看纪要 ${ICON.chevron}</a>
  </div>`;

const errorView = (job) => `
  <div class="live-head">处理没有完成</div>
  <div class="err-box" style="margin-top:12px">${esc(job.error)}</div>
  <div class="proc-note">录音和已完成的部分都保存在会议记录里，可以稍后重试</div>
  <div class="glass-foot">
    <button class="btn" id="againBtn">关闭</button>
    <span class="spacer"></span>
    <a class="btn primary" href="#/m/${encodeURIComponent(job.meeting)}">打开这场会议</a>
  </div>`;

function bindHome(phase) {
  if (phase === "idle") {
    $("#fTitle").oninput = (e) => (S.form.title = e.target.value);
    $("#fHot").oninput = (e) => (S.form.hotwords = e.target.value);
    $("#tSys").onclick = (e) => { S.form.system = !S.form.system; e.currentTarget.classList.toggle("on", S.form.system); };
    $("#tMic").onclick = (e) => { S.form.mic = !S.form.mic; e.currentTarget.classList.toggle("on", S.form.mic); };
    $("#fTitle").onkeydown = (e) => { if (e.key === "Enter") startRecording(); };
    $("#recBtn").onclick = startRecording;
  } else if (phase === "recording") {
    $("#stopBtn").onclick = stopRecording;
  } else if (phase === "done" || phase === "error") {
    $("#againBtn").onclick = async () => { await api("/api/job/dismiss", { method: "POST" }); S.job = { state: "idle" }; updateHome(S.job, { state: "__init" }); };
  }
}

async function startRecording() {
  const sys = S.form.system && (!S.env || S.env.system_audio);
  if (!sys && !S.form.mic) return toast("系统声音和麦克风至少开一个", true);
  const btn = $("#recBtn");
  if (btn) btn.disabled = true;
  try {
    S.job = await api("/api/record/start", {
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
    toast(e.message, true);
    if (btn) btn.disabled = false;
  }
}

async function stopRecording() {
  $("#stopBtn").disabled = true;
  try { await api("/api/record/stop", { method: "POST" }); poll(); } catch (e) { toast(e.message, true); }
}

/* ---------- 导入 ---------- */

async function importFile(file) {
  if (!file) return;
  if (isBusy(S.job)) return toast("还有任务在进行，请稍等", true);
  const fd = new FormData();
  fd.append("file", file);
  fd.append("title", file.name.replace(/\.[^.]+$/, ""));
  fd.append("modified", String(file.lastModified || 0));
  toast(`正在导入 ${file.name}`);
  try {
    S.job = await api("/api/import", { method: "POST", body: fd });
    if (location.hash && location.hash !== "#/") location.hash = "#/";
    else updateHome(S.job, { state: "__init" });
    poll();
  } catch (e) { toast(e.message, true); }
}

$("#fileInput").onchange = (e) => { importFile(e.target.files[0]); e.target.value = ""; };
let dragDepth = 0;
window.addEventListener("dragenter", (e) => { if (e.dataTransfer?.types?.includes("Files")) { dragDepth++; $("#dropOverlay").classList.add("show"); } });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#dropOverlay").classList.remove("show"); } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("#dropOverlay").classList.remove("show");
  importFile(e.dataTransfer.files[0]);
});

/* ---------- 会议记录 ---------- */

function renderMeetings() {
  main.innerHTML = `
    <div class="page">
      <div class="page-head">
        <div><h1>会议<em>.</em>记录</h1><div class="sub" id="mCount"></div></div>
        <span class="grow"></span>
        <label class="search">${ICON.search}<input class="input" id="mSearch" placeholder="搜索标题或内容"></label>
      </div>
      <div id="mList"></div>
    </div>`;
  $("#mSearch").oninput = (e) => drawMeetings(e.target.value.trim());
  refreshMeetings().then(() => drawMeetings(""));
  drawMeetings("");
}

function drawMeetings(q) {
  const box = $("#mList");
  if (!box) return;
  const items = S.meetings.filter((m) => !q || (m.title + m.summary + m.speakers.join("")).includes(q));
  $("#mCount").textContent = S.meetings.length ? `共 ${S.meetings.length} 场，存放在 ${S.env ? S.env.root.replace(/^\/Users\/[^/]+/, "~") : "~/Documents/jiyao"}` : "";
  if (!S.meetings.length) {
    box.innerHTML = `<div class="empty"><h3>还没有会议</h3><div>开一场会，或者把已有的录音拖进窗口</div><a class="btn primary" href="#/">开始录音</a></div>`;
    return;
  }
  if (!items.length) { box.innerHTML = `<div class="empty"><h3>没有找到</h3><div>换个关键词试试</div></div>`; return; }
  const groups = {};
  for (const m of items) (groups[m.date.slice(0, 7)] ||= []).push(m);
  box.innerHTML = Object.entries(groups).map(([ym, ms]) => `
    <div class="month">${ym.slice(0, 4)} 年 ${Number(ym.slice(5))} 月</div>
    <div class="grid">${ms.map((m, i) => `
      <a class="card m-card" href="#/m/${encodeURIComponent(m.id)}" style="animation-delay:${Math.min(i, 8) * 30}ms">
        <div class="when"><span>${esc(m.date.slice(5).replace("-", " 月 "))} 日 ${esc(m.time)}</span><span>${esc(m.duration)}</span></div>
        <h3>${esc(m.title)}</h3>
        <p>${esc(m.summary || (m.has_transcript ? "已转写，还没有生成纪要" : "录音还没有处理"))}</p>
        <div class="foot">
          <span class="avatars">${m.speakers.slice(0, 4).map((s, k) => `<span style="background:${color(k)}">${esc(initial(s))}</span>`).join("")}</span>
          ${m.speakers.length ? `<span>${m.speakers.length} 位说话人</span>` : ""}
          <span class="status ${m.has_minutes ? "ok" : m.has_transcript ? "half" : ""}">${m.has_minutes ? "已出纪要" : m.has_transcript ? "仅转写" : "未处理"}</span>
        </div>
      </a>`).join("")}
    </div>`).join("");
}

/* ---------- 纪要渲染（只覆盖纪要用到的 Markdown 子集） ---------- */

function inline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\s*[（(]\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*[）)]/g, "[$1]")
    .replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g, (_, t) => `<button class="ts" data-ms="${tsToMs(t)}">${t}</button>`);
}

function renderMarkdown(md, { skipHead = false } = {}) {
  const lines = md.replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0, afterH1 = false;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m;
    if ((m = /^(#{1,3})\s+(.*)$/.exec(line))) {
      if (!(skipHead && m[1].length === 1)) out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);
      afterH1 = m[1].length === 1;
      i++;
      continue;
    }
    if (line.trim().startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r, k) => !(k === 1 && /^[\s|:-]+$/.test(r)));
      const [head, ...rest] = body;
      out.push(`<table><thead><tr>${cells(head).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rest.map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      afterH1 = false;
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      const meta = afterH1 && items.some((t) => t.startsWith("日期："));
      const tag = ordered ? "ol" : "ul";
      if (!(skipHead && meta)) out.push(`<${tag}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${tag}>`);
      afterH1 = false;
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|\s*\||\s*([-*]|\d+\.)\s)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${inline(para.join(" "))}</p>`);
    afterH1 = false;
  }
  return out.join("\n");
}

/* ---------- 会议详情 ---------- */

let audioEl = null;
let detailTab = "minutes";

function stopAudio() {
  if (audioEl) { audioEl.pause(); audioEl.src = ""; audioEl = null; }
}

async function renderDetail(id, keepTab = false) {
  if (!keepTab) detailTab = "minutes";
  main.innerHTML = `<div class="page"><a class="back" href="#/meetings">${ICON.back}会议记录</a></div>`;
  let d;
  try { d = await api(`/api/meetings/${encodeURIComponent(id)}`); } catch (e) {
    main.innerHTML = `<div class="page"><a class="back" href="#/meetings">${ICON.back}会议记录</a><div class="empty"><h3>打不开这场会议</h3><div>${esc(e.message)}</div></div></div>`;
    return;
  }
  if (decodeURIComponent(location.hash.slice(1)) !== `/m/${id}`) return; // 加载期间已经切走
  S.detail = d;
  const jobHere = isBusy(S.job) && S.job.meeting === id;
  main.innerHTML = `
    <div class="page">
      <a class="back" href="#/meetings">${ICON.back}会议记录</a>
      <div class="detail-head">
        <h1>${esc(d.title)}</h1>
        <div class="detail-actions">
          ${d.minutes ? `<button class="btn" id="aCopy">${ICON.copy}复制纪要</button><button class="btn" id="aPush">${ICON.send}推送到群</button>` : ""}
          ${d.has_transcript ? `<button class="btn" id="aRegen">${ICON.redo}${d.minutes ? "重新生成" : "生成纪要"}</button>` : ""}
          <button class="btn ghost" id="aFolder" title="打开文件夹">${ICON.folder}</button>
        </div>
      </div>
      <div class="meta">
        <span>${esc(d.date)} ${esc(d.time)}</span>
        ${d.duration ? `<span class="sep"></span><span>时长 ${esc(d.duration)}</span>` : ""}
        ${d.speaker_list.length ? `<span class="sep"></span>
          <span class="speakers">${d.speaker_list.map((s) => `<span class="chip"><span class="dot" style="background:${color(s.index)}"></span>${esc(s.name)}</span>`).join("")}
          <button class="btn" id="aRename">${ICON.people}改名</button></span>` : ""}
      </div>
      ${d.has_transcript ? `
        <div class="tabs-row">
          <div class="seg"><button data-tab="minutes">纪要</button><button data-tab="transcript">转写</button></div>
          <span class="grow"></span>
          <button class="btn ghost" id="aEdit" ${detailTab === "minutes" && d.minutes ? "" : "hidden"}>${ICON.edit}编辑</button>
        </div>
        <div class="card" id="pane"></div>` : `
        <div class="card"><div class="empty"><h3>${jobHere ? "正在处理…" : "录音还没有转写"}</h3><div>${jobHere ? "进度在首页可以看到" : "可能是上次处理被打断了"}</div>
          ${jobHere ? `<a class="btn" href="#/">查看进度</a>` : d.audio ? `<button class="btn primary" id="aProcess">开始转写</button>` : ""}</div></div>`}
      ${d.audio ? playerHTML() : ""}
    </div>`;

  $$(".seg button").forEach((b) => (b.onclick = () => { detailTab = b.dataset.tab; drawPane(); }));
  if ($("#aCopy")) $("#aCopy").onclick = () => copyText(d.minutes);
  if ($("#aPush")) $("#aPush").onclick = pushMinutes;
  if ($("#aRegen")) $("#aRegen").onclick = regenerate;
  if ($("#aRename")) $("#aRename").onclick = renameSpeakers;
  if ($("#aProcess")) $("#aProcess").onclick = async () => {
    try { S.job = await api(`/api/meetings/${encodeURIComponent(id)}/process`, { method: "POST" }); location.hash = "#/"; poll(); } catch (e) { toast(e.message, true); }
  };
  $("#aFolder").onclick = () => api(`/api/meetings/${encodeURIComponent(id)}/reveal`, { method: "POST" });
  if ($("#aEdit")) $("#aEdit").onclick = editMinutes;
  if (d.audio) bindPlayer(id);
  if (d.has_transcript) drawPane();
  if (jobHere) updateDetailJob(S.job, { state: "idle" });
}

function drawPane() {
  const d = S.detail, pane = $("#pane");
  if (!d || !pane) return;
  $$(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.tab === detailTab));
  if ($("#aEdit")) $("#aEdit").hidden = !(detailTab === "minutes" && d.minutes);
  if (detailTab === "minutes") {
    pane.className = "card doc";
    pane.innerHTML = d.minutes
      ? `<div class="md">${renderMarkdown(d.minutes, { skipHead: true })}</div>`
      : `<div class="minutes-empty">还没有纪要。<br><br><button class="btn primary" id="paneGen">${ICON.spark}生成纪要</button></div>`;
    if ($("#paneGen")) $("#paneGen").onclick = regenerate;
  } else {
    pane.className = "card turns";
    pane.innerHTML = d.turns.map((t, i) => `
      <div class="turn" data-i="${i}" data-ms="${t.start_ms}">
        <span class="av" style="background:${color(t.color)}">${esc(initial(t.speaker))}</span>
        <div><div class="who">${esc(t.speaker)}<button class="ts" data-ms="${t.start_ms}">${esc(t.ts)}</button></div><div class="text">${esc(t.text)}</div></div>
      </div>`).join("");
  }
  $$(".ts", pane).forEach((b) => (b.onclick = () => seek(Number(b.dataset.ms))));
}

function updateDetailJob(job, prev) {
  if (job.kind !== "summarize") return;
  const pane = $("#pane");
  if (isBusy(job) && pane) {
    detailTab = "minutes";
    $$(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.tab === "minutes"));
    pane.className = "card doc";
    let box = $("#genBox");
    if (!box) {
      pane.innerHTML = `<div id="genBox"><div class="live-head" style="color:var(--blue);margin-bottom:18px"><span class="live-dot" style="background:var(--cyan)"></span><span id="genTitle">正在生成纪要</span></div><div class="md" id="genStream"></div></div>`;
      box = $("#genBox");
    }
    $("#genTitle").textContent = job.stage ? `正在生成纪要 · ${job.stage}` : `正在用 ${S.env ? S.env.llm_model : "大模型"} 生成纪要`;
    const gs = $("#genStream");
    if (gs.dataset.len !== String(job.minutes.length)) {
      gs.dataset.len = String(job.minutes.length);
      gs.innerHTML = job.minutes ? renderMarkdown(job.minutes) : `<p style="color:var(--muted)">正在阅读转写…</p>`;
    }
  } else if (prev.state !== job.state && job.state === "done") {
    toast("纪要已更新");
    api("/api/job/dismiss", { method: "POST" });
    renderDetail(S.detail.id, true);
  } else if (prev.state !== job.state && job.state === "error") {
    toast(job.error, true);
    api("/api/job/dismiss", { method: "POST" });
    renderDetail(S.detail.id, true);
  }
}

async function regenerate() {
  try {
    S.job = await api(`/api/meetings/${encodeURIComponent(S.detail.id)}/summarize`, { method: "POST" });
    updateDetailJob(S.job, { state: "idle" });
    poll();
  } catch (e) { toast(e.message, true); }
}

async function pushMinutes() {
  try { toast((await api(`/api/meetings/${encodeURIComponent(S.detail.id)}/push`, { method: "POST" })).message); }
  catch (e) { toast(e.message, true); }
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("已复制纪要 Markdown");
}

function editMinutes() {
  const d = S.detail, pane = $("#pane");
  $("#aEdit").hidden = true;
  pane.className = "card doc";
  pane.innerHTML = `<textarea class="textarea" id="mdEdit" style="min-height:520px;font-size:14px">${esc(d.minutes)}</textarea>
    <div class="modal-foot"><button class="btn" id="mdCancel">取消</button><button class="btn primary" id="mdSave">保存</button></div>`;
  $("#mdCancel").onclick = drawPane;
  $("#mdSave").onclick = async () => {
    const text = $("#mdEdit").value;
    try {
      await api(`/api/meetings/${encodeURIComponent(d.id)}/minutes`, { method: "PUT", json: { text } });
      d.minutes = text.trim() + "\n";
      toast("已保存");
      renderDetail(d.id, true);
      refreshMeetings();
    } catch (e) { toast(e.message, true); }
  };
}

function renameSpeakers() {
  const d = S.detail;
  openModal(`
    <h3>给说话人改名</h3>
    <p class="hint">按第一次发言的顺序排列。点转写里的时间戳可以回听确认是谁。</p>
    ${d.speaker_list.map((s) => `<div class="sp-row"><span class="av" style="background:${color(s.index)}">${esc(initial(s.name))}</span><input class="input" data-i="${s.index}" value="${s.custom ? esc(s.name) : ""}" placeholder="${esc(`说话人${s.index + 1}`)}"></div>`).join("")}
    <div class="modal-foot"><button class="btn" data-close>取消</button><button class="btn" id="spSave">仅保存</button>${d.minutes ? `<button class="btn primary" id="spRegen">保存并重新生成纪要</button>` : ""}</div>`);
  const save = async (regen) => {
    const names = $$("#modalBody input").map((x) => x.value.trim());
    try {
      S.detail = await api(`/api/meetings/${encodeURIComponent(d.id)}/speakers`, { method: "PUT", json: { names } });
      closeModal();
      await renderDetail(d.id, true);
      refreshMeetings();
      if (regen) regenerate(); else toast("已更新说话人");
    } catch (e) { toast(e.message, true); }
  };
  $("#spSave").onclick = () => save(false);
  if ($("#spRegen")) $("#spRegen").onclick = () => save(true);
}

/* ---------- 播放器 ---------- */

const playerHTML = () => `
  <div class="player" id="player">
    <button class="play" id="pPlay" title="播放（空格）">${ICON.play}</button>
    <span class="p-time" id="pTime">00:00 / --:--</span>
    <div class="track" id="pTrack"><div class="rail"></div><div class="fill" id="pFill"></div><div class="knob" id="pKnob"></div></div>
    <button class="speed" id="pSpeed">1×</button>
    <span></span>
  </div>`;

function bindPlayer(id) {
  stopAudio();
  audioEl = new Audio(`/api/meetings/${encodeURIComponent(id)}/audio`);
  audioEl.preload = "metadata";
  const speeds = [1, 1.25, 1.5, 2];
  let si = 0;
  const draw = () => {
    if (!audioEl) return;
    const dur = audioEl.duration || 0, t = audioEl.currentTime;
    const pct = dur ? (t / dur) * 100 : 0;
    $("#pFill").style.width = pct + "%";
    $("#pKnob").style.left = pct + "%";
    $("#pTime").textContent = `${fmt(t * 1000)} / ${dur ? fmt(dur * 1000) : "--:--"}`;
    $("#pPlay").innerHTML = audioEl.paused ? ICON.play : ICON.pause;
    highlightTurn(t * 1000);
  };
  audioEl.ontimeupdate = draw;
  audioEl.onloadedmetadata = draw;
  audioEl.onplay = draw;
  audioEl.onpause = draw;
  $("#pPlay").onclick = () => (audioEl.paused ? audioEl.play() : audioEl.pause());
  $("#pSpeed").onclick = (e) => { si = (si + 1) % speeds.length; audioEl.playbackRate = speeds[si]; e.target.textContent = speeds[si] + "×"; };
  $("#pTrack").onclick = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (audioEl.duration) audioEl.currentTime = ((e.clientX - r.left) / r.width) * audioEl.duration;
  };
}

function seek(ms) {
  if (!audioEl) return toast("这场会议没有录音文件", true);
  audioEl.currentTime = ms / 1000;
  audioEl.play();
}

let lastHi = -1;
function highlightTurn(ms) {
  const turns = $$(".turn");
  if (!turns.length) return;
  let idx = -1;
  for (const t of turns) { if (Number(t.dataset.ms) <= ms + 200) idx = Number(t.dataset.i); else break; }
  if (idx === lastHi) return;
  lastHi = idx;
  turns.forEach((t) => t.classList.toggle("playing", Number(t.dataset.i) === idx && audioEl && !audioEl.paused));
}

document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || !audioEl || /INPUT|TEXTAREA/.test(document.activeElement.tagName)) return;
  e.preventDefault();
  audioEl.paused ? audioEl.play() : audioEl.pause();
});

/* ---------- 弹窗 ---------- */

function openModal(html) {
  $("#modalBody").innerHTML = html;
  $("#modal").hidden = false;
  $$("[data-close]", $("#modal")).forEach((b) => (b.onclick = closeModal));
  const first = $("#modalBody input");
  if (first) first.focus();
}
function closeModal() { $("#modal").hidden = true; }
$("#modal").addEventListener("mousedown", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/* ---------- 设置 ---------- */

// 本机中转（Magpie、CC Switch、Ollama）由服务端探测后补在前面；这里是远程服务
const REMOTE_PRESETS = [
  { id: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com", model: "deepseek-chat" },
  { id: "bailian", name: "通义千问（百炼）", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { id: "custom", name: "自定义" },
];

/* 模型选择：点开显示全部模型（按服务商分组），输入时过滤。不用 <datalist>：它按输入框现有内容过滤，
   框里已经有模型名时就只剩这一个选项，等于没法选。 */
function bindModelPicker(getModels, getRecommended) {
  const input = $("#sModel"), pop = $("#sModelPop");
  let filter = "", active = -1, items = [];
  const close = () => { pop.hidden = true; active = -1; };
  const draw = () => {
    const q = filter.trim().toLowerCase();
    items = getModels().filter((m) => !q || m.toLowerCase().includes(q));
    if (!getModels().length) {
      pop.innerHTML = `<div class="combo-empty">这个接口没有返回模型列表，直接输入模型名即可</div>`;
      return;
    }
    if (!items.length) { pop.innerHTML = `<div class="combo-empty">没有匹配“${esc(filter)}”的模型</div>`; return; }
    const groups = {};
    items.forEach((m) => { const g = m.includes("/") ? m.split("/")[0] : "其他"; (groups[g] ||= []).push(m); });
    let i = 0;
    pop.innerHTML = Object.entries(groups).map(([g, ms]) => `
      <div class="combo-group">${esc(g)}</div>
      ${ms.map((m) => `<div class="combo-item ${m === input.value ? "sel" : ""} ${i === active ? "act" : ""}" data-i="${i++}">
        <span>${esc(m.includes("/") ? m.slice(g.length + 1) : m)}</span>${m === getRecommended() ? `<em>推荐</em>` : ""}</div>`).join("")}`).join("");
    const sel = $(".combo-item.act", pop) || $(".combo-item.sel", pop);
    if (sel) sel.scrollIntoView({ block: "nearest" });
  };
  const open = () => {
    filter = "";
    active = -1;
    pop.hidden = false;
    draw();
    input.select(); // 选中现有内容：直接打字就是搜索，不会接在原来的模型名后面
  };
  const pick = (m) => { input.value = m; close(); input.dispatchEvent(new Event("change")); };
  input.addEventListener("focus", open);
  input.addEventListener("click", () => { if (pop.hidden) open(); });
  input.addEventListener("input", () => { filter = input.value; active = -1; pop.hidden = false; draw(); });
  input.addEventListener("keydown", (e) => {
    if (pop.hidden) return;
    if (e.key === "ArrowDown") { active = Math.min(items.length - 1, active + 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); draw(); e.preventDefault(); }
    else if (e.key === "Enter" && active >= 0) { pick(items[active]); e.preventDefault(); }
    else if (e.key === "Escape") { close(); e.stopPropagation(); }
  });
  pop.addEventListener("mousedown", (e) => {
    e.preventDefault(); // 不让输入框先失焦把列表关掉
    const it = e.target.closest(".combo-item");
    if (it) pick(items[Number(it.dataset.i)]);
  });
  input.addEventListener("blur", () => setTimeout(close, 100));
  return () => { if (!pop.hidden) draw(); }; // 模型列表异步到达后刷新已打开的下拉
}

async function renderSettings() {
  main.innerHTML = `<div class="page"><div class="page-head"><div><h1>设置</h1><div class="sub">保存在本机配置文件里</div></div></div><div id="sBody"></div></div>`;
  let c, local = [];
  try {
    [c, local] = await Promise.all([api("/api/config"), api("/api/llm/providers").catch(() => []), refreshEnv()]);
  } catch (e) { toast(e.message, true); return; }
  const env = S.env || {};
  const PRESETS = [...local.map((p) => ({ ...p, local: true })), ...REMOTE_PRESETS];
  const norm = (u) => u.trim().replace(/\/+$/, "").replace("localhost", "127.0.0.1");
  const presetOf = () => (PRESETS.find((p) => p.base_url && norm($("#sBase").value) === norm(p.base_url)) || PRESETS.at(-1)).id;
  let hot = [...c.hotwords];

  $("#sBody").innerHTML = `
    <div class="settings">
      <section class="card s-card">
        <h2>大模型</h2>
        <p class="hint">用来把转写整理成纪要。任何 OpenAI 兼容接口都可以。本机的 Magpie、CC Switch 中转打开后可以直接选；选本地 Ollama 则全程不出本机。</p>
        <div class="s-grid">
          <div class="seg" id="sPreset">${PRESETS.map((p) => `<button data-id="${p.id}">${p.local ? `<span class="dot" style="background:${p.running ? "var(--green)" : "#c9ccd1"};display:inline-block;margin-right:6px;vertical-align:1px" title="${p.running ? "运行中" : "未启动"}"></span>` : ""}${p.name}</button>`).join("")}</div>
          <div class="s-row2">
            <div class="field"><label>接口地址 Base URL</label><input class="input" id="sBase" value="${esc(c.base_url)}"></div>
            <div class="field"><label>模型 <span id="sModelCount" style="color:var(--muted-2)"></span></label>
              <div class="combo"><input class="input" id="sModel" value="${esc(c.model)}" placeholder="点这里从列表里选，也可以直接输入" autocomplete="off" spellcheck="false"><span class="combo-arrow">${ICON.chevron}</span><div class="combo-pop" id="sModelPop" hidden></div></div></div>
          </div>
          <div class="field"><label>API Key</label><input class="input" id="sKey" type="password" autocomplete="off" placeholder="${c.api_key_set ? `已设置${c.api_key_hint !== "已设置" ? `（${esc(c.api_key_hint)}）` : ""}，留空表示不修改` : "本地 Ollama 不需要"}"></div>
          <div class="s-actions"><button class="btn" id="sTest">测试连接</button><span class="test-msg" id="sTestMsg"></span></div>
        </div>
      </section>

      <section class="card s-card">
        <h2>热词</h2>
        <p class="hint">人名、项目代号、内部系统名。识别时优先识别成这些词，每场会议都生效。</p>
        <div class="tags" id="sTags"><input id="sTagInput" placeholder="输入后回车添加"></div>
      </section>

      <section class="card s-card">
        <h2>推送到群</h2>
        <p class="hint">在群里添加"自定义机器人"，把 webhook 地址贴过来，按域名自动识别飞书、钉钉或企业微信。</p>
        <div class="s-grid">
          <div class="field"><label>Webhook 地址</label><input class="input" id="sHook" value="${esc(c.webhook)}" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/…"></div>
          <div class="field"><label>加签密钥（机器人开启了"加签"才需要）</label><input class="input" id="sSecret" type="password" autocomplete="off" placeholder="${c.secret_set ? "已设置，留空表示不修改" : "可选"}"></div>
        </div>
      </section>

      <section class="card s-card">
        <h2>运行环境</h2>
        <p class="hint">会议保存在 ${esc(env.root || "")}，配置文件在 ${esc(c.path)}</p>
        <div class="checks">
          ${[
            [env.ffmpeg, "ffmpeg", env.ffmpeg ? "已安装" : "brew install ffmpeg"],
            [env.system_audio, "录系统声音", env.system_audio ? "可以录腾讯会议、飞书里对方的声音" : "需要 macOS 15+ 和 Xcode 命令行工具；不影响导入录音"],
            [env.model_cached, "识别模型", env.model_loaded ? "已加载" : env.model_cached ? "已下载" : "首次转写时自动下载约 2 GB"],
          ].map(([ok, name, note]) => `<div class="check"><span class="dot" style="background:${ok ? "var(--green)" : "var(--amber)"}"></span>${name}<small>${esc(note)}</small></div>`).join("")}
        </div>
      </section>

      <div class="save-bar"><button class="btn primary lg" id="sSave">保存设置</button></div>
    </div>`;

  const markPreset = () => $$("#sPreset button").forEach((b) => b.classList.toggle("on", b.dataset.id === presetOf()));
  markPreset();
  let modelList = [];
  let recommended = "";
  let refreshPicker = () => {};
  const setModels = (models, rec = "") => {
    modelList = models;
    recommended = rec;
    $("#sModelCount").textContent = models.length ? `（可选 ${models.length} 个）` : "";
    refreshPicker();
  };
  refreshPicker = bindModelPicker(() => modelList, () => recommended);
  const msg = (text, cls = "") => { $("#sTestMsg").className = "test-msg " + cls; $("#sTestMsg").textContent = text; };
  const current = PRESETS.find((p) => p.id === presetOf());
  if (current && current.local) setModels(current.models, current.recommended);

  $$("#sPreset button").forEach((b) => (b.onclick = () => {
    const p = PRESETS.find((x) => x.id === b.dataset.id);
    msg("");
    if (!p.base_url) {
      // 自定义：清空，让用户填自己的地址；哪个预设高亮是按地址判断的，不清空就会跳回原来那个
      $("#sBase").value = "";
      $("#sModel").value = "";
      $("#sKey").value = "";
      setModels([]);
      markPreset();
      $("#sBase").focus();
      msg("填 OpenAI 兼容接口的地址（一般以 /v1 结尾），填完会自动拉取模型列表");
      return;
    }
    $("#sBase").value = p.base_url;
    if (p.local) {
      setModels(p.models, p.recommended);
      // 本机中转的 key 是固定的，直接填上
      $("#sKey").value = p.api_key;
      const keep = p.models.includes($("#sModel").value);
      if (!keep) $("#sModel").value = (p.recommended && p.models.includes(p.recommended) && p.recommended) || p.models[0] || "";
      if (!p.running) msg(p.hint, "bad");
      else if (p.recommended && $("#sModel").value === p.recommended) msg(`已选推荐模型 ${p.recommended}：实测 76 分钟的会 51 秒出纪要`, "ok");
    } else {
      setModels([]);
      $("#sKey").value = "";
      $("#sModel").value = p.model;
      if (!c.api_key_set) msg("需要填写这家服务的 API Key");
    }
    markPreset();
  }));
  $("#sBase").oninput = markPreset;
  $("#sBase").onchange = async () => {
    const key = $("#sKey").value.trim();
    try {
      const r = await api("/api/llm/models", { json: { base_url: $("#sBase").value.trim(), model: "", api_key: key || null } });
      setModels(r.models);
    } catch { setModels([]); }
  };

  const drawTags = () => {
    $$("#sTags .chip").forEach((x) => x.remove());
    const input = $("#sTagInput");
    hot.forEach((w, i) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `${esc(w)}<button class="x" title="删除">×</button>`;
      chip.querySelector("button").onclick = () => { hot.splice(i, 1); drawTags(); };
      input.before(chip);
    });
  };
  drawTags();
  $("#sTagInput").onkeydown = (e) => {
    const v = e.target.value.trim();
    if ((e.key === "Enter" || e.key === "," || e.key === "，") && v) {
      e.preventDefault();
      for (const w of v.split(/[,，、\s]+/)) if (w && !hot.includes(w)) hot.push(w);
      e.target.value = "";
      drawTags();
    } else if (e.key === "Backspace" && !v && hot.length) { hot.pop(); drawTags(); }
  };

  $("#sTest").onclick = async () => {
    const msg = $("#sTestMsg");
    msg.className = "test-msg";
    msg.textContent = "连接中…";
    const key = $("#sKey").value.trim();
    try {
      const r = await api("/api/config/test", { json: { base_url: $("#sBase").value.trim(), model: $("#sModel").value.trim(), api_key: key || null } });
      msg.className = "test-msg " + (r.ok ? "ok" : "bad");
      msg.textContent = r.message;
    } catch (e) { msg.className = "test-msg bad"; msg.textContent = e.message; }
  };

  $("#sSave").onclick = async () => {
    const pending = $("#sTagInput").value.trim();
    if (pending) hot.push(...pending.split(/[,，、\s]+/).filter(Boolean));
    const key = $("#sKey").value.trim(), secret = $("#sSecret").value.trim();
    try {
      c = await api("/api/config", {
        method: "PUT",
        json: {
          base_url: $("#sBase").value.trim(), model: $("#sModel").value.trim(), api_key: key || null,
          hotwords: hot, webhook: $("#sHook").value.trim(), secret: secret || null,
        },
      });
      toast("设置已保存");
      refreshEnv();
      renderSettings();
    } catch (e) { toast(e.message, true); }
  };
}

/* ---------- 启动 ---------- */

window.addEventListener("hashchange", route);
(async () => {
  await Promise.all([refreshEnv(), refreshMeetings()]);
  try { S.job = await api("/api/job"); } catch { /* 服务刚起来 */ }
  route();
  poll();
})();
