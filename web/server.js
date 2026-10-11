"use strict";
/* Panel serwera EmuStart (server.html) — okno `EmuStart.exe --server-panel`.
   Woła POST /admin/<akcja> serwera na tym komputerze (emustart/srvadmin.py). */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function admin(name) {
  const r = await fetch("/admin/" + name, { method: "POST", headers: { "X-EmuStart": "1" } });
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
function msg(text, ok) {
  $("msg").textContent = text || "";
  $("msg").style.color = ok ? "var(--ok)" : "var(--warn)";
}

let restarting = false;

async function refresh() {
  let st;
  try {
    st = await admin("status");
  } catch (e) {
    $("dot").className = "dot err";
    $("state").textContent = restarting ? "uruchamianie ponownie…" : "serwer nie odpowiada";
    return;
  }
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

  const sc = st.scan || {};
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
  if (!confirm("Uruchomić serwer ponownie? Trwające pobieranie na telefonie wznowi się samo.")) return;
  const r = await admin("restart").catch(() => null);
  if (r?.ok) { restarting = true; msg("Restartuję serwer…", true); }
  else msg(r?.reason || "Serwer nie odpowiada.");
});
$("bKey").addEventListener("click", async () => {
  const r = await admin("key").catch(() => null);
  if (!r) return msg("Serwer nie odpowiada.");
  $("key").textContent = r.key;
  $("keyBox").hidden = false;
  try { await navigator.clipboard.writeText(r.key); msg("Klucz skopiowany do schowka.", true); } catch (e) { /* bez schowka */ }
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
  $("updNotes").textContent = r.notes || "";
  if (!r.newer) return updText(`Serwer ma najnowszą wersję (${r.current}).`);
  if (!r.frozen) return updText(`Jest wersja ${r.version} (serwer ${r.current}) — aktualizacja z panelu działa tylko w EmuStart.exe.`);
  if (!r.available) return updText(`Jest wersja ${r.version}, ale wydanie nie ma jeszcze pliku dla Windows — spróbuj za kilka minut.`);
  updText(`Dostępna wersja ${r.version} (serwer ${r.current}) · ${fmtBytes(r.size)}`);
  $("bUpd").textContent = `Aktualizuj do ${r.version}`;
  $("bUpd").hidden = false;
});
$("bUpd").addEventListener("click", async () => {
  if (!confirm("Pobrać aktualizację i uruchomić serwer ponownie? Trwające pobieranie na telefonie wznowi się samo.")) return;
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
  const r = await admin("update_apply").catch(() => null);
  if (!r?.ok) { $("bUpd").disabled = $("bUpdCheck").disabled = false; return updText(r?.reason || "Serwer nie odpowiada."); }
  restarting = true;
  // okno panelu to ten sam EmuStart.exe — zamyka się, żeby pliki dało się podmienić
  const close = window.pywebview?.api?.close_for_update;
  if (close) setTimeout(() => close(st.version), 300);
}

refresh();
setInterval(refresh, 2000);
