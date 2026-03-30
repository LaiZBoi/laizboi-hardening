import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.gms.google-services")
}

// ---------------------------------------------------------------------------
// Release signing — reads from keystore.properties (not committed to VCS)
// ---------------------------------------------------------------------------
val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.exists()) load(keystorePropertiesFile.inputStream())
}

android {
    namespace   = "app.clientst0r"
    compileSdk  = 34

    defaultConfig {
        applicationId   = "app.clientst0r"
        minSdk          = 24      // Android 7.0 — ~97 % of active devices
        targetSdk       = 34
        versionCode     = 1
        versionName     = "1.0.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        vectorDrawables { useSupportLibrary = true }

        // Inject build-time constants
        buildConfigField("String", "BASE_URL", "\"https://clientst0r.app\"")
    }

    // ---------------------------------------------------------------------------
    // Signing configs
    // ---------------------------------------------------------------------------
    signingConfigs {
        create("release") {
            if (keystorePropertiesFile.exists()) {
                keyAlias      = keystoreProperties["keyAlias"]      as? String
                keyPassword   = keystoreProperties["keyPassword"]   as? String
                storeFile     = keystoreProperties["storeFile"]?.let { file(it as String) }
                storePassword = keystoreProperties["storePassword"] as? String
            }
        }
    }

    // ---------------------------------------------------------------------------
    // Build types
    // ---------------------------------------------------------------------------
    buildTypes {
        release {
            isMinifyEnabled    = true
            isShrinkResources  = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            signingConfig = signingConfigs.getByName("release")
        }
        debug {
            isDebuggable          = true
            applicationIdSuffix   = ".debug"
            versionNameSuffix     = "-debug"
            // Keep R8 off in debug for faster builds
            isMinifyEnabled       = false
        }
    }

    // ---------------------------------------------------------------------------
    // Compile options
    // ---------------------------------------------------------------------------
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions { jvmTarget = "17" }

    buildFeatures {
        compose     = true
        buildConfig = true
    }

    composeOptions {
        // Must match Kotlin version 1.9.22
        kotlinCompilerExtensionVersion = "1.5.8"
    }

    packaging {
        resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    }
}

// ---------------------------------------------------------------------------
// Dependencies
// ---------------------------------------------------------------------------
dependencies {
    // ---- Compose BOM ---------------------------------------------------------
    val composeBom = platform("androidx.compose:compose-bom:2024.02.00")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.animation:animation")

    // ---- Activity + Lifecycle ------------------------------------------------
    implementation("androidx.activity:activity-compose:1.8.2")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")

    // ---- Core ----------------------------------------------------------------
    implementation("androidx.core:core-ktx:1.12.0")

    // ---- Splash Screen API (Android 12+ compatible back to API 23) -----------
    implementation("androidx.core:core-splashscreen:1.0.1")

    // ---- Biometric -----------------------------------------------------------
    implementation("androidx.biometric:biometric:1.1.0")

    // ---- Trusted Web Activity ------------------------------------------------
    implementation("com.google.androidbrowserhelper:androidbrowserhelper:2.5.0")

    // ---- Firebase (BOM manages versions) -------------------------------------
    implementation(platform("com.google.firebase:firebase-bom:32.7.2"))
    implementation("com.google.firebase:firebase-messaging-ktx")
    implementation("com.google.firebase:firebase-analytics-ktx")
    implementation("com.google.firebase:firebase-installations-ktx")

    // ---- DataStore (persist settings / FCM token) ----------------------------
    implementation("androidx.datastore:datastore-preferences:1.0.0")

    // ---- Coroutines ----------------------------------------------------------
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.7.3")

    // ---- Security (EncryptedSharedPreferences for tokens) --------------------
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    // ---- Network (for token upload to server) --------------------------------
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // ---- Testing -------------------------------------------------------------
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.1")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
