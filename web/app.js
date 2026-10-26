"use strict";
/* EmuStart UI — ekran systemów (karuzela), lista gier, ustawienia, okna.
   Sterowanie: pad (Gamepad API), klawiatura, pomocniczo mysz. */

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

// błąd w interfejsie: na ekran i do logu (logs/emustart.log), zamiast cichej, martwej klawiatury
let uiErrors = 0;
function reportUiError(msg) {
  if (uiErrors++ > 20) return;
  let where = "";
  try { where = ` [ekran ${S.screen}, okno ${S.modal || "-"}]`; } catch (e) { /* błąd przed startem UI */ }
  try { if (window.pywebview && window.pywebview.api) window.pywebview.api.ui_log("BŁĄD JS: " + msg + where); } catch (e) { /* most niedostępny */ }
  try { toast("Błąd interfejsu: " + msg, 8000); } catch (e) { /* toast jeszcze nie gotowy */ }
}
window.addEventListener("error", (e) => reportUiError(`${e.message} (${(e.filename || "").split("/").pop()}:${e.lineno})`));
window.addEventListener("unhandledrejection", (e) => {
  const r = e.reason;
  reportUiError(r && r.message ? `${r.message} ${(r.stack || "").split("\n")[1] || ""}`.trim() : String(r));
});

const S = {
  screen: "systems",
  systems: [], sysIdx: 0,
  es: null, games: [], gameIdx: 0,
  settings: null, setRows: [], setIdx: 0, emuOpts: {},
  menu: null, menuIdx: 0,
  modal: null,            // launch | menu | scan | null
  state: null,
  copying: new Set(),
};

/* ───────────── formatowanie ───────────── */
const nf1 = new Intl.NumberFormat("pl-PL", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function fmtBytes(n) {
  if (n == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? String(Math.round(n)) : (n >= 100 ? nf1 : nf2).format(n)) + " " + u[i];
}
function fmtEta(s) {
  if (s == null || !isFinite(s)) return "liczę…";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return `${h} h ${String(m).padStart(2, "0")} min`;
  if (m) return `${m} min ${String(sec).padStart(2, "0")} s`;
  return `${sec} s`;
}
function fmtPlay(sec) {
  if (!sec) return "–";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}
function fmtDate(ts) {
  if (!ts) return "nigdy";
  return new Date(ts * 1000).toLocaleString("pl-PL", { dateStyle: "medium", timeStyle: "short" });
}
function plural(n, one, few, many) {
  const d = n % 10, t = n % 100;
  if (n === 1) return one;
  if (d >= 2 && d <= 4 && (t < 12 || t > 14)) return few;
  return many;
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function toast(text, ms = 2500) {
  const t = $("toast");
  t.textContent = text;
  t.classList.remove("hidden");
  clearTimeout(toast._h);
  toast._h = setTimeout(() => t.classList.add("hidden"), ms);
}

/* ───────────── podpowiedzi przycisków ───────────── */
const BTN = { a: "A", b: "B", x: "X", y: "Y", lb: "LB", rb: "RB", lt: "LT", rt: "RT", start: "Start", select: "Select", dpad: "↔" };
const hint = (b, text) => `<span class="hint" data-a="${b}"><span class="btn ${b}">${BTN[b]}</span>${esc(text)}</span>`;
// podpowiedzi przycisków są klikalne — EmuStart da się obsłużyć samą myszą (np. na NAS-ie bez pada)
document.addEventListener("click", (e) => {
  const h = e.target.closest?.(".hint[data-a]");
  if (!h || h.dataset.a === "dpad") return;
  e.preventDefault();
  press(h.dataset.a, false);
  release(h.dataset.a);
});
function setHints(list, el = $("hintbar")) { el.innerHTML = list.map(([b, t]) => hint(b, t)).join(""); }

/* ───────────── wejście: pad + klawiatura ───────────── */
const PAD = { 0: "a", 1: "b", 2: "x", 3: "y", 4: "lb", 5: "rb", 6: "lt", 7: "rt", 8: "select", 9: "start", 12: "up", 13: "down", 14: "left", 15: "right" };
const REPEATABLE = new Set(["up", "down", "left", "right", "lb", "rb", "lt", "rt"]);
const held = {};     // akcja → {since, next}

function pollPads(now) {
  // Gamepad API tylko w trybie „przeglądarka”. W trybie python (domyślnym) nie
  // wywołujemy go wcale — WebView2 otwiera wtedy pady przez Windows.Gaming.Input,
  // a podejrzewamy, że to psuje sterownik odbiornika Xbox po zamknięciu programu.
  if (S.padBackend !== "browser") return;
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const active = new Set();
  for (const p of pads) {
    if (!p) continue;
    // pady XInput czyta Python (uipad) — Gamepad API gubi je po ponownym podłączeniu
    if (S.pyPad && /xinput/i.test(p.id)) continue;
    p.buttons.forEach((b, i) => { if (PAD[i] && (b.pressed || b.value > 0.5)) active.add(PAD[i]); });
    const [x, y] = [p.axes[0] || 0, p.axes[1] || 0];
    if (y < -0.55) active.add("up");
    if (y > 0.55) active.add("down");
    if (x < -0.55) active.add("left");
    if (x > 0.55) active.add("right");
  }
  for (const a of active) {
    const h = held[a];
    if (!h) {
      held[a] = { since: now, next: now + 380 };
      press(a, true);
    } else if (REPEATABLE.has(a) && now >= h.next) {
      const fast = now - h.since > 1600;
      h.next = now + (fast ? 35 : 75);
      press(a, true);
    }
  }
  for (const a of Object.keys(held)) if (!active.has(a)) { delete held[a]; release(a); }
  if (S.modal === "ingame") {
    if (active.size) IG.quietSince = 0;
    else if (!IG.quietSince) IG.quietSince = now;
    else if (now - IG.quietSince > 150) IG.armed = true;
  }
  requestAnimationFrame(pollPads);
}

const KEYS = {
  ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
  Enter: "a", " ": "a", Escape: "b", Backspace: "b", x: "x", y: "y", p: "y",
  PageUp: "lb", PageDown: "rb", Home: "lt", End: "rt", F2: "start", Tab: "start",
};
// Windows wysyła do okna klawisze pada Xbox (VK_GAMEPAD_*, kody 195–218), a
// WebView2 robi z nich nawigację fokusem po stronie — na krawędzi potrafi
// wyrzucić fokus z okna. Pad obsługujemy sami, więc te klawisze blokujemy.
function isPadKey(e) {
  return (e.keyCode >= 195 && e.keyCode <= 218) || /^Gamepad/i.test(e.key || "") || /^Gamepad/i.test(e.code || "");
}
let padKeyLogged = 0;
for (const type of ["keydown", "keyup", "keypress"]) {
  window.addEventListener(type, (e) => {
    if (!isPadKey(e)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (type === "keydown" && padKeyLogged++ < 5 && window.pywebview) api().ui_log(`klawisz pada zablokowany: key=${e.key} code=${e.code} keyCode=${e.keyCode}`);
  }, true);
}
window.addEventListener("blur", () => { if (window.pywebview && !S.modal?.startsWith?.("launch")) api().ui_log(`okno straciło fokus (ekran ${S.screen}, okno ${S.modal || "-"})`); });

// Pole tekstowe z fokusem, w którym nikt nie pisze (ukryte okno, zamknięta edycja) — fokus
// zostaje w nim w WebView2 i klawiatura „umiera” (pad działa, bo idzie przez Pythona).
let staleLogged = 0;
function staleField(t) {
  if (!t || (t.tagName !== "INPUT" && t.tagName !== "TEXTAREA")) return false;
  const live = t.offsetParent !== null && ((S.modal === "osk" && t === oskField()) || (editing && t.closest("#setList")));
  if (live) return false;
  if (staleLogged++ < 3 && window.pywebview) api().ui_log(`klawiatura: fokus w nieaktywnym polu ${t.tagName}#${t.id || "-"} — zdejmuję`);
  t.blur();
  return true;
}
document.addEventListener("keyup", (e) => {
  const a = KEYS[e.key];
  if (a && (staleField(e.target) || (e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA"))) release(a);
});
document.addEventListener("keydown", (e) => {
  if (staleField(e.target)) { const a = KEYS[e.key]; if (a) { e.preventDefault(); press(a, false); } return; }
  if (S.modal === "osk" && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) {
    if (e.key === "Escape") { e.preventDefault(); oskClose(false); }
    else if (e.key === "Enter" && (!OSK.multiline || e.ctrlKey)) { e.preventDefault(); oskClose(true); }
    return;
  }
  if (e.repeat && KEYS[e.key] === "a") return;      // przytrzymany Enter = opcje gry, nie powtórka
  if (e.target.tagName === "INPUT") {
    if (e.key === "Enter") { e.preventDefault(); commitEdit(true); }
    else if (e.key === "Escape") { e.preventDefault(); commitEdit(false); }
    return;
  }
  const a = KEYS[e.key];
  if (!a) return;
  e.preventDefault();
  press(a, false);  // klawiatura
});
let mouseTimer;
document.addEventListener("mousemove", () => {
  document.body.classList.remove("nomouse");
  clearTimeout(mouseTimer);
  mouseTimer = setTimeout(() => document.body.classList.add("nomouse"), 2500);
});

function press(a, fromPad) {
  document.body.classList.add("nomouse");
  if (S.modal === "ingame") {
    // Menu gry ma dwa źródła pada: to okno (Gamepad API, gdy ma fokus) i wątek
    // XInput w Pythonie. Gdy okno samo widzi pad, akcje z Pythona są pomijane.
    if (!fromPad) return window.ingameInput(a);
    // A+Y z otwarcia menu: naciśnięcia liczą się dopiero, gdy pad był puszczony
    // przez chwilę i minęło pół sekundy od otwarcia (A i Y nie puszcza się
    // równocześnie, a odczyt pada tuż po wyjściu okna na wierzch bywa nieświeży)
    if (!IG.armed || performance.now() - IG.openedAt < 500) return;
    return window.ingameInput(a);
  }
  if (S.modal === "launch") return launchInput(a);
  if (S.modal === "menu") return menuInput(a);
  if (S.modal === "scan") return;
  if (S.modal === "join") return joinInput(a);
  if (S.modal === "osk") return oskInput(a);
  if (S.modal === "inst") return instInput(a);
  if (S.modal === "fbrowse") return fbInput(a);
  if (S.modal === "sopt") return soInput(a);
  if (S.modal === "flt") return flInput(a);
  if (S.modal === "gopt") return goptInput(a);
  if (S.modal === "look") return lookInput(a);
  if (S.screen === "pads") return padsInput(a);
  if (S.screen === "profiles") return profilesInput(a);
  if (S.screen === "systems") return systemsInput(a);
  if (S.screen === "games") return gamesInput(a);
  if (S.screen === "settings") return settingsInput(a);
}

// Przytrzymanie A na grze: krótko = graj, ≥ 0,6 s = opcje gry.
const LONG_PRESS = 600;
let aHold = null;
function release(a) {
  if (a === "a" && aHold) {
    clearTimeout(aHold.timer);
    const fired = aHold.fired;
    aHold = null;
    if (!fired && S.screen === "games" && !S.modal) launch();
    if (!fired && S.screen === "systems" && !S.modal) openSystem();
  }
}

/* ───────────── ekrany ───────────── */
function show(screen) {
  S.screen = screen;
  for (const id of ["systems", "games", "settings", "pads", "profiles"]) $(id).classList.toggle("hidden", id !== screen);
  if (screen === "systems") renderSystems();
  if (screen === "games") renderGames();
  if (screen === "settings") renderSettings();
  if (screen === "pads") renderPads();
  if (screen === "profiles") renderProfiles();
}

/* ── systemy ── */
function renderSystems() {
  const n = S.systems.length;
  $("emptyState").classList.toggle("hidden", n > 0);
  const car = $("carousel");
  if (car.childElementCount !== n) {
    car.innerHTML = S.systems.map((s, i) =>
      `<div class="syscard" data-i="${i}">${s.logo ? `<img src="${esc(s.logo)}" alt=""${s.logo_glow ? ' class="glow"' : ""}>` : `<div class="txt">${esc(s.display)}</div>`}</div>`).join("");
    // logo, które się nie wczytało (np. brak połączenia z serwerem) — nazwa systemu zamiast pustej karty
    car.querySelectorAll(".syscard img").forEach((img) => img.addEventListener("error", () => {
      const s = S.systems[+img.parentElement.dataset.i];
      img.replaceWith(Object.assign(document.createElement("div"), { className: "txt", textContent: s ? s.display : "" }));
    }));
    car.querySelectorAll(".syscard").forEach((el) => el.addEventListener("click", () => {
      const i = +el.dataset.i;
      if (i === S.sysIdx) openSystem(); else { S.sysIdx = i; renderSystems(); }
    }));
  }
  car.querySelectorAll(".syscard").forEach((el, i) => {
    let d = i - S.sysIdx;
    if (d > n / 2) d -= n;          // zawijanie: karuzela jest pętlą
    if (d < -n / 2) d += n;
    const ad = Math.abs(d);
    el.style.transform = `translate(calc(-50% + ${d * lookVal("car_gap")}vw), -50%) scale(${ad === 0 ? 1 : 0.55})`;
    el.style.opacity = ad === 0 ? 1 : ad <= 2 ? 0.45 : 0;
    el.style.filter = ad === 0 ? "none" : "grayscale(.6)";
    el.style.zIndex = 10 - ad;
  });
  const s = S.systems[S.sysIdx];
  $("sysName").textContent = s ? s.display : "";
  $("sysMeta").textContent = s
    ? `${s.games} ${plural(s.games, "gra", "gry", "gier")}` +
      (s.cached ? ` · ${s.cached} w pamięci podręcznej` : "") +
      (s.emulator ? ` · ${s.emulator}` : " · brak emulatora")
    : "";
  setHints(n ? [["dpad", "System"], ["a", "Wybierz (przytrzymaj: opcje)"], ["start", "Menu"]] : [["start", "Menu"]]);
  scheduleSysInfo(s);
}

// opis platformy pod nazwą systemu — z pamięci, pobierany w tle przy pierwszym razie
const SYSINFO = {};
let sysInfoTimer;
function scheduleSysInfo(s) {
  clearTimeout(sysInfoTimer);
  if (!s) return $("sysAbout").classList.add("hidden");
  if (SYSINFO[s.es]) return renderSysInfo(SYSINFO[s.es]);
  $("sysAbout").classList.add("hidden");
  let tries = 0;
  const load = async () => {
    const d = await api().system_info(s.es);
    if (d.pending) { if (++tries < 40) sysInfoTimer = setTimeout(load, 1500); return; }
    SYSINFO[s.es] = d;
    if (S.systems[S.sysIdx]?.es === s.es && S.screen === "systems") renderSysInfo(d);
  };
  sysInfoTimer = setTimeout(load, 250);
}
function renderSysInfo(d) {
  const facts = [];
  if (d.manufacturer) facts.push(`<b>${esc(d.manufacturer)}</b>`);
  if (d.release) facts.push(d.end ? `${esc(d.release)}–${esc(d.end)}` : `premiera ${esc(d.release)}`);
  if (d.media) facts.push(esc(d.media));
  if (d.controllers) facts.push(`pady: ${esc(d.controllers)}`);
  $("sysFacts").innerHTML = facts.join("  ·  ");
  $("sysDesc").textContent = d.description || "";
  $("sysAbout").classList.toggle("hidden", !facts.length && !d.description);
}

function systemsInput(a) {
  const n = S.systems.length;
  if (a === "left" && n) { S.sysIdx = (S.sysIdx - 1 + n) % n; renderSystems(); }
  else if (a === "right" && n) { S.sysIdx = (S.sysIdx + 1) % n; renderSystems(); }
  else if (a === "a" && n) {
    // krótko = wejdź do systemu, przytrzymane = opcje systemu (logo, nazwa, emulator…)
    aHold = { fired: false, timer: setTimeout(() => { if (aHold) { aHold.fired = true; openSystemOptions(); } }, LONG_PRESS) };
  }
  else if (a === "start") openMenu();
}

/* ── lista gier ── */
async function openSystem(keepIdx) {
  const s = S.systems[S.sysIdx];
  if (!s) return;
  const prevId = keepIdx && S.games[S.gameIdx] ? S.games[S.gameIdx].id : null;
  S.es = s.es;
  S.allGames = await api().list_games(s.es);
  if (FL.es !== s.es) flLoad(s.es);
  S.games = flApply(S.allGames);
  S.gameIdx = prevId ? Math.max(0, S.games.findIndex((g) => g.id === prevId)) : 0;
  $("listLogo").src = s.logo || "";
  $("listLogo").classList.toggle("glow", !!s.logo_glow);
  $("listLogo").classList.toggle("hidden", !s.logo);
  $("listTitle").textContent = s.display;
  show("games");
  applyGameFilter();
}

/** Lista gier dociągnięta w tle (telefon: duże systemy porcjami) — na tej samej grze. */
window.__emuGamesUpdated = (es) => {
  if (S.screen === "games" && S.es === es && !S.modal) openSystem(true);
};

function renderGames() {
  const box = $("rows");
  const n = S.games.length;
  const probe = document.createElement("div");
  probe.className = "row"; probe.style.visibility = "hidden";
  box.appendChild(probe);
  const rh = probe.getBoundingClientRect().height || 44;
  probe.remove();
  const vis = Math.max(1, Math.floor(box.clientHeight / rh));
  let top = S.gameIdx - Math.floor(vis / 2);
  top = Math.max(0, Math.min(top, n - vis));
  const html = [];
  for (let i = Math.max(0, top); i < Math.min(n, top + vis); i++) {
    const g = S.games[i];
    const icons = [];
    if (S.copying.has(g.id)) icons.push('<span class="tag d">⬇ kopiuje</span>');
    if (g.pinned) icons.push('<span class="tag p">📌</span>');
    if (g.cached) icons.push('<span class="tag c">lokalnie</span>');
    html.push(`<div class="row${i === S.gameIdx ? " sel" : ""}" data-i="${i}" style="top:${(i - top) * rh}px">` +
      (S.state?.games_logo && g.logo ? `<img class="tlogo" src="${esc(g.logo)}" alt="${esc(g.title)}">` : `<span class="t">${esc(g.title)}</span>`) +
      `<span class="g">${esc(listTags(g.tags))}</span>` +
      `<span class="ic">${icons.join("")}</span></div>`);
  }
  box.innerHTML = html.join("");
  box.querySelectorAll(".row").forEach((el) => {
    el.addEventListener("click", () => { S.gameIdx = +el.dataset.i; renderGames(); });
    el.addEventListener("dblclick", () => { S.gameIdx = +el.dataset.i; launch(); });
    // prawy przycisk myszy / długie dotknięcie — opcje gry (jak przytrzymane A)
    el.addEventListener("contextmenu", (ev) => { ev.preventDefault(); S.gameIdx = +el.dataset.i; renderGames(); openGameOptions(); });
  });
  setHints([["a", "Graj (przytrzymaj: opcje)"], ["x", "Filtry"], ["y", S.games[S.gameIdx]?.pinned ? "Odepnij" : "Przypnij"],
            ["lb", "Strona"], ["lt", "Litera"], ["b", "Wstecz"], ["start", "Menu"]]);
  schedulePreview();
  // okładki dla widocznej strony — w tle
  const page = S.games.slice(Math.max(0, top), top + vis);
  const want = (g) => (!g.box && !g.art_box) || (S.state?.games_logo && !g.logo && !g.art_logo);
  const ids = page.filter(want).map((g) => g.id);
  if (ids.length) { api().request_art(ids); scheduleRowArt(ids); }
}

$("rows").addEventListener("wheel", (e) => { gamesInput(e.deltaY > 0 ? "down" : "up"); e.preventDefault(); }, { passive: false });

// grafiki widocznych wierszy (logo/okładki) dociągane w tle — odśwież po chwili
let rowArtTimer;
function scheduleRowArt(ids) {
  clearTimeout(rowArtTimer);
  rowArtTimer = setTimeout(async () => {
    const res = await api().art_for(ids);
    let changed = false;
    for (const g of S.games) {
      const r = res[g.id];
      if (!r) continue;
      for (const k of ["box", "snap", "logo"]) if (r[k] && g[k] !== r[k]) { g[k] = r[k]; changed = true; }
      if (r.logo) g.art_logo = 1;
    }
    if (changed && S.screen === "games" && !S.modal) renderGames();
  }, 2500);
}

function jumpLetter(dir) {
  const key = (g) => (g.title[0] || "").toUpperCase().replace(/[^A-Z]/, "#");
  const cur = key(S.games[S.gameIdx]);
  let i = S.gameIdx;
  if (dir > 0) { while (i < S.games.length - 1 && key(S.games[i]) === cur) i++; }
  else {
    while (i > 0 && key(S.games[i - 1]) === cur) i--;          // początek bieżącej litery
    if (i === S.gameIdx && i > 0) { i--; const k = key(S.games[i]); while (i > 0 && key(S.games[i - 1]) === k) i--; }
  }
  S.gameIdx = i;
}

// Lista gier: oznaczenia bez dopisku tłumaczenia — „(Japan) [T-En by DeJap v1.2] [b]”
// → „(Japan)”. Pełne oznaczenia są w szczegółach gry (podgląd obok listy).
function listTags(tags) {
  const m = /[\[(]\s*T\s*[-+]\s*[A-Za-z]+/i.exec(tags || "");
  return m ? tags.slice(0, m.index).trim() : (tags || "");
}
function gamesInput(a) {
  const n = S.games.length;
  const page = Math.max(1, Math.floor($("rows").clientHeight / 44) - 1);
  if (a === "up" && n) S.gameIdx = (S.gameIdx - 1 + n) % n;
  else if (a === "down" && n) S.gameIdx = (S.gameIdx + 1) % n;
  else if (a === "lb" || a === "left") S.gameIdx = Math.max(0, S.gameIdx - page);
  else if (a === "rb" || a === "right") S.gameIdx = Math.min(n - 1, S.gameIdx + page);
  else if (a === "lt" && n) jumpLetter(-1);
  else if (a === "rt" && n) jumpLetter(1);
  else if (a === "a" && n) {
    aHold = { fired: false, timer: setTimeout(() => { if (aHold) { aHold.fired = true; openGameOptions(); } }, LONG_PRESS) };
    return;
  }
  else if (a === "y" && n) return togglePin();
  else if (a === "x") return openFilter();
  else if (a === "b") return show("systems");
  else if (a === "start") return openMenu();
  else return;
  renderGames();
}

let pvTimer, artPoll;
function schedulePreview() {
  clearTimeout(pvTimer);
  pvTimer = setTimeout(updatePreview, 90);
}
async function updatePreview() {
  const g = S.games[S.gameIdx];
  clearInterval(artPoll);
  if (!g) {
    // pusta lista (np. jeszcze się wczytuje): bez grafik i opisu poprzedniej gry/platformy
    $("pvTitle").textContent = ""; $("pvTags").textContent = "";
    $("pvMeta").innerHTML = ""; $("pvDesc").innerHTML = "";
    setArt({ title: "" });
    return;
  }
  $("pvTitle").textContent = g.title;
  $("pvTags").textContent = g.tags;
  setArt(g);
  if (!g.box || !g.snap || (!g.logo && !g.art_logo)) {
    api().request_art([g.id], !g.logo && !g.art_logo);   // logo zaznaczonej gry — zawsze
    let tries = 0;
    artPoll = setInterval(async () => {
      const r = (await api().art_for([g.id]))[g.id];
      if (!r || S.games[S.gameIdx] !== g) return clearInterval(artPoll);
      g.box = r.box; g.snap = r.snap;
      if (r.logo) { g.logo = r.logo; g.art_logo = 1; }
      setArt(g);
      if (r.checked || ++tries > 20) clearInterval(artPoll);
    }, 500);
  }
  const d = await api().game_detail(g.id);
  if (S.games[S.gameIdx] !== g || !d.id) return;
  setVariant(g, d);
  const status = d.copying ? '<span class="st-n">kopiowanie do pamięci podręcznej…</span>'
    : d.pinned && d.cached ? '<span class="st-p">przypięta, lokalnie</span>'
    : d.pinned ? '<span class="st-p">przypięta (pobierze się)</span>'
    : d.cached ? '<span class="st-c">w pamięci podręcznej</span>'
    : '<span class="st-n">na serwerze</span>';
  $("pvMeta").innerHTML =
    `<dt>Status</dt><dd>${status}</dd>` +
    `<dt>Rozmiar</dt><dd>${fmtBytes(d.size)}${d.files > 1 ? ` · ${d.files} ${plural(d.files, "plik", "pliki", "plików")}` : ""}</dd>` +
    metaRows(d.meta) +
    `<dt>Ostatnio</dt><dd>${fmtDate(d.last)}</dd>` +
    `<dt>Czas gry</dt><dd>${fmtPlay(d.seconds)}</dd>`;
  $("pvTitle").textContent = d.title + (d.variant_label ? `  ${d.variant_label}` : "");
  renderDesc(d.meta);
  // opis z sieci dla gry, przy której użytkownik się zatrzymał (bez TheGamesDB —
  // ma limit miesięczny; pełne pobranie jest w opcjach gry)
  clearTimeout(metaTimer);
  if (!d.meta_online) metaTimer = setTimeout(async () => {
    if (S.games[S.gameIdx] !== g) return;
    await api().meta_fetch(g.id, false);
    for (const wait of [2500, 5000]) {
      await new Promise((r) => setTimeout(r, wait));
      if (S.games[S.gameIdx] !== g) return;
      const d2 = await api().game_detail(g.id);
      if (d2.meta_online) return updatePreview();
    }
  }, 1200);
}
let metaTimer;
function metaRows(m) {
  const rows = [["Producent", m.developer], ["Wydawca", m.publisher], ["Rok", m.year],
                ["Gatunek", m.genre], ["Gracze", m.players]];
  return rows.filter(([, v]) => v).map(([k, v]) => `<dt>${k}</dt><dd title="${esc(v)}">${esc(v)}</dd>`).join("");
}
function renderDesc(m) {
  const parts = [];
  if (m.description) parts.push(esc(m.description));
  if (m.wiki) parts.push(`<span class="wl">Wikipedia (${esc(m.wiki_lang || "")})</span><br>${esc(m.wiki)}`);
  $("pvDesc").innerHTML = parts.join("<br><br>");
}
function setTitleArt(g, title) {
  // logo zaznaczonej gry: komputer — nad okładką (pvGameLogo), telefon — pod szczegółami (pvMetaLogo)
  for (const id of ["listGameLogo", "pvGameLogo", "pvMetaLogo"]) {
    const el = $(id);
    el.classList.toggle("hidden", !g.logo);
    if (g.logo && el.getAttribute("src") !== g.logo) { el.src = g.logo; el.alt = title; }
  }
}
// Okładka i zrzut: ta sama wysokość (równe górne i dolne krawędzie), ale razem nie
// szersze, niż pozwala miejsce — szczegóły gry pomiędzy nimi mają min. 30% szerokości.
function layoutPreview() {
  const pv = document.querySelector(".preview.pv3");
  if (!pv || !pv.clientWidth) return;
  const ratio = (img, dflt) => (img.classList.contains("hidden") ? dflt
    : img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : dflt);
  if (document.body.classList.contains("phone")) {
    // telefon: okładka i zrzut obok siebie w górnej części, szczegóły zawsze >= 38 % szerokości
    const arBox = ratio($("pvBox"), 0.75);
    const arSnap = $("pvSnap").classList.contains("hidden") ? 0 : ratio($("pvSnap"), 4 / 3);
    const gap = parseFloat(getComputedStyle(pv).columnGap) || 0;
    const share = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--ph-media")) || 56;
    const rowH = pv.clientHeight * share / 100;              // suwak „Wysokość okładki i zrzutu”
    const fit = (pv.clientWidth * 0.62 - gap) / (arBox + arSnap);
    const h = Math.max(60, Math.min(rowH, fit));
    pv.style.setProperty("--ph-h", `${h}px`);
    // szerokości kolumn wprost — bez tego siatka bierze naturalną szerokość pliku obrazka
    pv.style.setProperty("--ph-bw", `${h * arBox}px`);
    pv.style.setProperty("--ph-sw", `${h * arSnap}px`);
    return;
  }
  const css = getComputedStyle(document.documentElement).getPropertyValue("--media-h").trim();
  const mediaH = css.endsWith("vh") ? (parseFloat(css) * window.innerHeight) / 100 : parseFloat(css) || 300;
  const arBox = ratio($("pvBox"), 0.75);                     // bez okładki: miejsce na napis 3:4
  const arSnap = $("pvSnap").classList.contains("hidden") ? 0 : ratio($("pvSnap"), 4 / 3);
  const gap = parseFloat(getComputedStyle(pv).columnGap) || 0;
  const minMeta = Math.max(220, pv.clientWidth * 0.3);
  const fit = (pv.clientWidth - minMeta - 2 * gap) / (arBox + arSnap);
  // wolne miejsce pod opisem — okładka i zrzut rosną (do granicy szerokości), opis zostaje cały
  const rowGap = parseFloat(getComputedStyle(pv).rowGap) || 0;
  const desc = $("pvDesc");
  const descH = desc.textContent.trim() ? desc.scrollHeight : 0;
  const pad = parseFloat(getComputedStyle(pv).paddingTop) || 0;
  const room = pv.clientHeight - pad - (descH ? descH + rowGap : 0);
  pv.style.setProperty("--pv-h", `${Math.max(80, Math.min(mediaH, fit), Math.min(fit, room))}px`);
  placeGameLogo();
}

// logo gry nad okładką: na szerokość okładki, od górnej krawędzi ekranu do okładki
// (położenie względem ekranu — podgląd ucina wszystko, co wychodzi poza jego krawędź)
function placeGameLogo() {
  const el = $("pvGameLogo");
  const box = document.querySelector(".pv3 .box");
  if (!el || !box || document.body.classList.contains("phone")) return;
  requestAnimationFrame(() => {
    const r = box.getBoundingClientRect(), top = 0.006 * window.innerHeight, gap = 0.012 * window.innerHeight;
    Object.assign(el.style, { left: `${r.left}px`, width: `${r.width}px`, top: `${top}px`,
                              height: `${Math.max(24, r.top - gap - top)}px` });
  });
}
$("pvBox").addEventListener("load", layoutPreview);
$("pvSnap").addEventListener("load", layoutPreview);
window.addEventListener("resize", layoutPreview);

function setArt(g) {
  setTitleArt(g, g.title);
  const box = $("pvBox"), snap = $("pvSnap");
  box.classList.toggle("hidden", !g.box);
  $("pvBoxEmpty").classList.toggle("hidden", !!g.box);
  if (g.box && box.getAttribute("src") !== g.box) box.src = g.box;
  snap.classList.toggle("hidden", !g.snap);
  if (g.snap && snap.getAttribute("src") !== g.snap) snap.src = g.snap;
  layoutPreview();
}

// tłumaczenie wybrane w opcjach gry („Wersja”): pobiera się, uruchamia i ma zapisy zamiast oryginału
function setVariant(g, d) { g.vid = d.variant || 0; g.vlabel = d.variant_label || ""; }
function playId(g) { return g.vid || g.id; }

async function togglePin() {
  const g = S.games[S.gameIdx];
  const r = await api().toggle_pin(playId(g));
  if (!r.ok) return;
  g.pinned = r.pinned ? 1 : 0;
  toast(r.pinned ? `Przypięto: ${g.title}. Zostanie na dysku na stałe.` : `Odpięto: ${g.title}`);
  refreshState();
  renderGames();
}

/* ───────────── uruchamianie ───────────── */
let launchPoll, lastNotice = "";
async function launch(state = "", takeover = false, configure = false) {
  const g = S.games[S.gameIdx];
  if (S.modal === "launch") return;
  // okno od razu po A — sprawdzenie konta, BIOS-y i lista plików z serwera trwają chwilę,
  // a wcześniej przez ten czas nic się nie działo
  S.modal = "launch";
  $("launch").classList.remove("hidden");
  $("lSystem").textContent = "";
  $("lTitle").textContent = g.title + (g.vid && g.vlabel ? `  ${g.vlabel}` : "");
  $("lHead").textContent = "Przygotowuję…";
  $("lProgress").classList.add("hidden");
  $("lMsg").textContent = "";
  $("lMsg").className = "lmsg";
  $("lHints").innerHTML = "";
  const hide = () => { S.modal = null; $("launch").classList.add("hidden"); };
  let r;
  try {
    if (g.vid === undefined) setVariant(g, await api().game_detail(g.id));   // A przed wczytaniem podglądu
    r = configure ? await api().configure_emulator(playId(g)) : await api().launch(playId(g), state, takeover);
  } catch (e) { hide(); throw e; }
  if (!r.ok || r.android) hide();
  if (r.need_install) return askInstallForGame(r.need_install);
  if (r.profile_pin) { toast(r.reason, 5000); return openProfiles(true); }
  if (!r.ok) return toast(r.reason);
  if (r.android) return toast(`${r.label}: zmień ustawienia i wróć do EmuStart. Ustawienia telefonu są osobne od komputera.`, 6000);
  if (r.warning) toast(r.warning, 7000);
  clearInterval(launchPoll);
  launchPoll = setInterval(updateLaunch, 250);
}

let lastLaunch = {};
async function updateLaunch() {
  const st = await api().launch_status();
  lastLaunch = st;
  if (st.phase === "idle") return closeLaunch();
  $("lSystem").textContent = st.system || "";
  $("lTitle").textContent = st.title || "";
  const p = st.progress;
  const head = {
    preparing: "Przygotowuję…",
    downloading: S.platform === "android" || st.source ? "Pobieranie z serwera EmuStart" : "Kopiowanie do pamięci podręcznej",
    extracting: "Rozpakowuję do pamięci…",
    running: st.mode === "config" ? "Ustawienia emulatora — zamknij emulator, aby zapisać w profilu" : "Gra uruchomiona",
    finished: st.mode === "config" ? "Ustawienia zapisane w profilu" : "Koniec gry",
    error: "Nie udało się uruchomić gry",
    conflict: "Konflikt zapisów",
    cancelled: "Pobieranie przerwane",
  }[st.phase] || "";
  $("lHead").textContent = head;
  const showProg = p && (st.phase === "downloading" || st.phase === "extracting" || (st.phase === "running" && st.copying));
  $("lProgress").classList.toggle("hidden", !showProg);
  if (showProg) {
    const pct = p.total ? Math.min(100, (p.done / p.total) * 100) : 100;
    $("lBar").style.width = pct.toFixed(1) + "%";
    $("lPct").textContent = Math.floor(pct) + "%";
    $("lDone").textContent = `${fmtBytes(p.done)} / ${fmtBytes(p.total)}`;
    $("lLeft").textContent = fmtBytes(p.left);
    $("lSpeed").textContent = p.speed ? fmtBytes(p.speed) + "/s" : "–";
    $("lEta").textContent = p.left ? fmtEta(p.eta) : "gotowe";
    $("lFiles").textContent = p.files > 1 ? `Plik ${p.file} z ${p.files}` : "";
  }
  if (st.notice && st.notice !== lastNotice) { lastNotice = st.notice; toast(st.notice, 9000); }
  const msg = $("lMsg");
  msg.className = "lmsg" + (st.phase === "error" ? " err" : "");
  if (st.phase === "running") msg.textContent = !st.copying ? "Miłej gry!"
    : "Gra działa w trakcie pobierania — brakujące fragmenty dociągane na bieżąco.";
  else if (st.phase === "downloading" && st.mode === "remote") msg.textContent = "Następnym razem gra uruchomi się od razu z dysku lokalnego.";
  else msg.textContent = st.message && st.message !== head ? st.message : "";   // bez powtórzenia nagłówka

  const hints = [];
  if (st.phase === "downloading") {
    hints.push(["b", "Anuluj"]);
  } else if (st.phase === "conflict") {
    hints.push(["x", "Zostaw z tego urządzenia"], ["y", "Weź z serwera"], ["b", "Pomiń (bez synchronizacji)"]);
  } else if (["error", "cancelled"].includes(st.phase)) {
    if (st.locked_by) hints.push(["y", "Przejmij profil i graj"]);
    if (st.missing_core) hints.push(["y", "Otwórz RetroArch"]);
    hints.push(["a", "Zamknij"]);
  }
  setHints(hints, $("lHints"));

  renderConflict(st);
  if (st.phase === "finished") closeLaunch();
}
const KIND_PL = { saves: "zapisy w grze", states: "stany", memcards: "karta pamięci" };
function renderConflict(st) {
  const box = $("lConflict");
  const c = st.phase === "conflict" ? st.conflict || [] : [];
  box.classList.toggle("hidden", !c.length);
  if (!c.length) return;
  const newest = (k) => Math.max(...c.map((x) => x[k] || 0));
  const kinds = [...new Set(c.map((x) => KIND_PL[x.kind] || x.kind))].join(", ");
  const devs = [...new Set(c.map((x) => x.server_device).filter(Boolean))].join(", ");
  box.innerHTML =
    `<div><b>To urządzenie</b><small>${esc(kinds)}</small><small>zmienione ${esc(fmtDate(newest("local_time")))}</small></div>` +
    `<div><b>Serwer</b><small>${devs ? "z urządzenia " + esc(devs) : "inne urządzenie"}</small>` +
    `<small>zmienione ${esc(fmtDate(newest("server_time")))}</small></div>`;
}
async function closeLaunch() {
  clearInterval(launchPoll);
  await api().launch_dismiss();
  S.modal = null;
  $("launch").classList.add("hidden");
  await refreshState();
  if (S.screen === "games") openSystem(true);
}
function launchInput(a) {
  const ph = lastLaunch.phase;
  if (ph === "downloading" && a === "b") api().launch_cancel();
  else if (ph === "conflict") {
    // druga wersja nie ginie: zostaje w historii na serwerze albo w kopii zapasowej tutaj
    const choice = { x: "local", y: "remote", b: "skip" }[a];
    if (choice) api().launch_choice(choice);
  }
  else if (ph === "error" && a === "y" && lastLaunch.missing_core) {
    // telefon: RetroArch bez rdzenia — otwarcie RetroArcha (Online Updater → Core Downloader)
    const gid = lastLaunch.game_id;
    (async () => { await closeLaunch(); api().configure_emulator(gid); })();
  }
  else if (ph === "error" && a === "y" && lastLaunch.locked_by) {
    // profil zablokowany przez urządzenie, na którym już nie grasz — przejęcie
    (async () => { await closeLaunch(); launch("", true); })();
  }
  else if (["error", "cancelled"].includes(ph) && (a === "a" || a === "b")) closeLaunch();
}

/* ───────────── menu w grze ───────────── */
const IG = { items: [], idx: 0, prevModal: null, armed: false };
window.ingameOpen = (info) => {
  IG.items = info.items;
  IG.idx = 0;
  IG.armed = false;
  IG.quietSince = 0;
  IG.openedAt = performance.now();
  for (const k of Object.keys(held)) delete held[k];   // stan sprzed zminimalizowania
  if (S.modal !== "ingame") IG.prevModal = S.modal;
  S.modal = "ingame";
  $("igTitle").textContent = info.title || "";
  $("igMsg").textContent = info.message || "";
  $("ingame").classList.remove("hidden");
  renderIngame();
};
window.ingameClose = () => {
  if (S.modal !== "ingame") return;
  S.modal = IG.prevModal;
  $("ingame").classList.add("hidden");
};
function renderIngame() {
  $("igItems").innerHTML = IG.items.map((it, i) =>
    `<li class="${i === IG.idx ? "sel" : ""}${it.on ? "" : " off"}" data-i="${i}">${esc(it.label)}` +
    `${it.hint ? `<small>${esc(it.hint)}</small>` : ""}${it.on ? "" : "<small>ten emulator tego nie obsługuje</small>"}</li>`).join("");
  $("igItems").querySelectorAll("li").forEach((li) => li.addEventListener("click", () => { IG.idx = +li.dataset.i; window.ingameInput("a"); }));
  setHints([["a", "Wybierz"], ["b", "Wróć do gry"]], $("igHints"));
}
window.ingameInput = (a, fromPython) => {
  if (S.modal !== "ingame") return;
  // okno z fokusem widzi pad samo — wtedy to samo naciśnięcie z Pythona pomijamy
  if (fromPython && S.padBackend === "browser" && document.hasFocus() && [...(navigator.getGamepads?.() || [])].some((p) => p && p.connected)) return;
  const n = IG.items.length;
  const step = (d) => { let i = IG.idx; for (let k = 0; k < n; k++) { i = (i + d + n) % n; if (IG.items[i].on) break; } IG.idx = i; };
  if (a === "up") step(-1);
  else if (a === "down") step(1);
  else if (a === "b") return api().ingame_action("back");
  else if (a === "a") { const it = IG.items[IG.idx]; if (it.on) api().ingame_action(it.id); return; }
  renderIngame();
};

// Menu w grze odpytujemy, zamiast przyjmować wywołania z Pythona (patrz api.ingame_poll).
let igBusy = false, igSeq = 0;
setInterval(async () => {
  const running = (S.modal === "launch" && lastLaunch.phase === "running") || S.modal === "ingame";
  if (!running || igBusy || !window.pywebview) return;
  igBusy = true;
  try {
    const m = await api().ingame_poll();
    if (m.open && (S.modal !== "ingame" || m.seq !== igSeq)) { igSeq = m.seq; window.ingameOpen(m); }
    else if (!m.open && S.modal === "ingame") window.ingameClose();
    for (const a of m.inputs || []) window.ingameInput(a, true);
  } finally { igBusy = false; }
}, 80);

/* ───────────── menu Start ───────────── */
function openMenu() {
  $("menu").querySelector("h2").textContent = "Menu";
  const items = [
    ["Ustawienia", () => openSettings()],
    ["Wygląd (logo, czcionki, kolory)", () => openLook()],
    ["Zmień profil", () => openProfiles()],
    ["Kolejność padów", () => openPads()],
    ["Odśwież bibliotekę z serwera", () => startScan()],
    ["Aktualizuj EmuStart", () => appUpdate()],
    ["Wyjdź z EmuStart", () => api().quit()],
  ];
  if (S.platform === "android") {
    items.length = 0;
    items.push(["Ustawienia", () => openSettings()], ["Zmień profil", () => openProfiles()],
               ["Wygląd (logo, czcionki, kolory)", () => openLook()],
               ["Odśwież listę z serwera", async () => { await api().rescan(); await refreshState(); show("systems"); }],
               ["Aktualizuj EmuStart", () => appUpdate()],
               ["Wyjdź z EmuStart", () => api().quit()]);
  }
  if (S.screen === "settings") items.shift();
  S.menu = items; S.menuIdx = 0; S.modal = "menu";
  $("menu").classList.remove("hidden");
  renderMenu();
}
function renderMenu() {
  $("menuItems").innerHTML = S.menu.map(([t], i) => `<li class="${i === S.menuIdx ? "sel" : ""}" data-i="${i}">${esc(t)}</li>`).join("");
  $("menuItems").querySelectorAll("li").forEach((li) => li.addEventListener("click", () => { S.menuIdx = +li.dataset.i; menuInput("a"); }));
  setHints([["a", "Wybierz"], ["b", "Zamknij"]]);
}
function closeMenu() { S.modal = null; $("menu").classList.add("hidden"); show(S.screen); }
function menuInput(a) {
  const n = S.menu.length;
  if (a === "up") S.menuIdx = (S.menuIdx - 1 + n) % n;
  else if (a === "down") S.menuIdx = (S.menuIdx + 1) % n;
  else if (a === "b" || a === "start") return closeMenu();
  else if (a === "a") { const fn = S.menu[S.menuIdx][1]; closeMenu(); return fn(); }
  renderMenu();
}

/* ───────────── logowanie na serwerze: kod logowania (albo konto + hasło) + klucz urządzenia (emustart/enroll.py) ───────────── */
const DEFAULT_SERVER = "emustart.emucloud.org";
/** Zaloguj: `text` — link logowania (kod QR / link ze strony serwera: adresy, odcisk certyfikatu,
 *  kod jednorazowy i konto) — od razu; albo adres serwera → kod ze strony serwera / konto i hasło.
 *  Nowe konto zakłada się z zaproszenia (strona serwera), nie w aplikacji. */
function loginStart(text) {
  text = (text || "").trim();
  if (!text) return;
  if (text.startsWith("emustart://")) return loginCode(text, "");
  S.menu = [["Mam kod logowania ze strony serwera", () => setTimeout(() =>
              oskOpen(`Kod logowania (strona ${text} → Pokaż kod logowania)`, "", (c) => { if (c.trim()) loginCode(text, c.trim()); }), 50)],
            ["Mam konto i hasło", () => loginAccount(text)],
            ["Anuluj", () => {}]];
  S.menuIdx = 0; S.modal = "menu";
  $("menu").querySelector("h2").textContent = `Logowanie: ${text} · nowe konto — zaproszenie od administratora, potem kod ze strony https://${text.replace(/^https?:\/\//, "")}`;
  $("menu").classList.remove("hidden");
  renderMenu();
}
async function loginCode(text, code) {
  toast("Łączę z serwerem…", 10000);
  const r = await api().server_enroll(text, "code", "", "", code);
  if (!r.ok) return toast(r.reason, 7000);
  if (r.status === "approved") return loginDone(r.account);
  toast(r.reason || "Serwer nie przyjął kodu.", 7000);
}
function loginAddress() {
  oskOpen("Adres serwera (albo link logowania ze strony serwera)", DEFAULT_SERVER, (v) => loginStart(v));
}
async function loginQr() {
  const s = await api().server_scan();
  if (!s.ok) return toast(s.reason, 5000);
  loginStart(s.text);
}
function loginAccount(text) {
  oskOpen("Nazwa twojego konta", "", (acc) => {
    if (!acc.trim()) return;
    setTimeout(() => askPassword(`Hasło konta „${acc.trim()}”`, async (pw) => {
      toast("Łączę z serwerem…", 10000);
      const r = await api().server_enroll(text, "existing", acc.trim(), pw);
      if (!r.ok) return toast(r.reason, 7000);
      enrollWait(r.code, r.account);
    }), 50);
  });
}
async function loginDone(account) {
  S.settings = await api().get_settings(); await refreshState();
  toast(`Zalogowano — konto „${account}” na tym urządzeniu.`, 6000);
  openProfiles(true);
}
async function doLogout(force) {
  toast("Wysyłam zapisy na serwer…", 3000);
  const r = await api().server_logout(!!force);
  if (r && r.ask) return logoutPendingAsk(r);
  if (!r?.ok) return toast(r?.reason || "Nie udało się wylogować.", 6000);
  S.settings = await api().get_settings(); await refreshState();
  renderSettings(); toast("Wylogowano z serwera. Gry i zapisy na tym urządzeniu zostały.", 6000);
}
/** Zapisy nie doszły na serwer (np. brak połączenia) — wylogowanie dopiero po decyzji gracza. */
function logoutPendingAsk(r) {
  S.menu = [["Spróbuj jeszcze raz", () => doLogout(false)],
            ["Wyloguj mimo to — zapisy zostaną tutaj i wyślą się po zalogowaniu na to samo konto", () => doLogout(true)],
            ["Anuluj", () => {}]];
  S.menuIdx = 0; S.modal = "menu";
  $("menu").querySelector("h2").textContent = `${r.reason} ${(r.games || []).join(", ")}`;
  $("menu").classList.remove("hidden");
  renderMenu();
}
function logoutAsk() {
  S.menu = [["Wyloguj — to urządzenie straci dostęp do serwera", () => doLogout(false)], ["Anuluj", () => {}]];
  S.menuIdx = 1; S.modal = "menu";
  $("menu").querySelector("h2").textContent = "Wylogować z serwera?";
  $("menu").classList.remove("hidden");
  renderMenu();
}
/** Wiersze „Serwer EmuStart” — te same na komputerze i telefonie. */
function serverRows(rows, c) {
  if (c.server_key) {
    rows.push({ k: "Serwer", type: "info", fmt: () => `konto ${c.server_account || "—"}` +
                (c.server_via ? ` · przez: ${c.server_via}` : "") + (S.state?.server?.ok === false && !S.state?.server?.checking ? " · nie odpowiada" : "") });
    rows.push({ k: "Wyloguj z serwera", type: "action", fmt: () => "to urządzenie straci dostęp — zalogujesz się ponownie kontem", run: () => logoutAsk() });
    return;
  }
  const wait = c.enroll ? `czeka na zatwierdzenie na ${String(c.enroll.url || "").replace(/^https?:\/\//, "")} — kod ${c.enroll.code}` : "";
  if (S.platform === "android")
    rows.push({ k: "Zaloguj (kod QR)", type: "action", fmt: () => wait || "strona serwera → Pokaż kod logowania → zeskanuj", run: () => loginQr() });
  rows.push({ k: S.platform === "android" ? "Zaloguj (adres serwera)" : "Zaloguj", type: "action",
              fmt: () => wait || (S.platform === "android" ? `${DEFAULT_SERVER} + kod ze strony` : `${DEFAULT_SERVER} albo link ze strony serwera`),
              run: () => loginAddress() });
}
let enrollTimer = null;
function enrollWait(code, account) {
  $("join").querySelector("h2").textContent = `Konto „${account}” — nowe urządzenie`;
  $("join").querySelector("p").innerHTML =
    "Zatwierdź na innym urządzeniu tego konta (albo administrator). Sprawdź, czy kod się zgadza. " +
    "Szybciej: kod logowania ze strony serwera — bez czekania.";
  $("joinCode").textContent = code;
  const srv = String(S.settings?.enroll?.url || "").replace(/^https?:\/\//, "");
  $("joinText").textContent = (srv ? `Serwer: ${srv}. ` : "") + "Możesz zamknąć to okno albo aplikację — prośba czeka do 24 godzin.";
  S.modal = "join";
  $("join").classList.remove("hidden");
  setHints([["b", "Zamknij (prośba czeka)"], ["x", "Wycofaj prośbę"]]);
  const tick = async () => {
    const p = await api().server_enroll_poll();
    if (p.status === "approved") { enrollStop(); return loginDone(p.account); }
    if (p.status === "denied") { enrollStop(); return toast("Prośba odrzucona.", 6000); }
    if (p.status === "expired" || p.status === "none") { enrollStop(); return toast("Prośba wygasła — wyślij ją ponownie.", 6000); }
  };
  clearInterval(enrollTimer);
  enrollTimer = setInterval(tick, 4000);
  tick();
}
function enrollStop(keepTimer) {
  if (!keepTimer) { clearInterval(enrollTimer); enrollTimer = null; }
  if (S.modal === "join") { S.modal = null; $("join").classList.add("hidden"); show(S.screen); }
}
async function enrollResume() {
  // prośba wysłana wcześniej (aplikacja była zamknięta) — sprawdzenie przy starcie
  const p = await api().server_enroll_poll().catch(() => null);
  if (!p || p.status === "none") return;
  if (p.status === "pending") return enrollWait(p.code, p.account);
  if (p.status === "approved") { await refreshState(); toast(`Zatwierdzono — konto „${p.account}”.`, 6000); }
  else if (p.status === "denied") toast("Prośba o zalogowanie została odrzucona.", 6000);
}
const enrollSeen = new Set();
async function enrollCheck() {
  // prośby, które to urządzenie może zatwierdzić — przy otwarciu aplikacji i co minutę
  const r = await api().enroll_pending().catch(() => null);
  const q = (r?.requests || []).find((x) => !enrollSeen.has(x.id));
  if (!q || S.modal) return;
  enrollSeen.add(q.id);
  const kind = { pc: "komputer", phone: "telefon" }[q.class] || q.class;
  const when = new Date(q.created * 1000).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" });
  const decide = async (allow, games) => {
    const x = await api().enroll_decide(q.id, allow, games);
    toast(x.ok ? (allow ? `Zatwierdzono: ${q.name}` : `Odrzucono: ${q.name}`) : x.reason, 6000);
    setTimeout(enrollCheck, 1500);
  };
  S.menu = [[`Zatwierdź — z grami z serwera`, () => decide(true, true)],
            [`Zatwierdź — tylko zapisy i profil`, () => decide(true, false)],
            ["Odrzuć", () => decide(false, false)], ["Później", () => {}]];
  S.menuIdx = 0; S.modal = "menu";
  $("menu").querySelector("h2").textContent =
    `Nowe urządzenie konta „${q.account}”: ${q.name} (${q.app || "EmuStart"}, ${kind}` +
    `${q.from_account ? `, konto ${q.from_account}` : ""}) · ${q.ip} · ${when} · kod ${q.code}` +
    ((q.replaces || []).length ? ` · zastąpi wcześniejszą rejestrację tego programu na tym sprzęcie (${q.replaces.join(", ")})` : "");
  $("menu").classList.remove("hidden");
  renderMenu();
}

function joinInput(a) {
  if (a === "x") { api().server_enroll_cancel(); enrollStop(); return toast("Prośba wycofana.", 4000); }
  if (a === "b" || a === "start") enrollStop();
}

/* ───────────── aktualizacja EmuStart (menu Start) ───────────── */
let updTimer = null;
async function appUpdate() {
  toast("Sprawdzam wydania na GitHubie…", 4000);
  const r = await api().app_update_check();
  if (!r || !r.ok) return toast(r?.reason || "Nie udało się sprawdzić aktualizacji.", 5000);
  if (!r.newer) return toast(`Masz najnowszą wersję (${r.current}).`, 4000);
  if (!r.available) return toast(r.reason || `Jest wersja ${r.version}, ale wydanie nie ma jeszcze pliku dla tego urządzenia.`, 6000);
  S.menu = [[`Aktualizuj do ${r.version} (${fmtBytes(r.size)})`, () => appUpdateStart()], ["Nie teraz", () => {}]];
  S.menuIdx = 0; S.modal = "menu";
  $("menu").classList.remove("hidden");
  renderMenu();
}
async function appUpdateStart() {
  const r = await api().app_update_start();
  if (!r || !r.ok) return toast(r?.reason || "Nie udało się pobrać aktualizacji.", 5000);
  clearInterval(updTimer);
  updTimer = setInterval(async () => {
    const st = await api().app_update_status();
    if (!st) return;
    if (st.phase === "downloading") return toast(`Pobieram EmuStart ${st.version}: ${fmtBytes(st.done)} / ${fmtBytes(st.total)}`, 2000);
    if (st.phase === "extracting") return toast("Rozpakowuję aktualizację…", 2000);
    clearInterval(updTimer);
    if (st.phase === "error") return toast("Aktualizacja nie powiodła się: " + st.error, 7000);
    if (st.phase !== "ready") return;
    const a = await api().app_update_apply();
    if (!a || !a.ok) return toast(a?.reason || "Nie udało się zainstalować aktualizacji.", 6000);
    toast(S.platform === "android" ? "Potwierdź instalację w oknie Androida."
                                   : `Instaluję ${st.version} — EmuStart uruchomi się ponownie.`, 8000);
  }, 700);
}

/* ───────────── biblioteka z serwera (odświeżenie) ───────────── */
async function startScan(after) {
  const r = await api().rescan();
  if (!r.ok) return toast("Odświeżanie biblioteki już trwa.");
  S.modal = "scan";
  $("scan").classList.remove("hidden");
  const t = setInterval(async () => {
    const st = await api().scan_status();
    $("scanText").textContent = st.text || "";
    $("scanBar").style.width = st.total ? (100 * st.done / st.total).toFixed(0) + "%" : "0";
    if (!st.running) {
      clearInterval(t);
      S.modal = null;
      $("scan").classList.add("hidden");
      const res = st.result || {};
      toast(res.ok ? `Gotowe: ${res.games} gier w ${res.systems} systemach.` : `Błąd: ${res.reason}`, 4000);
      await refreshState();
      if (after) after(); else show(S.screen === "games" ? "systems" : S.screen);
    }
  }, 300);
}

/* ───────────── ustawienia ───────────── */

async function openSettings(first) {
  S.settings = await api().get_settings();
  S.setIdx = 0;
  $("setIntro").classList.toggle("hidden", !first);
  if (S.platform === "android") $("setIntro").textContent =
    "Pierwsze uruchomienie. Zaloguj się na serwerze EmuStart (komputer z grami): kod QR z panelu serwera albo adres serwera.";
  $("setTitle").textContent = first ? "Witaj w EmuStart" : "Ustawienia";
  buildSetRows();
  show("settings");
}

function buildSetRowsAndroid() {
  const c = S.settings;
  const rows = [];
  rows.push({ head: "Serwer EmuStart (komputer z grami)" });
  serverRows(rows, c);
  rows.push({ head: "Zapisy gier  ·  wspólne z EmuStart na komputerze (folder profilu na serwerze)" });
  rows.push({ k: "Profil", type: "action", fmt: () => c.profile || "nie wybrano — A: wybierz", run: () => openProfiles() });
  if (c.pending_saves) rows.push({ k: "Czekają na wysłanie", type: "info", key: "pending_saves", fmt: (v) => `${v} gier (wyślą się, gdy serwer będzie dostępny)` });
  for (const f of c.save_fams || []) {
    rows.push({ k: `Folder: ${f.label}`, key: `save_dir:${f.id}`, type: "path",
                fmt: (v) => (v ? f.found : `auto: ${f.found}`) });
    c[`save_dir:${f.id}`] = f.value;
  }
  rows.push({ head: "Gry w telefonie" });
  rows.push({ k: "Folder gier", key: "games_dir", type: "info", fmt: (v) => v });
  rows.push({ k: "Trzymaj ostatnie gry", key: "cache_recent", type: "num", step: 1, min: 1, max: 100, fmt: (v) => `${v} + przypięte` });
  rows.push({ k: "Pobieranie: strumieni naraz", key: "streams", type: "num", step: 1, min: 1, max: 8, fmt: (v) => `${v}` });
  rows.push({ head: "Wygląd" });
  rows.push({ k: "Edytor wyglądu", type: "action", run: () => openLook() });
  rows.push({ k: "Tytuły gier jako logo", key: "games_logo", type: "bool", fmt: (v) => (v ? "tak" : "nie") });
  rows.push({ head: "Emulatory  ·  ←/→ wybór  ·  ✓ rdzeń RetroArcha już działał na tym telefonie (pobierz brakujące w RetroArchu)" });
  if ((c.systems || []).some((s) => !s.options.length))
    rows.push({ k: "Pobierz brakujące emulatory", type: "action", fmt: () => "APK z oficjalnych wydań / Google Play", run: () => askInstallMissing() });
  for (const s of c.systems || []) {
    rows.push({ k: s.display, key: `emu:${s.es}`, type: "enum", opts: s.options.map((o) => o.id),
                fmt: (v) => (s.options.find((o) => o.id === v) || { label: "brak zainstalowanego emulatora" }).label });
    c[`emu:${s.es}`] = s.emulator || (s.options[0] || {}).id || "";
  }
  S.setRows = rows;
  if (S.setRows[S.setIdx]?.head) S.setIdx = 1;
}

function buildSetRows() {
  if (S.platform === "android") return buildSetRowsAndroid();
  const c = S.settings;
  const rows = [];
  rows.push({ head: "Foldery na tym komputerze  ·  gry, grafiki i BIOS-y są na serwerze" });
  rows.push({ k: "Emulatory", key: "emu_root", type: "path" });
  rows.push({ k: "Pamięć podręczna (pobrane gry)", key: "cache_dir", type: "path" });
  rows.push({ head: "Serwer EmuStart  ·  gry, zapisy i profile przez serwer (połączenie szyfrowane)" });
  serverRows(rows, c);
  rows.push({ head: "Pamięć podręczna i sieć" });
  rows.push({ k: "Trzymaj ostatnie gry", key: "cache_recent", type: "num", step: 1, min: 1, max: 100, fmt: (v) => `${v} + przypięte` });
  rows.push({ k: "Graj w trakcie pobierania", key: "stream_play", type: "bool",
              fmt: (v) => c.winfsp !== "dostępny" ? `niedostępne — ${c.winfsp} (https://winfsp.dev)`
                : v ? "tak — gra startuje od razu, brakujące fragmenty dociągane na bieżąco (WinFsp)" : "nie — najpierw pobranie" });
  rows.push({ k: "…gdy pobieranie potrwa dłużej niż", key: "stream_min_seconds", type: "num", step: 15, min: 0, max: 600,
              fmt: (v) => v ? `${v} s — krótsze pobranie: najpierw cała gra (przy 250 Mb/s ${Math.round(v * 250 / 8 / 1024 * 10) / 10} GB)`
                                : "zawsze w trakcie pobierania" });
  rows.push({ head: "Wygląd" });
  rows.push({ k: "Pełny ekran", key: "fullscreen", type: "bool", fmt: (v) => (v ? "tak" : "nie") + " (po restarcie)" });
  rows.push({ k: "Obsługa padów w menu", key: "pad_backend", type: "enum", opts: ["python", "browser", "none"],
              fmt: (v) => ({ python: "EmuStart / XInput (zalecane)", browser: "przeglądarka (Gamepad API)", none: "wyłączona (tylko klawiatura)" }[v] + " — po restarcie") });
  rows.push({ k: "Tytuły gier jako logo", key: "games_logo", type: "bool", fmt: (v) => (v ? "tak (Clear Logo z LaunchBox, gdy jest)" : "nie") });
  rows.push({ k: "Edytor wyglądu", type: "action", run: () => openLook() });
  rows.push({ head: "Ukrywanie gier  ·  wg oznaczeń w nazwie  ·  osobno dla każdego profilu" });
  const yn = (v) => (v ? "ukryte" : "widoczne");
  rows.push({ k: "Wersje rozwojowe", key: "hide_beta", type: "bool", fmt: (v) => `${yn(v)} — Beta, Proto, Alpha, Debug` });
  rows.push({ k: "Dema i wersje promocyjne", key: "hide_demo", type: "bool", fmt: (v) => `${yn(v)} — Demo, Kiosk, Promo, Sample, Trial` });
  rows.push({ k: "Pirackie i przeróbki", key: "hide_pirate", type: "bool", fmt: (v) => `${yn(v)} — Pirate, hack, bootleg` });
  rows.push({ k: "Nielicencjonowane", key: "hide_unl", type: "bool", fmt: (v) => `${yn(v)} — Unl, Aftermarket, Homebrew` });
  rows.push({ k: "Programy i BIOS-y", key: "hide_program", type: "bool", fmt: (v) => `${yn(v)} — Program, BIOS` });
  rows.push({ k: "Klony arcade", key: "hide_arcade_clones", type: "bool", fmt: (v) => `${yn(v)} — tylko wersja główna gry` });
  rows.push({ head: "Profile" });
  rows.push({ k: "Profil tego komputera", key: "machine_profile", type: "enum", opts: Object.keys(c.profile_names || {}).map(Number),
              fmt: (v) => `${(c.profile_names || {})[v] || "?"} — dostaje save'y i konto RA zastane w emulatorach` });
  rows.push({ k: "Pytaj, kto gra, przy starcie", key: "ask_profile", type: "bool",
              fmt: (v) => (v ? "tak" : "nie (od razu profil tego komputera)") });
  rows.push({ k: "Ustawienia emulatorów osobno dla profilu", key: "profile_settings", type: "bool",
              fmt: (v) => (v ? "tak (RetroArch, DuckStation, PCSX2 — kopia w profilu na serwerze)" : "nie (wspólne)") });
  rows.push({ head: "Akcje" });
  rows.push({ k: "Wykryj emulatory", type: "action", run: async () => { toast("Wykrywam emulatory…", 60000); const r = await api().autodetect(); if (!r.ok) return toast(r.reason, 4000); toast(`Przypisano emulatory: ${r.assigned}`); await openSettings(); } });
  rows.push({ k: "Pobierz brakujące emulatory", type: "action", run: () => askInstallMissing() });
  rows.push({ k: "Odśwież bibliotekę z serwera", type: "action", run: () => startScan(async () => { await openSettings(); }) });
  if (c.is_admin)                                   // klucze PyLinks da się odszyfrować tylko na tym komputerze
    rows.push({ k: "Wyślij klucze źródeł grafik z PyLinks na serwer", type: "action", run: async () => {
      const r = await api().art_keys_push();
      toast(r.ok ? `Klucze na serwerze (${(r.sent || []).length}). Źródła grafik: ${(r.sources || []).join(", ")}.`
        : r.reason || "Serwer nie odpowiada.", 6000);
    } });
  rows.push({ k: "Gotowe, przejdź do gier", type: "action", run: async () => { const st = await refreshState(); if (st.setup_needed) openProfiles("setup"); else show("systems"); } });
  rows.push({ head: "Systemy  ·  ←/→ emulator  ·  A włącz/wyłącz" });
  for (const s of c.systems) rows.push({ type: "system", sys: s });
  S.setRows = rows;
  if (S.setRows[S.setIdx]?.head) S.setIdx = 1;
}

function setValue(r) {
  const c = S.settings;
  if (r.type === "system") return r.sys.enabled ? (r.sys.label || "brak emulatora") : (r.sys.known ? "wyłączony" : "nierozpoznany folder — wyłączony");
  if (r.type === "root") return r.path;
  if (r.type === "addroot") return "A wybierz folder";
  if (r.type === "action") return r.fmt ? r.fmt() : "";
  const v = c[r.key];
  return r.fmt ? r.fmt(v) : v;
}

function renderSettings() {
  const list = $("setList");
  list.innerHTML = S.setRows.map((r, i) => {
    if (r.head) return `<div class="setrow head">${esc(r.head)}</div>`;
    const sel = i === S.setIdx ? " sel" : "";
    if (r.type === "system") {
      const s = r.sys;
      const where = s.folders > 1 ? `, ${s.folders} foldery` : "";
      return `<div class="setrow${sel}${s.enabled ? "" : " off"}" data-i="${i}"><span class="k">${esc(s.display)} <small style="opacity:.6">(${esc(s.es)}${where})</small></span>` +
        `<span class="v">${esc(setValue(r))}</span><span class="arrows">${sel ? "◀ ▶" : ""}</span></div>`;
    }
    const arrows = sel && ["num", "enum", "bool"].includes(r.type) ? "◀ ▶"
      : sel && r.type === "path" ? "A wybierz folder"
      : sel && r.type === "root" ? "◀ ▶ kolejność · Y usuń" : "";
    return `<div class="setrow${sel}${r.type === "action" ? " action" : ""}" data-i="${i}"><span class="k">${esc(r.k)}</span>` +
      `<span class="v" id="sv${i}">${esc(setValue(r))}</span><span class="arrows">${arrows}</span></div>`;
  }).join("");
  list.querySelectorAll(".setrow[data-i]").forEach((el) => el.addEventListener("click", () => {
    S.setIdx = +el.dataset.i; renderSettings(); settingsInput("a");
  }));
  const sel = list.querySelector(".setrow.sel");
  if (sel) sel.scrollIntoView({ block: "nearest" });
  const r = S.setRows[S.setIdx] || {};
  const h = [["dpad", "Zmień"], ["a", r.type === "action" ? "Wykonaj" : ["path", "root", "addroot"].includes(r.type) ? "Wybierz folder" : "Przełącz"]];
  if (["path", "root", "addroot"].includes(r.type))
    h.push(["x", S.platform === "android" ? "Wykrywaj automatycznie" : "Okno Windows (mysz)"]);
  if (r.type === "root") h.push(["y", "Usuń"]);
  h.push(["b", "Wstecz"]);
  setHints(h);
}

async function saveSetting(key, value) {
  S.settings[key] = value;
  await api().save_settings({ [key]: value });
}

/** Emulator systemu na tym komputerze — strzałkami w Ustawieniach (emulatory są lokalne). */
async function changeSystem(r, dir) {
  const s = r.sys;
  if (!S.emuOpts[s.es]) S.emuOpts[s.es] = await api().emulator_options(s.es);
  const opts = S.emuOpts[s.es];
  if (!opts.length) return toast(`Nie znaleziono emulatora dla: ${s.display}`);
  let i = opts.findIndex((o) => o.exe === s.exe && o.args === s.args);
  i = (i + dir + opts.length) % opts.length;
  if (i < 0) i = 0;
  const o = opts[i];
  Object.assign(s, { label: o.label, exe: o.exe, args: o.args, enabled: true });
  await api().save_settings({ systems: { [s.es]: { label: o.label, exe: o.exe, args: o.args, enabled: true } } });
}

let editing = null;
function startEdit(i) {
  const r = S.setRows[i];
  const v = $("sv" + i);
  editing = { i, r };
  v.innerHTML = `<input value="${esc(S.settings[r.key])}">`;
  const inp = v.querySelector("input");
  inp.focus(); inp.select();
}
async function commitEdit(ok) {
  if (!editing) return;
  const { r } = editing;
  const val = document.querySelector("#setList input")?.value ?? "";
  editing = null;
  if (ok) await saveSetting(r.key, val);
  renderSettings();
}

async function settingsInput(a) {
  const n = S.setRows.length;
  const move = (d) => { let i = S.setIdx; do { i = (i + d + n) % n; } while (S.setRows[i].head); S.setIdx = i; };
  const r = S.setRows[S.setIdx];
  if (a === "up") move(-1);
  else if (a === "down") move(1);
  else if (a === "lb") { for (let k = 0; k < 8; k++) move(-1); }
  else if (a === "rb") { for (let k = 0; k < 8; k++) move(1); }
  else if (a === "b") { await refreshState(); return show("systems"); }
  else if (a === "start") return openMenu();
  else if (r.type === "text" && a === "a") {
    return oskOpen(r.k, r.prefill ? r.prefill() : (S.settings[r.key] || ""), async (v) => { await saveSetting(r.key, v.trim()); renderSettings(); }, false, !!r.secret);
  }
  else if (r.type === "path") {
    if (a === "a") return folderBrowse(r.k, S.settings[r.key] || "", async (p) => { await saveSetting(r.key, p); renderSettings(); });
    if (a === "y") return startEdit(S.setIdx);      // wpisanie ścieżki klawiaturą — tylko na życzenie
    if (a === "x" && S.platform === "android") {      // bez okna Windows: wyczyszczenie = wykrywanie automatyczne
      await saveSetting(r.key, ""); S.settings = await api().get_settings(); buildSetRows(); return renderSettings();
    }
    if (a === "x") {
      const p = await api().pick_folder(S.settings[r.key]);
      if (p) await saveSetting(r.key, p);
    }
  } else if (r.type === "num" && (a === "left" || a === "right")) {
    const v = Math.min(r.max, Math.max(r.min, (+S.settings[r.key] || 0) + (a === "left" ? -r.step : r.step)));
    await saveSetting(r.key, v);
  } else if (r.type === "enum" && ["left", "right", "a"].includes(a)) {
    const i = r.opts.indexOf(S.settings[r.key]);
    await saveSetting(r.key, r.opts[(i + (a === "left" ? -1 : 1) + r.opts.length) % r.opts.length]);
  } else if (r.type === "bool" && ["left", "right", "a"].includes(a)) {
    await saveSetting(r.key, !S.settings[r.key]);
  } else if (r.type === "action" && a === "a") {
    return r.run();
  } else if (r.type === "system") {
    if (a === "left" || a === "right") await changeSystem(r, a === "left" ? -1 : 1);
    else if (a === "a") {
      r.sys.enabled = !r.sys.enabled;
      await api().save_settings({ systems: { [r.sys.es]: { enabled: r.sys.enabled } } });
    }
  }
  renderSettings();
}

/* ───────────── stan ogólny ───────────── */
async function refreshState() {
  const st = await api().get_state();
  S.state = st;
  S.platform = st.platform || "windows";
  const keep = S.systems[S.sysIdx]?.es;
  S.systems = st.systems;
  const i = S.systems.findIndex((s) => s.es === keep);
  S.sysIdx = i >= 0 ? i : Math.min(S.sysIdx, Math.max(0, S.systems.length - 1));
  const net = $("netInfo");
  if (st.server) {
    net.textContent = st.server.ok ? `Serwer ${st.server.name || "EmuStart"}`
      : st.server.checking ? "Serwer: łączę…"
      : st.server.key_rejected ? "Klucz urządzenia nieważny" : "Serwer niedostępny";
    if (st.server.key_rejected && !S.keyWarned) {
      S.keyWarned = true;
      const n = st.server.pending_saves || 0;
      toast((st.server.reason || ("Serwer nie przyjmuje tego urządzenia (unieważnione?) — zaloguj się ponownie: " +
            "Ustawienia → Wyloguj z serwera → Zaloguj.")) +
            (n ? ` Zapisy ${n} ${n === 1 ? "gry czekają" : "gier czekają"} na wysłanie — zaloguj się ponownie na to samo konto, wyślą się same.` : ""), 12000);
    }
    net.className = "pill " + (st.server.ok || st.server.checking ? "ok" : "warn");
  } else {
    net.textContent = "Nie zalogowano do serwera";
    net.className = "pill warn";
  }
  S.copying = new Set(st.copying);
  $("profInfo").textContent = st.profile ? "👤 " + st.profile.name : "";
  if (st.conflicts?.length) {
    const c = st.conflicts[0];
    toast(`Konflikt save'ów (${c.profile}): ${c.file}${st.conflicts.length > 1 ? ` i ${st.conflicts.length - 1} innych` : ""} — ` +
          "grano na dwóch urządzeniach bez połączenia z serwerem. Została nowsza wersja, druga jest w kopii zapasowej profilu.", 10000);
  }
  return st;
}

let libVersion = null;
async function pollCopies() {
  try {
    // biblioteka z serwera zmieniła się w tle (grafiki, opisy, logo) — lista na nowo
    const lv = S.state?.lib_version;
    if (libVersion !== null && lv !== undefined && lv !== libVersion && !S.modal) {
      await refreshState();
      if (S.screen === "systems") renderSystems();
      else if (S.screen === "games") openSystem(true);
    }
    if (lv !== undefined) libVersion = S.state.lib_version;
    profilesRecheck();
    if (S.platform !== "android") {                 // emulatory z serwera (w tle, emustart/installer.py)
      const e = await api().emu_sync_status();
      const on = e && e.running && e.current;
      $("emuInfo").textContent = on ? `⬇ emulatory: ${e.current}` + (e.total ? ` · ${Math.floor((100 * e.done) / e.total)}%` : "") +
        (e.steps > 1 ? ` (${e.step}/${e.steps})` : "") : "";
      $("emuInfo").classList.toggle("hidden", !on);
    }
    const post = await api().post_status();
    $("syncInfo").textContent = post ? `⇅ wysyłam zapisy: ${post}` : "";
    $("syncInfo").classList.toggle("hidden", !post);
    const list = await api().copy_status();
    const el = $("copyInfo");
    if (list.length) {
      const c = list[0];
      const pct = c.total ? Math.floor((100 * c.done) / c.total) : 100;
      el.textContent = `⬇ ${c.title} · ${pct}% · ${fmtEta(c.eta)}` + (list.length > 1 ? ` (+${list.length - 1})` : "");
      el.classList.remove("hidden");
    } else el.classList.add("hidden");
    const ids = new Set(list.map((c) => c.game_id));
    const changed = ids.size !== S.copying.size || [...ids].some((x) => !S.copying.has(x));
    S.copying = ids;
    if (changed && S.screen === "games" && !S.modal) openSystem(true);
  } catch (e) { /* most jeszcze niegotowy */ }
}

function tick() {
  $("clock").textContent = new Date().toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
}

// zdarzenia padów XInput z Pythona (patrz emustart/uipad.py)
let pyPadBusy = false;
async function pollPyPad() {
  if (pyPadBusy) return;
  pyPadBusy = true;
  try {
    for (const ev of await api().ui_pad_poll()) {
      if (ev.up) release(ev.a);
      else press(ev.a, true);
    }
  } catch (e) { /* most chwilowo niedostępny */ }
  finally { pyPadBusy = false; }
}

window.addEventListener("pywebviewready", async () => {
  tick(); setInterval(tick, 10000);
  const st = await refreshState();
  applyLook(st.look || {});
  S.pyPad = !!st.py_pad;
  S.padBackend = st.pad_backend || "browser";
  if (S.pyPad) setInterval(pollPyPad, 33);
  setInterval(pollCopies, 1500);
  setInterval(() => { if (!S.modal) refreshState().then(() => S.screen === "systems" && renderSystems()); }, 15000);
  requestAnimationFrame(pollPads);
  window.addEventListener("resize", () => show(S.screen));
  if (!st.configured) openSettings(true);
  else if (st.setup_needed) openProfiles("setup");      // nowy komputer: kto tu gra
  else if (st.ask_profile) openProfiles(true);
  else show("systems");
  enrollResume();
  setTimeout(enrollCheck, 4000);
  setInterval(() => { if (!S.modal) enrollCheck(); }, 60000);
  // profil zablokowany na tym urządzeniu (PIN zmieniony gdzie indziej / wylogowany)
  api().profile_pin_check().then((pc) => {
    if (pc && pc.state && pc.state !== "ok" && S.screen === "systems") {
      toast(pc.state === "need_pin" ? `Profil „${pc.name}” — podaj PIN.`
        : pc.state === "need_password" ? `Konto „${pc.name}” — podaj hasło.` : `Ustaw hasło konta „${pc.name}”.`, 5000);
      openProfiles(true);
    }
  }).catch(() => {});
});

// tryb deweloperski: UI w zwykłej przeglądarce (main.py --browser)
/** Start interfejsu (pywebviewready) dopiero po wykonaniu wszystkich skryptów strony
 *  (app.js, options.js, look.js). setTimeout(0) potrafił wyprzedzić look.js — na Androidzie
 *  pliki przychodzą asynchronicznie i start padał na „applyLook is not defined”: zostawało
 *  samo tło z paskiem. */
function uiReady() {
  const go = () => window.dispatchEvent(new Event("pywebviewready"));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go, { once: true });
  else setTimeout(go, 0);
}

if (new URLSearchParams(location.search).has("dev")) {
  window.pywebview = {
    api: new Proxy({}, {
      get: (_t, name) => (...args) => fetch("/api/" + name, { method: "POST", headers: { "X-EmuStart": "1" },
                                                                 body: JSON.stringify(args) }).then((r) => r.json()),
    }),
  };
  uiReady();
}


// aplikacja na Androida: most do Kotlina (EmuAndroid) zamiast pywebview
// układ dla telefonu (app.css: body.phone) — aplikacja na Androida; ?phone — podgląd w przeglądarce
if (["android", "phone"].some((k) => new URLSearchParams(location.search).has(k))) document.body.classList.add("phone");

if (new URLSearchParams(location.search).has("android") && window.EmuAndroid) {
  const pending = new Map();
  let seq = 0;
  window.__emuResolve = (id, json) => {
    const p = pending.get(id);
    if (p) { pending.delete(id); p(json === undefined ? null : JSON.parse(json)); }
  };
  window.pywebview = {
    api: new Proxy({}, {
      get: (_t, name) => (...args) => new Promise((res) => {
        const id = ++seq;
        pending.set(id, res);
        window.EmuAndroid.call(id, String(name), JSON.stringify(args));
      }),
    }),
  };
  // pad (GameSir itp.) obsługuje aktywność Androida: wciśnięcie / puszczenie
  window.__emuPad = (a, up) => (up ? release(a) : press(a, true));
  // powrót z emulatora do EmuStart
  window.__emuResumed = () => { if (S.modal === "launch") updateLaunch(); };
  uiReady();
}
