package io.github.nanomuse.todos

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TodoListTest {
    @Test
    fun `add list complete and delete stay on this list`() {
        val added = TodoList.add(emptyList(), "  buy milk  ", now = 1, id = "a")
        assertEquals(1, added.size)
        assertEquals("buy milk", added[0].text)
        assertFalse(added[0].done)
        assertEquals(emptyList<TodoList.Item>(), TodoList.add(emptyList(), "   ", 1, "x"))

        val done = TodoList.complete(added, "a")!!
        assertTrue(done[0].done)
        assertNull(TodoList.complete(added, "missing"))

        val gone = TodoList.delete(done, "a")!!
        assertTrue(gone.isEmpty())
        assertNull(TodoList.delete(done, "missing"))
        assertTrue(TodoList.format(emptyList()).contains("No to-dos"))
        assertTrue(TodoList.format(added).contains("buy milk"))
    }
}
