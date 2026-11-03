/* 通用小工具：取元素、转义、时间格式、图标、提示和弹窗。 */

export function $<T extends HTMLElement = HTMLElement>(selector: string, root: ParentNode = document): T {
  const el = root.querySelector<T>(selector);
  if (!el) throw new Error(`missing element ${selector}`);
  return el;
}

export function $maybe<T extends HTMLElement = HTMLElement>(selector: string, root: ParentNode = document): T | null {
  return root.querySelector<T>(selector);
}

export function $$<T extends HTMLElement = HTMLElement>(selector: string, root: ParentNode = document): T[] {
  return [...root.querySelectorAll<T>(selector)];
}

export const main = (): HTMLElement => $("#main");

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s: unknown): string => String(s ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);

export function fmt(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function tsToMs(t: string): number {
  const p = t.split(":").map(Number);
  return (p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1]) * 1000;
}

const COLORS = ["#1F4BFF", "#12B3A8", "#F79009", "#E04F9C", "#7A5AF8", "#0BA5EC", "#66C61C", "#EF6820"];
export const color = (i: number): string => COLORS[i % COLORS.length];

/** 头像上的字："说话人3" 显示 3，真名显示第一个字。 */
export function initial(name: string): string {
  const m = /^说话人(\d+)$/.exec(name);
  return m ? m[1] : [...name][0] || "?";
}

export const ICON = {
  chevron: '<svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="m15 6-6 6 6 6"/></svg>',
  monitor: '<svg viewBox="0 0 24 24"><rect x="3" y="4.5" width="18" height="12" rx="2"/><path d="M8.5 20h7M12 16.5V20"/></svg>',
  mic: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></svg>',
  upload: '<svg viewBox="0 0 24 24"><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/></svg>',
  globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.3 2.4 3.5 5.2 3.5 8.5s-1.2 6.1-3.5 8.5c-2.3-2.4-3.5-5.2-3.5-8.5S9.7 5.9 12 3.5z"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.4h6a1.5 1.5 0 0 0 1.5-1.4l1-12.5M10 11v6M14 11v6"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5M9 13h6M9 16.5h4"/></svg>',
  spark:
    '<svg viewBox="0 0 24 24"><path d="M12 3.5c.6 3.8 2.7 5.9 6.5 6.5-3.8.6-5.9 2.7-6.5 6.5-.6-3.8-2.7-5.9-6.5-6.5 3.8-.6 5.9-2.7 6.5-6.5z"/><path d="M18.5 16.5c.25 1.4 1 2.15 2.5 2.5-1.5.35-2.25 1.1-2.5 2.5-.25-1.4-1-2.15-2.5-2.5 1.5-.35 2.25-1.1 2.5-2.5z"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M20.5 3.5 10 14M20.5 3.5 14 20.5l-4-6.5-6.5-4z"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></svg>',
  redo: '<svg viewBox="0 0 24 24"><path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>',
  pause:
    '<svg viewBox="0 0 24 24"><rect x="6.5" y="5" width="4" height="14" rx="1.2"/><rect x="13.5" y="5" width="4" height="14" rx="1.2"/></svg>',
  people:
    '<svg viewBox="0 0 24 24"><circle cx="9" cy="8.5" r="3.2"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0M16 5.6a3.2 3.2 0 0 1 0 5.8M18 13.8c1.6.8 2.6 2.6 2.6 5.2"/></svg>',
  vault:
    '<svg viewBox="0 0 24 24"><path d="M9.5 3 17 6.5l2 8.5-6 6.5-6.5-4L5 9.5z"/><path d="M9.5 3 12 13.5l7 1.5M12 13.5l-5.5 4"/></svg>',
  draw: '<svg viewBox="0 0 24 24"><path d="M3.5 19.5c2.5-.5 3.5-3 6-3s3 2.5 5.5 2"/><path d="M14 5.5 18.5 10M16 3.5a2.1 2.1 0 0 1 3 3L10 15.5H7v-3z"/></svg>',
  bot: '<svg viewBox="0 0 24 24"><path d="M6 4.5h12A2.5 2.5 0 0 1 20.5 7v7.5A2.5 2.5 0 0 1 18 17h-6.5l-4.5 3.5V17H6a2.5 2.5 0 0 1-2.5-2.5V7A2.5 2.5 0 0 1 6 4.5z"/><path d="M12 7.6c.35 1.65 1.25 2.55 2.9 2.9-1.65.35-2.55 1.25-2.9 2.9-.35-1.65-1.25-2.55-2.9-2.9 1.65-.35 2.55-1.25 2.9-2.9z"/></svg>',
} as const;

let toastTimer: number | undefined;
export function toast(msg: string, error = false): void {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", error);
  t.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => t.classList.remove("show"), error ? 4200 : 2400);
}

export const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export function openModal(html: string): void {
  $("#modalBody").innerHTML = html;
  $("#modal").hidden = false;
  $$("[data-close]", $("#modal")).forEach((b) => (b.onclick = closeModal));
  $maybe<HTMLInputElement>("#modalBody input")?.focus();
}

export function closeModal(): void {
  $("#modal").hidden = true;
}

export function setupModal(): void {
  $("#modal").addEventListener("mousedown", (e) => {
    if ((e.target as HTMLElement).id === "modal") closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });
}
