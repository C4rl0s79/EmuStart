package io.github.c4rl0s79.emustart

import org.json.JSONArray
import org.json.JSONObject
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URLEncoder

/** Klient serwera EmuStart (komputer z grami, `EmuStart.exe --server`). */
class Server(private val prefs: Prefs) {

    class HttpError(val code: Int, msg: String) : Exception(msg)

    /** Skrót identyfikatora sprzętu (ANDROID_ID — stały dla aplikacji na tym telefonie, także po
     *  ponownej instalacji); serwer trzyma jedną rejestrację na sprzęt. Ustawia Bridge. */
    @Volatile var hwId: String = ""

    /** Profil, którego dotyczy zapytanie (parametr p= / profile=). */
    private fun profileOf(path: String): String {
        val q = path.substringAfter('?', "")
        val v = q.split('&').firstNotNullOfOrNull { kv ->
            val k = kv.substringBefore('=')
            if (k == "p" || k == "profile") java.net.URLDecoder.decode(kv.substringAfter('='), "UTF-8") else null
        } ?: return ""
        return v.replace('\\', '/').substringBefore('/')
    }

    /** Token PIN-u profilu, którego dotyczy zapytanie. */
    private fun profileToken(c: HttpURLConnection, path: String) {
        val prof = profileOf(path)
        val t = if (prof.isEmpty()) "" else prefs.token(prof)
        if (t.isNotEmpty()) c.setRequestProperty("X-Profile-Token", t)
    }

    // ── adres serwera: najlepszy z podanych przez serwer (sieć lokalna → Tailscale → publiczny) ──
    private data class Ep(val url: String, val rank: Int, val ca: Boolean)
    /** Adresy z certyfikatem sprawdzanym zwykłym trybem (tunel Cloudflare) — bez przypinania. */
    @Volatile private var caUrls: Set<String> = emptySet()
    @Volatile private var base = ""
    @Volatile private var baseAt = 0L
    @Volatile private var baseSig = ""

    private fun rankOf(url: String, kindName: String = ""): Int {
        val host = url.substringAfter("://").substringBefore('/').substringBeforeLast(':')
        val kind = when (kindName) {
            "lan" -> 0
            "tailscale" -> 1
            "public" -> 2
            "tunnel" -> 3
            else -> when {
                host.matches(Regex("""100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\..*""")) -> 1
                host.matches(Regex("""(10\..*|192\.168\..*|172\.(1[6-9]|2\d|3[01])\..*|169\.254\..*)""")) -> 0
                !host.contains('.') -> 0
                else -> 2
            }
        }
        return kind * 2 + (if (url.startsWith("https")) 1 else 0)
    }

    /** Wpisany adres + adresy podane przez serwer. HTTPS z własnym certyfikatem serwera tylko
     *  z odciskiem; tunel (i ręcznie wpisany adres https, którego serwer nie podał) — zwykłe
     *  sprawdzanie certyfikatu. Kolejność: sieć lokalna, Tailscale, publiczny, tunel. */
    private fun candidates(): List<Ep> {
        val known = LinkedHashMap<String, Ep>()
        try {
            val a = JSONArray(prefs.serverEndpoints.ifBlank { "[]" })
            for (i in 0 until a.length()) {
                val o = a.optJSONObject(i) ?: continue
                val u = o.optString("url").trimEnd('/').takeIf { it.isNotBlank() } ?: continue
                known[u] = Ep(u, rankOf(u, o.optString("kind")), o.optBoolean("ca"))
            }
        } catch (e: Exception) { }
        val out = LinkedHashMap<String, Ep>()
        val typed = prefs.serverUrl.trimEnd('/')
        if (typed.isNotBlank()) out[typed] = known[typed]
            ?: if (typed.startsWith("https")) Ep(typed, rankOf(typed, "tunnel"), true) else Ep(typed, rankOf(typed), false)
        for ((u, e) in known) if (u !in out) out[u] = e
        // tylko szyfrowane (0.43): własny certyfikat serwera — z odciskiem, tunel — zwykłe sprawdzanie
        val list = out.values.filter { it.url.startsWith("https://") && (it.ca || prefs.serverFp.isNotEmpty()) }
        caUrls = list.filter { it.ca }.map { it.url }.toSet()
        return list
    }

    /** Czy pod adresem odpowiada serwer EmuStart — czas w ms albo null. Bez logowania: wyzwanie
     *  logowania dla pustego id (odpowiedź „nieznane urządzenie”). Wcześniej test logował się
     *  przez badany adres pod wspólną blokadą logowania — martwy adres (np. Tailscale wyłączony)
     *  trzymał ją 8 s, testy nie mieściły się w czasie i wybierany był martwy adres. */
    private fun probe(url: String): Long? = try {
        val t = System.nanoTime()
        val c = Net.open("$url/v1/auth/challenge", prefs.serverFp, url in caUrls)
        c.connectTimeout = 2500; c.readTimeout = 2500
        c.requestMethod = "POST"; c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json")
        c.outputStream.use { it.write("""{"device": ""}""".toByteArray()) }
        val code = c.responseCode
        val body = (if (code in 200..299) c.inputStream else c.errorStream)?.bufferedReader()?.readText() ?: ""
        val ours = code in setOf(200, 403) && try { JSONObject(body).has("reason") } catch (e: Exception) { false }
        if (ours) (System.nanoTime() - t) / 1_000_000 else null
    } catch (e: Exception) { null }

    /** Adres serwera do zapytań; sprawdzanie co 5 minut albo po błędzie połączenia. */
    fun baseUrl(): String {
        val cands = candidates()
        if (cands.size <= 1) return cands.firstOrNull()?.url ?: prefs.serverUrl
        val sig = cands.joinToString("|") { it.url } + prefs.deviceId + prefs.serverFp
        if (base.isNotEmpty() && sig == baseSig && System.currentTimeMillis() - baseAt < 300_000) return base
        val res = java.util.concurrent.ConcurrentHashMap<String, Long>()
        val doneSet = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
        val lock = Object()
        for (e in cands) Thread {
            probe(e.url)?.let { res[e.url] = it }
            doneSet += e.url
            synchronized(lock) { lock.notifyAll() }
        }.apply { isDaemon = true }.start()
        var end = System.currentTimeMillis() + 3500
        var firstOk = 0L
        synchronized(lock) {
            while (true) {
                val ok = cands.filter { res.containsKey(it.url) }
                val waiting = cands.filter { it.url !in doneSet }
                // dość, gdy odpowiedział adres, od którego żaden niesprawdzony nie byłby lepszy
                if (waiting.isEmpty() || (ok.isNotEmpty() && ok.minOf { it.rank } <= waiting.minOf { it.rank })) break
                if (ok.isNotEmpty()) {
                    // jest działający adres — na lepszy (sieć lokalna) najwyżej 0,4 s: adresy sieci
                    // lokalnej serwera z sieci komórkowej nigdy nie odpowiadają (było: 3,5 s czekania)
                    if (firstOk == 0L) firstOk = System.currentTimeMillis()
                    end = minOf(end, firstOk + 400)
                }
                val left = end - System.currentTimeMillis()
                if (left <= 0) break
                lock.wait(left)
            }
        }
        val ok = cands.filter { res.containsKey(it.url) }
        alive = ok.map { it.url }.toSet()
        val pick = ok.minWithOrNull(compareBy<Ep>({ it.rank }, { res[it.url] ?: 0L }))?.url ?: cands.first().url
        if (pick != base) android.util.Log.i("EmuStart", "serwer: adres $pick" +
            cands.joinToString(prefix = " (", postfix = ")") { e -> "${e.url} " + (res[e.url]?.let { "$it ms" } ?: "brak") })
        base = pick; baseSig = sig
        // żaden adres nie odpowiedział — nie trzymamy pierwszego z listy przez 5 minut: ponownie za 15 s
        baseAt = if (ok.isEmpty()) System.currentTimeMillis() - 285_000 else System.currentTimeMillis()
        lastKind = cands.firstOrNull { it.url == pick }?.let { kindName(it) } ?: ""
        return pick
    }

    /** Adresy, które odpowiedziały przy ostatnim sprawdzaniu — tylko przez nie logowanie. */
    @Volatile private var alive: Set<String> = emptySet()

    /** Rodzaj wybranego adresu (sieć lokalna, Tailscale, port publiczny, tunel) — do ustawień. */
    @Volatile var lastKind = ""

    private fun kindName(e: Ep): String = when (e.rank / 2) {
        0 -> "sieć lokalna"; 1 -> "Tailscale"; 2 -> "port publiczny"; else -> "tunel"
    }

    /** Adres przestał odpowiadać — następne zapytanie sprawdzi wszystkie od nowa. */
    fun forgetBase() { baseAt = 0L }

    /** Adresy i odcisk certyfikatu z odpowiedzi serwera (info, zalogowanie) → ustawienia.
     *  `trusted` — odpowiedź z połączenia zaufanego (przypięty certyfikat, tunel); inna nie zmienia
     *  już zapamiętanych (podszywający się w sieci podałby własny certyfikat i adresy). */
    fun learn(d: JSONObject, trusted: Boolean = true) {
        d.optJSONArray("endpoints")?.let {
            if (it.length() > 0 && it.toString() != prefs.serverEndpoints && (trusted || prefs.serverEndpoints.isBlank())) {
                prefs.serverEndpoints = it.toString(); forgetBase()
            }
        }
        d.optJSONObject("tls")?.optString("fp")?.takeIf { it.isNotEmpty() && it != prefs.serverFp && (trusted || prefs.serverFp.isEmpty()) }
            ?.let { prefs.serverFp = it; forgetBase() }
    }

    private fun conn(path: String): HttpURLConnection {
        val b = baseUrl()
        return Net.open(b + path, prefs.serverFp, b in caUrls)
    }

    // ── logowanie podpisem (klucz urządzenia w Android Keystore — DeviceKey.kt, emustart/enroll.py) ──
    @Volatile private var tok = ""
    @Volatile private var tokExp = 0L
    private val loginLock = Any()

    fun dropToken() { tok = ""; tokExp = 0L }

    /** Nagłówek klucza: token sesji z logowania podpisem kluczem urządzenia. */
    private fun bearer(via: String? = null): String =
        token(via).ifEmpty { "brak-tokenu" }

    private fun token(via: String?): String {
        val margin = 300_000L
        if (tok.isNotEmpty() && System.currentTimeMillis() < tokExp - margin) return tok
        synchronized(loginLock) {           // jedno logowanie naraz — reszta bierze ten sam token
            if (tok.isNotEmpty() && System.currentTimeMillis() < tokExp - margin) return tok
            // tylko adresy, które odpowiedziały przy sprawdzaniu (martwy adres = 8 s czekania na
            // połączenie; przy kilku martwych logowanie trwało minutę i dłużej)
            val known = candidates().map { it.url }.filter { alive.isEmpty() || it in alive }
            val urls = (listOfNotNull(via) + listOf(base).filter { it.isNotEmpty() } + known).distinct()
            for (u in urls) {
                try {
                    val ch = postJson(u, "/v1/auth/challenge", JSONObject().put("device", prefs.deviceId), u in caUrls)
                    if (!ch.optBoolean("ok")) {
                        // serwer odpowiedział „nie” — inne adresy prowadzą do tego samego serwera
                        android.util.Log.w("EmuStart", "logowanie: ${ch.optString("reason")}"); break
                    }
                    val nonce = ch.getString("nonce")
                    val sig = DeviceKey.sign(prefs.deviceKey, "emustart-auth|${prefs.deviceId}|$nonce".toByteArray())
                    val t = postJson(u, "/v1/auth/token", JSONObject().put("device", prefs.deviceId)
                        .put("nonce", nonce).put("sig", sig), u in caUrls)
                    if (t.optBoolean("ok")) {
                        tok = t.getString("token")
                        tokExp = (t.getDouble("expires") * 1000).toLong()
                        return tok
                    }
                    android.util.Log.w("EmuStart", "logowanie: ${t.optString("reason")}")
                } catch (e: Exception) { android.util.Log.w("EmuStart", "logowanie przez $u: $e") }
            }
            return ""
        }
    }

    /** POST JSON bez klucza (dołączanie, logowanie); `ca` — zwykłe sprawdzanie certyfikatu (tunel). */
    fun postJson(url: String, path: String, body: JSONObject, ca: Boolean = true, fp: String = prefs.serverFp): JSONObject {
        if (!url.startsWith("https://")) return JSONObject().put("ok", false).put("reason", "Tylko połączenie szyfrowane (https).")
        val c = Net.open(url.trimEnd('/') + path, fp, ca)
        try {
            c.connectTimeout = 8000
            c.readTimeout = 15000
            c.requestMethod = "POST"
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(body.toString().toByteArray()) }
            val status = c.responseCode
            if (status == 404) return JSONObject().put("ok", false).put("reason", "Serwer ma starszą wersję EmuStart — zaktualizuj go.")
            val txt = (if (status in 200..299) c.inputStream else c.errorStream)?.bufferedReader(Charsets.UTF_8)?.readText() ?: "{}"
            return try { JSONObject(txt) } catch (e: Exception) { JSONObject().put("ok", false).put("reason", "błąd serwera $status") }
        } finally { c.disconnect() }
    }

    /** POST JSON z kluczem urządzenia (zatwierdzanie próśb). */
    fun postAuthed(path: String, body: JSONObject): JSONObject {
        for (attempt in 0..1) {
            val c = conn(path)
            try {
                c.connectTimeout = 8000
                c.readTimeout = 15000
                c.requestMethod = "POST"
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.setRequestProperty("Authorization", "Bearer " + bearer())
                c.outputStream.use { it.write(body.toString().toByteArray()) }
                val status = c.responseCode
                if (status == 401 && prefs.deviceId.isNotEmpty() && attempt == 0) { dropToken(); continue }
                val txt = (if (status in 200..299) c.inputStream else c.errorStream)?.bufferedReader(Charsets.UTF_8)?.readText() ?: "{}"
                return try { JSONObject(txt) } catch (e: Exception) { JSONObject().put("ok", false).put("reason", "błąd serwera $status") }
            } finally { c.disconnect() }
        }
        return JSONObject().put("ok", false).put("reason", "serwer nie przyjął klucza urządzenia")
    }

    fun open(path: String, range: String? = null, timeoutMs: Int = 20000, retry: Boolean = true): HttpURLConnection {
        val c = conn(path)
        c.connectTimeout = 8000
        c.readTimeout = timeoutMs
        c.setRequestProperty("Authorization", "Bearer " + bearer())
        profileToken(c, path)
        c.setRequestProperty("User-Agent", "EmuStart-Android/" + BuildConfig.VERSION_NAME)
        c.setRequestProperty("X-EmuStart-Version", BuildConfig.VERSION_NAME)      // panel serwera: wersje urządzeń
        if (hwId.isNotEmpty()) c.setRequestProperty("X-EmuStart-Hw", hwId)          // jedna rejestracja na sprzęt
        if (range != null) c.setRequestProperty("Range", range)
        val code = try { c.responseCode } catch (e: java.io.IOException) { forgetBase(); throw e }
        if (code == 401 && retry && prefs.deviceId.isNotEmpty()) {
            c.disconnect()
            dropToken()                          // token wygasł (np. restart serwera) — logowanie od nowa
            return open(path, range, timeoutMs, false)
        }
        if (code !in 200..299) {
            val msg = try { c.errorStream?.bufferedReader()?.readText() ?: "" } catch (e: Exception) { "" }
            c.disconnect()
            // PIN zmieniony na innym urządzeniu: token nieważny — następnym razem pytamy o PIN
            if (code == 403 && msg.contains("\"pin\"")) profileOf(path).takeIf { it.isNotEmpty() }?.let { prefs.setToken(it, "") }
            throw HttpError(code, if (code == 401) "zły klucz serwera" else "błąd serwera $code $msg".trim())
        }
        return c
    }

    private fun text(path: String): String {
        val c = open(path)
        try { return c.inputStream.bufferedReader(Charsets.UTF_8).readText() } finally { c.disconnect() }
    }

    /** To urządzenie administratora (zmienia grafiki, opisy i logo dla wszystkich urządzeń). */
    @Volatile var admin = false

    fun info(): JSONObject {
        val viaTls = baseUrl().startsWith("https")
        return JSONObject(text("/v1/info")).also { learn(it, trusted = viaTls); admin = it.optBoolean("admin") }
    }

    /** /v1/admin/… serwera (tylko urządzenie administratora): {ok, …} albo {ok: false, reason}. */
    fun adminCall(method: String, path: String, body: JSONObject? = null): JSONObject {
        for (attempt in 0..1) {
            val c = conn("/v1/admin/$path")
            try {
                c.connectTimeout = 8000
                c.readTimeout = 60000
                c.requestMethod = method
                c.setRequestProperty("Authorization", "Bearer " + bearer())
                c.setRequestProperty("Content-Type", "application/json")
                if (method == "PUT") {
                    val b = (body ?: JSONObject()).toString().toByteArray()
                    c.doOutput = true
                    c.setFixedLengthStreamingMode(b.size)
                    c.outputStream.use { it.write(b) }
                }
                val code = c.responseCode
                val text = (if (code in 200..299) c.inputStream else c.errorStream)?.bufferedReader(Charsets.UTF_8)?.readText() ?: "{}"
                if (code == 401 && attempt == 0) { dropToken(); continue }
                if (code == 403) return JSONObject().put("ok", false).put("reason", "Zmieniać grafiki i opisy może tylko administrator.")
                if (code !in 200..299) {
                    val why = try { JSONObject(text).optString("error") } catch (e: Exception) { "" }
                    return JSONObject().put("ok", false).put("reason", why.ifEmpty { "błąd serwera $code" })
                }
                return JSONObject(text)
            } finally { c.disconnect() }
        }
        return JSONObject().put("ok", false).put("reason", "serwer nie przyjął klucza")
    }
    fun systems(): JSONArray = JSONArray(text("/v1/systems"))
    fun games(es: String): JSONArray = JSONArray(text("/v1/systems/" + enc(es) + "/games"))

    /** Strona listy gier: (wszystkich, gry od `offset`). Starszy serwer (bez stron) — cała lista. */
    fun gamesPage(es: String, offset: Int, limit: Int): Pair<Int, JSONArray> {
        val t = text("/v1/systems/" + enc(es) + "/games?offset=$offset&limit=$limit").trim()
        if (t.startsWith("[")) { val a = JSONArray(t); return Pair(a.length(), a) }
        val o = JSONObject(t)
        return Pair(o.getInt("total"), o.getJSONArray("games"))
    }
    /** Szczegóły gry; ścieżki plików zawsze z „/” (serwer na Windows sprzed 0.30.6 wysyłał „\\”,
     *  a na Androidzie „\\” to znak w nazwie pliku — EPERM w pamięci telefonu). */
    fun game(id: Int): JSONObject {
        val d = JSONObject(text("/v1/games/$id"))
        for (k in listOf("file", "rel")) if (d.has(k)) d.put(k, d.optString(k).replace('\\', '/'))
        d.optJSONArray("files")?.let { a ->
            for (i in 0 until a.length()) a.optJSONObject(i)?.let { f -> f.put("path", f.optString("path").replace('\\', '/')) }
        }
        return d
    }

    fun fileUrlPath(id: Int, rel: String): String =
        "/v1/games/$id/file/" + rel.split('/', '\\').joinToString("/") { enc(it) }

    /** Grafika (adres względny z listy, np. /media/…) — strumień z serwera. */
    fun media(rel: String): Pair<String, InputStream> {
        val c = open(rel, timeoutMs = 15000)
        return Pair(c.contentType ?: "image/png", c.inputStream)
    }

    // ── folder profili na serwerze (/v1/nas/…): zapisy wspólne z EmuStart na Windows ──

    private fun nasPath(op: String, p: String, extra: String = ""): String = "/v1/nas/$op?p=" + enc(p) + extra

    /** Pliki pod ścieżką: {ścieżka względna: [rozmiar, czas w s]}; brak folderu — pusta mapa. */
    fun nasScan(p: String): Map<String, DoubleArray> {
        val o = JSONObject(text(nasPath("scan", p))).optJSONObject("files") ?: JSONObject()
        val out = HashMap<String, DoubleArray>()
        for (k in o.keys()) {
            val a = o.getJSONArray(k)
            out[k] = doubleArrayOf(a.getDouble(0), a.getDouble(1))
        }
        return out
    }

    /** Zawartość folderu: [(nazwa, czy folder)]; brak folderu — pusta lista. */
    fun nasList(p: String): List<Pair<String, Boolean>> = try {
        val a = JSONObject(text(nasPath("list", p))).optJSONArray("entries") ?: JSONArray()
        (0 until a.length()).map { val e = a.getJSONArray(it); Pair(e.getString(0), e.getBoolean(1)) }
    } catch (e: HttpError) { if (e.code == 404) emptyList() else throw e }

    /** Mały plik (blokada, indeks); null = nie ma. */
    fun nasRead(p: String): ByteArray? = try {
        val c = open(nasPath("file", p))
        try { c.inputStream.readBytes() } finally { c.disconnect() }
    } catch (e: HttpError) { if (e.code == 404) null else throw e }

    /** Plik z serwera do `dst` (przez plik tymczasowy), z czasem modyfikacji z serwera. */
    fun nasGet(p: String, dst: java.io.File): Boolean {
        val c = try { open(nasPath("file", p), timeoutMs = 60000) } catch (e: HttpError) {
            if (e.code == 404) return false else throw e
        }
        try {
            dst.parentFile?.mkdirs()
            val tmp = java.io.File(dst.path + ".emustart-tmp")
            c.inputStream.use { i -> tmp.outputStream().use { o -> i.copyTo(o) } }
            val mt = c.getHeaderField("X-Mtime")?.toDoubleOrNull()
            if (dst.exists()) dst.delete()
            if (!tmp.renameTo(dst)) { tmp.copyTo(dst, true); tmp.delete() }
            if (mt != null) dst.setLastModified((mt * 1000).toLong())
            return true
        } finally { c.disconnect() }
    }

    /** Zapis pliku na serwerze; `backup` — gdzie serwer odkłada poprzednią wersję. */
    fun nasPut(p: String, data: ByteArray, mtimeSec: Double, backup: String = "") {
        val extra = "&mtime=$mtimeSec" + (if (backup.isNotEmpty()) "&backup=" + enc(backup) else "")
        val c = conn(nasPath("file", p, extra))
        try {
            c.connectTimeout = 8000
            c.readTimeout = 60000
            c.requestMethod = "PUT"
            c.doOutput = true
            c.setFixedLengthStreamingMode(data.size)
            c.setRequestProperty("Authorization", "Bearer " + bearer())
            profileToken(c, nasPath("file", p, extra))
            c.setRequestProperty("Content-Type", "application/octet-stream")
            c.outputStream.use { it.write(data) }
            val code = c.responseCode
            if (code != 200) throw HttpError(code, "zapis na serwerze: $code")
            c.inputStream.use { it.readBytes() }
        } finally { c.disconnect() }
    }

    // ── menedżer zapisów (emustart/savemgr.py na serwerze) ──
    private fun query(q: Map<String, String>) = q.entries.joinToString("&") { enc(it.key) + "=" + enc(it.value) }

    /** Stany gry z komputera i telefonu (ze zgodnością) i wspólne zapisy w grze. */
    fun saveCatalog(q: Map<String, String>): JSONObject = JSONObject(text("/v1/savemgr/catalog?" + query(q)))

    /** Kopia stanu na drugą platformę (w folderze profili na serwerze). */
    fun saveCopy(q: Map<String, String>): JSONObject {
        val c = conn("/v1/savemgr/copy?" + query(q))
        try {
            c.connectTimeout = 8000
            c.readTimeout = 120000
            c.requestMethod = "PUT"
            c.doOutput = true
            c.setFixedLengthStreamingMode(0)
            c.setRequestProperty("Authorization", "Bearer " + bearer())
            profileToken(c, "/v1/savemgr/copy?" + query(q))
            c.outputStream.close()
            val code = c.responseCode
            if (code != 200) throw HttpError(code, "kopia stanu na serwerze: $code")
            return JSONObject(c.inputStream.bufferedReader(Charsets.UTF_8).readText())
        } finally { c.disconnect() }
    }

    /** BIOS-y rdzenia, które serwer ma (ścieżki względem folderu system RetroArcha). */
    fun biosFor(core: String): List<String> = try {
        val a = JSONObject(text("/v1/bios?core=" + enc(core))).optJSONArray("files") ?: JSONArray()
        (0 until a.length()).map { a.getString(it) }
    } catch (e: HttpError) { if (e.code == 404) emptyList() else throw e }

    /** BIOS z folderu BIOS-ów serwera (po nazwie) do `dst`; false = serwer go nie ma. */
    fun bios(name: String, dst: java.io.File): Boolean {
        val c = try { open("/v1/bios?name=" + enc(name), timeoutMs = 30000) } catch (e: HttpError) {
            if (e.code == 404) return false else throw e
        }
        try {
            val tmp = java.io.File(dst.path + ".part")
            c.inputStream.use { i -> tmp.outputStream().use { o -> i.copyTo(o) } }
            return tmp.renameTo(dst)
        } finally { c.disconnect() }
    }

    // ── profile z PIN-em (/v1/profiles) ──

    /** [(nazwa, czy ma PIN)]; null — serwer bez PIN-ów profili (starszy niż 0.27). */
    /** Konta (nazwa, czy ma hasło) — urządzenie konta widzi swoje; null = starszy serwer. */
    fun profiles(): List<Pair<String, Boolean>>? = try {
        val a = JSONObject(text("/v1/profiles")).optJSONArray("profiles") ?: JSONArray()
        (0 until a.length()).map { val o = a.getJSONObject(it); Pair(o.getString("name"), o.optBoolean("password", o.optBoolean("pin"))) }
    } catch (e: HttpError) { if (e.code == 404) null else throw e }

    /** Adres do operacji z hasłem konta: ten sam komputer na porcie HTTPS z przypiętym
     *  certyfikatem, gdy jest (zwykły port przyjmuje tylko sieć lokalną i Tailscale). */
    private fun secureBase(): String {
        val b = baseUrl()
        if (b.startsWith("https") || prefs.serverFp.isEmpty()) return b
        val host = b.substringAfter("://").substringBefore('/').substringBeforeLast(':')
        return candidates().firstOrNull {
            it.url.startsWith("https") && !it.ca && it.url.substringAfter("://").substringBefore('/').substringBeforeLast(':') == host
        }?.url ?: b
    }

    /** login | create | password — hasło w treści JSON (nigdy w adresie); odpowiedź serwera
     *  także przy błędzie (403 złe hasło, 409 nazwa zajęta, 429 za dużo prób). */
    fun profileOp(op: String, body: JSONObject): JSONObject {
        for (attempt in 0..1) {
            val b = secureBase()
            val c = Net.open("$b/v1/profiles/$op", prefs.serverFp, b in caUrls)
            try {
                c.connectTimeout = 8000
                c.readTimeout = 30000
                c.requestMethod = "PUT"
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.setRequestProperty("Authorization", "Bearer " + bearer())
                c.outputStream.use { it.write(body.toString().toByteArray()) }
                val code = c.responseCode
                if (code == 401 && prefs.deviceId.isNotEmpty() && attempt == 0) { dropToken(); continue }
                if (code == 401) throw HttpError(code, "zły klucz serwera")
                val txt = (if (code in 200..299) c.inputStream else c.errorStream)?.bufferedReader(Charsets.UTF_8)?.readText() ?: "{}"
                val o = try { JSONObject(txt) } catch (e: Exception) { JSONObject().put("error", "błąd serwera $code") }
                if (code == 404 && !o.has("error")) o.put("error", "Serwer ma starszą wersję EmuStart — zaktualizuj go.")
                if (code == 409) o.put("exists", true)
                return o
            } finally { c.disconnect() }
        }
        throw HttpError(401, "zły klucz serwera")
    }

    // ── synchronizacja zapisów gier (emustart/savesync.py) ──
    class SyncReply(val code: Int, val body: ByteArray)

    /** Zapytanie do API zapisów. Konto gracza w nagłówku — inne niż konto telefonu tylko
     *  z tokenem tego konta (serwer bierze konto z tokenu sesji, nie z treści zapytania).
     *  `file` — treść PUT (paczka ZIP), `sink` — tu trafia pobrana paczka. */
    fun sync(method: String, path: String, account: String, json: JSONObject? = null,
             file: java.io.File? = null, sink: java.io.File? = null): SyncReply {
        for (attempt in 0..1) {
            val c = conn(path)
            try {
                c.connectTimeout = 8000
                c.readTimeout = if (file != null || sink != null) 120000 else 20000
                c.requestMethod = method
                c.setRequestProperty("Authorization", "Bearer " + bearer())
                c.setRequestProperty("X-EmuStart-Version", BuildConfig.VERSION_NAME)
                if (hwId.isNotEmpty()) c.setRequestProperty("X-EmuStart-Hw", hwId)
                if (account.isNotEmpty()) {
                    c.setRequestProperty("X-EmuStart-Account", enc(account))
                    prefs.token(account).takeIf { it.isNotEmpty() }?.let { c.setRequestProperty("X-Profile-Token", it) }
                }
                if (json != null) {
                    val b = json.toString().toByteArray()
                    c.doOutput = true
                    c.setRequestProperty("Content-Type", "application/json")
                    c.setFixedLengthStreamingMode(b.size)
                    c.outputStream.use { it.write(b) }
                } else if (file != null) {
                    c.doOutput = true
                    c.setRequestProperty("Content-Type", "application/zip")
                    c.setFixedLengthStreamingMode(file.length())
                    file.inputStream().use { i -> c.outputStream.use { o -> i.copyTo(o) } }
                }
                val code = try { c.responseCode } catch (e: java.io.IOException) { forgetBase(); throw e }
                if (code == 401 && attempt == 0 && prefs.deviceId.isNotEmpty()) { dropToken(); continue }
                if (code == 200 && sink != null) {
                    c.inputStream.use { i -> sink.outputStream().use { o -> i.copyTo(o) } }
                    return SyncReply(code, ByteArray(0))
                }
                val body = try {
                    (if (code in 200..299) c.inputStream else c.errorStream)?.use { it.readBytes() } ?: ByteArray(0)
                } catch (e: java.io.IOException) { ByteArray(0) }
                return SyncReply(code, body)
            } finally { c.disconnect() }
        }
        return SyncReply(401, ByteArray(0))
    }

    fun nasDelete(p: String) {
        val c = conn(nasPath("file", p))
        try {
            c.connectTimeout = 8000
            c.readTimeout = 20000
            c.requestMethod = "DELETE"
            c.setRequestProperty("Authorization", "Bearer " + bearer())
            profileToken(c, nasPath("file", p))
            val code = c.responseCode
            if (code != 200 && code != 404) throw HttpError(code, "usuwanie na serwerze: $code")
        } finally { c.disconnect() }
    }

    companion object {
        fun enc(s: String): String = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
    }
}
