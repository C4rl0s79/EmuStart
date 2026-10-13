"use strict";
/* Panel serwera EmuStart (server.html) — okno `EmuStartServer.exe`.
   Woła POST /admin/<akcja> serwera na tym komputerze (emustart/srvadmin.py). */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function admin(name, args) {
  const r = await fetch("/admin/" + name, { method: "POST", headers: { "X-EmuStart": "1" },
                                            body: args ? JSON.stringify(args) : undefined });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return (n / 1024 ** i).toLocaleString("pl-PL", { maximumFractionDigits: i ? 1 : 0 }) + " " + u[i];
}
function fmtDur(s) {
  s = Math.floor(s);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : m ? `${m} min` : `${s} s`;
}
function ago(t) {
  const s = Date.now() / 1000 - t;
  return s < 5 ? "teraz" : s < 60 ? `${Math.floor(s)} s temu` : fmtDur(s) + " temu";
}
/** Komunikat w dymku w rogu okna — widoczny bez względu na to, gdzie przewinięta jest strona. */
let toastTimer = null;
function toast(text, ok) {
  const t = $("toast");
  if (!text) return;
  t.textContent = text;
  t.className = "on" + (ok ? "" : " bad");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = ok ? "" : "bad"; }, ok ? 5000 : 10000);
}
function msg(text, ok) { toast(text, ok); }

let restarting = false, lastStatus = null, curTab = "stan", sysScanWatch = false, lastScanRunning = false;

/* ── zakładki ── */
/** Sekcja zakładki pokazywana na żądanie: otwarta / zamknięta — showTab tego nie nadpisze. */
function setShown(id, on) {
  const s = $(id);
  s.dataset.show = on ? "1" : "0";
  s.hidden = !on || s.dataset.in !== curTab;
}
function showTab(t) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  // sekcje pokazywane tylko na żądanie (data-show="0": zamknięte) zostają ukryte także po zmianie zakładki
  document.querySelectorAll("main > section").forEach((s) => { s.hidden = s.dataset.in !== t || s.dataset.show === "0"; });
  try { localStorage.setItem("tab", t); } catch (e) { /* bez pamięci zakładki */ }
  if (t === "konfig") { refreshConfig(); refreshSysLook(); }
  curTab = t;
  clearInterval(artTimer);
  if (t === "art") { refreshArt(); artTimer = setInterval(refreshArt, 1500); }
  clearInterval(emuTimer);
  if (t === "emu") { refreshEmu(); emuTimer = setInterval(refreshEmu, 1500); refreshPolicy(); }
}

/* ── wymagane ustawienia emulatorów (emustart/emupolicy.py) ── */
function renderPolicy(d) {
  $("emuPolicy").innerHTML = d.families.map((f) =>
    `<div class="field" style="margin-top:10px"><b>${esc(f.label)}</b>` +
    `${f.default ? ' <span class="muted">(domyślne EmuStart)</span>' : ""}` +
    `<textarea data-fam="${esc(f.family)}" rows="4" spellcheck="false" style="width:100%;font-family:monospace">${esc(f.text)}</textarea>` +
    `<div class="actions"><button data-save="${esc(f.family)}" class="primary">Zapisz</button>` +
    `<button data-reset="${esc(f.family)}"${f.default ? " disabled" : ""}>Przywróć domyślne</button></div></div>`).join("");
  $("emuPolicy").querySelectorAll("[data-save],[data-reset]").forEach((b) => b.addEventListener("click", async () => {
    const fam = b.dataset.save || b.dataset.reset;
    const text = $("emuPolicy").querySelector(`textarea[data-fam="${fam}"]`).value;
    const r = await admin("emu_policy_set", b.dataset.reset ? { family: fam, reset: true } : { family: fam, text }).catch(() => null);
    msg(r?.ok ? "Zapisane — komputery nałożą je przed następną grą." : r?.reason || "Serwer nie odpowiada.", r?.ok);
    if (r?.ok) renderPolicy(r);
  }));
}
async function refreshPolicy() {
  const d = await admin("emu_policy").catch(() => null);
  if (d?.ok) renderPolicy(d);
}

/* ── emulatory: magazyn serwera (emustart/emustore.py) ── */
let emuTimer = null;
async function refreshEmu() {
  const d = await admin("emu_status").catch(() => null);
  if (!d?.ok) return;
  const j = d.job;
  $("emuChecked").textContent = d.checked ? `ostatnie sprawdzenie: ${ago(d.checked)}` : "jeszcze nie sprawdzano";
  $("bEmuUpd").disabled = j.running;
  $("emuJob").innerHTML = j.running
    ? `Pobieram: <b>${esc(j.item || "…")}</b> (${j.step}/${j.steps})` + (j.total ? ` — ${fmtBytes(j.done)} z ${fmtBytes(j.total)}` : "")
    : (j.log.length ? esc(j.log.slice(-6).join(" · ")) : "");
  $("emuBarBox").hidden = !(j.running && j.total);
  if (j.running && j.total) $("emuBar").style.width = `${Math.floor((100 * j.done) / j.total)}%`;
  $("emuRows").innerHTML = d.items.length ? d.items.map((r) =>
    `<tr><td>${esc(r.label)}</td><td class="muted">${esc(r.systems.join(", ") || "— (nieużywany)")}</td>` +
    `<td>${esc(r.version || "—")}${r.previous ? `<div class="muted">poprzednia: ${esc(r.previous)}</div>` : ""}` +
    `${r.error ? `<div style="color:var(--err)">${esc(r.error)}</div>` : ""}</td>` +
    `<td class="n">${r.size ? fmtBytes(r.size) : ""}</td><td class="muted">${r.updated ? ago(r.updated) : ""}</td>` +
    `<td class="n"><button data-emu="${esc(r.item)}" ${j.running ? "disabled" : ""}>Pobierz ponownie</button></td></tr>`).join("")
    : `<tr><td colspan="6" class="muted">Biblioteka nie ma jeszcze systemów — najpierw foldery z grami (Konfiguracja).</td></tr>`;
  $("emuRows").querySelectorAll("[data-emu]").forEach((b) => b.addEventListener("click", async () => {
    const r = await admin("emu_update", { items: [b.dataset.emu], force: true }).catch(() => null);
    msg(r?.ok ? "Pobieram ponownie…" : r?.reason || "Serwer nie odpowiada.", r?.ok);
    refreshEmu();
  }));
  $("emuDir").textContent = `Magazyn: ${d.dir}`;
}
$("bEmuUpd").addEventListener("click", async () => {
  const r = await admin("emu_update", {}).catch(() => null);
  msg(r?.ok ? "Sprawdzam nowe wersje emulatorów…" : r?.reason || "Serwer nie odpowiada.", r?.ok);
  refreshEmu();
});

/* ── grafiki i opisy (emustart/art.py — pobiera serwer) ── */
let artTimer = null;
const SRC = { libretro: "libretro (bez klucza)", sgdb: "SteamGridDB", igdb: "IGDB", tgdb: "TheGamesDB" };
function artMsg(text, ok) { toast(text, ok); }
let lbWasRunning = false;
async function refreshArt() {
  const d = await admin("art").catch(() => null);
  if (!d?.ok) return;
  if ($("ges").options.length <= 1)
    $("ges").innerHTML = `<option value="">wszystkie systemy</option>` +
      d.systems.map((s) => `<option value="${esc(s.es)}">${esc(s.display)}</option>`).join("");
  const job = d.job, run = !!job?.running, up = d.lb_update;
  if (lbWasRunning && up && !up.running) toast(up.error ? "Baza LaunchBox: " + up.error : "Baza LaunchBox gotowa.", !up.error);
  lbWasRunning = !!up?.running;
  document.querySelectorAll("[data-art]").forEach((b) => { b.disabled = run; });
  $("bArtStop").hidden = !run;
  $("artJob").hidden = !job && !(up && (up.running || up.error));
  if (up && (up.running || up.error)) {
    $("artLine").textContent = up.error ? "Baza LaunchBox: " + up.error : up.text;
    $("artBar").style.width = up.total ? (100 * up.done / up.total).toFixed(0) + "%" : "5%";
    $("artCur").textContent = "";
  } else if (job) {
    const m = job.mode || {};
    $("artBar").style.width = job.total ? (100 * job.done / job.total).toFixed(1) + "%" : "100%";
    $("artLine").textContent = job.error ? job.error : m.shrink
      ? `${job.done} / ${job.total} plików · zmniejszono ${job.found.box} · odzyskano ${fmtBytes(job.saved)}`
      : `${job.done} / ${job.total} gier` +
        (m.art ? ` · nowe okładki ${job.found.box} · zrzuty ${job.found.snap}` + (job.found.logo ? ` · logo ${job.found.logo}` : "") : "") +
        (m.meta ? ` · opisy ${(job.meta || {}).description || 0}` : "") +
        (run ? (job.eta ? ` · zostało ${fmtDur(job.eta)}` : "") : job.cancelled ? " · zatrzymano" : " · gotowe");
    $("artCur").textContent = run ? job.current || "" : "";
  }
  $("artSystems").innerHTML = d.systems.map((s) =>
    `<tr><td>${esc(s.display)}</td><td class="n">${s.games}</td><td class="n">${s.box}</td><td class="n">${s.snap}</td>` +
    `<td class="n">${s.desc}</td><td class="n"><button data-sys="${esc(s.es)}" ${run ? "disabled" : ""}>Pobierz</button></td></tr>`).join("");
  $("artSystems").querySelectorAll("[data-sys]").forEach((b) => b.addEventListener("click", () => artStart("all", b.dataset.sys)));
  $("artSources").innerHTML = Object.entries(SRC).map(([k, l]) =>
    `${esc(l)}: <span class="${d.sources[k] ? "on" : "off"}">${d.sources[k] ? "aktywne" : "brak klucza"}</span>`).join(" · ");
  const lb = d.launchbox || {};
  $("lbInfo").textContent = lb.ready ? `Baza LaunchBox (opisy, logo, metadane offline): ${lb.games.toLocaleString("pl-PL")} gier, z ${lb.updated}.`
                                     : "Baza LaunchBox (opisy, logo, metadane offline): nie pobrana (108 MB).";
  $("bLb").textContent = lb.ready ? "Aktualizuj bazę LaunchBox" : "Pobierz bazę LaunchBox";
  $("bLb").disabled = !!(up && up.running) || run;
}
async function artStart(mode, es) {
  const r = await admin("art_start", { mode, es: es || "" }).catch(() => null);
  artMsg(r?.ok ? "Pobieranie rozpoczęte." : r?.reason || "Serwer nie odpowiada.", !!r?.ok);
  refreshArt();
}
document.querySelectorAll("[data-art]").forEach((b) => b.addEventListener("click", () => artStart(b.dataset.art)));
$("bArtStop").addEventListener("click", async () => { await admin("art_cancel").catch(() => null); refreshArt(); });
$("bLb").addEventListener("click", async () => {
  const r = await admin("launchbox_update").catch(() => null);
  artMsg(r?.ok ? "Pobieram bazę LaunchBox…" : r?.reason || "Serwer nie odpowiada.", !!r?.ok);
  refreshArt();
});
/* ── jedna gra: grafiki i opis ── */
const META = [["title", "Tytuł"], ["developer", "Producent"], ["publisher", "Wydawca"], ["year", "Rok premiery"],
              ["genre", "Gatunek"], ["players", "Gracze"], ["description", "Opis"]];
const KINDS = { box: "Okładka", snap: "Zrzut ekranu", logo: "Logo gry" };
let gCur = null, gPickKind = "";
function gMsg(text, ok) { toast(text, ok); }
async function gSearch() {
  const r = await admin("games_search", { q: $("gq").value, es: $("ges").value }).catch(() => null);
  if (!r?.ok) return;
  $("gResults").innerHTML = r.games.length ? r.games.map((g) =>
    `<button data-g="${g.id}">${esc(g.name)} <span class="muted">· ${esc(g.system)}</span></button>`).join("")
    : `<div class="muted">Nic nie znaleziono.</div>`;
  $("gResults").querySelectorAll("[data-g]").forEach((b) => b.addEventListener("click", () => {
    $("gResults").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    gOpen(+b.dataset.g);
  }));
}
$("bGq").addEventListener("click", gSearch);
$("gq").addEventListener("keydown", (e) => { if (e.key === "Enter") gSearch(); });
async function gOpen(id) {
  const r = await admin("game_edit", { id }).catch(() => null);
  if (!r?.ok) return gMsg(r?.reason || "Serwer nie odpowiada.");
  gCur = r;
  $("gEdit").hidden = false;
  $("gPick").hidden = true;
  $("gTitle").textContent = r.name;
  $("gSub").textContent = `${r.system} · ${r.rel}`;
  $("gArts").innerHTML = Object.entries(KINDS).map(([k, l]) =>
    `<figure><div class="img">${r[k] ? `<img src="${esc(r[k])}" alt="">` : "brak"}</div>` +
    `<figcaption class="actions" style="margin-top:4px">${esc(l)} <button data-pk="${k}">Zmień…</button>` +
    `${r[k] ? ` <button class="danger" data-cl="${k}">Usuń</button>` : ""}</figcaption></figure>`).join("");
  $("gArts").querySelectorAll("[data-pk]").forEach((b) => b.addEventListener("click", () => gPick(b.dataset.pk, "")));
  $("gArts").querySelectorAll("[data-cl]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Usunąć: ${KINDS[b.dataset.cl]}?`)) return;
    await admin("game_art_clear", { id: gCur.id, kind: b.dataset.cl }).catch(() => null);
    gOpen(gCur.id);
  }));
  const m = r.meta || {}, ed = r.edits || {};
  $("gMeta").innerHTML = META.map(([k, l]) => {
    const v = k === "title" ? (m.title || r.title) : (m[k] || "");
    const input = k === "description" ? `<textarea data-m="${k}">${esc(v)}</textarea>` : `<input data-m="${k}" value="${esc(v)}">`;
    return `<label>${esc(l)}${ed[k] ? " ✎" : ""}</label>${input}`;
  }).join("");
  gMsg("");
}
async function gPick(kind, q) {
  gPickKind = kind;
  $("gPick").hidden = false;
  $("gPickQ").value = q;
  $("gPickInfo").textContent = `${KINDS[kind]}: szukam…`;
  $("gPickList").innerHTML = "";
  const r = await admin("game_art_candidates", { id: gCur.id, kind, q }).catch(() => null);
  const list = r?.list || [];
  $("gPickInfo").textContent = `${KINDS[kind]}: ${list.length ? list.length + " propozycji — kliknij, żeby wybrać" : "brak propozycji — spróbuj innej nazwy"}`;
  $("gPickList").innerHTML = list.map((c, i) =>
    `<button data-i="${i}" title="${esc(c.label || "")}"><img src="${esc(c.thumb || c.url)}" alt="" referrerpolicy="no-referrer">` +
    `<span>${esc(c.source || "")}</span></button>`).join("");
  $("gPickList").querySelectorAll("[data-i]").forEach((b) => b.addEventListener("click", async () => {
    const c = list[+b.dataset.i];
    gMsg("Pobieram grafikę…", true);
    const res = await admin("game_art_choose", { id: gCur.id, kind, url: c.url }).catch(() => null);
    if (!res?.ok) return gMsg(res?.reason || "Serwer nie odpowiada.");
    await gOpen(gCur.id);
    gMsg(`${KINDS[kind]} zapisana — urządzenia pokażą ją po odświeżeniu listy.`, true);
  }));
}
$("bPickQ").addEventListener("click", () => gPick(gPickKind, $("gPickQ").value));
$("bPickClose").addEventListener("click", () => { $("gPick").hidden = true; });
$("bMetaSave").addEventListener("click", async () => {
  const edits = {}, m = gCur.meta || {};
  $("gMeta").querySelectorAll("[data-m]").forEach((el) => {
    const k = el.dataset.m, auto = k === "title" ? (m.title || gCur.title) : (m[k] || "");
    if (el.value.trim() && (el.value !== auto || (gCur.edits || {})[k])) edits[k] = el.value;
  });
  const r = await admin("game_meta_save", { id: gCur.id, edits: { ...(gCur.edits || {}), ...edits } }).catch(() => null);
  gMsg(r?.ok ? "Opis zapisany." : r?.reason || "Serwer nie odpowiada.", r?.ok);
  gOpen(gCur.id);
});
$("bMetaReset").addEventListener("click", async () => {
  if (!confirm("Usunąć ręczne zmiany opisu tej gry?")) return;
  await admin("game_meta_save", { id: gCur.id, edits: {} }).catch(() => null);
  gOpen(gCur.id);
});
$("bMetaFetch").addEventListener("click", async () => {
  const r = await admin("game_meta_fetch", { id: gCur.id, full: true }).catch(() => null);
  gMsg(r?.ok ? "Pobieram opis (Wikipedia, IGDB, TheGamesDB)…" : r?.reason || "Serwer nie odpowiada.", r?.ok);
  if (r?.ok) setTimeout(() => gOpen(gCur.id), 5000);
});

/* ── systemy: logo, nazwa, poświata (emustart/logos.py) ── */
let sysPickEs = "";
function sysMsg(text, ok) { toast(text, ok); }
async function refreshSysLook() {
  const r = await admin("systems_look").catch(() => null);
  if (!r?.ok) return;
  $("sysLook").innerHTML = r.systems.map((s) =>
    `<tr><td>${s.logo ? `<img class="syslogo" src="${esc(s.logo)}" alt="" style="${s.glow ? "filter:drop-shadow(0 0 6px #fff8)" : ""}">` : '<span class="muted">bez logo</span>'}</td>` +
    `<td><input data-name="${esc(s.es)}" value="${esc(s.display)}" placeholder="${esc(s.default_name)}" style="width:100%">` +
    `<div class="sysdirs">${esc(s.es)} · ${(s.dirs || []).map(esc).join(" · ") || "brak folderu"}` +
    `${(s.merged || []).length ? ` · scalone: ${s.merged.map((m) => `${esc(m)} <button data-split="${esc(m)}">Rozdziel</button>`).join(" ")}` : ""}</div></td>` +
    `<td class="n">${s.games}</td>` +
    `<td class="n"><label><input type="checkbox" data-glow="${esc(s.es)}" ${s.glow ? "checked" : ""}> poświata</label> ` +
    `<label><input type="checkbox" data-en="${esc(s.es)}" ${s.enabled ? "checked" : ""}> widoczny</label> ` +
    `<button data-logo="${esc(s.es)}">Logo…</button> <select data-merge="${esc(s.es)}"><option value="">scal z…</option>` +
    r.systems.filter((o) => o.es !== s.es).map((o) => `<option value="${esc(o.es)}">${esc(o.display)} (${esc(o.es)})</option>`).join("") +
    `</select></td></tr>` +
    `<tr><td></td><td colspan="3" class="muted">zalecany emulator: <select data-emu="${esc(s.es)}">` +
    `<option value="" ${s.emu_rec ? "" : "selected"}>domyślny urządzenia</option>` +
    `<option value="retroarch" ${s.emu_rec === "retroarch" ? "selected" : ""}>RetroArch</option>` +
    `<option value="standalone" ${s.emu_rec === "standalone" ? "selected" : ""}>osobny emulator</option></select> ` +
    `rdzeń RetroArcha: <select data-core="${esc(s.es)}"><option value="">domyślny${s.core_default ? " (" + esc(s.core_default) + ")" : ""}</option>` +
    (s.cores || []).map((c) => `<option ${c === s.core_rec ? "selected" : ""}>${esc(c)}</option>`).join("") +
    `</select></td></tr>`).join("");
  const set = async (es, data) => {
    const res = await admin("system_set", { es, ...data }).catch(() => null);
    sysMsg(res?.ok ? "Zapisano — urządzenia pokażą to po odświeżeniu biblioteki." : res?.reason || "Serwer nie odpowiada.", res?.ok);
  };
  const merge = async (es, into) => {
    const res = await admin("system_merge", { es, into }).catch(() => null);
    sysMsg(res?.ok ? (into ? `Scalono ${es} z ${into} — przeładowuję listę gier.` : `Rozdzielono ${es} — przeładowuję listę gier.`)
                   : res?.reason || "Serwer nie odpowiada.", res?.ok);
    sysScanWatch = true;
  };
  $("sysLook").querySelectorAll("[data-merge]").forEach((el) => el.addEventListener("change", () => {
    if (!el.value) return;
    if (!confirm(`Scalić platformę ${el.dataset.merge} z ${el.value}? Gry z obu folderów będą na jednej liście ` +
                 "(te same gry raz). Można to cofnąć (Rozdziel).")) { el.value = ""; return; }
    merge(el.dataset.merge, el.value);
  }));
  $("sysLook").querySelectorAll("[data-split]").forEach((b) => b.addEventListener("click", () => merge(b.dataset.split, "")));
  $("sysLook").querySelectorAll("[data-name]").forEach((el) => el.addEventListener("change", () => set(el.dataset.name, { name: el.value })));
  $("sysLook").querySelectorAll("[data-glow]").forEach((el) => el.addEventListener("change", async () => { await set(el.dataset.glow, { glow: el.checked }); refreshSysLook(); }));
  $("sysLook").querySelectorAll("[data-en]").forEach((el) => el.addEventListener("change", () => set(el.dataset.en, { enabled: el.checked })));
  $("sysLook").querySelectorAll("[data-emu]").forEach((el) => el.addEventListener("change", () => set(el.dataset.emu, { emu_rec: el.value })));
  $("sysLook").querySelectorAll("[data-core]").forEach((el) => el.addEventListener("change", () => set(el.dataset.core, { core_rec: el.value })));
  $("sysLook").querySelectorAll("[data-logo]").forEach((b) => b.addEventListener("click", () => sysPick(b.dataset.logo)));
}
async function sysPick(es) {
  sysPickEs = es;
  $("sysPick").hidden = false;
  $("sysPickInfo").textContent = "Szukam logo…";
  $("sysPickList").innerHTML = "";
  const r = await admin("system_logo_candidates", { es }).catch(() => null);
  const list = [...(r?.list || []), { id: "none", url: "", source: "bez logo (sama nazwa)" }];
  $("sysPickInfo").textContent = `Logo systemu ${es} — ${list.length - 1} propozycji, kliknij, żeby wybrać (Esc — zamknij).`;
  $("sysPickList").innerHTML = list.map((c, i) =>
    `<button data-i="${i}">${c.url ? `<img src="${esc(c.url)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}<span>${esc(c.source || "")}${c.label ? " · " + esc(c.label) : ""}</span></button>`).join("");
  $("sysPickList").querySelectorAll("[data-i]").forEach((b) => b.addEventListener("click", async () => {
    const res = await admin("system_logo_choose", { es, id: list[+b.dataset.i].id }).catch(() => null);
    sysMsg(res?.ok ? "Logo zapisane — urządzenia pokażą je po odświeżeniu biblioteki." : res?.reason || "Serwer nie odpowiada.", res?.ok);
    $("sysPick").hidden = true;
    refreshSysLook();
  }));
}
$("bSysPickClose").addEventListener("click", () => { $("sysPick").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("sysPick").hidden) $("sysPick").hidden = true; });
document.addEventListener("mousedown", (e) => {          // kliknięcie obok okna — zamyka
  const box = $("sysPick");
  if (!box.hidden && !box.contains(e.target) && !e.target.closest("[data-logo]")) box.hidden = true;
});

/* ── przeniesienie serwera (emustart/tls.py: export_move / import_move) ── */
$("bMvPrep").addEventListener("click", async () => {
  const pw = $("mvPw1").value;
  if (pw !== $("mvPw2").value) return toast("Hasła się różnią.");
  const why = pwPolicy(pw);
  if (why) return toast(why);
  const r = await admin("move_prepare", { password: pw }).catch(() => null);
  toast(r?.ok ? "Przeniesienie przygotowane — skopiuj cały folder programu na nowy komputer i tam podaj to hasło."
               : r?.reason || "Serwer nie odpowiada.", r?.ok);
  $("mvPw1").value = $("mvPw2").value = "";
  refresh();
});
$("bMvImp").addEventListener("click", async () => {
  const r = await admin("move_import", { password: $("mvPw1").value }).catch(() => null);
  $("mvPw1").value = "";
  if (!r?.ok) return toast(r?.reason || "Serwer nie odpowiada.");
  restarting = true;
  toast("Klucz certyfikatu odblokowany na tym komputerze" + (r.art_keys ? ` (i ${r.art_keys} kluczy źródeł grafik)` : "") +
        " — serwer uruchamia się ponownie.", true);
});

$("bKeys").addEventListener("click", async () => {
  const r = await admin("art_keys_set", { sgdb_key: $("kSgdb").value, igdb_client_id: $("kIgdbId").value,
                                          igdb_client_secret: $("kIgdbSecret").value, tgdb_key: $("kTgdb").value }).catch(() => null);
  artMsg(r?.ok ? "Klucze zapisane (zaszyfrowane na serwerze)." : r?.reason || "Serwer nie odpowiada.", !!r?.ok);
  ["kSgdb", "kIgdbId", "kIgdbSecret", "kTgdb"].forEach((id) => { $(id).value = ""; });
  refreshArt();
});
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ── konfiguracja: foldery (emustart/srvconfig.py) ── */
let cfgFields = [];
function cfgMsg(text, ok) { toast(text, ok); }
const stHtml = (s) => `<span class="st ${s.ok ? "ok" : "bad"}">${esc(s.text)}</span>`;
async function refreshConfig() {
  const r = await admin("config").catch(() => null);
  if (!r?.ok) return cfgMsg("Serwer nie odpowiada.");
  cfgFields = r.fields;
  renderConfig();
}
function renderConfig() {
  $("cfgFields").innerHTML = cfgFields.map((f, i) => {
    const pick = `<button data-pick="${i}">Wybierz…</button>`;
    if (f.kind === "dirs") {
      const rows = f.value.map((v, j) => `<div class="row"><input value="${esc(v)}" data-f="${i}" data-j="${j}">` +
        `<button data-up="${i}:${j}" ${j ? "" : "disabled"} title="Wyżej">↑</button>` +
        `<button class="danger" data-del="${i}:${j}" title="Usuń">✕</button></div>` +
        `<div>${stHtml(f.status[j] || { ok: true, text: "" })}</div>`).join("");
      return `<div class="field"><b>${esc(f.label)}</b><div class="muted">${esc(f.hint)}</div>${rows}` +
        `<div class="row"><button data-add="${i}">Dodaj folder…</button><button class="primary" data-save="${i}">Zapisz</button></div></div>`;
    }
    return `<div class="field"><b>${esc(f.label)}</b><div class="muted">${esc(f.hint)}</div>` +
      `<div class="row"><input value="${esc(f.value)}" data-f="${i}" placeholder="${f.optional ? "(puste)" : ""}">${pick}` +
      `<button class="primary" data-save="${i}">Zapisz</button></div><div>${stHtml(f.status)}</div></div>`;
  }).join("");
  const box = $("cfgFields");
  const values = (i) => [...box.querySelectorAll(`input[data-f="${i}"]`)].map((x) => x.value.trim());
  box.querySelectorAll("[data-save]").forEach((b) => b.addEventListener("click", () => {
    const i = +b.dataset.save, f = cfgFields[i];
    saveField(f, f.kind === "dirs" ? values(i).filter(Boolean) : values(i)[0]);
  }));
  box.querySelectorAll("[data-pick],[data-add]").forEach((b) => b.addEventListener("click", async () => {
    const i = +(b.dataset.pick ?? b.dataset.add), f = cfgFields[i];
    const pick = window.pywebview?.api?.pick;
    if (!pick) return cfgMsg("Wybór folderu działa w oknie panelu — wpisz ścieżkę.");
    const r = await pick(f.kind === "file" ? "file" : "dir", f.kind === "dirs" ? "" : f.value).catch(() => null);
    if (!r?.ok) return;
    if (f.kind === "dirs") { f.value = [...values(i), r.path]; f.status.push({ ok: true, text: "nie zapisano" }); renderConfig(); }
    else box.querySelector(`input[data-f="${i}"]`).value = r.path;
  }));
  box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => {
    const [i, j] = b.dataset.del.split(":").map(Number);
    cfgFields[i].value = values(i); cfgFields[i].value.splice(j, 1); cfgFields[i].status.splice(j, 1);
    renderConfig();
  }));
  box.querySelectorAll("[data-up]").forEach((b) => b.addEventListener("click", () => {
    const [i, j] = b.dataset.up.split(":").map(Number), f = cfgFields[i];
    f.value = values(i);
    [f.value[j - 1], f.value[j]] = [f.value[j], f.value[j - 1]];
    [f.status[j - 1], f.status[j]] = [f.status[j], f.status[j - 1]];
    renderConfig();
  }));
}
async function saveField(f, value) {
  if (f.key === "rom_roots" && !confirm("Zapisać foldery z grami? Serwer przeładuje listę gier.")) return;
  cfgMsg("Sprawdzam…", true);
  const r = await admin("config_set", { key: f.key, value }).catch(() => null);
  if (!r?.ok) return cfgMsg(r?.reason || "Serwer nie odpowiada.");
  cfgFields = r.fields;
  renderConfig();
  cfgMsg(`Zapisano: ${f.label}.` + (f.key === "rom_roots" ? " Przeładowuję listę gier (zakładka Stan)." : ""), true);
}

// zdanie o pobieraniu tylko wtedy, gdy któreś urządzenie naprawdę pobiera grę
function busyNote() {
  const active = (lastStatus?.transfers || []).filter((t) => t.active);
  return active.length ? `

Teraz pobiera: ${active.map((t) => `${t.title} (${t.name || t.ip})`).join(", ")} — ` +
                         "pobieranie wznowi się samo po starcie serwera." : "";
}
// opis wydania z CHANGELOG.md bez znaczników Markdown
const plain = (t) => String(t || "").replace(/^#+\s*/gm, "").replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1");

async function refresh() {
  let st;
  try {
    st = await admin("status");
  } catch (e) {
    $("dot").className = "dot err";
    $("state").textContent = restarting ? "uruchamianie ponownie…" : "serwer nie odpowiada";
    return;
  }
  lastStatus = st;
  if (restarting) { restarting = false; msg("Serwer uruchomiony ponownie.", true); }
  $("dot").className = "dot ok";
  $("state").textContent = `działa od ${fmtDur(st.uptime)}`;
  $("ver").textContent = "wersja " + st.version;
  const addr = st.addresses.map((a) => `http://${a}:${st.port}`).join(", ") || `port ${st.port}`;
  const rows = [
    ["Komputer", st.host],
    ["Adres dla telefonu", addr],
    ["Gry", `${st.games.toLocaleString("pl-PL")} w ${st.systems} systemach`],
    ["Foldery z grami", st.roots.join(", ") || "—"],
    ["Folder profili", st.nas_root || "nie znaleziono (zapisy przez serwer wyłączone)"],
    ["Wysłano od startu", fmtBytes(st.sent_total)],
  ];
  $("info").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");

  const tl = st.tls || {};
  setShown("tlsAlert", tl.state === "locked");
  $("tlsInfo").innerHTML = tl.state === "locked"
    ? `<span class="st bad">Klucz certyfikatu z innego komputera — podaj hasło przeniesienia${tl.move_ready ? "" : " (brak pliku przeniesienie.emustart w folderze serwera)"}.</span>`
    : tl.state === "ok" ? `<span class="st ok">Klucz certyfikatu zaszyfrowany na tym komputerze.</span>` +
      (tl.move_ready ? ' <span class="muted">· przeniesienie przygotowane (przeniesienie.emustart)</span>' : "")
    : tl.state === "plain" ? '<span class="st bad">Klucz certyfikatu niezaszyfrowany (brak DPAPI).</span>'
    : '<span class="muted">Brak certyfikatu.</span>';
  $("bMvImp").hidden = tl.state !== "locked";
  $("bMvPrep").hidden = tl.state === "locked";
  $("mvPw2").hidden = tl.state === "locked";
  const sc = st.scan || {};
  // skan skończony (nowe foldery, scalenie platform) — lista systemów i stany folderów od nowa
  if (lastScanRunning && !sc.running && curTab === "konfig") { refreshSysLook(); refreshConfig(); }
  if (lastScanRunning && !sc.running && sysScanWatch) { sysScanWatch = false; toast("Lista gier przeładowana.", true); }
  lastScanRunning = !!sc.running;
  $("bRescan").disabled = !!sc.running;
  $("scanBox").hidden = !sc.running && !sc.result;
  if (sc.running) {
    $("scanText").textContent = sc.text || "Skanuję…";
    $("scanBar").style.width = sc.total ? (100 * sc.done / sc.total).toFixed(0) + "%" : "5%";
  } else if (sc.result) {
    const r = sc.result;
    $("scanText").textContent = r.ok ? `Lista gier przeładowana: ${r.games ?? "?"} gier w ${r.systems ?? "?"} systemach.`
                                     : "Skanowanie nie powiodło się: " + (r.reason || "");
    $("scanBar").style.width = "100%";
  }

  $("transfers").innerHTML = st.transfers.length ? st.transfers.map((t) =>
    `<tr><td>${esc(t.title)}</td><td>${esc(t.name || t.ip)}</td><td class="n">${fmtBytes(t.sent)}</td>` +
    `<td class="n ${t.active ? "on" : "off"}">${t.active ? "pobiera" : ago(t.last)}</td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">Nic w ostatniej godzinie.</td></tr>`;
  $("clients").innerHTML = st.clients.length ? st.clients.map((c) =>
    `<tr><td>${esc(c.name ? `${c.name} (${c.ip})` : c.ip)}</td><td class="n">${c.requests}</td>` +
    `<td class="n">${fmtBytes(c.sent)}</td><td class="n ${c.active ? "on" : "off"}">${ago(c.last)}</td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">Żadne urządzenie nie łączyło się od startu serwera.</td></tr>`;
}

$("bRescan").addEventListener("click", async () => {
  const r = await admin("rescan").catch(() => null);
  msg(r?.ok ? "Przeładowuję listę gier…" : r?.reason || "Serwer nie odpowiada.", !!r?.ok);
  refresh();
});
$("bRestart").addEventListener("click", async () => {
  if (!confirm("Uruchomić serwer ponownie?" + busyNote())) return;
  const r = await admin("restart").catch(() => null);
  if (r?.ok) { restarting = true; msg("Restartuję serwer…", true); }
  else msg(r?.reason || "Serwer nie odpowiada.");
});
$("bLogs").addEventListener("click", async () => {
  // okno panelu otwiera folder samo (serwer na koncie SYSTEM zrobiłby to niewidocznie)
  const local = window.pywebview?.api?.open_logs;
  const r = await (local ? local() : admin("logs")).catch(() => null);
  if (!r?.ok) msg(r?.reason || "Nie udało się otworzyć folderu logów.");
});

/* ── aktualizacja z GitHuba (emustart/updater.py) ── */
let updPoll = null;
function updText(t) { $("updText").textContent = t || ""; }
$("bUpdCheck").addEventListener("click", async () => {
  updText("Sprawdzam wydania na GitHubie…");
  $("bUpd").hidden = true;
  const r = await admin("update_check").catch(() => null);
  if (!r) return updText("Serwer nie odpowiada.");
  if (!r.ok) return updText(r.reason);
  $("updNotes").hidden = !r.notes || !r.newer;
  $("updNotes").textContent = plain(r.notes);
  if (!r.newer) return updText(`Serwer ma najnowszą wersję (${r.current}).`);
  if (!r.frozen) return updText(`Jest wersja ${r.version} (serwer ${r.current}) — aktualizacja z panelu działa tylko w EmuStart.exe.`);
  if (!r.available) return updText(`Jest wersja ${r.version}, ale wydanie nie ma jeszcze pliku dla Windows — spróbuj za kilka minut.`);
  updText(`Dostępna wersja ${r.version} (serwer ${r.current}) · ${fmtBytes(r.size)}`);
  $("bUpd").textContent = `Aktualizuj do ${r.version}`;
  $("bUpd").hidden = false;
});
$("bUpd").addEventListener("click", async () => {
  if (!confirm("Pobrać aktualizację i uruchomić serwer ponownie?" + busyNote())) return;
  $("bUpd").disabled = $("bUpdCheck").disabled = true;
  const r = await admin("update_start").catch(() => null);
  if (!r?.ok) { $("bUpd").disabled = $("bUpdCheck").disabled = false; return updText(r?.reason || "Serwer nie odpowiada."); }
  $("updBarBox").hidden = false;
  clearInterval(updPoll);
  updPoll = setInterval(updTick, 700);
});
async function updTick() {
  const st = await admin("update_status").catch(() => null);
  if (!st) return;
  $("updBar").style.width = st.total ? (100 * st.done / st.total).toFixed(0) + "%" : "5%";
  if (st.phase === "downloading") return updText(`Pobieram ${st.version}: ${fmtBytes(st.done)} / ${fmtBytes(st.total)}`);
  if (st.phase === "extracting") return updText("Rozpakowuję i sprawdzam archiwum…");
  clearInterval(updPoll);
  if (st.phase === "error") {
    $("bUpd").disabled = $("bUpdCheck").disabled = false;
    return updText("Aktualizacja nie powiodła się: " + st.error);
  }
  if (st.phase !== "ready") return;
  updText("Podmieniam pliki i uruchamiam serwer ponownie — panel otworzy się sam po starcie nowej wersji (do kilku minut).");
  const who = await (window.pywebview?.api?.whoami?.() ?? Promise.resolve("")).catch(() => "");
  const r = await admin("update_apply", { panel_user: who || "" }).catch(() => null);
  if (!r?.ok) { $("bUpd").disabled = $("bUpdCheck").disabled = false; return updText(r?.reason || "Serwer nie odpowiada."); }
  restarting = true;
  // okno panelu to ten sam EmuStart.exe — zamyka się, żeby pliki dało się podmienić
  const close = window.pywebview?.api?.close_for_update;
  if (close) setTimeout(() => close(st.version), 300);
}

/* ── urządzenia zalogowane kontem i prośby o zalogowanie (emustart/devices.py, enroll.py) ── */
const KIND = { pc: "komputer", phone: "telefon" };
let lastDevices = [];
async function refreshDevices() {
  const r = await admin("devices").catch(() => null);
  if (!r) return;
  lastDevices = r.devices;
  const enr = r.enroll || [];
  $("enrBox").hidden = !enr.length;
  $("enroll").innerHTML = enr.map((q) =>
    `<tr><td>${esc(q.name)}<div class="muted">${esc(KIND[q.class] || q.class)} · ${esc(q.ip)} · ${ago(q.created)}</div></td>` +
    `<td>${q.mode === "new" ? "nowe konto" : "konto"} <b>${esc(q.account)}</b>` +
    `${q.from_account ? `<div class="muted">prosi zalogowane urządzenie konta ${esc(q.from_account)}</div>` : ""}` +
    `${(q.replaces || []).length ? `<div class="muted">zastąpi: ${esc(q.replaces.join(", "))}</div>` : ""}</td>` +
    `<td><code style="font-size:18px;letter-spacing:.08em">${esc(q.code)}</code></td>` +
    `<td class="n"><button class="primary" data-eg="${esc(q.id)}">Zatwierdź z grami</button> ` +
    `<button data-es="${esc(q.id)}">Bez gier</button> <button class="danger" data-en="${esc(q.id)}">Odrzuć</button></td></tr>`).join("");
  $("enroll").querySelectorAll("[data-eg],[data-es],[data-en]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.eg || b.dataset.es || b.dataset.en;
    const res = await admin("enroll_decide", { id, allow: !b.dataset.en, games: !!b.dataset.eg }).catch(() => null);
    msg(res?.ok ? (b.dataset.en ? `Odrzucono: ${res.name}` : `Zatwierdzono: ${res.name}`) : (res?.reason || "Serwer nie odpowiada."), res?.ok);
    refreshDevices();
  }));
  $("devices").innerHTML = r.devices.length ? r.devices.map((d) =>
    `<tr class="${d.revoked ? "off" : ""}"><td>${esc(d.name)}<div class="muted">${esc(d.ip || "")}</div></td>` +
    `<td>${esc(KIND[d.class] || d.class)}</td>` +
    `<td>${esc(d.account || "—")}${d.admin ? " · administrator" : ""} · ${d.games || d.admin ? "z grami" : "bez gier"}` +
    `${d.revoked || d.admin ? "" : ` <button data-games="${esc(d.id)}" data-on="${d.games ? 1 : 0}">${d.games ? "Bez gier" : "Z grami"}</button>`}` +
    `${d.revoked_reason ? `<div class="muted">${esc(d.revoked_reason)}</div>` : ""}</td>` +
    `<td>${esc(d.version || "—")}</td>` +
    `<td class="n">${d.revoked ? "unieważnione" : ago(d.last_seen)}</td>` +
    `<td class="n">${d.revoked ? "" : `<button data-ren="${esc(d.id)}">Nazwa</button> <button class="danger" data-rev="${esc(d.id)}">Unieważnij</button>`}</td></tr>`).join("")
    : `<tr><td colspan="6" class="muted">Brak urządzeń — „Dodaj urządzenie” i zaloguj się w aplikacji.</td></tr>`;
  $("devices").querySelectorAll("[data-games]").forEach((b) => b.addEventListener("click", async () => {
    await admin("device_games", { id: b.dataset.games, games: b.dataset.on !== "1" }).catch(() => null);
    refreshDevices();
  }));
  $("devices").querySelectorAll("[data-rev]").forEach((b) => b.addEventListener("click", async () => {
    const d = (lastDevices || []).find((x) => x.id === b.dataset.rev) || {};
    const recent = d.last_seen && Date.now() / 1000 - d.last_seen < 6 * 3600;
    if (!confirm((recent ? `UWAGA: „${d.name}” łączyło się z serwerem ${ago(d.last_seen)} — to urządzenie jest w użyciu.\n\n` : "") +
                 "Unieważnić to urządzenie? Straci dostęp do serwera, dopóki nie zaloguje się ponownie.")) return;
    await admin("device_revoke", { id: b.dataset.rev }).catch(() => null);
    refreshDevices();
  }));
  $("devices").querySelectorAll("[data-ren]").forEach((b) => b.addEventListener("click", async () => {
    const name = prompt("Nowa nazwa urządzenia:");
    if (!name) return;
    await admin("device_rename", { id: b.dataset.ren, name }).catch(() => null);
    refreshDevices();
  }));
}
/* ── konta i hasła (emustart/accounts.py) ── */
function pwPolicy(pw) {
  const miss = [];
  if (pw.length < 10) miss.push("co najmniej 10 znaków");
  if (!/[A-ZĄĆĘŁŃÓŚŹŻ]/.test(pw)) miss.push("wielka litera");
  if (!/\d/.test(pw)) miss.push("cyfra");
  if (!/[^\p{L}\p{N}\s]|_/u.test(pw)) miss.push("znak specjalny");
  return miss.length ? `Hasło musi mieć: ${miss.join(", ")}.` : "";
}
async function refreshAccounts() {
  const r = await admin("accounts").catch(() => null);
  if (!r) return;
  $("accounts").innerHTML = r.accounts.length ? r.accounts.map((a) =>
    `<tr><td>${esc(a.name)}${a.admin ? ' <span class="muted">· administrator</span>' : ""}</td>` +
    `<td>${a.password ? `ustawione <span class="muted">${a.set ? ago(a.set) : ""}</span>` : "<b>brak</b>"}</td>` +
    `<td class="n">${a.devices}</td><td class="n"><button data-pw="${esc(a.name)}">${a.password ? "Nowe hasło" : "Ustaw hasło"}</button> ` +
    `<button data-adm="${esc(a.name)}" data-on="${a.admin ? 1 : 0}">${a.admin ? "Odbierz admina" : "Administrator"}</button> ` +
    `<button data-files="${esc(a.name)}">Pliki i kopie</button></td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">Brak kont.</td></tr>`;
  $("accounts").querySelectorAll("[data-pw]").forEach((b) => b.addEventListener("click", () => askAccountPassword(b.dataset.pw)));
  $("accounts").querySelectorAll("[data-files]").forEach((b) => b.addEventListener("click", () => openAccount(b.dataset.files)));
  $("accounts").querySelectorAll("[data-adm]").forEach((b) => b.addEventListener("click", async () => {
    const make = b.dataset.on !== "1";
    if (!confirm(make ? `Konto „${b.dataset.adm}” administratorem? Wszystkie jego urządzenia zatwierdzą nowe konta i zobaczą wszystkie konta.`
                      : `Odebrać kontu „${b.dataset.adm}” administratora?`)) return;
    const r = await admin("account_admin", { name: b.dataset.adm, admin: make }).catch(() => null);
    msg(r?.ok ? "Zapisano." : (r?.reason || "Serwer nie odpowiada."), r?.ok);
    refreshAccounts(); refreshDevices();
  }));
}
function askAccountPassword(name) {
  $("pwName").textContent = `„${name}”`;
  $("pw1").value = ""; $("pw2").value = ""; $("pwWhy").textContent = "";
  const dlg = $("pwDlg");
  dlg.onclose = async () => {
    if (dlg.returnValue !== "ok") return;
    const pw = $("pw1").value;
    const why = pwPolicy(pw) || (pw !== $("pw2").value ? "Hasła się różnią." : "");
    if (why) { msg(why); return; }
    const r = await admin("account_password", { name, password: pw }).catch(() => null);
    msg(r?.ok ? `Hasło konta „${r.name}” ustawione. Urządzenia innych użytkowników tego konta zapytają o nowe.` : (r?.reason || "Serwer nie odpowiada."), r?.ok);
    refreshAccounts();
  };
  dlg.showModal();
}
refreshAccounts();
setInterval(refreshAccounts, 30000);

/* ── konto: pliki i kopie zapasowe (emustart/acctbackup.py) ── */
let acctOpen = "";
const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" }) : "—";
async function openAccount(name) {
  acctOpen = name;
  setShown("acctBox", true);
  $("acctName").textContent = `„${name}”`;
  $("acctParts").innerHTML = `<tr><td colspan="4" class="muted">Wczytuję…</td></tr>`;
  const r = await admin("account_files", { name }).catch(() => null);
  if (!r?.ok) { $("acctParts").innerHTML = `<tr><td colspan="4" class="muted">${esc(r?.reason || "Serwer nie odpowiada.")}</td></tr>`; return; }
  $("acctParts").innerHTML = r.parts.length ? r.parts.map((p) =>
    `<tr><td>${esc(p.name)}</td><td class="n">${p.files}</td><td class="n">${fmtBytes(p.size)}</td><td class="n">${fmtTime(p.changed)}</td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">Profil jest pusty.</td></tr>`;
  $("acctRecent").innerHTML = r.recent.map((f) =>
    `<tr><td>${esc(f.path)}</td><td class="n">${fmtBytes(f.size)}</td><td class="n">${fmtTime(f.changed)}</td></tr>`).join("");
  $("acctBackups").innerHTML = r.backups.length ? r.backups.map((b) =>
    `<tr><td>${esc(b.file)}<div class="muted">${fmtTime(b.time)}</div></td><td class="n">${fmtBytes(b.size)}</td>` +
    `<td class="n"><button class="danger" data-restore="${esc(b.file)}">Przywróć</button></td></tr>`).join("")
    : `<tr><td colspan="3" class="muted">Brak kopii.</td></tr>`;
  $("acctBackups").querySelectorAll("[data-restore]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Przywrócić konto „${name}” z kopii ${b.dataset.restore}?\n\nNajpierw powstanie kopia obecnego stanu. ` +
                 "Urządzenia tego konta nie powinny teraz grać.")) return;
    const res = await admin("account_restore", { name, file: b.dataset.restore }).catch(() => null);
    msg(res?.ok ? `Przywrócono ${res.files} plików konta „${name}” (kopia sprzed: ${res.before}).` : res?.reason || "Serwer nie odpowiada.", res?.ok);
    openAccount(name);
  }));
  $("bakAuto").checked = !!r.auto;
  $("bakKeep").value = r.keep;
  $("acctBox").scrollIntoView({ behavior: "smooth", block: "start" });
}
$("bAcctBackup").addEventListener("click", async () => {
  if (!acctOpen) return;
  $("bAcctBackup").disabled = true;
  const r = await admin("account_backup", { name: acctOpen }).catch(() => null);
  $("bAcctBackup").disabled = false;
  msg(r?.ok ? `Kopia konta „${acctOpen}”: ${r.file} (${fmtBytes(r.size)}).` : r?.reason || "Serwer nie odpowiada.", r?.ok);
  openAccount(acctOpen);
});
$("bAcctClose").addEventListener("click", () => { acctOpen = ""; setShown("acctBox", false); });
$("bBakSave").addEventListener("click", async () => {
  const r = await admin("backup_settings", { auto: $("bakAuto").checked, keep: $("bakKeep").value }).catch(() => null);
  msg(r?.ok ? "Ustawienia kopii zapisane." : r?.reason || "Serwer nie odpowiada.", r?.ok);
});

let pairTimer = null;
$("bPair").addEventListener("click", async () => {
  const r = await admin("login_code").catch(() => null);
  if (!r?.ok) return msg(r?.reason || "Serwer nie odpowiada.");
  $("pairQr").innerHTML = r.svg || "";
  $("pairQr").hidden = !r.svg;
  $("pairCode").textContent = r.pretty;
  $("pairUrl").textContent = r.tunnel || "kod QR / link";
  $("pairLink").value = r.uri;
  $("pairBox").hidden = false;
  clearInterval(pairTimer);
  const tick = () => {
    const left = Math.max(0, r.expires - Date.now() / 1000);
    $("pairLeft").textContent = left ? `jeszcze ${Math.ceil(left / 60)} min` : "— wygasł, wygeneruj nowy";
    if (!left) {                     // wygasły kod nic nie da — znika cały, z linkiem i przyciskiem
      clearInterval(pairTimer);
      $("pairBox").hidden = true; $("pairQr").innerHTML = ""; $("pairLink").value = ""; $("pairCode").textContent = "";
      msg("Kod logowania wygasł — „Dodaj urządzenie” wygeneruje nowy.");
    }
    refreshDevices();
  };
  tick();
  pairTimer = setInterval(tick, 3000);
});
$("bPairCopy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("pairLink").value); msg("Link skopiowany — wklej go w EmuStart na komputerze: Ustawienia → Zaloguj.", true); }
  catch (e) { $("pairLink").select(); }
});

/* ── dostęp z internetu: port publiczny przez UPnP (emustart/public.py) ── */
let pubOn = false;
async function refreshPublic() {
  const r = await admin("public_status").catch(() => null);
  if (!r) return;
  showTunnel(r.tunnel);
  pubOn = !!r.enabled;
  $("bPublic").textContent = pubOn ? "Wyłącz dostęp z internetu" : "Włącz dostęp z internetu";
  if (!pubOn) { $("pubInfo").innerHTML = r.error ? `<dt>Błąd</dt><dd>${esc(r.error)}</dd>` : ""; return; }
  const rows = [
    ["Port", String(r.port)],
    ["Router (UPnP)", r.router ? "znaleziony" : (r.checked ? "nie znaleziono" : "szukam…")],
    ["Adres zewnętrzny", r.ext_ip ? r.ext_ip + (r.cgnat ? " (za NAT-em operatora)" : "") : "—"],
    ["Przekierowanie", r.mapped ? (r.lease ? `otwarte (odnawiane, ważne ${Math.round(r.lease / 60)} min)` : "otwarte (stałe)") : "nie"],
    ["Osiągalność", r.reach === "ok" ? "działa (sprawdzone z serwera)" :
      r.last_inbound ? "działa (połączenie z internetu " + ago(r.last_inbound) + ")" :
      "nie potwierdzona — otwórz EmuStart na telefonie poza Wi-Fi i bez Tailscale"],
  ];
  if (r.last_inbound && r.reach === "ok") rows.push(["Ostatnio z internetu", ago(r.last_inbound) + " (" + r.last_ip + ")"]);
  if (r.banned) rows.push(["Zablokowane adresy", `${r.banned} (seria złych kluczy)`]);
  if (r.error) rows.push(["Uwaga", r.error]);
  $("pubInfo").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
}
function showTunnel(t) {
  if (!t) return;
  if (document.activeElement !== $("tunUrl")) $("tunUrl").value = t.url || "";
  $("tunInfo").textContent = !t.url ? "Tunel nie ustawiony." :
    t.state === "ok" ? `Tunel działa (sprawdzony ${ago(t.checked)}).` :
    t.state === "error" ? `Tunel nie odpowiada: ${t.error}` : "Sprawdzam tunel…";
}
$("bTunnel").addEventListener("click", async () => {
  const r = await admin("tunnel_set", { url: $("tunUrl").value.trim() }).catch(() => null);
  msg(r?.ok ? (r.url ? "Zapisano adres tunelu — sprawdzam…" : "Tunel wyłączony.") : (r?.reason || "Serwer nie odpowiada."), r?.ok);
  setTimeout(refreshPublic, 2500);
});
$("bPublic").addEventListener("click", async () => {
  if (!pubOn && !confirm("Włączyć dostęp z internetu? Serwer otworzy port na routerze (UPnP). " +
                         "Wejdą tylko urządzenia zalogowane kontem (klucz urządzenia).")) return;
  $("bPublic").disabled = true;
  const r = await admin("public_set", { enabled: !pubOn }).catch(() => null);
  $("bPublic").disabled = false;
  if (!r?.ok) msg(r?.reason || "Serwer nie odpowiada.");
  refreshPublic();
});

/* ── przeniesienie profili z udziału do katalogu serwera (2.0) ── */
let migrPoll = null;
async function migrTick() {
  const st = await admin("profiles_migrate_status").catch(() => null);
  if (!st) return;
  $("profRoot").textContent = "Teraz: " + (st.root || "—");
  const inServer = /[\\/]server[\\/]profiles$/i.test(st.root || "");
  $("bMigrate").hidden = inServer;
  if (!st.phase) return;
  $("migrBarBox").hidden = st.phase === "done";
  $("migrBar").style.width = st.total ? (100 * st.done / st.total).toFixed(0) + "%" : "5%";
  if (st.phase === "copying") $("migrText").textContent = `Kopiuję ${st.files} plików: ${fmtBytes(st.done)} / ${fmtBytes(st.total)}`;
  else if (st.phase === "verifying") $("migrText").textContent = `Sprawdzam kopie (SHA-1): ${fmtBytes(st.done)} / ${fmtBytes(st.total)}`;
  else {
    clearInterval(migrPoll); migrPoll = null;
    $("bMigrate").disabled = false;
    $("migrText").textContent = st.phase === "done"
      ? "Gotowe — profile są w katalogu serwera. Stary folder na udziale został nietknięty (zapas); możesz go usunąć, gdy wszystko działa."
      : "Nie udało się: " + st.error + " — serwer dalej używa starego folderu.";
  }
}
$("bMigrate").addEventListener("click", async () => {
  const plan = await admin("profiles_migrate", {}).catch(() => null);
  if (!plan?.ok) return ($("migrText").textContent = plan?.reason || "Serwer nie odpowiada.");
  if (!confirm(`Przenieść profile do katalogu serwera?

Z: ${plan.src}
Do: ${plan.dst}
` +
               `${plan.files} plików, ${fmtBytes(plan.size)}.

Każdy plik zostanie skopiowany i sprawdzony; ` +
               `dopiero potem serwer przełączy się na nowy folder. Stary folder zostaje nietknięty.`)) return;
  const r = await admin("profiles_migrate", { confirm: true }).catch(() => null);
  if (!r?.ok) return ($("migrText").textContent = r?.reason || "Serwer nie odpowiada.");
  $("bMigrate").disabled = true;
  migrPoll = setInterval(migrTick, 700);
});

let startTab = "stan";
try { startTab = localStorage.getItem("tab") || "stan"; } catch (e) { /* domyślna zakładka */ }
showTab(document.querySelector(`#tabs button[data-tab="${startTab}"]`) ? startTab : "stan");
refresh();
refreshDevices();
refreshPublic();
setInterval(refreshPublic, 5000);
migrTick();
setInterval(refresh, 2000);
setInterval(refreshDevices, 4000);          // prośby o dołączenie pojawiają się od razu
