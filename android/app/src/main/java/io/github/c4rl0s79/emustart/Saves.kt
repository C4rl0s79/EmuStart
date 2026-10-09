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

    enum class Kind { BY_STEM, PS1_SRM, PS1_CARDS, PS2_CARDS, RA_STATES, PS2_STATES }
    /** Emulator: id (ustawienia, manifest), folder na serwerze, sposób dobierania plików,
     *  nazwa folderu danych w pamięci telefonu (wykrywanie). */
    data class Fam(val id: String, val label: String, val nas: String, val kind: Kind, val dirName: Regex,
                   val dirs: String = id, val cards: String = "") {
        /** Pliki w folderach RetroArcha (dobierane po nazwie pliku gry). */
        val ra: Boolean get() = kind == Kind.BY_STEM || kind == Kind.PS1_SRM || kind == Kind.RA_STATES
        /** Stany gry: na serwerze osobno dla telefonu (states/android/…), nie wspólne z PC. */
        val states: Boolean get() = kind == Kind.RA_STATES || kind == Kind.PS2_STATES
        /** Podfolder danych emulatora PS1/PS2 (karty / stany). */
        val sub: String get() = if (kind == Kind.PS2_STATES) "sstates" else "memcards"
    }

    /** Gra: nazwa (jak na serwerze), nazwa pliku bez rozszerzenia, folder gry w telefonie, rdzeń RetroArcha. */
    data class Game(val es: String, val name: String, val stem: String, val dir: File, val core: String,
                    val plat: String = "") {
        fun toJson(): JSONObject = JSONObject().put("es", es).put("name", name).put("stem", stem)
            .put("dir", dir.absolutePath).put("core", core).put("plat", plat)
        companion object {
            fun of(o: JSONObject) = Game(o.getString("es"), o.getString("name"), o.getString("stem"),
                                         File(o.getString("dir")), o.optString("core"), o.optString("plat"))
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
            Fam("nethersx2", "AetherSX2 / NetherSX2", "save/pcsx2/memcards", Kind.PS2_CARDS, rx("aether|nether")),
            // stany gry z telefonu — na serwerze, ale osobno od PC (inne wersje emulatorów ich nie wczytują)
            Fam("retroarch_states", "RetroArch — stany gry", "states/android/retroarch", Kind.RA_STATES, rx("^retroarch$")),
            Fam("armsx2_states", "ArmSX2 — stany gry", "states/android/armsx2", Kind.PS2_STATES, rx("armsx2"), cards = "armsx2"),
            Fam("nethersx2_states", "AetherSX2 — stany gry", "states/android/nethersx2", Kind.PS2_STATES,
                rx("aether|nether"), cards = "nethersx2"))

        private fun byId(id: String): Fam? = if (id == RA_PS1.id) RA_PS1 else FAMS.firstOrNull { it.id == id }

        /** Zapisy i stany emulatora: [zapisy, stany]. */
        fun famsFor(emu: String, plat: String = ""): List<Fam> {
            val id = Emulators.baseId(emu)
            val save = famFor(emu, plat)
            val st = if (id in setOf("retroarch", "armsx2", "nethersx2")) byId(id + "_states") else null
            return listOfNotNull(save, st)
        }

        /** Nazwa stanu PCSX2/ArmSX2 → przedrostek gry („SLUS-21115 (ABCD1234)”). */
        private val P2S = Regex("""^(.*)\.(\d{2}|resume)\.p2s""", RegexOption.IGNORE_CASE)

        /** PS1 w RetroArchu (SwanStation, Beetle PSX): zapis `<gra>.srm` to surowa karta pamięci
         *  128 KB — ta sama co karta DuckStation `<gra>_1.mcd` na komputerze. DuckStation na
         *  Androidzie trzyma dane w Android/data, niedostępnym dla innych aplikacji. */
        val RA_PS1 = Fam("retroarch_ps1", "RetroArch (PS1)", "save/duckstation/memcards", Kind.PS1_SRM,
                         rx("^retroarch$"), dirs = "retroarch")
        const val CARD_SIZE = 131072L

        fun famFor(emu: String, plat: String = ""): Fam? {
            val id = Emulators.baseId(emu)
            if (id == "retroarch" && plat == "PS1") return RA_PS1
            return FAMS.firstOrNull { it.id == id }
        }

        /** Pliki zapisów rdzeni RetroArcha (nie sama gra, nie stany, nie zrzuty). */
        private val SAVE_EXT = rx("""\.(srm|sav|rtc|eep|fla|mpk|sra|nv|mcr|bkr|bcr|brm|dsv|ram)$""")
        // dzierżawa blokady profilu: telefon odnawia ją, dopóki działa aplikacja; po jej
        // zamknięciu w trakcie gry blokada przestaje obowiązywać po LOCK_LEASE s
        private const val LOCK_LEASE = 900.0
        private const val LOCK_BEAT_MS = 120_000L
        private const val LOCK_LEGACY = 900.0

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

    @Volatile private var deep: Pair<Long, Map<String, List<File>>>? = null

    /** Foldery `memcards` / `sstates` w pamięci telefonu (do 3 poziomów, bez Android/ i EmuStart/). */
    private fun deepFind(sub: String, fresh: Boolean = false): List<File> {
        val cached = deep
        if (!fresh && cached != null && System.currentTimeMillis() - cached.first < 60_000) return cached.second[sub] ?: emptyList()
        val found = HashMap<String, MutableList<File>>()
        var budget = 5000
        fun walk(d: File, depth: Int) {
            val list = d.listFiles() ?: return
            for (x in list) {
                if (budget-- <= 0) return
                if (!x.isDirectory || x.name.startsWith(".")) continue
                if (depth == 0 && (x.name == "Android" || x.name == "EmuStart")) continue
                val n = x.name.lowercase()
                if (n == "memcards" || n == "sstates") found.getOrPut(n) { mutableListOf() } += x
                else if (depth < 3) walk(x, depth + 1)
            }
        }
        walk(ext, 0)
        deep = Pair(System.currentTimeMillis(), found)
        return found[sub] ?: emptyList()
    }

    /** Foldery zapisów emulatora: ustawiony ręcznie, wykryty po grze, typowe miejsca. */
    fun phoneDirs(f: Fam, g: Game?): List<File> {
        val out = LinkedHashSet<File>()
        prefs.saveDir(f.dirs).takeIf { it.isNotBlank() }?.let { out += inner(f, File(it)) }
        prefs.learnedDir(f.dirs).takeIf { it.isNotBlank() }?.let { out += File(it) }
        if (f.ra) {
            out += File(ext, if (f.kind == Kind.RA_STATES) "RetroArch/states" else "RetroArch/saves")
            if (g != null) out += g.dir                           // „zapis obok gry” (domyślne w części wersji)
        } else {
            // stany PS2 obok kart pamięci (ten sam folder danych emulatora)
            if (f.cards.isNotEmpty()) (prefs.saveDir(f.cards).ifEmpty { prefs.learnedDir(f.cards) })
                .takeIf { it.isNotBlank() }?.let { File(it).parentFile?.let { d -> out += File(d, f.sub) } }
            // foldery memcards / sstates w pamięci telefonu (do 3 poziomów) — tylko w folderze
            // o nazwie emulatora; cudzy folder (np. ArmSX2 dla DuckStation) pomieszałby zapisy
            out += deepFind(f.sub).filter { d -> d.absolutePath.split('/').any { f.dirName.containsMatchIn(it) } }
        }
        return out.filter { it.isDirectory }
    }

    /** Wskazany ręcznie folder danych emulatora (np. ArmSX2, RetroArch) → jego podfolder
     *  z kartami / stanami / zapisami; wskazany bezpośrednio podfolder zostaje. */
    private fun inner(f: Fam, d: File): File {
        val sub = when (f.kind) {
            Kind.BY_STEM, Kind.PS1_SRM -> "saves"
            Kind.RA_STATES -> "states"
            else -> f.sub
        }
        val x = File(d, sub)
        return if (!d.name.equals(sub, true) && x.isDirectory) x else d
    }

    /** Opis dla Ustawień: gdzie telefon szuka zapisów emulatora — z liczbą plików i godziną
     *  ostatniej zmiany, żeby było widać, czy to folder, do którego emulator naprawdę zapisuje. */
    fun describe(f: Fam): String {
        val dirs = phoneDirs(f, null)
        fun info(d: File): String {
            val files = (d.listFiles() ?: emptyArray()).filter { it.isFile } +
                (if (f.ra) (d.listFiles() ?: emptyArray()).filter { it.isDirectory }.flatMap { (it.listFiles() ?: emptyArray()).filter { x -> x.isFile } } else emptyList())
            val last = files.maxOfOrNull { it.lastModified() } ?: 0L
            val t = if (last > 0) SimpleDateFormat("d.MM HH:mm", Locale.ROOT).format(Date(last)) else "—"
            return "${d.absolutePath} (${files.size} plików, ostatnia zmiana $t)"
        }
        return when {
            f.kind == Kind.PS1_CARDS && prefs.saveDir(f.id).isEmpty() && prefs.learnedDir(f.dirs).isEmpty() && dirs.isEmpty() ->
                "DuckStation na Androidzie trzyma dane w Android/data (niedostępne) — " +
                "dla zapisów wspólnych z PC wybierz dla PS1 RetroArch (SwanStation)"
            dirs.isNotEmpty() -> dirs.take(3).joinToString("; ") { info(it) } +
                (if (prefs.saveDir(f.id).isEmpty() && prefs.learnedDir(f.dirs).isEmpty() && dirs.size > 1) " — kilka folderów, wskaż właściwy (A)" else "")
            prefs.saveDir(f.id).isNotBlank() -> prefs.saveDir(f.id) + " (nie ma takiego folderu)"
            f.kind == Kind.BY_STEM || f.states -> "wykryje się po pierwszej grze"
            f.kind == Kind.PS1_CARDS -> "DuckStation na Androidzie trzyma dane w Android/data (niedostępne) — " +
                "dla zapisów wspólnych z PC wybierz dla PS1 RetroArch (SwanStation)"
            else -> "nie znaleziono — wskaż folder danych emulatora (A)"
        }
    }

    private fun matches(f: Fam, g: Game, name: String): Boolean = when (f.kind) {
        Kind.BY_STEM -> name.startsWith(g.stem + ".") && SAVE_EXT.containsMatchIn(name)
        Kind.PS1_SRM -> name == g.stem + ".srm" || name == g.name + "_1.mcd"
        Kind.PS1_CARDS -> name.endsWith(".mcd", true) && (name.startsWith(g.name + "_") || name.startsWith("shared_card", true))
        Kind.PS2_CARDS -> name.endsWith(".ps2", true)
        Kind.RA_STATES -> name.startsWith(g.stem + ".state")
        Kind.PS2_STATES -> !name.endsWith(".backup", true) && statePrefixes(f, g).any { name.startsWith("$it.") }
    }

    // stany PS2 nazywają się od numeru płyty, nie od nazwy gry — przedrostki poznane po grze
    private fun prefixKey(f: Fam) = "prefixes-" + f.id + ".json"
    private fun statePrefixes(f: Fam, g: Game): List<String> {
        val a = readJson(prefixKey(f)).optJSONArray(g.es + "/" + g.name) ?: return emptyList()
        return (0 until a.length()).map { a.getString(it) }
    }
    private fun mergePrefixes(f: Fam, add: JSONObject) {
        val o = readJson(prefixKey(f))
        for (k in add.keys()) {
            val cur = (o.optJSONArray(k) ?: JSONArray())
            val have = (0 until cur.length()).map { cur.getString(it) }.toMutableSet()
            val a = add.getJSONArray(k)
            for (i in 0 until a.length()) if (have.add(a.getString(i))) cur.put(a.getString(i))
            o.put(k, cur)
        }
        writeJson(prefixKey(f), o)
    }

    /** Nazwa pliku w telefonie dla pliku z serwera i odwrotnie (różne tylko dla PS1 w RetroArchu). */
    private fun toPhone(f: Fam, g: Game, nasName: String) = if (f.kind == Kind.PS1_SRM) g.stem + ".srm" else nasName
    private fun toNas(f: Fam, g: Game, phoneName: String) = if (f.kind == Kind.PS1_SRM) g.name + "_1.mcd" else phoneName

    /** Pliki gry w telefonie: nazwa pliku → plik (gdy w kilku miejscach — najnowszy). */
    private fun phoneFiles(f: Fam, g: Game, dirs: List<File>): MutableMap<String, File> {
        val out = HashMap<String, File>()
        fun take(x: File) {
            if (x.isFile && matches(f, g, x.name) && !(f.kind == Kind.PS1_SRM && x.name.endsWith(".mcd"))) {
                val cur = out[x.name]
                if (cur == null || x.lastModified() > cur.lastModified()) out[x.name] = x
            }
        }
        for (d in dirs) {
            for (x in d.listFiles() ?: emptyArray()) {
                if (x.isDirectory && f.ra && d != g.dir) x.listFiles()?.forEach { take(it) }
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
        .map { it.first }
        // folder po zmianie nazwy profilu (moved.json → nowa nazwa) to nie osobny profil
        .filter { server.nasRead("$it/moved.json") == null }
        .sortedBy { it.lowercase() }

    fun createProfile(name: String): String {
        val n = safe(name.trim().take(32))
        if (profiles().any { it.equals(n, true) }) throw IllegalArgumentException("Profil „$n” już istnieje.")
        server.nasPut("$n/emustart.json", "{}".toByteArray(), System.currentTimeMillis() / 1000.0)
        return n
    }

    // ── blokada: ten sam profil nie gra na dwóch urządzeniach naraz ──
    @Volatile private var beat: Thread? = null

    private fun now() = System.currentTimeMillis() / 1000.0

    private fun writeLock(profile: String) = server.nasPut("$profile/lock",
        JSONObject().put("host", device).put("time", now()).put("lease", LOCK_LEASE).toString().toByteArray(), now())

    private fun lockProfile(profile: String, force: Boolean) {
        val raw = server.nasRead("$profile/lock")
        if (raw != null) {
            val cur = try { JSONObject(String(raw)) } catch (e: Exception) { JSONObject() }
            val host = cur.optString("host")
            val active = host.isNotEmpty() && host != device &&
                now() - cur.optDouble("time", 0.0) < cur.optDouble("lease", LOCK_LEGACY)
            if (active && !force) throw Locked(host)
        }
        writeLock(profile)
        // odnawianie, dopóki proces aplikacji żyje (gra w emulatorze, EmuStart w tle)
        beat?.interrupt()
        beat = Thread({
            try {
                while (true) {
                    Thread.sleep(LOCK_BEAT_MS)
                    val cur = server.nasRead("$profile/lock")?.let { JSONObject(String(it)) } ?: break
                    if (cur.optString("host") != device) break      // przejęty przez inne urządzenie
                    writeLock(profile)
                }
            } catch (e: InterruptedException) { } catch (e: Exception) { Log.w(tag, "odnowienie blokady: $e") }
        }, "blokada").apply { isDaemon = true; start() }
    }

    private fun unlockProfile(profile: String) {
        beat?.interrupt(); beat = null
        try {
            val raw = server.nasRead("$profile/lock") ?: return
            if (JSONObject(String(raw)).optString("host") == device) server.nasDelete("$profile/lock")
        } catch (e: Exception) { Log.w(tag, "blokada: $e") }
    }

    /** Start aplikacji bez trwającej gry: własna blokada z poprzedniego uruchomienia
     *  (aplikację zamknięto w trakcie gry) nie może dalej blokować komputera. */
    fun releaseStaleLock(profile: String) {
        if (profile.isNotEmpty()) synchronized(lock) { unlockProfile(profile) }
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
    fun before(profile: String, emu: String, g: Game, takeover: Boolean = false): String {
        synchronized(lock) {
            if (prefs.phoneOwner.isEmpty()) prefs.phoneOwner = profile
            retryLocked()
            var msg = ""
            for (f in famsFor(emu, g.plat)) {
                val m = beforeLocked(profile, f, g, takeover)
                if (msg.isEmpty()) msg = m
            }
            return msg
        }
    }

    private fun beforeLocked(profile: String, f: Fam, g: Game, takeover: Boolean): String {
        if (f.kind == Kind.PS2_STATES) {
            // przedrostki stanów z serwera (inny telefon / po reinstalacji)
            try {
                server.nasRead("$profile/${f.nas}/.emustart-index.json")?.let { mergePrefixes(f, JSONObject(String(it))) }
            } catch (e: Exception) { }
        }
        val dirs = phoneDirs(f, g)
        if (dirs.isEmpty() && !f.ra)
            return if (f.states) "" else
                "Nie znaleziono folderu kart pamięci ${f.label} — zapisy zostaną tylko w telefonie (Ustawienia → Zapisy gier)."
        val phone = phoneFiles(f, g, dirs)
        swapOwner(profile, f, g, phone)
        try {
            lockProfile(profile, takeover)
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
            if (f.kind == Kind.PS1_SRM && (n != g.name + "_1.mcd" || v[0].toLong() != CARD_SIZE)) continue
            val cur = byName[n]
            if (cur == null || v[1] > nas.getValue(cur)[1]) byName[n] = rel
        }
        var n = 0
        for ((nasName, rel) in byName) {
            val name = toPhone(f, g, nasName)
            val v = nas.getValue(rel)
            val nSig = sig(v[0].toLong(), v[1])
            val key = "${f.id}/$name"
            val m = man.optJSONObject(key)
            val x = phone[name]
            val download = when {
                x == null -> true
                // pierwsze spotkanie z plikiem, który różni się od serwera (np. świeża karta
                // emulatora w telefonie): wygrywa serwer, wersja z telefonu — do kopii zapasowej
                m == null -> if (same(nSig, sig(x))) false else {
                    try { server.nasPut("$profile/_backup/${stamp()}-${safe(device)}/${f.nas}/konflikt/$rel",
                                        x.readBytes(), x.lastModified() / 1000.0) } catch (e: Exception) { }
                    conflicts += JSONObject().put("profile", profile).put("file",
                        "$name (pierwsza synchronizacja: wersja z serwera; z telefonu w kopii zapasowej)")
                    true
                }
                same(m.optJSONArray("n"), nSig) -> false                         // serwer bez zmian
                same(m.optJSONArray("p"), sig(x)) -> true                         // zmienił się tylko serwer
                else -> {                                                         // oba zmienione — konflikt
                    val nasWins = v[1] > x.lastModified() / 1000.0 + 2
                    conflicts += JSONObject().put("profile", profile).put("file", "$name (zostaje ${if (nasWins) "wersja z serwera" else "wersja z telefonu"})")
                    nasWins
                }
            }
            if (!download) {
                // pierwsze spotkanie z plikiem: zapamiętujemy wersję z serwera jako punkt odniesienia
                // (bez tego wysyłka po grze uznałaby plik z serwera za nieznany i go nie nadpisała)
                if (x != null && m == null)
                    man.put(key, JSONObject().put("n", nSig)
                        .put("p", if (same(nSig, sig(x))) sig(x) else JSONArray().put(-1).put(0)).put("rel", rel))
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
        val name = toPhone(f, g, rel.substringAfterLast('/'))
        if (!f.ra) return listOfNotNull(dirs.firstOrNull()?.let { File(it, name) })
        val learned = prefs.learnedDir(f.dirs).ifEmpty { prefs.saveDir(f.dirs) }
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
        val fams = famsFor(emu, g.plat)
        if (fams.isEmpty()) return
        busy = title
        try {
            synchronized(lock) {
                for (f in fams) {
                    learn(f, g, startedAt)
                    try {
                        push(profile, f, g)
                        removePending(profile, f, g)
                    } catch (e: Exception) {
                        Log.w(tag, "wysyłanie ${f.id}: $e")
                        addPending(profile, f, g)
                    }
                }
                try { unlockProfile(profile) } catch (e: Exception) { }
            }
        } finally { busy = "" }
    }

    /** Po grze: gdzie emulator zapisał (folder z plikiem gry zmienionym w trakcie gry). */
    private fun learn(f: Fam, g: Game, startedAt: Long) {
        val cands = LinkedHashSet<File>()
        cands += phoneDirs(f, g)
        if (!f.ra) cands += deepFind(f.sub, fresh = true)
        if (f.kind == Kind.PS2_STATES) {
            // stany zapisane w trakcie tej gry → przedrostek gry (także na serwer, dla innych telefonów)
            val found = LinkedHashSet<String>()
            for (d in cands.filter { it.isDirectory })
                for (x in d.listFiles() ?: emptyArray())
                    if (x.isFile && x.lastModified() >= startedAt - 2000) P2S.find(x.name)?.let {
                        found += it.groupValues[1]
                        if (prefs.learnedDir(f.dirs) != d.absolutePath) prefs.setLearnedDir(f.dirs, d.absolutePath)
                    }
            if (found.isNotEmpty()) {
                mergePrefixes(f, JSONObject().put(g.es + "/" + g.name, JSONArray(found.toList())))
                pendingIndex += f.id
            }
            return
        }
        for (d in cands.filter { it.isDirectory }) {
            val hit = phoneFiles(f, g, listOf(d)).values.firstOrNull { it.lastModified() >= startedAt - 2000 } ?: continue
            val dir = if (f.ra && hit.parentFile != d) d else hit.parentFile!!
            if (prefs.learnedDir(f.dirs) != dir.absolutePath) {
                prefs.setLearnedDir(f.dirs, dir.absolutePath)
                Log.i(tag, "folder zapisów ${f.id}: $dir")
            }
            return
        }
    }

    private val pendingIndex = HashSet<String>()

    private fun push(profile: String, f: Fam, g: Game) {
        if (f.kind == Kind.PS2_STATES && f.id in pendingIndex) {
            server.nasPut("$profile/${f.nas}/.emustart-index.json", readJson(prefixKey(f)).toString().toByteArray(),
                          System.currentTimeMillis() / 1000.0)
            pendingIndex -= f.id
        }
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
        if (f.kind == Kind.PS1_SRM && x.length() != CARD_SIZE) return false       // nie surowa karta 128 KB
        val nasName = toNas(f, g, name)
        val rel = m?.optString("rel")?.takeIf { it.isNotEmpty() && nas.containsKey(it) }
            ?: nas.keys.firstOrNull { it.substringAfterLast('/') == nasName }
            ?: newRel(f, g, x, nas)
        val v = nas[rel]
        val nSig = v?.let { sig(it[0].toLong(), it[1]) }
        val bak = "$profile/_backup/${stamp()}-${safe(device)}/${f.nas}"
        val first = v != null && m == null && !same(nSig, pSig)
        if (first || (v != null && m != null && !same(m.optJSONArray("n"), nSig) && v[1] > x.lastModified() / 1000.0 + 2)) {
            // serwer zmieniony w międzyczasie i nowszy — albo plik, którego telefon jeszcze nie
            // synchronizował (np. karta pamięci z komputera): zostaje wersja z serwera,
            // wersja z telefonu do kopii zapasowej (na serwerze i w telefonie)
            conflicts += JSONObject().put("profile", profile).put("file",
                "$name (zostaje wersja z serwera, wersja z telefonu w kopii zapasowej" + (if (first) " — pierwsza synchronizacja)" else ")"))
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
        if (f.kind == Kind.PS1_SRM) return toNas(f, g, x.name)
        if (f.kind != Kind.BY_STEM) return x.name
        val parent = x.parentFile
        val learned = prefs.learnedDir(f.dirs)
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
    private fun pendKey(profile: String, f: Fam, g: Game) = "$profile|${f.id}|${g.es}|${g.name}"

    private fun addPending(profile: String, f: Fam, g: Game) {
        val o = readJson("do-wyslania.json")
        o.put(pendKey(profile, f, g), JSONObject().put("profile", profile).put("fam", f.id).put("game", g.toJson()))
        writeJson("do-wyslania.json", o)
    }

    private fun removePending(profile: String, f: Fam, g: Game) {
        val o = readJson("do-wyslania.json")
        if (o.has(pendKey(profile, f, g))) { o.remove(pendKey(profile, f, g)); writeJson("do-wyslania.json", o) }
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
            val gm = Game.of(e.getJSONObject("game"))
            val f = (if (e.has("fam")) byId(e.getString("fam")) else famFor(e.optString("emu"), gm.plat))
            if (f == null) { o.remove(k); done++; continue }
            try {
                push(e.getString("profile"), f, gm)
                o.remove(k); done++
            } catch (ex: Exception) { Log.w(tag, "zaległe zapisy: $ex"); break }
        }
        if (done > 0) writeJson("do-wyslania.json", o)
        return done
    }
}
