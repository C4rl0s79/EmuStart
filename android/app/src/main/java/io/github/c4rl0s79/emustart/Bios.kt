package io.github.c4rl0s79.emustart

import android.os.Environment
import android.util.Log
import java.io.File

/**
 * BIOS-y dla RetroArcha na telefonie — z serwera EmuStart (jego folder BIOS-ów),
 * jak na Windows (emustart/bios.py). Amiga (PUAE): ROM-y Kickstart do folderu
 * `system` RetroArcha (bez nich rdzeń bierze zamiennik AROS i WHDLoad kończy się
 * błędem) oraz do `…/WHDLoad/Devs/Kickstarts` (WHDLoad szuka ich tam sam).
 */
object Bios {
    private const val tag = "EmuStart"
    val KICKSTARTS = listOf("kick33180.A500", "kick34005.A500", "kick37175.A500", "kick40063.A600",
                            "kick40068.A1200", "kick40068.A4000")

    private val ext: File get() = Environment.getExternalStorageDirectory()

    /** Folder system RetroArcha (RetroArch na telefonie trzyma dane w /sdcard/RetroArch). */
    fun raSystem(): File = File(ext, "RetroArch/system")

    /** Foldery WHDLoad rdzenia PUAE (w zapisach RetroArcha, z podfolderem rdzenia albo bez). */
    private fun whdloadDirs(prefs: Prefs): List<File> {
        val saves = LinkedHashSet<File>()
        saves += File(ext, "RetroArch/saves")
        prefs.learnedDir("retroarch").takeIf { it.isNotBlank() }?.let { saves += File(it) }
        prefs.saveDir("retroarch").takeIf { it.isNotBlank() }?.let { saves += File(it) }
        val out = mutableListOf<File>()
        for (s in saves) for (sub in listOf("PUAE/WHDLoad", "WHDLoad"))
            File(s, sub).takeIf { it.isDirectory }?.let { out += it }
        return out
    }

    /** BIOS-y rdzenia RetroArcha (lista z pliku .info na serwerze), których brakuje w folderze
     *  system telefonu — z serwera. Zwraca liczbę pobranych. */
    fun forCore(server: Server, core: String): Int {
        if (core.isEmpty()) return 0
        val sys = raSystem()
        var n = 0
        for (rel in try { server.biosFor(core) } catch (e: Exception) { Log.w(tag, "BIOS $core: $e"); emptyList() }) {
            val dst = File(sys, rel)
            if (dst.isFile) continue
            try {
                dst.parentFile?.mkdirs()
                if (server.bios(rel, dst)) { n++; Log.i(tag, "BIOS z serwera: $rel ($core)") }
            } catch (e: Exception) { Log.w(tag, "BIOS $rel: $e") }
        }
        return n
    }

    /** Amiga: Kickstarty z serwera i do WHDLoad; zwraca opis, gdy żadnego nie ma (gra pójdzie na AROS). */
    fun amiga(server: Server, prefs: Prefs): String {
        val sys = raSystem()
        var have = 0
        for (name in KICKSTARTS) {
            val dst = File(sys, name)
            if (!dst.isFile) {
                try {
                    sys.mkdirs()
                    if (server.bios(name, dst)) Log.i(tag, "Kickstart z serwera: $name")
                } catch (e: Exception) { Log.w(tag, "Kickstart $name: $e") }
            }
            if (dst.isFile) have++
        }
        // WHDLoad (gry i dema .lha): te same ROM-y w devs:kickstarts
        for (w in whdloadDirs(prefs)) {
            val ks = File(w, "Devs/Kickstarts")
            for (name in KICKSTARTS) {
                val src = File(sys, name)
                val dst = File(ks, name)
                if (src.isFile && !dst.isFile) try {
                    ks.mkdirs()
                    src.copyTo(dst)
                } catch (e: Exception) { Log.w(tag, "WHDLoad $name: $e") }
            }
        }
        return if (have == 0) "Brak ROM-ów Kickstart (np. kick40068.A1200) w folderze BIOS-ów serwera — " +
            "rdzeń użyje zamiennika AROS, część gier i WHDLoad nie zadziała." else ""
    }
}
