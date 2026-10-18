package io.github.nanomuse.cloud

import android.content.Context
import com.openminis.app.MinisApp
import com.openminis.app.data.model.ModelGroup
import com.openminis.app.data.model.ProviderType
import com.openminis.app.data.repository.ProviderRepository
import io.github.nanomuse.avatar.ImageGen

/**
 * Which model answers after sign-in. The Hearth relay is the default the sign-in already
 * provisioned. "My Grok account" is an xAI sign-in on this phone. Either one is an ordinary
 * provider, so the phone's tools (calendar, to-dos, hands) are the same.
 */
object ModelSource {
    fun grokReady(context: Context): Boolean {
        val repo = repo(context) ?: return false
        return repo.config.value.instances.any { it.providerType == ProviderType.xAI && it.isEnabled && repo.hasAnyCredential(it) }
    }

    /** Make the relay's group the one new chats use. */
    fun useRelay(context: Context): Boolean {
        val repo = repo(context) ?: return false
        val cloudId = NanoMuseCloud.instance(context)?.id ?: return false
        val config = repo.config.value
        val group = config.modelGroups.firstOrNull { g ->
            g.memberEntryIds.any { eid -> config.modelEntries.any { it.id == eid && it.providerInstanceId == cloudId } }
        } ?: config.modelGroups.firstOrNull { it.name == NanoMuseCloud.LABEL && it.memberEntryIds.isNotEmpty() }
            ?: return false
        repo.defaultPrimaryGroupId = group.id
        return true
    }

    /** Make the signed-in Grok account the one new chats use. False when that sign-in is not here yet. */
    fun useGrok(context: Context): Boolean {
        val repo = repo(context) ?: return false
        val inst = repo.config.value.instances.firstOrNull {
            it.providerType == ProviderType.xAI && it.isEnabled && repo.hasAnyCredential(it)
        } ?: return false
        val entries = repo.config.value.modelEntries.filter { it.providerInstanceId == inst.id && !it.isHidden }
        val chat = entries.firstOrNull { !ImageGen.looksLikeImageModel(it.model.id) } ?: entries.firstOrNull() ?: return false
        val existing = repo.config.value.modelGroups.firstOrNull { chat.id in it.memberEntryIds }
        if (existing != null) {
            repo.defaultPrimaryGroupId = existing.id
            return true
        }
        val group = ModelGroup(name = "Grok")
        group.memberEntryIds.add(chat.id)
        repo.addGroup(group)
        repo.defaultPrimaryGroupId = group.id
        return true
    }

    private fun repo(context: Context): ProviderRepository? =
        (context.applicationContext as? MinisApp)?.providerRepositoryOrNull
}
