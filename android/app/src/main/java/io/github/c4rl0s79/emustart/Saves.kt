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
 * Zapisy gier: folder emulatora w telefonie ↔ serwer EmuStart.
 *
 * Zapisy w grze, stany RetroArcha i karty pamięci synchronizują się paczkami z serwerem
 * (SaveSync.kt, jak EmuStart na Windows — emustart/syncclient.py): jedna wersja na
 * wszystkich urządzeniach, rozbieżne zmiany (np. gra bez internetu) to konflikt, o którym
 * decyduje gracz. Paczki rozdzielone wg emulatora i rdzenia RetroArcha (Snes9x ≠ bsnes):
 *
 *   RetroArch          zapisy (.srm…) i stany (.stateN, .auto, miniatury) tej gry, per rdzeń
 *   RetroArch (PS1)    <gra>.srm (surowa karta 128 KB) = karta DuckStation <gra>_1.mcd na PC
 *   ArmSX2 / AetherSX2 karta Mcd001.ps2 = karta tej gry na PC (PCSX2: karta osobna dla gry);
 *                      telefon trzyma karty innych gier w schowku aplikacji i podmienia je
 *
 * Stany PS2 (.p2s) — różne wersje formatu na komputerze i telefonie — leżą osobno
 * (<profil>/states/android/…) i kopiuje je menedżer zapisów (emustart/savemgr.py).
 *
 * Profile: pliki w folderze emulatora należą do jednego profilu naraz (owners.json);
 * przed grą innego profilu pliki poprzedniego trafiają do schowka aplikacji
 * (zapisy/park/<profil>/…) i wracają, gdy tamten znów gra.
 */
class Saves(private val ctx: Context, private val server: Server, private val prefs: Prefs) {
    private val tag = "EmuStart"
    private val root = File(ctx.filesDir, "zapisy").apply { mkdirs() }
    private val sync = SaveSync(ctx, server)
    val device: String = "Telefon " + Build.MODEL

    enum class Kind { BY_STEM, PS1_SRM, PS1_CARDS, PS2_CARDS, RA_STATES, PS2_STATES }
    /** Emulator: id (ustawienia), folder na serwerze (tylko stany PS2), sposób dobierania
     *  plików, nazwa folderu danych w pamięci telefonu (wykrywanie). */
    data class Fam(val id: String, val label: String, val nas: String, val kind: Kind, val dirName: Regex,
                   val dirs: String = id, val cards: String = "") {
        /** Pliki w folderach RetroArcha (dobierane po nazwie pliku gry). */
        val ra: Boolean get() = kind == Kind.BY_STEM || kind == Kind.PS1_SRM || kind == Kind.RA_STATES
        /** Stany gry. */
        val states: Boolean get() = kind == Kind.RA_STATES || kind == Kind.PS2_STATES
        /** Podfolder danych emulatora PS1/PS2 (karty / stany). */
        val sub: String get() = if (kind == Kind.PS2_STATES) "sstates" else "memcards"
    }

    /** Gra: nazwa (jak na serwerze), nazwa pliku bez rozszerzenia, folder gry w telefonie, rdzeń RetroArcha. */
    data class Game(val es: String, val name: String, val stem: String, val dir: File, val core: String,
                    val plat: String = "", val serial: String = "", val alts: String = "") {
        fun toJson(): JSONObject = JSONObject().put("es", es).put("name", name).put("stem", stem)
            .put("dir", dir.absolutePath).put("core", core).put("plat", plat).put("serial", serial).put("alts", alts)
        /** Nazwy, od których RetroArch nazywa zapisy i stany: plik gry i ROM w środku ZIP-a. */
        val stems: List<String> get() = listOf(stem) + alts.split('|').filter { it.isNotEmpty() && it != stem }
        /** Przedrostek stanów PCSX2/ArmSX2 z numeru płyty: „SLUS-21346 (” (jak na komputerze). */
        val serialPrefix: String get() =
            Regex("[A-Z]{4}-\\d{3,5}").find(serial.uppercase())?.value?.let { "$it (" } ?: ""
        val key: String get() = "$es|$name"
        companion object {
            fun of(o: JSONObject) = Game(o.getString("es"), o.getString("name"), o.getString("stem"),
                                         File(o.getString("dir")), o.optString("core"), o.optString("plat"),
                                         o.optString("serial"), o.optString("alts"))
        }
    }

    class Locked(val host: String) : Exception("Profil gra teraz na: $host")

    companion object {
        private fun rx(s: String) = Regex(s, RegexOption.IGNORE_CASE)
        val FAMS = listOf(
            Fam("retroarch", "RetroArch", "", Kind.BY_STEM, rx("^retroarch$")),
            Fam("duckstation", "DuckStation", "", Kind.PS1_CARDS, rx("duck")),
            Fam("armsx2", "ArmSX2", "", Kind.PS2_CARDS, rx("armsx2")),
            Fam("nethersx2", "AetherSX2 / NetherSX2", "", Kind.PS2_CARDS, rx("aether|nether")),
            Fam("retroarch_states", "RetroArch — stany gry", "", Kind.RA_STATES, rx("^retroarch$")),
            // stany PS2 z telefonu — na serwerze osobno od PC (menedżer zapisów kopiuje je między nimi)
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

        /** Sloty 00–09, wznowienie PCSX2 (.resume) i autozapis ArmSX2 (.autosave, .autosave.2–5). */
        private val P2S = Regex("""^(.*)\.(\d{2}|resume|autosave(?:\.\d)?)\.p2s$""", RegexOption.IGNORE_CASE)
        /** Stan RetroArcha z miniaturą: „Gra.state3.png” → („Gra”, „.state3.png”). */
        private val RA_STATE = Regex("""(.+?)(\.state\d*(?:\.auto)?(?:\.png)?)""", RegexOption.IGNORE_CASE)
        /** Karta PS2 w slocie 1 (ArmSX2 / AetherSX2) — w niej karta uruchamianej gry. */
        const val PS2_SLOT = "Mcd001.ps2"

        /** „Beetle PSX HW” = „beetle_psx_hw” — porównanie folderów rdzeni. */
        fun normCore(s: String) = s.lowercase().replace(Regex("[^a-z0-9]"), "")

        /** PS1 w RetroArchu (SwanStation, Beetle PSX): zapis `<gra>.srm` to surowa karta pamięci
         *  128 KB — ta sama co karta DuckStation `<gra>_1.mcd` na komputerze. DuckStation na
         *  Androidzie trzyma dane w Android/data, niedostępnym dla innych aplikacji. */
        val RA_PS1 = Fam("retroarch_ps1", "RetroArch (PS1)", "", Kind.PS1_SRM, rx("^retroarch$"), dirs = "retroarch")
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

        /** Folder rdzenia RetroArcha (nazwa jak w RetroArchu) dla identyfikatora rdzenia („bsnes_libretro_android”). */
        fun coreDirOf(core: String): String {
            val c = core.removeSuffix("_libretro_android").removeSuffix("_libretro")
            return CORE_DIR[c] ?: c
        }

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

        private fun norm(s: String) = s.replace(Regex("[\\s_]+"), " ").trim().lowercase(Locale.ROOT)
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
    /** Paczki pominięte przy konflikcie („Pomiń”) — bez wysyłania po grze; konflikt wróci. */
    private val skipped = HashMap<String, Set<String>>()

    // ── foldery emulatorów w telefonie ──
    private val ext: File get() = Environment.getExternalStorageDirectory()

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
            if (g != null && f.kind != Kind.RA_STATES) out += g.dir     // „zapis obok gry” (domyślne w części wersji)
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

    // ── paczki zapisów gry (SaveSync) ──
    /** Paczka dla rodziny zapisów telefonu; null — rodzina bez paczek (stany PS2, DuckStation). */
    private fun bundleOf(f: Fam, g: Game): SaveSync.Bundle? {
        val core = SaveSync.coreId(g.core)
        val (emu, c, kind) = when (f.kind) {
            Kind.BY_STEM -> Triple("retroarch", SaveSync.saveCore(core), "saves")      // zgodne rdzenie — wspólne
            Kind.RA_STATES -> Triple("retroarch", core, "states")
            Kind.PS1_SRM -> Triple("duckstation", "", "memcards")
            Kind.PS2_CARDS -> Triple("pcsx2", "", "memcards")
            Kind.PS2_STATES -> Triple("pcsx2", "", "states")              // wspólne z PCSX2 na komputerze
            else -> return null
        }
        if (emu == "retroarch" && c.isEmpty()) return null           // dane rdzeni są osobne
        return SaveSync.Bundle(emu, c, kind, { collect(f, g, phoneDirs(f, g)) }, { arc -> targetsFor(f, g, arc) },
                               JSONObject().put("fam", f.id).put("game", g.toJson()))
    }

    /** Nazwa w paczce dla pliku z telefonu (null — plik nie należy do paczki tej gry). */
    private fun arcOf(f: Fam, g: Game, x: File): String? {
        val name = x.name
        if (name.endsWith(".emustart-new") || name.endsWith(".emustart-tmp")) return null
        val stems = g.stems.map { norm(it) }.toSet() + norm(g.name)
        return when (f.kind) {
            Kind.BY_STEM -> {
                val base = name.substringBeforeLast('.', "")
                if (base.isNotEmpty() && SAVE_EXT.containsMatchIn(name) && norm(base) in stems)
                    "game." + name.substringAfterLast('.').lowercase(Locale.ROOT) else null
            }
            Kind.RA_STATES -> RA_STATE.matchEntire(name)?.takeIf { norm(it.groupValues[1]) in stems }
                ?.let { "game" + it.groupValues[2].lowercase(Locale.ROOT) }
            Kind.PS1_SRM -> if (name.endsWith(".srm", true) && norm(name.dropLast(4)) in stems && x.length() == CARD_SIZE)
                "game_1.mcd" else null
            Kind.PS2_CARDS -> if (name.equals(PS2_SLOT, true)) "game.ps2" else null
            // stany PS2: nazwa jak w emulatorze (numer płyty i CRC — ta sama na komputerze)
            Kind.PS2_STATES -> if (!name.endsWith(".backup", true) && P2S.containsMatchIn(name) &&
                (statePrefixes(f, g).any { name.startsWith("$it.") } ||
                 (g.serialPrefix.isNotEmpty() && name.startsWith(g.serialPrefix)))) name else null
            else -> null
        }
    }

    /** Nazwa pliku w telefonie dla pliku paczki. */
    private fun localName(f: Fam, g: Game, arc: String): String? = when (f.kind) {
        Kind.PS1_SRM -> if (arc == "game_1.mcd") g.stem + ".srm" else null      // karta 2 — tylko na PC
        Kind.PS2_CARDS -> if (arc == "game.ps2") PS2_SLOT else null
        Kind.PS2_STATES -> if (P2S.containsMatchIn(arc) && !arc.contains('/')) arc else null
        else -> if (arc.startsWith("game") && !arc.contains('/')) g.stem + arc.removePrefix("game") else null
    }

    /** Pliki paczki w folderach telefonu (gdy w kilku miejscach — najnowszy). Podfoldery
     *  RetroArcha (sortowanie wg rdzenia / folderu gry) — tylko rdzenia tej gry: stany innych
     *  rdzeni o tej samej nazwie gry to inne dane. */
    private fun collect(f: Fam, g: Game, dirs: List<File>): Map<String, File> {
        val out = HashMap<String, File>()
        val myCore = normCore(coreDirOf(g.core))
        fun take(x: File) {
            if (!x.isFile) return
            val arc = arcOf(f, g, x) ?: return
            val cur = out[arc]
            if (cur == null || x.lastModified() > cur.lastModified()) out[arc] = x
        }
        fun mine(d: File) = normCore(d.name) == myCore || d.name == g.dir.name
        for (d in dirs) {
            for (x in d.listFiles() ?: emptyArray()) {
                if (x.isDirectory && f.ra && d != g.dir) {
                    if (!mine(x)) continue
                    x.listFiles()?.forEach { y ->
                        if (y.isDirectory) { if (normCore(y.name) == myCore) y.listFiles()?.forEach { take(it) } }
                        else take(y)
                    }
                } else take(x)
            }
        }
        return out
    }

    private fun targetsFor(f: Fam, g: Game, arc: String): List<File> {
        val n = localName(f, g, arc) ?: return emptyList()
        val dirs = phoneDirs(f, g)
        if (f.kind == Kind.PS2_CARDS) return listOfNotNull(dirs.firstOrNull()?.let { File(it, n) })
        return newTargets(f, g, dirs, n)
    }

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

    // ── blokada: ten sam profil nie gra na dwóch urządzeniach naraz (serwer: emustart/plock.py) ──
    // Atomowa blokada po stronie serwera; urządzenie rozpoznawane po tokenie sesji, nie po nazwie.
    @Volatile private var beat: Thread? = null

    private fun lockCall(profile: String, op: String, body: JSONObject): JSONObject {
        val r = server.sync("POST", "/v1/lock/$op", profile, body)
        if (r.code != 200) throw Server.HttpError(r.code, "blokada profilu: serwer odpowiedział ${r.code}")
        return try { JSONObject(String(r.body)) } catch (e: Exception) { JSONObject() }
    }

    private fun lockProfile(profile: String, force: Boolean) {
        val r = lockCall(profile, "take", JSONObject().put("lease", LOCK_LEASE).put("force", force))
        if (!r.optBoolean("ok")) throw Locked(r.optString("held_by").ifEmpty { "inne urządzenie" })
        // odnawianie, dopóki proces aplikacji żyje (gra w emulatorze, EmuStart w tle)
        beat?.interrupt()
        beat = Thread({
            try {
                while (true) {
                    Thread.sleep(LOCK_BEAT_MS)
                    if (!lockCall(profile, "renew", JSONObject().put("lease", LOCK_LEASE)).optBoolean("ok")) break   // przejęty
                }
            } catch (e: InterruptedException) { } catch (e: Exception) { Log.w(tag, "odnowienie blokady: $e") }
        }, "blokada").apply { isDaemon = true; start() }
    }

    private fun unlockProfile(profile: String) {
        beat?.interrupt(); beat = null
        try { lockCall(profile, "release", JSONObject()) } catch (e: Exception) { Log.w(tag, "blokada: $e") }
    }

    /** Start aplikacji bez trwającej gry: własna blokada z poprzedniego uruchomienia
     *  (aplikację zamknięto w trakcie gry) nie może dalej blokować komputera. */
    fun releaseStaleLock(profile: String) {
        if (profile.isNotEmpty()) synchronized(lock) { unlockProfile(profile) }
    }

    /** Po zalogowaniu i przy starcie: nowsze wersje zapisów gier znanych na telefonie (tego
     *  konta), gdy w telefonie bez zmian; bez gry, która właśnie działa (`skip` — Game.key). */
    fun pullAll(profile: String, skip: Set<String> = emptySet()): Int {
        if (profile.isEmpty()) return 0
        synchronized(lock) {
            val byGame = LinkedHashMap<String, MutableList<Pair<Fam, Game>>>()
            for (t in sync.known(profile)) {
                val g = try { Game.of(t.getJSONObject("game")) } catch (e: Exception) { continue }
                val f = byId(t.optString("fam")) ?: continue
                if (g.key in skip) continue
                byGame.getOrPut(g.key) { mutableListOf() }.add(f to g)
            }
            var n = 0
            val owners = readJson("owners.json")
            for ((_, list) in byGame) {
                val g = list.first().second
                // pliki w folderze emulatora należą do kogoś innego (inny profil / karta innej gry) — pomijamy
                val bundles = list.mapNotNull { (f, gg) -> bundleOf(f, gg)?.takeIf { b ->
                    b.files().values.none { owners.optString(it.absolutePath).let { o -> o.isNotEmpty() && o != ownerOf(profile, f, gg) } }
                } }
                val res = sync.pull(profile, g.es, g.name, bundles)
                n += res.downloaded
                if (res.offline.isNotEmpty()) break
            }
            if (n > 0) Log.i(tag, "zapisy z serwera po zalogowaniu / starcie: $n paczek ($profile)")
            return n
        }
    }

    // ── właściciel plików w folderze emulatora ──
    /** Właściciel plików paczki: profil, a dla karty PS2 w slocie — profil i gra (karta gry). */
    private fun ownerOf(profile: String, f: Fam, g: Game) = if (f.kind == Kind.PS2_CARDS) "$profile|${g.key}" else profile

    private fun parkDir(owner: String, f: Fam) = File(root, "park/" + safe(owner) + "/" + f.id)

    /** Pliki innego właściciela do schowka, pliki tego właściciela ze schowka na miejsce.
     *  Zwraca true, gdy pliki w folderze to tylko punkt wyjścia (karta PS2 innej gry albo
     *  wspólna karta sprzed EmuStart) — synchronizacja weźmie wtedy wersję z serwera. */
    private fun swapOwner(owner: String, f: Fam, g: Game, phone: Map<String, File>): Boolean {
        val owners = readJson("owners.json")
        val parked = readJson("park.json")              // plik w schowku → ścieżka w folderze emulatora
        val first = if (f.kind == Kind.PS2_CARDS) "" else prefs.phoneOwner.ifEmpty { owner }
        val mine = parkDir(owner, f)
        val mineParked = mine.walkTopDown().filter { it.isFile && parked.has(it.absolutePath) }.toList()
        var seed = false
        for (x in phone.values) {
            val cur = owners.optString(x.absolutePath).ifEmpty { first }
            if (cur == owner) continue
            if (cur.isEmpty()) { seed = true; continue }  // karta bez właściciela: punkt wyjścia dla tej gry
            val dst = File(parkDir(cur, f), Bridge.sha1(x.absolutePath) + "/" + x.name)
            dst.parentFile?.mkdirs()
            if (f.kind == Kind.PS2_CARDS && mineParked.isEmpty()) {
                // karta tej gry jeszcze nie istnieje w telefonie: kopia karty innej gry zostaje jako
                // punkt wyjścia (ma też zapisy tej gry z czasów wspólnej karty)
                x.copyTo(dst, true)
                seed = true
            } else if (!x.renameTo(dst)) { x.copyTo(dst, true); x.delete() }
            parked.put(dst.absolutePath, x.absolutePath)
            owners.remove(x.absolutePath)
        }
        // pliki tego właściciela ze schowka wracają na miejsce
        for (p in mineParked) {
            val back = File(parked.optString(p.absolutePath))
            if (back.exists()) {
                if (f.kind != Kind.PS2_CARDS) continue
                backupLocal(back, "karta-przed-podmiana")
                back.delete()
            }
            back.parentFile?.mkdirs()
            if (!p.renameTo(back)) { p.copyTo(back, true); p.delete() }
            parked.remove(p.absolutePath)
            owners.put(back.absolutePath, owner)
            seed = false
        }
        writeJson("owners.json", owners)
        writeJson("park.json", parked)
        return seed
    }

    private fun own(owner: String, files: Collection<File>) {
        val owners = readJson("owners.json")
        for (x in files) owners.put(x.absolutePath, owner)
        writeJson("owners.json", owners)
    }

    // ── przed grą ──
    /** Zapisy profilu do folderu emulatora. `ask` — decyzja gracza przy konflikcie
     *  (local | remote | skip). Zwraca komunikat dla użytkownika ('' = OK). */
    fun before(profile: String, emu: String, g: Game, takeover: Boolean = false,
               ask: (List<SaveSync.Conflict>) -> String = { "skip" }): String {
        synchronized(lock) {
            if (prefs.phoneOwner.isEmpty()) prefs.phoneOwner = profile
            retryLocked()
            var msg = ""
            val fams = famsFor(emu, g.plat)
            try { lockProfile(profile, takeover) } catch (e: Locked) { throw e } catch (e: Exception) {
                Log.w(tag, "blokada profilu: $e")
                msg = "Serwer niedostępny — gra na zapisach z telefonu; wyślą się później."
            }
            val bundles = ArrayList<Pair<Fam, SaveSync.Bundle>>()
            val seeds = HashSet<String>()
            for (f in fams) {
                val b = bundleOf(f, g) ?: continue
                if (!f.ra && phoneDirs(f, g).isEmpty()) {
                    if (msg.isEmpty() && f.kind == Kind.PS2_CARDS)
                        msg = "Nie znaleziono folderu kart pamięci ${f.label} — zapisy zostaną " +
                            "tylko w telefonie (Ustawienia → Zapisy gier)."
                    continue
                }
                if (swapOwner(ownerOf(profile, f, g), f, g, b.files())) seeds += b.key
                bundles += f to b
            }
            if (bundles.isEmpty()) return msg
            val res = sync.before(profile, g.es, g.name, bundles.map { it.second }, seeds)
            if (res.conflicts.isNotEmpty()) {
                var choice = ask(res.conflicts)
                if (choice != "skip") try {
                    sync.resolve(profile, g.es, g.name, res.conflicts, choice)
                } catch (e: Exception) {
                    Log.w(tag, "konflikt zapisów: $e")
                    msg = "Nie udało się rozstrzygnąć konfliktu zapisów — gram na zapisach z telefonu."
                    choice = "skip"
                }
                skipped["$profile|${g.key}"] = if (choice == "skip") res.conflicts.map { it.bundle.key }.toSet() else emptySet()
            } else {
                skipped.remove("$profile|${g.key}")
                if (res.offline.isNotEmpty() && msg.isEmpty()) msg = "Zapisy bez synchronizacji z serwerem — wyślą się później."
            }
            for ((f, b) in bundles) own(ownerOf(profile, f, g), b.files().values)
            if (res.downloaded > 0) Log.i(tag, "zapisy z serwera: ${res.downloaded} paczek ($profile)")
            return msg
        }
    }

    /** Zapisy gry na telefonie po synchronizacji z serwerem: stany (nazwa w paczce → plik),
     *  liczba plików zapisów w grze, powód braku synchronizacji ('' = zsynchronizowane). */
    class View(val states: Map<String, File>, val saves: Int, val offline: String, val conflicts: Int)

    /** „Wczytaj zapis”: zapisy w grze i stany RetroArcha tej gry z serwera (jak przed grą, bez
     *  pytania o konflikty — te rozstrzyga gracz przy uruchomieniu). */
    fun raStates(profile: String, emu: String, g: Game): View {
        val fams = listOfNotNull(famFor(emu, g.plat), byId("retroarch_states"))
        val bundles = fams.mapNotNull { f -> bundleOf(f, g)?.let { f to it } }
        synchronized(lock) {
            var offline = ""
            var conflicts = 0
            val ready = bundles.filter { (f, b) -> !swapOwner(ownerOf(profile, f, g), f, g, b.files()) }
            if (ready.isNotEmpty()) {
                try {
                    val res = sync.before(profile, g.es, g.name, ready.map { it.second })
                    offline = res.offline
                    conflicts = res.conflicts.size
                } catch (e: Exception) { Log.w(tag, "zapisy z serwera: $e"); offline = e.message ?: e.toString() }
                for ((f, b) in ready) own(ownerOf(profile, f, g), b.files().values)
            }
            val st = bundles.firstOrNull { it.first.kind == Kind.RA_STATES }?.second?.files() ?: emptyMap()
            val sv = bundles.firstOrNull { it.first.kind != Kind.RA_STATES }?.second?.files()?.size ?: 0
            return View(st, sv, offline, conflicts)
        }
    }

    /** Wybrany stan (plik w telefonie) jako autozapis tej gry — RetroArch wczytuje go przy
     *  starcie (autowczytywanie jest zawsze włączone). Zwraca liczbę plików. */
    fun placeAuto(profile: String, g: Game, path: String): Int {
        // „arc:game.state3” — stan z paczki (po synchronizacji przed grą, także z innego urządzenia)
        val src = if (path.startsWith("arc:")) {
            val f = byId("retroarch_states") ?: return 0
            bundleOf(f, g)?.files()?.get(path.removePrefix("arc:")) ?: return 0
        } else File(path.removePrefix("ra:"))
        if (!src.isFile) return 0
        val m = RA_STATE.matchEntire(src.name) ?: return 0
        val auto = File(src.parentFile, m.groupValues[1] + ".state.auto")
        if (auto == src) return 1
        synchronized(lock) {
            if (auto.exists()) backupLocal(auto, "przed-wczytaniem-stanu")
            src.copyTo(auto, true)
            File(src.path + ".png").takeIf { it.isFile }?.copyTo(File(auto.path + ".png"), true)
            own(profile, listOf(auto))
            Log.i(tag, "stan do wczytania: ${src.name} → ${auto.name}")
            return 1
        }
    }

    /** Gdzie położyć zapis, którego w telefonie jeszcze nie ma. */
    private fun newTargets(f: Fam, g: Game, dirs: List<File>, name: String): List<File> {
        if (!f.ra) return listOfNotNull(dirs.firstOrNull()?.let { File(it, name) })
        if (f.kind == Kind.RA_STATES) {
            val root = File(prefs.learnedDir(f.dirs).ifEmpty { prefs.saveDir(f.dirs) }
                .ifEmpty { File(ext, "RetroArch/states").absolutePath })
            fun coreSub(base: File) = base.listFiles()?.firstOrNull { it.isDirectory && normCore(it.name) == normCore(coreDir(g)) }
                ?.name ?: coreDir(g)
            return when (prefs.learnedDir("layout:" + f.id)) {
                "flat" -> listOf(File(root, name))
                "core" -> listOf(File(File(root, coreSub(root)), name))
                "content" -> listOf(File(File(root, g.dir.name), name))
                "content+core" -> File(root, g.dir.name).let { listOf(File(File(it, coreSub(it)), name)) }
                // układ jeszcze nieznany (pierwsza gra): oba typowe miejsca
                else -> listOf(File(File(root, coreSub(root)), name), File(root, name))
            }
        }
        val learned = prefs.learnedDir(f.dirs).ifEmpty { prefs.saveDir(f.dirs) }
        val pick = if (learned.isNotEmpty() && File(learned).isDirectory) listOf(File(learned)) else dirs
        return pick.map { d ->
            // RetroArch z sortowaniem wg rdzenia: podfolder rdzenia, gdy folder ma podfoldery
            val sub = d.listFiles()?.firstOrNull { it.isDirectory && normCore(it.name) == normCore(coreDir(g)) }
            if (d != g.dir && sub != null) File(sub, name) else File(d, name)
        }
    }

    // ── po grze ──
    fun after(profile: String, emu: String, g: Game, startedAt: Long, title: String) {
        val fams = famsFor(emu, g.plat)
        if (fams.isEmpty()) return
        busy = title
        try {
            synchronized(lock) {
                for (f in fams) learn(f, g, startedAt)
                val skip = skipped.remove("$profile|${g.key}") ?: emptySet()
                for (f in fams) {
                    try {
                        upload(profile, f, g, skip)
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

    /** Zmienione zapisy rodziny `f` na serwer (paczka albo — stany PS2 — folder na serwerze). */
    private fun upload(profile: String, f: Fam, g: Game, skip: Set<String> = emptySet()) {
        val b = bundleOf(f, g) ?: return
        val res = sync.after(profile, g.es, g.name, listOf(b), skip)
        own(ownerOf(profile, f, g), b.files().values)
        if (res.conflicts.isNotEmpty())
            Log.i(tag, "zapisy ${b.key}: na serwerze nowsza wersja z innego urządzenia — decyzja przy następnej grze")
    }

    /** Stany tej gry z innych rdzeni (podgląd w „Wczytaj zapis”). */
    fun otherCoreStates(profile: String, g: Game): List<Pair<String, File>> =
        sync.otherCoreStates(profile, g.es, g.name, SaveSync.coreId(g.core))

    /** Stan synchronizacji gry z serwerem (opcje gry). */
    fun syncStatus(profile: String, es: String, name: String): JSONObject = sync.status(profile, es, name)

    /** Po grze: gdzie emulator zapisał (folder z plikiem gry zmienionym w trakcie gry). */
    private fun learn(f: Fam, g: Game, startedAt: Long) {
        val cands = LinkedHashSet<File>()
        cands += phoneDirs(f, g)
        if (!f.ra) cands += deepFind(f.sub, fresh = true)
        if (f.kind == Kind.PS2_STATES) {
            // stany zapisane w trakcie tej gry → przedrostek gry (także na serwer, dla innych telefonów)
            val found = LinkedHashSet<String>()
            val sp = g.serialPrefix
            for (d in cands.filter { it.isDirectory })
                for (x in d.listFiles() ?: emptyArray()) {
                    if (!x.isFile) continue
                    val fresh = x.lastModified() >= startedAt - 2000
                    // zmieniony w tej grze albo (bez względu na czas) z numerem płyty tej gry
                    if (fresh || (sp.isNotEmpty() && x.name.startsWith(sp))) P2S.find(x.name)?.let {
                        found += it.groupValues[1]
                        if (fresh && prefs.learnedDir(f.dirs) != d.absolutePath) prefs.setLearnedDir(f.dirs, d.absolutePath)
                    }
                }
            val known = statePrefixes(f, g).toSet()
            if (found.isNotEmpty() && !known.containsAll(found))
                mergePrefixes(f, JSONObject().put(g.es + "/" + g.name, JSONArray(found.toList())))
            return
        }
        for (d in cands.filter { it.isDirectory }) {
            val hit = collect(f, g, listOf(d)).values.firstOrNull { it.lastModified() >= startedAt - 2000 } ?: continue
            if (f.kind == Kind.RA_STATES) {
                val parts = hit.parentFile!!.absolutePath.removePrefix(d.absolutePath).trim('/').split('/').filter { it.isNotEmpty() }
                val lay = when {
                    parts.isEmpty() -> "flat"
                    parts.size == 1 && normCore(parts[0]) == normCore(coreDir(g)) -> "core"
                    parts.size == 1 -> "content"
                    else -> "content+core"
                }
                if (prefs.learnedDir("layout:" + f.id) != lay) {
                    prefs.setLearnedDir("layout:" + f.id, lay)
                    Log.i(tag, "układ stanów RetroArcha: $lay ($d)")
                }
            }
            val dir = if (f.ra && hit.parentFile != d) d else hit.parentFile!!
            if (prefs.learnedDir(f.dirs) != dir.absolutePath) {
                prefs.setLearnedDir(f.dirs, dir.absolutePath)
                Log.i(tag, "folder zapisów ${f.id}: $dir")
            }
            return
        }
    }

    /** Folder rdzenia tej gry (nazwa jak w RetroArchu: corename, np. „bsnes”, „Beetle PSX HW”). */
    private fun coreDir(g: Game): String = coreDirOf(g.core)

    // ── stany PS2: przedrostki nazw (numer płyty i CRC) poznane po grze ──

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

    fun pendingCount(): Int = pendingGames().size

    /** Gry, których zapisy czekają na wysłanie (bez połączenia) — nazwy, po jednej na grę. */
    fun pendingGames(): List<String> {
        val o = readJson("do-wyslania.json")
        return o.keys().asSequence().mapNotNull { o.optJSONObject(it)?.optJSONObject("game")?.optString("name") }
            .filter { it.isNotEmpty() }.distinct().toList()
    }

    /** Zaległe wysyłki (przy starcie aplikacji i przed grą). */
    fun retryPending(): Int {
        synchronized(lock) { return retryLocked() }
    }

    private fun retryLocked(): Int {
        val o = readJson("do-wyslania.json")
        var done = 0
        val owners = readJson("owners.json")
        for (k in o.keys().asSequence().toList()) {
            val e = o.getJSONObject(k)
            val gm = Game.of(e.getJSONObject("game"))
            val f = (if (e.has("fam")) byId(e.getString("fam")) else famFor(e.optString("emu"), gm.plat))
            if (f == null) { o.remove(k); done++; continue }
            val profile = e.getString("profile")
            // pliki w folderze emulatora należą już do kogoś innego (inny profil / inna gra na karcie PS2)
            val b = bundleOf(f, gm)
            if (b != null && b.files().values.any { owners.optString(it.absolutePath).let { o2 -> o2.isNotEmpty() && o2 != ownerOf(profile, f, gm) } }) {
                continue
            }
            try {
                upload(profile, f, gm)
                o.remove(k); done++
            } catch (ex: Exception) { Log.w(tag, "zaległe zapisy: $ex"); break }
        }
        if (done > 0) writeJson("do-wyslania.json", o)
        return done
    }
}
