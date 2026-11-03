/* 鱼骨图：纪要里的 ```fishbone 代码块（JSON）渲染成卡片。
   每个分支都绑着原话：大模型给出的时间戳要能在转写里找到对应的话才算数，
   统计每个分支实际引用了几句、多少字——一句都对不上的标"无原文依据"，用来判断这条分支是不是在说空话。 */

import { docIsEn, speakerName, t, tl } from "./i18n";
import { esc, fmt, tsToMs } from "./dom";

export interface FishboneSpec {
  problem: string;
  branches: { title: string; detail?: string; refs?: string[] }[];
}

export interface Quote {
  ms: number;
  speaker: string;
  text: string;
}

/** 时间戳 → 转写里对应的那段话；对不上返回 null。 */
export type Resolver = (ref: string) => Quote | null;

const WEAK_CHARS = 20; // 引用的原文不到这么多字，算"依据很少"

export function parseFishbone(json: string): FishboneSpec | null {
  try {
    const v = JSON.parse(json);
    if (!v || typeof v.problem !== "string" || !Array.isArray(v.branches)) return null;
    return v as FishboneSpec;
  } catch {
    return null;
  }
}

interface Grounding {
  quotes: Quote[];
  chars: number;
  level: "ok" | "weak" | "none";
}

function ground(refs: string[] | undefined, resolve: Resolver): Grounding {
  const seen = new Set<number>();
  const quotes: Quote[] = [];
  for (const r of refs ?? []) {
    const q = resolve(String(r).replace(/[[\]]/g, "").trim());
    if (q && !seen.has(q.ms)) {
      seen.add(q.ms);
      quotes.push(q);
    }
  }
  quotes.sort((a, b) => a.ms - b.ms);
  const chars = quotes.reduce((n, q) => n + [...q.text].length, 0);
  return { quotes, chars, level: !quotes.length ? "none" : chars < WEAK_CHARS ? "weak" : "ok" };
}

function badge(g: Grounding, en?: boolean): string {
  if (g.level === "none") return `<span class="fb-src none">${tl(en, "无原文依据", "No evidence")}</span>`;
  const n = g.quotes.length;
  const label = tl(en, `引用 ${n} 段 · ${g.chars} 字`, `${n} quote${n > 1 ? "s" : ""} · ${g.chars} chars`);
  return `<span class="fb-src ${g.level}">${g.level === "weak" ? tl(en, "依据很少 · ", "Thin evidence · ") : ""}${label}</span>`;
}

/** `en` 指定文案语言（导出 PDF 时跟纪要语言），不传就跟界面语言 */
export function renderFishbone(spec: FishboneSpec, resolve?: Resolver, en?: boolean): string {
  const branches = spec.branches.filter((b) => b && b.title);
  const grounded = resolve ? branches.map((b) => ground(b.refs, resolve)) : null;
  const total = grounded ? grounded.reduce((n, g) => n + g.quotes.length, 0) : 0;
  const empty = grounded ? grounded.filter((g) => g.level === "none").length : 0;
  const summary = grounded
    ? tl(
        en,
        `${branches.length} 个分支 · 引用原话 ${total} 段${empty ? ` · <b>${empty} 个分支无原文依据</b>` : ""}`,
        `${branches.length} branches · ${total} quotes${empty ? ` · <b>${empty} with no evidence</b>` : ""}`,
      )
    : tl(en, `${branches.length} 个分支`, `${branches.length} branches`);
  const cards = branches.map((b, i) => {
    const g = grounded?.[i];
    const quotes = g?.quotes.length
      ? `<div class="fb-quotes" hidden>${g.quotes
          .map(
            (q) =>
              `<div class="fb-quote"><button class="ts" data-ms="${q.ms}">${fmt(q.ms)}</button><span class="fb-who">${esc(speakerName(q.speaker))}</span>${esc(q.text)}</div>`,
          )
          .join("")}</div>`
      : "";
    return `
      <div class="fb-branch ${g ? `lv-${g.level}` : ""} ${i % 2 ? "right" : "left"}" ${g?.quotes.length ? 'role="button" tabindex="0"' : ""}>
        <div class="fb-title">${esc(b.title)}</div>
        ${b.detail ? `<div class="fb-detail">${esc(b.detail)}</div>` : ""}
        ${g ? badge(g, en) : ""}
        ${quotes}
      </div>`;
  });
  return `
    <div class="fishbone">
      <div class="fb-head">
        <svg viewBox="0 0 24 24"><circle cx="5" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="12" r="2.5"/><path d="M7 7l9.5 4M7 17l9.5-4"/></svg>
        <span class="fb-problem">${esc(spec.problem)}</span>
        <button class="fb-draw" title="${t("导出成 .excalidraw 文件，可以在 excalidraw.com 或 Obsidian 里继续改", "Export as an .excalidraw file to keep editing on excalidraw.com or in Obsidian")}">${t("在 Excalidraw 中编辑", "Edit in Excalidraw")}</button>
        <span class="fb-kind">${tl(en, "鱼骨图", "Fishbone")}</span>
      </div>
      <div class="fb-summary">${summary}</div>
      <div class="fb-body">${cards.join("")}<div class="fb-spine"></div></div>
    </div>`;
}

/** 复制纪要时把鱼骨图换成可读的列表（与后端推送时的转换一致）。 */
export function fishboneToText(md: string): string {
  const en = docIsEn(md);
  return md.replace(/```fishbone\s*\n([\s\S]*?)\n```/g, (_, json: string) => {
    const fb = parseFishbone(json);
    if (!fb) return "";
    return [
      `**${fb.problem}**`,
      ...fb.branches.map((b) => {
        const refs = (b.refs ?? []).join("、");
        if (en) return `- ${b.title}${b.detail ? `: ${b.detail}` : ""}${refs ? ` (${refs.replace(/、/g, ", ")})` : " (no evidence)"}`;
        return `- ${b.title}${b.detail ? `：${b.detail}` : ""}${refs ? `（${refs}）` : "（无原文依据）"}`;
      }),
    ].join("\n");
  });
}

/** 详情页：根据转写构造时间戳解析器。时间戳落在某段发言的时间范围里就算引用了这段话。 */
export function turnResolver(turns: { start_ms: number; speaker: string; text: string }[], durationMs: number): Resolver {
  return (ref) => {
    if (!/^\d{1,2}:\d{2}(:\d{2})?$/.test(ref)) return null;
    const ms = tsToMs(ref);
    if (ms > durationMs + 1000) return null;
    let hit = -1;
    for (let i = 0; i < turns.length; i++) {
      if (turns[i].start_ms <= ms + 999) hit = i;
      else break;
    }
    if (hit < 0) return null;
    const t = turns[hit];
    return { ms: t.start_ms, speaker: t.speaker, text: t.text };
  };
}
