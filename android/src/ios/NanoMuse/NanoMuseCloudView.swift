import SwiftUI

/// Settings › Providers › nanoMuse Cloud. Signed out: a phone number or an e-mail address, a
/// code, done. Signed in: what is left of the starter allowance, a refresh, the way out.
/// Mirror of the Android `CloudSignInScreen` / `CloudAccountScreen`.
struct NanoMuseCloudView: View {
    @ObservedObject private var store = ProviderConfigStore.shared
    @State private var account: NanoMuseCloudAccount?
    @State private var identifier = ""
    @State private var code = ""
    @State private var codeSent = false
    /// A friend's invite code, optional, with the six digits.
    @State private var invite = ""
    @State private var inviteOpen = false
    /// What the relay gives on sign-up (relay 0.15), for the line above the form.
    @State private var config = NanoMuseConfig()
    @State private var busy = false
    @State private var message: String?
    @State private var failed = false
    /// Which way out is being confirmed, if any.
    @State private var confirm: WayOut?
    /// "Use a different server" (NanoMuseRelayPicker).
    @State private var pickingRelay = false

    private enum WayOut: String, Identifiable {
        case here, everywhere, delete
        var id: String { rawValue }
    }

    private var signedIn: Bool {
        _ = store.instances  // re-evaluate when the provider is removed elsewhere
        return NanoMuseCloud.isSignedIn
    }

    var body: some View {
        Form {
            if signedIn {
                // nanoMuse: the blocks and their order follow the Android account screen —
                // identity, sessions, star, allowance, invite, usage, activity,
                // devices, links, the ways out, the notice.
                identitySection
                NanoMuseAccountSections(account: $account)
                NanoMuseDevicesSection()
                linksSection
                NanoMuseReachSection()
                waysOutSection
                noticeSection
            } else {
                signInSections
            }
        }
        .navigationTitle(NanoMuseCloud.label)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $pickingRelay, onDismiss: { Task { config = await NanoMuseCloud.config() } }) {
            NanoMuseRelayPickerSheet()
        }
        .task {
            account = NanoMuseCloud.account
            if signedIn { await refresh(quiet: true) } else { config = await NanoMuseCloud.config() }
        }
    }

    // MARK: - Signed in

    /// Who is signed in: the hint, the channel it came through, a refresh.
    private var identitySection: some View {
        Section {
            if let account {
                VStack(alignment: .leading, spacing: 2) {
                    Text(account.hint)
                    let channel = account.channel == "phone" ? AppLocalized("Phone number") : account.channel == "email" ? AppLocalized("E-mail") : ""
                    if !channel.isEmpty {
                        Text(channel).font(.caption).foregroundStyle(.secondary)
                    }
                }
            } else {
                Text(AppLocalized("Signed in."))
                    .foregroundStyle(.secondary)
            }
            Button {
                Task { await refresh(quiet: false) }
            } label: {
                Label(AppLocalized("Refresh"), systemImage: "arrow.clockwise")
            }
            .disabled(busy)
            // The relay this sign-in belongs to; changing it signs out first.
            NanoMuseRelayRow(busy: busy) { keep in
                Task { await changeRelay(keep: keep) }
            }
        } footer: {
            if let message {
                Text(message).foregroundStyle(failed ? Color.red : Color.secondary)
            }
        }
    }

    /// Where the account's pieces live in the rest of the app.
    @ViewBuilder
    private var linksSection: some View {
        if let inst = NanoMuseCloud.instance {
            Section {
                NavigationLink {
                    NanoMuseMediaModelsView()
                } label: {
                    Label(AppLocalized("Image & video models"), systemImage: "photo.on.rectangle")
                }
                NavigationLink {
                    NanoMuseAvatarStudioView(embedded: true)
                } label: {
                    Label(AppLocalized("Avatar"), systemImage: "face.smiling")
                }
                NavigationLink {
                    ProviderInstanceDetailView(instanceId: inst.id)
                } label: {
                    Label(AppLocalized("Provider settings"), systemImage: "slider.horizontal.3")
                }
                NavigationLink {
                    ModelGroupsView()
                } label: {
                    Label(AppLocalized("Model groups"), systemImage: "square.stack.3d.up")
                }
                NavigationLink {
                    NanoMuseDataControlsView()
                } label: {
                    Label(AppLocalized("Data controls"), systemImage: "hand.raised")
                }
            } footer: {
                Text(AppLocalized("nanoMuse Cloud is an ordinary provider in this app: its models are listed under Providers and can be mixed with your own in a model group."))
            }
        }
    }

    /// Sign out here, sign out everywhere, delete — each behind one confirmation.
    private var waysOutSection: some View {
        Section {
            Button {
                confirm = .here
            } label: {
                Label(AppLocalized("Sign out on this phone"), systemImage: "rectangle.portrait.and.arrow.right")
            }
            Button {
                confirm = .everywhere
            } label: {
                Label(AppLocalized("Sign out everywhere"), systemImage: "rectangle.portrait.and.arrow.right")
            }
            Button(role: .destructive) {
                confirm = .delete
            } label: {
                Label(AppLocalized("Delete the account"), systemImage: "trash")
            }
        } footer: {
            Text(AppLocalized("This phone's key is revoked and the Cloud provider removed; the allowance stays with your account."))
        }
        .disabled(busy)
        // Delete: one confirmation. A sign-out: the sheet with its one question (C12).
        .confirmationDialog(
            confirmTitle,
            isPresented: Binding(get: { confirm == .delete }, set: { if !$0 { confirm = nil } }),
            titleVisibility: .visible
        ) {
            Button(AppLocalized("Delete the account"), role: .destructive) {
                Task { await leave(.delete, keep: false) }
            }
        } message: {
            Text(confirmMessage)
        }
        .sheet(item: Binding(get: { confirm.flatMap { $0 == .delete ? nil : $0 } }, set: { if $0 == nil { confirm = nil } })) { way in
            NanoMuseSignOutSheet(title: confirmTitle, message: confirmMessage, action: AppLocalized("Sign out")) { keep in
                Task { await leave(way, keep: keep) }
            }
        }
    }

    private var confirmTitle: String {
        switch confirm {
        case .everywhere: return AppLocalized("Sign out everywhere")
        case .delete: return AppLocalized("Delete the account")
        case .here, .none: return AppLocalized("Sign out on this phone")
        }
    }

    private var confirmMessage: String {
        switch confirm {
        case .everywhere: return AppLocalized("Every device signed in to this account loses its key, this phone included. The account stays; sign in again any time.")
        case .delete: return AppLocalized("The account, its sign-ins, usage and history are deleted at the relay, and this account's chats, memory, feed, goals and face are removed from this phone. This cannot be undone.")
        case .here, .none: return AppLocalized("The nanoMuse Cloud provider and its models will be removed from this phone.")
        }
    }

    /// What nanoMuse is, in a few lines, and where to bring a bug or a patch.
    private var noticeSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 6) {
                Text(AppLocalized("Free, open source, non-profit")).font(.subheadline.weight(.semibold))
                Text(AppLocalized("nanoMuse is a non-profit open-source community project, free, forever. The model comes with a free allowance paid by the developer; after that, your own key. Nothing is sold; what the relay keeps is in the privacy policy, and Settings → Data controls is yours."))
                    .font(.caption).foregroundStyle(.secondary)
                Text(AppLocalized("Found a bug, want a feature, have a patch? The GitHub repository is the place."))
                    .font(.caption).foregroundStyle(.secondary)
                Link(AppLocalized("Open on GitHub"), destination: NanoMuseStar.repoURL)
                    .font(.caption.weight(.medium))
            }
            .padding(.vertical, 4)
        } footer: {
            privacyFooter
        }
    }

    // MARK: - Signed out

    @ViewBuilder
    private var signInSections: some View {
        Section {
            if NanoMuseCloud.signInEnded {
                // the relay refused the phone's key: the account's chats wait here for the
                // same account to sign in again (C12)
                Label(AppLocalized("Your sign-in on this phone was ended. Sign in again to continue; your chats are kept on this device until then."), systemImage: "person.crop.circle.badge.clock")
                    .font(.subheadline)
            }
            Text(AppLocalized("Sign in with a phone number or an e-mail address and start right away with a starter allowance, no key of your own needed. A provider of your own can be added at any time."))
                .font(.subheadline)
                .foregroundStyle(.secondary)
            if let allowance = config.allowanceCny, allowance > 0 {
                let amount = "¥" + allowance.formatted(.number.precision(.fractionLength(allowance.rounded() == allowance ? 0 : 2)))
                Label(String(format: AppLocalized("Free to start: %@ of credit comes with the account. No card."), amount), systemImage: "gift")
                    .font(.subheadline)
            }
        }

        Section {
            // The app's mark above the field (docs/brand.md: the sign-in stands for the app), as on Android.
            HStack {
                Spacer()
                NanoMuseBrandMark(size: 64)
                    .accessibilityHidden(true)
                Spacer()
            }
            .padding(.vertical, 8)
            .listRowBackground(Color.clear)
            // SMS codes reach mainland-China numbers only: the placeholder says so, and a number
            // from elsewhere gets the e-mail sentence while it is being typed, before any tap.
            TextField(AppLocalized("Mainland China phone number or e-mail"), text: $identifier)
                .keyboardType(.emailAddress)
                .textContentType(.username)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .disabled(busy || codeSent)
            if !codeSent, NanoMuseCloud.needsEmailInstead(identifier: identifier) {
                Text(NanoMuseCloud.phoneRegionSentence)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            if codeSent {
                TextField(AppLocalized("Verification code"), text: $code)
                    .keyboardType(.numberPad)
                    .textContentType(.oneTimeCode)
                    .disabled(busy)
                if inviteOpen {
                    TextField(AppLocalized("Invite code (optional)"), text: $invite)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .disabled(busy)
                } else {
                    Button(AppLocalized("Have an invite code?")) { inviteOpen = true }
                        .font(.subheadline)
                        .disabled(busy)
                }
            }
        } footer: {
            if let message {
                Text(message).foregroundStyle(failed ? Color.red : Color.secondary)
            } else if inviteOpen && codeSent {
                let bonus = "¥" + (config.inviteeBonusCny ?? 5).formatted(.number.precision(.fractionLength(0)))
                Text(String(format: AppLocalized("A friend’s code on a first sign-in adds %@ for both of you after your first request."), bonus))
            }
        }

        Section {
            if codeSent {
                Button {
                    Task { await signIn() }
                } label: {
                    HStack {
                        Text(AppLocalized("Sign in"))
                        if busy { Spacer(); ProgressView() }
                    }
                }
                .disabled(busy || code.trimmingCharacters(in: .whitespaces).isEmpty)
                Button(AppLocalized("Use another number or address")) {
                    codeSent = false
                    code = ""
                    message = nil
                }
                .disabled(busy)
            } else {
                Button {
                    Task { await sendCode() }
                } label: {
                    HStack {
                        Text(AppLocalized("Send code"))
                        if busy { Spacer(); ProgressView() }
                    }
                }
                .disabled(busy || identifier.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        } footer: {
            privacyFooter
        }

        // Anyone can run the relay; the sign-in can go to one's own.
        Section {
            Button(AppLocalized("Use a different server")) { pickingRelay = true }
                .disabled(busy)
        } footer: {
            if NanoMuseCloud.usesOwnRelay {
                Text(String(format: AppLocalized("Server: %@"), NanoMuseCloud.relayHost))
            }
        }
    }

    /// Android's `nm_cloud_fine_print`: what the relay keeps, and where the rest is written.
    private var privacyFooter: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(AppLocalized("The relay keeps an account id, a masked identifier, usage counts and your agent's name and look; what else, and what is yours to switch off, is in the privacy policy."))
            Link(AppLocalized("Privacy policy"), destination: NanoMuseLinks.privacy)
        }
    }

    // MARK: - Actions

    /// Settings → Account → Change: sign out on this phone (a sign-out like any other, C12), then the picker.
    private func changeRelay(keep: Bool) async {
        busy = true
        defer { busy = false }
        await NanoMuseCloud.signOut(keep: keep)
        account = nil
        message = nil
        failed = false
        pickingRelay = true
    }

    private func sendCode() async {
        // A number outside mainland China gets no text message; say so before asking the relay
        // (whose `phone_region` answer is the same sentence).
        if NanoMuseCloud.needsEmailInstead(identifier: identifier) {
            failed = true
            message = NanoMuseCloud.phoneRegionSentence
            return
        }
        busy = true
        failed = false
        defer { busy = false }
        do {
            try await NanoMuseCloud.requestCode(identifier: identifier)
            codeSent = true
            message = AppLocalized("Code sent. Enter it below.")
        } catch {
            failed = true
            message = NanoMuseCloud.describe(error)
        }
    }

    private func signIn() async {
        busy = true
        failed = false
        defer { busy = false }
        do {
            account = try await NanoMuseCloud.verify(identifier: identifier, code: code, invite: invite)
            message = nil
            code = ""
            invite = ""
            inviteOpen = false
            codeSent = false
        } catch {
            failed = true
            message = NanoMuseCloud.describe(error)
        }
    }

    private func refresh(quiet: Bool) async {
        busy = true
        defer { busy = false }
        do {
            account = try await NanoMuseCloud.refresh()
            if !quiet {
                failed = false
                message = nil
            }
        } catch {
            if !quiet {
                failed = true
                message = NanoMuseCloud.describe(error)
            }
        }
    }

    /// One of the ways out; the sign-in form comes back when it worked. `keep` is the sheet's
    /// answer (C12) — the account's data stays on the phone only when asked.
    private func leave(_ way: WayOut, keep: Bool) async {
        busy = true
        defer { busy = false }
        do {
            switch way {
            case .here: await NanoMuseCloud.signOut(keep: keep)
            case .everywhere: try await NanoMuseCloud.signOutEverywhere(keep: keep)
            case .delete: try await NanoMuseCloud.deleteAccount()
            }
            account = nil
            message = nil
            failed = false
        } catch {
            failed = true
            message = NanoMuseCloud.describe(error)
        }
        confirm = nil
    }
}

/// The row in the providers list that leads to `NanoMuseCloudView`.
struct NanoMuseCloudRow: View {
    @ObservedObject private var store = ProviderConfigStore.shared

    var body: some View {
        _ = store.instances
        let account = NanoMuseCloud.isSignedIn ? NanoMuseCloud.account : nil
        return HStack(spacing: 12) {
            Image(systemName: "cloud.fill")
                .font(.title3)
                .foregroundStyle(Color.accentColor)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(NanoMuseCloud.label)
                if let account {
                    // Who is signed in, and what is left by the same rule as the Settings card: the
                    // yuan on a relay that prices in money, tokens on an older one, nothing to count
                    // on a relay without a ceiling (where a token fraction would read 0 percent).
                    Text(account.leftLine.map { "\(account.hint) · \($0)" } ?? account.hint)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else if NanoMuseCloud.isSignedIn {
                    Text(AppLocalized("Signed in"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text(AppLocalized("Phone or e-mail sign-in, no key needed"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}
