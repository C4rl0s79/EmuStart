package io.github.c4rl0s79.emustart

import android.content.Context
import android.content.SharedPreferences
import android.os.Environment
import org.json.JSONObject
import java.io.File

/** Ustawienia aplikacji (adres i klucz serwera, emulator dla systemu, wygląd). */
class Prefs(ctx: Context) {
    private val sp: SharedPreferences = ctx.getSharedPreferences("emustart", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = sp.getString("server_url", "") ?: ""
        set(v) = sp.edit().putString("server_url", v.trim().trimEnd('/')).apply()

    var serverKey: String
        get() = sp.getString("server_key", "") ?: ""
        set(v) = sp.edit().putString("server_key", v.trim()).apply()

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

    fun emulator(es: String): String = sp.getString("emu:$es", "") ?: ""
    fun setEmulator(es: String, id: String) = sp.edit().putString("emu:$es", id).apply()

    val configured: Boolean get() = serverUrl.isNotBlank() && serverKey.isNotBlank()

    /** Gry w publicznym folderze — emulatory z dostępem do pamięci mogą je czytać. */
    fun gamesDir(ctx: Context): File {
        val pub = File(Environment.getExternalStorageDirectory(), "EmuStart/games")
        return if (Environment.isExternalStorageManager()) pub
               else File(ctx.getExternalFilesDir(null), "games")
    }
}
