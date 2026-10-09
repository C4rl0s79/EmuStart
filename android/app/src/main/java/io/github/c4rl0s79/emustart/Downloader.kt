package io.github.c4rl0s79.emustart

import java.io.File
import java.io.RandomAccessFile
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/**
 * Pobieranie pliku gry z serwera blokami, kilkoma strumieniami naraz (HTTP Range),
 * jak w EmuStart na Windows. Obok `<plik>.part` mapa pobranych bloków `<plik>.part.map`
 * — przerwane pobieranie wznawia się od brakujących bloków.
 */
class Progress(val total: Long, val files: Int) {
    val done = AtomicLong(0)
    @Volatile var file = 0
    private val started = System.nanoTime()
    private val window = ArrayDeque<Pair<Long, Long>>()

    @Synchronized fun add(n: Long) {
        val d = done.addAndGet(n)
        val now = System.nanoTime()
        window.addLast(Pair(now, d))
        while (window.size > 2 && now - window.first().first > 5_000_000_000L) window.removeFirst()
    }

    @Synchronized fun speed(): Double {
        if (window.size < 2) return 0.0
        val (t0, b0) = window.first(); val (t1, b1) = window.last()
        return if (t1 > t0) (b1 - b0) * 1e9 / (t1 - t0) else 0.0
    }

    fun snapshot(): Map<String, Any?> {
        val sp = speed()
        val left = maxOf(0L, total - done.get())
        return mapOf("done" to done.get(), "total" to total, "left" to left, "speed" to sp,
                     "eta" to (if (sp > 0) left / sp else null), "file" to file, "files" to files)
    }
}

class Downloader(private val server: Server, private val streams: Int) {
    companion object { const val BLOCK = 4L * 1024 * 1024 }

    val cancel = AtomicBoolean(false)

    /** Ile bajtów pliku jeszcze brakuje (do paska postępu). */
    fun missing(dst: File, size: Long): Long {
        if (dst.isFile && dst.length() == size) return 0
        val map = File(dst.path + ".part.map")
        val blocks = ((size + BLOCK - 1) / BLOCK).toInt()
        if (!map.isFile || map.length() != blocks.toLong()) return size
        val m = map.readBytes()
        var have = 0L
        for (i in m.indices) if (m[i].toInt() == 1) have += minOf(BLOCK, size - i * BLOCK)
        return size - have
    }

    fun fetch(gameId: Int, rel: String, dst: File, size: Long, prog: Progress) {
        if (dst.isFile && dst.length() == size) return
        dst.parentFile?.mkdirs()
        val part = File(dst.path + ".part")
        val mapf = File(dst.path + ".part.map")
        val blocks = ((size + BLOCK - 1) / BLOCK).toInt().coerceAtLeast(1)
        val done = ByteArray(blocks)
        if (part.isFile && part.length() == size && mapf.isFile && mapf.length() == blocks.toLong()) {
            mapf.readBytes().copyInto(done)
        }
        RandomAccessFile(part, "rw").use { it.setLength(size) }
        val next = AtomicInteger(0)
        val error = arrayOfNulls<Exception>(1)
        val path = server.fileUrlPath(gameId, rel)
        val lock = Any()
        var dirty = 0

        fun worker() {
            try {
                RandomAccessFile(part, "rw").use { raf ->
                    val buf = ByteArray(256 * 1024)
                    while (!cancel.get() && error[0] == null) {
                        var i: Int
                        do { i = next.getAndIncrement() } while (i < blocks && done[i].toInt() == 1)
                        if (i >= blocks) return
                        val off = i * BLOCK
                        val n = minOf(BLOCK, size - off)
                        val c = server.open(path, "bytes=$off-${off + n - 1}", 60000)
                        try {
                            c.inputStream.use { inp ->
                                var pos = off
                                var left = n
                                while (left > 0) {
                                    if (cancel.get()) return
                                    val r = inp.read(buf, 0, minOf(buf.size.toLong(), left).toInt())
                                    if (r < 0) throw java.io.IOException("przerwane połączenie (blok $i)")
                                    raf.seek(pos); raf.write(buf, 0, r)
                                    pos += r; left -= r
                                    prog.add(r.toLong())
                                }
                            }
                        } finally { c.disconnect() }
                        synchronized(lock) {
                            done[i] = 1
                            if (++dirty >= 8) { mapf.writeBytes(done); dirty = 0 }
                        }
                    }
                }
            } catch (e: Exception) {
                synchronized(lock) { if (error[0] == null) error[0] = e }
            }
        }

        val ths = (0 until streams.coerceIn(1, 8)).map { Thread({ worker() }, "pobieranie-$it").apply { start() } }
        ths.forEach { it.join() }
        synchronized(lock) { mapf.writeBytes(done) }
        error[0]?.let { throw it }
        if (cancel.get()) throw InterruptedException("anulowano")
        if (done.any { it.toInt() != 1 }) throw java.io.IOException("niepełne pobieranie")
        if (dst.exists()) dst.delete()
        if (!part.renameTo(dst)) throw java.io.IOException("nie można zapisać ${dst.name}")
        mapf.delete()
    }
}
