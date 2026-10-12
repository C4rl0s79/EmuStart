package io.github.c4rl0s79.emustart

import android.os.Environment
import android.util.Log
import org.json.JSONObject
import java.io.File

/**
 * Ustawienia emulatorów należące do profilu gracza na telefonie — osobno od komputera,
 * wspólne dla telefonów tego profilu (serwer: `<profil>/settings/android/<emulator>/`).
 *
 * RetroArch: własny `retroarch.cfg` RetroArcha leży w Android/data (niedostępne dla innych
 * aplikacji), więc RetroArch dostaje plik profilu: na górze ustawienia gracza i jego konto
 * RetroAchievements, na końcu `#include` oryginału. Wpisy z dołączonego pliku RetroArch
 * traktuje jako tylko do odczytu — przy wyjściu zapisuje do pliku profilu wyłącznie to,
 * co gracz zmienił (z powrotem z `#include`), więc oryginał zostaje nietknięty.
 *
 * ArmSX2: konto RetroAchievements w `inis/PCSX2.ini` i `inis/secrets.ini` folderu danych
 * (tego samego, w którym leżą karty pamięci) — jak PCSX2 na komputerze.
 */
class EmuProfile(private val server: Server, private val prefs: Prefs) {
    private val tag = "EmuStart"
    private val ext: File get() = Environment.getExternalStorageDirectory()

    companion object {
        const val RA_NAS = "settings/android/retroarch/retroarch.cfg"
        /** Wartości zależne od telefonu — zostają z tego telefonu, nie idą na serwer. */
        private val MACHINE = Regex("""^(.*_directory|.*_path|.*_dir|video_driver|audio_driver|audio_device|""" +
            """input_joypad_driver|input_driver|camera_driver|location_driver|menu_driver|libretro_.*)$""")
        private val SECRET = Regex("""^(cheevos_token|cheevos_password|.*_api_key|.*_auth_token|netplay_password|netplay_spectate_password)$""")
        private val AUTOSTATE = setOf("savestate_auto_save", "savestate_auto_load")
        private val KEY = Regex("""^\s*([A-Za-z0-9_]+)\s*=""")
    }

    private fun safe(s: String) = s.replace(Regex("""[<>:"/\\|?*\x00-\x1f]+"""), "_").trim(' ', '.').ifEmpty { "profil" }

    fun overlay(profile: String): File = File(ext, "EmuStart/profiles/${safe(profile)}/retroarch.cfg")

    /** Konto RA profilu z serwera: null — profil nie wybrał (zostaje logowanie z emulatora). */
    private fun account(profile: String): JSONObject? = try {
        server.nasRead("$profile/retroachievements.json")?.let { JSONObject(String(it)) }
    } catch (e: Exception) { Log.w(tag, "konto RA: $e"); null }

    private fun lines(f: File): List<String> = try { if (f.isFile) f.readLines() else emptyList() } catch (e: Exception) { emptyList() }
    private fun keyOf(line: String): String = KEY.find(line)?.groupValues?.get(1) ?: ""

    /** Przed grą w RetroArchu: plik profilu (nowszy z serwera, konto RA, `#include` oryginału). */
    fun retroarchBefore(profile: String, original: String): File? {
        if (profile.isEmpty()) return null
        val f = overlay(profile)
        try {
            f.parentFile?.mkdirs()
            val local = lines(f)
            // nowsza wersja z innego telefonu tego profilu: jej ustawienia + wartości tego telefonu
            var body = local
            try {
                val nas = server.nasScan("$profile/settings/android/retroarch")
                val v = nas["retroarch.cfg"]
                if (v != null && (!f.isFile || v[1] > f.lastModified() / 1000.0 + 2)) {
                    val remote = server.nasRead("$profile/$RA_NAS")?.let { String(it).lines() }
                    if (remote != null) {
                        val mine = local.filter { MACHINE.matches(keyOf(it)) }
                        body = mine + remote.filter { keyOf(it).isNotEmpty() && !MACHINE.matches(keyOf(it)) }
                    }
                }
            } catch (e: Exception) { Log.w(tag, "ustawienia RetroArcha z serwera: $e") }
            val out = body.filter { val k = keyOf(it); k.isNotEmpty() && !k.startsWith("cheevos_") && k !in AUTOSTATE }
                .toMutableList()
            // autozapis wspólny dla urządzeń: zapis przy wyjściu, wczytanie przy starcie (jak na komputerze)
            out += "savestate_auto_save = \"true\""
            out += "savestate_auto_load = \"true\""
            val acc = account(profile)
            val keep = local.filter { keyOf(it).startsWith("cheevos_") }
            if (acc == null) out += keep                                      // bez wyboru — jak było
            else {
                val on = acc.optString("user").isNotEmpty() && acc.optString("token").isNotEmpty()
                out += "cheevos_enable = \"${if (on) "true" else "false"}\""
                out += "cheevos_username = \"${if (on) acc.optString("user") else ""}\""
                out += "cheevos_token = \"${if (on) acc.optString("token") else ""}\""
                out += "cheevos_password = \"\""
                out += "cheevos_hardcore_mode_enable = \"${if (on && acc.optBoolean("hardcore")) "true" else "false"}\""
            }
            out += "#include \"$original\""
            val text = out.joinToString("\n") + "\n"
            if (!f.isFile || f.readText() != text) f.writeText(text)
            return f
        } catch (e: Exception) {
            Log.w(tag, "plik ustawień profilu RetroArcha: $e")
            return null
        }
    }

    /** Po grze: ustawienia zmienione w RetroArchu → serwer (bez wartości telefonu i sekretów). */
    fun retroarchAfter(profile: String) {
        val f = overlay(profile)
        if (profile.isEmpty() || !f.isFile) return
        val text = lines(f).filter {
            val k = keyOf(it)
            k.isNotEmpty() && !MACHINE.matches(k) && !SECRET.matches(k) && !k.startsWith("cheevos_")
        }.joinToString("\n") + "\n"
        val key = "ra_cfg_sent:$profile"
        if (prefs.getString(key) == text.hashCode().toString()) return
        try {
            server.nasPut("$profile/$RA_NAS", text.toByteArray(), f.lastModified() / 1000.0)
            prefs.putString(key, text.hashCode().toString())
        } catch (e: Exception) { Log.w(tag, "ustawienia RetroArcha na serwer: $e") }
    }

    /** ArmSX2: konto RA profilu w PCSX2.ini / secrets.ini folderu danych (obok kart pamięci). */
    fun armsx2(profile: String, memcardsDir: String) {
        if (profile.isEmpty() || memcardsDir.isEmpty()) return
        val acc = account(profile) ?: return
        val inis = File(File(memcardsDir).parentFile ?: return, "inis")
        if (!inis.isDirectory) return
        val on = acc.optString("user").isNotEmpty() && acc.optString("token").isNotEmpty()
        try {
            iniSet(File(inis, "PCSX2.ini"), "Achievements", mapOf(
                "Enabled" to (if (on) "true" else "false"),
                "Username" to (if (on) acc.optString("user") else ""),
                "LoginTimestamp" to (if (on) (System.currentTimeMillis() / 1000).toString() else "0"),
                "ChallengeMode" to (if (on && acc.optBoolean("hardcore")) "true" else "false")))
            iniSet(File(inis, "secrets.ini"), "Achievements", mapOf("Token" to (if (on) acc.optString("token") else "")))
        } catch (e: Exception) { Log.w(tag, "konto RA w ArmSX2: $e") }
    }

    /** Zmienia / dopisuje klucze w sekcji pliku ini (resztę pliku zostawia bez zmian). */
    private fun iniSet(f: File, section: String, values: Map<String, String>) {
        val src = if (f.isFile) f.readLines() else emptyList()
        val out = mutableListOf<String>()
        val todo = values.toMutableMap()
        var cur = ""
        var lastInSection = -1
        for (line in src) {
            val sec = Regex("""^\s*\[(.+?)]\s*$""").find(line)?.groupValues?.get(1)
            if (sec != null) {
                cur = sec
                out += line
                if (cur == section) lastInSection = out.size - 1
                continue
            }
            val k = Regex("""^\s*([^=;#]+?)\s*=""").find(line)?.groupValues?.get(1)
            if (cur == section && k != null && k in todo) {
                out += "$k = ${todo.remove(k)}"
            } else out += line
            if (cur == section && line.isNotBlank()) lastInSection = out.size - 1
        }
        if (todo.isNotEmpty()) {
            val add = todo.map { "${it.key} = ${it.value}" }
            if (lastInSection >= 0) out.addAll(lastInSection + 1, add)
            else { if (out.isNotEmpty()) out += ""; out += "[$section]"; out += add }
        }
        f.parentFile?.mkdirs()
        f.writeText(out.joinToString("\n") + "\n")
    }
}
