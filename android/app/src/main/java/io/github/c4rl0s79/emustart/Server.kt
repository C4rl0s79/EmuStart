package io.github.c4rl0s79.emustart

import org.json.JSONArray
import org.json.JSONObject
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Klient serwera EmuStart (komputer z grami, `EmuStart.exe --server`). */
class Server(private val prefs: Prefs) {

    class HttpError(val code: Int, msg: String) : Exception(msg)

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

    fun open(path: String, range: String? = null, timeoutMs: Int = 20000): HttpURLConnection {
        val c = URL(prefs.serverUrl + path).openConnection() as HttpURLConnection
        c.connectTimeout = 8000
        c.readTimeout = timeoutMs
        c.setRequestProperty("Authorization", "Bearer " + prefs.serverKey)
        profileToken(c, path)
        c.setRequestProperty("User-Agent", "EmuStart-Android/" + BuildConfig.VERSION_NAME)
        c.setRequestProperty("X-EmuStart-Version", BuildConfig.VERSION_NAME)      // panel serwera: wersje urządzeń
        if (range != null) c.setRequestProperty("Range", range)
        val code = c.responseCode
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

    fun info(): JSONObject = JSONObject(text("/v1/info"))
    fun systems(): JSONArray = JSONArray(text("/v1/systems"))
    fun games(es: String): JSONArray = JSONArray(text("/v1/systems/" + enc(es) + "/games"))
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
        val c = URL(prefs.serverUrl + nasPath("file", p, extra)).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = 8000
            c.readTimeout = 60000
            c.requestMethod = "PUT"
            c.doOutput = true
            c.setFixedLengthStreamingMode(data.size)
            c.setRequestProperty("Authorization", "Bearer " + prefs.serverKey)
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
        val c = URL(prefs.serverUrl + "/v1/savemgr/copy?" + query(q)).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = 8000
            c.readTimeout = 120000
            c.requestMethod = "PUT"
            c.doOutput = true
            c.setFixedLengthStreamingMode(0)
            c.setRequestProperty("Authorization", "Bearer " + prefs.serverKey)
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
    fun profiles(): List<Pair<String, Boolean>>? = try {
        val a = JSONObject(text("/v1/profiles")).optJSONArray("profiles") ?: JSONArray()
        (0 until a.length()).map { val o = a.getJSONObject(it); Pair(o.getString("name"), o.optBoolean("pin")) }
    } catch (e: HttpError) { if (e.code == 404) null else throw e }

    /** login | create | pin — odpowiedź serwera także przy błędzie (403 zły PIN, 409 nazwa zajęta). */
    fun profileOp(op: String, q: Map<String, String>): JSONObject {
        val c = URL(prefs.serverUrl + "/v1/profiles/$op?" + query(q)).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = 8000
            c.readTimeout = 30000
            c.requestMethod = "PUT"
            c.doOutput = true
            c.setFixedLengthStreamingMode(0)
            c.setRequestProperty("Authorization", "Bearer " + prefs.serverKey)
            c.outputStream.close()
            val code = c.responseCode
            if (code == 401) throw HttpError(code, "zły klucz serwera")
            val body = (if (code in 200..299) c.inputStream else c.errorStream)?.bufferedReader(Charsets.UTF_8)?.readText() ?: "{}"
            val o = try { JSONObject(body) } catch (e: Exception) { JSONObject().put("error", "błąd serwera $code") }
            if (code == 409) o.put("exists", true)
            return o
        } finally { c.disconnect() }
    }

    /** Zatwierdzenie nowego urządzenia (POST /v1/pair/approve); allow == null — podgląd prośby. */
    fun approve(code: String, allow: Boolean?): JSONObject {
        val c = URL(prefs.serverUrl + "/v1/pair/approve").openConnection() as HttpURLConnection
        try {
            c.connectTimeout = 8000
            c.readTimeout = 15000
            c.requestMethod = "POST"
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.setRequestProperty("Authorization", "Bearer " + prefs.serverKey)
            val b = JSONObject().put("code", code)
            if (allow != null) b.put("allow", allow)
            c.outputStream.use { it.write(b.toString().toByteArray()) }
            val status = c.responseCode
            if (status == 404) return JSONObject().put("ok", false).put("reason", "Serwer ma starszą wersję EmuStart — zaktualizuj go.")
            val body = (if (status in 200..299) c.inputStream else c.errorStream)?.bufferedReader(Charsets.UTF_8)?.readText() ?: "{}"
            return try { JSONObject(body) } catch (e: Exception) { JSONObject().put("ok", false).put("reason", "błąd serwera $status") }
        } finally { c.disconnect() }
    }

    fun nasDelete(p: String) {
        val c = URL(prefs.serverUrl + nasPath("file", p)).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = 8000
            c.readTimeout = 20000
            c.requestMethod = "DELETE"
            c.setRequestProperty("Authorization", "Bearer " + prefs.serverKey)
            profileToken(c, nasPath("file", p))
            val code = c.responseCode
            if (code != 200 && code != 404) throw HttpError(code, "usuwanie na serwerze: $code")
        } finally { c.disconnect() }
    }

    companion object {
        fun enc(s: String): String = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
    }
}
