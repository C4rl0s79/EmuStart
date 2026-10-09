package io.github.c4rl0s79.emustart

import java.io.File
import java.util.zip.ZipFile

/**
 * Przygotowanie pobranej gry dla emulatora — te same reguły co na Windows
 * (emustart/launcher.py: _prepare, _extract):
 *  - ZIP z kilkoma plikami (MSU-1: ROM + .msu + ścieżki .pcm; Amiga na kilku dyskietkach)
 *    albo dla emulatora, który ZIP-ów nie czyta — rozpakowany w całości obok gry,
 *    uruchamiany jest ROM (dyskietki: playlista .m3u),
 *  - gra na kilku płytach — playlista .m3u (zmiana płyty w emulatorze),
 *  - playlista .m3u dla emulatora, który jej nie obsługuje — pierwsza płyta.
 */
object Prepare {
    private val DISK_EXTS = setOf("ipf", "adf", "adz", "dms", "fdi", "d64", "st", "msa", "dsk")
    /** Emulatory, które same czytają ZIP z pojedynczym ROM-em. */
    private val ZIP_NATIVE = setOf("retroarch", "melonds", "drastic", "ppsspp")
    /** Emulatory obsługujące playlisty .m3u. */
    private val M3U = setOf("retroarch", "duckstation")

    fun zipNative(emu: String) = Emulators.baseId(emu) in ZIP_NATIVE
    fun supportsM3u(emu: String) = Emulators.baseId(emu) in M3U

    class Result(val rom: File, val extra: List<File>)   // extra — utworzone pliki/foldery (do sprzątania z grą)

    /**
     * @param base   folder systemu w telefonie (pliki gry względem niego)
     * @param main   główny plik gry
     * @param files  pliki gry (ścieżki względne)
     * @param name   nazwa gry (jak na serwerze — od niej playlisty, jak na Windows)
     * @param kind   rodzaj systemu (arcade: ZIP zostaje — to zestaw ROM-ów)
     * @param exts   rozszerzenia gier systemu
     */
    fun prepare(emu: String, base: File, main: File, files: List<String>, name: String,
                multidisc: Boolean, kind: String, exts: Set<String>): Result {
        val extra = mutableListOf<File>()
        var rom = main
        if (main.extension.equals("zip", true) && kind != "arcade" && (!zipNative(emu) || zipMulti(main))) {
            val (r, dir) = extract(main, exts, supportsM3u(emu))
            rom = r
            extra += dir
        }
        if (multidisc && supportsM3u(emu)) {
            val dir = File(base, ".emustart")
            dir.mkdirs()
            val m3u = File(dir, "$name.m3u")
            m3u.writeText(files.joinToString("\n") { File(base, it).absolutePath } + "\n")
            extra += m3u
            return Result(m3u, extra)
        }
        if (rom.extension.equals("m3u", true) && !supportsM3u(emu)) {
            val disc = files.drop(1).firstOrNull { !it.lowercase().endsWith(".bin") && !it.lowercase().endsWith(".raw") }
            if (disc != null) rom = File(base, disc)
        }
        return Result(rom, extra)
    }

    private fun zipMulti(f: File): Boolean = try {
        ZipFile(f).use { z -> z.entries().asSequence().count { !it.isDirectory } > 1 }
    } catch (e: Exception) { false }

    private fun diskOrder(f: File): Pair<Int, String> {
        val m = Regex("""\((?:Disk|Disc|Side)\s*(\d+)""", RegexOption.IGNORE_CASE).find(f.name)
        return Pair(m?.groupValues?.get(1)?.toInt() ?: 0, f.name.lowercase())
    }

    /** Rozpakowanie obok gry (raz — ponownie tylko, gdy archiwum się zmieniło). */
    private fun extract(zip: File, exts: Set<String>, m3uOk: Boolean): Pair<File, File> {
        val dir = File(zip.parentFile, ".emustart/" + zip.nameWithoutExtension)
        val mark = File(dir, ".zrodlo")
        val sig = "${zip.length()}:${zip.lastModified()}"
        val names = mutableListOf<String>()
        if (!(mark.isFile && mark.readText() == sig)) {
            dir.deleteRecursively()
            dir.mkdirs()
            ZipFile(zip).use { z ->
                for (e in z.entries()) {
                    if (e.isDirectory) continue
                    val out = File(dir, File(e.name).name)          // bez podfolderów z archiwum
                    z.getInputStream(e).use { i -> out.outputStream().use { o -> i.copyTo(o, 1 shl 20) } }
                }
            }
            mark.writeText(sig)
        }
        val files = dir.listFiles()?.filter { it.isFile && it.name != ".zrodlo" && !it.name.endsWith(".m3u") } ?: emptyList()
        if (files.isEmpty()) throw IllegalStateException("Archiwum ZIP jest puste.")
        val wanted = exts - setOf("zip", "7z")
        val pick = files.filter { it.extension.lowercase() in wanted }
        if (pick.isEmpty() && files.size > 1)
            throw IllegalStateException("W archiwum nie ma pliku gry — są tylko pliki dodatkowe (np. muzyka MSU-1).")
        val disks = pick.filter { it.extension.lowercase() in DISK_EXTS }.sortedWith(compareBy({ diskOrder(it).first }, { diskOrder(it).second }))
        if (m3uOk && disks.size > 1) {
            val m3u = File(dir, zip.nameWithoutExtension + ".m3u")
            m3u.writeText(disks.joinToString("\n") { it.name } + "\n")
            return Pair(m3u, dir)
        }
        if (disks.isNotEmpty()) return Pair(disks[0], dir)
        return Pair((pick.ifEmpty { files }).maxByOrNull { it.length() }!!, dir)
    }
}
