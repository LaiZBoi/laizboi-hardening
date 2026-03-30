package app.clientst0r

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging

/**
 * Application class — initialised once when the process starts.
 *
 * Responsibilities:
 *  - Bootstrap Firebase
 *  - Create the default FCM notification channel (required on API 26+)
 */
class ClientSt0rApp : Application() {

    override fun onCreate() {
        super.onCreate()

        FirebaseApp.initializeApp(this)

        createNotificationChannels()

        // Retrieve (and log) the current FCM registration token on startup.
        // In production, upload this to your server so you can target the device.
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (task.isSuccessful) {
                val token = task.result
                // TODO: Upload token to https://clientst0r.app/api/fcm-tokens/
                android.util.Log.d(TAG, "FCM token: $token")
            }
        }
    }

    // -------------------------------------------------------------------------

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return

        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager

        // Default channel
        NotificationChannel(
            getString(R.string.default_notification_channel_id),
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_DEFAULT
        ).apply {
            description = getString(R.string.notification_channel_description)
            enableVibration(true)
            manager.createNotificationChannel(this)
        }

        // High-priority alerts channel
        NotificationChannel(
            CHANNEL_ALERTS,
            "Alerts",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Urgent system alerts"
            enableVibration(true)
            manager.createNotificationChannel(this)
        }
    }

    companion object {
        private const val TAG = "ClientSt0rApp"
        const val CHANNEL_ALERTS = "clientst0r_alerts"
    }
}
