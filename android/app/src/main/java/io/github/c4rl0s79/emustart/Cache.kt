package io.github.c4rl0s79.emustart

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Gry w telefonie: ostatnio uruchamiane (limit) + przypięte. Indeks w pliku JSON. */
object Cache {
    private val lock = Any()
    private fun file(ctx: Context) = File(ctx.filesDir, "gry-w-telefonie.json")

    fun index(ctx: Context): JSONObject = synchronized(lock) {
        try { JSONObject(file(ctx).readText()) } catch (e: Exception) { JSONObject() }
    }

    private fun save(ctx: Context, idx: JSONObject) = synchronized(lock) {
        val f = file(ctx)
        val tmp = File(f.path + ".tmp")
        tmp.writeText(idx.toString())
        tmp.renameTo(f)
    }

    fun complete(ctx: Context, key: String, paths: List<String>) = synchronized(lock) {
        val idx = index(ctx)
        val e = idx.optJSONObject(key) ?: JSONObject()
        e.put("complete", true).put("last", System.currentTimeMillis() / 1000.0).put("paths", JSONArray(paths))
        idx.put(key, e)
        save(ctx, idx)
    }

    fun togglePin(ctx: Context, key: String): Boolean = synchronized(lock) {
        val idx = index(ctx)
        val e = idx.optJSONObject(key) ?: JSONObject().put("complete", false)
        val p = !e.optBoolean("pinned")
        e.put("pinned", p)
        idx.put(key, e)
        save(ctx, idx)
        p
    }

    /** Usuwa najdawniej uruchamiane gry ponad limit (przypięte i `keep` zostają). */
    fun enforce(ctx: Context, recent: Int, keep: String) = synchronized(lock) {
        val idx = index(ctx)
        val games = idx.keys().asSequence().map { it to idx.getJSONObject(it) }
            .filter { it.second.optBoolean("complete") && !it.second.optBoolean("pinned") && it.first != keep }
            .sortedByDescending { it.second.optDouble("last", 0.0) }.toList()
        for ((k, e) in games.drop(maxOf(0, recent - 1))) {
            val paths = e.optJSONArray("paths") ?: JSONArray()
            for (i in 0 until paths.length()) File(paths.getString(i)).deleteRecursively()   // także rozpakowane ZIP-y
            idx.remove(k)
        }
        save(ctx, idx)
    }
}
