package app.clientst0r.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

private val DarkColorScheme = darkColorScheme(
    primary            = Blue400,
    onPrimary          = Navy900,
    primaryContainer   = Navy700,
    onPrimaryContainer = Blue200,
    background         = Navy900,
    onBackground       = OnSurface,
    surface            = Surface,
    onSurface          = OnSurface,
    surfaceVariant     = Navy800,
    onSurfaceVariant   = Blue200,
    outline            = Outline,
    error              = ErrorRed,
)

/**
 * Root Compose theme for ClientSt0r.
 *
 * Uses a static dark colour scheme that matches the web app palette.
 * Dynamic colour (Material You) is intentionally disabled so the branding
 * is consistent regardless of the device wallpaper.
 */
@Composable
fun ClientSt0rTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColorScheme,
        typography  = Typography,
        content     = content,
    )
}
