package app.clientst0r

import android.content.Intent
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.fragment.app.FragmentActivity
import app.clientst0r.ui.BiometricScreen
import app.clientst0r.ui.theme.ClientSt0rTheme

/**
 * Entry-point activity.
 *
 * Responsibilities:
 *  1. Install the Android 12+ splash screen.
 *  2. Present the biometric / device-credential gate via Compose.
 *  3. On successful authentication, hand off to [TwaActivity].
 *  4. Forward any App Link deep-link URL to the TWA.
 */
class MainActivity : FragmentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        // Must be called before super.onCreate()
        installSplashScreen()
        super.onCreate(savedInstanceState)

        // If the activity was launched via an App Link, capture the URL
        val deepLinkUrl = intent?.data?.toString()

        setContent {
            ClientSt0rTheme {
                BiometricScreen(
                    onAuthenticated = { launchTwa(deepLinkUrl) }
                )
            }
        }
    }

    /** Handle new App Links while the activity is already running (singleTop). */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        // Re-trigger biometric gate for incoming deep links
        val url = intent.data?.toString()
        setContent {
            ClientSt0rTheme {
                BiometricScreen(
                    onAuthenticated = { launchTwa(url) }
                )
            }
        }
    }

    // -------------------------------------------------------------------------

    private fun launchTwa(url: String? = null) {
        val intent = Intent(this, TwaActivity::class.java).apply {
            if (!url.isNullOrBlank()) {
                putExtra(TwaActivity.EXTRA_URL, url)
            }
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        startActivity(intent)
        finish()
    }
}
