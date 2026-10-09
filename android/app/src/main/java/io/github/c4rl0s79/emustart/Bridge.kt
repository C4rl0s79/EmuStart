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
import java.util.concurrent.Executors

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

    init {
        pool.execute { try { saves.retryPending() } catch (e: Exception) { Log.w(tag, "zaległe zapisy: $e") } }
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
        var downloader: Downloader? = null
        var emu = ""
        var saveGame: Saves.Game? = null
        var profile = ""
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
        "get_settings" -> settings()
        "save_settings" -> { saveSettings(a.getJSONObject(0)); JSONObject().put("ok", true) }
        "server_test" -> serverTest()
        "rescan" -> { systemsCache = null; synchronized(gamesCache) { gamesCache.clear() }; JSONObject().put("ok", true) }
        "launch" -> startLaunch(a.getInt(0))
        "launch_status" -> launchStatus()
        "launch_cancel" -> { launch?.downloader?.cancel?.set(true); null }
        "launch_dismiss" -> { val l = launch; if (l != null && l.phase in setOf("finished", "error", "cancelled")) launch = null; null }
        "launch_play_now" -> null
        "toggle_pin" -> togglePin(a.getInt(0))
        "look_save" -> { prefs.look = a.getJSONObject(0); JSONObject().put("ok", true) }
        "quit" -> { act.runOnUiThread { act.finishAndRemoveTask() }; null }
        "copy_status" -> JSONArray()
        "post_status" -> saves.busy
        "art_status" -> null
        "art_for" -> JSONObject()
        "system_info" -> JSONObject()
        "profiles_list" -> profilesList()
        "profile_select", "profile_setup" -> selectProfile(a.getInt(0))
        "profile_create" -> createProfile(a.getString(0))
        "profile_rename", "profile_delete", "profile_nas_rename" -> JSONObject().put("ok", false)
            .put("reason", "Nazwę i folder profilu zmienia się w EmuStart na komputerze.")
        "pad_backend" -> "android"
        else -> null      // request_art, meta_fetch, ui_log… — niepotrzebne na Androidzie
    }

    // ── stan i listy ──
    private fun state(): JSONObject {
        val sys = try { systems() } catch (e: Exception) { JSONArray() }
        if (profList.isEmpty() && online) try { profList = saves.profiles() } catch (e: Exception) { }
        return JSONObject()
            .put("version", BuildConfig.VERSION_NAME).put("platform", "android")
            .put("configured", prefs.configured).put("rom_online", online)
            .put("network_mode", "remote").put("systems", sys).put("copying", JSONArray())
            .put("games_logo", prefs.gamesLogo).put("look", prefs.look).put("look_scope", "profile")
            .put("setup_needed", prefs.configured && online && prefs.profile.isEmpty())
            .put("ask_profile", false).put("conflicts", drainConflicts())
            .put("profile", JSONObject().put("id", profileId()).put("name", prefs.profile.ifEmpty { "—" }))
            .put("profiles", profList.size.coerceAtLeast(1))
            .put("py_pad", false).put("pad_backend", "android")
    }

    private fun cacheFile(name: String) = File(act.filesDir, "lista-$name.json")

    private fun systems(): JSONArray {
        systemsCache?.let { return it }
        if (!prefs.configured) return JSONArray()
        val arr = try {
            server.systems().also { online = true; cacheFile("systemy").writeText(it.toString()) }
        } catch (e: Exception) {
            online = false
            Log.w(tag, "serwer: $e")
            val f = cacheFile("systemy")
            if (f.isFile) JSONArray(f.readText()) else JSONArray()
        }
        for (i in 0 until arr.length()) {
            val s = arr.getJSONObject(i)
            s.put("logo", remote(s.optString("logo")))
            val emu = chosenEmu(s)
            s.put("emulator", if (emu.isEmpty()) "" else Emulators.label(emu))
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

    private fun detail(id: Int): JSONObject {
        val d = server.game(id)
        val files = d.optJSONArray("files") ?: JSONArray()
        d.put("filelist", files).put("files", files.length())
        val e = Cache.index(act).optJSONObject(key(d.optString("es"), d.optString("name")))
        d.put("cached", e?.optBoolean("complete") == true).put("pinned", e?.optBoolean("pinned") == true)
        d.put("copying", false).put("meta_online", true)
        for (k in listOf("box", "snap", "logo")) if (d.has(k)) d.put(k, remote(d.optString(k)))
        return d
    }

    // ── ustawienia ──
    private fun chosenEmu(s: JSONObject): String {
        val es = s.optString("es")
        val opts = Emulators.options(act, s.optString("plat"), s.optString("core"))
        val pick = prefs.emulator(es)
        return if (opts.any { it.first == pick }) pick else opts.firstOrNull()?.first ?: ""
    }

    private fun settings(): JSONObject {
        val sys = JSONArray()
        val arr = systems()
        for (i in 0 until arr.length()) {
            val s = arr.getJSONObject(i)
            val opts = JSONArray()
            for ((oid, label) in Emulators.options(act, s.optString("plat"), s.optString("core"))) {
                opts.put(JSONObject().put("id", oid).put("label", label))
            }
            sys.put(JSONObject().put("es", s.optString("es")).put("display", s.optString("display"))
                .put("options", opts).put("emulator", chosenEmu(s)))
        }
        return JSONObject()
            .put("server_url", prefs.serverUrl).put("server_key", prefs.serverKey)
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
                    if (u.isNotEmpty() && !u.startsWith("http")) u = "http://$u"
                    if (u.isNotEmpty() && !Regex(":\\d+$").containsMatchIn(u.removePrefix("http://").removePrefix("https://"))) u += ":8740"
                    prefs.serverUrl = u; systemsCache = null
                }
                k == "server_key" -> { prefs.serverKey = d.getString(k); systemsCache = null }
                k == "cache_recent" -> prefs.cacheRecent = d.getInt(k)
                k == "streams" -> prefs.streams = d.getInt(k)
                k == "games_logo" -> prefs.gamesLogo = d.getBoolean(k)
                k.startsWith("emu:") -> { prefs.setEmulator(k.removePrefix("emu:"), d.getString(k)); systemsCache = null }
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

    /** Zrozumiały powód błędu połączenia z serwerem. */
    private fun explain(e: Exception): String {
        val url = prefs.serverUrl.ifBlank { "(brak adresu)" }
        return when (e) {
            is Server.HttpError -> if (e.code == 401)
                "Serwer $url odpowiada, ale klucz jest nieprawidłowy. Skopiuj klucz z komputera z grami (EmuStart.exe --server-key)."
                else "Serwer $url odpowiedział błędem ${e.code}: ${e.message}"
            is java.net.UnknownHostException -> "Nieznany adres serwera: $url. Wpisz adres Tailscale komputera z grami, np. 100.85.254.31."
            is java.net.SocketTimeoutException, is java.net.ConnectException, is java.net.NoRouteToHostException ->
                "Telefon nie łączy się z $url. Sprawdź, czy Tailscale na telefonie jest włączony (to samo konto co komputer z grami) i czy na komputerze działa serwer EmuStart."
            is java.net.MalformedURLException -> "Niepoprawny adres serwera: $url"
            else -> "${e.javaClass.simpleName}: ${e.message ?: ""} ($url)"
        }
    }

    // ── gra ──
    private fun startLaunch(id: Int): JSONObject {
        val busy = launch?.phase.let { it == "preparing" || it == "downloading" }
        if (busy) return JSONObject().put("ok", false).put("reason", "Inna gra jest właśnie pobierana.")
        val g = try { server.game(id) } catch (e: Exception) {
            return JSONObject().put("ok", false).put("reason", explain(e))
        }
        val s = sysByEs(g.optString("es")) ?: return JSONObject().put("ok", false).put("reason", "Nieznany system.")
        val emu = chosenEmu(s)
        if (emu.isEmpty()) return JSONObject().put("ok", false)
            .put("reason", "Brak emulatora dla ${s.optString("display")} — zainstaluj go i wybierz w Ustawieniach.")
        val l = Launch(g, s)
        launch = l
        pool.execute { runLaunch(l, emu) }
        return JSONObject().put("ok", true)
    }

    private fun runLaunch(l: Launch, emu: String) {
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
            Cache.complete(act, key(es, g.optString("name")), files.length().let { _ ->
                (0 until files.length()).map { File(base, files.getJSONObject(it).getString("path")).absolutePath } })
            Cache.enforce(act, prefs.cacheRecent, key(es, g.optString("name")))
            val main = File(base, g.optString("file").ifEmpty { files.getJSONObject(0).getString("path") })
            // zapisy profilu: nowsze z serwera do folderu emulatora (wspólne z EmuStart na Windows)
            if (prefs.profile.isNotEmpty() && Saves.famFor(emu) != null) {
                l.phase = "preparing"; l.message = "Zapisy profilu ${prefs.profile}…"
                val sg = Saves.Game(es, g.optString("name"), main.nameWithoutExtension, main.parentFile ?: base,
                                    Emulators.retroCore(l.sys.optString("plat"), l.sys.optString("core")))
                try {
                    val msg = saves.before(prefs.profile, emu, sg)
                    l.message = msg
                    if (msg.isNotEmpty()) Log.i(tag, msg)
                } catch (e: Saves.Locked) {
                    l.phase = "error"
                    l.message = "Profil „${prefs.profile}” gra teraz na: ${e.host}. Wybierz inny profil albo zakończ tamtą grę."
                    return
                }
                l.emu = emu; l.saveGame = sg; l.profile = prefs.profile
            }
            val intent = Emulators.intent(act, emu, main, l.sys.optString("plat"), l.sys.optString("core"))
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
            .put("can_play_now", false).put("copying", false)
        l.progress?.let { o.put("progress", JSONObject(it.snapshot())) }
        return o
    }

    /** Powrót z emulatora: gra skończona. */
    fun onResumed() {
        val l = launch ?: return
        if (l.phase == "running" && System.currentTimeMillis() - l.startedAt > 3000) {
            l.phase = "finished"
            val sg = l.saveGame
            if (sg != null) pool.execute {
                try { saves.after(l.profile, l.emu, sg, l.startedAt, l.game.optString("title")) }
                catch (e: Exception) { Log.w(tag, "zapisy po grze: $e") }
            }
            synchronized(gamesCache) { gamesCache.clear() }
            act.js("window.__emuResumed && window.__emuResumed()")
        }
    }

    // ── profile (foldery profili na serwerze, te same co w EmuStart na Windows) ──
    private fun profileId(): Int = profList.indexOfFirst { it == prefs.profile }.let { if (it < 0) 0 else it + 1 }

    private fun profilesList(): JSONObject {
        try { profList = saves.profiles() } catch (e: Exception) { Log.w(tag, "profile: $e") }
        val arr = JSONArray()
        profList.forEachIndexed { i, n -> arr.put(JSONObject().put("id", i + 1).put("name", n).put("nas_name", n)) }
        return JSONObject().put("profiles", arr).put("current", profileId())
    }

    private fun selectProfile(id: Int): JSONObject {
        val n = profList.getOrNull(id - 1) ?: return JSONObject().put("ok", false)
        prefs.profile = n
        if (prefs.phoneOwner.isEmpty()) prefs.phoneOwner = n
        return JSONObject().put("ok", true)
    }

    private fun createProfile(name: String): JSONObject = try {
        val n = saves.createProfile(name)
        profList = saves.profiles()
        JSONObject().put("ok", true).put("profile", JSONObject().put("id", profList.indexOf(n) + 1).put("name", n))
    } catch (e: Exception) {
        JSONObject().put("ok", false).put("reason", if (e is IllegalArgumentException) e.message else explain(e))
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
