package io.github.nanomuse.todos

import android.content.Context
import com.openminis.app.sandbox.NativeOffloadHandler
import com.openminis.app.sandbox.NativeOffloadRequest
import com.openminis.app.sandbox.NativeOffloadResult

/**
 * `hearth-todos` for Nix: add, list, complete, and delete this account's to-dos.
 *
 *   hearth-todos list
 *   hearth-todos add --text "buy milk"
 *   hearth-todos complete --id <id>
 *   hearth-todos delete --id <id>
 */
class TodosOffloadHandler(private val context: Context) : NativeOffloadHandler {
    override fun handle(request: NativeOffloadRequest): NativeOffloadResult {
        val argv = request.argv.dropWhile { it == "hearth-todos" || it.endsWith("/hearth-todos") }
        val sub = argv.firstOrNull()?.lowercase() ?: "list"
        val rest = argv.drop(1)
        return when (sub) {
            "list", "--help", "help" -> if (sub == "list") list(rest) else NativeOffloadResult(0, HELP)
            "add" -> add(rest)
            "complete", "done" -> complete(rest)
            "delete", "remove" -> delete(rest)
            else -> NativeOffloadResult(2, "hearth-todos: unknown subcommand '$sub'\n$HELP")
        }
    }

    private fun list(rest: List<String>): NativeOffloadResult {
        val openOnly = rest.any { it == "--open" }
        return NativeOffloadResult(0, TodoList.format(TodosStore.list(context, includeDone = !openOnly)) + "\n")
    }

    private fun add(rest: List<String>): NativeOffloadResult {
        val text = value(rest, "text") ?: rest.filterNot { it.startsWith("-") }.joinToString(" ").trim()
        if (text.isBlank()) return NativeOffloadResult(2, "hearth-todos add: --text is required\n")
        val item = TodosStore.add(context, text) ?: return NativeOffloadResult(2, "hearth-todos add: --text is required\n")
        return NativeOffloadResult(0, "added ${item.id}\t${item.text}\n")
    }

    private fun complete(rest: List<String>): NativeOffloadResult {
        val id = value(rest, "id") ?: rest.firstOrNull { !it.startsWith("-") }
            ?: return NativeOffloadResult(2, "hearth-todos complete: --id is required\n")
        return if (TodosStore.complete(context, id)) NativeOffloadResult(0, "completed $id\n")
        else NativeOffloadResult(1, "hearth-todos: no to-do with id $id\n")
    }

    private fun delete(rest: List<String>): NativeOffloadResult {
        val id = value(rest, "id") ?: rest.firstOrNull { !it.startsWith("-") }
            ?: return NativeOffloadResult(2, "hearth-todos delete: --id is required\n")
        return if (TodosStore.delete(context, id)) NativeOffloadResult(0, "deleted $id\n")
        else NativeOffloadResult(1, "hearth-todos: no to-do with id $id\n")
    }

    private fun value(argv: List<String>, name: String): String? {
        val flag = "--$name"
        argv.forEachIndexed { i, a ->
            if (a == flag) return argv.getOrNull(i + 1)?.takeIf { !it.startsWith("-") }
            if (a.startsWith("$flag=")) return a.substring(flag.length + 1).ifBlank { null }
        }
        return null
    }

    private companion object {
        const val HELP = """hearth-todos — this account's to-do list on this phone
  hearth-todos list [--open]
  hearth-todos add --text "what needs doing"
  hearth-todos complete --id <id>
  hearth-todos delete --id <id>
"""
    }
}
