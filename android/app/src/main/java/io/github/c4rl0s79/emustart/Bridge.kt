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
            try { upgradeLegacyKey() } catch (e: Exception) { Log.w(tag, "klucz urządzenia: $e") }
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
            act.runOnUiThread { act.js("window.__emuResolve($id, ${JSONObject.quote(json)})") }
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
        "server_test" -> serverTest()
        "server_scan" -> scanQr()
        "server_pair" -> pairServer(a.optString(0, ""), a.optString(1, ""))
        "server_approve" -> approveDevice(a.optString(0, ""), a.opt(1) as? Boolean)
        "server_enroll" -> enrollStart(a.optString(0, ""), a.optString(1, ""), a.optString(2, ""))
        "server_enroll_poll" -> enrollPoll()
        "server_enroll_cancel" -> { enrollCancel(); JSONObject().put("ok", true) }
        "enroll_pending" -> enrollPending()
        "enroll_decide" -> server.postAuthed("/v1/enroll/decide", JSONObject().put("id", a.optString(0, ""))
            .put("allow", a.optBoolean(1)).put("games", a.optBoolean(2)))
        "rescan" -> { systemsCache = null; synchronized(gamesCache) { gamesCache.clear() }; JSONObject().put("ok", true) }
        "launch" -> startLaunch(a.getInt(0), a.optBoolean(2, false))
        "launch_status" -> launchStatus()
        "launch_cancel" -> { launch?.downloader?.cancel?.set(true); null }
        "launch_dismiss" -> { val l = launch; if (l != null && l.phase in setOf("finished", "error", "cancelled")) launch = null; null }
        "launch_play_now" -> null
        "toggle_pin" -> togglePin(a.getInt(0))
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
        "look_save" -> { prefs.look = a.getJSONObject(0); JSONObject().put("ok", true) }
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
        "profile_unlock" -> unlockProfile(a.getInt(0), a.getString(1))
        "profile_set_pin" -> setPin(a.getInt(0), a.getString(1), a.optString(2, ""))
        "profile_logout" -> { profList.getOrNull(a.getInt(0) - 1)?.let { prefs.setToken(it, "") }; JSONObject().put("ok", true) }
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
                .put("key_rejected", keyRejected) else JSONObject.NULL)
            .put("py_pad", false).put("pad_backend", "android")
    }

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
     *  przy parowaniu i po wyłączeniu Tailscale nie miał dokąd się połączyć. */
    private fun learnAddresses() {
        val now = System.currentTimeMillis()
        if (now - learnedAt < 3_600_000) return
        learnedAt = now
        pool.execute {
            try { server.info() } catch (e: Exception) { learnedAt = 0L; Log.w(tag, "adresy serwera: $e") }
            pullEmuChoices()
        }
    }

    private fun systems(): JSONArray {
        systemsCache?.let { return it }
        if (!prefs.configured) return JSONArray()
        val arr = try {
            server.systems().also { online = true; keyRejected = false; cacheFile("systemy").writeText(it.toString()); learnAddresses() }
        } catch (e: Exception) {
            online = false
            keyRejected = e is Server.HttpError && e.code == 401      // klucz unieważniony / nieznany
            Log.w(tag, "serwer: $e")
            val f = cacheFile("systemy")
            if (f.isFile) JSONArray(f.readText()) else JSONArray()
        }
        for (i in 0 until arr.length()) {
            val s = arr.getJSONObject(i)
            s.put("logo", remote(s.optString("logo")))
            s.put("emulator", emuLabel(s, chosenEmu(s)))
        }
        systemsCache = arr
        return arr
    }

    private fun sysByEs(es: String): JSONObject? {
        val arr = systems()
        for (i in 0 until arr.length()) if (arr.getJSONObject(i).optString("es") == es) return arr.getJSONObject(i)
        return null
    }

    private fun games(es: String): JSONArray {
        synchronized(gamesCache) { gamesCache[es]?.let { return decorate(es, it) } }
        val arr = try {
            server.games(es).also { online = true; cacheFile("gry-" + safe(es)).writeText(it.toString()) }
        } catch (e: Exception) {
            online = false
            val f = cacheFile("gry-" + safe(es))
            if (f.isFile) JSONArray(f.readText()) else JSONArray()
        }
        for (i in 0 until arr.length()) {
            val g = arr.getJSONObject(i)
            for (k in listOf("box", "snap", "logo")) g.put(k, remote(g.optString(k)))
        }
        synchronized(gamesCache) { gamesCache[es] = arr }
        return decorate(es, arr)
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
            .put("server_url", prefs.serverUrl).put("server_key", prefs.serverKey.ifEmpty { if (prefs.deviceId.isNotEmpty()) "dev:" + prefs.deviceId else "" })
            .put("server_keykind", if (prefs.deviceId.isNotEmpty()) "Android Keystore" else "")
            .put("server_account", prefs.serverAccount)
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
                k == "server_url" -> {
                    var u = d.getString(k).trim()
                    if (u.isNotEmpty()) u = normUrl(u)
                    // inny serwer: adresy i certyfikat poprzedniego nie obowiązują (nauczy się od nowa)
                    if (u.trimEnd('/') != prefs.serverUrl) { prefs.serverEndpoints = ""; prefs.serverFp = ""; server.forgetBase() }
                    prefs.serverUrl = u; systemsCache = null
                }
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

    private fun serverTest(): JSONObject = try {
        val info = server.info()
        systemsCache = null
        online = true
        JSONObject().put("ok", true).put("name", info.optString("name")).put("version", info.optString("version"))
            .put("systems", systems().length())
    } catch (e: Exception) {
        online = false
        JSONObject().put("ok", false).put("reason", explain(e))
    }

    /** Kod QR z panelu serwera (Dodaj urządzenie) — skaner kodów Google Play: bez uprawnienia
     *  do aparatu w EmuStart. Bez Google Play: ręczne wpisanie kodu (Połącz kodem). */
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
                            .put("reason", "Skaner kodów niedostępny (${e.message}) — wpisz kod ręcznie: Połącz kodem.")
                        latch.countDown()
                    }
            } catch (e: Exception) {
                out = JSONObject().put("ok", false).put("reason", "Skaner kodów niedostępny — wpisz kod ręcznie: Połącz kodem.")
                latch.countDown()
            }
        }
        latch.await(5, TimeUnit.MINUTES)
        return out
    }

    /** Parowanie: kod z panelu serwera albo link z kodu QR (emustart://pair?u=…&c=…) →
     *  własny klucz tego telefonu (zamiast przepisywania wspólnego klucza serwera). */
    // ── nowa instalacja przez internet (emustart/enroll.py): klucz urządzenia, prośba, zatwierdzenie ──
    private fun enrollStart(url0: String, mode: String, account: String): JSONObject {
        if (url0.isBlank() || account.isBlank() || mode !in setOf("new", "existing"))
            return JSONObject().put("ok", false).put("reason", "Podaj adres serwera i nazwę konta.")
        val url = normUrl(url0)
        val old = prefs.enrollPending.takeIf { it.isNotEmpty() }?.let { JSONObject(it) }
        if (old != null && (old.optString("url") != url || old.optString("account") != account || old.optString("mode") != mode)) enrollCancel()
        val keep = old != null && prefs.enrollPending.isNotEmpty()
        val alias = if (keep) old!!.optString("alias") else "emustart-" + java.util.UUID.randomUUID().toString().take(12)
        val (kind, pem) = if (keep) Pair(old!!.optString("kind"), old.optString("pubkey")) else try {
            DeviceKey.create(alias)
        } catch (e: Exception) {
            return JSONObject().put("ok", false).put("reason", "Nie udało się utworzyć klucza urządzenia: ${e.message}")
        }
        val r = try {
            server.postJson(url, "/v1/enroll/request", JSONObject().put("mode", mode).put("account", account)
                .put("name", saves.device).put("class", "phone").put("version", BuildConfig.VERSION_NAME).put("pubkey", pem)
                .put("hw", server.hwId),
                ca = !url.startsWith("http://"))
        } catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)) }
        if (!r.optBoolean("ok")) {
            if (!keep) DeviceKey.delete(alias)
            return JSONObject().put("ok", false).put("reason", r.optString("reason", "Serwer odrzucił prośbę."))
        }
        prefs.enrollPending = JSONObject().put("url", url).put("rid", r.getString("rid")).put("code", r.getString("pretty"))
            .put("account", account).put("mode", mode).put("alias", alias).put("kind", kind).put("pubkey", pem).toString()
        Log.i(tag, "prośba o dołączenie do $url ($mode, konto $account, klucz: $kind)")
        return JSONObject().put("ok", true).put("code", r.getString("pretty")).put("account", account).put("url", url).put("key", kind)
    }

    private fun enrollPoll(): JSONObject {
        val p = prefs.enrollPending.takeIf { it.isNotEmpty() }?.let { JSONObject(it) }
            ?: return JSONObject().put("ok", false).put("status", "none")
        val url = p.getString("url")
        val r = try {
            server.postJson(url, "/v1/enroll/poll", JSONObject().put("rid", p.getString("rid")), ca = !url.startsWith("http://"))
        } catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)) }
        val st = if (r.optBoolean("ok")) r.optString("status") else "error"
        when (st) {
            "approved" -> {
                prefs.serverUrl = url
                prefs.serverKey = ""
                prefs.deviceId = r.getString("device")
                prefs.deviceKey = p.getString("alias")
                prefs.serverAccount = r.optString("account", p.optString("account"))
                // konto = profil tego telefonu (zamiast wyboru z listy po zatwierdzeniu)
                prefs.profile = prefs.serverAccount
                if (prefs.phoneOwner.isEmpty()) prefs.phoneOwner = prefs.serverAccount
                prefs.serverEndpoints = ""
                prefs.enrollPending = ""
                server.dropToken(); server.forgetBase()
                systemsCache = null
                learnedAt = 0L
                Log.i(tag, "dołączono do serwera $url (konto ${prefs.serverAccount})")
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

    /** Prośby, które ten telefon może zatwierdzić (pokazywane przy otwarciu aplikacji). */
    private fun enrollPending(): JSONObject {
        if (!prefs.configured) return JSONObject().put("ok", true).put("requests", JSONArray())
        val r = try { server.postAuthed("/v1/enroll/pending", JSONObject()) } catch (e: Exception) { JSONObject() }
        return if (r.optBoolean("ok")) r else JSONObject().put("ok", true).put("requests", JSONArray())
    }

    /** Adres serwera wpisany przez gracza: nazwa domeny (np. emustart.emucloud.org — tunel)
     *  → https (port 443), adres IP / nazwa komputera → http z portem 8740. */
    private fun normUrl(s: String): String {
        var u = s.trim().trimEnd('/')
        if (!u.startsWith("http://") && !u.startsWith("https://")) {
            val host = u.substringBefore('/').substringBefore(':')
            val domain = host.contains('.') && !Regex("""^\d+(\.\d+){3}$""").matches(host)
            u = (if (domain) "https://" else "http://") + u
        }
        if (u.startsWith("http://") && !Regex(""":\d+$""").containsMatchIn(u.removePrefix("http://"))) u += ":8740"
        return u
    }

    /** Zatwierdzenie nowego urządzenia kodem z jego ekranu albo linkiem z jego kodu QR
     *  (emustart://approve?u=…&c=…). allow == null — tylko podgląd prośby (nazwa, rodzaj, adres). */
    private fun approveDevice(text: String, allow: Boolean?): JSONObject {
        if (!prefs.configured)
            return JSONObject().put("ok", false).put("reason", "Ten telefon nie jest połączony z serwerem.")
        var code = text.trim()
        if (code.lowercase().startsWith("emustart://approve")) code = Uri.parse(code).getQueryParameter("c") ?: ""
        else if (code.lowercase().startsWith("emustart://pair"))
            return JSONObject().put("ok", false).put("reason", "To kod do łączenia telefonu z serwerem — użyj „Połącz z serwerem (kod QR)”.")
        return try { server.approve(code, allow) } catch (e: Exception) {
            JSONObject().put("ok", false).put("reason", explain(e))
        }
    }

    private fun pairServer(text: String, url0: String): JSONObject {
        var url = url0.ifBlank { prefs.serverUrl }
        var code = text.trim()
        if (code.lowercase().startsWith("emustart://pair")) {
            val u = Uri.parse(code)
            url = u.getQueryParameter("u") ?: url
            code = u.getQueryParameter("c") ?: ""
        }
        if (url.isBlank()) return JSONObject().put("ok", false).put("reason", "Podaj adres serwera albo zeskanuj kod QR.")
        url = normUrl(url)
        // klucz urządzenia w Android Keystore — serwer nie wydaje już kluczy-haseł (0.39)
        val alias = "emustart-" + java.util.UUID.randomUUID().toString().take(12)
        val pem = try { DeviceKey.create(alias).second } catch (e: Exception) {
            return JSONObject().put("ok", false).put("reason", "Nie udało się utworzyć klucza urządzenia: ${e.message}")
        }
        return try {
            val c = java.net.URL(url.trimEnd('/') + "/v1/pair").openConnection() as java.net.HttpURLConnection
            c.connectTimeout = 8000; c.readTimeout = 15000
            c.requestMethod = "POST"; c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            val body = JSONObject().put("code", code).put("name", saves.device).put("class", "phone")
                .put("version", BuildConfig.VERSION_NAME).put("hw", server.hwId).put("pubkey", pem).toString().toByteArray()
            c.outputStream.use { it.write(body) }
            val code2 = c.responseCode
            val txt = (if (code2 in 200..299) c.inputStream else c.errorStream)?.bufferedReader()?.readText() ?: "{}"
            c.disconnect()
            val d = JSONObject(txt)
            if (!d.optBoolean("ok")) {
                DeviceKey.delete(alias)
                return JSONObject().put("ok", false).put("reason", d.optString("reason", "Serwer odrzucił kod."))
            }
            prefs.serverUrl = url
            useDeviceKey(d.getString("id"), alias)
            server.learn(d)                        // adresy serwera i odcisk certyfikatu (HTTPS)
            systemsCache = null
            online = true
            JSONObject().put("ok", true).put("server", d.optString("server")).put("url", url)
        } catch (e: Exception) {
            DeviceKey.delete(alias)
            JSONObject().put("ok", false).put("reason", explain(e))
        }
    }

    /** Ten telefon łączy się kluczem urządzenia `did` (stary klucz-hasło i poprzedni klucz — usunięte). */
    private fun useDeviceKey(did: String, alias: String) {
        val old = prefs.deviceKey
        prefs.deviceId = did
        prefs.deviceKey = alias
        prefs.serverKey = ""
        if (old.isNotEmpty() && old != alias) DeviceKey.delete(old)
        server.dropToken(); server.forgetBase()
        systemsCache = null
    }

    /** Stary klucz-hasło → klucz urządzenia (ten sam wpis na serwerze) — przy starcie aplikacji. */
    private fun upgradeLegacyKey() {
        if (prefs.serverKey.isEmpty() || prefs.deviceId.isNotEmpty() || prefs.serverUrl.isEmpty()) return
        val alias = "emustart-" + java.util.UUID.randomUUID().toString().take(12)
        try {
            val pem = DeviceKey.create(alias).second
            val r = server.postAuthed("/v1/auth/upgrade", JSONObject().put("pubkey", pem).put("hw", server.hwId))
            if (r.optBoolean("ok")) {
                useDeviceKey(r.getString("id"), alias)
                Log.i(tag, "telefon przeszedł na klucz urządzenia")
            } else {
                DeviceKey.delete(alias)
                Log.w(tag, "przejście na klucz urządzenia: ${r.optString("reason")}")
            }
        } catch (e: Exception) {
            DeviceKey.delete(alias)
            Log.w(tag, "przejście na klucz urządzenia: $e")
        }
    }

    /** Zrozumiały powód błędu połączenia z serwerem. */
    private fun explain(e: Exception): String {
        val url = prefs.serverUrl.ifBlank { "(brak adresu)" }
        return when (e) {
            is Server.HttpError -> if (e.code == 401)
                "Serwer $url odpowiada, ale nie przyjmuje klucza tego telefonu — połącz ponownie: Dołącz przez internet albo kod z panelu serwera."
                else "Serwer $url odpowiedział błędem ${e.code}: ${e.message}"
            is java.net.UnknownHostException -> "Nieznany adres serwera: $url. Wpisz adres Tailscale komputera z grami, np. 100.85.254.31."
            is java.net.SocketTimeoutException, is java.net.ConnectException, is java.net.NoRouteToHostException ->
                "Telefon nie łączy się z $url. Sprawdź, czy Tailscale na telefonie jest włączony (to samo konto co komputer z grami) i czy na komputerze działa serwer EmuStart."
            is java.net.MalformedURLException -> "Niepoprawny adres serwera: $url"
            else -> "${e.javaClass.simpleName}: ${e.message ?: ""} ($url)"
        }
    }

    // ── gra ──
    private fun startLaunch(id: Int, takeover: Boolean = false): JSONObject {
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
            // ustawienia i konto RetroAchievements profilu (osobno od komputera, wspólne dla telefonów)
            var raCfg: String? = null
            if (prefs.profile.isNotEmpty()) {
                when (Emulators.baseId(emu)) {
                    "retroarch" -> raCfg = emuProfile.retroarchBefore(prefs.profile, Emulators.raConfig(act, emu))?.absolutePath
                    "armsx2" -> emuProfile.armsx2(prefs.profile, prefs.saveDir("armsx2").ifEmpty { prefs.learnedDir("armsx2") })
                }
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
            .put("can_play_now", false).put("copying", false).put("locked_by", l.lockedBy)
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
        val withPin = server.profiles()
        if (withPin != null) {
            profList = withPin.map { it.first }
            profPin = withPin.toMap()
        } else {
            profList = saves.profiles()              // starszy serwer: bez PIN-ów
            profPin = emptyMap()
        }
    }

    /** ok | need_pin (PIN raz na tym telefonie) | set_pin (profil jeszcze bez PIN-u). */
    private fun pinState(n: String): String = when {
        n.isEmpty() || n !in profPin -> "ok"
        // telefon przypisany do konta: klucz urządzenia zastępuje PIN serwera
        prefs.deviceId.isNotEmpty() && n.equals(prefs.serverAccount, ignoreCase = true) -> "ok"
        profPin[n] != true -> "set_pin"
        prefs.token(n).isEmpty() -> "need_pin"
        else -> "ok"
    }

    private fun profilesList(): JSONObject {
        try { loadProfiles() } catch (e: Exception) { Log.w(tag, "profile: $e") }
        val arr = JSONArray()
        profList.forEachIndexed { i, n ->
            arr.put(JSONObject().put("id", i + 1).put("name", n).put("nas_name", n).put("pin", pinState(n)))
        }
        return JSONObject().put("profiles", arr).put("current", profileId())
    }

    private fun pinBlock(n: String): JSONObject? {
        val st = pinState(n)
        if (st == "ok") return null
        return JSONObject().put("ok", false).put("need", st).put("name", n)
            .put("reason", "Profil „$n” — " + if (st == "need_pin") "podaj PIN." else "ustaw PIN profilu.")
    }

    private fun selectProfile(id: Int): JSONObject {
        val n = profList.getOrNull(id - 1) ?: return JSONObject().put("ok", false)
        pinBlock(n)?.let { return it }
        prefs.profile = n
        if (prefs.phoneOwner.isEmpty()) prefs.phoneOwner = n
        return JSONObject().put("ok", true)
    }

    private fun opResult(n: String, r: JSONObject): JSONObject {
        if (!r.optBoolean("ok")) return JSONObject().put("ok", false)
            .put("reason", r.optString("error", "Nie udało się.")).put("exists", if (r.optBoolean("exists")) r.optString("name", n) else "")
        if (r.optBoolean("needs_pin")) return JSONObject().put("ok", false).put("need", "set_pin")
            .put("reason", "Ten profil nie ma jeszcze PIN-u — ustaw go.")
        val name = r.optString("name", n)
        prefs.setToken(name, r.optString("token"))
        try { loadProfiles() } catch (e: Exception) { }
        return JSONObject().put("ok", true).put("profile", JSONObject().put("id", profList.indexOf(name) + 1).put("name", name))
    }

    private fun createProfile(name: String, pin: String): JSONObject = try {
        if (profPin.isEmpty() && server.profiles() == null) {
            // starszy serwer: profil bez PIN-u, jak dotąd
            val n = saves.createProfile(name)
            profList = saves.profiles()
            JSONObject().put("ok", true).put("profile", JSONObject().put("id", profList.indexOf(n) + 1).put("name", n))
        } else opResult(name, server.profileOp("create", mapOf("name" to name, "pin" to pin)))
    } catch (e: Exception) {
        JSONObject().put("ok", false).put("reason", if (e is IllegalArgumentException) e.message else explain(e))
    }

    private fun joinProfile(name: String, pin: String): JSONObject = try {
        opResult(name, server.profileOp("login", mapOf("name" to name, "pin" to pin)))
    } catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)) }

    private fun unlockProfile(id: Int, pin: String): JSONObject {
        val n = profList.getOrNull(id - 1) ?: return JSONObject().put("ok", false).put("reason", "Nie ma takiego profilu.")
        return joinProfile(n, pin)
    }

    private fun setPin(id: Int, pin: String, old: String): JSONObject {
        val n = profList.getOrNull(id - 1) ?: return JSONObject().put("ok", false).put("reason", "Nie ma takiego profilu.")
        return try {
            opResult(n, server.profileOp("pin", mapOf("name" to n, "pin" to pin, "old" to old)))
        } catch (e: Exception) { JSONObject().put("ok", false).put("reason", explain(e)) }
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
    private fun remote(url: String): String = if (url.isBlank()) "" else "/remote$url"

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
