package io.github.c4rl0s79.emustart

import android.net.Uri
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.WebResourceResponse
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Funkcje dla interfejsu EmuStart (ten sam HTML/JS co na Windows) — odpowiednik
 * emustart/api.py. Interfejs woła `EmuAndroid.call(id, nazwa, argumenty)`, wynik
 * wraca przez `window.__emuResolve(id, json)`.
 */
class Bridge(private val act: MainActivity) {
    private val prefs = Prefs(act)
    private val server = Server(prefs)
    private val saves = Saves(act, server, prefs)
    private val pool = Executors.newFixedThreadPool(6)
    private val tag = "EmuStart"
    @Volatile private var profList: List<String> = emptyList()
    @Volatile private var profPin: Map<String, Boolean> = emptyMap()
    private val installer by lazy { Installer(act) }
    private val emuProfile by lazy { EmuProfile(server, prefs) }      // profil → ma PIN (serwer 0.27+)

    init {
        pool.execute {
            try { saves.retryPending() } catch (e: Exception) { Log.w(tag, "zaległe zapisy: $e") }
            // blokada profilu z poprzedniego uruchomienia (aplikację zamknięto w trakcie gry)
            try { if (launch == null) saves.releaseStaleLock(prefs.profile) } catch (e: Exception) { }
        }
    }

    @Volatile private var systemsCache: JSONArray? = null
    private val gamesCache = HashMap<String, JSONArray>()
    @Volatile private var online = false

    // ── uruchamianie gry ──
    private class Launch(val game: JSONObject, val sys: JSONObject) {
        @Volatile var phase = "preparing"
        @Volatile var message = ""
        @Volatile var progress: Progress? = null
        @Volatile var startedAt = 0L
        @Volatile var missingCore = ""
        var downloader: Downloader? = null
        var emu = ""
        var saveGame: Saves.Game? = null
        var profile = ""
        var lockedBy = ""
        var emuId = ""
        var startState = ""        // „Wczytaj zapis”: stan RetroArcha na serwerze (względem profilu)
    }
    @Volatile private var launch: Launch? = null

    @JavascriptInterface
    fun call(id: Int, name: String, args: String) {
        pool.execute {
            val res: Any? = try {
                dispatch(name, JSONArray(args))
            } catch (e: Exception) {
                Log.w(tag, "$name: $e")
                null
            }
            val json = when (res) {
                null -> "null"
                is JSONObject, is JSONArray -> res.toString()
                is String -> JSONObject.quote(res)
                is Boolean, is Number -> res.toString()
                is Map<*, *> -> JSONObject(res).toString()
                is List<*> -> JSONArray(res).toString()
                else -> JSONObject.quote(res.toString())
            }
            act.js("window.__emuResolve($id, ${JSONObject.quote(json)})")
        }
    }

    private fun dispatch(name: String, a: JSONArray): Any? = when (name) {
        "get_state" -> state()
        "list_systems" -> systems()
        "list_games" -> games(a.getString(0))
        "game_detail" -> detail(a.getInt(0))
        "set_variant" -> setVariant(a.getInt(0), a.optInt(1, 0))
        "get_settings" -> settings()
        "save_settings" -> { saveSettings(a.getJSONObject(0)); JSONObject().put("ok", true) }
        "server_scan" -> scanQr()
        "server_logout" -> logout()
        "server_enroll" -> enrollStart(a.optString(0, ""), a.optString(1, ""), a.optString(2, ""), a.optString(3, ""))
        "server_enroll_poll" -> enrollPoll()
        "server_enroll_cancel" -> { enrollCancel(); JSONObject().put("ok", true) }
        "enroll_pending" -> enrollPending()
        "enroll_decide" -> server.postAuthed("/v1/enroll/decide", JSONObject().put("id", a.optString(0, ""))
            .put("allow", a.optBoolean(1)).put("games", a.optBoolean(2)))
        "rescan" -> { systemsCache = null; synchronized(gamesCache) { gamesCache.clear() }; JSONObject().put("ok", true) }
        "launch" -> startLaunch(a.getInt(0), a.optBoolean(2, false), a.optString(1, ""))
        "ra_states" -> raStates(a.getInt(0))
        "launch_status" -> launchStatus()
        "launch_cancel" -> { launch?.downloader?.cancel?.set(true); null }
        "launch_dismiss" -> { val l = launch; if (l != null && l.phase in setOf("finished", "error", "cancelled")) launch = null; null }
        "toggle_pin" -> togglePin(a.getInt(0))
        // administrator: grafiki, opisy i wygląd systemów na serwerze (wszystkie urządzenia)
        "art_candidates" -> artCandidates(a.getInt(0), a.getString(1), a.optString(2, ""))
        "art_choose" -> artChange(a.getInt(0), "PUT", "kind=" + Server.enc(a.getString(1)) + "&url=" + Server.enc(a.getString(2)))
        "art_clear" -> artChange(a.getInt(0), "DELETE", "kind=" + Server.enc(a.getString(1)))
        "meta_save" -> adminGame(a.getInt(0)) { server.adminCall("PUT", "games/${a.getInt(0)}/meta", JSONObject().put("edits", a.optJSONObject(1) ?: JSONObject())) }
        "meta_fetch_full" -> adminGame(a.getInt(0)) { server.adminCall("PUT", "games/${a.getInt(0)}/meta/fetch?full=1") }
        "system_options" -> systemOptions(a.getString(0))
        "system_set" -> systemChange { server.adminCall("PUT", "systems/" + Server.enc(a.getString(0)), a.getJSONObject(1)) }
        "system_logo_candidates" -> logoCandidates(a.getString(0))
        "system_logo_choose" -> systemChange { server.adminCall("PUT", "systems/" + Server.enc(a.getString(0)) + "/logo?id=" + Server.enc(a.getString(1))) }
        "remove_local" -> removeLocal(a.getInt(0))
        "configure_emulator" -> configureEmulator(a.getInt(0))
        "install_missing" -> installMissing()
        "install_start" -> installStart(a.getJSONArray(0))
        "install_status" -> installer.status()
        "install_cancel" -> { installer.cancel.set(true); null }
        "app_update_check" -> appUpdateCheck()
        "app_update_start" -> appUpdateStart()
        "app_update_status" -> appUpdateStatus()
        "app_update_apply" -> { act.runOnUiThread { installer.next() }; JSONObject().put("ok", true).put("android", true) }
        "save_manager" -> saveManager(a.getInt(0))
        "save_copy" -> saveCopy(a.getInt(0), a.getString(1), a.getString(2), a.getString(3))
        "look_save" -> {
            prefs.look = a.getJSONObject(0); prefs.lookTime = System.currentTimeMillis()
            pool.execute { pushLook() }             // wygląd na koncie — inne telefony tego konta też
            JSONObject().put("ok", true)
        }
        "account_request" -> accountRequest(a.getString(0), a.getString(1))
        "account_requests_poll" -> accountRequestsPoll()
        "quit" -> { act.runOnUiThread { act.finishAndRemoveTask() }; null }
        "copy_status" -> JSONArray()
        "post_status" -> saves.busy
        "art_status" -> null
        "art_for" -> JSONObject()
        "system_info" -> systemInfo(a.optString(0, ""))
        "profiles_list" -> profilesList()
        "profile_select", "profile_setup" -> selectProfile(a.getInt(0))
        "profile_create" -> createProfile(a.getString(0), a.optString(1, ""))
        "profile_join" -> joinProfile(a.getString(0), a.getString(1))
        "profile_login" -> loginProfile(a.getInt(0), a.getString(1))
        "profile_set_password" -> setPassword(a.getInt(0), a.getString(1), a.optString(2, ""))
        "profile_unlock" -> unlockPin(a.getInt(0), a.getString(1))
        "profile_set_pin" -> setLocalPin(a.getInt(0), a.getString(1), a.optString(2, ""))
        "profile_clear_pin" -> clearLocalPin(a.getInt(0), a.getString(1))
        "profile_logout" -> {
            profList.getOrNull(a.getInt(0) - 1)?.let { prefs.setToken(it, ""); pinUnlocked.remove(it.lowercase()) }
            JSONObject().put("ok", true)
        }
        "profile_pin_check" -> JSONObject().put("state", pinState(prefs.profile)).put("name", prefs.profile)
        "profile_rename", "profile_delete", "profile_nas_rename" -> JSONObject().put("ok", false)
            .put("reason", "Nazwę i folder profilu zmienia się w EmuStart na komputerze.")
        "pad_backend" -> "android"
        "browse" -> browse(a.optString(0, ""))
        "pick_folder" -> null                       // okno wyboru folderu Windows — tu przeglądarka padem (browse)
        "ingame_poll" -> JSONObject().put("open", false).put("seq", 0).put("inputs", JSONArray())
        "meta_fetch", "request_art" -> JSONObject().put("ok", true)   // opisy i grafiki robi serwer
        "ui_log" -> { Log.i(tag, "UI: " + a.optString(0, "")); null }
        // pozostałe (grafiki, pady, instalacja emulatorów, opcje gier i systemów, RetroAchievements,
        // skanowanie) są tylko w EmuStart na komputerze — tests/test_android_api.py pilnuje listy
        else -> JSONObject().put("ok", false)
            .put("reason", "Na telefonie niedostępne — zmień to w EmuStart na komputerze z grami.")
    }

    /** Przeglądarka folderów dla pada (jak na Windows): pamięć telefonu i karty SD, potem podfoldery. */
    private fun browse(path: String): JSONObject {
        val roots = (listOf(android.os.Environment.getExternalStorageDirectory()) +
            (File("/storage").listFiles()?.filter { it.isDirectory && it.canRead() && it.name != "emulated" && it.name != "self" }
                ?: emptyList()))
        if (path.isEmpty()) {
            val arr = JSONArray()
            roots.forEachIndexed { i, r ->
                arr.put(JSONObject().put("name", if (i == 0) "Pamięć telefonu" else "Karta ${r.name}")
                    .put("path", r.absolutePath).put("info", ""))
            }
            return JSONObject().put("path", "").put("parent", JSONObject.NULL).put("entries", arr).put("systems", 0)
        }
        val d = File(path)
        val parent = if (roots.any { it.absolutePath == d.absolutePath }) "" else (d.parent ?: "")
        val subs = d.listFiles()?.filter { it.isDirectory && !it.name.startsWith(".") }?.sortedBy { it.name.lowercase() }
            ?: return JSONObject().put("path", d.absolutePath).put("parent", parent).put("entries", JSONArray())
                .put("systems", 0).put("error", "brak dostępu")
        val arr = JSONArray()
        for (x in subs) {
            // podpowiedź: co emulator trzyma w tym folderze
            val inside = setOf("memcards", "sstates", "saves", "states").filter { File(x, it).isDirectory }
            val info = when {
                x.name.equals("memcards", true) -> "karty pamięci"
                x.name.equals("sstates", true) || x.name.equals("states", true) -> "stany gry"
                x.name.equals("saves", true) -> "zapisy"
                inside.isNotEmpty() -> "zawiera: " + inside.joinToString(", ")
                x.name == "Android" -> "niedostępne dla innych aplikacji"
                else -> ""
            }
            arr.put(JSONObject().put("name", x.name).put("path", x.absolutePath).put("info", info))
        }
        return JSONObject().put("path", d.absolutePath).put("parent", parent).put("entries", arr).put("systems", 0)
    }

    // ── stan i listy ──
    private fun state(): JSONObject {
        // przypisany do konta, a bez profilu (np. zatwierdzenie bez połączenia) — konto jako profil
        if (prefs.deviceId.isNotEmpty() && prefs.profile.isEmpty() && prefs.serverAccount.isNotEmpty()) {
            prefs.profile = prefs.serverAccount
            if (prefs.phoneOwner.isEmpty()) prefs.phoneOwner = prefs.serverAccount
        }
        val sys = try { systems() } catch (e: Exception) { JSONArray() }
        if (profList.isEmpty() && online) try { loadProfiles() } catch (e: Exception) { }
        return JSONObject()
            .put("version", BuildConfig.VERSION_NAME).put("platform", "android")
            .put("configured", prefs.configured).put("rom_online", online)
            .put("network_mode", "remote").put("systems", sys).put("copying", JSONArray())
            .put("games_logo", prefs.gamesLogo).put("look", prefs.look).put("look_scope", "profile")
            .put("setup_needed", prefs.configured && online && prefs.profile.isEmpty())
            .put("ask_profile", false).put("conflicts", drainConflicts())
            .put("profile", JSONObject().put("id", profileId()).put("name", prefs.profile.ifEmpty { "—" }))
            .put("profiles", profList.size.coerceAtLeast(1))
            .put("account", if (prefs.deviceId.isNotEmpty()) prefs.serverAccount else "")
            .put("server", if (prefs.configured) JSONObject().put("ok", online).put("name", "")
                .put("checking", checking && !online).put("key_rejected", keyRejected) else JSONObject.NULL)
            .put("py_pad", false).put("pad_backend", "android")
            .put("admin", server.admin)
    }

    // ── administrator: zmiany na serwerze, potem lista z serwera od nowa ──
    private fun refreshGames(es: String) {
        if (es.isEmpty()) return
        synchronized(gamesCache) { gamesCache.remove(es) }
        cacheFile("gry-" + safe(es)).delete()
        loadGamesInBackground(es)            // po pobraniu interfejs odświeży listę sam
    }

    private fun adminGame(id: Int, call: () -> JSONObject): JSONObject {
        val r = try { call() } catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)) }
        if (r.optBoolean("ok")) pool.execute { try { refreshGames(server.game(id).optString("es")) } catch (e: Exception) { } }
        return r
    }

    private fun artCandidates(id: Int, kind: String, q: String): JSONArray = try {
        val r = server.adminCall("GET", "games/$id/art?kind=" + Server.enc(kind) + "&q=" + Server.enc(q))
        val list = r.optJSONArray("list") ?: JSONArray()
        for (i in 0 until list.length()) list.getJSONObject(i).let { c ->
            c.put("thumb", web(c.optString("thumb").ifEmpty { c.optString("url") }))
        }
        list
    } catch (e: Exception) { Log.w(tag, "propozycje grafik: $e"); JSONArray() }

    private fun artChange(id: Int, method: String, q: String): JSONObject {
        val r = adminGame(id) { server.adminCall(method, "games/$id/art?$q") }
        if (r.optString("url").isNotEmpty()) r.put("url", remote(r.optString("url")))
        return r
    }

    /** Adres z serwera do pokazania w WebView: względny — przez serwer (media), pełny — wprost. */
    private fun web(url: String): String = if (url.startsWith("/")) remote(url) else url

    private fun systemOptions(es: String): JSONObject {
        val all = systems()
        val s = (0 until all.length()).map { all.getJSONObject(it) }.firstOrNull { it.optString("es") == es }
            ?: JSONObject()
        return JSONObject().put("es", es).put("display", s.optString("display", es)).put("default_name", s.optString("display", es))
            .put("logo", s.optString("logo")).put("glow", s.optBoolean("logo_glow")).put("admin", server.admin)
            .put("emulator", "").put("options", JSONArray()).put("install", JSONArray())
    }

    private fun systemChange(call: () -> JSONObject): JSONObject {
        val r = try { call() } catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)) }
        if (r.optBoolean("ok")) {
            systemsCache = null
            pool.execute {
                try { fetchSystems() } catch (e: Exception) { }
                act.js("window.refreshState && refreshState().then(() => S.screen === 'systems' && renderSystems())")
            }
        }
        return r
    }

    private fun logoCandidates(es: String): JSONArray = try {
        val list = server.adminCall("GET", "systems/" + Server.enc(es) + "/logos").optJSONArray("list") ?: JSONArray()
        for (i in 0 until list.length()) list.getJSONObject(i).let { c -> c.put("url", web(c.optString("url"))) }
        list
    } catch (e: Exception) { Log.w(tag, "propozycje logo: $e"); JSONArray() }

    private fun cacheFile(name: String) = File(act.filesDir, "lista-$name.json")

    @Volatile private var learnedAt = 0L

    init {
        try {
            val raw = android.provider.Settings.Secure.getString(act.contentResolver, android.provider.Settings.Secure.ANDROID_ID) ?: ""
            if (raw.isNotEmpty()) server.hwId = MessageDigest.getInstance("SHA-256")
                .digest(("emustart-hw|" + raw).toByteArray()).joinToString("") { "%02x".format(it) }.take(32)
        } catch (e: Exception) { Log.w(tag, "identyfikator sprzętu: $e") }
    }
    private val sysInfo = HashMap<String, JSONObject>()
    @Volatile private var keyRejected = false

    // ── wybór emulatora / rdzenia na koncie (<profil>/settings/android/emulators.json) ──
    private fun emuRel(): String? = prefs.profile.takeIf { it.isNotEmpty() }?.let { "$it/settings/android/emulators.json" }

    private fun pushEmuChoices() {
        val rel = emuRel() ?: return
        try {
            val o = try { server.nasRead(rel)?.let { JSONObject(String(it)) } } catch (e: Exception) { null } ?: JSONObject()
            for ((es, v) in prefs.emulatorChoices()) {
                val cur = o.optJSONObject(es)
                if (cur == null || cur.optLong("t") < v.second) o.put(es, JSONObject().put("id", v.first).put("t", v.second))
            }
            server.nasPut(rel, o.toString(1).toByteArray(), System.currentTimeMillis() / 1000.0)
        } catch (e: Exception) { Log.w(tag, "wybór emulatorów na serwer: $e") }
    }

    /** Wybory z innych telefonów tego konta (nowszy wygrywa) — nowy telefon dostaje je od razu. */
    private fun pullEmuChoices() {
        val rel = emuRel() ?: return
        try {
            val o = server.nasRead(rel)?.let { JSONObject(String(it)) } ?: return
            var changed = false
            for (es in o.keys()) {
                val v = o.optJSONObject(es) ?: continue
                if (v.optLong("t") > prefs.emulatorTime(es) && v.optString("id").isNotEmpty()) {
                    prefs.setEmulatorChoice(es, v.optString("id"), v.optLong("t"))
                    changed = true
                }
            }
            if (changed) { systemsCache = null; Log.i(tag, "wybór emulatorów z konta zastosowany") }
            if (prefs.emulatorChoices().any { (es, v) -> o.optJSONObject(es)?.optLong("t") ?: 0L < v.second }) pushEmuChoices()
        } catch (e: Exception) { Log.w(tag, "wybór emulatorów z serwera: $e") }
    }

    // ── wygląd na koncie (<profil>/settings/android/look.json) — telefony konta mają ten sam ──
    private fun lookRel(): String? = prefs.profile.takeIf { it.isNotEmpty() }?.let { "$it/settings/android/look.json" }

    private fun pushLook() {
        val rel = lookRel() ?: return
        try {
            server.nasPut(rel, JSONObject().put("look", prefs.look).put("t", prefs.lookTime).toString(1).toByteArray(),
                          System.currentTimeMillis() / 1000.0)
        } catch (e: Exception) { Log.w(tag, "wygląd na serwer: $e") }
    }

    private fun pullLook() {
        val rel = lookRel() ?: return
        try {
            val o = server.nasRead(rel)?.let { JSONObject(String(it)) }
            val t = o?.optLong("t") ?: 0L
            when {
                o != null && t > prefs.lookTime -> {
                    prefs.look = o.optJSONObject("look") ?: JSONObject(); prefs.lookTime = t
                    Log.i(tag, "wygląd z konta zastosowany")
                }
                prefs.lookTime > t -> pushLook()
            }
        } catch (e: Exception) { Log.w(tag, "wygląd z serwera: $e") }
    }

    // ── nowe konto z tego telefonu (Inny użytkownik → nazwa, której nie ma) — zatwierdza administrator ──
    private fun accountRequest(name: String, password: String): JSONObject {
        val r = try { server.postAuthed("/v1/enroll/account", JSONObject().put("account", name).put("password", password)) }
            catch (e: Exception) { return JSONObject().put("ok", false).put("reason", explain(e)) }
        if (!r.optBoolean("ok")) return JSONObject().put("ok", false)
            .put("reason", r.optString("reason", r.optString("error", "Serwer nie przyjął prośby."))).put("exists", r.optBoolean("exists"))
        val acc = r.optString("account", name)
        if (r.optString("status") == "approved") {
            prefs.setToken(acc, r.optString("token"))
            try { loadProfiles() } catch (e: Exception) { }
            return JSONObject().put("ok", true).put("status", "approved")
                .put("profile", JSONObject().put("id", profList.indexOf(acc) + 1).put("name", acc))
        }
        val pend = try { JSONArray(prefs.accountRequests) } catch (e: Exception) { JSONArray() }
        val keep = JSONArray()
        for (i in 0 until pend.length()) pend.optJSONObject(i)?.takeIf { it.optString("account") != acc }?.let { keep.put(it) }
        keep.put(JSONObject().put("rid", r.optString("rid")).put("account", acc).put("code", r.optString("pretty")))
        prefs.accountRequests = keep.toString()
        return JSONObject().put("ok", true).put("status", "pending").put("account", acc).put("code", r.optString("pretty"))
    }

    private fun accountRequestsPoll(): JSONObject {
        val done = JSONArray()
        val pend = try { JSONArray(prefs.accountRequests) } catch (e: Exception) { JSONArray() }
        if (pend.length() == 0 || !prefs.configured) return JSONObject().put("ok", true).put("done", done)
        val keep = JSONArray()
        for (i in 0 until pend.length()) {
            val p = pend.optJSONObject(i) ?: continue
            val r = try { server.postAuthed("/v1/enroll/account_poll", JSONObject().put("rid", p.optString("rid"))) }
                catch (e: Exception) { JSONObject() }
            when (if (r.optBoolean("ok")) r.optString("status") else "error") {
                "approved" -> if (r.optString("token").isNotEmpty()) {
                    prefs.setToken(r.optString("account", p.optString("account")), r.optString("token"))
                    done.put(JSONObject().put("account", p.optString("account")).put("status", "approved"))
                } else keep.put(p)
                "denied", "expired" -> done.put(JSONObject().put("account", p.optString("account")).put("status", r.optString("status")))
                else -> keep.put(p)
            }
        }
        if (keep.length() != pend.length()) {
            prefs.accountRequests = keep.toString()
            try { loadProfiles() } catch (e: Exception) { }
        }
        return JSONObject().put("ok", true).put("done", done)
    }

    /** Opis platformy do karuzeli (producent, lata, nośnik, opis) — z serwera, jak na komputerze. */
    private fun systemInfo(es: String): JSONObject {
        synchronized(sysInfo) { sysInfo[es]?.let { return it } }
        val f = cacheFile("opis-" + safe(es))
        val d = try {
            JSONObject(server.open("/v1/sysinfo?es=" + Server.enc(es), timeoutMs = 15000).let { c ->
                try { c.inputStream.bufferedReader(Charsets.UTF_8).readText() } finally { c.disconnect() } })
        } catch (e: Exception) {
            if (f.isFile) JSONObject(f.readText()) else JSONObject()
        }
        if (!d.optBoolean("pending") && d.length() > 0) {
            synchronized(sysInfo) { sysInfo[es] = d }
            try { f.writeText(d.toString()) } catch (e: Exception) { }
        }
        return d
    }

    /** Adresy serwera (sieć lokalna, Tailscale, publiczny, tunel) i odcisk certyfikatu — po
     *  udanym połączeniu, najwyżej raz na godzinę. Bez tego telefon znał tylko adres wpisany
     *  przy zalogowaniu i po wyłączeniu Tailscale nie miał dokąd się połączyć. */
    private fun learnAddresses() {
        val now = System.currentTimeMillis()
        if (now - learnedAt < 3_600_000) return
        learnedAt = now
        pool.execute {
            try { server.info() } catch (e: Exception) { learnedAt = 0L; Log.w(tag, "adresy serwera: $e") }
            pullEmuChoices()
            pullLook()
        }
    }

    @Volatile private var systemsFailAt = 0L
    /** Od startu aplikacji serwer jeszcze nie odpowiedział ani nie zawiódł — pasek: „łączę…”. */
    @Volatile private var checking = true
    private val systemsRefreshing = java.util.concurrent.atomic.AtomicBoolean(false)

    /** Lista systemów bez czekania na sieć: z pamięci (ostatnie połączenie), a serwer w tle —
     *  po odpowiedzi interfejs odświeża się sam. Wcześniej start czekał na wybór adresu
     *  serwera i logowanie (z wyłączonym Tailscale ponad 10 s), zanim cokolwiek się pokazało. */
    private fun systems(): JSONArray {
        // po błędzie połączenia lista z pamięci — ale nie na zawsze: ponowna próba co 30 s
        if (!online && systemsCache != null && System.currentTimeMillis() - systemsFailAt > 30_000) systemsCache = null
        systemsCache?.let { return it }
        if (!prefs.configured) return JSONArray()
        val f = cacheFile("systemy")
        if (f.isFile) {
            if (systemsRefreshing.compareAndSet(false, true)) pool.execute {
                try { fetchSystems() } finally { systemsRefreshing.set(false) }
                act.js("window.refreshState && refreshState().then(() => S.screen === 'systems' && renderSystems())")
            }
            return decorate(try { JSONArray(f.readText()) } catch (e: Exception) { JSONArray() })
        }
        return fetchSystems()                 // pierwsze uruchomienie — nie ma czego pokazać bez serwera
    }

    private fun fetchSystems(): JSONArray {
        val arr = try {
            server.systems().also { online = true; keyRejected = false; cacheFile("systemy").writeText(it.toString()); learnAddresses() }
        } catch (e: Exception) {
            online = false
            systemsFailAt = System.currentTimeMillis()
            keyRejected = e is Server.HttpError && e.code == 401      // klucz unieważniony / nieznany
            Log.w(tag, "serwer: $e")
            val f = cacheFile("systemy")
            if (f.isFile) JSONArray(f.readText()) else JSONArray()
        }
        checking = false
        systemsCache = decorate(arr)
        return systemsCache!!
    }

    private fun decorate(arr: JSONArray): JSONArray {
        for (i in 0 until arr.length()) {
            val s = arr.getJSONObject(i)
            s.put("logo", remote(s.optString("logo")))
            s.put("emulator", emuLabel(s, chosenEmu(s)))
        }
        return arr
    }

    private fun sysByEs(es: String): JSONObject? {
        val arr = systems()
        for (i in 0 until arr.length()) if (arr.getJSONObject(i).optString("es") == es) return arr.getJSONObject(i)
        return null
    }

    private val gamesLoading = java.util.Collections.synchronizedSet(mutableSetOf<String>())
    private val gamesFailAt = java.util.concurrent.ConcurrentHashMap<String, Long>()

    /** Lista gier systemu bez czekania na całą: z pamięci (ostatnie połączenie) od razu
     *  i odświeżenie w tle; bez listy w pamięci — 100 gier z góry i 100 z dołu, reszta
     *  dociągana w tle porcjami. Nieudane pobranie nigdy nie zostaje jako „zero gier”
     *  (wcześniej zostawało do ręcznego przeładowania — FBA z tysiącami gier). */
    private fun games(es: String): JSONArray {
        synchronized(gamesCache) { gamesCache[es]?.let { return decorate(es, it) } }
        val f = cacheFile("gry-" + safe(es))
        if (f.isFile) {
            val arr = gameUrls(try { JSONArray(f.readText()) } catch (e: Exception) { JSONArray() })
            synchronized(gamesCache) { gamesCache[es] = arr }
            loadGamesInBackground(es)
            return decorate(es, arr)
        }
        var whole = false                            // cała lista przyszła od razu (≤ 100 gier)
        val head = try {
            val (total, top) = server.gamesPage(es, 0, 100)
            online = true
            if (total > top.length()) {
                val bottom = server.gamesPage(es, maxOf(top.length(), total - 100), 100).second
                for (i in 0 until bottom.length()) top.put(bottom.get(i))
                loadGamesInBackground(es)            // reszta listy w tle
            } else {
                f.writeText(top.toString())         // mała lista — od razu cała
                whole = true
            }
            gameUrls(top)
        } catch (e: Exception) {
            online = false
            gamesFailAt[es] = System.currentTimeMillis()
            Log.w(tag, "lista gier $es: $e")
            loadGamesInBackground(es, delayMs = 15_000)
            JSONArray()
        }
        if (whole) synchronized(gamesCache) { gamesCache[es] = head }
        return decorate(es, head)
    }

    /** Cała lista gier porcjami po 1000 (każda z ponowieniem) → pamięć, plik i odświeżenie
     *  interfejsu. Nieudane — ponowienie za 30 s, dopóki lista nie przyjdzie. */
    private fun loadGamesInBackground(es: String, delayMs: Long = 0) {
        if (!gamesLoading.add(es)) return
        pool.execute {
            try {
                if (delayMs > 0) Thread.sleep(delayMs)
                val all = JSONArray()
                var off = 0
                var total = Int.MAX_VALUE
                while (off < total) {
                    var page: Pair<Int, JSONArray>? = null
                    for (attempt in 0..2) {
                        page = try { server.gamesPage(es, off, 1000) } catch (e: Exception) {
                            Log.w(tag, "lista gier $es od $off: $e"); Thread.sleep(2000L * (attempt + 1)); null }
                        if (page != null) break
                    }
                    if (page == null) throw java.io.IOException("lista gier $es: brak odpowiedzi")
                    total = page.first
                    for (i in 0 until page.second.length()) all.put(page.second.get(i))
                    if (page.second.length() == 0) break
                    off += page.second.length()
                }
                online = true
                cacheFile("gry-" + safe(es)).writeText(all.toString())
                synchronized(gamesCache) { gamesCache[es] = gameUrls(all) }
                gamesFailAt.remove(es)
                act.js("window.__emuGamesUpdated && window.__emuGamesUpdated(${JSONObject.quote(es)})")
            } catch (e: Exception) {
                gamesFailAt[es] = System.currentTimeMillis()
                Log.w(tag, "lista gier $es w tle: $e")
                gamesLoading.remove(es)
                loadGamesInBackground(es, delayMs = 30_000)
                return@execute
            }
            gamesLoading.remove(es)
        }
    }

    private fun gameUrls(arr: JSONArray): JSONArray {
        for (i in 0 until arr.length()) {
            val g = arr.getJSONObject(i)
            for (k in listOf("box", "snap", "logo")) g.put(k, remote(g.optString(k)))
        }
        return arr
    }

    /** Stan lokalny: gra w telefonie, przypięta. */
    private fun decorate(es: String, arr: JSONArray): JSONArray {
        val idx = Cache.index(act)
        for (i in 0 until arr.length()) {
            val g = arr.getJSONObject(i)
            val e = idx.optJSONObject(key(es, g.optString("name")))
            g.put("cached", if (e != null && e.optBoolean("complete")) 1 else 0)
            g.put("pinned", if (e != null && e.optBoolean("pinned")) 1 else 0)
            g.put("last", e?.optDouble("last", 0.0) ?: 0.0)
            g.put("seconds", 0).put("favorite", 0)
        }
        return arr
    }

    // ── aktualizacja EmuStart (menu Start): APK z wydania na GitHubie ──
    @Volatile private var selfUpdate: JSONObject? = null
    @Volatile private var selfStarted = false

    private fun vparts(v: String) = Regex("\\d+").findAll(v).map { it.value.toInt() }.toList()
    private fun newer(a: String, b: String): Boolean {
        val x = vparts(a); val y = vparts(b)
        for (i in 0 until maxOf(x.size, y.size)) {
            val p = x.getOrElse(i) { 0 }; val q = y.getOrElse(i) { 0 }
            if (p != q) return p > q
        }
        return false
    }

    private fun appUpdateCheck(): JSONObject = try {
        val rel = installer.latestRelease("C4rl0s79/EmuStart")
        val ver = rel.optString("tag_name").removePrefix("v")
        val assets = rel.optJSONArray("assets") ?: JSONArray()
        val apk = (0 until assets.length()).map { assets.getJSONObject(it) }
            .firstOrNull { Regex("EmuStart-[\\d.]+-android\\.apk").matches(it.optString("name")) }
        val cur = BuildConfig.VERSION_NAME
        val info = JSONObject().put("ok", true).put("version", ver).put("current", cur)
            .put("newer", newer(ver, cur)).put("size", apk?.optLong("size") ?: 0L)
            .put("available", apk != null).put("url", apk?.optString("browser_download_url") ?: "")
        selfUpdate = info
        info
    } catch (e: Exception) {
        JSONObject().put("ok", false).put("reason", "Nie udało się sprawdzić wydań na GitHubie: ${e.message ?: e.javaClass.simpleName}")
    }

    private fun appUpdateStart(): JSONObject {
        val u = selfUpdate
        if (u == null || !u.optBoolean("newer") || u.optString("url").isEmpty())
            return JSONObject().put("ok", false).put("reason", "Najpierw sprawdź aktualizacje.")
        selfStarted = true
        installer.startSelf(u.optString("url"), "EmuStart ${u.optString("version")}")
        return appUpdateStatus()
    }

    private fun appUpdateStatus(): JSONObject {
        val st = installer.status()
        val phase = when {
            !selfStarted -> "idle"
            installer.running -> "downloading"
            installer.errors.isNotEmpty() -> "error"
            else -> "ready"
        }
        return JSONObject().put("ok", true).put("phase", phase).put("done", st.optLong("done"))
            .put("total", st.optLong("total")).put("version", selfUpdate?.optString("version") ?: "")
            .put("error", installer.errors.joinToString("; "))
    }

    // ── tłumaczenia: wersja gry wybrana w profilu (<profil>/variants.json na serwerze) ──
    private fun variants(): JSONObject = try {
        if (prefs.profile.isEmpty()) JSONObject()
        else server.nasRead("${prefs.profile}/variants.json")?.let { JSONObject(String(it)) } ?: JSONObject()
    } catch (e: Exception) { Log.w(tag, "wersje gier: $e"); JSONObject() }

    private fun variantKey(d: JSONObject) = d.optString("es") + "/" + d.optString("rel").replace('\\', '/')

    /** Wybrana wersja gry z listy `variants` szczegółów (null = oryginał). */
    private fun chosenVariant(d: JSONObject): JSONObject? {
        val list = d.optJSONArray("variants") ?: return null
        if (list.length() == 0) return null
        val want = variants().optString(variantKey(d))
        if (want.isEmpty()) return null
        return (0 until list.length()).map { list.getJSONObject(it) }.firstOrNull { it.optString("rel") == want }
    }

    private fun setVariant(id: Int, vid: Int): JSONObject {
        if (prefs.profile.isEmpty()) return JSONObject().put("ok", false)
            .put("reason", "Wybierz profil gracza — wersja gry należy do profilu.")
        return try {
            val d = server.game(id)
            val list = d.optJSONArray("variants") ?: JSONArray()
            val v = (0 until list.length()).map { list.getJSONObject(it) }.firstOrNull { it.optInt("id") == vid }
            if (vid != 0 && v == null) return JSONObject().put("ok", false).put("reason", "Nie ma takiej wersji gry.")
            val data = variants()
            if (v != null) data.put(variantKey(d), v.optString("rel")) else data.remove(variantKey(d))
            server.nasPut("${prefs.profile}/variants.json", data.toString(1).toByteArray(), System.currentTimeMillis() / 1000.0)
            JSONObject().put("ok", true).put("variant", v?.optInt("id") ?: 0).put("label", v?.optString("label") ?: "Oryginał")
        } catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)) }
    }

    private fun detail(id: Int): JSONObject {
        val orig = server.game(id)
        val chosen = chosenVariant(orig)
        // stan w telefonie, rozmiar i pliki — wybranej wersji (tę się pobiera i uruchamia)
        val d = if (chosen != null) server.game(chosen.optInt("id")).also { v ->
            for (k in listOf("id", "title", "meta", "box", "snap", "logo", "es", "rel", "variants")) if (orig.has(k)) v.put(k, orig.get(k))
        } else orig
        d.put("variant", chosen?.optInt("id") ?: 0).put("variant_label", chosen?.optString("label") ?: "")
        val files = d.optJSONArray("files") ?: JSONArray()
        d.put("filelist", files).put("files", files.length())
        val e = Cache.index(act).optJSONObject(key(d.optString("es"), d.optString("name")))
        d.put("cached", e?.optBoolean("complete") == true).put("pinned", e?.optBoolean("pinned") == true)
        // ile gry jest już w telefonie (także przerwane pobieranie) — do „Usuń z telefonu”
        val base = File(prefs.gamesDir(act), d.optString("es"))
        val dl = Downloader(server, 1)
        var local = 0L
        for (i in 0 until files.length()) {
            val f = files.getJSONObject(i)
            val size = f.optLong("size")
            local += size - dl.missing(File(base, f.getString("path")), size)
        }
        d.put("local", local)
        d.put("copying", false).put("meta_online", true)
        for (k in listOf("box", "snap", "logo")) if (d.has(k)) d.put(k, remote(d.optString(k)))
        return d
    }

    // ── ustawienia ──
    /** Wszystkie rdzenie RetroArcha dla platformy (serwer 0.25.8+; starszy — tylko domyślny). */
    private fun cores(s: JSONObject): List<String> {
        val a = s.optJSONArray("cores") ?: return emptyList()
        return (0 until a.length()).map { a.getString(it) }
    }

    /** Emulator systemu: wybór gracza, a bez niego — ten, którego używa komputer (zalecany przez
     *  serwer: RetroArch z tym samym rdzeniem — stany zgodne), dopiero potem pierwszy dostępny. */
    private fun chosenEmu(s: JSONObject): String {
        val es = s.optString("es")
        val plat = s.optString("plat")
        val opts = Emulators.options(act, plat, s.optString("core"), cores(s))
        var pick = prefs.emulator(es)
        // wybór sprzed 0.37.2: „retroarch” bez rdzenia = pierwszy rdzeń z listy serwera — utrwalamy go
        if (pick.isNotEmpty() && Emulators.baseId(pick) == "retroarch" && Emulators.coreOf(pick).isEmpty()) {
            val c = Emulators.retroCore(plat, s.optString("core"))
            if (c.isNotEmpty()) { pick = "$pick#$c"; prefs.setEmulator(es, pick) }
        }
        if (opts.any { it.first == pick }) return pick
        return recommended(s, opts) ?: opts.firstOrNull()?.first ?: ""
    }

    private fun recommended(s: JSONObject, opts: List<Pair<String, String>>): String? {
        val core = Emulators.retroCore(s.optString("plat"), s.optString("core"))
        return when (s.optString("emu_rec")) {
            "retroarch" -> opts.firstOrNull { Emulators.baseId(it.first) == "retroarch" && Emulators.coreOf(it.first) == core }?.first
            "standalone" -> opts.firstOrNull { Emulators.baseId(it.first) != "retroarch" }?.first
            else -> null
        }
    }

    /** Opis wyboru: emulator z rdzeniem, a gdy rdzeń inny niż na komputerze — ostrzeżenie. */
    private fun emuLabel(s: JSONObject, emu: String): String {
        if (emu.isEmpty()) return ""
        val label = Emulators.label(emu)
        val rec = Emulators.retroCore(s.optString("plat"), s.optString("core"))
        val core = Emulators.coreOf(emu)
        return if (s.optString("emu_rec") == "retroarch" && Emulators.baseId(emu) == "retroarch" && core.isNotEmpty() && core != rec)
            "$label — inny rdzeń niż na komputerze ($rec): stany się nie wczytają" else label
    }

    private fun settings(): JSONObject {
        val sys = JSONArray()
        val arr = systems()
        for (i in 0 until arr.length()) {
            val s = arr.getJSONObject(i)
            val opts = JSONArray()
            for ((oid, label) in Emulators.options(act, s.optString("plat"), s.optString("core"), cores(s))) {
                val c = if (Emulators.baseId(oid) == "retroarch")
                    Emulators.retroCore(s.optString("plat"), Emulators.coreOf(oid).ifEmpty { s.optString("core") }) else ""
                val rec = s.optString("emu_rec") == "retroarch" && c.isNotEmpty() &&
                    c == Emulators.retroCore(s.optString("plat"), s.optString("core"))
                val other = s.optString("emu_rec") == "retroarch" && c.isNotEmpty() && !rec
                opts.put(JSONObject().put("id", oid).put("label", label + (if (c.isNotEmpty() && prefs.coreOk(c)) " ✓" else "") +
                    (if (rec) " · jak na komputerze" else "") +
                    (if (other) " · ⚠ inny rdzeń niż na komputerze — stany z PC się nie wczytają" else "")))
            }
            sys.put(JSONObject().put("es", s.optString("es")).put("display", s.optString("display"))
                .put("options", opts).put("emulator", chosenEmu(s)))
        }
        return JSONObject()
            .put("server_url", prefs.serverUrl).put("server_key", if (prefs.configured) "dev:" + prefs.deviceId else "")
            .put("server_keykind", if (prefs.deviceId.isNotEmpty()) "Android Keystore" else "")
            .put("server_account", prefs.serverAccount).put("server_via", server.lastKind)
            .put("enroll", prefs.enrollPending.takeIf { it.isNotEmpty() }?.let { val o = JSONObject(it)
                JSONObject().put("url", o.optString("url")).put("code", o.optString("code")).put("account", o.optString("account"))
                    .put("mode", o.optString("mode")) } ?: JSONObject.NULL)
            .put("cache_recent", prefs.cacheRecent).put("streams", prefs.streams)
            .put("games_logo", prefs.gamesLogo).put("games_dir", prefs.gamesDir(act).absolutePath)
            .put("profile", prefs.profile).put("pending_saves", saves.pendingCount())
            .put("save_fams", JSONArray(Saves.FAMS.map { f ->
                JSONObject().put("id", f.id).put("label", f.label).put("value", prefs.saveDir(f.id)).put("found", saves.describe(f)) }))
            .put("systems", sys)
    }

    private fun saveSettings(d: JSONObject) {
        val keys = d.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            when {
                k == "cache_recent" -> prefs.cacheRecent = d.getInt(k)
                k == "streams" -> prefs.streams = d.getInt(k)
                k == "games_logo" -> prefs.gamesLogo = d.getBoolean(k)
                k.startsWith("emu:") -> {
                    prefs.setEmulatorChoice(k.removePrefix("emu:"), d.getString(k)); systemsCache = null
                    pool.execute { pushEmuChoices() }       // wybór na koncie — inne telefony tego konta też
                }
                k.startsWith("save_dir:") -> prefs.setSaveDir(k.removePrefix("save_dir:"), d.getString(k))
            }
        }
    }

    /** Kod QR z panelu serwera (Dodaj urządzenie) — skaner kodów Google Play: bez uprawnienia
     *  do aparatu w EmuStart. Bez Google Play: Zaloguj (adres serwera). */
    private fun scanQr(): JSONObject {
        val latch = CountDownLatch(1)
        var out = JSONObject().put("ok", false).put("reason", "Anulowano.")
        act.runOnUiThread {
            try {
                val opts = com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions.Builder()
                    .setBarcodeFormats(com.google.mlkit.vision.barcode.common.Barcode.FORMAT_QR_CODE).build()
                com.google.mlkit.vision.codescanner.GmsBarcodeScanning.getClient(act, opts).startScan()
                    .addOnSuccessListener { b -> out = JSONObject().put("ok", true).put("text", b.rawValue ?: ""); latch.countDown() }
                    .addOnCanceledListener { latch.countDown() }
                    .addOnFailureListener { e ->
                        out = JSONObject().put("ok", false)
                            .put("reason", "Skaner kodów niedostępny (${e.message}) — użyj: Zaloguj (adres serwera).")
                        latch.countDown()
                    }
            } catch (e: Exception) {
                out = JSONObject().put("ok", false).put("reason", "Skaner kodów niedostępny — użyj: Zaloguj (adres serwera).")
                latch.countDown()
            }
        }
        latch.await(5, TimeUnit.MINUTES)
        return out
    }

    // ── logowanie na serwerze (emustart/enroll.py): konto + hasło + klucz urządzenia ──
    private data class Login(val eps: List<Triple<String, String, Boolean>>, val fp: String, val code: String)

    /** Link z kodu QR panelu (adresy, odcisk certyfikatu, kod jednorazowy) albo adres tunelu.
     *  Adres IP bez kodu QR nie ma czym sprawdzić certyfikatu — tylko domena z prawdziwym. */
    private fun parseLogin(text0: String): Pair<Login?, String> {
        val text = text0.trim()
        if (text.lowercase().startsWith("emustart://login")) {
            return try {
                val raw = Uri.parse(text).getQueryParameter("d") ?: ""
                val d = JSONObject(String(android.util.Base64.decode(raw,
                    android.util.Base64.URL_SAFE or android.util.Base64.NO_PADDING or android.util.Base64.NO_WRAP), Charsets.UTF_8))
                val a = d.getJSONArray("e")
                val eps = (0 until a.length()).map { a.getJSONObject(it) }
                    .filter { it.optString("u").startsWith("https://") }
                    .map { Triple(it.getString("u").trimEnd('/'), it.optString("k"), it.optInt("ca", 0) == 1) }
                val fp = d.optString("f").lowercase()
                if (eps.isEmpty() || !Regex("[0-9a-f]{64}").matches(fp)) throw IllegalArgumentException("brak adresów")
                Pair(Login(eps, fp, d.optString("c")), "")
            } catch (e: Exception) { Pair(null, "To nie jest kod logowania EmuStart.") }
        }
        if (text.isBlank()) return Pair(null, "Podaj adres serwera albo zeskanuj kod QR z panelu.")
        if (text.lowercase().startsWith("http://"))
            return Pair(null, "Tylko połączenie szyfrowane — podaj adres https (tunel) albo użyj kodu QR z panelu.")
        val u = (if (text.lowercase().startsWith("https://")) text else "https://$text").trimEnd('/')
        val host = u.removePrefix("https://").substringBefore('/').substringBeforeLast(':')
        if (!host.contains('.') || Regex("""^[\d.]+$""").matches(host) || host.contains(':'))
            return Pair(null, "Adres IP albo nazwę komputera da się sprawdzić tylko kodem QR z panelu serwera " +
                              "(Dodaj urządzenie) — wpisz adres tunelu (np. emustart.emucloud.org).")
        return Pair(Login(listOf(Triple(u, "tunnel", true)), "", ""), "")
    }

    private fun enrollStart(text: String, mode: String, account: String, password: String): JSONObject {
        if (account.isBlank() || mode !in setOf("new", "existing"))
            return JSONObject().put("ok", false).put("reason", "Podaj nazwę konta.")
        // hasło konta (emustart/accounts.py) idzie tylko w prośbie — telefon go nie zapisuje
        if (password.isEmpty()) return JSONObject().put("ok", false).put("reason", "Podaj hasło konta.")
        val (login, why) = parseLogin(text)
        if (login == null) return JSONObject().put("ok", false).put("reason", why)
        val old = prefs.enrollPending.takeIf { it.isNotEmpty() }?.let { JSONObject(it) }
        if (old != null && (old.optString("account") != account || old.optString("mode") != mode || login.code.isNotEmpty())) enrollCancel()
        val keep = old != null && prefs.enrollPending.isNotEmpty()
        val alias = if (keep) old!!.optString("alias") else "emustart-" + java.util.UUID.randomUUID().toString().take(12)
        val (kind, pem) = if (keep) Pair(old!!.optString("kind"), old.optString("pubkey")) else try {
            DeviceKey.create(alias)
        } catch (e: Exception) {
            return JSONObject().put("ok", false).put("reason", "Nie udało się utworzyć klucza urządzenia: ${e.message}")
        }
        val body = JSONObject().put("mode", mode).put("account", account).put("password", password)
            .put("name", saves.device).put("class", "phone").put("version", BuildConfig.VERSION_NAME)
            .put("pubkey", pem).put("hw", server.hwId).put("login_code", login.code)
        var r = JSONObject().put("ok", false).put("reason", "Serwer nie odpowiada.")
        var used = Triple("", "", true)
        for (ep in login.eps) {                       // pierwszy osiągalny adres serwera
            used = ep
            r = try { server.postJson(ep.first, "/v1/enroll/request", body, ca = ep.third, fp = if (ep.third) "" else login.fp) }
                catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)).put("unreachable", true) }
            if (!r.optBoolean("unreachable")) break
        }
        if (!r.optBoolean("ok")) {
            if (!keep) DeviceKey.delete(alias)
            return JSONObject().put("ok", false).put("reason", r.optString("reason", "Serwer odrzucił prośbę."))
        }
        prefs.enrollPending = JSONObject().put("url", used.first).put("ca", used.third).put("fp", login.fp)
            .put("rid", r.getString("rid")).put("code", r.getString("pretty"))
            .put("account", account).put("mode", mode).put("alias", alias).put("kind", kind).put("pubkey", pem).toString()
        Log.i(tag, "prośba o zalogowanie na ${used.first} ($mode, konto $account, klucz: $kind)")
        val out = JSONObject().put("ok", true).put("code", r.getString("pretty")).put("account", account)
            .put("url", used.first).put("key", kind)
        if (r.optString("status") == "approved") out.put("status", enrollPoll().optString("status"))   // kod z panelu
        return out
    }

    private fun enrollPoll(): JSONObject {
        val p = prefs.enrollPending.takeIf { it.isNotEmpty() }?.let { JSONObject(it) }
            ?: return JSONObject().put("ok", false).put("status", "none")
        val url = p.getString("url")
        val ca = p.optBoolean("ca", true)
        val r = try {
            server.postJson(url, "/v1/enroll/poll", JSONObject().put("rid", p.getString("rid")), ca = ca,
                            fp = if (ca) "" else p.optString("fp"))
        } catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)) }
        val st = if (r.optBoolean("ok")) r.optString("status") else "error"
        when (st) {
            "approved" -> {
                val oldKey = prefs.deviceKey
                prefs.serverUrl = url
                prefs.deviceId = r.getString("device")
                prefs.deviceKey = p.getString("alias")
                if (oldKey.isNotEmpty() && oldKey != prefs.deviceKey) DeviceKey.delete(oldKey)
                prefs.serverAccount = r.optString("account", p.optString("account"))
                // konto = profil tego telefonu (zamiast wyboru z listy po zatwierdzeniu)
                prefs.profile = prefs.serverAccount
                if (prefs.phoneOwner.isEmpty()) prefs.phoneOwner = prefs.serverAccount
                prefs.serverEndpoints = ""
                prefs.serverFp = p.optString("fp")
                server.learn(r, trusted = true)       // adresy i odcisk — z połączenia szyfrowanego
                prefs.enrollPending = ""
                server.dropToken(); server.forgetBase()
                systemsCache = null
                learnedAt = 0L
                Log.i(tag, "zalogowano na serwerze $url (konto ${prefs.serverAccount})")
            }
            "denied", "expired" -> enrollCancel()
        }
        return JSONObject().put("ok", true).put("status", st).put("account", p.optString("account"))
            .put("code", p.optString("code")).put("reason", r.optString("reason"))
    }

    private fun enrollCancel() {
        val p = prefs.enrollPending.takeIf { it.isNotEmpty() }?.let { JSONObject(it) } ?: return
        DeviceKey.delete(p.optString("alias"))
        prefs.enrollPending = ""
    }

    /** Wyloguj z serwera: serwer unieważnia ten telefon, klucz urządzenia i adresy znikają. */
    private fun logout(): JSONObject {
        if (prefs.configured) try { server.postAuthed("/v1/auth/logout", JSONObject()) } catch (e: Exception) { Log.w(tag, "wylogowanie: $e") }
        if (prefs.deviceKey.isNotEmpty()) DeviceKey.delete(prefs.deviceKey)
        prefs.forgetServer()
        server.dropToken(); server.forgetBase()
        systemsCache = null
        online = false
        Log.i(tag, "wylogowano z serwera")
        return JSONObject().put("ok", true)
    }

    /** Prośby, które ten telefon może zatwierdzić (pokazywane przy otwarciu aplikacji). */
    private fun enrollPending(): JSONObject {
        if (!prefs.configured) return JSONObject().put("ok", true).put("requests", JSONArray())
        val r = try { server.postAuthed("/v1/enroll/pending", JSONObject()) } catch (e: Exception) { JSONObject() }
        return if (r.optBoolean("ok")) r else JSONObject().put("ok", true).put("requests", JSONArray())
    }

    /** Zrozumiały powód błędu połączenia z serwerem. */
    private fun explain(e: Exception): String {
        val url = prefs.serverUrl.ifBlank { "(serwer)" }
        return when (e) {
            is Server.HttpError -> if (e.code == 401)
                "Serwer $url odpowiada, ale nie przyjmuje tego telefonu — zaloguj się ponownie (Ustawienia → Zaloguj)."
                else "Serwer $url odpowiedział błędem ${e.code}: ${e.message}"
            is java.net.UnknownHostException -> "Nieznany adres serwera: ${e.message ?: url}."
            is javax.net.ssl.SSLException -> "Połączenie szyfrowane z $url nie powiodło się (${e.message}) — zaloguj się ponownie kodem QR z panelu."
            is java.net.SocketTimeoutException, is java.net.ConnectException, is java.net.NoRouteToHostException ->
                "Telefon nie łączy się z serwerem ($url). Sprawdź internet i czy na komputerze z grami działa serwer EmuStart."
            is java.net.MalformedURLException -> "Niepoprawny adres serwera: $url"
            else -> "${e.javaClass.simpleName}: ${e.message ?: ""} ($url)"
        }
    }

    // ── gra ──
    private fun startLaunch(id: Int, takeover: Boolean = false, state: String = ""): JSONObject {
        val busy = launch?.phase.let { it == "preparing" || it == "downloading" }
        if (busy) return JSONObject().put("ok", false).put("reason", "Inna gra jest właśnie pobierana.")
        val g = try { server.game(id) } catch (e: Exception) {
            return JSONObject().put("ok", false).put("reason", explain(e))
        }
        val s = sysByEs(g.optString("es")) ?: return JSONObject().put("ok", false).put("reason", "Nieznany system.")
        val emu = chosenEmu(s)
        if (emu.isEmpty()) {
            val steps = installSteps(s)
            if (steps.length() > 0) return JSONObject().put("ok", false).put("need_install", JSONObject()
                .put("es", s.optString("es")).put("system", s.optString("display")).put("steps", steps))
            return JSONObject().put("ok", false)
                .put("reason", "Brak emulatora dla ${s.optString("display")} — zainstaluj go i wybierz w Ustawieniach.")
        }
        pinBlock(prefs.profile)?.let { return it.put("profile_pin", true) }
        val l = Launch(g, s)
        l.startState = if (state.startsWith("ra:")) state.removePrefix("ra:") else ""
        launch = l
        pool.execute { runLaunch(l, emu, takeover) }
        return JSONObject().put("ok", true)
    }

    private fun runLaunch(l: Launch, emu: String, takeover: Boolean = false) {
        l.emuId = emu
        try {
            val g = l.game
            val es = g.optString("es")
            val base = File(prefs.gamesDir(act), es)
            val files = g.optJSONArray("files") ?: JSONArray()
            val dl = Downloader(server, prefs.streams)
            l.downloader = dl
            var total = 0L
            for (i in 0 until files.length()) {
                val f = files.getJSONObject(i)
                total += dl.missing(File(base, f.getString("path")), f.getLong("size"))
            }
            val prog = Progress(total, files.length())
            l.progress = prog
            if (total > 0) l.phase = "downloading"
            for (i in 0 until files.length()) {
                val f = files.getJSONObject(i)
                prog.file = i + 1
                dl.fetch(g.getInt("id"), f.getString("path"), File(base, f.getString("path")), f.getLong("size"), prog)
            }
            val rels = (0 until files.length()).map { files.getJSONObject(it).getString("path") }
            val main0 = File(base, g.optString("file").ifEmpty { rels[0] })
            // jak na Windows: ZIP z MSU-1 / kilkoma dyskietkami rozpakowany, playlista płyt
            val exts = g.optJSONArray("exts")?.let { a -> (0 until a.length()).map { a.getString(it) }.toSet() } ?: emptySet()
            if (main0.extension.equals("zip", true)) { l.phase = "preparing"; l.message = "Rozpakowuję…" }
            val prep = Prepare.prepare(emu, base, main0, rels, g.optString("name"), g.optBoolean("multidisc"),
                                       g.optString("kind", l.sys.optString("kind")), exts)
            val main = prep.rom
            Cache.complete(act, key(es, g.optString("name")),
                           rels.map { File(base, it).absolutePath } + prep.extra.map { it.absolutePath })
            Cache.enforce(act, prefs.cacheRecent, key(es, g.optString("name")))
            // zapisy profilu: nowsze z serwera do folderu emulatora (wspólne z EmuStart na Windows)
            if (prefs.profile.isNotEmpty() && Saves.famsFor(emu, l.sys.optString("plat")).isNotEmpty()) {
                l.phase = "preparing"; l.message = "Zapisy profilu ${prefs.profile}…"
                val sg = Saves.Game(es, g.optString("name"), main.nameWithoutExtension, main.parentFile ?: base,
                                    Emulators.retroCore(l.sys.optString("plat"),
                                        Emulators.coreOf(emu).ifEmpty { l.sys.optString("core") }),
                                    l.sys.optString("plat"), g.optJSONObject("meta")?.optString("serial") ?: "",
                                    zipStems(main0).joinToString("|"))
                try {
                    val msg = saves.before(prefs.profile, emu, sg, takeover)
                    l.message = msg
                    if (msg.isNotEmpty()) Log.i(tag, msg)
                    // „Wczytaj zapis”: wybrany stan (z dowolnego urządzenia) jako autozapis — RetroArch go wczyta
                    if (l.startState.isNotEmpty() && Emulators.baseId(emu) == "retroarch") {
                        l.message = "Wczytuję wybrany stan…"
                        if (saves.placeAuto(prefs.profile, sg, l.startState) == 0)
                            l.message = "Nie udało się pobrać wybranego stanu — gra wystartuje od najnowszego."
                    }
                } catch (e: Saves.Locked) {
                    l.phase = "error"; l.lockedBy = e.host
                    l.message = "Profil „${prefs.profile}” gra teraz na: ${e.host}. Jeśli tam już nie grasz, " +
                        "przejmij profil — inaczej wybierz inny profil."
                    return
                }
                l.emu = emu; l.saveGame = sg; l.profile = prefs.profile
            }
            if (Emulators.baseId(emu) == "retroarch") {
                // BIOS-y rdzenia z serwera (PS1, Sega CD, PC Engine CD, FDS, Kickstarty…) — jak na Windows
                l.phase = "preparing"; l.message = "BIOS-y…"
                val core = Emulators.retroCore(l.sys.optString("plat"), Emulators.coreOf(emu).ifEmpty { l.sys.optString("core") })
                Bios.forCore(server, core)
                if (l.sys.optString("plat") == "AMIGA") {
                    val warn = Bios.amiga(server, prefs)
                    if (warn.isNotEmpty()) { Log.w(tag, warn); l.message = warn }
                }
            }
            // ustawienia i konto RetroAchievements profilu w RetroArchu (osobno od komputera,
            // wspólne dla telefonów); ArmSX2 trzyma swoje we własnych ustawieniach — bez zmian
            var raCfg: String? = null
            if (Emulators.baseId(emu) == "retroarch") {
                val original = Emulators.raConfig(act, emu)
                raCfg = (if (prefs.profile.isNotEmpty()) emuProfile.retroarchBefore(prefs.profile, original) else null)
                    ?.absolutePath ?: emuProfile.retroarchBase(original)?.absolutePath
            }
            val intent = Emulators.intent(act, emu, main, l.sys.optString("plat"), l.sys.optString("core"), raCfg)
            l.startedAt = System.currentTimeMillis()
            l.phase = "running"
            act.runOnUiThread {
                try { act.startActivity(intent) } catch (e: Exception) {
                    l.phase = "error"; l.message = "Nie udało się uruchomić emulatora: ${e.message}"
                }
            }
        } catch (e: InterruptedException) {
            l.phase = "cancelled"; l.message = "Anulowano. Pobrana część zostanie użyta następnym razem."
        } catch (e: Exception) {
            Log.w(tag, "uruchamianie: $e")
            l.phase = "error"; l.message = e.message ?: e.toString()
        }
    }

    private fun launchStatus(): JSONObject {
        val l = launch ?: return JSONObject().put("phase", "idle")
        val o = JSONObject().put("phase", l.phase).put("message", l.message).put("mode", "remote")
            .put("title", l.game.optString("title")).put("tags", l.game.optString("tags"))
            .put("system", l.sys.optString("display")).put("game_id", l.game.optInt("id"))
            .put("copying", false).put("locked_by", l.lockedBy)
            .put("missing_core", l.missingCore)
        l.progress?.let { o.put("progress", JSONObject(it.snapshot())) }
        return o
    }

    /** Powrót z emulatora: gra skończona. */
    fun onResumed() {
        installer.next()                                  // następny instalator z kolejki
        val l = launch ?: return
        val core = Emulators.retroCore(l.sys.optString("plat"), Emulators.coreOf(l.emuId).ifEmpty { l.sys.optString("core") })
        if (l.phase == "running" && Emulators.baseId(l.emuId) == "retroarch" && !prefs.coreOk(core)
            && System.currentTimeMillis() - l.startedAt < 8000) {
            // RetroArch wrócił od razu, a ten rdzeń jeszcze nigdy nie uruchomił gry — najpewniej
            // nie jest pobrany (EmuStart nie widzi rdzeni RetroArcha, nie może ich też wgrać)
            l.phase = "error"; l.missingCore = core
            l.message = "RetroArch nie uruchomił gry — prawdopodobnie brakuje rdzenia „$core”. " +
                "Pobierz go w RetroArchu: Główne menu → Online Updater → Core Downloader → $core, potem uruchom grę ponownie."
            val sg = l.saveGame
            if (sg != null) pool.execute { try { saves.after(l.profile, l.emu, sg, l.startedAt, l.game.optString("title")) } catch (e: Exception) { } }
            return
        }
        if (l.phase == "running" && System.currentTimeMillis() - l.startedAt > 3000) {
            l.phase = "finished"
            // rdzeń, na którym gra działała dłużej niż chwilę — w ustawieniach oznaczony ✓
            if (System.currentTimeMillis() - l.startedAt > 30_000 && Emulators.baseId(l.emuId) == "retroarch")
                prefs.markCoreOk(Emulators.retroCore(l.sys.optString("plat"),
                    Emulators.coreOf(l.emuId).ifEmpty { l.sys.optString("core") }))
            val sg = l.saveGame
            if (sg != null) pool.execute {
                try { saves.after(l.profile, l.emu, sg, l.startedAt, l.game.optString("title")) }
                catch (e: Exception) { Log.w(tag, "zapisy po grze: $e") }
            }
            if (Emulators.baseId(l.emuId) == "retroarch" && prefs.profile.isNotEmpty()) {
                val prof = prefs.profile
                pool.execute { emuProfile.retroarchAfter(prof) }
            }
            synchronized(gamesCache) { gamesCache.clear() }
            act.js("window.__emuResumed && window.__emuResumed()")
        }
    }

    // ── profile (foldery profili na serwerze, te same co w EmuStart na Windows) ──
    private fun profileId(): Int = profList.indexOfFirst { it == prefs.profile }.let { if (it < 0) 0 else it + 1 }

    private fun loadProfiles() {
        val withPw = server.profiles()
        if (withPw != null) {
            // + konta innych użytkowników zalogowane hasłem na tym telefonie
            val extra = prefs.tokenAccounts().filter { t -> withPw.none { it.first.equals(t, ignoreCase = true) } }
            profList = withPw.map { it.first } + extra
            profPin = withPw.toMap() + extra.associateWith { true }
        } else {
            profList = saves.profiles()              // starszy serwer: bez kont z hasłem
            profPin = emptyMap()
        }
    }

    /** Profile odblokowane PIN-em od uruchomienia aplikacji. */
    private val pinUnlocked = java.util.Collections.synchronizedSet(mutableSetOf<String>())

    /** Konto na serwerze: ok | need_password (inny użytkownik — jego hasło) | set_password
     *  (konto bez hasła); potem PIN tego telefonu: need_pin. */
    private fun pinState(n: String): String {
        if (n.isEmpty()) return "ok"
        val own = prefs.deviceId.isNotEmpty() && n.equals(prefs.serverAccount, ignoreCase = true)
        val pw = profPin.entries.firstOrNull { it.key.equals(n, ignoreCase = true) }?.value
        val acc = when {
            own -> if (pw == false) "set_password" else "ok"
            prefs.token(n).isNotEmpty() -> "ok"
            prefs.serverAccount.isNotEmpty() -> "need_password"     // telefon konta: inne konto — hasło
            pw == null -> "ok"
            pw -> "need_password"
            else -> "set_password"
        }
        if (acc != "ok") return acc
        return if (prefs.pinHash(n).isNotEmpty() && n.lowercase() !in pinUnlocked) "need_pin" else "ok"
    }

    private fun profilesList(): JSONObject {
        try { loadProfiles() } catch (e: Exception) { Log.w(tag, "profile: $e") }
        val arr = JSONArray()
        profList.forEachIndexed { i, n ->
            arr.put(JSONObject().put("id", i + 1).put("name", n).put("nas_name", n).put("pin", pinState(n))
                .put("has_pin", prefs.pinHash(n).isNotEmpty()))
        }
        return JSONObject().put("profiles", arr).put("current", profileId())
    }

    private fun pinBlock(n: String): JSONObject? {
        val st = pinState(n)
        if (st == "ok") return null
        return JSONObject().put("ok", false).put("need", st).put("name", n).put("reason", when (st) {
            "need_pin" -> "Profil „$n” — podaj PIN tego telefonu."
            "need_password" -> "Konto „$n” — podaj hasło konta."
            else -> "Konto „$n” nie ma jeszcze hasła — ustaw je."
        })
    }

    private fun selectProfile(id: Int): JSONObject {
        val n = profList.getOrNull(id - 1) ?: return JSONObject().put("ok", false)
        pinBlock(n)?.let { return it }
        val changed = n != prefs.profile
        prefs.profile = n
        if (prefs.phoneOwner.isEmpty()) prefs.phoneOwner = n
        if (changed) {
            // wygląd innego użytkownika — z jego konta (bez nadpisywania go wyglądem poprzedniego)
            prefs.lookTime = 0L
            try { pullLook() } catch (e: Exception) { Log.w(tag, "wygląd profilu: $e") }
        }
        return JSONObject().put("ok", true).put("look", prefs.look)
    }

    private fun opResult(n: String, r: JSONObject): JSONObject {
        if (r.optBoolean("missing")) return JSONObject().put("ok", false).put("missing", true).put("name", n)
            .put("reason", "Nie ma konta „$n”.")
        if (!r.optBoolean("ok")) return JSONObject().put("ok", false)
            .put("reason", r.optString("error", "Nie udało się.")).put("exists", if (r.optBoolean("exists")) r.optString("name", n) else "")
        if (r.optBoolean("needs_password")) return JSONObject().put("ok", false).put("need", "set_password")
            .put("reason", "Konto „${r.optString("name", n)}” nie ma jeszcze hasła — ustawia je jego urządzenie albo administrator.")
        val name = r.optString("name", n)
        val own = prefs.deviceId.isNotEmpty() && name.equals(prefs.serverAccount, ignoreCase = true)
        if (!own) prefs.setToken(name, r.optString("token"))      // swoje konto — wystarczy klucz urządzenia
        pinUnlocked.add(name.lowercase())
        try { loadProfiles() } catch (e: Exception) { }
        return JSONObject().put("ok", true).put("profile", JSONObject().put("id", profList.indexOf(name) + 1).put("name", name))
    }

    private fun createProfile(name: String, password: String): JSONObject = try {
        if (profPin.isEmpty() && server.profiles() == null) {
            // starszy serwer: profil bez hasła, jak dotąd
            val n = saves.createProfile(name)
            profList = saves.profiles()
            JSONObject().put("ok", true).put("profile", JSONObject().put("id", profList.indexOf(n) + 1).put("name", n))
        } else opResult(name, server.profileOp("create", JSONObject().put("name", name).put("password", password)))
    } catch (e: Exception) {
        JSONObject().put("ok", false).put("reason", if (e is IllegalArgumentException) e.message else explain(e))
    }

    /** Konto innego użytkownika na tym telefonie — z jego hasłem. */
    private fun joinProfile(name: String, password: String): JSONObject = try {
        opResult(name, server.profileOp("login", JSONObject().put("name", name).put("password", password)))
    } catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)) }

    private fun loginProfile(id: Int, password: String): JSONObject {
        val n = profList.getOrNull(id - 1) ?: return JSONObject().put("ok", false).put("reason", "Nie ma takiego profilu.")
        return joinProfile(n, password)
    }

    private fun setPassword(id: Int, password: String, old: String): JSONObject {
        val n = profList.getOrNull(id - 1) ?: return JSONObject().put("ok", false).put("reason", "Nie ma takiego profilu.")
        return try {
            val body = JSONObject().put("name", n).put("password", password).put("old", old)
            var r = server.profileOp("password", body)
            if (r.optString("error").contains("Nie ma konta")) {
                saves.createProfile(n)               // konto jeszcze bez folderu
                r = server.profileOp("password", body)
            }
            opResult(n, r)
        } catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)) }
    }

    // ── PIN tylko na tym telefonie (PBKDF2, sól; serwer go nie zna) ──
    private val pinFails = java.util.concurrent.ConcurrentHashMap<String, Pair<Int, Long>>()

    private fun pinDigest(pin: String, salt: ByteArray, iter: Int): ByteArray {
        val spec = javax.crypto.spec.PBEKeySpec(pin.toCharArray(), salt, iter, 256)
        return javax.crypto.SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
    }

    private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
    private fun unhex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    private fun pinCheck(n: String, pin: String): Pair<Boolean, String> {
        val h = prefs.pinHash(n)
        if (h.isEmpty()) { pinUnlocked.add(n.lowercase()); return Pair(true, "") }
        val (fails, last) = pinFails[n.lowercase()] ?: Pair(0, 0L)
        if (fails >= 3) {
            val wait = minOf(300_000L, 1000L shl minOf(fails - 2, 9)) - (System.currentTimeMillis() - last)
            if (wait > 0) return Pair(false, "Za dużo złych PIN-ów — spróbuj za ${wait / 1000 + 1} s.")
        }
        val parts = h.split('$')
        val ok = parts.size == 4 && parts[0] == "pbkdf2" &&
            java.security.MessageDigest.isEqual(pinDigest(pin, unhex(parts[2]), parts[1].toInt()), unhex(parts[3]))
        if (!ok) { pinFails[n.lowercase()] = Pair(fails + 1, System.currentTimeMillis()); return Pair(false, "Zły PIN.") }
        pinFails.remove(n.lowercase())
        pinUnlocked.add(n.lowercase())
        return Pair(true, "")
    }

    private fun unlockPin(id: Int, pin: String): JSONObject {
        val n = profList.getOrNull(id - 1) ?: return JSONObject().put("ok", false).put("reason", "Nie ma takiego profilu.")
        val (ok, why) = pinCheck(n, pin)
        return JSONObject().put("ok", ok).put("reason", why)
    }

    private fun setLocalPin(id: Int, pin: String, old: String): JSONObject {
        val n = profList.getOrNull(id - 1) ?: return JSONObject().put("ok", false).put("reason", "Nie ma takiego profilu.")
        if (!Regex("""\d{4,8}""").matches(pin)) return JSONObject().put("ok", false).put("reason", "PIN to 4–8 cyfr.")
        if (prefs.pinHash(n).isNotEmpty()) {
            val (ok, why) = pinCheck(n, old)
            if (!ok) return JSONObject().put("ok", false).put("reason", if (why == "Zły PIN.") "Zły dotychczasowy PIN." else why)
        }
        val salt = ByteArray(16).also { java.security.SecureRandom().nextBytes(it) }
        val iter = 120_000
        prefs.setPinHash(n, "pbkdf2$" + iter + "$" + hex(salt) + "$" + hex(pinDigest(pin, salt, iter)))
        pinUnlocked.add(n.lowercase())
        return JSONObject().put("ok", true)
    }

    private fun clearLocalPin(id: Int, pin: String): JSONObject {
        val n = profList.getOrNull(id - 1) ?: return JSONObject().put("ok", false).put("reason", "Nie ma takiego profilu.")
        val (ok, why) = pinCheck(n, pin)
        if (ok) prefs.setPinHash(n, "")
        return JSONObject().put("ok", ok).put("reason", why)
    }

    private fun drainConflicts(): JSONArray {
        val out = JSONArray()
        synchronized(saves.conflicts) { saves.conflicts.forEach { out.put(it) }; saves.conflicts.clear() }
        return out
    }

    private fun togglePin(id: Int): JSONObject {
        val g = server.game(id)
        val pinned = Cache.togglePin(act, key(g.optString("es"), g.optString("name")))
        synchronized(gamesCache) { gamesCache.clear() }
        return JSONObject().put("ok", true).put("pinned", pinned)
    }

    // ── instalacja emulatorów (APK / Google Play) ──
    /** Emulator do zainstalowania dla systemu: najpierw te z bezpośrednim APK, potem ze sklepu. */
    private fun installPick(s: JSONObject): String? {
        val c = Emulators.candidates(s.optString("plat"), s.optString("core")).filter { it in Installer.SOURCES }
        return c.sortedBy { if (Installer.SOURCES[it] is Installer.Src.Play) 1 else 0 }.firstOrNull()
    }

    private fun installSteps(s: JSONObject): JSONArray {
        val id = installPick(s) ?: return JSONArray()
        val core = Emulators.retroCore(s.optString("plat"), s.optString("core"))
        val label = Emulators.labelOf(id) + (if (id == "retroarch" && core.isNotEmpty()) " (potem rdzeń $core w RetroArchu)" else "")
        return JSONArray().put(JSONObject().put("label", label).put("version", Installer.describe(id)).put("id", id))
    }

    private fun installMissing(): JSONArray {
        val out = JSONArray()
        val list = try { systems() } catch (e: Exception) { JSONArray() }
        for (i in 0 until list.length()) {
            val s = list.getJSONObject(i)
            if (chosenEmu(s).isNotEmpty()) continue
            out.put(JSONObject().put("es", s.optString("es")).put("system", s.optString("display")).put("steps", installSteps(s)))
        }
        return out
    }

    private fun installStart(es: JSONArray): JSONObject {
        val ids = LinkedHashMap<String, String>()
        for (i in 0 until es.length()) {
            val s = sysByEs(es.getString(i)) ?: continue
            installPick(s)?.let { ids[it] = Emulators.labelOf(it) }
        }
        if (ids.isEmpty()) return JSONObject().put("ok", false).put("reason", "Nie ma czego instalować.")
        installer.start(ids.keys.toList(), ids)
        return JSONObject().put("ok", true)
    }

    /** Menedżer zapisów: stany tej gry z komputera i telefonu, wspólne zapisy w grze. */
    /** „Wczytaj zapis” na telefonie: stany RetroArcha tej gry ze wszystkich urządzeń konta
     *  (wspólny folder na serwerze, rdzeń wybrany na tym telefonie), najnowsze pierwsze, z miniaturami. */
    private fun raStates(id: Int): JSONObject {
        if (prefs.profile.isEmpty()) return JSONObject().put("ok", false)
            .put("reason", "Wybierz profil gracza — stany należą do profilu.")
        return try {
            val g = server.game(id)
            val s = sysByEs(g.optString("es")) ?: return JSONObject().put("ok", false).put("reason", "Nieznany system.")
            val emu = chosenEmu(s)
            if (Emulators.baseId(emu) != "retroarch")
                return JSONObject().put("ok", true).put("states", JSONArray())
                    .put("note", "Wspólne stany ma tylko RetroArch — ten system gra w innym emulatorze.")
            val core = Emulators.retroCore(s.optString("plat"), Emulators.coreOf(emu).ifEmpty { s.optString("core") })
            val coreDir = Saves.normCore(Saves.coreDirOf(core))
            val file = g.optString("file")
            val stems = listOf(File(file).nameWithoutExtension, g.optString("name")).filter { it.isNotEmpty() }
            val cat = server.saveCatalog(mapOf("profile" to prefs.profile, "es" to g.optString("es"),
                                               "name" to g.optString("name"), "stems" to stems.joinToString("|")))
            val all = cat.optJSONArray("states") ?: JSONArray()
            val out = JSONArray()
            var thumbs = 0
            val list = (0 until all.length()).map { all.getJSONObject(it) }
                .filter { it.optString("kind") == "ra" && it.optString("platform") == "shared"
                          && Saves.normCore(it.optString("core")) == coreDir }
                .sortedWith(compareBy<JSONObject>({ it.optString("slot") != "auto" }, { -it.optDouble("mtime") }))
            for (st in list) {
                val auto = st.optString("slot") == "auto"
                val who = st.optString("device").let { d ->
                    if (d.isEmpty()) "" else (if (st.optString("device_platform") == "telefon") "telefon " else "") + d }
                val kind = if (st.optString("state_kind") == "auto") "autozapis przy wyjściu" else ""
                val o = JSONObject().put("path", "ra:" + st.optString("rel")).put("name", st.optString("name"))
                    .put("time", st.optDouble("mtime"))
                    .put("label", if (auto) "Kontynuuj — najnowszy stan" else "Stan #${st.optString("slot").toIntOrNull() ?: 0}")
                    .put("continue", auto)
                    .put("detail", listOf(who, kind).filter { it.isNotEmpty() }.joinToString(" · "))
                val shot = st.optString("shot")
                if (shot.isNotEmpty() && thumbs < 24) {
                    try {
                        server.nasRead("${prefs.profile}/$shot")?.takeIf { it.size < 600_000 }?.let {
                            o.put("thumb", "data:image/png;base64," + android.util.Base64.encodeToString(it, android.util.Base64.NO_WRAP))
                            thumbs++
                        }
                    } catch (e: Exception) { Log.w(tag, "miniatura stanu: $e") }
                }
                out.put(o)
            }
            JSONObject().put("ok", true).put("states", out)
        } catch (e: Exception) {
            JSONObject().put("ok", false).put("reason", explain(e))
        }
    }

    private fun saveManager(id: Int): JSONObject {
        if (prefs.profile.isEmpty()) return JSONObject().put("ok", false)
            .put("reason", "Wybierz profil gracza — zapisy i stany należą do profilu.")
        return try {
            val g = server.game(id)
            val file = g.optString("file")
            val stems = listOf(File(file).nameWithoutExtension, g.optString("name")).filter { it.isNotEmpty() }
            server.saveCatalog(mapOf("profile" to prefs.profile, "es" to g.optString("es"),
                                     "name" to g.optString("name"), "stems" to stems.joinToString("|")))
                .put("ok", true).put("here", "android")
        } catch (e: Server.HttpError) {
            JSONObject().put("ok", false).put("reason", if (e.code == 404)
                "Serwer nie ma menedżera zapisów — zaktualizuj EmuStart na komputerze z grami." else explain(e))
        } catch (e: Exception) {
            JSONObject().put("ok", false).put("reason", explain(e))
        }
    }

    private fun saveCopy(id: Int, rel: String, platform: String, emu: String): JSONObject = try {
        val g = server.game(id)
        server.saveCopy(mapOf("profile" to prefs.profile, "src" to rel, "platform" to platform, "emu" to emu,
                              "es" to g.optString("es"), "name" to g.optString("name"), "device" to android.os.Build.MODEL))
    } catch (e: Exception) {
        JSONObject().put("ok", false).put("reason", explain(e))
    }

    /** Emulator gry bez gry — ustawienia na tym telefonie (osobne od Windows). */
    private fun configureEmulator(id: Int): JSONObject {
        val g = try { server.game(id) } catch (e: Exception) {
            return JSONObject().put("ok", false).put("reason", explain(e))
        }
        val s = sysByEs(g.optString("es")) ?: return JSONObject().put("ok", false).put("reason", "Nieznany system.")
        val emu = chosenEmu(s)
        if (emu.isEmpty()) return JSONObject().put("ok", false)
            .put("reason", "Brak emulatora dla ${s.optString("display")} — zainstaluj go i wybierz w Ustawieniach.")
        return try {
            // RetroArch z plikiem profilu — zmienione ustawienia należą do gracza (i jego telefonów)
            val cfg = if (Emulators.baseId(emu) == "retroarch" && prefs.profile.isNotEmpty())
                emuProfile.retroarchBefore(prefs.profile, Emulators.raConfig(act, emu))?.absolutePath else null
            val intent = Emulators.settingsIntent(act, emu, s.optString("plat"), s.optString("core"), cfg)
            act.runOnUiThread { act.startActivity(intent) }
            JSONObject().put("ok", true).put("android", true).put("label", Emulators.label(emu))
        } catch (e: Exception) {
            JSONObject().put("ok", false).put("reason", "Nie udało się otworzyć emulatora: ${e.message}")
        }
    }

    /** Pliki gry w ZIP-ie (bez rozszerzeń): RetroArch nazywa od nich stany i zapisy. */
    private fun zipStems(f: File): List<String> = if (!f.extension.equals("zip", true)) emptyList() else try {
        java.util.zip.ZipFile(f).use { z ->
            z.entries().asSequence().filter { !it.isDirectory }.map { File(it.name).nameWithoutExtension }.toList()
        }
    } catch (e: Exception) { emptyList() }

    /** Usuwa grę z telefonu (pliki, niedokończone pobieranie, rozpakowany ZIP, przypięcie). */
    private fun removeLocal(id: Int): JSONObject {
        val l = launch
        if (l != null && l.game.optInt("id") == id && l.phase in setOf("preparing", "downloading"))
            return JSONObject().put("ok", false).put("reason", "Ta gra właśnie się pobiera — anuluj pobieranie.")
        val g = try { server.game(id) } catch (e: Exception) {
            return JSONObject().put("ok", false).put("reason", explain(e))
        }
        val es = g.optString("es")
        val base = File(prefs.gamesDir(act), es)
        val files = g.optJSONArray("files") ?: JSONArray()
        val paths = (0 until files.length()).map { File(base, files.getJSONObject(it).getString("path")) }
        val freed = Cache.remove(act, key(es, g.optString("name")), paths)
        synchronized(gamesCache) { gamesCache.clear() }
        return JSONObject().put("ok", true).put("freed", freed)
    }

    // ── grafiki z serwera (przez /remote/ — ten sam „origin” co interfejs) ──
    /** Adres grafiki z serwera → adres w WebView (pobierany przez media()). Idempotentne:
     *  ponowne wywołanie na już przerobionym adresie nic nie psuje (0.44.4: „/remote/remote/…”
     *  — brak grafik w małych systemach, np. Sega CD, MSU-1). */
    private fun remote(url: String): String =
        if (url.isBlank() || url.startsWith("/remote/")) url else "/remote$url"

    fun media(uri: Uri): WebResourceResponse? {
        val rel = uri.encodedPath!!.removePrefix("/remote") + (uri.encodedQuery?.let { "?$it" } ?: "")
        val dir = File(act.cacheDir, "grafiki").apply { mkdirs() }
        val f = File(dir, sha1(rel))
        try {
            if (!f.isFile) {
                val (_, inp) = server.media(rel)
                val tmp = File(dir, f.name + ".tmp")
                inp.use { i -> tmp.outputStream().use { o -> i.copyTo(o) } }
                tmp.renameTo(f)
            }
            return WebResourceResponse(mime(rel), null, f.inputStream())
        } catch (e: Exception) {
            return WebResourceResponse("text/plain", "utf-8", 404, "Not Found", null, ByteArrayInputStream(ByteArray(0)))
        }
    }

    companion object {
        fun key(es: String, name: String) = "$es/$name"
        fun safe(s: String) = s.replace(Regex("[^A-Za-z0-9._-]"), "_")
        fun sha1(s: String): String = MessageDigest.getInstance("SHA-1").digest(s.toByteArray())
            .joinToString("") { "%02x".format(it) }
        fun mime(p: String): String {
            val path = p.substringBefore('?').lowercase()
            return when {
                path.endsWith(".png") -> "image/png"
                path.endsWith(".jpg") || path.endsWith(".jpeg") -> "image/jpeg"
                path.endsWith(".webp") -> "image/webp"
                path.endsWith(".svg") -> "image/svg+xml"
                path.endsWith(".gif") -> "image/gif"
                path.endsWith(".css") -> "text/css"
                path.endsWith(".js") -> "application/javascript"
                path.endsWith(".html") -> "text/html"
                path.endsWith(".json") -> "application/json"
                path.endsWith(".ico") -> "image/x-icon"
                else -> "application/octet-stream"
            }
        }
    }
}
