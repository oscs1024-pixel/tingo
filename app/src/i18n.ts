/* 界面语言：中文 / English。默认跟系统语言，设置里可以改（存在配置文件 [ui] language）。
   文案就地写成 t("中文", "English")，读代码时两种语言一眼都能看到；
   以后加别的语言，可以按中文原文做键补一张对照表。 */

export type Lang = "zh" | "en";
export type LangPref = "auto" | Lang;

const KEY = "tingo.lang"; // 记住上次的语言，启动时先用它渲染，避免先闪一下中文

const systemLang = (): Lang => (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";

function initial(): Lang {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "zh" || v === "en") return v;
  } catch {
    /* 无痕模式等拿不到 localStorage */
  }
  return systemLang();
}

let current: Lang = initial();

export const lang = (): Lang => current;
export const isEn = (): boolean => current === "en";
export const t = (zh: string, en: string): string => (current === "en" ? en : zh);

/** 按设置决定语言，返回是否变了 */
export function applyLang(pref: LangPref | undefined): boolean {
  const next = pref === "zh" || pref === "en" ? pref : systemLang();
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* 同上 */
  }
  const changed = next !== current;
  current = next;
  document.documentElement.lang = next === "zh" ? "zh-CN" : "en";
  return changed;
}

/** 没改过名的说话人（后端存的是"说话人N"），英文界面显示成 Speaker N */
export const speakerName = (name: string): string => {
  const m = /^说话人(\d+)$/.exec(name);
  return m && current === "en" ? `Speaker ${m[1]}` : name;
};

/** 纪要本身是哪种语言（英文纪要有 "## Summary" 这一节）。导出的文件跟纪要语言走，不跟界面语言 */
export const docIsEn = (md: string): boolean => /^##\s+Summary\b/m.test(md);

/** 按指定语言取文案：en 为 undefined 时跟界面语言 */
export const tl = (en: boolean | undefined, zh: string, enText: string): string => (en === undefined ? t(zh, enText) : en ? enText : zh);

/** 日期、数字等的区域设置 */
export const locale = (): string => (current === "en" ? "en-US" : "zh-CN");

/** 页面里写死的文字（侧边栏、拖拽提示）：data-t="中文|English" */
export function renderShell(): void {
  document.querySelectorAll<HTMLElement>("[data-t]").forEach((el) => {
    const [zh, en] = (el.dataset.t ?? "").split("|");
    el.textContent = t(zh, en ?? zh);
  });
}
