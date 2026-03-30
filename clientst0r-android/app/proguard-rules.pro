# ---- ClientSt0r ProGuard Rules -----------------------------------------------

# Preserve line numbers in stack traces for debugging
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# ---------------------------------------------------------------------------
# Biometric
# ---------------------------------------------------------------------------
-keep class androidx.biometric.** { *; }

# ---------------------------------------------------------------------------
# Trusted Web Activity / Custom Tabs
# ---------------------------------------------------------------------------
-keep class com.google.androidbrowserhelper.** { *; }
-keep class androidx.browser.** { *; }

# ---------------------------------------------------------------------------
# Firebase
# ---------------------------------------------------------------------------
-keep class com.google.firebase.** { *; }
-keep class com.google.android.gms.** { *; }
-dontwarn com.google.firebase.**
-dontwarn com.google.android.gms.**

# ---------------------------------------------------------------------------
# Kotlin
# ---------------------------------------------------------------------------
-keep class kotlin.Metadata { *; }
-dontwarn kotlin.**
-keepclassmembers class **$WhenMappings { <fields>; }
-keepclassmembers class kotlin.Lazy { *; }

# ---------------------------------------------------------------------------
# Coroutines
# ---------------------------------------------------------------------------
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory { *; }
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler { *; }
-keepclassmembernames class kotlinx.** {
    volatile <fields>;
}

# ---------------------------------------------------------------------------
# OkHttp
# ---------------------------------------------------------------------------
-dontwarn okhttp3.**
-dontwarn okio.**
-keep class okhttp3.** { *; }
-keep interface okhttp3.** { *; }

# ---------------------------------------------------------------------------
# DataStore / Protobuf
# ---------------------------------------------------------------------------
-keep class androidx.datastore.** { *; }

# ---------------------------------------------------------------------------
# Application class
# ---------------------------------------------------------------------------
-keep class app.clientst0r.ClientSt0rApp { *; }
