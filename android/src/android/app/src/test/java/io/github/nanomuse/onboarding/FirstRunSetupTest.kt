package io.github.nanomuse.onboarding

import io.github.nanomuse.ui.onboarding.FirstRunSetup
import io.github.nanomuse.ui.onboarding.FirstRunSetup.Stage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FirstRunSetupTest {

    @Test
    fun `sign-in is a wall, and the setup follows a new account until Start`() {
        // Signed out is the sign-in screen, not these pages, even with no key and even with one.
        assertFalse(FirstRunSetup.needed(signedIn = false, hasProviders = false, hasSessions = false, done = false))
        assertFalse(FirstRunSetup.needed(signedIn = false, hasProviders = true, hasSessions = true, done = true))
        assertFalse(FirstRunSetup.needed(signedIn = false, hasProviders = false, hasSessions = true, done = true))
        // Signed in, no conversation yet, Start not tapped.
        assertTrue(FirstRunSetup.needed(signedIn = true, hasProviders = true, hasSessions = false, done = false))
        assertFalse(FirstRunSetup.needed(signedIn = true, hasProviders = true, hasSessions = false, done = true))
    }

    @Test
    fun `an existing install with conversations never sees the setup while signed in`() {
        assertFalse(FirstRunSetup.needed(signedIn = true, hasProviders = true, hasSessions = true, done = false))
        assertFalse(FirstRunSetup.needed(signedIn = true, hasProviders = true, hasSessions = true, done = true))
        assertFalse(FirstRunSetup.needed(signedIn = true, hasProviders = false, hasSessions = true, done = true))
    }

    @Test
    fun `the pages come in order after the account exists`() {
        assertEquals(Stage.WELCOME, FirstRunSetup.stage(signedIn = false, hasGroups = false, sourceChosen = false, modelsSkipped = false, handsSeen = false, handsPossible = true))
        assertEquals(Stage.SOURCE, FirstRunSetup.stage(signedIn = true, hasGroups = true, sourceChosen = false, modelsSkipped = false, handsSeen = false, handsPossible = true))
        assertEquals(Stage.MODELS, FirstRunSetup.stage(signedIn = true, hasGroups = false, sourceChosen = true, modelsSkipped = false, handsSeen = false, handsPossible = true))
        assertEquals(Stage.HANDS, FirstRunSetup.stage(signedIn = true, hasGroups = true, sourceChosen = true, modelsSkipped = false, handsSeen = false, handsPossible = true))
        assertEquals(Stage.MEET, FirstRunSetup.stage(signedIn = true, hasGroups = true, sourceChosen = true, modelsSkipped = false, handsSeen = true, handsPossible = true))
        assertEquals(Stage.MEET, FirstRunSetup.stage(signedIn = true, hasGroups = true, sourceChosen = true, modelsSkipped = false, handsSeen = false, handsPossible = false))
    }

    @Test
    fun `a sign-in that created the account is followed by the password page`() {
        assertEquals(Stage.PASSWORD, FirstRunSetup.stage(signedIn = true, hasGroups = true, sourceChosen = false, modelsSkipped = false, handsSeen = false, handsPossible = true, fresh = true, passwordAnswered = false))
        assertEquals(Stage.SOURCE, FirstRunSetup.stage(signedIn = true, hasGroups = true, sourceChosen = false, modelsSkipped = false, handsSeen = false, handsPossible = true, fresh = true, passwordAnswered = true))
        assertEquals(Stage.SOURCE, FirstRunSetup.stage(signedIn = true, hasGroups = true, sourceChosen = false, modelsSkipped = false, handsSeen = false, handsPossible = true, fresh = false, passwordAnswered = false))
        assertEquals(Stage.WELCOME, FirstRunSetup.stage(signedIn = false, hasGroups = true, sourceChosen = false, modelsSkipped = false, handsSeen = false, handsPossible = true, fresh = true, passwordAnswered = false))
        assertEquals(0, FirstRunSetup.dot(Stage.PASSWORD))
    }

    @Test
    fun `a key already on the phone does not skip the model choice`() {
        assertEquals(Stage.SOURCE, FirstRunSetup.stage(signedIn = true, hasGroups = true, sourceChosen = false, modelsSkipped = false, handsSeen = false, handsPossible = true, hasProviders = true))
    }

    @Test
    fun `the three dots - the model pages fold into the first`() {
        assertEquals(0, FirstRunSetup.dot(Stage.WELCOME))
        assertEquals(0, FirstRunSetup.dot(Stage.SOURCE))
        assertEquals(0, FirstRunSetup.dot(Stage.MODELS))
        assertEquals(1, FirstRunSetup.dot(Stage.HANDS))
        assertEquals(2, FirstRunSetup.dot(Stage.MEET))
    }
}
