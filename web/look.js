"use strict";
/* Edytor wyglądu: wielkości logo, czcionek, układ podglądu i kolory.
   Wartości trafiają do zmiennych CSS (:root) — zmiana widoczna od razu,
   zapis w config.json (cfg["look"]) przy zamknięciu edytora. */

const ACCENTS = [
  ["#4f8cff", "niebieski"], ["#8a5cff", "fioletowy"], ["#1fb5c4", "turkusowy"], ["#2fb36f", "zielony"],
  ["#d4a017", "złoty"], ["#ff8a3d", "pomarańczowy"], ["#e5484d", "czerwony"], ["#e0559b", "różowy"],
];
const BACKS = {   // nazwa → [kolor tła, poświata u góry]
  granat: ["#0b0d12", "#1b2236", "granatowe"],
  czern: ["#000000", "#000000", "czarne"],
  grafit: ["#101010", "#2a2a2a", "grafitowe"],
  fiolet: ["#0d0b14", "#2a1f45", "fioletowe"],
  zielen: ["#0a100d", "#173226", "zielone"],
  bordo: ["#120a0b", "#3a171c", "bordowe"],
};

const pct = (v) => `${Math.round(v)}%`;
const vh = (v) => `${+v.toFixed(1)} vh`;
const scale = (css) => (v) => [css, String(v / 100)];
// k, etykieta, min, max, krok, domyślna, format, (v) → [zmienna CSS, wartość]
const LOOK_SPEC = [
  { head: "Logo" },
  { k: "tlogo", t: "Logo tytułu na liście — wysokość", min: 50, max: 100, step: 2, def: 92, fmt: (v) => `${v}% wiersza`, css: scale("--tlogo-h") },
  { k: "tlogo_w", t: "Logo tytułu na liście — szerokość", min: 30, max: 95, step: 5, def: 75, fmt: (v) => `do ${v}%`, css: (v) => ["--tlogo-w", v + "%"] },
  { k: "pvlogo", t: "Logo tytułu w podglądzie", min: 5, max: 32, step: 1, def: 16, fmt: vh, css: (v) => ["--pvlogo-h", v + "vh"] },
  { k: "syslogo", t: "Logo systemu w karuzeli", min: 50, max: 150, step: 5, def: 100, fmt: pct,
    css: (v) => [["--car-h", `${34 * v / 100}%`], ["--car-w", `${26 * v / 100}vw`]] },
  { k: "car_gap", t: "Odstęp między logo systemów", min: 14, max: 40, step: 1, def: 25, fmt: (v) => `${v} vw`, css: null },
  { k: "listlogo", t: "Logo systemu nad listą gier", min: 3, max: 14, step: 0.5, def: 6.5, fmt: vh, css: (v) => ["--listlogo-h", v + "vh"] },
  { head: "Lista i podgląd" },
  { k: "row", t: "Wysokość wiersza listy", min: 3, max: 10, step: 0.2, def: 4.6, fmt: vh, css: (v) => ["--row", `max(28px, ${v}vh)`] },
  { k: "list_w", t: "Szerokość listy gier", min: 25, max: 65, step: 1, def: 42, fmt: (v) => `${v}% ekranu`, css: (v) => ["--list-w", v + "%"] },
  { k: "media_h", t: "Wysokość okładki i zrzutu", min: 20, max: 62, step: 1, def: 44, fmt: vh, css: (v) => ["--media-h", v + "vh"] },
  { k: "box_w", t: "Szerokość kolumny okładki", min: 18, max: 55, step: 1, def: 32, fmt: pct, css: (v) => ["--box-w", v + "%"] },
  { k: "desc_lines", t: "Liczba linii opisu gry", min: 3, max: 40, step: 1, def: 14, fmt: (v) => String(v), css: (v) => ["--desc-lines", String(v)] },
  { head: "Wielkość czcionek" },
  { k: "fs", t: "Cały interfejs", min: 70, max: 170, step: 5, def: 100, fmt: pct, css: scale("--fs-all") },
  { k: "fs_list", t: "Lista gier", min: 60, max: 200, step: 5, def: 100, fmt: pct, css: scale("--fs-list") },
  { k: "fs_title", t: "Tytuł gry w podglądzie", min: 60, max: 220, step: 5, def: 100, fmt: pct, css: scale("--fs-title") },
  { k: "fs_meta", t: "Metadane (rok, gatunek…)", min: 60, max: 200, step: 5, def: 100, fmt: pct, css: scale("--fs-meta") },
  { k: "fs_desc", t: "Opis gry", min: 60, max: 200, step: 5, def: 100, fmt: pct, css: scale("--fs-desc") },
  { k: "fs_sys", t: "Nazwa systemu (karuzela)", min: 50, max: 200, step: 5, def: 100, fmt: pct, css: scale("--fs-sys") },
  { k: "fs_sysdesc", t: "Informacje i opis systemu", min: 60, max: 200, step: 5, def: 100, fmt: pct, css: scale("--fs-sysdesc") },
  { k: "fs_menu", t: "Menu i okna", min: 60, max: 180, step: 5, def: 100, fmt: pct, css: scale("--fs-menu") },
  { k: "fs_hint", t: "Pasek podpowiedzi (dół)", min: 60, max: 180, step: 5, def: 100, fmt: pct, css: scale("--fs-hint") },
  { k: "fs_top", t: "Pasek górny", min: 60, max: 180, step: 5, def: 100, fmt: pct, css: scale("--fs-top") },
  { head: "Kolory" },
  { k: "accent", t: "Kolor akcentu", opts: ACCENTS.map(([c]) => c), def: "#4f8cff",
    fmt: (v) => (ACCENTS.find(([c]) => c === v) || [v, v])[1], swatch: true, css: (v) => ["--accent", v] },
  { k: "bg", t: "Tło", opts: Object.keys(BACKS), def: "granat", fmt: (v) => (BACKS[v] || BACKS.granat)[2],
    css: (v) => { const b = BACKS[v] || BACKS.granat; return [["--bg", b[0]], ["--bg-grad", b[1]]]; } },
  { head: "Inne" },
  { k: "games_logo", t: "Tytuły gier jako logo", cfg: true, opts: [true, false], fmt: (v) => (v ? "tak" : "nie") },
  { k: "look_scope", t: "Wygląd zapisywany", cfg: true, opts: ["profile", "machine"], def0: "profile",
    fmt: (v) => (v === "machine" ? "dla tego komputera" : "dla profilu (na każdym komputerze)") },
];
const LOOK_ITEMS = LOOK_SPEC.filter((r) => r.k);
const LV = {};   // bieżące wartości (bez games_logo — to zwykłe ustawienie)

function lookVal(k) {
  const it = LOOK_ITEMS.find((r) => r.k === k);
  return LV[k] ?? it?.def;
}

function applyLook(values, replace = false) {
  if (replace) for (const k of Object.keys(LV)) delete LV[k];
  if (values) for (const it of LOOK_ITEMS) if (!it.cfg && values[it.k] !== undefined) LV[it.k] = values[it.k];
  const root = document.documentElement.style;
  for (const it of LOOK_ITEMS) {
    if (!it.css) continue;
    let v = lookVal(it.k);
    if (it.opts ? !it.opts.includes(v) : typeof v !== "number") v = it.def;
    let pairs = it.css(v);
    if (!Array.isArray(pairs[0])) pairs = [pairs];
    for (const [name, val] of pairs) root.setProperty(name, val);
  }
}

/* ───────────── okno edytora ───────────── */
const LK = { idx: 1, prev: null, startArmed: 0, redraw: null };

function openLook() {
  LK.prev = S.screen;
  LK.saved = JSON.stringify(LV);
  // edytor pokazuje zmiany na żywo — ekran systemów lub gier pod spodem
  if (S.screen !== "systems" && S.screen !== "games") show("systems");
  S.modal = "look";
  $("lookEd").classList.remove("hidden");
  renderLook();
}

function lookGet(it) {
  if (!it.cfg) return lookVal(it.k);
  return typeof it.opts[0] === "boolean" ? !!S.state?.[it.k] : (S.state?.[it.k] ?? it.def0);
}

function renderLook() {
  const html = LOOK_SPEC.map((r, i) => {
    if (r.head) return `<div class="lkrow head">${esc(r.head)}</div>`;
    const v = lookGet(r);
    let val;
    if (r.opts) {
      val = `<span class="ch">◀</span>${r.swatch ? `<span class="sw" style="background:${esc(v)}"></span>` : ""}<b>${esc(r.fmt(v))}</b><span class="ch">▶</span>`;
    } else {
      const w = 100 * (v - r.min) / (r.max - r.min);
      val = `<span class="track"><i style="width:${w.toFixed(1)}%"></i></span><b>${esc(r.fmt(v))}</b>`;
    }
    const chg = !r.cfg && v !== r.def;
    return `<div class="lkrow${i === LK.idx ? " sel" : ""}${chg ? " chg" : ""}" data-i="${i}"><span class="lk">${esc(r.t)}</span><span class="lv">${val}</span></div>`;
  }).join("");
  const list = $("lkList");
  list.innerHTML = html;
  list.querySelectorAll(".lkrow[data-i]").forEach((el) => {
    if (LOOK_SPEC[+el.dataset.i].head) return;
    el.addEventListener("click", () => { LK.idx = +el.dataset.i; renderLook(); });
    el.addEventListener("wheel", (e) => { LK.idx = +el.dataset.i; lookStep(e.deltaY < 0 ? 1 : -1); e.preventDefault(); }, { passive: false });
  });
  list.querySelector(".lkrow.sel")?.scrollIntoView({ block: "nearest" });
  const other = S.screen === "games" ? "systemy" : "gry";
  setHints([["dpad", "Zmień"], ["lb", "×5"], ["a", "Podgląd: " + other], ["y", "Przesuń okno"],
            ["x", "Domyślna"], ["start", "Wszystko domyślne"], ["b", "Zapisz"]], $("lkHints"));
  $("hintbar").classList.add("hidden");
}

function lookRedraw() {
  // przelicza listę (wysokość wiersza) i karuzelę; podgląd zostaje
  clearTimeout(LK.redraw);
  LK.redraw = setTimeout(() => {
    if (S.screen === "games") renderGames(); else if (S.screen === "systems") renderSystems();
  }, 60);
}

function lookStep(dir) {
  const it = LOOK_SPEC[LK.idx];
  if (!it?.k) return;
  if (it.cfg) {
    const n = it.opts.length, i = Math.max(0, it.opts.indexOf(lookGet(it)));
    const v = it.opts[(i + dir + n) % n];
    S.state[it.k] = v;
    api().save_settings({ [it.k]: v });
    if (S.screen === "games") renderGames();
  } else if (it.opts) {
    const n = it.opts.length;
    const i = Math.max(0, it.opts.indexOf(lookVal(it.k)));
    LV[it.k] = it.opts[(i + dir + n) % n];
  } else {
    const v = lookVal(it.k) + dir * it.step;
    LV[it.k] = +Math.min(it.max, Math.max(it.min, v)).toFixed(2);
  }
  applyLook();
  lookRedraw();
  renderLook();
}

function lookMove(dir) {
  let i = LK.idx;
  do i = (i + dir + LOOK_SPEC.length) % LOOK_SPEC.length; while (LOOK_SPEC[i].head);
  LK.idx = i;
  renderLook();
}

async function closeLook() {
  $("lookEd").classList.add("hidden");
  $("hintbar").classList.remove("hidden");
  S.modal = null;
  const vals = {};
  for (const it of LOOK_ITEMS) if (!it.cfg && LV[it.k] !== undefined && LV[it.k] !== it.def) vals[it.k] = LV[it.k];
  if (JSON.stringify(LV) !== LK.saved) {
    const r = await api().look_save(vals);
    toast(r?.ok ? "Zapisano wygląd." : "Nie udało się zapisać wyglądu.");
  }
  if (LK.prev && LK.prev !== S.screen && !["systems", "games"].includes(LK.prev)) {
    if (LK.prev === "settings") return openSettings();
    return show(LK.prev);
  }
  show(S.screen);
}

function lookInput(a) {
  if (a === "up") return lookMove(-1);
  if (a === "down") return lookMove(1);
  if (a === "left") return lookStep(-1);
  if (a === "right") return lookStep(1);
  if (a === "lb" || a === "rb") {
    const n = LOOK_SPEC[LK.idx]?.opts ? 1 : 5;
    for (let i = 0; i < n; i++) lookStep(a === "lb" ? -1 : 1);
    return;
  }
  if (a === "y") return $("lookEd").classList.toggle("left");
  if (a === "x") {
    const it = LOOK_SPEC[LK.idx];
    if (it?.k && !it.cfg) { delete LV[it.k]; applyLook(); lookRedraw(); renderLook(); }
    return;
  }
  if (a === "start") {
    if (performance.now() - LK.startArmed > 2500) { LK.startArmed = performance.now(); return toast("Wciśnij Start jeszcze raz, żeby przywrócić wszystko."); }
    LK.startArmed = 0;
    for (const k of Object.keys(LV)) delete LV[k];
    applyLook(); lookRedraw(); renderLook();
    return toast("Przywrócono domyślny wygląd.");
  }
  if (a === "a") {
    if (S.screen === "games") show("systems");
    else if (S.systems.length) openSystem(true).then(() => { if (S.modal === "look") renderLook(); });
    return renderLook();
  }
  if (a === "b" || a === "select") return closeLook();
}

window.openLook = openLook;
window.lookInput = lookInput;
window.applyLook = applyLook;
window.lookVal = lookVal;
