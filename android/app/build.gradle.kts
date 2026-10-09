import java.util.Base64

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// wersja jak w EmuStart na Windows (emustart/__init__.py)
val emuVersion: String = Regex("__version__ = \"([0-9.]+)\"")
    .find(rootDir.resolve("../emustart/__init__.py").readText())?.groupValues?.get(1) ?: "0.0.0"
val emuCode: Int = emuVersion.split(".").map { it.toInt() }.let { it[0] * 10000 + it[1] * 100 + it[2] }

// podpis: klucz z sekretów GitHuba (ANDROID_KEYSTORE_B64 itd.); bez nich — klucz debug
val ksB64: String? = System.getenv("ANDROID_KEYSTORE_B64")
val ksFile = layout.buildDirectory.file("emustart.keystore").get().asFile
if (!ksB64.isNullOrBlank()) {
    ksFile.parentFile.mkdirs()
    ksFile.writeBytes(Base64.getDecoder().decode(ksB64.trim()))
}

android {
    namespace = "io.github.c4rl0s79.emustart"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.c4rl0s79.emustart"
        minSdk = 30
        targetSdk = 35
        versionCode = emuCode
        versionName = emuVersion
    }

    signingConfigs {
        if (!ksB64.isNullOrBlank()) {
            create("release") {
                storeFile = ksFile
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                // nieustawiony sekret GitHuba przychodzi jako pusty tekst, nie jako brak
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")?.takeIf { it.isNotBlank() } ?: "emustart"
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")?.takeIf { it.isNotBlank() }
                    ?: System.getenv("ANDROID_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (!ksB64.isNullOrBlank()) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }

    buildFeatures { buildConfig = true }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    // interfejs EmuStart (web/) — ten sam co na Windows, kopiowany przy budowaniu
    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/webassets"))
}

val copyWeb by tasks.registering(Copy::class) {
    from(rootDir.resolve("../web"))
    into(layout.buildDirectory.dir("generated/webassets/web"))
}
tasks.named("preBuild") { dependsOn(copyWeb) }

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")      // FileProvider (gry dla emulatorów)
}
