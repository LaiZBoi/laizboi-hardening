package app.clientst0r.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import app.clientst0r.BiometricHelper

/**
 * Full-screen Compose UI displayed while the biometric gate is active.
 *
 * Behaviour:
 *  - If the device supports biometric / device-credential auth, the system
 *    prompt fires automatically on first composition.
 *  - If authentication is not available (no screen lock set), [onAuthenticated]
 *    is called immediately — the app is usable but unsecured.
 *  - On terminal failure (cancel / lockout), an error message and a
 *    "Try Again" button are shown.
 */
@Composable
fun BiometricScreen(onAuthenticated: () -> Unit) {
    val context = LocalContext.current
    val activity = context as FragmentActivity

    var errorMessage   by remember { mutableStateOf<String?>(null) }
    var isAuthenticating by remember { mutableStateOf(false) }

    // Trigger the biometric prompt automatically when this screen first appears
    LaunchedEffect(Unit) {
        if (BiometricHelper.canAuthenticate(context)) {
            isAuthenticating = true
            BiometricHelper.showPrompt(
                activity  = activity,
                onSuccess = {
                    isAuthenticating = false
                    onAuthenticated()
                },
                onError = { error ->
                    isAuthenticating = false
                    errorMessage = error
                },
            )
        } else {
            // No screen lock enrolled — proceed without authentication
            onAuthenticated()
        }
    }

    Surface(
        modifier = Modifier.fillMaxSize(),
        color    = MaterialTheme.colorScheme.background,
    ) {
        Box(
            modifier          = Modifier.fillMaxSize(),
            contentAlignment  = Alignment.Center,
        ) {
            Column(
                horizontalAlignment  = Alignment.CenterHorizontally,
                verticalArrangement  = Arrangement.spacedBy(16.dp),
                modifier             = Modifier.padding(32.dp),
            ) {
                Text(
                    text  = "ClientSt0r",
                    style = MaterialTheme.typography.headlineLarge,
                    color = MaterialTheme.colorScheme.primary,
                )

                Spacer(modifier = Modifier.height(8.dp))

                when {
                    errorMessage != null -> {
                        Text(
                            text  = errorMessage ?: "",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.error,
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Button(
                            onClick = {
                                errorMessage = null
                                isAuthenticating = true
                                BiometricHelper.showPrompt(
                                    activity  = activity,
                                    onSuccess = {
                                        isAuthenticating = false
                                        onAuthenticated()
                                    },
                                    onError = { error ->
                                        isAuthenticating = false
                                        errorMessage = error
                                    },
                                )
                            }
                        ) {
                            Text("Try Again")
                        }
                    }

                    isAuthenticating -> {
                        CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text  = "Waiting for authentication\u2026",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    else -> {
                        CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
                    }
                }
            }
        }
    }
}
