package io.github.c4rl0s79.emustart

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Environment
import androidx.core.content.FileProvider
import java.io.File

/**
 * Emulatory na Androidzie i sposób przekazania im gry (intencje jak w ES-DE).
 * `uri` = content:// z FileProvidera z prawem odczytu dla emulatora; RetroArch dostaje
 * ścieżkę pliku (ma dostęp do pamięci).
 */
object Emulators {

    enum class Pass { RETROARCH, DATA_VIEW, DATA, EXTRA_BOOTPATH, EXTRA_AUTOSTART, MELONDS, EDEN }

    data class Emu(val id: String, val label: String, val components: List<String>, val pass: Pass)

    private val ALL = listOf(
        Emu("retroarch", "RetroArch", listOf(
            "com.retroarch.aarch64/com.retroarch.browser.retroactivity.RetroActivityFuture",
            "com.retroarch/com.retroarch.browser.retroactivity.RetroActivityFuture",
            "com.retroarch.ra32/com.retroarch.browser.retroactivity.RetroActivityFuture"), Pass.RETROARCH),
        Emu("duckstation", "DuckStation", listOf(
            "com.github.stenzek.duckstation/com.github.stenzek.duckstation.EmulationActivity"), Pass.EXTRA_BOOTPATH),
        Emu("armsx2", "ArmSX2", listOf(
            "come.nanodata.armsx2/com.armsx2.MainActivity",
            "com.armsx2/com.armsx2.MainActivity",
            "com.armsx2.nightly/com.armsx2.MainActivity",
            "come.nanodata.armsx2/kr.co.iefriends.pcsx2.MainActivity"), Pass.DATA_VIEW),
        Emu("nethersx2", "NetherSX2", listOf(
            "xyz.aethersx2.android/xyz.aethersx2.android.EmulationActivity"), Pass.EXTRA_BOOTPATH),
        Emu("ppsspp", "PPSSPP", listOf(
            "org.ppsspp.ppssppgold/org.ppsspp.ppsspp.PpssppActivity",
            "org.ppsspp.ppsspp/org.ppsspp.ppsspp.PpssppActivity"), Pass.DATA_VIEW),
        Emu("dolphin", "Dolphin", listOf(
            "org.dolphinemu.dolphinemu/org.dolphinemu.dolphinemu.ui.main.TvMainActivity"), Pass.EXTRA_AUTOSTART),
        Emu("azahar", "Azahar", listOf(
            "org.azahar_emu.azahar/org.citra.citra_emu.activities.EmulationActivity",
            "io.github.lime3ds.android/org.citra.citra_emu.activities.EmulationActivity"), Pass.DATA),
        Emu("melonds", "melonDS", listOf(
            "me.magnum.melonds/me.magnum.melonds.ui.emulator.EmulatorActivity"), Pass.MELONDS),
        Emu("drastic", "DraStic", listOf(
            "com.dsemu.drastic/com.dsemu.drastic.DraSticActivity"), Pass.DATA),
        Emu("flycast", "Flycast", listOf(
            "com.flycast.emulator/com.flycast.emulator.MainActivity"), Pass.DATA_VIEW),
        Emu("redream", "Redream", listOf(
            "io.recompiled.redream/io.recompiled.redream.MainActivity"), Pass.DATA_VIEW),
        Emu("m64fz", "M64Plus FZ", listOf(
            "org.mupen64plusae.v3.fzurita.pro/paulscode.android.mupen64plusae.SplashActivity",
            "org.mupen64plusae.v3.fzurita/paulscode.android.mupen64plusae.SplashActivity"), Pass.DATA_VIEW),
        Emu("eden", "Eden", listOf(
            "dev.eden.eden_emulator/org.yuzu.yuzu_emu.activities.EmulationActivity"), Pass.EDEN),
    )
    private val BY_ID = ALL.associateBy { it.id }

    /** Samodzielne emulatory dla platformy (kody jak na Windows), w kolejności preferencji. */
    private val STANDALONE = mapOf(
        "PS2" to listOf("armsx2", "nethersx2"),
        "PS1" to listOf("duckstation"),
        "PSP" to listOf("ppsspp"),
        "GCN" to listOf("dolphin"), "WII" to listOf("dolphin"),
        "3DS" to listOf("azahar"),
        "NDS" to listOf("melonds", "drastic"),
        "DC" to listOf("flycast", "redream"), "NAOMI" to listOf("flycast"),
        "N64" to listOf("m64fz"),
        "NSW" to listOf("eden"),
    )
    /** Rdzenie RetroArcha na Androidzie, gdy serwer nie podał (albo podał rdzeń bez wersji na Androida). */
    private val CORE_ANDROID = mapOf("PS1" to "swanstation", "PSP" to "ppsspp", "NDS" to "melonds",
                                     "N64" to "mupen64plus_next_gles3", "DC" to "flycast", "SATURN" to "yabasanshiro")

    private fun installed(ctx: Context, comp: String): Boolean = try {
        ctx.packageManager.getPackageInfo(comp.substringBefore('/'), 0); true
    } catch (e: PackageManager.NameNotFoundException) { false }

    fun component(ctx: Context, emu: Emu): String? = emu.components.firstOrNull { installed(ctx, it) }

    /** Zainstalowane emulatory dla systemu: [(id, opis)]. */
    fun options(ctx: Context, plat: String, core: String): List<Pair<String, String>> {
        val out = mutableListOf<Pair<String, String>>()
        for (id in STANDALONE[plat] ?: emptyList()) {
            val e = BY_ID[id] ?: continue
            if (component(ctx, e) != null) out += Pair(id, e.label)
        }
        val c = retroCore(plat, core)
        if (c.isNotEmpty() && component(ctx, BY_ID.getValue("retroarch")) != null) {
            out += Pair("retroarch", "RetroArch: $c")
        }
        return out
    }

    fun retroCore(plat: String, core: String): String = when {
        plat in CORE_ANDROID && (core.isEmpty() || core == "swanstation" || core == "ppsspp") -> CORE_ANDROID.getValue(plat)
        else -> core
    }

    fun label(id: String): String = BY_ID[id]?.label ?: id

    /** Intencja uruchamiająca grę `file` w emulatorze `id`. */
    fun intent(ctx: Context, id: String, file: File, plat: String, core: String): Intent {
        val emu = BY_ID[id] ?: throw IllegalStateException("nieznany emulator $id")
        val comp = component(ctx, emu) ?: throw IllegalStateException("${emu.label} nie jest zainstalowany")
        val pkg = comp.substringBefore('/')
        val i = Intent()
        i.component = ComponentName.unflattenFromString(comp)
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        if (emu.pass == Pass.RETROARCH) {
            val c = retroCore(plat, core)
            i.action = Intent.ACTION_MAIN
            i.putExtra("ROM", file.absolutePath)
            i.putExtra("LIBRETRO", "/data/data/$pkg/cores/${c}_libretro_android.so")
            i.putExtra("CONFIGFILE", "${Environment.getExternalStorageDirectory()}/Android/data/$pkg/files/retroarch.cfg")
            return i
        }
        // gra wielu plików (.cue/.m3u/.gdi): emulator musi widzieć pliki obok — ścieżka zamiast URI
        val multi = file.extension.lowercase() in setOf("cue", "m3u", "gdi", "ccd")
        val uri: Uri = FileProvider.getUriForFile(ctx, ctx.packageName + ".files", file)
        ctx.grantUriPermission(pkg, uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        val ref: String = if (multi) file.absolutePath else uri.toString()
        when (emu.pass) {
            Pass.DATA_VIEW -> { i.action = Intent.ACTION_VIEW; i.setDataAndType(uri, "application/octet-stream") }
            Pass.DATA -> i.data = uri
            Pass.EXTRA_BOOTPATH -> { i.action = Intent.ACTION_MAIN; i.putExtra("bootPath", ref); i.putExtra("resumeState", false) }
            Pass.EXTRA_AUTOSTART -> {
                i.action = Intent.ACTION_MAIN
                i.addCategory("android.intent.category.LEANBACK_LAUNCHER")
                i.putExtra("AutoStartFile", ref)
            }
            Pass.MELONDS -> { i.action = "me.magnum.melonds.LAUNCH_ROM"; i.putExtra("uri", uri.toString()) }
            Pass.EDEN -> { i.action = "android.nfc.action.TECH_DISCOVERED"; i.data = uri }
            Pass.RETROARCH -> {}
        }
        return i
    }
}
