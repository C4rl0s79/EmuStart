/* Strona serwera EmuStart (emustart/webportal.py): niebo synthwave, logo z pikseli, logowanie Google,
 * po zalogowaniu — kod logowania urządzenia, programy do pobrania, hasło konta. */
"use strict";
const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ───────────── niebo: gwiazdy, słońce, siatka ───────────── */
(function sky() {
  const c = $("sky"), g = c.getContext("2d");
  let w = 0, h = 0, dpr = 1, stars = [];
  const resize = () => {
    dpr = Math.min(2, devicePixelRatio || 1);
    w = c.width = Math.floor(innerWidth * dpr); h = c.height = Math.floor(innerHeight * dpr);
    stars = Array.from({ length: Math.floor((w * h) / (9000 * dpr)) }, () => ({
      x: Math.random() * w, y: Math.random() * h * 0.62, r: Math.random() * 1.4 * dpr + 0.3, p: Math.random() * 6.3 }));
  };
  addEventListener("resize", resize);
  resize();
  const frame = (t) => {
    const hz = h * 0.64;                                   // horyzont
    const sky = g.createLinearGradient(0, 0, 0, hz);
    sky.addColorStop(0, "#07021a"); sky.addColorStop(0.55, "#1d0748"); sky.addColorStop(1, "#5a0e6b");
    g.fillStyle = sky; g.fillRect(0, 0, w, hz);
    for (const s of stars) {
      g.globalAlpha = 0.45 + 0.55 * Math.abs(Math.sin(s.p + t / 900));
      g.fillStyle = "#fff"; g.fillRect(s.x, s.y, s.r, s.r);
    }
    g.globalAlpha = 1;
    // słońce z paskami
    const R = Math.min(w, h) * 0.2, cx = w / 2, cy = hz - R * 0.18;
    const sun = g.createLinearGradient(0, cy - R, 0, cy + R);
    sun.addColorStop(0, "#ffe66d"); sun.addColorStop(0.5, "#ff7a59"); sun.addColorStop(1, "#ff2e97");
    g.save();
    g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.clip();
    g.fillStyle = sun; g.fillRect(cx - R, cy - R, 2 * R, 2 * R);
    g.fillStyle = "#1d0748";
    for (let i = 0; i < 7; i++) {
      const y = cy + R * (0.08 + i * 0.14) + ((t / 60) % (R * 0.14)) * 0.0;
      g.fillRect(cx - R, y, 2 * R, R * 0.025 * (i + 1));
    }
    g.restore();
    g.shadowColor = "rgba(255, 46, 151, 0.8)"; g.shadowBlur = 60 * dpr;
    g.beginPath(); g.arc(cx, cy, R, Math.PI, 0); g.strokeStyle = "rgba(255,46,151,0.25)"; g.lineWidth = 2 * dpr; g.stroke();
    g.shadowBlur = 0;
    // podłoga z siatką
    const floor = g.createLinearGradient(0, hz, 0, h);
    floor.addColorStop(0, "#2a0645"); floor.addColorStop(1, "#07021a");
    g.fillStyle = floor; g.fillRect(0, hz, w, h - hz);
    g.strokeStyle = "rgba(255, 63, 164, 0.75)"; g.lineWidth = 1.5 * dpr;
    g.shadowColor = "rgba(255, 63, 164, 0.9)"; g.shadowBlur = 8 * dpr;
    g.beginPath();
    for (let i = -24; i <= 24; i++) {                     // linie zbiegające się w horyzoncie
      g.moveTo(cx + i * w * 0.012, hz); g.lineTo(cx + i * w * 0.16, h);
    }
    const off = calm ? 0 : (t / 1400) % 1;
    for (let i = 0; i < 18; i++) {                        // linie poziome — płyną do widza
      const z = (i + off) / 18, y = hz + (h - hz) * z * z;
      g.moveTo(0, y); g.lineTo(w, y);
    }
    g.stroke(); g.shadowBlur = 0;
    g.fillStyle = "rgba(255, 120, 200, 0.9)"; g.fillRect(0, hz - dpr, w, 2 * dpr);
    if (!calm) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
  if (calm) addEventListener("resize", () => requestAnimationFrame(frame));
})();

/* ───────────── logo z pikseli (czcionka 5×7) ───────────── */
const FONT = {
  E: ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
  M: ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
  U: ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
  S: ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
  T: ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
  A: ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
  R: ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
};
function el(tag, attrs, parent) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function buildLogo(svg, animate) {
  const word = "EMUSTART", gap = 1, cw = 5 + gap, W = word.length * cw - gap, H = 7;
  svg.setAttribute("viewBox", `-1 -1 ${W + 2.6} ${H + 2.6}`);
  const defs = el("defs", {}, svg);
  const grad = el("linearGradient", { id: svg.id + "G", x1: "0", y1: "0", x2: "0", y2: "1" }, defs);
  el("stop", { offset: "0", "stop-color": "#fff6a8" }, grad);
  el("stop", { offset: "0.45", "stop-color": "#ff9a3c" }, grad);
  el("stop", { offset: "0.55", "stop-color": "#ff3fa4" }, grad);
  el("stop", { offset: "1", "stop-color": "#b23cff" }, grad);
  const shine = el("linearGradient", { id: svg.id + "S", x1: "0", y1: "0", x2: "1", y2: "0" }, defs);
  el("stop", { offset: "0", "stop-color": "#fff", "stop-opacity": "0" }, shine);
  el("stop", { offset: "0.5", "stop-color": "#fff", "stop-opacity": "0.85" }, shine);
  el("stop", { offset: "1", "stop-color": "#fff", "stop-opacity": "0" }, shine);
  const shadow = el("g", {}, svg), front = el("g", { fill: `url(#${svg.id}G)` }, svg);
  let n = 0;
  [...word].forEach((ch, li) => FONT[ch].forEach((row, y) => [...row].forEach((bit, x) => {
    if (bit !== "1") return;
    const px = li * cw + x;
    const delay = `${(li * 90 + y * 25 + Math.random() * 120) | 0}ms`;
    for (const [layer, d, cls] of [[shadow, 0.55, "px sh"], [front, 0, "px"]]) {
      const r = el("rect", { x: px + d, y: y + d, width: 1.02, height: 1.02, class: cls }, layer);
      if (animate) r.style.setProperty("--d", delay); else r.style.animation = "none";
    }
    n++;
  })));
  // błysk przesuwający się po logo
  const clip = el("clipPath", { id: svg.id + "C" }, defs);
  front.querySelectorAll("rect").forEach((r) => clip.appendChild(r.cloneNode()));
  const glint = el("rect", { x: -6, y: -1, width: 4, height: H + 2, fill: `url(#${svg.id}S)`,
                             "clip-path": `url(#${svg.id}C)`, class: "glint" }, svg);
  if (!calm) {
    const a = el("animate", { attributeName: "x", values: `-6;${W + 6};${W + 6}`, keyTimes: "0;0.35;1",
                              dur: "4.5s", begin: animate ? "1.8s" : "0s", repeatCount: "indefinite" }, glint);
    a.setAttribute("calcMode", "spline"); a.setAttribute("keySplines", "0.4 0 0.2 1;0 0 1 1");
  }
  return n;
}

/* ───────────── duszki ───────────── */
const SPRITES = {
  invader: { c: "#5dffa8", m: ["00100000100", "00010001000", "00111111100", "01101110110", "11111111111", "10111111101", "10100000101", "00011011000"] },
  invader2: { c: "#2de2ff", m: ["00100000100", "10010001001", "10111111101", "11101110111", "11111111111", "01111111110", "00100000100", "01000000010"] },
  pad: { c: "#c7b8ff", m: ["011111111111110", "111111111111111", "110111111111011", "100011111110101", "110111111111011",
                           "111111111111111", "111110000011111", "011100000001110"] },
  coin: { c: "#ffd23f", m: ["011110", "110011", "110111", "110111", "110111", "110011", "011110"] },
  heart: { c: "#ff3fa4", m: ["0110110", "1111111", "1111111", "0111110", "0011100", "0001000"] },
};
document.querySelectorAll("[data-sprite]").forEach((svg) => {
  const s = SPRITES[svg.dataset.sprite];
  s.m.forEach((row, y) => [...row].forEach((b, x) => { if (b === "1") el("rect", { x, y, width: 1.02, height: 1.02, fill: s.c }, svg); }));
});

/* ───────────── napis pisany na ekranie ───────────── */
function typeLoop(lines) {
  const out = $("typed");
  if (calm) { out.textContent = lines[0]; return; }
  let li = 0, i = 0, back = false;
  const step = () => {
    const s = lines[li];
    if (!back) { i++; if (i > s.length) { back = true; return setTimeout(step, 2200); } }
    else { i--; if (i === 0) { back = false; li = (li + 1) % lines.length; } }
    out.textContent = s.slice(0, i);
    setTimeout(step, back ? 28 : 55);
  };
  step();
}

/* ───────────── stan i widoki ───────────── */
const ERRORS = {
  "brak-zaproszenia": "To konto Google nie ma zaproszenia na ten serwer. Poproś administratora o zaproszenie dla swojego adresu.",
  "inny-adres": "To zaproszenie jest dla innego adresu Google. Zaloguj się kontem, na które przyszło zaproszenie.",
  "konfiguracja": "Logowanie Google nie jest jeszcze skonfigurowane na serwerze.",
  "sesja": "Logowanie przerwane albo wygasło. Spróbuj jeszcze raz.",
  "anulowane": "Logowanie anulowane.",
  "google": "Google nie potwierdził logowania. Spróbuj jeszcze raz.",
  "zajety": "Serwer jest teraz zajęty. Spróbuj za chwilę.",
};
const q = new URLSearchParams(location.search);
const invite = q.get("zaproszenie") || "";

async function api(path, body) {
  const r = await fetch(path, body === undefined ? { credentials: "same-origin" } : {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-EmuStart-Web": "1" }, body: JSON.stringify(body) });
  return r.json().catch(() => ({ ok: false, reason: `Błąd serwera (${r.status}).` }));
}

async function init() {
  const me = await api("/web/me" + (invite ? `?zaproszenie=${encodeURIComponent(invite)}` : ""));
  if (q.has("blad") || q.has("zaproszenie")) history.replaceState(null, "", "/");
  if (me.logged) return showHome(me);
  $("start").hidden = false;
  buildLogo($("logo"), true);
  typeLoop(["Twoja kolekcja gier.", "Każdy ekran.", "Zapisy zawsze z tobą.", "Weź pada. Graj."]);
  if (invite) $("login").href = `/auth/google?zaproszenie=${encodeURIComponent(invite)}`;
  $("invite").hidden = !me.invite;
  const err = q.get("blad");
  if (err) { $("error").textContent = ERRORS[err] || "Nie udało się zalogować."; $("error").hidden = false; }
  else if (invite && !me.invite) { $("error").textContent = "Link zaproszenia wygasł albo został już wykorzystany."; $("error").hidden = false; }
  if (!me.google) {
    $("login").classList.add("off");
    $("error").textContent = ERRORS.konfiguracja; $("error").hidden = false;
  }
}

function fmtBytes(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i > 1 ? 1 : 0)} ${u[i]}`;
}

function showHome(me) {
  $("home").hidden = false;
  buildLogo($("logoSmall"), false);
  $("acct").textContent = me.account;
  $("email").textContent = me.email;
  const box = $("apps");
  box.textContent = "";
  for (const a of me.apps) {
    const n = $("appTpl").content.firstElementChild.cloneNode(true);
    n.querySelector("h3").textContent = a.name;
    n.querySelector(".platform").textContent = a.platform;
    n.querySelector(".desc").textContent = a.desc;
    const img = n.querySelector("img"), ph = n.querySelector(".shot-ph");
    img.alt = `${a.name} — zrzut ekranu`;
    img.addEventListener("error", () => { img.hidden = true; ph.hidden = false; ph.textContent = a.name; });
    img.src = `/portal/shots/${a.shot}`;
    const btn = n.querySelector("a.btn");
    if (a.ready) {
      btn.href = `/dl/${a.id}`;
      btn.textContent = "Pobierz";
      n.querySelector(".meta").textContent = `wersja ${a.version} · ${fmtBytes(a.size)}`;
    } else {
      btn.removeAttribute("href"); btn.classList.add("off");
      btn.textContent = "Wkrótce";
      n.querySelector(".meta").textContent = "serwer jeszcze pobiera ten program";
    }
    box.appendChild(n);
  }
  $("pwState").textContent = me.password
    ? "Konto ma hasło. Możesz je zmienić. Hasło przydaje się przy „Mam konto” i przy dodaniu konta na wspólnym urządzeniu."
    : "Konto nie ma hasła. Nie jest potrzebne — urządzenia logujesz kodem. Ustaw je, jeśli chcesz dodać konto na wspólnym urządzeniu (Inny użytkownik).";
  $("pwForm").dataset.rules = me.rules || "";
}

let codeTimer = null, codeUri = "";
$("newCode").addEventListener("click", async () => {
  $("newCode").disabled = true;
  const r = await api("/web/code", {});
  $("newCode").disabled = false;
  if (r.ok === false && r.reason === "Zaloguj się ponownie.") return location.reload();
  if (!r.ok) { alert(r.reason || "Nie udało się."); return; }
  $("qr").innerHTML = r.svg;                    // SVG kodu QR z serwera (segno)
  $("code").textContent = r.pretty;
  $("codeServer").textContent = r.server;
  codeUri = r.uri;
  $("codeBox").hidden = false;
  $("newCode").textContent = "Nowy kod";
  const end = Date.now() + r.expires_in * 1000;
  clearInterval(codeTimer);
  const tick = () => {
    const left = Math.max(0, Math.round((end - Date.now()) / 1000));
    $("codeLeft").textContent = left ? `ważny jeszcze ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} · jednorazowy` : "wygasł — weź nowy kod";
    if (!left) { clearInterval(codeTimer); $("codeBox").hidden = true; codeUri = ""; }
  };
  tick();
  codeTimer = setInterval(tick, 1000);
});
$("copyLink").addEventListener("click", async () => {
  if (!codeUri) return;
  try { await navigator.clipboard.writeText(codeUri); $("copyLink").textContent = "Skopiowano ✓"; }
  catch (e) { prompt("Skopiuj link:", codeUri); }
  setTimeout(() => { $("copyLink").textContent = "Kopiuj link"; }, 2500);
});
$("logout").addEventListener("click", async () => { await api("/web/logout", {}); location.href = "/"; });
$("pwForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pw = $("pw1").value;
  if (pw !== $("pw2").value) { $("pwMsg").textContent = "Hasła się różnią."; return; }
  const r = await api("/web/password", { password: pw });
  $("pwMsg").textContent = r.ok ? "Hasło ustawione." : (r.reason || $("pwForm").dataset.rules || "Nie udało się.");
  if (r.ok) { $("pw1").value = ""; $("pw2").value = ""; }
});

init();
