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
 * ArmSX2 trzyma ustawienia i konto RetroAchievements we własnych ustawieniach aplikacji
 * (niedostępnych dla innych aplikacji) — EmuStart ich nie zmienia; konto loguje się raz
 * w ArmSX2. Ustawienia osobne dla gry ArmSX2 (od 2.8.2) stosuje też przy uruchomieniu
 * z EmuStart (rozpoznaje grę po numerze z płyty).
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
        /** Zawsze przy starcie z EmuStart: RetroArch zamyka się po zamknięciu gry (inaczej na
         *  Androidzie zostaje w tle z menu i nie wraca do EmuStart), a stan gry ma obrazek
         *  (podgląd przy wyborze zapisu w EmuStart). Pad jak na PC (emustart/emupolicy.py):
         *  każdy zapis stanu z nowym numerem, Start+Select — menu, Select — skróty,
         *  Select+RB — przyspieszenie, Select+Y — zapis stanu (kody przycisków Androida:
         *  Y=100, R1=103, SELECT=109). */
        val LAUNCH = linkedMapOf("quit_on_close_content" to "2", "savestate_thumbnail_enable" to "true",
            "savestate_auto_index" to "true", "input_menu_toggle_gamepad_combo" to "4",
            "input_enable_hotkey_btn" to "109", "input_toggle_fast_forward_btn" to "103",
            "input_save_state_btn" to "100")
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
            val out = body.filter { val k = keyOf(it); k.isNotEmpty() && !k.startsWith("cheevos_") && k !in AUTOSTATE && k !in LAUNCH }
                .toMutableList()
            for ((k, v) in LAUNCH) out += "$k = \"$v\""
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

    /** Bez profilu gracza: mały plik z ustawieniami startu (LAUNCH) i `#include` własnych
     *  ustawień RetroArcha — RetroArch zamyka się po grze także bez profilu. */
    fun retroarchBase(original: String): File? = try {
        val f = File(ext, "EmuStart/retroarch-start.cfg")
        f.parentFile?.mkdirs()
        val text = LAUNCH.map { (k, v) -> "$k = \"$v\"" }.joinToString("\n") + "\n#include \"$original\"\n"
        if (!f.isFile || f.readText() != text) f.writeText(text)
        f
    } catch (e: Exception) { Log.w(tag, "plik startowy RetroArcha: $e"); null }

    /** Po grze: ustawienia zmienione w RetroArchu → serwer (bez wartości telefonu i sekretów). */
    fun retroarchAfter(profile: String) {
        val f = overlay(profile)
        if (profile.isEmpty() || !f.isFile) return
        val text = lines(f).filter {
            val k = keyOf(it)
            k.isNotEmpty() && !MACHINE.matches(k) && !SECRET.matches(k) && !k.startsWith("cheevos_") && k !in LAUNCH
        }.joinToString("\n") + "\n"
        val key = "ra_cfg_sent:$profile"
        if (prefs.getString(key) == text.hashCode().toString()) return
        try {
            server.nasPut("$profile/$RA_NAS", text.toByteArray(), f.lastModified() / 1000.0)
            prefs.putString(key, text.hashCode().toString())
        } catch (e: Exception) { Log.w(tag, "ustawienia RetroArcha na serwer: $e") }
    }


}
