package io.github.nanomuse.todos

/**
 * One account's to-do list. Pure data: the phone store and the tests share it.
 * Items are per account. They stay on this phone.
 */
object TodoList {
    data class Item(
        val id: String,
        val text: String,
        val done: Boolean,
        val createdAt: Long,
    )

    fun add(items: List<Item>, text: String, now: Long, id: String): List<Item> {
        val clean = text.trim()
        if (clean.isEmpty()) return items
        return items + Item(id = id, text = clean.take(500), done = false, createdAt = now)
    }

    /** Null when [id] is not on the list. */
    fun complete(items: List<Item>, id: String): List<Item>? {
        if (items.none { it.id == id }) return null
        return items.map { if (it.id == id) it.copy(done = true) else it }
    }

    /** Null when [id] is not on the list. */
    fun delete(items: List<Item>, id: String): List<Item>? {
        if (items.none { it.id == id }) return null
        return items.filterNot { it.id == id }
    }

    fun format(items: List<Item>, includeDone: Boolean = true): String {
        val rows = items.filter { includeDone || !it.done }
        if (rows.isEmpty()) return "No to-dos."
        return rows.joinToString("\n") { item ->
            val state = if (item.done) "done" else "open"
            "${item.id}\t$state\t${item.text}"
        }
    }
}
