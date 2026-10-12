"use strict";
/* Panel serwera EmuStart (server.html) — okno `EmuStart.exe --server-panel`.
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
function msg(text, ok) {
  $("msg").textContent = text || "";
  $("msg").style.color = ok ? "var(--ok)" : "var(--warn)";
}

let restarting = false, lastStatus = null;

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
  if (!confirm("Uruchomić serwer ponownie?" + busyNote())) return;
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
  const r = await admin("update_apply").catch(() => null);
  if (!r?.ok) { $("bUpd").disabled = $("bUpdCheck").disabled = false; return updText(r?.reason || "Serwer nie odpowiada."); }
  restarting = true;
  // okno panelu to ten sam EmuStart.exe — zamyka się, żeby pliki dało się podmienić
  const close = window.pywebview?.api?.close_for_update;
  if (close) setTimeout(() => close(st.version), 300);
}

/* ── sparowane urządzenia i parowanie kodem QR (emustart/devices.py) ── */
const KIND = { pc: "komputer", phone: "telefon" };
async function refreshDevices() {
  const r = await admin("devices").catch(() => null);
  if (!r) return;
  const enr = r.enroll || [];
  $("enrBox").hidden = !enr.length;
  $("enroll").innerHTML = enr.map((q) =>
    `<tr><td>${esc(q.name)}<div class="muted">${esc(KIND[q.class] || q.class)} · ${esc(q.ip)} · ${ago(q.created)}</div></td>` +
    `<td>${q.mode === "new" ? "nowe konto" : "konto"} <b>${esc(q.account)}</b>` +
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
  const reqs = r.requests || [];
  $("reqBox").hidden = !reqs.length;
  $("requests").innerHTML = reqs.map((q) =>
    `<tr><td>${esc(q.name)}<div class="muted">${esc(KIND[q.class] || q.class)} · ${esc(q.ip || "")}</div></td>` +
    `<td><code style="font-size:20px;letter-spacing:.1em">${esc(q.pretty)}</code></td>` +
    `<td class="n"><button class="primary" data-ok="${esc(q.code)}">Zatwierdź</button> ` +
    `<button class="danger" data-no="${esc(q.code)}">Odrzuć</button></td></tr>`).join("");
  $("requests").querySelectorAll("[data-ok],[data-no]").forEach((b) => b.addEventListener("click", async () => {
    const allow = !!b.dataset.ok;
    const res = await admin("pair_approve", { code: b.dataset.ok || b.dataset.no, allow }).catch(() => null);
    msg(res?.ok ? (allow ? `Dołączono: ${res.name}` : `Odrzucono: ${res.name}`) : (res?.reason || "Serwer nie odpowiada."));
    refreshDevices();
  }));
  $("devices").innerHTML = r.devices.length ? r.devices.map((d) =>
    `<tr class="${d.revoked ? "off" : ""}"><td>${esc(d.name)}<div class="muted">${esc(d.ip || "")}</div></td>` +
    `<td>${esc(KIND[d.class] || d.class)}</td>` +
    `<td>${d.admin ? "administrator" : `${esc(d.account || "—")} · ${d.games ? "z grami" : "bez gier"}`}` +
    `${d.keyed ? ' <span class="muted">(klucz urządzenia)</span>' : ""}` +
    `${d.revoked ? "" : ` <button data-acc="${esc(d.id)}" data-adm="${d.admin ? 1 : 0}">${d.admin ? "Odbierz admina" : "Nadaj admina"}</button>`}</td>` +
    `<td>${esc(d.version || "—")}</td>` +
    `<td class="n">${d.revoked ? "unieważnione" : ago(d.last_seen)}</td>` +
    `<td class="n">${d.revoked ? "" : `<button data-ren="${esc(d.id)}">Nazwa</button> <button class="danger" data-rev="${esc(d.id)}">Unieważnij</button>`}</td></tr>`).join("")
    : `<tr><td colspan="6" class="muted">Brak sparowanych urządzeń — „Dodaj urządzenie”. Urządzenia ze wspólnym kluczem serwera działają dalej.</td></tr>`;
  $("devices").querySelectorAll("[data-acc]").forEach((b) => b.addEventListener("click", async () => {
    const make = b.dataset.adm !== "1";
    if (!confirm(make ? "Nadać temu urządzeniu rolę administratora (zatwierdza wszystkie prośby, widzi wszystkie konta)?"
                      : "Odebrać rolę administratora?")) return;
    await admin("device_access", { id: b.dataset.acc, role: make ? "admin" : "user" }).catch(() => null);
    refreshDevices();
  }));
  $("devices").querySelectorAll("[data-rev]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Unieważnić to urządzenie? Straci dostęp do serwera, dopóki nie sparujesz go ponownie.")) return;
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
let pairTimer = null;
$("bPair").addEventListener("click", async () => {
  const r = await admin("pair_code").catch(() => null);
  if (!r?.ok) return msg("Serwer nie odpowiada.");
  $("pairQr").innerHTML = r.svg || "";
  $("pairQr").hidden = !r.svg;
  $("pairCode").textContent = r.pretty;
  $("pairUrl").textContent = r.url;
  $("pairBox").hidden = false;
  clearInterval(pairTimer);
  const tick = () => {
    const left = Math.max(0, r.expires - Date.now() / 1000);
    $("pairLeft").textContent = left ? `jeszcze ${Math.ceil(left / 60)} min` : "— wygasł, wygeneruj nowy";
    if (!left) { clearInterval(pairTimer); $("pairQr").innerHTML = ""; }
    refreshDevices();
  };
  tick();
  pairTimer = setInterval(tick, 3000);
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
                         "Wejdą tylko sparowane urządzenia z kluczem.")) return;
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

refresh();
refreshDevices();
refreshPublic();
setInterval(refreshPublic, 5000);
migrTick();
setInterval(refresh, 2000);
setInterval(refreshDevices, 4000);          // prośby o dołączenie pojawiają się od razu
