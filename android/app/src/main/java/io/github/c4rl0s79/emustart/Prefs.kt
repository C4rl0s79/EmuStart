package io.github.c4rl0s79.emustart

import android.content.Context
import android.content.SharedPreferences
import android.os.Environment
import org.json.JSONObject
import java.io.File

/** Ustawienia aplikacji (serwer i klucz urządzenia, emulator dla systemu, wygląd). */
class Prefs(ctx: Context) {
    private val sp: SharedPreferences = ctx.getSharedPreferences("emustart", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = sp.getString("server_url", "") ?: ""
        set(v) = sp.edit().putString("server_url", v.trim().trimEnd('/')).apply()

    /** Adresy serwera podane przez serwer (JSON: [{url, kind, tls}]) — klient wybiera najlepszy. */
    var serverEndpoints: String
        get() = sp.getString("server_endpoints", "") ?: ""
        set(v) = sp.edit().putString("server_endpoints", v).apply()

    /** Odcisk SHA-256 certyfikatu serwera (port szyfrowany) — przypięty przy zalogowaniu (kod QR). */
    var serverFp: String
        get() = sp.getString("server_fp", "") ?: ""
        set(v) = sp.edit().putString("server_fp", v.trim().lowercase()).apply()

    /** Urządzenie z kluczem urządzenia (0.37): id na serwerze, alias klucza w Android Keystore, konto. */
    var deviceId: String
        get() = sp.getString("device_id", "") ?: ""
        set(v) = sp.edit().putString("device_id", v).apply()
    var deviceKey: String
        get() = sp.getString("device_key", "") ?: ""
        set(v) = sp.edit().putString("device_key", v).apply()
    var serverAccount: String
        get() = sp.getString("server_account", "") ?: ""
        set(v) = sp.edit().putString("server_account", v).apply()
    /** Prośba o dołączenie czekająca na zatwierdzenie (JSON; '' = brak) — przetrwa restart. */
    var enrollPending: String
        get() = sp.getString("enroll_pending", "") ?: ""
        set(v) = sp.edit().putString("enroll_pending", v).apply()

    var cacheRecent: Int
        get() = sp.getInt("cache_recent", 10)
        set(v) = sp.edit().putInt("cache_recent", v.coerceIn(1, 100)).apply()

    var streams: Int
        get() = sp.getInt("streams", 8)
        set(v) = sp.edit().putInt("streams", v.coerceIn(1, 8)).apply()

    var gamesLogo: Boolean
        get() = sp.getBoolean("games_logo", false)
        set(v) = sp.edit().putBoolean("games_logo", v).apply()

    var look: JSONObject
        get() = try { JSONObject(sp.getString("look", "{}") ?: "{}") } catch (e: Exception) { JSONObject() }
        set(v) = sp.edit().putString("look", v.toString()).apply()

    /** Czas ostatniej zmiany wyglądu (synchronizacja z kontem: nowszy wygrywa). */
    var lookTime: Long
        get() = sp.getLong("look_t", 0L)
        set(v) = sp.edit().putLong("look_t", v).apply()

    /** Prośby o nowe konto z tego telefonu (JSON: [{rid, account, code}]) — czekają na administratora. */
    var accountRequests: String
        get() = sp.getString("account_requests", "[]") ?: "[]"
        set(v) = sp.edit().putString("account_requests", v).apply()

    /** Profil grający na telefonie (nazwa folderu profilu na serwerze); '' = nie wybrano. */
    var profile: String
        get() = sp.getString("profile", "") ?: ""
        set(v) = sp.edit().putString("profile", v).apply()

    /** Tokeny kont (od serwera po podaniu hasła konta — inny użytkownik na tym telefonie;
     *  do wylogowania). */
    fun token(profile: String): String = obj("profile_tokens").optString(profile.lowercase())

    fun setToken(profile: String, token: String) {
        val o = obj("profile_tokens")
        val names = obj("profile_token_names")
        if (token.isEmpty()) { o.remove(profile.lowercase()); names.remove(profile.lowercase()) }
        else { o.put(profile.lowercase(), token); names.put(profile.lowercase(), profile) }
        sp.edit().putString("profile_tokens", o.toString()).putString("profile_token_names", names.toString()).apply()
    }

    /** Konta zalogowane hasłem na tym telefonie (nazwy jak na serwerze). */
    fun tokenAccounts(): List<String> {
        val t = obj("profile_tokens")
        val n = obj("profile_token_names")
        return t.keys().asSequence().map { n.optString(it, it) }.toList()
    }

    /** PIN tylko na tym telefonie (skrót PBKDF2 — serwer go nie zna); '' = bez PIN-u. */
    fun pinHash(profile: String): String = obj("local_pins").optString(profile.lowercase())

    fun setPinHash(profile: String, hash: String) {
        val o = obj("local_pins")
        if (hash.isEmpty()) o.remove(profile.lowercase()) else o.put(profile.lowercase(), hash)
        sp.edit().putString("local_pins", o.toString()).apply()
    }

    private fun obj(key: String): org.json.JSONObject =
        try { org.json.JSONObject(sp.getString(key, "{}") ?: "{}") } catch (e: Exception) { org.json.JSONObject() }

    /** Profil, do którego należą zapisy zastane w emulatorach (pierwszy wybrany). */
    var phoneOwner: String
        get() = sp.getString("phone_owner", "") ?: ""
        set(v) = sp.edit().putString("phone_owner", v).apply()

    /** Rdzenie RetroArcha, które na tym telefonie uruchomiły grę (RetroArch nie pokazuje
     *  innym aplikacjom, które rdzenie są pobrane). */
    fun coreOk(core: String): Boolean = sp.getStringSet("cores_ok", emptySet())?.contains(core) == true
    fun getString(key: String): String = sp.getString(key, "") ?: ""
    fun putString(key: String, v: String) = sp.edit().putString(key, v).apply()

    fun markCoreOk(core: String) {
        val s = HashSet(sp.getStringSet("cores_ok", emptySet()) ?: emptySet())
        if (s.add(core)) sp.edit().putStringSet("cores_ok", s).apply()
    }

    fun saveDir(fam: String): String = sp.getString("save_dir:$fam", "") ?: ""
    fun setSaveDir(fam: String, v: String) = sp.edit().putString("save_dir:$fam", v.trim()).apply()
    fun learnedDir(fam: String): String = sp.getString("learned_dir:$fam", "") ?: ""
    fun setLearnedDir(fam: String, v: String) = sp.edit().putString("learned_dir:$fam", v).apply()

    fun emulator(es: String): String = sp.getString("emu:$es", "") ?: ""
    fun setEmulator(es: String, id: String) = sp.edit().putString("emu:$es", id).apply()
    /** Czas wyboru emulatora przez gracza (synchronizacja z kontem na serwerze; 0 = nie wybierał). */
    fun emulatorTime(es: String): Long = sp.getLong("emu_t:$es", 0L)
    fun setEmulatorChoice(es: String, id: String, t: Long = System.currentTimeMillis()) =
        sp.edit().putString("emu:$es", id).putLong("emu_t:$es", t).apply()
    fun emulatorChoices(): Map<String, Pair<String, Long>> = sp.all.keys.filter { it.startsWith("emu_t:") }
        .associate { k -> val es = k.removePrefix("emu_t:"); es to Pair(emulator(es), emulatorTime(es)) }

    /** Zalogowany na serwerze: adres i klucz urządzenia (od 0.43 jedyna droga). */
    val configured: Boolean get() = serverUrl.startsWith("https://") && deviceId.isNotBlank()

    /** Wylogowanie z serwera: adres, klucz urządzenia, odcisk certyfikatu, tokeny kont. */
    fun forgetServer() {
        sp.edit().remove("server_url").remove("server_key").remove("server_endpoints").remove("server_fp")
            .remove("device_id").remove("device_key").remove("server_account")
            .remove("profile_tokens").remove("profile_token_names").apply()
    }

    /** Gry w publicznym folderze — emulatory z dostępem do pamięci mogą je czytać. */
    fun gamesDir(ctx: Context): File {
        val pub = File(Environment.getExternalStorageDirectory(), "EmuStart/games")
        return if (Environment.isExternalStorageManager()) pub
               else File(ctx.getExternalFilesDir(null), "games")
    }
}
