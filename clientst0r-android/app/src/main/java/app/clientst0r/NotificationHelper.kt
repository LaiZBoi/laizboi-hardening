package app.clientst0r

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

/**
 * Utility for building and posting local notifications.
 *
 * Used by [MyFirebaseMessagingService] to display foreground FCM messages
 * and can be called from anywhere else in the app that needs to surface
 * a notification.
 */
object NotificationHelper {

    /**
     * Build and post a notification.
     *
     * @param context   Application context.
     * @param channelId One of the channel IDs created in [ClientSt0rApp].
     * @param title     Notification title.
     * @param body      Notification body text.
     * @param data      Optional key/value payload from FCM.  If `data["url"]`
     *                  is present, tapping the notification opens that URL in
     *                  the TWA (via [MainActivity]).
     */
    fun show(
        context: Context,
        channelId: String,
        title: String,
        body: String,
        data: Map<String, String> = emptyMap(),
    ) {
        val url = data["url"]

        val tapIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            if (!url.isNullOrBlank()) {
                putExtra("url", url)
            }
        }

        val pendingIntent = PendingIntent.getActivity(
            context,
            System.currentTimeMillis().toInt(),
            tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val priority = if (channelId == ClientSt0rApp.CHANNEL_ALERTS) {
            NotificationCompat.PRIORITY_HIGH
        } else {
            NotificationCompat.PRIORITY_DEFAULT
        }

        val notification = NotificationCompat.Builder(context, channelId)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setContentIntent(pendingIntent)
            .setPriority(priority)
            .build()

        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        // Use a unique ID per notification so they stack rather than replace each other
        manager.notify(System.currentTimeMillis().toInt(), notification)
    }
}
