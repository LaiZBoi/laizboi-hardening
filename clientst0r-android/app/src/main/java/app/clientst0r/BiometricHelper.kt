package app.clientst0r

import android.content.Context
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

/**
 * Thin wrapper around [BiometricPrompt].
 *
 * Supports BIOMETRIC_STRONG (fingerprint / face / iris) with a
 * DEVICE_CREDENTIAL fallback (PIN / pattern / password) so that every
 * device that has any screen lock can use the app.
 */
object BiometricHelper {

    private val AUTHENTICATORS = BIOMETRIC_STRONG or DEVICE_CREDENTIAL

    /**
     * Returns `true` if the device can perform authentication right now.
     * A result of `false` means no screen lock is enrolled — the app will
     * skip the biometric gate.
     */
    fun canAuthenticate(context: Context): Boolean {
        return when (BiometricManager.from(context).canAuthenticate(AUTHENTICATORS)) {
            BiometricManager.BIOMETRIC_SUCCESS -> true
            else -> false
        }
    }

    /**
     * Show the system biometric / device-credential prompt.
     *
     * @param activity   Must be a [FragmentActivity] (MainActivity is one).
     * @param onSuccess  Called on the main thread when authentication succeeds.
     * @param onError    Called on the main thread with a human-readable message
     *                   when authentication fails permanently (e.g. too many
     *                   attempts, user pressed Cancel).
     */
    fun showPrompt(
        activity: FragmentActivity,
        onSuccess: () -> Unit,
        onError: (String) -> Unit,
    ) {
        val executor = ContextCompat.getMainExecutor(activity)

        val callback = object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                onSuccess()
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                // Only called for terminal errors (cancel, lockout, etc.)
                onError(errString.toString())
            }

            override fun onAuthenticationFailed() {
                // Non-terminal failure (bad fingerprint scan) — let the system
                // show its own error UI and allow retry automatically.
            }
        }

        val promptInfo = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Unlock ClientSt0r")
            .setSubtitle("Use your biometric or device credential to continue")
            .setAllowedAuthenticators(AUTHENTICATORS)
            .build()

        BiometricPrompt(activity, executor, callback).authenticate(promptInfo)
    }
}
