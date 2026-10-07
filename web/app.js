"use strict";
/* EmuStart UI — ekran systemów (karuzela), lista gier, ustawienia, okna.
   Sterowanie: pad (Gamepad API), klawiatura, pomocniczo mysz. */

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

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
const hint = (b, text) => `<span class="hint"><span class="btn ${b}">${BTN[b]}</span>${esc(text)}</span>`;
function setHints(list, el = $("hintbar")) { el.innerHTML = list.map(([b, t]) => hint(b, t)).join(""); }

/* ───────────── wejście: pad + klawiatura ───────────── */
const PAD = { 0: "a", 1: "b", 2: "x", 3: "y", 4: "lb", 5: "rb", 6: "lt", 7: "rt", 8: "select", 9: "start", 12: "up", 13: "down", 14: "left", 15: "right" };
const REPEATABLE = new Set(["up", "down", "left", "right", "lb", "rb", "lt", "rt"]);
const held = {};     // akcja → {since, next}

function pollPads(now) {
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

document.addEventListener("keyup", (e) => { const a = KEYS[e.key]; if (a && e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA") release(a); });
document.addEventListener("keydown", (e) => {
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
  if (S.modal === "osk") return oskInput(a);
  if (S.modal === "inst") return instInput(a);
  if (S.modal === "fbrowse") return fbInput(a);
  if (S.modal === "sopt") return soInput(a);
  if (S.modal === "flt") return flInput(a);
  if (S.modal === "gopt") return goptInput(a);
  if (S.screen === "pads") return padsInput(a);
  if (S.screen === "profiles") return profilesInput(a);
  if (S.screen === "systems") return systemsInput(a);
  if (S.screen === "games") return gamesInput(a);
  if (S.screen === "settings") return settingsInput(a);
  if (S.screen === "art") return artInput(a);
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
  for (const id of ["systems", "games", "settings", "art", "pads", "profiles"]) $(id).classList.toggle("hidden", id !== screen);
  if (screen === "systems") renderSystems();
  if (screen === "games") renderGames();
  if (screen === "settings") renderSettings();
  if (screen === "art") renderArt();
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
      `<div class="syscard" data-i="${i}">${s.logo ? `<img src="${s.logo}" alt=""${s.logo_glow ? ' class="glow"' : ""}>` : `<div class="txt">${esc(s.display)}</div>`}</div>`).join("");
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
    el.style.transform = `translate(calc(-50% + ${d * 25}vw), -50%) scale(${ad === 0 ? 1 : 0.55})`;
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
      `<span class="g">${esc(g.tags)}</span>` +
      `<span class="ic">${icons.join("")}</span></div>`);
  }
  box.innerHTML = html.join("");
  box.querySelectorAll(".row").forEach((el) => {
    el.addEventListener("click", () => { S.gameIdx = +el.dataset.i; renderGames(); });
    el.addEventListener("dblclick", () => { S.gameIdx = +el.dataset.i; launch(); });
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
  if (!g) return;
  $("pvTitle").textContent = g.title;
  $("pvTags").textContent = g.tags;
  setArt(g);
  if (!g.box || !g.snap || (S.state?.games_logo && !g.logo && !g.art_logo)) {
    api().request_art([g.id]);
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
  const status = d.copying ? '<span class="st-n">kopiowanie do pamięci podręcznej…</span>'
    : d.pinned && d.cached ? '<span class="st-p">przypięta, lokalnie</span>'
    : d.pinned ? '<span class="st-p">przypięta (pobierze się)</span>'
    : d.cached ? '<span class="st-c">w pamięci podręcznej</span>'
    : '<span class="st-n">na NAS</span>';
  $("pvMeta").innerHTML =
    `<dt>Status</dt><dd>${status}</dd>` +
    `<dt>Rozmiar</dt><dd>${fmtBytes(d.size)}${d.files > 1 ? ` · ${d.files} ${plural(d.files, "plik", "pliki", "plików")}` : ""}</dd>` +
    metaRows(d.meta) +
    `<dt>Ostatnio</dt><dd>${fmtDate(d.last)}</dd>` +
    `<dt>Czas gry</dt><dd>${fmtPlay(d.seconds)}</dd>`;
  $("pvTitle").textContent = d.title;
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
  const useLogo = !!(S.state?.games_logo && g.logo);
  $("pvLogo").classList.toggle("hidden", !useLogo);
  $("pvTitle").classList.toggle("hidden", useLogo);
  if (useLogo && $("pvLogo").getAttribute("src") !== g.logo) { $("pvLogo").src = g.logo; $("pvLogo").alt = title; }
}
function setArt(g) {
  setTitleArt(g, g.title);
  const box = $("pvBox"), snap = $("pvSnap");
  box.classList.toggle("hidden", !g.box);
  $("pvBoxEmpty").classList.toggle("hidden", !!g.box);
  if (g.box && box.getAttribute("src") !== g.box) box.src = g.box;
  snap.classList.toggle("hidden", !g.snap);
  if (g.snap && snap.getAttribute("src") !== g.snap) snap.src = g.snap;
}

async function togglePin() {
  const g = S.games[S.gameIdx];
  const r = await api().toggle_pin(g.id);
  if (!r.ok) return;
  g.pinned = r.pinned ? 1 : 0;
  toast(r.pinned ? `Przypięto: ${g.title}. Zostanie na dysku na stałe.` : `Odpięto: ${g.title}`);
  refreshState();
  renderGames();
}

/* ───────────── uruchamianie ───────────── */
let launchPoll;
async function launch(state = "") {
  const g = S.games[S.gameIdx];
  const r = await api().launch(g.id, state);
  if (r.need_install) return askInstallForGame(r.need_install);
  if (!r.ok) return toast(r.reason);
  S.modal = "launch";
  $("launch").classList.remove("hidden");
  $("lSystem").textContent = "";
  $("lTitle").textContent = g.title;
  $("lHead").textContent = "Przygotowuję…";
  $("lProgress").classList.add("hidden");
  $("lMsg").textContent = "";
  $("lMsg").className = "lmsg";
  $("lHints").innerHTML = "";
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
    downloading: st.mode === "remote" ? "Pobieranie z NAS (połączenie zdalne)"
      : st.mode === "lan" ? "Kopiowanie z NAS" : "Kopiowanie do pamięci podręcznej",
    extracting: "Rozpakowuję do pamięci…",
    running: "Gra uruchomiona",
    finished: "Koniec gry",
    error: "Nie udało się uruchomić gry",
    cancelled: "Pobieranie przerwane",
  }[st.phase] || "";
  $("lHead").textContent = head;
  const showProg = p && (st.phase === "downloading" || (st.phase === "running" && st.copying));
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
  const msg = $("lMsg");
  msg.className = "lmsg" + (st.phase === "error" ? " err" : "");
  if (st.phase === "running") msg.textContent = st.copying ? "Gra działa wprost z NAS, kopia do pamięci podręcznej trwa w tle." : "Miłej gry!";
  else if (st.phase === "downloading" && st.mode === "remote") msg.textContent = "Następnym razem gra uruchomi się od razu z dysku lokalnego.";
  else msg.textContent = st.message || "";

  const hints = [];
  if (st.phase === "downloading") {
    if (st.can_play_now) hints.push(["x", "Graj teraz (z sieci)"]);
    hints.push(["b", "Anuluj"]);
  } else if (["error", "cancelled"].includes(st.phase)) hints.push(["a", "Zamknij"]);
  setHints(hints, $("lHints"));

  if (st.phase === "finished") closeLaunch();
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
  else if (ph === "downloading" && a === "x" && lastLaunch.can_play_now) api().launch_play_now();
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
  if (fromPython && document.hasFocus() && [...(navigator.getGamepads?.() || [])].some((p) => p && p.connected)) return;
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
  const items = [
    ["Ustawienia", () => openSettings()],
    ["Grafiki i metadane", () => openArt()],
    ["Zmień profil", () => openProfiles()],
    ["Kolejność padów", () => openPads()],
    ["Skanuj kolekcję ponownie", () => startScan()],
    ["Wyjdź z EmuStart", () => api().quit()],
  ];
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

/* ───────────── skanowanie ───────────── */
async function startScan(after) {
  const r = await api().rescan();
  if (!r.ok) return toast("Skanowanie już trwa.");
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
const NET = { auto: "Automatycznie (pomiar)", lan: "Zawsze LAN (gra wprost z NAS)", remote: "Zawsze zdalnie (najpierw pobierz)" };

async function openSettings(first) {
  S.settings = await api().get_settings();
  S.setIdx = 0;
  $("setIntro").classList.toggle("hidden", !first);
  $("setTitle").textContent = first ? "Witaj w EmuStart" : "Ustawienia";
  buildSetRows();
  show("settings");
}

function buildSetRows() {
  const c = S.settings;
  const rows = [];
  rows.push({ head: "Foldery z grami  ·  kolejność = pierwszeństwo przy duplikatach" });
  (c.rom_roots || []).forEach((path, i) => rows.push({ k: `Folder ${i + 1}`, type: "root", idx: i, path }));
  rows.push({ k: "Dodaj folder z grami", type: "addroot" });
  rows.push({ head: "Inne foldery" });
  rows.push({ k: "Emulatory", key: "emu_root", type: "path" });
  rows.push({ k: "Pamięć podręczna", key: "cache_dir", type: "path" });
  rows.push({ head: "Pamięć podręczna i sieć" });
  rows.push({ k: "Trzymaj ostatnie gry", key: "cache_recent", type: "num", step: 1, min: 1, max: 100, fmt: (v) => `${v} + przypięte` });
  rows.push({ k: "Tryb sieci", key: "network_mode", type: "enum", opts: Object.keys(NET), fmt: (v) => NET[v] });
  rows.push({ k: "Próg LAN", key: "lan_threshold_mbps", type: "num", step: 50, min: 50, max: 2000, fmt: (v) => `${v} Mb/s` });
  rows.push({ head: "Wygląd" });
  rows.push({ k: "Pełny ekran", key: "fullscreen", type: "bool", fmt: (v) => (v ? "tak" : "nie") + " (po restarcie)" });
  rows.push({ k: "Ukryj klony arcade", key: "hide_arcade_clones", type: "bool", fmt: (v) => (v ? "tak" : "nie") });
  rows.push({ k: "Tytuły gier jako logo", key: "games_logo", type: "bool", fmt: (v) => (v ? "tak (Clear Logo z LaunchBox, gdy jest)" : "nie") });
  rows.push({ head: "Akcje" });
  rows.push({ k: "Wykryj emulatory i skanuj", type: "action", run: async () => { toast("Wykrywam emulatory…", 60000); const r = await api().autodetect(); if (!r.ok) return toast(r.reason, 4000); toast(`Przypisano emulatory: ${r.assigned}`); startScan(async () => { await openSettings(); }); } });
  rows.push({ k: "Pobierz brakujące emulatory", type: "action", run: () => askInstallMissing() });
  rows.push({ k: "Skanuj kolekcję", type: "action", run: () => startScan(async () => { await openSettings(); }) });
  rows.push({ k: "Gotowe, przejdź do gier", type: "action", run: async () => { await refreshState(); show("systems"); } });
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
  if (r.type === "action") return "";
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
  if (["path", "root", "addroot"].includes(r.type)) h.push(["x", "Okno Windows (mysz)"]);
  if (r.type === "root") h.push(["y", "Usuń"]);
  h.push(["b", "Wstecz"]);
  setHints(h);
}

async function saveRoots(roots) {
  await api().save_settings({ rom_roots: roots });
  S.settings = await api().get_settings();
  buildSetRows();
  renderSettings();
  toast("Foldery zapisane. Wybierz „Skanuj kolekcję”, żeby wczytać gry.", 3500);
}

async function saveSetting(key, value) {
  S.settings[key] = value;
  await api().save_settings({ [key]: value });
}

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
  if (ok) {
    await saveSetting(r.key, val);
    if (r.key === "rom_root") { S.settings = await api().get_settings(); buildSetRows(); }
  }
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
  else if (r.type === "root" || r.type === "addroot") {
    const roots = [...(S.settings.rom_roots || [])];
    const at = r.type === "root" ? r.idx : roots.length;
    const put = async (path) => {
      if (!path) return;
      roots[at] = path.trim();
      await saveRoots(roots);
    };
    if (a === "a") return folderBrowse(r.type === "root" ? "Folder z grami" : "Nowy folder z grami", r.path || "", put);
    if (a === "x") return put(await api().pick_folder(r.path || ""));
    if (r.type === "root" && a === "y") { roots.splice(r.idx, 1); return saveRoots(roots); }
    if (r.type === "root" && (a === "left" || a === "right")) {
      const j = r.idx + (a === "left" ? -1 : 1);
      if (j < 0 || j >= roots.length) return;
      [roots[r.idx], roots[j]] = [roots[j], roots[r.idx]];
      S.setIdx += a === "left" ? -1 : 1;
      return saveRoots(roots);
    }
  } else if (r.type === "path") {
    if (a === "a") return folderBrowse(r.k, S.settings[r.key] || "", async (p) => { await saveSetting(r.key, p); renderSettings(); });
    if (a === "y") return startEdit(S.setIdx);      // wpisanie ścieżki klawiaturą — tylko na życzenie
    if (a === "x") {
      const p = await api().pick_folder(S.settings[r.key]);
      if (p) { await saveSetting(r.key, p); if (r.key === "rom_root") { S.settings = await api().get_settings(); buildSetRows(); } }
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

/* ───────────── narzędzie: grafiki ───────────── */
const SRC = { libretro: "libretro (bez klucza)", sgdb: "SteamGridDB", igdb: "IGDB", tgdb: "TheGamesDB" };
const A = { data: null, rows: [], idx: 0, timer: null };

async function openArt() {
  A.data = await api().art_overview();
  buildArtRows();
  show("art");
  clearInterval(A.timer);
  A.timer = setInterval(artTick, 700);
}
function buildArtRows() {
  const d = A.data, rows = [];
  rows.push({ head: "Źródła (kolejność prób)" });
  for (const [k, label] of Object.entries(SRC))
    rows.push({ k: label, v: d.sources[k] ? '<span class="ok">aktywne</span>' : '<span class="no">brak klucza</span>' });
  rows.push({ k: "Importuj klucze z PyLinksWeb", act: "import",
              v: d.keys_from ? esc("z " + d.keys_from) : "SteamGridDB, IGDB, TheGamesDB" });
  const lb = d.launchbox || {};
  rows.push({ head: "Baza LaunchBox (opisy, metadane, grafiki w kategoriach — offline)" });
  rows.push({ k: lb.ready ? "Aktualizuj bazę LaunchBox" : "Pobierz bazę LaunchBox (108 MB)", act: "lb",
              v: lb.ready ? `${lb.games.toLocaleString("pl-PL")} gier · z ${lb.updated}` : "potrzebna do opisów i logo gier" });
  rows.push({ head: "Pobieranie (wszystkie systemy)" });
  const tot = d.systems.reduce((a, s) => ({ g: a.g + s.games, b: a.b + s.box, s: a.s + s.snap, d: a.d + s.desc, l: a.l + s.logo }),
                               { g: 0, b: 0, s: 0, d: 0, l: 0 });
  rows.push({ k: "Pobierz brakujące grafiki", act: "all", mode: "art",
              v: `okładki ${tot.b}/${tot.g} · zrzuty ${tot.s}/${tot.g}` + (d.games_logo ? ` · logo ${tot.l}/${tot.g}` : "") });
  rows.push({ k: "Pobierz brakujące metadane i opisy", act: "all", mode: "meta", v: `opisy ${tot.d}/${tot.g} · LaunchBox + baza RetroArcha` });
  rows.push({ k: "… oraz Wikipedia dla gier bez opisu", act: "all", mode: "meta_wiki", v: "wolniej: ok. 1–2 s na grę" });
  rows.push({ head: "Systemy  ·  A pobierz brakujące grafiki i metadane" });
  for (const s of d.systems)
    rows.push({ k: s.display, act: "sys", es: s.es, mode: "all",
                v: `okładki ${s.box}/${s.games} · zrzuty ${s.snap}/${s.games} · opisy ${s.desc}/${s.games}` +
                   (d.games_logo ? ` · logo ${s.logo}/${s.games}` : "") });
  A.rows = rows;
  if (!A.rows[A.idx] || !A.rows[A.idx].act) A.idx = A.rows.findIndex((r) => r.act);
}
function renderArt() {
  const list = $("artList");
  list.innerHTML = A.rows.map((r, i) => r.head
    ? `<div class="setrow head">${esc(r.head)}</div>`
    : `<div class="setrow${i === A.idx ? " sel" : ""}${r.act ? " action" : ""}" data-i="${i}"><span class="k">${esc(r.k)}</span><span class="v">${r.v}</span><span></span></div>`).join("");
  list.querySelectorAll(".setrow[data-i]").forEach((el) => el.addEventListener("click", () => {
    if (!A.rows[+el.dataset.i].act) return;
    A.idx = +el.dataset.i; renderArt(); artInput("a");
  }));
  list.querySelector(".setrow.sel")?.scrollIntoView({ block: "nearest" });
  const job = A.data?.job;
  setHints(job?.running ? [["a", "Wybierz"], ["x", "Zatrzymaj pobieranie"], ["b", "Wstecz"]] : [["a", "Wybierz"], ["b", "Wstecz"]]);
  renderArtJob(job);
}
function renderArtJob(job) {
  const up = A.data?.lb_update;
  if (up && (up.running || up.error)) {            // pobieranie bazy LaunchBox
    $("artJob").classList.remove("hidden");
    const pct = up.total ? (100 * up.done) / up.total : 0;
    $("ajBar").style.width = (up.total ? pct : 100).toFixed(1) + "%";
    $("ajPct").textContent = up.total ? Math.floor(pct) + "%" : "";
    $("ajLine").textContent = up.error ? `Błąd: ${up.error}` : up.text;
    $("ajCur").textContent = "";
    return;
  }
  $("artJob").classList.toggle("hidden", !job);
  if (!job) return;
  const pct = job.total ? (100 * job.done) / job.total : 100;
  $("ajBar").style.width = pct.toFixed(1) + "%";
  $("ajPct").textContent = Math.floor(pct) + "%";
  const src = Object.entries(job.by_source).map(([k, n]) => `${(SRC[k] || k).split(" ")[0]} ${n}`).join(", ");
  const m = job.mode || { art: true };
  $("ajLine").textContent = `${job.done} / ${job.total} gier` +
    (m.art ? ` · nowe okładki ${job.found.box} · nowe zrzuty ${job.found.snap}` + (job.found.logo ? ` · logo ${job.found.logo}` : "") + ` · bez grafiki ${job.missing}` : "") +
    (m.meta ? ` · nowe opisy ${job.meta.description}` + (m.wiki ? ` · z Wikipedii ${job.meta.wiki}` : "") : "") +
    (src ? ` · źródła: ${src}` : "") +
    (job.running ? ` · zostało ${fmtEta(job.eta)}` : job.cancelled ? " · zatrzymano" : " · gotowe");
  $("ajCur").textContent = job.running ? job.current : "";
}
async function artTick() {
  if (S.screen !== "art") return clearInterval(A.timer);
  if (A.data?.lb_update?.running) {
    const st = await api().launchbox_status();
    A.data.lb_update = st.update;
    if (!st.update.running) { A.data = await api().art_overview(); buildArtRows(); renderArt();
      toast(st.update.error ? `Baza LaunchBox: ${st.update.error}` : "Baza LaunchBox gotowa.", 4000); }
    else renderArtJob(null);
    return;
  }
  const job = await api().art_status();
  const was = A.data.job && A.data.job.running;
  A.data.job = job;
  renderArtJob(job);
  if (job && was && !job.running) { A.data = await api().art_overview(); buildArtRows(); renderArt(); }
}
async function artInput(a) {
  const n = A.rows.length;
  const move = (d) => { let i = A.idx; do { i = (i + d + n) % n; } while (!A.rows[i].act); A.idx = i; };
  if (a === "up") move(-1);
  else if (a === "down") move(1);
  else if (a === "b") { clearInterval(A.timer); await refreshState(); return show("systems"); }
  else if (a === "x") { await api().art_cancel(); toast("Zatrzymuję pobieranie grafik…"); }
  else if (a === "start") return openMenu();
  else if (a === "a") {
    const r = A.rows[A.idx];
    if (r.act === "import") {
      const res = await api().import_pylinks_keys();
      toast(res.ok ? "Zaimportowano klucze z PyLinksWeb." : res.reason, 3500);
    } else if (r.act === "lb") {
      const res = await api().launchbox_update();
      if (!res.ok) toast(res.reason);
    } else {
      const res = await api().art_start(r.act === "sys" ? r.es : "", r.mode || "art");
      if (!res.ok) toast(res.reason);
    }
    A.data = await api().art_overview();
    buildArtRows();
  }
  renderArt();
}

/* ───────────── stan ogólny ───────────── */
async function refreshState() {
  const st = await api().get_state();
  S.state = st;
  const keep = S.systems[S.sysIdx]?.es;
  S.systems = st.systems;
  const i = S.systems.findIndex((s) => s.es === keep);
  S.sysIdx = i >= 0 ? i : Math.min(S.sysIdx, Math.max(0, S.systems.length - 1));
  const net = $("netInfo");
  const mode = { auto: "", lan: " · LAN", remote: " · zdalnie" }[st.network_mode] || "";
  net.textContent = (st.rom_online ? "NAS dostępny" : "NAS offline, tylko gry lokalne") + mode;
  net.className = "pill " + (st.rom_online ? "ok" : "warn");
  S.copying = new Set(st.copying);
  $("profInfo").textContent = st.profile ? "👤 " + st.profile.name : "";
  return st;
}

async function pollCopies() {
  try {
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
  S.pyPad = !!st.py_pad;
  if (S.pyPad) setInterval(pollPyPad, 33);
  setInterval(pollCopies, 1500);
  setInterval(() => { if (!S.modal) refreshState().then(() => S.screen === "systems" && renderSystems()); }, 15000);
  requestAnimationFrame(pollPads);
  window.addEventListener("resize", () => show(S.screen));
  if (!st.configured) openSettings(true);
  else if (st.profiles > 1) openProfiles(true);
  else show("systems");
});

// tryb deweloperski: UI w zwykłej przeglądarce (main.py --browser)
if (new URLSearchParams(location.search).has("dev")) {
  window.pywebview = {
    api: new Proxy({}, {
      get: (_t, name) => (...args) => fetch("/api/" + name, { method: "POST", body: JSON.stringify(args) }).then((r) => r.json()),
    }),
  };
  setTimeout(() => window.dispatchEvent(new Event("pywebviewready")), 0);
}
