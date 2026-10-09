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

    fun open(path: String, range: String? = null, timeoutMs: Int = 20000): HttpURLConnection {
        val c = URL(prefs.serverUrl + path).openConnection() as HttpURLConnection
        c.connectTimeout = 8000
        c.readTimeout = timeoutMs
        c.setRequestProperty("Authorization", "Bearer " + prefs.serverKey)
        c.setRequestProperty("User-Agent", "EmuStart-Android/" + BuildConfig.VERSION_NAME)
        if (range != null) c.setRequestProperty("Range", range)
        val code = c.responseCode
        if (code !in 200..299) {
            val msg = try { c.errorStream?.bufferedReader()?.readText() ?: "" } catch (e: Exception) { "" }
            c.disconnect()
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
    fun game(id: Int): JSONObject = JSONObject(text("/v1/games/$id"))

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
            c.setRequestProperty("Content-Type", "application/octet-stream")
            c.outputStream.use { it.write(data) }
            val code = c.responseCode
            if (code != 200) throw HttpError(code, "zapis na serwerze: $code")
            c.inputStream.use { it.readBytes() }
        } finally { c.disconnect() }
    }

    fun nasDelete(p: String) {
        val c = URL(prefs.serverUrl + nasPath("file", p)).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = 8000
            c.readTimeout = 20000
            c.requestMethod = "DELETE"
            c.setRequestProperty("Authorization", "Bearer " + prefs.serverKey)
            val code = c.responseCode
            if (code != 200 && code != 404) throw HttpError(code, "usuwanie na serwerze: $code")
        } finally { c.disconnect() }
    }

    companion object {
        fun enc(s: String): String = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
    }
}
