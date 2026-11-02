/* 设置：大模型（本机中转一键选择 + 远程服务 + 自定义）、热词、推送到群、运行环境。 */

import { api, type Config, type LocalProvider } from "../api";
import { $, $$, $maybe, errorMessage, esc, ICON, main, toast } from "../dom";
import { refreshEnv } from "../sidebar";
import { S } from "../state";

interface Preset {
  id: string;
  name: string;
  base_url?: string;
  model?: string;
  local?: LocalProvider;
}

// 本机中转（Magpie、CC Switch、Ollama）由服务端探测后补在前面；这里是远程服务
const REMOTE_PRESETS: Preset[] = [
  { id: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com", model: "deepseek-chat" },
  { id: "bailian", name: "通义千问（百炼）", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { id: "custom", name: "自定义" },
];

/* 模型选择：点开显示全部模型（按服务商分组），输入时过滤。不用 <datalist>：它按输入框现有内容过滤，
   框里已经有模型名时就只剩这一个选项，等于没法选。返回一个刷新函数，模型列表异步到达后调用。 */
function bindModelPicker(getModels: () => string[], getRecommended: () => string): () => void {
  const input = $<HTMLInputElement>("#sModel");
  const pop = $("#sModelPop");
  let filter = "";
  let active = -1;
  let items: string[] = [];
  const close = () => {
    pop.hidden = true;
    active = -1;
  };
  const draw = () => {
    const q = filter.trim().toLowerCase();
    const all = getModels();
    items = all.filter((m) => !q || m.toLowerCase().includes(q));
    if (!all.length) {
      pop.innerHTML = `<div class="combo-empty">这个接口没有返回模型列表，直接输入模型名即可</div>`;
      return;
    }
    if (!items.length) {
      pop.innerHTML = `<div class="combo-empty">没有匹配“${esc(filter)}”的模型</div>`;
      return;
    }
    const groups: Record<string, string[]> = {};
    for (const m of items) (groups[m.includes("/") ? m.split("/")[0] : "其他"] ||= []).push(m);
    let i = 0;
    pop.innerHTML = Object.entries(groups)
      .map(
        ([g, ms]) => `
      <div class="combo-group">${esc(g)}</div>
      ${ms
        .map(
          (m) => `<div class="combo-item ${m === input.value ? "sel" : ""} ${i === active ? "act" : ""}" data-i="${i++}">
        <span>${esc(m.includes("/") ? m.slice(g.length + 1) : m)}</span>${m === getRecommended() ? `<em>推荐</em>` : ""}</div>`,
        )
        .join("")}`,
      )
      .join("");
    ($maybe(".combo-item.act", pop) ?? $maybe(".combo-item.sel", pop))?.scrollIntoView({ block: "nearest" });
  };
  const open = () => {
    filter = "";
    active = -1;
    pop.hidden = false;
    draw();
    input.select(); // 选中现有内容：直接打字就是搜索，不会接在原来的模型名后面
  };
  const pick = (m: string) => {
    input.value = m;
    close();
    input.dispatchEvent(new Event("change"));
  };
  input.addEventListener("focus", open);
  input.addEventListener("click", () => {
    if (pop.hidden) open();
  });
  input.addEventListener("input", () => {
    filter = input.value;
    active = -1;
    pop.hidden = false;
    draw();
  });
  input.addEventListener("keydown", (e) => {
    if (pop.hidden) return;
    if (e.key === "ArrowDown") {
      active = Math.min(items.length - 1, active + 1);
      draw();
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      active = Math.max(0, active - 1);
      draw();
      e.preventDefault();
    } else if (e.key === "Enter" && active >= 0) {
      pick(items[active]);
      e.preventDefault();
    } else if (e.key === "Escape") {
      close();
      e.stopPropagation();
    }
  });
  pop.addEventListener("mousedown", (e) => {
    e.preventDefault(); // 不让输入框先失焦把列表关掉
    const it = (e.target as HTMLElement).closest<HTMLElement>(".combo-item");
    if (it) pick(items[Number(it.dataset.i)]);
  });
  input.addEventListener("blur", () => setTimeout(close, 100));
  return () => {
    if (!pop.hidden) draw();
  };
}

export async function renderSettings(): Promise<void> {
  main().innerHTML = `<div class="page"><div class="page-head"><div><h1>设置</h1><div class="sub">保存在本机配置文件里</div></div></div><div id="sBody"></div></div>`;
  let c: Config;
  let local: LocalProvider[];
  try {
    [c, local] = await Promise.all([
      api<Config>("/api/config"),
      api<LocalProvider[]>("/api/llm/providers").catch(() => []),
      refreshEnv(),
    ]);
  } catch (e) {
    toast(errorMessage(e), true);
    return;
  }
  const env = S.env;
  const presets: Preset[] = [...local.map((p) => ({ id: p.id, name: p.name, base_url: p.base_url, local: p })), ...REMOTE_PRESETS];
  const norm = (u: string) => u.trim().replace(/\/+$/, "").replace("localhost", "127.0.0.1");
  const baseInput = () => $<HTMLInputElement>("#sBase");
  const presetOf = () =>
    (presets.find((p) => p.base_url && norm(baseInput().value) === norm(p.base_url)) ?? presets[presets.length - 1]).id;
  const hot = [...c.hotwords];
  const dot = (p: Preset) =>
    p.local
      ? `<span class="dot" style="background:${p.local.running ? "var(--green)" : "#c9ccd1"};display:inline-block;margin-right:6px;vertical-align:1px" title="${p.local.running ? "运行中" : "未启动"}"></span>`
      : "";
  const keyHint = c.api_key_set
    ? `已设置${c.api_key_hint !== "已设置" ? `（${esc(c.api_key_hint)}）` : ""}，留空表示不修改`
    : "本地 Ollama 不需要";
  const checks: [boolean, string, string][] = [
    [!!env?.ffmpeg, "ffmpeg", env?.ffmpeg ? "已安装" : "brew install ffmpeg"],
    [
      !!env?.system_audio,
      "录系统声音",
      env?.system_audio ? "可以录腾讯会议、飞书里对方的声音" : "需要 macOS 15+ 和 Xcode 命令行工具；不影响导入录音",
    ],
    [!!env?.model_cached, "识别模型", env?.model_loaded ? "已加载" : env?.model_cached ? "已下载" : "首次转写时自动下载约 2 GB"],
  ];

  $("#sBody").innerHTML = `
    <div class="settings">
      <section class="card s-card">
        <h2>大模型</h2>
        <p class="hint">用来把转写整理成纪要。任何 OpenAI 兼容接口都可以。本机的 Magpie、CC Switch 中转打开后可以直接选；选本地 Ollama 则全程不出本机。</p>
        <div class="s-grid">
          <div class="seg" id="sPreset">${presets.map((p) => `<button data-id="${p.id}">${dot(p)}${p.name}</button>`).join("")}</div>
          <div class="s-row2">
            <div class="field"><label>接口地址 Base URL</label><input class="input" id="sBase" value="${esc(c.base_url)}"></div>
            <div class="field"><label>模型 <span id="sModelCount" style="color:var(--muted-2)"></span></label>
              <div class="combo"><input class="input" id="sModel" value="${esc(c.model)}" placeholder="点这里从列表里选，也可以直接输入" autocomplete="off" spellcheck="false"><span class="combo-arrow">${ICON.chevron}</span><div class="combo-pop" id="sModelPop" hidden></div></div></div>
          </div>
          <div class="field"><label>API Key</label><input class="input" id="sKey" type="password" autocomplete="off" placeholder="${keyHint}"></div>
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
        <p class="hint">会议保存在 ${esc(env?.root ?? "")}，配置文件在 ${esc(c.path)}</p>
        <div class="checks">
          ${checks
            .map(
              ([ok, name, note]) =>
                `<div class="check"><span class="dot" style="background:${ok ? "var(--green)" : "var(--amber)"}"></span>${name}<small>${esc(note)}</small></div>`,
            )
            .join("")}
        </div>
      </section>

      <div class="save-bar"><button class="btn primary lg" id="sSave">保存设置</button></div>
    </div>`;

  const model = $<HTMLInputElement>("#sModel");
  const key = $<HTMLInputElement>("#sKey");
  const markPreset = () => $$<HTMLButtonElement>("#sPreset button").forEach((b) => b.classList.toggle("on", b.dataset.id === presetOf()));
  markPreset();

  let modelList: string[] = [];
  let recommended = "";
  let refreshPicker = () => {};
  const setModels = (models: string[], rec = "") => {
    modelList = models;
    recommended = rec;
    $("#sModelCount").textContent = models.length ? `（可选 ${models.length} 个）` : "";
    refreshPicker();
  };
  refreshPicker = bindModelPicker(
    () => modelList,
    () => recommended,
  );
  const msg = (text: string, cls = "") => {
    const el = $("#sTestMsg");
    el.className = "test-msg " + cls;
    el.textContent = text;
  };
  const fetchModels = async () => {
    try {
      const r = await api<{ models: string[] }>("/api/llm/models", {
        json: { base_url: baseInput().value.trim(), model: "", api_key: key.value.trim() || null },
      });
      setModels(r.models);
    } catch {
      setModels([]);
    }
  };
  const current = presets.find((p) => p.id === presetOf());
  if (current?.local) setModels(current.local.models, current.local.recommended);
  else if (current?.id === "custom" && c.base_url) fetchModels(); // 自定义地址：打开设置页时也拉一次模型列表

  $$<HTMLButtonElement>("#sPreset button").forEach(
    (b) =>
      (b.onclick = () => {
        const p = presets.find((x) => x.id === b.dataset.id);
        if (!p) return;
        msg("");
        if (!p.base_url) {
          // 自定义：清空，让用户填自己的地址；哪个预设高亮是按地址判断的，不清空就会跳回原来那个
          baseInput().value = "";
          model.value = "";
          key.value = "";
          setModels([]);
          markPreset();
          baseInput().focus();
          msg("填 OpenAI 兼容接口的地址（一般以 /v1 结尾），填完会自动拉取模型列表");
          return;
        }
        baseInput().value = p.base_url;
        if (p.local) {
          const lp = p.local;
          setModels(lp.models, lp.recommended);
          key.value = lp.api_key; // 本机中转的 key 是固定的，直接填上
          if (!lp.models.includes(model.value)) {
            model.value = (lp.recommended && lp.models.includes(lp.recommended) && lp.recommended) || lp.models[0] || "";
          }
          if (!lp.running) msg(lp.hint, "bad");
          else if (lp.recommended && model.value === lp.recommended) msg(`已选推荐模型 ${lp.recommended}：实测 76 分钟的会 51 秒出纪要`, "ok");
        } else {
          setModels([]);
          key.value = "";
          model.value = p.model ?? "";
          if (!c.api_key_set) msg("需要填写这家服务的 API Key");
        }
        markPreset();
      }),
  );
  baseInput().oninput = markPreset;
  baseInput().onchange = fetchModels;

  const tagInput = $<HTMLInputElement>("#sTagInput");
  const drawTags = () => {
    $$("#sTags .chip").forEach((x) => x.remove());
    hot.forEach((w, i) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `${esc(w)}<button class="x" title="删除">×</button>`;
      $("button", chip).onclick = () => {
        hot.splice(i, 1);
        drawTags();
      };
      tagInput.before(chip);
    });
  };
  drawTags();
  tagInput.onkeydown = (e) => {
    const v = tagInput.value.trim();
    if ((e.key === "Enter" || e.key === "," || e.key === "，") && v) {
      e.preventDefault();
      for (const w of v.split(/[,，、\s]+/)) if (w && !hot.includes(w)) hot.push(w);
      tagInput.value = "";
      drawTags();
    } else if (e.key === "Backspace" && !v && hot.length) {
      hot.pop();
      drawTags();
    }
  };

  $("#sTest").onclick = async () => {
    msg("连接中…");
    try {
      const r = await api<{ ok: boolean; message: string }>("/api/config/test", {
        json: { base_url: baseInput().value.trim(), model: model.value.trim(), api_key: key.value.trim() || null },
      });
      msg(r.message, r.ok ? "ok" : "bad");
    } catch (e) {
      msg(errorMessage(e), "bad");
    }
  };

  $("#sSave").onclick = async () => {
    const pending = tagInput.value.trim();
    if (pending) hot.push(...pending.split(/[,，、\s]+/).filter(Boolean));
    const secret = $<HTMLInputElement>("#sSecret").value.trim();
    try {
      await api<Config>("/api/config", {
        method: "PUT",
        json: {
          base_url: baseInput().value.trim(),
          model: model.value.trim(),
          api_key: key.value.trim() || null,
          hotwords: hot,
          webhook: $<HTMLInputElement>("#sHook").value.trim(),
          secret: secret || null,
        },
      });
      toast("设置已保存");
      refreshEnv();
      renderSettings();
    } catch (e) {
      toast(errorMessage(e), true);
    }
  };
}
