package app.clientst0r

import android.net.Uri
import com.google.androidbrowserhelper.trusted.LauncherActivity

/**
 * Trusted Web Activity that loads https://clientst0r.app.
 *
 * Extends [LauncherActivity] from the Android Browser Helper library.
 * The default URL is declared in AndroidManifest as meta-data
 * (android.support.customtabs.trusted.DEFAULT_URL).
 *
 * When launched from [MainActivity] with a deep-link URL the TWA opens
 * that URL directly instead of the default root.
 */
class TwaActivity : LauncherActivity() {

    /**
     * Return the URL to open. If [MainActivity] forwarded a deep-link URL
     * via [EXTRA_URL], use it; otherwise fall back to the manifest default.
     */
    override fun getLaunchingUrl(): Uri {
        val urlExtra = intent?.getStringExtra(EXTRA_URL)
        return if (!urlExtra.isNullOrBlank()) {
            Uri.parse(urlExtra)
        } else {
            super.getLaunchingUrl()
        }
    }

    companion object {
        /** Intent extra key for an optional override URL. */
        const val EXTRA_URL = "extra_url"
    }
}
