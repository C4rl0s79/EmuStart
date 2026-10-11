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

    /** Usuwa grę z telefonu: pliki gry (także `.part` i mapy bloków), utworzone przy
     *  przygotowaniu (rozpakowany ZIP, playlista) i wpis w indeksie. Zwraca zwolnione bajty. */
    fun remove(ctx: Context, key: String, files: List<File>): Long = synchronized(lock) {
        val idx = index(ctx)
        val e = idx.optJSONObject(key)
        val all = files.flatMap { listOf(it, File(it.path + ".part"), File(it.path + ".part.map")) }.toMutableList()
        val paths = e?.optJSONArray("paths") ?: JSONArray()
        for (i in 0 until paths.length()) all += File(paths.getString(i))
        var freed = 0L
        for (f in all.distinctBy { it.absolutePath }) {
            if (!f.exists()) continue
            freed += if (f.isDirectory) f.walkBottomUp().filter { it.isFile }.sumOf { it.length() } else f.length()
            f.deleteRecursively()
        }
        // puste foldery po grze (np. folder gry na kilku płytach)
        files.mapNotNull { it.parentFile }.distinct().forEach { d -> if (d.list()?.isEmpty() == true) d.delete() }
        idx.remove(key)
        save(ctx, idx)
        freed
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
