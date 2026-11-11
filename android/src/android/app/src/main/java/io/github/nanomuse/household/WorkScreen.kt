package io.github.nanomuse.household

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.openminis.app.R
import io.github.nanomuse.cloud.NanoMuseCloud
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

const val ROUTE_WORK = "hearth/work"

/** Admin sees every list and can assign. Staff sees her own list, her notes, and the shared board. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WorkScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var reload by remember { mutableIntStateOf(0) }
    var work by remember { mutableStateOf<JSONObject?>(null) }
    var note by remember { mutableStateOf<String?>(null) }
    val adminWord = stringResource(R.string.nm_work_title_admin)
    val staffWord = stringResource(R.string.nm_work_title_staff)
    val notLinked = stringResource(R.string.nm_panic_not_linked)
    val needsSignIn = stringResource(R.string.nm_household_needs_sign_in)
    LaunchedEffect(reload) {
        try {
            work = withContext(Dispatchers.IO) { NanoMuseCloud.householdWork(context) }
            note = null
        } catch (e: NanoMuseCloud.CloudException) {
            work = null
            note = when (e.code) {
                "not_linked" -> notLinked
                "signed_out", "bad_key" -> needsSignIn
                else -> e.message
            }
        }
    }
    val role = work?.optString("role").orEmpty()
    val admin = role == "admin"
    fun act(block: suspend () -> Unit) {
        scope.launch {
            try {
                withContext(Dispatchers.IO) { block() }
                note = null
                reload += 1
            } catch (e: NanoMuseCloud.CloudException) {
                note = e.message
            }
        }
    }
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        when (role) {
                            "admin" -> adminWord
                            "staff" -> staffWord
                            else -> stringResource(R.string.nm_work_menu)
                        },
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.nm_back))
                    }
                },
            )
        },
    ) { padding ->
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(padding).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            if (admin) Text(adminWord, fontSize = 28.sp, fontWeight = FontWeight.Bold)
            else if (role == "staff") Text(staffWord, fontSize = 28.sp, fontWeight = FontWeight.Bold)
            if (note != null) Text(note ?: "")
            val data = work
            if (data != null) {
                Board(data, admin, ::act)
                val people = data.optJSONArray("people") ?: JSONArray()
                for (i in 0 until people.length()) {
                    val person = people.getJSONObject(i)
                    if (!person.has("tasks")) continue
                    Person(person, ::act)
                }
            }
        }
    }
}

@Composable
private fun Board(data: JSONObject, admin: Boolean, act: (suspend () -> Unit) -> Unit) {
    val context = LocalContext.current
    var draft by remember { mutableStateOf("") }
    Text(stringResource(R.string.nm_work_board), fontSize = 22.sp, fontWeight = FontWeight.Bold)
    Row(verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(
            value = draft,
            onValueChange = { draft = it },
            modifier = Modifier.weight(1f),
            label = { Text(stringResource(R.string.nm_work_add)) },
        )
        Button(onClick = {
            val text = draft.trim()
            if (text.isEmpty()) return@Button
            draft = ""
            act { NanoMuseCloud.householdAddShared(context, text) }
        }) { Text(stringResource(R.string.nm_work_add)) }
    }
    val board = data.optJSONArray("board") ?: JSONArray()
    val people = data.optJSONArray("people") ?: JSONArray()
    for (i in 0 until board.length()) {
        val task = board.getJSONObject(i)
        val who = task.optString("assignee").ifBlank { stringResource(R.string.nm_work_open) }
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Checkbox(
                checked = task.optBoolean("done"),
                onCheckedChange = { done -> act { NanoMuseCloud.householdMarkShared(context, task.optString("id"), done) } },
            )
            Column(Modifier.weight(1f)) {
                Text(task.optString("text"), fontSize = 18.sp)
                Text(who)
            }
        }
        if (admin) {
            Text(stringResource(R.string.nm_work_assign))
            Row {
                for (p in 0 until people.length()) {
                    val person = people.getJSONObject(p)
                    TextButton(onClick = {
                        act { NanoMuseCloud.householdAssign(context, task.optString("id"), person.optString("account_id")) }
                    }) { Text(person.optString("who")) }
                }
            }
        }
    }
}

@Composable
private fun Person(person: JSONObject, act: (suspend () -> Unit) -> Unit) {
    val context = LocalContext.current
    var notes by remember(person.optString("account_id"), person.optString("notes")) { mutableStateOf(person.optString("notes")) }
    var draft by remember(person.optString("account_id")) { mutableStateOf("") }
    Text(person.optString("who"), fontSize = 22.sp, fontWeight = FontWeight.Bold)
    Text(stringResource(R.string.nm_work_notes), fontSize = 18.sp)
    OutlinedTextField(value = notes, onValueChange = { notes = it }, modifier = Modifier.fillMaxWidth(), minLines = 3)
    Button(onClick = {
        act { NanoMuseCloud.householdSaveNotes(context, person.optString("account_id"), notes) }
    }) { Text(stringResource(R.string.nm_work_save)) }
    Text(stringResource(R.string.nm_work_mine), fontSize = 18.sp)
    val tasks = person.optJSONArray("tasks") ?: JSONArray()
    if (tasks.length() == 0) Text(stringResource(R.string.nm_work_empty))
    for (i in 0 until tasks.length()) {
        val task = tasks.getJSONObject(i)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(
                checked = task.optBoolean("done"),
                onCheckedChange = { done ->
                    act { NanoMuseCloud.householdTogglePrivate(context, person.optString("account_id"), task.optString("id"), done) }
                },
            )
            Text(task.optString("text"), fontSize = 18.sp)
        }
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(value = draft, onValueChange = { draft = it }, modifier = Modifier.weight(1f))
        Button(onClick = {
            val text = draft.trim()
            if (text.isEmpty()) return@Button
            draft = ""
            act { NanoMuseCloud.householdAddPrivate(context, person.optString("account_id"), text) }
        }) { Text(stringResource(R.string.nm_work_add))         }
    }
}
