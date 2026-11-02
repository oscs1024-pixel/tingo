import { $$, main } from "./dom";
import { S } from "./state";
import { renderDetail, stopAudio } from "./views/detail";
import { renderHome } from "./views/home";
import { renderMeetings } from "./views/meetings";
import { renderSettings } from "./views/settings";

export function route(): void {
  const h = decodeURIComponent(location.hash.slice(1) || "/");
  stopAudio();
  S.detail = null;
  let nav = "home";
  if (h.startsWith("/m/")) {
    nav = "meetings";
    renderDetail(h.slice(3));
  } else if (h === "/meetings") {
    nav = "meetings";
    renderMeetings();
  } else if (h === "/settings") {
    nav = "settings";
    renderSettings();
  } else {
    renderHome();
  }
  $$<HTMLAnchorElement>(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
  $$<HTMLAnchorElement>("#recentList a").forEach((a) => a.classList.toggle("active", h === `/m/${a.dataset.id}`));
  main().scrollTop = 0;
}
