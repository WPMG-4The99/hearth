package io.github.nanomuse.panic

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.openminis.app.R
import io.github.nanomuse.hub.Hub
import io.github.nanomuse.hub.HubException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * Sends a panic to the linked household phone. The first tap arms it. The second tap, within
 * a few seconds, sends. One tap does nothing.
 */
@Composable
fun PanicButton(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var armed by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<String?>(null) }
    val delivered = stringResource(R.string.nm_panic_delivered)
    val offline = stringResource(R.string.nm_panic_not_delivered)
    val unlinked = stringResource(R.string.nm_panic_not_linked)
    val disconnected = stringResource(R.string.nm_panic_not_connected)
    LaunchedEffect(armed) {
        if (armed) {
            delay(4_000)
            armed = false
        }
    }
    TextButton(
        onClick = {
            if (busy) return@TextButton
            if (!armed) {
                armed = true
                return@TextButton
            }
            armed = false
            busy = true
            scope.launch {
                val line = withContext(Dispatchers.IO) { send(context, delivered, offline, unlinked, disconnected) }
                busy = false
                result = line
            }
        },
        enabled = !busy,
        modifier = modifier
            .clip(RoundedCornerShape(20.dp))
            .background(if (armed) Color(0xFF8C1D18) else Color(0xFFB3261E)),
    ) {
        Text(
            text = when {
                busy -> stringResource(R.string.nm_panic_sending)
                armed -> stringResource(R.string.nm_panic_confirm)
                else -> stringResource(R.string.nm_panic)
            },
            color = Color.White,
            fontWeight = FontWeight.Bold,
            fontSize = 13.sp,
            modifier = Modifier.padding(horizontal = 4.dp),
        )
    }
    result?.let { line ->
        AlertDialog(
            onDismissRequest = { result = null },
            confirmButton = {
                TextButton(onClick = { result = null }) { Text(stringResource(R.string.nm_panic_close)) }
            },
            title = { Text(stringResource(R.string.nm_panic)) },
            text = { Text(line) },
        )
    }
}

private fun send(context: android.content.Context, delivered: String, offline: String, unlinked: String, disconnected: String): String {
    if (!Hub.isConnected) return disconnected
    return try {
        Hub.call("household", "panic", JSONObject().put("text", "panic"), timeoutMs = 20_000)
        delivered
    } catch (e: HubException) {
        when (e.code) {
            "not_linked" -> unlinked
            "device_offline", "timeout", "disconnected" -> offline
            else -> offline
        }
    }
}
