package io.github.nanomuse.todos

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Delete
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import com.openminis.app.R

const val ROUTE_TODOS = "hearth/todos"

/** The list Nix edits with `hearth-todos`, for the person holding the phone. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TodosScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    var items by remember { mutableStateOf(TodosStore.list(context)) }
    var draft by remember { mutableStateOf("") }
    fun reload() { items = TodosStore.list(context) }
    fun add() {
        if (TodosStore.add(context, draft) != null) {
            draft = ""
            reload()
        }
    }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.nm_todos_title)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.settings_back))
                    }
                },
            )
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                OutlinedTextField(
                    value = draft,
                    onValueChange = { draft = it },
                    placeholder = { Text(stringResource(R.string.nm_todos_hint)) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { add() }),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { add() }, enabled = draft.isNotBlank()) {
                    Text(stringResource(R.string.nm_todos_add))
                }
            }
            if (items.isEmpty()) {
                Text(
                    stringResource(R.string.nm_todos_empty),
                    modifier = Modifier.padding(top = 24.dp),
                )
            } else {
                LazyColumn(Modifier.fillMaxSize().padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    items(items, key = { it.id }) { item ->
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                            Checkbox(
                                checked = item.done,
                                onCheckedChange = {
                                    TodosStore.complete(context, item.id)
                                    reload()
                                },
                                enabled = !item.done,
                            )
                            Text(
                                item.text,
                                modifier = Modifier.weight(1f),
                                textDecoration = if (item.done) TextDecoration.LineThrough else null,
                            )
                            IconButton(onClick = { TodosStore.delete(context, item.id); reload() }) {
                                Icon(Icons.Outlined.Delete, contentDescription = stringResource(R.string.nm_todos_delete))
                            }
                        }
                    }
                }
            }
        }
    }
}
