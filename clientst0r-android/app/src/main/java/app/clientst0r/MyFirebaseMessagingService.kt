package app.clientst0r

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/**
 * Handles incoming FCM messages and token refresh events.
 *
 * Declared in AndroidManifest with the com.google.firebase.MESSAGING_EVENT
 * intent-filter so Firebase routes messages here automatically.
 */
class MyFirebaseMessagingService : FirebaseMessagingService() {

    /**
     * Called when a new FCM registration token is generated (first launch,
     * or after the old token is invalidated).
     *
     * Upload the token to the server so it can target this device for push
     * notifications.
     */
    override fun onNewToken(token: String) {
        super.onNewToken(token)
        android.util.Log.d(TAG, "Refreshed FCM token: $token")
        // TODO: Upload token to https://clientst0r.app/api/fcm-tokens/
        //       Use the OkHttp client (already a dependency) or DataStore to
        //       persist the token and retry on next launch if upload fails.
    }

    /**
     * Called when a data or notification message arrives while the app is
     * in the foreground (or for data-only messages in the background).
     *
     * For notification messages sent while the app is in the background,
     * Firebase displays the notification automatically using the default
     * channel configured in AndroidManifest.
     */
    override fun onMessageReceived(remoteMessage: RemoteMessage) {
        super.onMessageReceived(remoteMessage)

        val title = remoteMessage.notification?.title
            ?: remoteMessage.data["title"]
            ?: getString(R.string.app_name)

        val body = remoteMessage.notification?.body
            ?: remoteMessage.data["body"]
            ?: return // Nothing to show

        // Use the high-priority alerts channel for messages flagged as alerts
        val channelId = if (remoteMessage.data["alert"] == "true") {
            ClientSt0rApp.CHANNEL_ALERTS
        } else {
            getString(R.string.default_notification_channel_id)
        }

        NotificationHelper.show(
            context  = this,
            channelId = channelId,
            title    = title,
            body     = body,
            data     = remoteMessage.data,
        )
    }

    companion object {
        private const val TAG = "FCMService"
    }
}
