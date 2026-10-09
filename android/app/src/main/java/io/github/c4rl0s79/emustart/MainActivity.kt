package io.github.c4rl0s79.emustart

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient

/** EmuStart na Androida: interfejs (web/) w WebView, pad przez zdarzenia Androida. */
class MainActivity : Activity() {
    lateinit var web: WebView
    private lateinit var bridge: Bridge
    private val ui = Handler(Looper.getMainLooper())

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        web = WebView(this)
        setContentView(web)
        immersive()
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.mediaPlaybackRequiresUserGesture = false
        web.isFocusable = true
        web.setBackgroundColor(0xFF0B0D12.toInt())
        bridge = Bridge(this)
        web.addJavascriptInterface(bridge, "EmuAndroid")
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, req: WebResourceRequest): WebResourceResponse? =
                serve(req.url)
            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean = true
        }
        web.loadUrl("http://emustart.local/index.html?android")
        askStorage()
    }

    /** Interfejs z zasobów aplikacji; /remote/… — grafiki z serwera. */
    private fun serve(uri: Uri): WebResourceResponse? {
        if (uri.host != "emustart.local") return null
        val path = uri.path ?: "/"
        if (path.startsWith("/remote/")) return bridge.media(uri)
        val asset = "web" + (if (path == "/") "/index.html" else path)
        return try {
            WebResourceResponse(Bridge.mime(asset), "utf-8", assets.open(asset))
        } catch (e: Exception) { null }
    }

    /** Gry w publicznym folderze, żeby emulatory mogły je czytać — prośba raz przy starcie. */
    private fun askStorage() {
        if (Environment.isExternalStorageManager()) return
        try {
            startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                                 Uri.parse("package:$packageName")))
        } catch (e: Exception) {
            startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
        }
    }

    fun js(code: String) = web.evaluateJavascript(code, null)

    private fun immersive() {
        window.insetsController?.let {
            it.hide(WindowInsets.Type.systemBars())
            it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    override fun onResume() {
        super.onResume()
        immersive()
        if (::bridge.isInitialized) bridge.onResumed()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) immersive()
    }

    // ── pad ──
    private fun pad(a: String, up: Boolean) = js("window.__emuPad && window.__emuPad('$a', $up)")

    private val repeatable = setOf("up", "down", "left", "right", "lb", "rb", "lt", "rt")

    override fun dispatchKeyEvent(e: KeyEvent): Boolean {
        val a = when (e.keyCode) {
            KeyEvent.KEYCODE_DPAD_UP -> "up"
            KeyEvent.KEYCODE_DPAD_DOWN -> "down"
            KeyEvent.KEYCODE_DPAD_LEFT -> "left"
            KeyEvent.KEYCODE_DPAD_RIGHT -> "right"
            KeyEvent.KEYCODE_BUTTON_A, KeyEvent.KEYCODE_DPAD_CENTER -> "a"
            KeyEvent.KEYCODE_BUTTON_B, KeyEvent.KEYCODE_BACK -> "b"
            KeyEvent.KEYCODE_BUTTON_X -> "x"
            KeyEvent.KEYCODE_BUTTON_Y -> "y"
            KeyEvent.KEYCODE_BUTTON_L1 -> "lb"
            KeyEvent.KEYCODE_BUTTON_R1 -> "rb"
            KeyEvent.KEYCODE_BUTTON_L2 -> "lt"
            KeyEvent.KEYCODE_BUTTON_R2 -> "rt"
            KeyEvent.KEYCODE_BUTTON_START, KeyEvent.KEYCODE_MENU -> "start"
            KeyEvent.KEYCODE_BUTTON_SELECT -> "select"
            else -> null
        } ?: return super.dispatchKeyEvent(e)
        val fromPad = (e.source and InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD ||
            (e.source and InputDevice.SOURCE_DPAD) == InputDevice.SOURCE_DPAD ||
            e.keyCode == KeyEvent.KEYCODE_BACK || e.keyCode == KeyEvent.KEYCODE_MENU
        if (!fromPad) return super.dispatchKeyEvent(e)          // zwykła klawiatura — do pola tekstowego
        when (e.action) {
            KeyEvent.ACTION_DOWN -> if (e.repeatCount == 0 || a in repeatable) pad(a, false)
            KeyEvent.ACTION_UP -> pad(a, true)
        }
        return true
    }

    // gałka i krzyżak jako osie (część padów), spusty analogowe
    private var stickDir: String? = null
    private var ltDown = false
    private var rtDown = false
    private val repeatTask = object : Runnable {
        override fun run() {
            stickDir?.let { pad(it, false); ui.postDelayed(this, 90) }
        }
    }

    override fun dispatchGenericMotionEvent(e: MotionEvent): Boolean {
        val joy = (e.source and InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK
        if (!joy || e.action != MotionEvent.ACTION_MOVE) return super.dispatchGenericMotionEvent(e)
        val x = e.getAxisValue(MotionEvent.AXIS_X).let { if (Math.abs(it) > 0.6f) it else e.getAxisValue(MotionEvent.AXIS_HAT_X) }
        val y = e.getAxisValue(MotionEvent.AXIS_Y).let { if (Math.abs(it) > 0.6f) it else e.getAxisValue(MotionEvent.AXIS_HAT_Y) }
        val dir = when {
            y < -0.6f -> "up"; y > 0.6f -> "down"; x < -0.6f -> "left"; x > 0.6f -> "right"; else -> null
        }
        if (dir != stickDir) {
            stickDir?.let { pad(it, true) }
            ui.removeCallbacks(repeatTask)
            stickDir = dir
            if (dir != null) { pad(dir, false); ui.postDelayed(repeatTask, 350) }
        }
        val lt = maxOf(e.getAxisValue(MotionEvent.AXIS_LTRIGGER), e.getAxisValue(MotionEvent.AXIS_BRAKE)) > 0.5f
        val rt = maxOf(e.getAxisValue(MotionEvent.AXIS_RTRIGGER), e.getAxisValue(MotionEvent.AXIS_GAS)) > 0.5f
        if (lt != ltDown) { ltDown = lt; pad("lt", !lt) }
        if (rt != rtDown) { rtDown = rt; pad("rt", !rt) }
        return true
    }
}
