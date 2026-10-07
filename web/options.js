"use strict";
/* EmuStart UI, część 2: opcje gry (przytrzymane A), klawiatura ekranowa,
   wybór grafik, kolejność padów, profile. Korzysta z globali z app.js. */

/* ───────────── klawiatura ekranowa ───────────── */
const OSK = { value: "", onDone: null, r: 1, c: 0, shift: false, multiline: false, prevModal: null };
const OSK_ROWS = [
  [..."1234567890"], [..."qwertyuiop"], [..."asdfghjkl'"], [..."zxcvbnm,.-"],
  [..."ąćęłńóśźż:"], ["⇧", "Spacja", "⌫", "OK"],
];

function oskOpen(title, value, onDone, multiline = false) {
  Object.assign(OSK, { value: value || "", onDone, r: 1, c: 0, shift: false, multiline, prevModal: S.modal });
  S.modal = "osk";
  $("oskTitle").textContent = title;
  $("oskInput").classList.toggle("hidden", multiline);
  $("oskArea").classList.toggle("hidden", !multiline);
  const f = oskField();
  f.value = OSK.value;
  $("osk").classList.remove("hidden");
  renderOsk();
  setTimeout(() => { f.focus(); f.setSelectionRange(f.value.length, f.value.length); }, 30);
}
function oskField() { return OSK.multiline ? $("oskArea") : $("oskInput"); }
function oskClose(ok) {
  const val = oskField().value;
  $("osk").classList.add("hidden");
  S.modal = OSK.prevModal;
  oskField().blur();
  if (ok && OSK.onDone) OSK.onDone(val);
}
function renderOsk() {
  $("oskKeys").innerHTML = OSK_ROWS.map((row, r) => `<div class="oskrow">` + row.map((k, c) => {
    const label = k.length === 1 && OSK.shift ? k.toUpperCase() : k;
    const wide = k.length > 1 ? " wide" : "";
    return `<span class="oskkey${wide}${r === OSK.r && c === OSK.c ? " sel" : ""}" data-r="${r}" data-c="${c}">${esc(label)}</span>`;
  }).join("") + `</div>`).join("");
  $("oskKeys").querySelectorAll(".oskkey").forEach((el) => el.addEventListener("click", () => {
    OSK.r = +el.dataset.r; OSK.c = +el.dataset.c; oskInput("a");
  }));
  setHints([["a", "Wpisz"], ["x", "Usuń znak"], ["y", "Spacja"], ["start", "Gotowe"], ["b", "Anuluj"]], $("oskHints"));
}
function oskType(text) {
  const f = oskField();
  const s = f.selectionStart ?? f.value.length, e = f.selectionEnd ?? f.value.length;
  f.value = f.value.slice(0, s) + text + f.value.slice(e);
  f.setSelectionRange(s + text.length, s + text.length);
}
function oskBack() {
  const f = oskField();
  const s = f.selectionStart ?? f.value.length, e = f.selectionEnd ?? f.value.length;
  if (s !== e) { f.value = f.value.slice(0, s) + f.value.slice(e); f.setSelectionRange(s, s); }
  else if (s > 0) { f.value = f.value.slice(0, s - 1) + f.value.slice(s); f.setSelectionRange(s - 1, s - 1); }
}
function oskInput(a) {
  const row = OSK_ROWS[OSK.r];
  if (a === "up") { OSK.r = (OSK.r - 1 + OSK_ROWS.length) % OSK_ROWS.length; OSK.c = Math.min(OSK.c, OSK_ROWS[OSK.r].length - 1); }
  else if (a === "down") { OSK.r = (OSK.r + 1) % OSK_ROWS.length; OSK.c = Math.min(OSK.c, OSK_ROWS[OSK.r].length - 1); }
  else if (a === "left") OSK.c = (OSK.c - 1 + row.length) % row.length;
  else if (a === "right") OSK.c = (OSK.c + 1) % row.length;
  else if (a === "x") oskBack();
  else if (a === "y") oskType(" ");
  else if (a === "b") return oskClose(false);
  else if (a === "start") return oskClose(true);
  else if (a === "a") {
    const k = row[OSK.c];
    if (k === "OK") return oskClose(true);
    if (k === "⌫") oskBack();
    else if (k === "Spacja") oskType(" ");
    else if (k === "⇧") OSK.shift = !OSK.shift;
    else { oskType(OSK.shift ? k.toUpperCase() : k); if (OSK.shift) OSK.shift = false; }
  }
  renderOsk();
}

/* ───────────── opcje gry (przytrzymane A) ───────────── */
const GO = { id: 0, data: null, detail: null, page: "main", idx: 0, items: [], art: null };
const META_FIELDS = [["title", "Tytuł"], ["developer", "Producent"], ["publisher", "Wydawca"],
  ["year", "Rok premiery"], ["genre", "Gatunek"], ["players", "Gracze"], ["description", "Opis"]];

async function openGameOptions() {
  const g = S.games[S.gameIdx];
  if (!g) return;
  GO.id = g.id;
  [GO.data, GO.detail] = await Promise.all([api().game_options(g.id), api().game_detail(g.id)]);
  GO.page = "main"; GO.idx = 0;
  S.modal = "gopt";
  $("gopt").classList.remove("hidden");
  renderGopt();
}
function closeGopt() {
  S.modal = null;
  $("gopt").classList.add("hidden");
  renderGames();
}
function goptItems() {
  const d = GO.data, det = GO.detail, g = S.games[S.gameIdx] || {};
  if (GO.page === "main") return [
    { label: "Graj", act: () => { closeGopt(); launch(); } },
    { label: "Emulator", value: d.emulator.label + (d.emulator.own ? "  (tylko ta gra)" : ""), act: () => goPage("emus") },
    { label: "Wczytaj zapis", value: d.states.length ? `${d.states.length} ${plural(d.states.length, "zapis", "zapisy", "zapisów")}` : "brak zapisów",
      act: () => d.states.length ? goPage("states") : toast("Ten emulator nie ma zapisów tej gry.") },
    { label: "Metadane i opis", value: [det.meta.developer, det.meta.year].filter(Boolean).join(" · "), act: () => goPage("meta") },
    { label: "Okładka", value: g.box ? "jest" : "brak", act: () => openArtPicker("box") },
    { label: "Zrzut ekranu", value: g.snap ? "jest" : "brak", act: () => openArtPicker("snap") },
    { label: "Pobierz opis z sieci", value: "Wikipedia, IGDB, TheGamesDB", act: async () => {
      await api().meta_fetch(GO.id, true); toast("Pobieram opis…"); setTimeout(refreshGoDetail, 4000); } },
    { label: g.pinned ? "Odepnij (pozwól usunąć z dysku)" : "Przypnij (trzymaj na dysku)", act: async () => { await togglePin(); closeGopt(); } },
  ];
  if (GO.page === "emus") {
    const cur = d.emulator;
    const sys = { label: "Domyślny dla systemu", value: cur.own ? "" : "✓", act: async () => { await api().set_game_emulator(GO.id, null); await reloadGo("main"); toast("Gra używa emulatora systemu."); } };
    return [sys, ...d.options.map((o) => ({
      label: o.label, value: cur.own && cur.exe === o.exe && cur.args === o.args ? "✓" : "",
      act: async () => { await api().set_game_emulator(GO.id, o); await reloadGo("main"); toast(`Ta gra: ${o.label}`); },
    }))];
  }
  if (GO.page === "states") return d.states.map((st) => ({
    label: st.resume ? "★ " + st.name : st.name, value: fmtDate(st.time),
    act: () => { closeGopt(); launch(st.path); },
  }));
  if (GO.page === "meta") {
    const m = det.meta, ed = det.meta_edits || {};
    const items = META_FIELDS.map(([k, label]) => ({
      label: label + (ed[k] ? "  ✎" : ""), value: k === "title" ? (m.title || det.title) : (m[k] || "—"),
      act: () => oskOpen(label, k === "title" ? (m.title || det.title) : (m[k] || ""), async (val) => {
        const edits = { ...(det.meta_edits || {}), [k]: val };
        await api().meta_save(GO.id, edits);
        await refreshGoDetail();
        if (k === "title") { const gg = S.games.find((x) => x.id === GO.id); if (gg) gg.title = val || gg.title; }
      }, k === "description"),
    }));
    if (Object.keys(ed).length) items.push({ label: "Przywróć dane pobrane", value: "usuń ręczne zmiany",
      act: async () => { await api().meta_save(GO.id, {}); await refreshGoDetail(); } });
    return items;
  }
  return [];
}
async function refreshGoDetail() {
  GO.detail = await api().game_detail(GO.id);
  if (S.modal === "gopt") renderGopt();
  schedulePreview();
}
async function reloadGo(page) { GO.data = await api().game_options(GO.id); goPage(page); }
function goPage(page) { GO.page = page; GO.idx = 0; renderGopt(); }

function renderGopt() {
  const titles = { main: "Opcje gry", emus: "Emulator dla tej gry", states: "Wczytaj zapis", meta: "Metadane", art: GO.art?.kind === "snap" ? "Zrzut ekranu" : "Okładka" };
  $("goTitle").textContent = GO.detail?.title || "";
  $("goPage").textContent = titles[GO.page];
  const isArt = GO.page === "art";
  $("goItems").classList.toggle("hidden", isArt);
  $("goArt").classList.toggle("hidden", !isArt);
  if (isArt) return renderArtPicker();
  GO.items = goptItems();
  GO.idx = Math.min(GO.idx, GO.items.length - 1);
  $("goItems").innerHTML = GO.items.map((it, i) =>
    `<li class="${i === GO.idx ? "sel" : ""}" data-i="${i}"><span>${esc(it.label)}</span><span class="gv">${esc(it.value || "")}</span></li>`).join("");
  $("goItems").querySelectorAll("li").forEach((li) => li.addEventListener("click", () => { GO.idx = +li.dataset.i; goptInput("a"); }));
  $("goItems").querySelector("li.sel")?.scrollIntoView({ block: "nearest" });
  setHints([["a", "Wybierz"], ["b", GO.page === "main" ? "Zamknij" : "Wstecz"]], $("goHints"));
}
function goptInput(a) {
  if (GO.page === "art") return artPickInput(a);
  const n = GO.items.length;
  if (a === "up") GO.idx = (GO.idx - 1 + n) % n;
  else if (a === "down") GO.idx = (GO.idx + 1) % n;
  else if (a === "lb") GO.idx = Math.max(0, GO.idx - 8);
  else if (a === "rb") GO.idx = Math.min(n - 1, GO.idx + 8);
  else if (a === "b") return GO.page === "main" ? closeGopt() : goPage("main");
  else if (a === "a") return GO.items[GO.idx]?.act();
  renderGopt();
}

/* ── wybór grafiki ── */
async function openArtPicker(kind, query = "") {
  GO.art = { kind, query, list: null, idx: 0 };
  GO.page = "art";
  renderGopt();
  const list = await api().art_candidates(GO.id, kind, query);
  if (GO.page !== "art" || GO.art.kind !== kind) return;
  GO.art.list = list;
  renderGopt();
}
function renderArtPicker() {
  const A_ = GO.art;
  const grid = $("goArtGrid");
  $("goArtInfo").textContent = A_.list === null ? "Szukam grafik…" :
    A_.list.length ? `${A_.list.length} propozycji` + (A_.query ? ` dla „${A_.query}”` : "") :
    "Nic nie znaleziono. X: szukaj pod inną nazwą.";
  grid.classList.toggle("snapgrid", A_.kind === "snap");
  grid.innerHTML = (A_.list || []).map((c, i) =>
    `<figure class="${i === A_.idx ? "sel" : ""}" data-i="${i}"><img src="${esc(c.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">` +
    `<figcaption>${esc(c.source)}<br><small>${esc(c.label)}</small></figcaption></figure>`).join("");
  grid.querySelectorAll("figure").forEach((f) => f.addEventListener("click", () => { A_.idx = +f.dataset.i; artPickInput("a"); }));
  grid.querySelector("figure.sel")?.scrollIntoView({ block: "nearest" });
  setHints([["a", "Wybierz"], ["x", "Szukaj inną nazwą"], ["y", "Usuń grafikę"], ["b", "Wstecz"]], $("goHints"));
}
function artCols() {
  const f = $("goArtGrid").querySelector("figure");
  return f ? Math.max(1, Math.round($("goArtGrid").clientWidth / f.getBoundingClientRect().width)) : 4;
}
async function artPickInput(a) {
  const A_ = GO.art, n = (A_.list || []).length, cols = artCols();
  if (a === "b") return goPage("main");
  if (a === "x") return oskOpen("Szukaj grafiki", A_.query || (S.games[S.gameIdx]?.title || ""), (q) => openArtPicker(A_.kind, q));
  if (a === "y") {
    await api().art_clear(GO.id, A_.kind);
    const g = S.games.find((x) => x.id === GO.id);
    if (g) g[A_.kind] = "";
    toast("Usunięto grafikę."); schedulePreview(); return goPage("main");
  }
  if (!n) return;
  if (a === "left") A_.idx = Math.max(0, A_.idx - 1);
  else if (a === "right") A_.idx = Math.min(n - 1, A_.idx + 1);
  else if (a === "up") A_.idx = Math.max(0, A_.idx - cols);
  else if (a === "down") A_.idx = Math.min(n - 1, A_.idx + cols);
  else if (a === "a") {
    const c = A_.list[A_.idx];
    toast("Pobieram grafikę…");
    const r = await api().art_choose(GO.id, A_.kind, c.url);
    if (!r.ok) return toast(r.reason);
    const g = S.games.find((x) => x.id === GO.id);
    if (g) g[A_.kind] = r.url;
    toast(A_.kind === "box" ? "Okładka zmieniona." : "Zrzut zmieniony.");
    schedulePreview();
    return goPage("main");
  }
  renderArtPicker();
}

/* ───────────── kolejność padów ───────────── */
const PADS = { st: null, idx: 0, timer: null, assign: null };
const PAD_MODES = [["windows", "Jak w Windows (kolejność podłączenia)"], ["wireless_first", "Bezprzewodowe przed przewodowymi"], ["manual", "Ręcznie"]];
const A_BTN = 0x1000, B_BTN = 0x2000;

function padLabel(p) {
  return `Pad #${p.slot + 1}: ` + (p.wireless ? `bezprzewodowy${p.battery ? `, bateria ${p.battery}` : ""}` : "przewodowy");
}
async function openPads() {
  PADS.st = await api().pads_state();
  PADS.idx = 0;
  show("pads");
  clearInterval(PADS.timer);
  PADS.timer = setInterval(padsTick, 200);
}
async function padsTick() {
  if (S.screen !== "pads") return clearInterval(PADS.timer);
  const st = await api().pads_state();
  if (PADS.assign) return padAssignStep(st);
  PADS.st = st;
  renderPads();
}
function renderPads() {
  const st = PADS.st;
  const rows = PAD_MODES.map(([m, label]) => ({ k: label, v: st.mode === m ? "✓" : "", act: async () => { PADS.st = await api().pads_set(m, st.manual); } }));
  rows.push({ k: "Przypisz ręcznie", v: "naciśnij A na padzie każdego gracza", act: startPadAssign });
  PADS.rows = rows;
  const byslot = Object.fromEntries(st.pads.map((p) => [p.slot, p]));
  const order = st.order.map((slot, i) => `<div class="setrow"><span class="k">Gracz ${i + 1}</span><span class="v">${esc(padLabel(byslot[slot]))}${byslot[slot].buttons ? " · ●" : ""}</span><span></span></div>`).join("")
    || `<div class="setrow"><span class="k">Brak podłączonych padów XInput</span><span class="v">Pady PS: przez Steam Input albo DS4Windows</span><span></span></div>`;
  $("padList").innerHTML = `<div class="setrow head">Tryb</div>` +
    rows.map((r, i) => `<div class="setrow${i === PADS.idx ? " sel" : ""} action" data-i="${i}"><span class="k">${esc(r.k)}</span><span class="v">${esc(r.v)}</span><span></span></div>`).join("") +
    `<div class="setrow head">Kolejność graczy (stosowana przy starcie gry)</div>` + order;
  $("padList").querySelectorAll(".setrow[data-i]").forEach((el) => el.addEventListener("click", () => { PADS.idx = +el.dataset.i; padsInput("a"); }));
  $("padPrompt").classList.add("hidden");
  setHints([["a", "Wybierz"], ["b", "Wstecz"]]);
}
async function padsInput(a) {
  if (PADS.assign) return;               // w trakcie przypisywania pad czytamy z XInput
  const n = PADS.rows.length;
  if (a === "up") PADS.idx = (PADS.idx - 1 + n) % n;
  else if (a === "down") PADS.idx = (PADS.idx + 1) % n;
  else if (a === "b") { clearInterval(PADS.timer); return show("systems"); }
  else if (a === "a") await PADS.rows[PADS.idx].act();
  renderPads();
}
function startPadAssign() {
  PADS.assign = { order: [], prev: {}, armed: false };
  $("padPrompt").classList.remove("hidden");
  $("padPrompt").textContent = "Gracz 1: naciśnij A na swoim padzie";
  setHints([["a", "Ten pad = kolejny gracz"], ["b", "Koniec (na dowolnym padzie)"]]);
}
async function padAssignStep(st) {
  const as = PADS.assign;
  const prev = as.prev;
  as.prev = Object.fromEntries(st.pads.map((p) => [p.slot, p.buttons]));
  if (!as.armed) { as.armed = st.pads.every((p) => !p.buttons); return; }   // A z wyboru opcji
  for (const p of st.pads) {
    const was = prev[p.slot] || 0;
    const down = p.buttons & ~was;
    if (down & B_BTN) return finishPadAssign();
    if (down & A_BTN && !as.order.includes(p.slot)) {
      as.order.push(p.slot);
      $("padPrompt").textContent = `Gracz ${as.order.length} → ${padLabel(p)}. ` +
        (as.order.length < st.pads.length ? `Gracz ${as.order.length + 1}: naciśnij A` : "Gotowe.");
      if (as.order.length >= st.pads.length) return finishPadAssign();
    }
  }
}
async function finishPadAssign() {
  const order = PADS.assign.order;
  PADS.assign = null;
  if (order.length) { PADS.st = await api().pads_set("manual", order); toast("Zapisano kolejność padów."); }
  renderPads();
}

/* ───────────── profile ───────────── */
const PR = { list: [], current: 0, idx: 0, delArmed: null, startup: false };

async function openProfiles(startup = false) {
  const r = await api().profiles_list();
  PR.list = r.profiles; PR.current = r.current; PR.startup = startup;
  PR.idx = Math.max(0, PR.list.findIndex((p) => p.id === r.current));
  show("profiles");
}
function renderProfiles() {
  $("profTitle").textContent = PR.startup ? "Kto gra?" : "Profile";
  const cards = PR.list.map((p, i) =>
    `<div class="pcard${i === PR.idx ? " sel" : ""}${p.id === PR.current ? " cur" : ""}" data-i="${i}">` +
    `<div class="pav">${esc((p.name[0] || "?").toUpperCase())}</div><div class="pname">${esc(p.name)}</div></div>`).join("") +
    `<div class="pcard add${PR.idx === PR.list.length ? " sel" : ""}" data-i="${PR.list.length}"><div class="pav">+</div><div class="pname">Nowy profil</div></div>`;
  $("profCards").innerHTML = cards;
  $("profCards").querySelectorAll(".pcard").forEach((el) => el.addEventListener("click", () => { PR.idx = +el.dataset.i; profilesInput("a"); }));
  const onProfile = PR.idx < PR.list.length;
  setHints(onProfile ? [["a", "Graj jako"], ["y", "Zmień nazwę"], ["x", "Usuń"], ["b", "Wstecz"]] : [["a", "Utwórz"], ["b", "Wstecz"]]);
}
async function profilesInput(a) {
  const n = PR.list.length + 1;
  const p = PR.list[PR.idx];
  if (a === "left" || a === "up") PR.idx = (PR.idx - 1 + n) % n;
  else if (a === "right" || a === "down") PR.idx = (PR.idx + 1) % n;
  else if (a === "b") { await refreshState(); return show("systems"); }
  else if (a === "a" && !p) {
    return oskOpen("Nazwa nowego profilu", "", async (name) => {
      const r = await api().profile_create(name);
      if (!r.ok) return toast(r.reason);
      toast(`Utworzono profil: ${r.profile.name}`);
      openProfiles(PR.startup);
    });
  } else if (a === "a") {
    await api().profile_select(p.id);
    await refreshState();
    toast(`Gra: ${p.name}`);
    return show("systems");
  } else if (a === "y" && p) {
    return oskOpen("Nowa nazwa profilu", p.name, async (name) => {
      const r = await api().profile_rename(p.id, name);
      if (!r.ok) return toast(r.reason);
      openProfiles(PR.startup);
    });
  } else if (a === "x" && p) {
    if (PR.delArmed !== p.id) { PR.delArmed = p.id; return toast(`Naciśnij X ponownie, aby usunąć profil „${p.name}”. Save'y zostaną na dysku.`, 4000); }
    PR.delArmed = null;
    const r = await api().profile_delete(p.id);
    if (!r.ok) return toast(r.reason);
    return openProfiles(PR.startup);
  }
  PR.delArmed = null;
  renderProfiles();
}


/* ───────────── pobieranie emulatorów ───────────── */
const INST = { mode: null, es: [], after: null, timer: null };

function instOpen(title, bodyHtml, hints) {
  S.modal = "inst";
  $("inst").classList.remove("hidden");
  $("inTitle").textContent = title;
  $("inBody").innerHTML = bodyHtml;
  $("inProg").classList.add("hidden");
  $("inMsg").textContent = "";
  $("inMsg").className = "lmsg";
  setHints(hints, $("inHints"));
}
function instClose() {
  clearInterval(INST.timer);
  INST.mode = null;
  S.modal = null;
  $("inst").classList.add("hidden");
  show(S.screen);
}
function stepList(steps) {
  return `<ul class="menulist">` + steps.map((s) =>
    `<li>${esc(s.label)}<small>${s.version ? esc(s.version) + " · " : ""}${s.size ? fmtBytes(s.size) : (s.error ? "błąd: " + esc(s.error) : "")}</small></li>`).join("") + `</ul>`;
}

// gra bez emulatora: pytanie przed pobraniem, potem start gry
function askInstallForGame(info) {
  INST.mode = "confirm";
  INST.es = [info.es];
  INST.after = () => launch();
  const total = info.steps.reduce((a, s) => a + (s.size || 0), 0);
  instOpen(`Brak emulatora: ${info.system}`,
    `<p>Pobrać i zainstalować w folderze emulatorów?</p>` + stepList(info.steps) +
    (total ? `<p class="lfiles">Razem około ${fmtBytes(total)}.</p>` : ""),
    [["a", "Pobierz i graj"], ["b", "Anuluj"]]);
}

// ustawienia: wszystkie systemy bez emulatora
async function askInstallMissing() {
  const list = await api().install_missing();
  const can = list.filter((x) => x.steps.length);
  if (!can.length) return toast(list.length ? "Brakujących emulatorów nie umiem pobrać automatycznie." : "Wszystkie systemy mają emulator.");
  INST.mode = "confirm";
  INST.es = can.map((x) => x.es);
  INST.after = async () => { await openSettings(); };
  const rows = can.map((x) => `<li>${esc(x.system)}<small>${esc(x.steps.map((s) => s.label).join(" + "))}</small></li>`).join("");
  const no = list.length - can.length;
  instOpen(`Pobierz brakujące emulatory (${can.length})`,
    `<ul class="menulist golist">${rows}</ul>` + (no ? `<p class="lfiles">Bez automatycznego źródła: ${no}.</p>` : ""),
    [["a", "Pobierz wszystko"], ["b", "Anuluj"]]);
}

async function instStart() {
  const r = await api().install_start(INST.es);
  if (!r.ok) { $("inMsg").textContent = r.reason; return; }
  INST.mode = "progress";
  $("inBody").innerHTML = "";
  $("inProg").classList.remove("hidden");
  setHints([["b", "Przerwij"]], $("inHints"));
  INST.timer = setInterval(instTick, 300);
}
async function instTick() {
  const st = await api().install_status();
  if (!st) return;
  $("inStep").textContent = `Krok ${st.step} z ${st.steps}: ${st.current}`;
  const pct = st.total ? (100 * st.done) / st.total : 0;
  $("inBar").style.width = pct.toFixed(1) + "%";
  $("inPct").textContent = st.total ? Math.floor(pct) + "%" : "";
  $("inBytes").textContent = st.total ? `${fmtBytes(st.done)} / ${fmtBytes(st.total)}` : (st.done ? fmtBytes(st.done) : "");
  if (st.running) return;
  clearInterval(INST.timer);
  INST.mode = "done";
  if (st.errors.length) {
    $("inMsg").className = "lmsg err";
    $("inMsg").textContent = "Błędy: " + st.errors.join("; ");
    setHints([["a", "Zamknij"]], $("inHints"));
    return;
  }
  const after = INST.after;
  instClose();
  await refreshState();
  toast(st.cancelled ? "Przerwano." : `Zainstalowano: ${st.installed.join(", ") || "nic nowego"}`, 4000);
  if (!st.cancelled && after) after();
}
function instInput(a) {
  if (INST.mode === "confirm") {
    if (a === "a") return instStart();
    if (a === "b") return instClose();
  } else if (INST.mode === "progress") {
    if (a === "b") { api().install_cancel(); toast("Przerywam po bieżącym pliku…"); }
  } else if (INST.mode === "done" && (a === "a" || a === "b")) instClose();
}
