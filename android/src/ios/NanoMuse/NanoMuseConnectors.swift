//
//  NanoMuseConnectors.swift
//  nanoMuse
//
//  The connectors catalogue: the vendors' own remote MCP servers, each one
//  tap away. Connecting creates an MCP server entry (MCPStore) and, for
//  OAuth servers, discovers the authorization server (RFC 9728 / 8414),
//  registers nanoMuse as a client (RFC 7591) and signs in through
//  MCPOAuthController. Android: connectors/*.kt, ui/connectors/*.kt.
//

import SwiftUI
import Combine
import UIKit

// MARK: - Catalogue

struct NanoMuseConnector: Identifiable, Equatable {
    enum Auth: Equatable {
        case none
        case key(header: String?, prefix: String, query: String?, hint: String)
        case oauth(clientIdRequired: Bool, developer: String?)
        case auto
    }

    let id: String
    let name: String
    let category: String
    let url: String
    let auth: Auth
    let docs: String?
    let color: Color
    private let aboutEn: String
    private let aboutZh: String

    var about: String { NanoMuseLocale.isChinese && !aboutZh.isEmpty ? aboutZh : aboutEn }
    var serverId: String { id }

    init?(_ json: [String: Any]) {
        guard let id = json["id"] as? String, let name = json["name"] as? String, let url = json["url"] as? String else { return nil }
        self.id = id
        self.name = name
        self.url = url
        category = json["category"] as? String ?? "misc"
        docs = (json["docs"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        let about = json["about"] as? [String: String] ?? [:]
        aboutEn = about["en"] ?? ""
        aboutZh = about["zh"] ?? ""
        color = Self.color(hex: json["color"] as? String)
        let a = json["auth"] as? [String: Any] ?? [:]
        switch a["kind"] as? String ?? "oauth" {
        case "none":
            auth = .none
        case "key":
            auth = .key(
                header: (a["header"] as? String).flatMap { $0.isEmpty ? nil : $0 },
                prefix: a["prefix"] as? String ?? "",
                query: (a["query"] as? String).flatMap { $0.isEmpty ? nil : $0 },
                hint: a["where"] as? String ?? ""
            )
        case "auto":
            auth = .auto
        default:
            auth = .oauth(clientIdRequired: (a["clientIdRequired"] as? Bool) ?? false, developer: a["developer"] as? String)
        }
    }

    private static func color(hex: String?) -> Color {
        guard var h = hex?.trimmingCharacters(in: .whitespaces), !h.isEmpty else { return NanoMuseTones.action }
        if h.hasPrefix("#") { h.removeFirst() }
        guard h.count == 6, let v = UInt32(h, radix: 16) else { return NanoMuseTones.action }
        return Color(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
    }

    static func categoryName(_ id: String) -> String {
        switch id {
        case "work": return AppLocalized("Work")
        case "talk": return AppLocalized("Messaging")
        case "files": return AppLocalized("Files")
        case "dev": return AppLocalized("Development")
        case "data": return AppLocalized("Data")
        case "design": return AppLocalized("Design")
        case "money": return AppLocalized("Money")
        case "search": return AppLocalized("Search & web")
        case "infra": return AppLocalized("Infrastructure")
        default: return AppLocalized("More")
        }
    }
}

enum NanoMuseConnectorsCatalogue {
    static let shared: (categories: [String], connectors: [NanoMuseConnector]) = load()

    private static func load() -> (categories: [String], connectors: [NanoMuseConnector]) {
        guard let url = Bundle.main.url(forResource: "connectors", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return ([], [])
        }
        let categories = json["categories"] as? [String] ?? []
        let connectors = (json["connectors"] as? [[String: Any]] ?? []).compactMap(NanoMuseConnector.init)
        return (categories, connectors)
    }
}

// MARK: - Authorization discovery

/// What a remote MCP server wants before it talks: nothing, a key, or an
/// OAuth sign-in (with the client registered on the spot when the
/// authorization server allows it).
enum NanoMuseMcpAuthDiscovery {
    static let clientName = "nanoMuse"
    static let clientURI = "https://github.com/WPMG-4The99/hearth"
    static let protocolVersion = "2025-06-18"

    enum Probe {
        case open
        case key(hint: String)
        case oauth(config: MCPOAuthConfig, clientSecret: String?)
        case needsClient
    }

    struct Failure: LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }

    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 30
        return URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
    }()

    private final class NoRedirects: NSObject, URLSessionTaskDelegate {
        func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
            completionHandler(nil)
        }
    }

    static func probe(url: String, redirectURI: String, clientId: String? = nil, clientSecret: String? = nil) async throws -> Probe {
        guard let target = URL(string: url), let scheme = target.scheme, let host = target.host else {
            throw Failure(message: "The server address is not a URL")
        }
        var request = URLRequest(url: target)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json, text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue(protocolVersion, forHTTPHeaderField: "mcp-protocol-version")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": [
                "protocolVersion": protocolVersion,
                "capabilities": [String: Any](),
                "clientInfo": ["name": clientName, "version": "0.1"],
            ],
        ])
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw Failure(message: "The server did not answer") }
        if (200..<300).contains(http.statusCode) { return .open }
        guard http.statusCode == 401 else {
            throw Failure(message: "The server answered \(http.statusCode) to initialize")
        }
        let challenge = parseChallenge(http.value(forHTTPHeaderField: "WWW-Authenticate") ?? "")

        var origin = "\(scheme)://\(host)"
        if let port = target.port { origin += ":\(port)" }
        var path = target.path
        while path.hasSuffix("/") { path.removeLast() }

        var candidates: [String] = []
        if let m = challenge["resource_metadata"] { candidates.append(m) }
        if let m = challenge["resource_metadata_uri"] { candidates.append(m) }
        candidates.append("\(origin)/.well-known/oauth-protected-resource\(path)")
        candidates.append("\(origin)/.well-known/oauth-protected-resource")

        var prm: [String: Any]?
        for candidate in candidates {
            if let json = await getJSON(candidate), let servers = json["authorization_servers"] as? [String], !servers.isEmpty {
                prm = json
                break
            }
        }
        let asBase = (prm?["authorization_servers"] as? [String])?.first.flatMap { $0.isEmpty ? nil : $0 } ?? origin
        guard let meta = await authorizationServer(asBase) else {
            return .key(hint: challenge["error_description"] ?? "")
        }
        let scope = challenge["scope"]
            ?? (prm?["scopes_supported"] as? [String]).flatMap { $0.isEmpty ? nil : $0.joined(separator: " ") }

        let registered: (id: String, secret: String?)
        if let clientId = clientId?.trimmingCharacters(in: .whitespaces), !clientId.isEmpty {
            let secret = clientSecret?.trimmingCharacters(in: .whitespaces)
            registered = (clientId, (secret?.isEmpty == false) ? secret : nil)
        } else {
            guard let endpoint = meta["registration_endpoint"] as? String, !endpoint.isEmpty else { return .needsClient }
            registered = try await register(endpoint: endpoint, redirectURI: redirectURI, scope: scope)
        }
        var config = MCPOAuthConfig()
        config.mode = "static"
        config.clientId = registered.id
        config.authorizationEndpoint = meta["authorization_endpoint"] as? String ?? ""
        config.tokenEndpoint = meta["token_endpoint"] as? String ?? ""
        config.scopes = scope
        config.redirectURI = redirectURI
        return .oauth(config: config, clientSecret: registered.secret)
    }

    private static func authorizationServer(_ base: String) async -> [String: Any]? {
        guard let u = URL(string: base), let scheme = u.scheme, let host = u.host else { return nil }
        var origin = "\(scheme)://\(host)"
        if let port = u.port { origin += ":\(port)" }
        var path = u.path
        while path.hasSuffix("/") { path.removeLast() }
        var candidates = [
            "\(origin)/.well-known/oauth-authorization-server\(path)",
            "\(origin)/.well-known/openid-configuration\(path)",
        ]
        if !path.isEmpty {
            candidates.append("\(origin)\(path)/.well-known/openid-configuration")
            candidates.append("\(origin)\(path)/.well-known/oauth-authorization-server")
        }
        for candidate in candidates {
            if let meta = await getJSON(candidate),
               let a = meta["authorization_endpoint"] as? String, !a.isEmpty,
               let t = meta["token_endpoint"] as? String, !t.isEmpty {
                return meta
            }
        }
        return nil
    }

    private static func register(endpoint: String, redirectURI: String, scope: String?) async throws -> (id: String, secret: String?) {
        var body: [String: Any] = [
            "client_name": clientName,
            "client_uri": clientURI,
            "redirect_uris": [redirectURI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        ]
        if let scope { body["scope"] = scope }
        var (code, text) = try await postJSON(endpoint, body)
        if !(200..<300).contains(code) {
            body.removeValue(forKey: "token_endpoint_auth_method")
            (code, text) = try await postJSON(endpoint, body)
        }
        guard (200..<300).contains(code) else {
            throw Failure(message: "The authorization server refused to register nanoMuse (\(code)): \(text.prefix(200))")
        }
        guard let reg = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any],
              let clientId = reg["client_id"] as? String, !clientId.isEmpty else {
            throw Failure(message: "The authorization server returned no client id")
        }
        let secret = (reg["client_secret"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        return (clientId, secret)
    }

    private static func postJSON(_ url: String, _ body: [String: Any]) async throws -> (Int, String) {
        guard let u = URL(string: url) else { throw Failure(message: "Bad registration endpoint") }
        var request = URLRequest(url: u)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await session.data(for: request)
        return ((response as? HTTPURLResponse)?.statusCode ?? 0, String(data: data, encoding: .utf8) ?? "")
    }

    private static func getJSON(_ url: String) async -> [String: Any]? {
        guard let u = URL(string: url) else { return nil }
        var request = URLRequest(url: u)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        guard let result = try? await session.data(for: request),
              let http = result.1 as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { return nil }
        return (try? JSONSerialization.jsonObject(with: result.0)) as? [String: Any]
    }

    static func parseChallenge(_ header: String) -> [String: String] {
        var out: [String: String] = [:]
        guard let regex = try? NSRegularExpression(pattern: "([A-Za-z_]+)=\"([^\"]*)\"") else { return out }
        let ns = header as NSString
        for m in regex.matches(in: header, range: NSRange(location: 0, length: ns.length)) {
            out[ns.substring(with: m.range(at: 1)).lowercased()] = ns.substring(with: m.range(at: 2))
        }
        return out
    }
}

// MARK: - Connecting

@MainActor
enum NanoMuseConnectors {
    enum Outcome {
        case connected
        case cancelled
        case needsKey(hint: String)
        case needsClient(developer: String?, redirectURI: String)
        case failed(String)
    }

    enum State { case off, connected, needsSignIn }

    static func state(_ connector: NanoMuseConnector, servers: [MCPServerConfig]) -> State {
        guard let server = servers.first(where: { $0.id == connector.serverId }) else { return .off }
        let wantsToken: Bool = {
            if let o = server.oauth, !o.clientId.isEmpty { return true }
            if case .oauth = connector.auth { return true }
            return false
        }()
        if wantsToken, server.headers?["Authorization"] == nil, !MCPOAuthController.isAuthorized(server: server.id) {
            return .needsSignIn
        }
        return .connected
    }

    static func connect(_ connector: NanoMuseConnector, key: String? = nil, clientId: String? = nil, clientSecret: String? = nil) async -> Outcome {
        if let key = key?.trimmingCharacters(in: .whitespacesAndNewlines), !key.isEmpty {
            return withKey(connector, key: key)
        }
        switch connector.auth {
        case .none:
            add(connector, server(connector))
            return .connected
        case .key(_, _, _, let hint):
            return .needsKey(hint: hint)
        case .oauth(let clientIdRequired, let developer):
            if clientIdRequired, (clientId ?? "").trimmingCharacters(in: .whitespaces).isEmpty {
                return .needsClient(developer: developer, redirectURI: MCPOAuthController.defaultRedirectURI)
            }
            return await oauth(connector, clientId: clientId, clientSecret: clientSecret)
        case .auto:
            return await oauth(connector, clientId: clientId, clientSecret: clientSecret)
        }
    }

    static func disconnect(_ connector: NanoMuseConnector) {
        MCPStore.shared.delete(id: connector.serverId)
        MCPOAuthController.purge(server: connector.serverId)
    }

    private static func withKey(_ connector: NanoMuseConnector, key: String) -> Outcome {
        var entry = server(connector)
        if case .key(let header, let prefix, let query, _) = connector.auth {
            if let header {
                entry.headers = [header: prefix + key]
            } else if let query {
                let sep = connector.url.contains("?") ? "&" : "?"
                let encoded = key.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? key
                entry.url = "\(connector.url)\(sep)\(query)=\(encoded)"
            } else {
                entry.headers = ["Authorization": "Bearer " + key]
            }
        } else {
            entry.headers = ["Authorization": "Bearer " + key]
        }
        add(connector, entry)
        return .connected
    }

    private static func oauth(_ connector: NanoMuseConnector, clientId: String?, clientSecret: String?) async -> Outcome {
        let redirect = MCPOAuthController.defaultRedirectURI
        let probe: NanoMuseMcpAuthDiscovery.Probe
        do {
            probe = try await NanoMuseMcpAuthDiscovery.probe(url: connector.url, redirectURI: redirect, clientId: clientId, clientSecret: clientSecret)
        } catch {
            return .failed(error.localizedDescription)
        }
        switch probe {
        case .open:
            add(connector, server(connector))
            return .connected
        case .key(let hint):
            return .needsKey(hint: hint)
        case .needsClient:
            var developer: String?
            if case .oauth(_, let d) = connector.auth { developer = d }
            return .needsClient(developer: developer, redirectURI: redirect)
        case .oauth(let config, let secret):
            let id = connector.serverId
            MCPOAuthController.setClientSecret(secret ?? "", server: id)
            var entry = server(connector)
            entry.oauth = config
            add(connector, entry)
            do {
                try await MCPOAuthController.shared.authorize(server: id, oauth: config)
                return .connected
            } catch {
                if case MCPOAuthController.OAuthError.cancelled = error { return .cancelled }
                return .failed(error.localizedDescription)
            }
        }
    }

    private static func server(_ connector: NanoMuseConnector) -> MCPServerConfig {
        let now = Date().timeIntervalSince1970
        return MCPServerConfig(
            id: connector.serverId, note: connector.about, enabled: true,
            createdAt: now, updatedAt: now,
            url: connector.url, headers: nil, oauth: nil,
            command: nil, args: nil, env: nil, startupTimeoutSeconds: nil
        )
    }

    private static func add(_ connector: NanoMuseConnector, _ entry: MCPServerConfig) {
        if let existing = MCPStore.shared.servers.first(where: { $0.id == connector.serverId }) {
            var merged = entry
            merged.note = existing.note ?? entry.note
            merged.enabled = existing.enabled
            merged.createdAt = existing.createdAt
            MCPStore.shared.update(merged)
        } else {
            MCPStore.shared.add(entry)
        }
    }
}

// MARK: - Which device connected what (contract C3)

/// The one part of a connection that travels: the account's profile
/// (`/v1/me/profile`) carries a `connectors` list — for every entry its id,
/// a label, the server's address without any query string, how it signs in
/// (`oauth` / `key` / `open`), whether it is on, when, and which device
/// holds it. Never a token, a header or a key: those stay on the device
/// that signed in. The relay keeps every device's entries side by side and
/// replaces only the writer's own, so this phone puts what it holds and
/// reads back what the others hold. Android: connectors/SharedConnectors.kt.
@MainActor
final class NanoMuseSharedConnectors: ObservableObject {
    static let shared = NanoMuseSharedConnectors()

    struct Entry: Equatable, Identifiable {
        var id: String
        var label: String
        var url: String
        /// `oauth`, `key` or `open`.
        var auth: String
        var device: String
        var deviceId: String
        var enabled: Bool
        /// ISO-8601, as the relay keeps it.
        var at: String
    }

    private enum Keys {
        static let others = "nanomuse.shared_connectors.others"
    }

    /// The relay caps the list at 64 for the whole account; this phone keeps its share modest.
    nonisolated static let maxMine = 32

    /// The other devices' connections, as last read from the account; kept across restarts.
    @Published private(set) var others: [Entry]

    private var watching: AnyCancellable?
    private var fuse: Task<Void, Never>?

    private init() {
        let raw = UserDefaults.standard.string(forKey: Keys.others) ?? "[]"
        if let data = raw.data(using: .utf8), let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
            others = Self.parse(arr)
        } else {
            others = []
        }
    }

    /// The other devices' entries for one connector, newest first.
    func elsewhere(_ connectorId: String) -> [Entry] {
        others.filter { $0.id == connectorId && $0.enabled }.sorted { $0.at > $1.at }
    }

    /// This phone's entries for the profile body — the catalogue connectors and the
    /// person's own remote servers. Only names, addresses and kinds; nothing that opens anything.
    func mine() -> [[String: Any]] {
        Self.mine(servers: MCPStore.shared.servers,
                  catalogue: NanoMuseConnectorsCatalogue.shared.connectors,
                  device: NanoMuseHub.shared.name, deviceId: NanoMuseHub.shared.deviceId)
    }

    nonisolated static func mine(servers: [MCPServerConfig], catalogue: [NanoMuseConnector], device: String, deviceId: String) -> [[String: Any]] {
        let iso = ISO8601DateFormatter()
        var out: [[String: Any]] = []
        let sorted = servers.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
        for server in sorted.prefix(maxMine) {
            guard let url = server.url?.trimmingCharacters(in: .whitespaces), !url.isEmpty else { continue }
            let connector = catalogue.first { $0.serverId == server.id }
            let auth: String
            if let o = server.oauth, !o.clientId.isEmpty {
                auth = "oauth"
            } else if case .oauth = connector?.auth {
                auth = "oauth"
            } else if !(server.headers ?? [:]).isEmpty || url.contains("?") {
                auth = "key"
            } else if case .key = connector?.auth {
                auth = "key"
            } else {
                auth = "open"
            }
            let label = connector?.name ?? ((server.note ?? "").isEmpty ? server.id : server.note!)
            let bare = url.split(separator: "?", maxSplits: 1).first.map(String.init) ?? url
            out.append([
                "id": String(server.id.prefix(64)),
                "label": String(label.prefix(80)),
                "url": String(bare.prefix(256)),
                "auth": auth,
                "device": String(device.prefix(80)),
                "device_id": String(deviceId.prefix(80)),
                "enabled": server.enabled,
                "at": iso.string(from: Date(timeIntervalSince1970: server.createdAt ?? 0)),
            ])
        }
        return out
    }

    /// A stable string for "did anything change since the last push".
    func stamp() -> String {
        Self.stamp(mine())
    }

    nonisolated static func stamp(_ entries: [[String: Any]]) -> String {
        entries.map { e in
            ["id", "label", "url", "auth", "enabled"].map { k in "\(e[k] ?? "")" }.joined(separator: "|")
        }.sorted().joined(separator: "\n")
    }

    /// A profile read from the relay: keep what the other devices hold, drop our own echo.
    func absorb(_ profile: [String: Any]) {
        guard let arr = profile["connectors"] as? [[String: Any]] else { return }
        let ours = NanoMuseHub.shared.deviceId
        let theirs = Self.parse(arr).filter { !$0.deviceId.isEmpty && $0.deviceId != ours }
        if theirs != others { others = theirs }
        if let data = try? JSONSerialization.data(withJSONObject: Self.serialize(theirs)), let text = String(data: data, encoding: .utf8) {
            UserDefaults.standard.set(text, forKey: Keys.others)
        }
    }

    /// Signed out: another account's devices are not ours to list.
    func forget() {
        others = []
        UserDefaults.standard.removeObject(forKey: Keys.others)
    }

    /// Follow the MCP entries: when what this phone connected changes, the account hears
    /// about it a moment later — the same debounce the name and the face use.
    func watch() {
        guard watching == nil else { return }
        var last = stamp()
        watching = MCPStore.shared.$servers
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in
                guard let self else { return }
                let now = self.stamp()
                guard now != last else { return }
                last = now
                self.fuse?.cancel()
                self.fuse = Task { @MainActor in
                    try? await Task.sleep(nanoseconds: 1_500_000_000)
                    guard !Task.isCancelled else { return }
                    NanoMuseProfileSync.shared.connectorsChanged()
                }
            }
    }

    nonisolated static func parse(_ arr: [[String: Any]]) -> [Entry] {
        arr.compactMap { o in
            let id = (o["id"] as? String ?? "").trimmingCharacters(in: .whitespaces)
            guard !id.isEmpty else { return nil }
            let label = (o["label"] as? String ?? "").trimmingCharacters(in: .whitespaces)
            return Entry(
                id: id,
                label: label.isEmpty ? id : label,
                url: o["url"] as? String ?? "",
                auth: o["auth"] as? String ?? "open",
                device: o["device"] as? String ?? "",
                deviceId: o["device_id"] as? String ?? "",
                enabled: (o["enabled"] as? Bool) ?? true,
                at: o["at"] as? String ?? ""
            )
        }
    }

    nonisolated static func serialize(_ list: [Entry]) -> [[String: Any]] {
        list.map { e in
            ["id": e.id, "label": e.label, "url": e.url, "auth": e.auth,
             "device": e.device, "device_id": e.deviceId, "enabled": e.enabled, "at": e.at]
        }
    }
}

// MARK: - UI

struct NanoMuseConnectorsView: View {
    @ObservedObject private var mcp = MCPStore.shared
    @ObservedObject private var shared = NanoMuseSharedConnectors.shared
    @State private var selected: NanoMuseConnector?
    @State private var query = ""

    private let catalogue = NanoMuseConnectorsCatalogue.shared

    private var connectedCount: Int {
        catalogue.connectors.filter { NanoMuseConnectors.state($0, servers: mcp.servers) != .off }.count
    }

    private var ownServers: Int {
        let ids = Set(catalogue.connectors.map(\.serverId))
        return mcp.servers.filter { !ids.contains($0.id) }.count
    }

    private func matches(_ c: NanoMuseConnector) -> Bool {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return true }
        return c.name.lowercased().contains(q) || c.about.lowercased().contains(q)
    }

    var body: some View {
        List {
            Section {
                Text(AppLocalized("Let the agent into the services you use. Each is the vendor's own remote MCP server; most sign in with the account you already have, a few want a key. A connected service is an MCP server entry the agent can use in any chat."))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                if connectedCount > 0 {
                    Text(String(format: AppLocalized("%d connected"), connectedCount))
                        .font(.footnote.weight(.semibold))
                }
            }
            Section {
                NavigationLink {
                    MCPIntegrationsView()
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(AppLocalized("MCP servers"))
                        Text(ownServers > 0
                             ? String(format: AppLocalized("%d of your own · add by address, command or JSON"), ownServers)
                             : AppLocalized("Add a server by its address or command, or import a JSON config."))
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            } header: {
                Text(AppLocalized("Your own servers"))
            }
            // Contract C3: what the account's other devices connected. The sign-in itself stays per device.
            let elsewhere = shared.others.filter { entry in
                guard entry.enabled else { return false }
                if let connector = catalogue.connectors.first(where: { $0.serverId == entry.id }) {
                    return NanoMuseConnectors.state(connector, servers: mcp.servers) != .connected
                }
                return !mcp.servers.contains { $0.id == entry.id }
            }
            if !elsewhere.isEmpty {
                Section {
                    ForEach(elsewhere) { entry in
                        let connector = catalogue.connectors.first { $0.serverId == entry.id }
                        let device = entry.device.isEmpty ? AppLocalized("another device") : entry.device
                        Button {
                            if let connector { selected = connector }
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(connector?.name ?? entry.label).foregroundStyle(.primary)
                                Text(connector != nil
                                     ? String(format: AppLocalized("Connected on %@. Sign in here to use it on this phone."), device)
                                     : String(format: AppLocalized("Connected on %@. Not in this phone's catalogue; add it under Your own servers if you need it here."), device))
                                    .font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                        .disabled(connector == nil)
                    }
                } header: {
                    Text(AppLocalized("On your other devices"))
                }
            }
            ForEach(catalogue.categories, id: \.self) { category in
                let items = catalogue.connectors.filter { $0.category == category && matches($0) }
                if !items.isEmpty {
                    Section(NanoMuseConnector.categoryName(category)) {
                        ForEach(items) { connector in
                            Button { selected = connector } label: { row(connector) }
                                .buttonStyle(.plain)
                        }
                    }
                }
            }
            if catalogue.connectors.isEmpty {
                Section {
                    Text(AppLocalized("The connectors catalogue could not be loaded."))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .searchable(text: $query, prompt: Text(AppLocalized("Search connectors")))
        .navigationTitle(AppLocalized("Connectors"))
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $selected) { connector in
            NanoMuseConnectorSheet(connector: connector)
        }
    }

    private func row(_ connector: NanoMuseConnector) -> some View {
        let state = NanoMuseConnectors.state(connector, servers: mcp.servers)
        return HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 9, style: .continuous).fill(connector.color)
                Text(String(connector.name.prefix(1)).uppercased())
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(.white)
            }
            .frame(width: 34, height: 34)
            VStack(alignment: .leading, spacing: 2) {
                Text(connector.name).font(.body).foregroundStyle(.primary)
                Text(connector.about).font(.footnote).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer(minLength: 0)
            switch state {
            case .connected:
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            case .needsSignIn:
                Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
            case .off:
                Image(systemName: "chevron.right").font(.footnote).foregroundStyle(.tertiary)
            }
        }
        .contentShape(Rectangle())
    }
}

private struct NanoMuseConnectorSheet: View {
    let connector: NanoMuseConnector

    @ObservedObject private var mcp = MCPStore.shared
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL

    enum Ask: Equatable { case key(hint: String), client(developer: String?, redirect: String) }

    @State private var busy = false
    @State private var ask: Ask?
    @State private var key = ""
    @State private var clientId = ""
    @State private var clientSecret = ""
    @State private var message: String?
    @State private var confirmDisconnect = false

    private var state: NanoMuseConnectors.State { NanoMuseConnectors.state(connector, servers: mcp.servers) }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 14) {
                        ZStack {
                            RoundedRectangle(cornerRadius: 12, style: .continuous).fill(connector.color)
                            Text(String(connector.name.prefix(1)).uppercased())
                                .font(.system(size: 22, weight: .bold)).foregroundStyle(.white)
                        }
                        .frame(width: 52, height: 52)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(connector.name).font(.headline)
                            Text(connector.about).font(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 4)
                    Text(how).font(.footnote).foregroundStyle(.secondary)
                    if state != .connected, let other = NanoMuseSharedConnectors.shared.elsewhere(connector.serverId).first {
                        // Contract C3: another device of the account has this one; the sign-in is per device.
                        Label(String(format: AppLocalized("Connected on %@. Sign in here to use it on this phone."), other.device.isEmpty ? AppLocalized("another device") : other.device), systemImage: "laptopcomputer.and.iphone")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    if let docs = connector.docs, let url = URL(string: docs) {
                        Button {
                            openURL(url)
                        } label: {
                            Label(AppLocalized("The vendor's page about this server"), systemImage: "safari")
                        }
                    }
                }

                if case .key(let hint) = ask {
                    Section {
                        if !hint.isEmpty {
                            Text(String(format: AppLocalized("Needs a key from the service: %@. It is kept in the MCP entry on this phone only."), hint))
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        SecureField(AppLocalized("Paste the key"), text: $key)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        Button(AppLocalized("Connect")) {
                            run { await NanoMuseConnectors.connect(connector, key: key) }
                        }
                        .disabled(key.trimmingCharacters(in: .whitespaces).isEmpty || busy)
                    }
                }

                if case .client(let developer, let redirect) = ask {
                    Section {
                        Text(String(format: AppLocalized("%@ does not register apps by itself, so this takes a minute of setup once: create an OAuth app of your own in its developer settings, give it the callback address below, and paste the app's client ID here (and its secret, if there is one). Both stay on this phone."), connector.name))
                            .font(.footnote).foregroundStyle(.secondary)
                        if let developer, let url = URL(string: developer) {
                            Button {
                                openURL(url)
                            } label: {
                                Label(String(format: AppLocalized("Open %@'s developer settings"), connector.name), systemImage: "arrow.up.right.square")
                            }
                        }
                        VStack(alignment: .leading, spacing: 4) {
                            Text(AppLocalized("Callback (redirect) address for the app, tap to copy:"))
                                .font(.footnote).foregroundStyle(.secondary)
                            Button {
                                UIPasteboard.general.string = redirect
                                message = AppLocalized("Copied")
                            } label: {
                                Text(redirect).font(.system(.footnote, design: .monospaced))
                            }
                        }
                        TextField(AppLocalized("OAuth client ID"), text: $clientId)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        SecureField(AppLocalized("OAuth client secret (if any)"), text: $clientSecret)
                        Button(AppLocalized("Sign in")) {
                            run { await NanoMuseConnectors.connect(connector, clientId: clientId, clientSecret: clientSecret) }
                        }
                        .disabled(clientId.trimmingCharacters(in: .whitespaces).isEmpty || busy)
                    }
                }

                Section {
                    switch state {
                    case .connected:
                        Label(String(format: AppLocalized("Connected as MCP server “%@”. Its tools are available in every chat."), connector.serverId), systemImage: "checkmark.circle")
                            .font(.footnote)
                        Button(AppLocalized("Disconnect"), role: .destructive) { confirmDisconnect = true }
                    case .needsSignIn:
                        Label(AppLocalized("Sign in needed"), systemImage: "exclamationmark.circle").font(.footnote)
                        Button(AppLocalized("Sign in")) { run { await NanoMuseConnectors.connect(connector) } }
                            .disabled(busy)
                        Button(AppLocalized("Disconnect"), role: .destructive) { confirmDisconnect = true }
                    case .off:
                        if ask == nil {
                            Button {
                                run { await NanoMuseConnectors.connect(connector) }
                            } label: {
                                HStack {
                                    Text(primaryLabel)
                                    if busy {
                                        Spacer()
                                        ProgressView()
                                    }
                                }
                            }
                            .disabled(busy)
                        }
                    }
                    if let message {
                        Text(message).font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle(connector.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(AppLocalized("Done")) { dismiss() }
                }
            }
            .confirmationDialog(AppLocalized("Disconnect"), isPresented: $confirmDisconnect, titleVisibility: .visible) {
                Button(AppLocalized("Disconnect"), role: .destructive) {
                    NanoMuseConnectors.disconnect(connector)
                    message = nil
                }
                Button(AppLocalized("Cancel"), role: .cancel) {}
            }
        }
    }

    private var primaryLabel: String {
        switch connector.auth {
        case .none: return AppLocalized("Connect")
        case .key: return AppLocalized("Connect")
        case .oauth, .auto: return AppLocalized("Sign in")
        }
    }

    private var how: String {
        switch connector.auth {
        case .none:
            return AppLocalized("Open to everyone, no sign-in needed.")
        case .key(_, _, _, let hint):
            return String(format: AppLocalized("Needs a key from the service: %@. It is kept in the MCP entry on this phone only."), hint)
        case .oauth:
            return String(format: AppLocalized("Signs in with your %@ account in the browser; nanoMuse registers itself with the service, so there is nothing to type. The token stays on this phone."), connector.name)
        case .auto:
            return AppLocalized("Checks what the server needs first: nothing, a sign-in, or a key.")
        }
    }

    private func run(_ work: @escaping () async -> NanoMuseConnectors.Outcome) {
        busy = true
        message = nil
        Task { @MainActor in
            let outcome = await work()
            busy = false
            switch outcome {
            case .connected:
                ask = nil
                key = ""
                message = AppLocalized("Connected")
            case .cancelled:
                message = nil
            case .needsKey(let hint):
                ask = .key(hint: hint)
            case .needsClient(let developer, let redirect):
                ask = .client(developer: developer, redirect: redirect)
            case .failed(let text):
                message = text
            }
        }
    }
}
