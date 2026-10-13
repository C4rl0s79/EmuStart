package io.github.c4rl0s79.emustart

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Instalacja emulatorów na telefonie: APK z oficjalnego wydania (GitHub, buildbot RetroArcha,
 * ppsspp.org) → systemowy instalator (gracz potwierdza; potrzebna zgoda „Instalowanie
 * nieznanych aplikacji” dla EmuStart). Emulatory dostępne tylko w sklepie — strona Google Play.
 * Adres najnowszego APK ustalany przy instalacji; gdy się nie da — strona wydania w przeglądarce.
 *
 * Rdzeni RetroArcha nie da się zainstalować z zewnątrz (prywatny folder RetroArcha) — pobiera
 * je sam RetroArch: Online Updater → Core Downloader.
 */
class Installer(private val act: MainActivity) {
    private val tag = "EmuStart"

    sealed class Src {
        data class GitHub(val repo: String, val asset: Regex = Regex("(?i)\\.apk$")) : Src()
        object RetroArch : Src()
        object Ppsspp : Src()
        data class Play(val pkg: String) : Src()
    }

    companion object {
        val SOURCES: Map<String, Src> = mapOf(
            "retroarch" to Src.RetroArch,
            "armsx2" to Src.GitHub("ARMSX2/ARMSX2"),
            "melonds" to Src.GitHub("rafaelvcaetano/melonDS-android"),
            "azahar" to Src.GitHub("azahar-emu/azahar", Regex("(?i)android.*\\.apk$|\\.apk$")),
            "flycast" to Src.GitHub("flyinghead/flycast"),
            "ppsspp" to Src.Ppsspp,
            "duckstation" to Src.Play("com.github.stenzek.duckstation"),
            "dolphin" to Src.Play("org.dolphinemu.dolphinemu"),
            "drastic" to Src.Play("com.dsemu.drastic"),
            "m64fz" to Src.Play("org.mupen64plusae.v3.fzurita"),
            "redream" to Src.Play("io.recompiled.redream"),
        )

        fun describe(id: String): String = when (val s = SOURCES[id]) {
            is Src.GitHub -> "APK z GitHuba (${s.repo})"
            Src.RetroArch -> "APK z buildbot.libretro.com"
            Src.Ppsspp -> "APK z ppsspp.org"
            is Src.Play -> "Google Play"
            null -> ""
        }
    }

    // ── stan dla UI (jak install_status na Windows) ──
    @Volatile var running = false
    @Volatile var step = 0
    @Volatile var steps = 0
    @Volatile var current = ""
    @Volatile var done = 0L
    @Volatile var total = 0L
    val errors = mutableListOf<String>()
    val installed = mutableListOf<String>()
    val cancel = AtomicBoolean(false)
    /** Do otwarcia po kolei (instalator / sklep) — po powrocie z jednego następny. */
    private val queue = ArrayDeque<Intent>()

    fun status(): JSONObject = JSONObject().put("running", running).put("step", step).put("steps", steps)
        .put("current", current).put("done", done).put("total", total)
        .put("errors", JSONArray(errors)).put("installed", JSONArray(installed))
        .put("cancelled", cancel.get()).put("android_pending", queue.isNotEmpty() || !running && installed.isNotEmpty())

    /** Pobiera APK emulatorów `ids` (po kolei), potem otwiera instalatory. */
    fun start(ids: List<String>, labels: Map<String, String>) {
        if (running) return
        running = true
        cancel.set(false)
        errors.clear(); installed.clear(); queue.clear()
        steps = ids.size
        Thread {
            try {
                ids.forEachIndexed { i, id ->
                    if (cancel.get()) return@forEachIndexed
                    step = i + 1
                    val label = labels[id] ?: id
                    current = label
                    done = 0; total = 0
                    try {
                        queue += prepare(id, label)
                        installed += label
                    } catch (e: Exception) {
                        Log.w(tag, "instalacja $id: $e")
                        errors += "$label: ${e.message ?: e.javaClass.simpleName}"
                        fallbackPage(id)?.let { queue += it }
                    }
                }
            } finally {
                running = false
                act.runOnUiThread { next() }
            }
        }.start()
    }

    /** Aktualizacja samego EmuStart: APK z wydania na GitHubie → systemowy instalator. */
    fun startSelf(url: String, label: String) {
        if (running) return
        running = true
        cancel.set(false)
        errors.clear(); installed.clear(); queue.clear()
        steps = 1; step = 1; current = label; done = 0; total = 0
        Thread {
            try {
                queue += install(download(url, "emustart"))
                installed += label
            } catch (e: Exception) {
                Log.w(tag, "aktualizacja EmuStart: $e")
                errors += "$label: ${e.message ?: e.javaClass.simpleName}"
            } finally {
                running = false
                act.runOnUiThread { next() }
            }
        }.start()
    }

    /** Najnowsze wydanie repozytorium (API GitHuba). */
    fun latestRelease(repo: String): JSONObject = json("https://api.github.com/repos/$repo/releases/latest")

    /** Następny instalator z kolejki (po powrocie do EmuStart). */
    fun next() {
        if (running || queue.isEmpty()) return
        if (Build.VERSION.SDK_INT >= 26 && !act.packageManager.canRequestPackageInstalls()
            && queue.first().type == APK) {
            // zgoda na instalowanie z EmuStart — po powrocie kolejka rusza dalej
            try {
                act.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + act.packageName)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                return
            } catch (e: Exception) { Log.w(tag, "zgoda na instalację: $e") }
        }
        val i = queue.removeFirst()
        try { act.startActivity(i) } catch (e: ActivityNotFoundException) {
            errors += "Nie udało się otworzyć: ${i.data}"
        }
    }

    private val APK = "application/vnd.android.package-archive"

    private fun prepare(id: String, label: String): Intent = when (val s = SOURCES[id]) {
        is Src.Play -> Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=${s.pkg}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        is Src.GitHub -> install(download(githubApk(s), id))
        Src.RetroArch -> install(download(retroarchApk(), id))
        Src.Ppsspp -> install(download(ppssppApk(), id))
        null -> throw IllegalStateException("brak źródła instalacji")
    }

    private fun fallbackPage(id: String): Intent? {
        val url = when (val s = SOURCES[id]) {
            is Src.GitHub -> "https://github.com/${s.repo}/releases/latest"
            Src.RetroArch -> "https://www.retroarch.com/?page=platforms"
            Src.Ppsspp -> "https://www.ppsspp.org/download/"
            is Src.Play -> "https://play.google.com/store/apps/details?id=${s.pkg}"
            null -> return null
        }
        return Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    private fun install(apk: File): Intent {
        val uri = FileProvider.getUriForFile(act, act.packageName + ".files", apk)
        return Intent(Intent.ACTION_VIEW).setDataAndType(uri, APK)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    private fun json(url: String): JSONObject {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 10000; c.readTimeout = 20000
        c.setRequestProperty("Accept", "application/vnd.github+json")
        c.setRequestProperty("User-Agent", "EmuStart-Android")
        try {
            if (c.responseCode != 200) throw IllegalStateException("${URL(url).host}: ${c.responseCode}")
            return JSONObject(c.inputStream.bufferedReader().readText())
        } finally { c.disconnect() }
    }

    private fun latestTag(repo: String): String =
        json("https://api.github.com/repos/$repo/releases/latest").getString("tag_name").removePrefix("v")

    /** APK z najnowszego wydania: ARM64 / uniwersalny, bez wersji x86 i debug. */
    private fun githubApk(s: Src.GitHub): String {
        val a = json("https://api.github.com/repos/${s.repo}/releases/latest").optJSONArray("assets") ?: JSONArray()
        val apks = (0 until a.length()).map { a.getJSONObject(it) }
            .filter { s.asset.containsMatchIn(it.getString("name")) }
            .filter { !Regex("(?i)x86|debug|armeabi-v7a|arm32").containsMatchIn(it.getString("name")) }
        // wydania z wersjami pod poziom Androida (np. ArmSX2: a11-…-sdk30, a13-…-sdk33, a15-…-sdk35,
        // legacy-…-sdk26): najwyższy zgodny z tym telefonem, bez „legacy”; wcześniej brany był
        // pierwszy plik z listy (wersja dla Androida 11)
        val sdk = android.os.Build.VERSION.SDK_INT
        fun sdkOf(n: String) = Regex("""(?i)sdk(\d+)""").find(n)?.groupValues?.get(1)?.toIntOrNull()
        val bySdk = apks.filter { a -> sdkOf(a.getString("name"))?.let { it <= sdk } == true }
            .sortedWith(compareBy<JSONObject>({ Regex("(?i)legacy").containsMatchIn(it.getString("name")) },
                                              { -(sdkOf(it.getString("name")) ?: 0) }))
        val pick = bySdk.firstOrNull()
            ?: apks.firstOrNull { Regex("(?i)arm64|aarch64|v8a").containsMatchIn(it.getString("name")) }
            ?: apks.firstOrNull { Regex("(?i)universal").containsMatchIn(it.getString("name")) } ?: apks.firstOrNull()
            ?: throw IllegalStateException("w najnowszym wydaniu ${s.repo} nie ma pliku APK")
        return pick.getString("browser_download_url")
    }

    private fun retroarchApk(): String {
        val v = latestTag("libretro/RetroArch")
        return "https://buildbot.libretro.com/stable/$v/android/RetroArch_aarch64.apk"
    }

    private fun ppssppApk(): String {
        val v = latestTag("hrydgard/ppsspp")
        return "https://www.ppsspp.org/files/${v.replace('.', '_')}/ppsspp.apk"
    }

    private fun download(url: String, id: String): File {
        val dir = File(act.getExternalFilesDir(null), "apk").apply { mkdirs() }
        val dst = File(dir, "$id.apk")
        val tmp = File(dir, "$id.apk.part")
        var u = URL(url)
        var c: HttpURLConnection
        var hops = 0
        while (true) {                               // przekierowania (GitHub → serwer plików)
            c = u.openConnection() as HttpURLConnection
            c.instanceFollowRedirects = false
            c.connectTimeout = 15000; c.readTimeout = 60000
            c.setRequestProperty("User-Agent", "EmuStart-Android")
            val code = c.responseCode
            if (code in 300..399 && hops++ < 6) {
                u = URL(u, c.getHeaderField("Location")); c.disconnect(); continue
            }
            if (code != 200) { c.disconnect(); throw IllegalStateException("${u.host}: $code") }
            break
        }
        try {
            total = c.contentLengthLong.coerceAtLeast(0)
            c.inputStream.use { i -> tmp.outputStream().use { o ->
                val buf = ByteArray(1 shl 16)
                while (true) {
                    if (cancel.get()) throw InterruptedException("przerwano")
                    val n = i.read(buf)
                    if (n < 0) break
                    o.write(buf, 0, n); done += n
                }
            } }
        } finally { c.disconnect() }
        if (!tmp.renameTo(dst)) { dst.delete(); tmp.renameTo(dst) }
        return dst
    }
}
