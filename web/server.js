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
function msg(text, ok) {
  $("msg").textContent = text || "";
  $("msg").style.color = ok ? "var(--ok)" : "var(--warn)";
}

let restarting = false, lastStatus = null;

/* ── zakładki ── */
function showTab(t) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  document.querySelectorAll("main > section").forEach((s) => { s.hidden = s.dataset.in !== t; });
  try { localStorage.setItem("tab", t); } catch (e) { /* bez pamięci zakładki */ }
  if (t === "konfig") refreshConfig();
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ── konfiguracja: foldery (emustart/srvconfig.py) ── */
let cfgFields = [];
function cfgMsg(text, ok) {
  $("cfgMsg").textContent = text || "";
  $("cfgMsg").style.color = ok ? "var(--ok)" : "var(--warn)";
}
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
    `<button data-adm="${esc(a.name)}" data-on="${a.admin ? 1 : 0}">${a.admin ? "Odbierz admina" : "Administrator"}</button></td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">Brak kont.</td></tr>`;
  $("accounts").querySelectorAll("[data-pw]").forEach((b) => b.addEventListener("click", () => askAccountPassword(b.dataset.pw)));
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
    if (!left) { clearInterval(pairTimer); $("pairQr").innerHTML = ""; $("pairLink").value = ""; }
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
