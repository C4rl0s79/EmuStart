package io.github.c4rl0s79.emustart

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * Klucz urządzenia (EC P-256) do logowania podpisem (emustart/enroll.py na serwerze).
 * Klucz prywatny powstaje w Android Keystore (StrongBox, gdy telefon go ma) i nie da się go
 * wyeksportować — skopiowanie danych aplikacji na inny telefon nie przenosi tożsamości.
 */
object DeviceKey {
    private const val STORE = "AndroidKeyStore"
    private const val TAG = "EmuStart"

    /** Nowy klucz → (rodzaj: strongbox|keystore, klucz publiczny PEM). */
    fun create(alias: String): Pair<String, String> {
        fun gen(strong: Boolean): java.security.KeyPair {
            val b = KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
            if (strong && Build.VERSION.SDK_INT >= 28) b.setIsStrongBoxBacked(true)
            val g = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, STORE)
            g.initialize(b.build())
            return g.generateKeyPair()
        }
        var kind = "strongbox"
        val kp = try {
            if (Build.VERSION.SDK_INT >= 28) gen(true) else { kind = "keystore"; gen(false) }
        } catch (e: Exception) {
            Log.i(TAG, "StrongBox niedostępny ($e) — Android Keystore")
            kind = "keystore"
            gen(false)
        }
        val pem = "-----BEGIN PUBLIC KEY-----\n" +
            Base64.encodeToString(kp.public.encoded, Base64.NO_WRAP).chunked(64).joinToString("\n") +
            "\n-----END PUBLIC KEY-----\n"
        return Pair(kind, pem)
    }

    /** Podpis ECDSA-SHA256 (DER, Base64). */
    fun sign(alias: String, data: ByteArray): String {
        val ks = KeyStore.getInstance(STORE).apply { load(null) }
        val key = ks.getKey(alias, null) as PrivateKey
        val s = Signature.getInstance("SHA256withECDSA")
        s.initSign(key)
        s.update(data)
        return Base64.encodeToString(s.sign(), Base64.NO_WRAP)
    }

    fun delete(alias: String) {
        if (alias.isEmpty()) return
        try { KeyStore.getInstance(STORE).apply { load(null) }.deleteEntry(alias) } catch (e: Exception) { }
    }
}
