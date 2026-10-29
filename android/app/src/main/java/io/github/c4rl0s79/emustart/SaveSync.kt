package io.github.c4rl0s79.emustart

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

/**
 * Synchronizacja zapisów gry z serwerem EmuStart — paczkami, jak EmuStart na Windows
 * (emustart/syncclient.py; serwer: emustart/savesync.py). Serwer jest jedynym źródłem prawdy.
 *
 * Paczka = pełny zestaw plików gry dla emulatora (i rdzenia RetroArcha) jednego rodzaju
 * (saves | states | memcards), w paczce pod nazwami umownymi („game.srm”, „game.state3.png”,
 * „game_1.mcd”, „game.ps2”) — urządzenia nazywają pliki gry różnie.
 *
 *   zmiana tutaj         = skrót zawartości ≠ zapamiętany przy ostatniej synchronizacji
 *   zmiana gdzie indziej = revision na serwerze ≠ zapamiętana
 *   tylko tam → pobranie, tylko tu → wysłanie, tu i tam → konflikt (decyduje gracz)
 *
 * Skrót zawartości liczony tak samo jak na komputerze (pierwsza synchronizacja porównuje
 * pliki telefonu z paczką na serwerze bez zgadywania).
 */
class SaveSync(ctx: Context, private val server: Server) {
    private val tag = "EmuStart"
    private val root = File(ctx.filesDir, "zapisy").apply { mkdirs() }
    private val tmpDir = File(ctx.cacheDir, "zapisy-tmp").apply { mkdirs() }

    /** Paczka gry na tym telefonie: `files()` — pliki teraz (nazwa w paczce → plik),
     *  `target(arc)` — gdzie położyć plik pobrany z serwera (może być kilka miejsc). */
    class Bundle(val emu: String, val core: String, val kind: String,
                 val files: () -> Map<String, File>, val target: (String) -> List<File>,
                 val tag: JSONObject = JSONObject()) {
        val key: String get() = "$emu/${core.ifEmpty { "-" }}/$kind"
    }

    class Conflict(val bundle: Bundle, val server: JSONObject, val localTime: Long) {
        fun describe(): JSONObject = JSONObject().put("emulator", bundle.emu).put("core", bundle.core)
            .put("kind", bundle.kind).put("server_revision", server.optInt("revision"))
            .put("server_time", server.optDouble("updated_at", 0.0))
            .put("server_device", server.optString("updated_by_device_name").ifEmpty { server.optString("updated_by_device_id") })
            .put("local_time", localTime / 1000.0).put("local_files", bundle.files().size)
    }

    class Offline(msg: String) : Exception(msg)
    private class Rejected(val current: JSONObject) : Exception("konflikt")

    class Result {
        var offline = ""
        var downloaded = 0
        var uploaded = 0
        val conflicts = mutableListOf<Conflict>()
    }

    companion object {
        /** Stały identyfikator gry — jak savesync.game_id na serwerze. */
        fun gameId(es: String, name: String): String {
            val key = es.lowercase(Locale.ROOT) + "|" + name.replace(Regex("[\\s_]+"), " ").trim().lowercase(Locale.ROOT)
            return "g" + hex(MessageDigest.getInstance("SHA-1").digest(key.toByteArray())).take(20)
        }

        fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }

        fun fileSha(f: File): String {
            val md = MessageDigest.getInstance("SHA-256")
            f.inputStream().use { i ->
                val buf = ByteArray(1 shl 16)
                while (true) { val n = i.read(buf); if (n <= 0) break; md.update(buf, 0, n) }
            }
            return hex(md.digest())
        }

        /** Skrót zestawu: nazwy w paczce i treści (savebundles.content_hash); pusty — "". */
        fun contentHash(items: List<Pair<String, String>>): String {
            if (items.isEmpty()) return ""
            val md = MessageDigest.getInstance("SHA-256")
            for ((arc, sha) in items.sortedBy { it.first }) md.update((arc + "\u0000" + sha + "\n").toByteArray())
            return hex(md.digest())
        }

        /** Rdzeń jak na komputerze („bsnes_libretro_android” → „bsnes”). */
        fun coreId(core: String) = core.removeSuffix("_libretro_android").removeSuffix("_libretro").lowercase(Locale.ROOT)

        /** Zapisy w grze wspólne dla rdzeni o zgodnym formacie (surowy SRAM) — jak
         *  savebundles.SAVE_GROUPS na komputerze. Stany zostają per rdzeń. */
        val SAVE_GROUPS = mapOf(
            "sram-snes" to setOf("snes9x", "snes9x2002", "snes9x2005", "snes9x2005_plus", "snes9x2010", "bsnes",
                                 "bsnes_hd_beta", "bsnes2014_accuracy", "bsnes2014_balanced", "bsnes2014_performance",
                                 "bsnes_mercury_accuracy", "bsnes_mercury_balanced", "bsnes_mercury_performance",
                                 "bsnes_cplusplus98", "mesen-s"),
            "sram-nes" to setOf("fceumm", "nestopia", "mesen", "quicknes"))

        /** Rdzeń paczki zapisów w grze: grupa zgodnych rdzeni albo sam rdzeń. */
        fun saveCore(core: String): String {
            val c = core.lowercase(Locale.ROOT)
            return SAVE_GROUPS.entries.firstOrNull { c in it.value }?.key ?: c
        }
    }

    // ── stan lokalny (osobno dla każdego konta) ──
    private fun stateFile(account: String) = File(root, "sync-" + Saves.safe(account) + ".json")
    private fun readState(account: String): JSONObject = try { JSONObject(stateFile(account).readText()) } catch (e: Exception) { JSONObject() }
    private fun writeState(account: String, o: JSONObject) {
        val f = stateFile(account)
        val tmp = File(f.path + ".tmp")
        tmp.writeText(o.toString())
        if (!tmp.renameTo(f)) { f.writeText(o.toString()); tmp.delete() }
    }
    private fun skey(plat: String, gid: String, b: Bundle) = "$plat/$gid/${b.key}"

    private fun remember(account: String, plat: String, gid: String, b: Bundle, rev: Int, content: String, dirty: Boolean = false) {
        val o = readState(account)
        o.put(skey(plat, gid, b), JSONObject().put("rev", rev).put("content", content).put("dirty", dirty)
            .put("time", System.currentTimeMillis() / 1000.0).put("tag", b.tag))
        writeState(account, o)
    }

    private fun state(account: String, plat: String, gid: String, b: Bundle): JSONObject? =
        readState(account).optJSONObject(skey(plat, gid, b))

    fun status(account: String, es: String, name: String): JSONObject {
        val prefix = es.lowercase(Locale.ROOT) + "/" + gameId(es, name) + "/"
        val o = readState(account)
        val rows = o.keys().asSequence().filter { it.startsWith(prefix) }.map { o.getJSONObject(it) }.toList()
        val st = when {
            rows.isEmpty() -> "never"
            rows.any { it.optBoolean("dirty") } -> "pending"
            else -> "synced"
        }
        return JSONObject().put("state", st).put("last", rows.maxOfOrNull { it.optDouble("time", 0.0) } ?: 0.0)
    }

    private fun localHash(b: Bundle): String = contentHash(b.files().map { (arc, f) -> arc to fileSha(f) })
    private fun newest(b: Bundle): Long = b.files().values.maxOfOrNull { it.lastModified() } ?: 0L

    // ── serwer ──
    private fun json(account: String, method: String, path: String, body: JSONObject? = null): JSONObject {
        val r = try { server.sync(method, path, account, body) } catch (e: Exception) {
            throw Offline("brak połączenia z serwerem (${e.javaClass.simpleName})")
        }
        val o = try { JSONObject(String(r.body)) } catch (e: Exception) { JSONObject() }
        return when (r.code) {
            200 -> o
            409 -> throw Rejected(o.optJSONObject("current") ?: JSONObject())
            401, 403 -> throw Offline("serwer nie wydaje zapisów tego konta (${r.code})")
            404 -> throw Offline("serwer bez synchronizacji zapisów (starsza wersja)")
            else -> throw Offline("serwer odpowiedział ${r.code} ${o.optString("reason")}".trim())
        }
    }

    private fun serverBundle(man: JSONObject, b: Bundle): JSONObject {
        var e = man.optJSONObject("emulators")?.optJSONObject(b.emu) ?: return JSONObject()
        if (b.emu == "retroarch") e = e.optJSONObject("cores")?.optJSONObject(b.core) ?: return JSONObject()
        return e.optJSONObject("bundles")?.optJSONObject(b.kind) ?: JSONObject()
    }

    private fun pack(b: Bundle): File {
        val z = File.createTempFile("paczka-", ".zip", tmpDir)
        ZipOutputStream(z.outputStream()).use { out ->
            for ((arc, f) in b.files().toSortedMap()) {
                val e = ZipEntry(arc)
                e.time = f.lastModified()
                out.putNextEntry(e)
                f.inputStream().use { it.copyTo(out) }
                out.closeEntry()
            }
        }
        return z
    }

    private fun upload(account: String, plat: String, gid: String, b: Bundle, base: Int): Int {
        val z = pack(b)
        try {
            val sha = fileSha(z)
            val d = JSONObject().put("platform_id", plat).put("game_id", gid).put("emulator_id", b.emu)
                .put("core_id", b.core).put("bundle_kind", b.kind).put("base_revision", base)
                .put("sha256", sha).put("size", z.length())
            val init = json(account, "POST", "/v1/savesync/upload/init", d)
            val put = try { server.sync("PUT", init.getString("upload_url"), account, file = z) } catch (e: Exception) {
                throw Offline("wysyłanie paczki: ${e.javaClass.simpleName}")
            }
            if (put.code != 200) throw Offline("wysyłanie paczki: serwer odpowiedział ${put.code}")
            val r = json(account, "POST", "/v1/savesync/upload/commit", JSONObject().put("upload_id", init.getString("upload_id"))
                .put("base_revision", base).put("sha256", sha).put("size", z.length()))
            Log.i(tag, "zapisy → serwer: $plat/$gid ${b.key} rev ${r.optInt("revision")}")
            return r.getInt("revision")
        } finally { z.delete() }
    }

    private fun fetch(account: String, srv: JSONObject): File {
        val z = File.createTempFile("paczka-", ".zip", tmpDir)
        try {
            val r = try { server.sync("GET", srv.getString("download_url"), account, sink = z) } catch (e: Exception) {
                throw Offline("pobieranie paczki: ${e.javaClass.simpleName}")
            }
            if (r.code != 200) throw Offline("pobieranie paczki: serwer odpowiedział ${r.code}")
            val want = srv.optString("sha256")
            if (want.isNotEmpty() && fileSha(z) != want) throw Offline("pobrana paczka uszkodzona (SHA-256)")
            return z
        } catch (e: Exception) { z.delete(); throw e }
    }

    private fun zipHash(z: File): String = ZipFile(z).use { zf ->
        contentHash(zf.entries().asSequence().filter { !it.isDirectory }.map { e ->
            val md = MessageDigest.getInstance("SHA-256")
            zf.getInputStream(e).use { i ->
                val buf = ByteArray(1 shl 16)
                while (true) { val n = i.read(buf); if (n <= 0) break; md.update(buf, 0, n) }
            }
            e.name to hex(md.digest())
        }.toList())
    }

    private fun stamp() = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(Date())

    /** Pliki paczki z serwera zamiast bieżących: bieżące do kopii zapasowej (zapisy/kopie),
     *  każdy plik przez plik tymczasowy; pliki paczki, których na serwerze już nie ma, znikają. */
    private fun apply(b: Bundle, z: File) {
        val cur = b.files()
        if (cur.isNotEmpty()) {
            val bak = File(root, "kopie/" + stamp() + "-z-serwera/" + b.key.replace('/', '-'))
            bak.mkdirs()
            for (f in cur.values) try { f.copyTo(File(bak, f.name), true) } catch (e: Exception) { Log.w(tag, "kopia $f: $e") }
            File(root, "kopie").listFiles()?.sortedBy { it.name }?.dropLast(20)?.forEach { it.deleteRecursively() }
        }
        val written = HashSet<String>()
        ZipFile(z).use { zf ->
            for (e in zf.entries().asSequence().filter { !it.isDirectory }) {
                val arc = e.name
                // podfoldery tylko w paczkach, które je mają (zapisy PSP: „ULUS10297DATA00/DATA.BIN”)
                require(!arc.startsWith("/") && !arc.contains('\\') && arc.split('/').none { it.isEmpty() || it == ".." } &&
                        (!arc.contains('/') || b.target(arc).isNotEmpty())) { "zła nazwa w paczce: $arc" }
                val targets = cur[arc]?.let { listOf(it) } ?: b.target(arc)
                for (t in targets) {
                    t.parentFile?.mkdirs()
                    val tmp = File(t.path + ".emustart-new")
                    zf.getInputStream(e).use { i -> tmp.outputStream().use { o -> i.copyTo(o) } }
                    if (e.time > 0) tmp.setLastModified(e.time)
                    if (t.exists()) t.delete()
                    if (!tmp.renameTo(t)) { tmp.copyTo(t, true); tmp.delete() }
                    written += t.absolutePath
                }
            }
        }
        for (f in cur.values) if (f.absolutePath !in written) f.delete()
    }

    private fun download(account: String, plat: String, gid: String, b: Bundle, srv: JSONObject) {
        val z = fetch(account, srv)
        try {
            val h = zipHash(z)
            apply(b, z)
            remember(account, plat, gid, b, srv.optInt("revision"), h)
            Log.i(tag, "zapisy ← serwer: $plat/$gid ${b.key} rev ${srv.optInt("revision")}")
        } finally { z.delete() }
    }

    /** Numer slotu stanu („game.state3” → 3, „game.state” → 0; autozapis i miniatury → -1). */
    private fun slotNo(arc: String): Int {
        val m = Regex("^game\\.state(\\d*)(\\.auto)?$").find(arc) ?: return -1
        return if (m.groupValues[2].isNotEmpty()) -1 else m.groupValues[1].toIntOrNull() ?: 0
    }

    /** Stany zmienione tu i na innym urządzeniu: suma slotów (jak savebundles.merge_states na
     *  komputerze) — nic nie znika; ten sam slot z inną zawartością: nowszy zostaje, starszy
     *  dostaje następny wolny numer (z miniaturą). Wynik w telefonie i na serwerze. */
    private fun mergeStates(account: String, plat: String, gid: String, b: Bundle, srv: JSONObject, res: Result) {
        val z = fetch(account, srv)
        try {
            val serverContent = zipHash(z)
            val mine = b.files()
            val mineSha = mine.mapValues { fileSha(it.value) }
            ZipFile(z).use { zf ->
                val other = zf.entries().asSequence().filter { !it.isDirectory }.associateBy { it.name }
                fun otherSha(n: String): String {
                    val md = MessageDigest.getInstance("SHA-256")
                    zf.getInputStream(other.getValue(n)).use { i ->
                        val buf = ByteArray(1 shl 16)
                        while (true) { val k = i.read(buf); if (k <= 0) break; md.update(buf, 0, k) }
                    }
                    return hex(md.digest())
                }
                var next = ((mine.keys + other.keys).map { slotNo(it) } + 0).maxOrNull()!! + 1
                fun write(n: String, dst: File) {
                    dst.parentFile?.mkdirs()
                    val tmp = File(dst.path + ".emustart-new")
                    zf.getInputStream(other.getValue(n)).use { i -> tmp.outputStream().use { o -> i.copyTo(o) } }
                    val t = other.getValue(n).time
                    if (t > 0) tmp.setLastModified(t)
                    if (dst.exists()) dst.delete()
                    if (!tmp.renameTo(dst)) { tmp.copyTo(dst, true); tmp.delete() }
                }
                fun target(arc: String): File = mine[arc] ?: b.target(arc).firstOrNull() ?: File("")
                val taken = HashSet<String>()
                for (arc in other.keys.sorted()) {
                    if (arc.endsWith(".png")) continue
                    val png = "$arc.png"
                    val t = target(arc)
                    if (t.path.isEmpty()) continue
                    if (arc !in mine) {
                        write(arc, t)
                        if (png in other) write(png, File(t.path + ".png"))
                        continue
                    }
                    if (mineSha[arc] == otherSha(arc)) {
                        if (png in other && png !in mine) write(png, File(t.path + ".png"))
                        continue
                    }
                    val p2s = Regex("^(.+?)\\.(\\d{2}|resume|autosave(?:\\.\\d)?)\\.p2s$", RegexOption.IGNORE_CASE).find(arc)
                    val ppst = Regex("^([A-Z]{4}\\d{5})_\\d{1,2}\\.ppst$", RegexOption.IGNORE_CASE).find(arc)
                    val newArc = if (ppst != null) {
                        // PPSSPP: pierwszy wolny numer tej gry (jak na komputerze)
                        val pre = ppst.groupValues[1]
                        (0..99).map { "${pre}_$it.ppst" }.firstOrNull { it !in mine && it !in other && it !in taken }
                    } else if (p2s != null) {
                        // PCSX2 / ArmSX2: pierwszy wolny slot 00–09 tej gry
                        val pre = p2s.groupValues[1]
                        (0..9).map { "$pre.%02d.p2s".format(it) }.firstOrNull { it !in mine && it !in other && it !in taken }
                    } else "game.state${next++}"
                    if (newArc == null) {                      // wszystkie sloty zajęte — nowszy w slocie
                        if (other.getValue(arc).time > mine.getValue(arc).lastModified()) write(arc, t)
                        continue
                    }
                    taken += newArc
                    val newFile = b.target(newArc).firstOrNull() ?: continue
                    if (other.getValue(arc).time > mine.getValue(arc).lastModified()) {
                        // serwer nowszy: nasz stan na nowy slot, w slocie stan z serwera
                        mine.getValue(arc).renameTo(newFile)
                        mine[png]?.renameTo(File(newFile.path + ".png"))
                        write(arc, t)
                        if (png in other) write(png, File(t.path + ".png"))
                    } else {
                        write(arc, newFile)
                        if (png in other) write(png, File(newFile.path + ".png"))
                    }
                    Log.i(tag, "stany ${b.key}: slot $arc zajęty — starszy stan jako $newArc")
                }
            }
            res.downloaded++
            val local = localHash(b)
            if (local == serverContent) remember(account, plat, gid, b, srv.optInt("revision"), local)
            else push(account, plat, gid, b, srv.optInt("revision"), local, res)
        } finally { z.delete() }
    }

    private fun push(account: String, plat: String, gid: String, b: Bundle, base: Int, local: String, res: Result) {
        if (local.isEmpty()) return                     // pusty zestaw nie kasuje zapisów na serwerze
        try {
            val rev = upload(account, plat, gid, b, base)
            remember(account, plat, gid, b, rev, local)
            res.uploaded++
        } catch (e: Rejected) {
            res.conflicts += Conflict(b, e.current, newest(b))
        }
    }

    // ── przed grą / po grze ──
    /** `seeds` — paczki, których pliki w telefonie są tylko punktem wyjścia (karta PS2 innej
     *  gry): gdy serwer ma tę paczkę, wygrywa serwer bez pytania. */
    fun before(account: String, es: String, name: String, bundles: List<Bundle>, seeds: Set<String> = emptySet()): Result {
        val res = Result()
        if (bundles.isEmpty()) return res
        val plat = es.lowercase(Locale.ROOT)
        val gid = gameId(es, name)
        val man = try { json(account, "GET", "/v1/savesync/$plat/$gid/manifest") } catch (e: Offline) {
            res.offline = e.message ?: ""; Log.i(tag, "zapisy $plat/$gid bez synchronizacji: ${e.message}"); return res
        } catch (e: Rejected) { return res }
        for (b in bundles) {
            try { beforeOne(account, plat, gid, b, serverBundle(man, b), res, b.key in seeds) }
            catch (e: Offline) { res.offline = e.message ?: ""; Log.i(tag, "zapisy ${b.key}: ${e.message}") }
            catch (e: Exception) { res.offline = e.toString(); Log.w(tag, "zapisy ${b.key}: $e") }
        }
        return res
    }

    private fun beforeOne(account: String, plat: String, gid: String, b: Bundle, srv: JSONObject, res: Result,
                          seed: Boolean = false) {
        val revS = srv.optInt("revision")
        val local = localHash(b)
        val st = state(account, plat, gid, b)
        if (seed && revS > 0) {
            if (local != st?.optString("content") || revS != st?.optInt("rev")) { download(account, plat, gid, b, srv); res.downloaded++ }
            return
        }
        if (st == null) {
            when {
                revS == 0 -> push(account, plat, gid, b, 0, local, res)
                local.isEmpty() -> { download(account, plat, gid, b, srv); res.downloaded++ }
                else -> {
                    val z = fetch(account, srv)
                    val same = try { zipHash(z) == local } finally { z.delete() }
                    if (same) remember(account, plat, gid, b, revS, local)
                    else if (b.kind == "states") mergeStates(account, plat, gid, b, srv, res)     // stany się sumują
                    else res.conflicts += Conflict(b, srv, newest(b))
                }
            }
            return
        }
        val here = local != st.optString("content")
        val there = revS != st.optInt("rev")
        when {
            there && !here -> if (revS > 0) { download(account, plat, gid, b, srv); res.downloaded++ }
            here && !there -> push(account, plat, gid, b, revS, local, res)
            here && there -> if (b.kind == "states") mergeStates(account, plat, gid, b, srv, res)
                             else res.conflicts += Conflict(b, srv, newest(b))
            st.optBoolean("dirty") -> remember(account, plat, gid, b, revS, local)
        }
    }

    /** Metki paczek znanych na telefonie dla konta (gra, rodzina) — do pobrania po zalogowaniu. */
    fun known(account: String): List<JSONObject> {
        val o = readState(account)
        return o.keys().asSequence().mapNotNull { o.optJSONObject(it)?.optJSONObject("tag") }
            .filter { it.has("game") }.toList()
    }

    /** Po zalogowaniu i przy starcie: nowsze wersje paczek z serwera, gdy w telefonie bez zmian.
     *  Nic nie wysyła i nie rozstrzyga konfliktów (zostają do decyzji przy uruchomieniu gry). */
    fun pull(account: String, es: String, name: String, bundles: List<Bundle>): Result {
        val res = Result()
        if (bundles.isEmpty()) return res
        val plat = es.lowercase(Locale.ROOT)
        val gid = gameId(es, name)
        val man = try { json(account, "GET", "/v1/savesync/$plat/$gid/manifest") } catch (e: Offline) {
            res.offline = e.message ?: ""; return res
        } catch (e: Rejected) { return res }
        for (b in bundles) {
            try {
                val srv = serverBundle(man, b)
                val st = state(account, plat, gid, b) ?: continue
                val revS = srv.optInt("revision")
                if (revS == 0 || revS == st.optInt("rev")) continue
                if (localHash(b) != st.optString("content")) { res.conflicts += Conflict(b, srv, newest(b)); continue }
                download(account, plat, gid, b, srv)
                res.downloaded++
            } catch (e: Offline) { res.offline = e.message ?: ""; break }
            catch (e: Exception) { Log.w(tag, "zapisy ${b.key}: $e") }
        }
        return res
    }

    /** Decyzja gracza: local — wersja z telefonu zostaje (serwerowa w historii na serwerze),
     *  remote — pliki z serwera (bieżące do kopii zapasowej), skip — bez zmian. */
    fun resolve(account: String, es: String, name: String, conflicts: List<Conflict>, choice: String): Result {
        val res = Result()
        if (choice == "skip" || conflicts.isEmpty()) return res
        val plat = es.lowercase(Locale.ROOT)
        val gid = gameId(es, name)
        val man = json(account, "GET", "/v1/savesync/$plat/$gid/manifest")
        for (c in conflicts) {
            val srv = serverBundle(man, c.bundle)
            if (choice == "local") push(account, plat, gid, c.bundle, srv.optInt("revision"), localHash(c.bundle), res)
            else if (choice == "remote" && srv.has("download_url")) { download(account, plat, gid, c.bundle, srv); res.downloaded++ }
        }
        Log.i(tag, "zapisy $plat/$gid: konflikt rozstrzygnięty — $choice")
        return res
    }

    /** Stany tej gry z innych rdzeni RetroArcha (z serwera, rozpakowane do pamięci podręcznej) —
     *  w „Wczytaj zapis” z oznaczeniem, że w bieżącym rdzeniu się nie wczytają. (rdzeń, plik). */
    fun otherCoreStates(account: String, es: String, name: String, core: String): List<Pair<String, File>> {
        val plat = es.lowercase(Locale.ROOT)
        val gid = gameId(es, name)
        val man = try { json(account, "GET", "/v1/savesync/$plat/$gid/manifest") } catch (e: Exception) { return emptyList() }
        val cores = man.optJSONObject("emulators")?.optJSONObject("retroarch")?.optJSONObject("cores") ?: return emptyList()
        val out = ArrayList<Pair<String, File>>()
        for (c in cores.keys()) {
            val b = cores.optJSONObject(c)?.optJSONObject("bundles")?.optJSONObject("states") ?: continue
            if (c == core || !b.has("download_url")) continue
            val d = File(tmpDir.parentFile, "inne-rdzenie/$plat/$gid/$c")
            val mark = File(d, ".revision")
            val have = try { mark.readText().trim() } catch (e: Exception) { "" }
            if (have != b.optInt("revision").toString()) {
                val z = try { fetch(account, b) } catch (e: Exception) { Log.i(tag, "stany rdzenia $c: $e"); continue }
                try {
                    d.deleteRecursively(); d.mkdirs()
                    ZipFile(z).use { zf ->
                        for (e in zf.entries().asSequence().filter { !it.isDirectory }) {
                            if (e.name.contains('/') || e.name.contains('\\') || e.name.startsWith(".")) continue
                            val t = File(d, e.name)
                            zf.getInputStream(e).use { i -> t.outputStream().use { o -> i.copyTo(o) } }
                            if (e.time > 0) t.setLastModified(e.time)
                        }
                    }
                    mark.writeText(b.optInt("revision").toString())
                } finally { z.delete() }
            }
            d.listFiles()?.filter { it.name.startsWith("game.state") && !it.name.endsWith(".png") }?.forEach { out += c to it }
        }
        return out
    }

    /** Po grze: zmienione paczki na serwer; bez połączenia — oznaczone (wyślą się później). */
    fun after(account: String, es: String, name: String, bundles: List<Bundle>, skip: Set<String> = emptySet()): Result {
        val res = Result()
        val plat = es.lowercase(Locale.ROOT)
        val gid = gameId(es, name)
        for (b in bundles) {
            if (b.key in skip) continue
            val local = localHash(b)
            val st = state(account, plat, gid, b)
            if (local.isEmpty() || local == st?.optString("content")) continue
            try { push(account, plat, gid, b, st?.optInt("rev") ?: 0, local, res) }
            catch (e: Offline) {
                res.offline = e.message ?: ""
                remember(account, plat, gid, b, st?.optInt("rev") ?: 0, st?.optString("content") ?: "", true)
            }
        }
        if (res.offline.isNotEmpty()) throw Offline(res.offline)       // Saves: do kolejki zaległych
        return res
    }
}
