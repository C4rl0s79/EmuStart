package io.github.c4rl0s79.emustart

import android.content.Context
import android.os.Build
import android.os.Environment
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Zapisy gier: folder emulatora w telefonie ↔ folder profilu na serwerze EmuStart
 * (ten sam, z którego korzysta EmuStart na Windows: <profil>/save/<emulator>/…).
 *
 * Synchronizowane są tylko pliki uruchamianej gry (RetroArch: pliki o nazwie gry,
 * DuckStation: karty pamięci gry, ArmSX2/AetherSX2: karty Mcd00x.ps2). Przed grą —
 * nowsze z serwera do telefonu, po grze — zmienione z telefonu na serwer (poprzednia
 * wersja zostaje w kopii zapasowej na serwerze). Zmiany wykrywamy po rozmiarze i czasie
 * zapamiętanym przy ostatniej synchronizacji osobno dla telefonu i serwera, więc różnica
 * zegarów nie ma znaczenia. Zmiana po obu stronach — konflikt: zostaje nowszy, drugi
 * do kopii zapasowej.
 *
 * Profile: pliki w folderze emulatora należą do jednego profilu naraz (owners.json);
 * przed grą innego profilu pliki poprzedniego trafiają do schowka aplikacji
 * (zapisy/park/<profil>/…) i wracają, gdy tamten znów gra.
 */
class Saves(private val ctx: Context, private val server: Server, private val prefs: Prefs) {
    private val tag = "EmuStart"
    private val root = File(ctx.filesDir, "zapisy").apply { mkdirs() }
    val device: String = "Telefon " + Build.MODEL

    enum class Kind { BY_STEM, PS1_CARDS, PS2_CARDS }
    /** Emulator: id (ustawienia, manifest), folder na serwerze, sposób dobierania plików,
     *  nazwa folderu danych w pamięci telefonu (wykrywanie). */
    data class Fam(val id: String, val label: String, val nas: String, val kind: Kind, val dirName: Regex)

    /** Gra: nazwa (jak na serwerze), nazwa pliku bez rozszerzenia, folder gry w telefonie, rdzeń RetroArcha. */
    data class Game(val es: String, val name: String, val stem: String, val dir: File, val core: String) {
        fun toJson(): JSONObject = JSONObject().put("es", es).put("name", name).put("stem", stem)
            .put("dir", dir.absolutePath).put("core", core)
        companion object {
            fun of(o: JSONObject) = Game(o.getString("es"), o.getString("name"), o.getString("stem"),
                                         File(o.getString("dir")), o.optString("core"))
        }
    }

    class Locked(val host: String) : Exception("Profil gra teraz na: $host")

    companion object {
        private fun rx(s: String) = Regex(s, RegexOption.IGNORE_CASE)
        // ArmSX2 i AetherSX2 mają osobne foldery w telefonie, na serwerze — wspólne karty PCSX2
        val FAMS = listOf(
            Fam("retroarch", "RetroArch", "save/retroarch/saves", Kind.BY_STEM, rx("^retroarch$")),
            Fam("duckstation", "DuckStation", "save/duckstation/memcards", Kind.PS1_CARDS, rx("duck")),
            Fam("armsx2", "ArmSX2", "save/pcsx2/memcards", Kind.PS2_CARDS, rx("armsx2")),
            Fam("nethersx2", "AetherSX2 / NetherSX2", "save/pcsx2/memcards", Kind.PS2_CARDS, rx("aether|nether")))

        fun famFor(emu: String): Fam? = FAMS.firstOrNull { it.id == emu.substringBefore('@') }

        /** Pliki zapisów rdzeni RetroArcha (nie sama gra, nie stany, nie zrzuty). */
        private val SAVE_EXT = rx("""\.(srm|sav|rtc|eep|fla|mpk|sra|nv|mcr|bkr|bcr|brm|dsv|ram)$""")
        private const val LOCK_MAX_AGE = 12 * 3600.0

        /** Nazwy folderów rdzeni (RetroArch sortuje save'y wg nazwy rdzenia), gdy różnią się od identyfikatora. */
        private val CORE_DIR = mapOf(
            "mednafen_saturn" to "Beetle Saturn", "mednafen_psx_hw" to "Beetle PSX HW", "mednafen_psx" to "Beetle PSX",
            "mednafen_pce" to "Beetle PCE", "mednafen_pce_fast" to "Beetle PCE Fast", "mednafen_wswan" to "Beetle WonderSwan",
            "mednafen_ngp" to "Beetle NeoPop", "mednafen_vb" to "Beetle VB", "fbneo" to "FinalBurn Neo",
            "genesis_plus_gx" to "Genesis Plus GX", "mgba" to "mGBA", "swanstation" to "SwanStation",
            "mupen64plus_next" to "Mupen64Plus-Next", "mupen64plus_next_gles3" to "Mupen64Plus-Next",
            "melonds" to "melonDS", "melondsds" to "melonDS DS", "desmume" to "DeSmuME", "fceumm" to "FCEUmm",
            "gambatte" to "Gambatte", "sameboy" to "SameBoy", "gearboy" to "Gearboy", "stella" to "Stella",
            "yabasanshiro" to "YabaSanshiro", "ppsspp" to "PPSSPP", "puae" to "PUAE", "picodrive" to "PicoDrive",
            "snes9x" to "Snes9x", "bsnes" to "bsnes", "nestopia" to "Nestopia", "mesen" to "Mesen", "opera" to "Opera",
            "flycast" to "Flycast", "mame" to "MAME", "vice_x64" to "VICE x64", "dosbox_pure" to "DOSBox-pure",
            "pcsx_rearmed" to "PCSX-ReARMed", "parallel_n64" to "ParaLLEl N64", "handy" to "Handy", "prosystem" to "ProSystem")

        fun safe(s: String) = s.replace(Regex("""[<>:"/\\|?*\x00-\x1f]+"""), "_").trim(' ', '.').ifEmpty { "profil" }
    }

    // ── stan w plikach aplikacji ──
    private fun jsonFile(name: String) = File(root, name)
    private fun readJson(name: String): JSONObject = try {
        JSONObject(jsonFile(name).readText())
    } catch (e: Exception) { JSONObject() }
    private fun writeJson(name: String, o: JSONObject) {
        val f = jsonFile(name)
        val tmp = File(f.path + ".tmp")
        tmp.writeText(o.toString())
        if (!tmp.renameTo(f)) { f.writeText(o.toString()); tmp.delete() }
    }

    private val lock = Any()
    @Volatile var busy = ""            // tytuł gry, której zapisy właśnie idą na serwer
    val conflicts = java.util.Collections.synchronizedList(mutableListOf<JSONObject>())

    // ── foldery emulatorów w telefonie ──
    private val ext: File get() = Environment.getExternalStorageDirectory()

    private fun topDirs(): List<File> =
        ext.listFiles()?.filter { it.isDirectory && it.name != "Android" && !it.name.startsWith(".") } ?: emptyList()

    /** Foldery zapisów emulatora: ustawiony ręcznie, wykryty po grze, typowe miejsca. */
    fun phoneDirs(f: Fam, g: Game?): List<File> {
        val out = LinkedHashSet<File>()
        prefs.saveDir(f.id).takeIf { it.isNotBlank() }?.let { out += File(it) }
        prefs.learnedDir(f.id).takeIf { it.isNotBlank() }?.let { out += File(it) }
        when (f.kind) {
            Kind.BY_STEM -> {
                out += File(ext, "RetroArch/saves")
                if (g != null) out += g.dir                       // „zapis obok gry” (domyślne w części wersji)
            }
            else -> topDirs().filter { f.dirName.containsMatchIn(it.name) }.forEach { out += File(it, "memcards") }
        }
        return out.filter { it.isDirectory }
    }

    /** Opis dla Ustawień: gdzie telefon szuka zapisów emulatora. */
    fun describe(f: Fam): String {
        val dirs = phoneDirs(f, null)
        return when {
            prefs.saveDir(f.id).isNotBlank() -> prefs.saveDir(f.id) + (if (dirs.isEmpty()) " (nie ma takiego folderu)" else "")
            prefs.learnedDir(f.id).isNotBlank() && dirs.isNotEmpty() -> prefs.learnedDir(f.id) + " (wykryty)"
            dirs.isNotEmpty() -> dirs.joinToString(", ") { it.absolutePath }
            f.kind == Kind.BY_STEM -> "wykryje się po pierwszej grze"
            else -> "nie znaleziono — w emulatorze ustaw folder danych w pamięci telefonu (nie Android/data)"
        }
    }

    private fun matches(f: Fam, g: Game, name: String): Boolean = when (f.kind) {
        Kind.BY_STEM -> name.startsWith(g.stem + ".") && SAVE_EXT.containsMatchIn(name)
        Kind.PS1_CARDS -> name.endsWith(".mcd", true) && (name.startsWith(g.name + "_") || name.startsWith("shared_card", true))
        Kind.PS2_CARDS -> name.endsWith(".ps2", true)
    }

    /** Pliki gry w telefonie: nazwa pliku → plik (gdy w kilku miejscach — najnowszy). */
    private fun phoneFiles(f: Fam, g: Game, dirs: List<File>): MutableMap<String, File> {
        val out = HashMap<String, File>()
        fun take(x: File) {
            if (x.isFile && matches(f, g, x.name)) {
                val cur = out[x.name]
                if (cur == null || x.lastModified() > cur.lastModified()) out[x.name] = x
            }
        }
        for (d in dirs) {
            for (x in d.listFiles() ?: emptyArray()) {
                if (x.isDirectory && f.kind == Kind.BY_STEM && d != g.dir) x.listFiles()?.forEach { take(it) }
                else take(x)
            }
        }
        return out
    }

    // ── manifest: stan plików z ostatniej synchronizacji (osobno telefon i serwer) ──
    private fun manName(profile: String) = "stan-" + safe(profile) + ".json"
    private fun sig(size: Long, sec: Double) = JSONArray().put(size).put(Math.round(sec))
    private fun sig(x: File) = sig(x.length(), x.lastModified() / 1000.0)
    private fun same(a: JSONArray?, b: JSONArray?): Boolean =
        a != null && b != null && a.optLong(0) == b.optLong(0) && Math.abs(a.optLong(1) - b.optLong(1)) <= 2

    private fun stamp() = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(Date())

    private fun backupLocal(x: File, why: String) {
        try {
            val d = File(root, "kopie/" + stamp() + "-" + why)
            d.mkdirs()
            x.copyTo(File(d, x.name), true)
            val all = File(root, "kopie").listFiles()?.sortedBy { it.name } ?: return
            for (old in all.dropLast(20)) old.deleteRecursively()
        } catch (e: Exception) { Log.w(tag, "kopia $x: $e") }
    }

    // ── profile na serwerze ──
    fun profiles(): List<String> = server.nasList("").filter { it.second && !it.first.startsWith(".") && !it.first.startsWith("_") }
        .map { it.first }.sortedBy { it.lowercase() }

    fun createProfile(name: String): String {
        val n = safe(name.trim().take(32))
        if (profiles().any { it.equals(n, true) }) throw IllegalArgumentException("Profil „$n” już istnieje.")
        server.nasPut("$n/emustart.json", "{}".toByteArray(), System.currentTimeMillis() / 1000.0)
        return n
    }

    // ── blokada: ten sam profil nie gra na dwóch urządzeniach naraz ──
    private fun lockProfile(profile: String) {
        val raw = server.nasRead("$profile/lock")
        if (raw != null) {
            val cur = try { JSONObject(String(raw)) } catch (e: Exception) { JSONObject() }
            val host = cur.optString("host")
            if (host.isNotEmpty() && host != device && System.currentTimeMillis() / 1000.0 - cur.optDouble("time", 0.0) < LOCK_MAX_AGE)
                throw Locked(host)
        }
        server.nasPut("$profile/lock", JSONObject().put("host", device).put("time", System.currentTimeMillis() / 1000.0)
            .toString().toByteArray(), System.currentTimeMillis() / 1000.0)
    }

    private fun unlockProfile(profile: String) {
        try {
            val raw = server.nasRead("$profile/lock") ?: return
            if (JSONObject(String(raw)).optString("host") == device) server.nasDelete("$profile/lock")
        } catch (e: Exception) { Log.w(tag, "blokada: $e") }
    }

    // ── właściciel plików w folderze emulatora ──
    private fun swapOwner(profile: String, f: Fam, g: Game, phone: MutableMap<String, File>) {
        val owners = readJson("owners.json")
        val parked = readJson("park.json")              // plik w schowku → ścieżka w folderze emulatora
        val first = prefs.phoneOwner.ifEmpty { profile }
        for ((name, x) in phone.entries.toList()) {
            val owner = owners.optString(x.absolutePath).ifEmpty { first }
            if (owner == profile) continue
            // zapis innego profilu: najpierw na serwer (gdyby nie doszedł), potem do schowka
            try { pushFile(owner, f, g, name, x) } catch (e: Exception) { Log.w(tag, "zapis profilu $owner: $e") }
            val dst = File(root, "park/" + safe(owner) + "/" + f.id + "/" + Bridge.sha1(x.absolutePath) + "/" + x.name)
            dst.parentFile?.mkdirs()
            if (!x.renameTo(dst)) { x.copyTo(dst, true); x.delete() }
            parked.put(dst.absolutePath, x.absolutePath)
            owners.remove(x.absolutePath)
            phone.remove(name)
        }
        // pliki tego profilu ze schowka wracają na miejsce
        val mine = File(root, "park/" + safe(profile) + "/" + f.id)
        for (p in mine.walkTopDown().filter { it.isFile }.toList()) {
            if (!matches(f, g, p.name)) continue
            val orig = parked.optString(p.absolutePath)
            if (orig.isEmpty()) continue
            val back = File(orig)
            if (back.exists()) continue
            back.parentFile?.mkdirs()
            if (!p.renameTo(back)) { p.copyTo(back, true); p.delete() }
            parked.remove(p.absolutePath)
            owners.put(back.absolutePath, profile)
            phone[back.name] = back
        }
        writeJson("owners.json", owners)
        writeJson("park.json", parked)
    }

    private fun own(profile: String, files: Collection<File>) {
        val owners = readJson("owners.json")
        for (x in files) owners.put(x.absolutePath, profile)
        writeJson("owners.json", owners)
    }

    // ── przed grą ──
    /** Zapisy profilu do folderu emulatora. Zwraca komunikat dla użytkownika ('' = OK). */
    fun before(profile: String, emu: String, g: Game): String {
        synchronized(lock) { return beforeLocked(profile, emu, g) }
    }

    private fun beforeLocked(profile: String, emu: String, g: Game): String {
        val f = famFor(emu) ?: return ""
        if (prefs.phoneOwner.isEmpty()) prefs.phoneOwner = profile
        retryLocked()
        val dirs = phoneDirs(f, g)
        if (dirs.isEmpty() && f.kind != Kind.BY_STEM)
            return "Nie znaleziono folderu kart pamięci ${f.label} — zapisy zostaną tylko w telefonie (Ustawienia → Zapisy gier)."
        val phone = phoneFiles(f, g, dirs)
        swapOwner(profile, f, g, phone)
        try {
            lockProfile(profile)
        } catch (e: Locked) { throw e } catch (e: Exception) {
            own(profile, phone.values)
            return "Serwer niedostępny — gra na zapisach z telefonu; wyślą się później."
        }
        val man = readJson(manName(profile))
        val nas = try { server.nasScan("$profile/${f.nas}") } catch (e: Exception) {
            own(profile, phone.values)
            return "Serwer niedostępny — gra na zapisach z telefonu; wyślą się później."
        }
        val byName = HashMap<String, String>()                 // nazwa pliku → ścieżka na serwerze (najnowsza)
        for ((rel, v) in nas) {
            val n = rel.substringAfterLast('/')
            if (!matches(f, g, n)) continue
            val cur = byName[n]
            if (cur == null || v[1] > nas.getValue(cur)[1]) byName[n] = rel
        }
        var n = 0
        for ((name, rel) in byName) {
            val v = nas.getValue(rel)
            val nSig = sig(v[0].toLong(), v[1])
            val key = "${f.id}/$name"
            val m = man.optJSONObject(key)
            val x = phone[name]
            val download = when {
                x == null -> true
                m == null -> if (same(nSig, sig(x))) false else v[1] > x.lastModified() / 1000.0 + 2
                same(m.optJSONArray("n"), nSig) -> false                         // serwer bez zmian
                same(m.optJSONArray("p"), sig(x)) -> true                         // zmienił się tylko serwer
                else -> {                                                         // oba zmienione — konflikt
                    val nasWins = v[1] > x.lastModified() / 1000.0 + 2
                    conflicts += JSONObject().put("profile", profile).put("file", "$name (zostaje ${if (nasWins) "wersja z serwera" else "wersja z telefonu"})")
                    nasWins
                }
            }
            if (!download) {
                if (x != null && m == null && same(nSig, sig(x)))
                    man.put(key, JSONObject().put("n", nSig).put("p", sig(x)).put("rel", rel))
                continue
            }
            val targets = if (x != null) listOf(x) else newTargets(f, g, dirs, rel)
            for (t in targets) {
                if (t.exists()) backupLocal(t, "przed-pobraniem")
                if (server.nasGet("$profile/${f.nas}/$rel", t)) n++
            }
            targets.firstOrNull()?.let { man.put(key, JSONObject().put("n", nSig).put("p", sig(it)).put("rel", rel)) }
            targets.forEach { phone[name] = it }
        }
        writeJson(manName(profile), man)
        own(profile, phone.values)
        if (n > 0) Log.i(tag, "zapisy z serwera: $n plików (${f.id}, $profile)")
        return ""
    }

    /** Gdzie położyć zapis, którego w telefonie jeszcze nie ma. */
    private fun newTargets(f: Fam, g: Game, dirs: List<File>, rel: String): List<File> {
        val name = rel.substringAfterLast('/')
        if (f.kind != Kind.BY_STEM) return listOfNotNull(dirs.firstOrNull()?.let { File(it, name) })
        val learned = prefs.learnedDir(f.id).ifEmpty { prefs.saveDir(f.id) }
        val pick = if (learned.isNotEmpty() && File(learned).isDirectory) listOf(File(learned)) else dirs
        return pick.map { d ->
            // RetroArch z sortowaniem wg rdzenia: ten sam podfolder co na serwerze
            val sub = rel.substringBeforeLast('/', "")
            val sorted = d != g.dir && (d.listFiles()?.any { it.isDirectory } == true)
            if (sub.isNotEmpty() && sorted) File(File(d, sub), name) else File(d, name)
        }
    }

    // ── po grze ──
    fun after(profile: String, emu: String, g: Game, startedAt: Long, title: String) {
        val f = famFor(emu) ?: return
        busy = title
        try {
            synchronized(lock) {
                learn(f, g, startedAt)
                try {
                    push(profile, f, g)
                    removePending(profile, emu, g)
                } catch (e: Exception) {
                    Log.w(tag, "wysyłanie zapisów: $e")
                    addPending(profile, emu, g)
                }
                try { unlockProfile(profile) } catch (e: Exception) { }
            }
        } finally { busy = "" }
    }

    /** Po grze: gdzie emulator zapisał (folder z plikiem gry zmienionym w trakcie gry). */
    private fun learn(f: Fam, g: Game, startedAt: Long) {
        val cands = LinkedHashSet<File>()
        cands += phoneDirs(f, g)
        if (f.kind != Kind.BY_STEM) topDirs().forEach { cands += File(it, "memcards") }
        for (d in cands.filter { it.isDirectory }) {
            val hit = phoneFiles(f, g, listOf(d)).values.firstOrNull { it.lastModified() >= startedAt - 2000 } ?: continue
            val dir = if (f.kind == Kind.BY_STEM && hit.parentFile != d) d else hit.parentFile!!
            if (prefs.learnedDir(f.id) != dir.absolutePath) {
                prefs.setLearnedDir(f.id, dir.absolutePath)
                Log.i(tag, "folder zapisów ${f.id}: $dir")
            }
            return
        }
    }

    private fun push(profile: String, f: Fam, g: Game) {
        val dirs = phoneDirs(f, g)
        val phone = phoneFiles(f, g, dirs)
        if (phone.isEmpty()) return
        val man = readJson(manName(profile))
        val nas = server.nasScan("$profile/${f.nas}")
        var n = 0
        for ((name, x) in phone) {
            if (pushFile(profile, f, g, name, x, man, nas)) n++
        }
        writeJson(manName(profile), man)
        own(profile, phone.values)
        if (n > 0) Log.i(tag, "zapisy na serwer: $n plików (${f.id}, $profile)")
    }

    private fun pushFile(profile: String, f: Fam, g: Game, name: String, x: File,
                         man0: JSONObject? = null, nas0: Map<String, DoubleArray>? = null): Boolean {
        val man = man0 ?: readJson(manName(profile))
        val nas = nas0 ?: server.nasScan("$profile/${f.nas}")
        val key = "${f.id}/$name"
        val m = man.optJSONObject(key)
        val pSig = sig(x)
        if (m != null && same(m.optJSONArray("p"), pSig)) return false            // bez zmian w telefonie
        val rel = m?.optString("rel")?.takeIf { it.isNotEmpty() && nas.containsKey(it) }
            ?: nas.keys.firstOrNull { it.substringAfterLast('/') == name }
            ?: newRel(f, g, x, nas)
        val v = nas[rel]
        val nSig = v?.let { sig(it[0].toLong(), it[1]) }
        val bak = "$profile/_backup/${stamp()}-${safe(device)}/${f.nas}"
        if (v != null && m != null && !same(m.optJSONArray("n"), nSig) && v[1] > x.lastModified() / 1000.0 + 2) {
            // serwer zmieniony w międzyczasie i nowszy: zostaje; wersja z telefonu do kopii zapasowej
            conflicts += JSONObject().put("profile", profile).put("file", "$name (zostaje wersja z serwera, telefonu w kopii zapasowej)")
            server.nasPut("$bak/konflikt/$rel", x.readBytes(), x.lastModified() / 1000.0)
            backupLocal(x, "konflikt")
            server.nasGet("$profile/${f.nas}/$rel", x)
            man.put(key, JSONObject().put("n", nSig).put("p", sig(x)).put("rel", rel))
            if (man0 == null) writeJson(manName(profile), man)
            return false
        }
        if (v != null && same(nSig, pSig)) {
            man.put(key, JSONObject().put("n", nSig).put("p", pSig).put("rel", rel))
            if (man0 == null) writeJson(manName(profile), man)
            return false
        }
        val sec = x.lastModified() / 1000.0
        server.nasPut("$profile/${f.nas}/$rel", x.readBytes(), sec, if (v != null) "$bak/$rel" else "")
        man.put(key, JSONObject().put("n", sig(x.length(), sec)).put("p", pSig).put("rel", rel))
        if (man0 == null) writeJson(manName(profile), man)
        return true
    }

    /** Ścieżka na serwerze dla nowego zapisu (RetroArch: podfolder rdzenia, gdy serwer je ma). */
    private fun newRel(f: Fam, g: Game, x: File, nas: Map<String, DoubleArray>): String {
        if (f.kind != Kind.BY_STEM) return x.name
        val parent = x.parentFile
        val learned = prefs.learnedDir(f.id)
        if (parent != null && learned.isNotEmpty() && parent.parentFile?.absolutePath == learned) return parent.name + "/" + x.name
        val sorted = nas.keys.any { it.contains('/') }
        if (!sorted || g.core.isEmpty()) return x.name
        val core = g.core.removeSuffix("_libretro_android").removeSuffix("_libretro")
        val dir = CORE_DIR[core] ?: nas.keys.map { it.substringBefore('/') }.firstOrNull {
            it.replace(" ", "").replace("-", "").equals(core.replace("_", ""), true)
        } ?: core
        return "$dir/${x.name}"
    }

    // ── kolejka: zapisy, które nie doszły (brak połączenia) ──
    private fun pendKey(profile: String, emu: String, g: Game) = "$profile|$emu|${g.es}|${g.name}"

    private fun addPending(profile: String, emu: String, g: Game) {
        val o = readJson("do-wyslania.json")
        o.put(pendKey(profile, emu, g), JSONObject().put("profile", profile).put("emu", emu).put("game", g.toJson()))
        writeJson("do-wyslania.json", o)
    }

    private fun removePending(profile: String, emu: String, g: Game) {
        val o = readJson("do-wyslania.json")
        if (o.has(pendKey(profile, emu, g))) { o.remove(pendKey(profile, emu, g)); writeJson("do-wyslania.json", o) }
    }

    fun pendingCount(): Int = readJson("do-wyslania.json").length()

    /** Zaległe wysyłki (przy starcie aplikacji i przed grą). */
    fun retryPending(): Int {
        synchronized(lock) { return retryLocked() }
    }

    private fun retryLocked(): Int {
        val o = readJson("do-wyslania.json")
        var done = 0
        for (k in o.keys().asSequence().toList()) {
            val e = o.getJSONObject(k)
            val f = famFor(e.getString("emu")) ?: continue
            try {
                push(e.getString("profile"), f, Game.of(e.getJSONObject("game")))
                o.remove(k); done++
            } catch (ex: Exception) { Log.w(tag, "zaległe zapisy: $ex"); break }
        }
        if (done > 0) writeJson("do-wyslania.json", o)
        return done
    }
}
