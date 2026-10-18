//
//  NanoMuseNudges.swift
//  nanoMuse
//
//  When the app may ask for a star on GitHub (contract C1). The policy comes
//  from the relay — `GET /v1/nudges`, public, also the `nudges` field of
//  `/v1/me` — so the moments can change without an app update; it is
//  fetched at most once a day, the last good copy kept, and these built-in
//  defaults used when nothing was ever fetched. The ask ledger — tasks
//  finished, asks shown, the last ask, days the app was opened, "starred" —
//  lives in UserDefaults under `nm.star.*`, one gate for every moment:
//  enabled, cooldown, lifetime cap, not starred yet, each moment once.
//  The pure gate (`NanoMuseStarGate`) is unit-tested.
//

import Combine
import Foundation

// MARK: - Policy

struct NanoMuseNudgePolicy: Equatable, Sendable {
    var enabled: Bool
    var url: String
    var signedIn: Bool
    var tasks: [Int]
    var newLook: Bool
    var exhausted: Bool
    var daysUsed: [Int]
    var goalDone: Bool
    var cooldownDays: Int
    var maxAsks: Int
    /// The card's sentence set by the operator, in English; empty = the app's own line for the moment.
    var text: String
    /// The same in 简体中文; empty = `text`, then the app's own line.
    var textZh: String

    /// The longest sentence the card takes (characters); the relay enforces the same.
    static let textMax: Int = 200

    /// The built-in fallback — identical on Android, iOS, desktop and web.
    static let defaults = NanoMuseNudgePolicy(
        enabled: true,
        url: "https://github.com/WPMG-4The99/hearth",
        signedIn: true,
        tasks: [3, 10, 30],
        newLook: true,
        exhausted: true,
        daysUsed: [7, 30],
        goalDone: true,
        cooldownDays: 7,
        maxAsks: 4,
        text: "",
        textZh: ""
    )

    /// The operator's sentence for the card's body: `text_zh` first when the UI is Chinese,
    /// then `text`; nil when neither is set, so the caller draws its own line. Only the body
    /// is ever replaced — the title and the buttons stay the app's.
    func sentence(chinese: Bool) -> String? {
        if chinese, !textZh.isEmpty { return textZh }
        return text.isEmpty ? nil : text
    }

    /// One of the operator's sentences as the relay sent it: trimmed, and empty when it is not
    /// a string or is longer than `textMax` (a cached policy from before the fields has neither key).
    static func sentenceField(_ v: Any?) -> String {
        guard let raw = v as? String else { return "" }
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return s.count > textMax ? "" : s
    }

    /// A policy from the relay's JSON (`{"version":1,"star":{…}}`); missing fields keep the defaults.
    static func parse(_ json: [String: Any]) -> NanoMuseNudgePolicy {
        let star = json["star"] as? [String: Any] ?? [:]
        let moments = star["moments"] as? [String: Any] ?? [:]
        func bool(_ v: Any?, _ fallback: Bool) -> Bool {
            if let b = v as? Bool { return b }
            if let n = v as? NSNumber { return n.boolValue }
            return fallback
        }
        func int(_ v: Any?, _ fallback: Int) -> Int {
            if let n = v as? NSNumber { return n.intValue }
            if let s = v as? String, let n = Int(s) { return n }
            return fallback
        }
        func ints(_ v: Any?, _ fallback: [Int]) -> [Int] {
            guard let list = v as? [Any] else { return fallback }
            let out = list.compactMap { ($0 as? NSNumber)?.intValue ?? ($0 as? String).flatMap { Int($0) } }.filter { $0 > 0 }
            return Array(Set(out)).sorted()
        }
        let d = defaults
        var url = (star["url"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !url.hasPrefix("https://") { url = d.url }
        return NanoMuseNudgePolicy(
            enabled: bool(star["enabled"], d.enabled),
            url: url,
            signedIn: bool(moments["signed_in"], d.signedIn),
            tasks: ints(moments["tasks"], d.tasks),
            newLook: bool(moments["new_look"], d.newLook),
            exhausted: bool(moments["exhausted"], d.exhausted),
            daysUsed: ints(moments["days_used"], d.daysUsed),
            goalDone: bool(moments["goal_done"], d.goalDone),
            cooldownDays: max(int(star["cooldown_days"], d.cooldownDays), 0),
            maxAsks: max(int(star["max_asks"], d.maxAsks), 0),
            text: sentenceField(star["text"]),
            textZh: sentenceField(star["text_zh"])
        )
    }

    /// Back to JSON, for the cached copy.
    var json: [String: Any] {
        [
            "version": 1,
            "star": [
                "enabled": enabled,
                "url": url,
                "moments": [
                    "signed_in": signedIn,
                    "tasks": tasks,
                    "new_look": newLook,
                    "exhausted": exhausted,
                    "days_used": daysUsed,
                    "goal_done": goalDone,
                ] as [String: Any],
                "cooldown_days": cooldownDays,
                "max_asks": maxAsks,
                "text": text,
                "text_zh": textZh,
            ] as [String: Any],
        ]
    }
}

// MARK: - The gate (pure)

/// What the phone remembers about asking; the gate decides from this and the policy alone.
struct NanoMuseStarLedger: Equatable, Sendable {
    /// Tasks the person started that ended with a reply (first conversation excluded).
    var tasks = 0
    /// Asks shown so far (a card or a row; "Not now" counts).
    var asks = 0
    /// When the last ask was shown.
    var lastAsk: Date?
    /// Distinct calendar days the app was opened, and the last one counted (`yyyy-MM-dd`).
    var days = 0
    var lastDay = ""
    /// Tapped "Star on GitHub" once: no more asking, anywhere, ever.
    var starred = false
    /// Moments already spent: `signed_in`, `new_look`, `exhausted`, `goal_done`, `tasks.3`, `days_used.7`, …
    var shown: Set<String> = []
}

enum NanoMuseStarMoment: Equatable, Sendable {
    case signedIn
    case tasks(Int)
    case newLook
    case exhausted
    case daysUsed(Int)
    case goalDone

    /// The ledger key: once per moment, once per threshold for the counted ones.
    var key: String {
        switch self {
        case .signedIn: return "signed_in"
        case .tasks(let n): return "tasks.\(n)"
        case .newLook: return "new_look"
        case .exhausted: return "exhausted"
        case .daysUsed(let n): return "days_used.\(n)"
        case .goalDone: return "goal_done"
        }
    }
}

enum NanoMuseStarGate {
    /// Whether the moment is on in the policy and, for the counted ones, whether the count is one of its thresholds.
    static func wanted(_ moment: NanoMuseStarMoment, policy: NanoMuseNudgePolicy) -> Bool {
        switch moment {
        case .signedIn: return policy.signedIn
        case .tasks(let n): return policy.tasks.contains(n)
        case .newLook: return policy.newLook
        case .exhausted: return policy.exhausted
        case .daysUsed(let n): return policy.daysUsed.contains(n)
        case .goalDone: return policy.goalDone
        }
    }

    /// The one gate: policy on, the moment wanted and not spent, not starred, under the lifetime
    /// cap, and the cooldown since the last ask over.
    static func due(_ moment: NanoMuseStarMoment, ledger: NanoMuseStarLedger, policy: NanoMuseNudgePolicy, now: Date = Date()) -> Bool {
        guard policy.enabled, !ledger.starred else { return false }
        guard wanted(moment, policy: policy), !ledger.shown.contains(moment.key) else { return false }
        guard ledger.asks < policy.maxAsks else { return false }
        if let last = ledger.lastAsk, now.timeIntervalSince(last) < Double(policy.cooldownDays) * 86_400 { return false }
        return true
    }

    /// The ledger after a moment was shown.
    static func marked(_ moment: NanoMuseStarMoment, in ledger: NanoMuseStarLedger, now: Date = Date()) -> NanoMuseStarLedger {
        var next = ledger
        next.shown.insert(moment.key)
        next.asks += 1
        next.lastAsk = now
        return next
    }

    /// One more day the app was opened, if `dayKey` is a new one.
    static func dayOpened(_ ledger: NanoMuseStarLedger, dayKey: String) -> NanoMuseStarLedger {
        guard ledger.lastDay != dayKey else { return ledger }
        var next = ledger
        next.lastDay = dayKey
        next.days += 1
        return next
    }
}

// MARK: - Fetch and cache

/// The policy as this phone knows it: the cached copy, refreshed from the relay at most once
/// a day, or read off `/v1/me` when the account page loads. Own-key users without an account
/// still ask the default relay, failing silently.
@MainActor
final class NanoMuseNudges: ObservableObject {
    static let shared = NanoMuseNudges()

    private enum Keys {
        static let policy = "nm.nudges.policy"
        static let fetchedAt = "nm.nudges.fetched_at"
    }

    private static let ttl: TimeInterval = 24 * 60 * 60

    @Published private(set) var policy: NanoMuseNudgePolicy
    private var fetching = false

    private init() {
        if let data = UserDefaults.standard.data(forKey: Keys.policy),
           let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            policy = NanoMuseNudgePolicy.parse(json)
        } else {
            policy = .defaults
        }
    }

    /// `GET /v1/nudges` when the copy is older than a day (or never fetched).
    func fetchIfStale() {
        let last = UserDefaults.standard.double(forKey: Keys.fetchedAt)
        guard Date().timeIntervalSince1970 - last > Self.ttl, !fetching else { return }
        fetching = true
        Task { @MainActor [self] in
            defer { fetching = false }
            let base = NanoMuseCloud.isSignedIn ? NanoMuseCloud.baseURL : NanoMuseCloud.defaultBase
            guard let url = URL(string: base + "/v1/nudges") else { return }
            var request = URLRequest(url: url)
            request.timeoutInterval = 10
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            guard let result = try? await URLSession.shared.data(for: request) else { return }
            let (data, response) = result
            guard (200..<300).contains((response as? HTTPURLResponse)?.statusCode ?? 0),
                  let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return }
            absorb(json)
        }
    }

    /// The `nudges` field of a `/v1/me` reply, when the relay sends one.
    func absorb(me reply: [String: Any]) {
        guard let json = reply["nudges"] as? [String: Any] else { return }
        absorb(json)
    }

    private func absorb(_ json: [String: Any]) {
        let next = NanoMuseNudgePolicy.parse(json)
        policy = next
        if let data = try? JSONSerialization.data(withJSONObject: next.json) {
            UserDefaults.standard.set(data, forKey: Keys.policy)
        }
        UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: Keys.fetchedAt)
    }
}
