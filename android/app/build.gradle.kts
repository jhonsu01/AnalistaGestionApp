import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// La version sale del fichero VERSION de la raiz: una sola fuente de verdad
// para el MSI, el APK y la release de GitHub.
val versionFile = rootProject.file("../VERSION")
val appVersion = if (versionFile.exists()) versionFile.readText().trim() else "0.0.1"
val (maj, min, pat) = appVersion.split(".").map { it.toIntOrNull() ?: 0 }

android {
    namespace = "com.jhonsu01.analista"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.jhonsu01.analista"
        minSdk = 24          // Android 7: cubre practicamente cualquier tablet viva
        targetSdk = 34
        versionCode = maj * 10000 + min * 100 + pat
        versionName = appVersion
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
}
