package io.github.nanomuse.deeplink

import android.content.Context
import android.content.Intent
import android.net.Uri

/**
 * The names of the app's own links.
 *
 * Inside the app a link is `minis://…`: that is upstream's vocabulary, shared with the sandbox
 * and with the model, and it stays. What the app registers with Android is `hearth`
 * (`AndroidManifest.xml`). A `hearth://…` link, and a leftover `nanomuse://…` link, mean the
 * same as the `minis://…` form. A link the app opens on itself goes to its own activity by
 * name, never through the system's resolution of a scheme.
 */
object OwnScheme {
    /** What the manifest registers: the name other apps and web pages can address. */
    const val SYSTEM = "hearth"

    /** Upstream's in-app vocabulary; never registered with the system. */
    const val INTERNAL = "minis"

    /** The previous public scheme. Read if it arrives; not registered, so it is not claimed. */
    const val LEGACY = "nanomuse"

    /** The activity that takes every deep link, named so no scheme resolution is involved. */
    const val ACTIVITY = "com.openminis.app.MainActivity"

    /** Whether [scheme] is one of the app's own names. */
    fun isOwn(scheme: String?): Boolean {
        val s = scheme?.lowercase() ?: return false
        return s == INTERNAL || s == SYSTEM || s == LEGACY
    }

    /** `hearth://x` or `nanomuse://x` read as `minis://x`; any other scheme is left as written. */
    fun internal(scheme: String?): String? {
        val s = scheme?.lowercase()
        return if (s == SYSTEM || s == LEGACY) INTERNAL else scheme
    }

    /** The `minis://…` form of [url] when it is a Hearth or legacy link; otherwise [url] itself. */
    fun internalUrl(url: String): String {
        for (name in listOf(SYSTEM, LEGACY)) {
            val prefix = "$name://"
            if (url.regionMatches(0, prefix, 0, prefix.length, ignoreCase = true)) {
                return "$INTERNAL://" + url.substring(prefix.length)
            }
        }
        return url
    }

    /** A VIEW intent for [uri] addressed to the app's own activity; [flags] as the caller needs. */
    fun intent(context: Context, uri: Uri, flags: Int = Intent.FLAG_ACTIVITY_NEW_TASK): Intent =
        Intent(Intent.ACTION_VIEW, uri).apply {
            setClassName(context.packageName, ACTIVITY)
            addFlags(flags)
        }
}
