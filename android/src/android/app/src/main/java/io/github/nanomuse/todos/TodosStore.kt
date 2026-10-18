package io.github.nanomuse.todos

import android.content.Context
import io.github.nanomuse.account.AccountData
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/** This account's to-dos, on this phone. A different account does not see them. */
object TodosStore {
    private const val PREFS = "hearth.todos"

    fun list(context: Context, includeDone: Boolean = true): List<TodoList.Item> {
        val all = read(context)
        return if (includeDone) all else all.filterNot { it.done }
    }

    fun add(context: Context, text: String): TodoList.Item? {
        val clean = text.trim()
        if (clean.isEmpty()) return null
        val items = read(context)
        val item = TodoList.Item(
            id = UUID.randomUUID().toString().replace("-", "").take(8),
            text = clean.take(500),
            done = false,
            createdAt = System.currentTimeMillis(),
        )
        write(context, TodoList.add(items, item.text, item.createdAt, item.id))
        return item
    }

    fun complete(context: Context, id: String): Boolean {
        val next = TodoList.complete(read(context), id.trim()) ?: return false
        write(context, next)
        return true
    }

    fun delete(context: Context, id: String): Boolean {
        val next = TodoList.delete(read(context), id.trim()) ?: return false
        write(context, next)
        return true
    }

    private fun accountKey(context: Context): String = AccountData.key(context).ifBlank { "local" }

    private fun read(context: Context): List<TodoList.Item> {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(accountKey(context), null) ?: return emptyList()
        return runCatching {
            val arr = JSONArray(raw)
            (0 until arr.length()).mapNotNull { i ->
                val o = arr.optJSONObject(i) ?: return@mapNotNull null
                val id = o.optString("id")
                val text = o.optString("text")
                if (id.isBlank() || text.isBlank()) null
                else TodoList.Item(id, text, o.optBoolean("done"), o.optLong("createdAt"))
            }
        }.getOrDefault(emptyList())
    }

    private fun write(context: Context, items: List<TodoList.Item>) {
        val arr = JSONArray()
        items.forEach { item ->
            arr.put(
                JSONObject()
                    .put("id", item.id)
                    .put("text", item.text)
                    .put("done", item.done)
                    .put("createdAt", item.createdAt),
            )
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(accountKey(context), arr.toString()).apply()
    }
}
