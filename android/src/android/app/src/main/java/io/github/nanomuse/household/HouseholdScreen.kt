package io.github.nanomuse.household

import android.text.format.DateFormat
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.openminis.app.R
import io.github.nanomuse.cloud.NanoMuseCloud
import java.util.Date
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

const val ROUTE_HOUSEHOLD = "hearth/household"

/** Check-ins, panics and alerts for this household. Loaded when the screen opens. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HouseholdScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var reload by remember { mutableIntStateOf(0) }
    var events by remember { mutableStateOf(JSONArray()) }
    var note by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val kindCheckIn = stringResource(R.string.nm_household_kind_check_in)
    val kindPanic = stringResource(R.string.nm_household_kind_panic)
    val kindAlert = stringResource(R.string.nm_household_kind_alert)
    val notLinked = stringResource(R.string.nm_panic_not_linked)
    val needsSignIn = stringResource(R.string.nm_household_needs_sign_in)
    LaunchedEffect(reload) {
        busy = true
        try {
            events = withContext(Dispatchers.IO) { NanoMuseCloud.householdEvents(context) }
            note = null
        } catch (e: NanoMuseCloud.CloudException) {
            events = JSONArray()
            note = when (e.code) {
                "not_linked" -> notLinked
                "signed_out", "bad_key" -> needsSignIn
                else -> e.message
            }
        }
        busy = false
    }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.nm_household_title)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.nm_back))
                    }
                },
            )
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = {
                    if (busy) return@Button
                    scope.launch {
                        busy = true
                        try {
                            withContext(Dispatchers.IO) { NanoMuseCloud.householdCheckIn(context) }
                            reload += 1
                        } catch (e: NanoMuseCloud.CloudException) {
                            note = when (e.code) {
                                "not_linked" -> notLinked
                                "signed_out", "bad_key" -> needsSignIn
                                else -> e.message
                            }
                            busy = false
                        }
                    }
                },
                enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text(stringResource(R.string.nm_household_check_in)) }
            if (note != null) {
                Text(note ?: "")
            } else if (events.length() == 0) {
                Text(stringResource(R.string.nm_household_empty))
            }
            LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items((0 until events.length()).map { events.getJSONObject(it) }) { row ->
                    EventRow(row, kindCheckIn, kindPanic, kindAlert)
                }
            }
        }
    }
}

@Composable
private fun EventRow(row: JSONObject, checkIn: String, panic: String, alert: String) {
    val kind = when (row.optString("kind")) {
        "panic" -> panic
        "alert" -> alert
        else -> checkIn
    }
    val whenText = DateFormat.getDateFormat(LocalContext.current).let { day ->
        val clock = DateFormat.getTimeFormat(LocalContext.current)
        val at = Date(row.optLong("ts") * 1000L)
        day.format(at) + " " + clock.format(at)
    }
    Column {
        Text("$kind · ${row.optString("who")} · $whenText")
        val text = row.optString("text")
        if (text.isNotBlank()) Text(text)
    }
}
