/* 纪要渲染：只覆盖纪要用到的 Markdown 子集（标题、列表、表格、粗体、行内代码），时间戳变成可点的按钮。 */

import { esc, tsToMs } from "./dom";

const TS = String.raw`\d{1,2}:\d{2}(?::\d{2})?`;

function inline(s: string): string {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(new RegExp(String.raw`\s*[（(]\s*\[(${TS})\]\s*[）)]`, "g"), "[$1]")
    .replace(new RegExp(String.raw`\[(${TS})\]`, "g"), (_, t: string) => `<button class="ts" data-ms="${tsToMs(t)}">${t}</button>`);
}

const LIST = /^\s*([-*]|\d+\.)\s+/;

export function renderMarkdown(md: string, { skipHead = false } = {}): string {
  const lines = md.replace(/\r/g, "").split("\n");
  const out: string[] = [];
  let i = 0;
  let afterH1 = false;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      // 详情页的标题和"日期/时长/参会人"已经在页头显示，跳过
      if (!(skipHead && h[1].length === 1)) out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);
      afterH1 = h[1].length === 1;
      i++;
      continue;
    }
    if (line.trim().startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++]);
      const cells = (r: string) =>
        r
          .trim()
          .replace(/^\||\|$/g, "")
          .split("|")
          .map((c) => c.trim());
      const [head, ...rest] = rows.filter((r, k) => !(k === 1 && /^[\s|:-]+$/.test(r)));
      out.push(
        `<table><thead><tr>${cells(head)
          .map((c) => `<th>${inline(c)}</th>`)
          .join("")}</tr></thead><tbody>${rest
          .map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`)
          .join("")}</tbody></table>`,
      );
      afterH1 = false;
      continue;
    }
    if (LIST.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && LIST.test(lines[i])) items.push(lines[i++].replace(LIST, ""));
      const meta = afterH1 && items.some((t) => t.startsWith("日期："));
      const tag = ordered ? "ol" : "ul";
      if (!(skipHead && meta)) out.push(`<${tag}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${tag}>`);
      afterH1 = false;
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|\s*\||\s*([-*]|\d+\.)\s)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${inline(para.join(" "))}</p>`);
    afterH1 = false;
  }
  return out.join("\n");
}
