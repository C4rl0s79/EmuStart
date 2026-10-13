package io.github.c4rl0s79.emustart

import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.X509TrustManager

/**
 * Połączenia z serwerem EmuStart. HTTPS (port szyfrowany, emustart/tls.py): serwer ma własny
 * certyfikat, więc zaufanie = zgodny odcisk SHA-256 certyfikatu zapamiętany przy zalogowaniu
 * („przypięcie”), a nie urząd certyfikacji. Inne adresy (np. GitHub) — zwykłe sprawdzanie.
 */
object Net {
    private val factories = HashMap<String, SSLSocketFactory>()

    /** Jeden obiekt na cały program: pula połączeń Androida (OkHttp) używa połączenia ponownie
     *  tylko przy tym samym SSLSocketFactory i HostnameVerifier. Nowa lambda przy każdym
     *  otwarciu = nowe TCP + TLS + wolny start na każdy 4-MB blok gry (0.43–0.44: wolne
     *  pobieranie). O zaufaniu decyduje odcisk certyfikatu (Pin). */
    private val pinnedHosts = javax.net.ssl.HostnameVerifier { _, _ -> true }

    /** Połączenie z adresem serwera; `fp` — odcisk certyfikatu (wymagany dla https z własnym
     *  certyfikatem serwera); `ca` — zwykłe sprawdzanie certyfikatu (tunel Cloudflare). */
    fun open(url: String, fp: String, ca: Boolean = false): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        if (c is HttpsURLConnection && !ca) {
            if (fp.isEmpty()) throw javax.net.ssl.SSLException("brak zapamiętanego certyfikatu serwera — połącz telefon z serwerem ponownie")
            c.sslSocketFactory = factory(fp)
            c.hostnameVerifier = pinnedHosts      // o zaufaniu decyduje odcisk; ten sam obiekt — pula działa
        }
        return c
    }

    @Synchronized private fun factory(fp: String): SSLSocketFactory = factories.getOrPut(fp) {
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf(Pin(fp)), null)
        ctx.socketFactory
    }

    fun sha256(der: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }

    private class Pin(private val fp: String) : X509TrustManager {
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            if (chain.isEmpty() || sha256(chain[0].encoded) != fp)
                throw CertificateException("certyfikat serwera nie zgadza się z zapamiętanym — połącz telefon z serwerem ponownie")
        }
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
            throw CertificateException("nieużywane")
        override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
    }
}
