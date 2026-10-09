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

    companion object {
        fun enc(s: String): String = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
    }
}
