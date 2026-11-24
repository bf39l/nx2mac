import Foundation

/// One prerequisite check, as emitted by `nx2mac doctor|setup --json`.
///
/// `name` is a stable machine key (`macos`, `xcode`, `zstd`, `glslangValidator`,
/// `engine-python`, …). `fix` is the human command line; `installable` plus
/// `install` (argv) tell the GUI whether it can offer an Install button, and
/// `note`/`link` explain the checks we must not automate.
struct DoctorCheck: Decodable, Identifiable, Hashable {
    let name: String
    let ok: Bool
    let detail: String
    let fix: String
    let installable: Bool
    let install: [String]?
    let kind: String
    let note: String
    let link: String

    var id: String { name }

    /// Whether we can run the fix from here (`nx2mac setup --install <name>`).
    var canInstall: Bool { installable && !ok }

    private enum CodingKeys: String, CodingKey {
        case name, ok, detail, fix, installable, install, kind, note, link
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        ok = try container.decode(Bool.self, forKey: .ok)
        detail = try container.decodeIfPresent(String.self, forKey: .detail) ?? ""
        fix = try container.decodeIfPresent(String.self, forKey: .fix) ?? ""
        install = try container.decodeIfPresent([String].self, forKey: .install)
        installable = try container.decodeIfPresent(Bool.self, forKey: .installable) ?? (install != nil)
        kind = try container.decodeIfPresent(String.self, forKey: .kind) ?? (install != nil ? "command" : "manual")
        note = try container.decodeIfPresent(String.self, forKey: .note) ?? ""
        link = try container.decodeIfPresent(String.self, forKey: .link) ?? ""
    }

    /// Memberwise init for tests and previews.
    init(name: String, ok: Bool, detail: String = "", fix: String = "", installable: Bool = false,
         install: [String]? = nil, kind: String = "manual", note: String = "", link: String = "") {
        self.name = name
        self.ok = ok
        self.detail = detail
        self.fix = fix
        self.installable = installable
        self.install = install
        self.kind = kind
        self.note = note
        self.link = link
    }
}

/// The whole `doctor --json` document.
struct DoctorReport: Decodable, Hashable {
    let ok: Bool
    let checks: [DoctorCheck]
    let failures: [String]
    let fixes: [String]

    var failedChecks: [DoctorCheck] { checks.filter { !$0.ok } }
}

/// A selectable engine repository (`upstream` or `fork`).
struct EngineSourceInfo: Decodable, Hashable {
    let id: String
    let label: String
    let url: String
}

/// A pinned engine checkout, as reported by `engine` / the `build` engine event.
struct ResolvedEngine: Decodable, Hashable {
    let source: EngineSourceInfo
    let ref: String
    let branch: String?
    let commit: String
    let shortCommit: String
    let path: String
    let patches: [String]

    enum CodingKeys: String, CodingKey {
        case source, ref, branch, commit, path, patches
        case shortCommit = "short_commit"
    }

    /// e.g. "ref 'fork-customizations', branch=fork-customizations, commit=69513b66656d"
    var summary: String {
        let where_ = branch ?? "detached"
        var text = "ref '\(ref)', branch=\(where_), commit=\(shortCommit)"
        if !patches.isEmpty { text += ", patches=\(patches.joined(separator: ", "))" }
        return text
    }
}

/// The persisted selection inside `engine --show --json`.
struct SavedSelection: Decodable, Hashable {
    let engineID: String?
    let engineRef: String?

    enum CodingKeys: String, CodingKey {
        case engineID = "engine_id"
        case engineRef = "engine_ref"
    }
}

/// `nx2mac engine --show --json`: the saved choice, without touching the network.
struct EngineStatus: Decodable, Hashable {
    let source: EngineSourceInfo
    let ref: String?
    let saved: SavedSelection
}

/// `nx2mac engine --json`: the pinned checkout plus what was written to config.
struct EnginePin: Decodable {
    let engine: ResolvedEngine
    let saved: SavedSelection
}

/// Final `build --json` result event.
struct BuildResult: Hashable {
    let ok: Bool
    let returncode: Int32
    let appPath: URL?
    let argv: [String]
}

/// One line of `build --json` (newline-delimited JSON).
enum BuildEvent: Hashable {
    case engine(ResolvedEngine)
    case log(String)
    case result(BuildResult)
    case error(String)
    case unknown(String)

    private struct Envelope: Decodable {
        let event: String
    }

    private struct EngineEnvelope: Decodable { let engine: ResolvedEngine }
    private struct LogEnvelope: Decodable { let line: String }
    private struct ErrorEnvelope: Decodable { let message: String }

    private struct ResultEnvelope: Decodable {
        let ok: Bool
        let returncode: Int32
        let appPath: String?
        let argv: [String]?

        enum CodingKeys: String, CodingKey {
            case ok, returncode, argv
            case appPath = "app_path"
        }
    }

    /// Decode one NDJSON line; nil for blank lines, `.unknown` for events we don't know yet.
    static func parse(_ line: String) -> BuildEvent? {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let data = trimmed.data(using: .utf8) else { return nil }
        let decoder = JSONDecoder()
        guard let envelope = try? decoder.decode(Envelope.self, from: data) else { return nil }

        switch envelope.event {
        case "engine":
            guard let payload = try? decoder.decode(EngineEnvelope.self, from: data) else { return .unknown(trimmed) }
            return .engine(payload.engine)
        case "log":
            guard let payload = try? decoder.decode(LogEnvelope.self, from: data) else { return .unknown(trimmed) }
            return .log(payload.line)
        case "error":
            guard let payload = try? decoder.decode(ErrorEnvelope.self, from: data) else { return .unknown(trimmed) }
            return .error(payload.message)
        case "result":
            guard let payload = try? decoder.decode(ResultEnvelope.self, from: data) else { return .unknown(trimmed) }
            let url = payload.appPath.map { URL(fileURLWithPath: $0) }
            return .result(BuildResult(ok: payload.ok, returncode: payload.returncode, appPath: url, argv: payload.argv ?? []))
        default:
            return .unknown(trimmed)
        }
    }
}

/// A human display name for an engine log line: the engine prefixes progress
/// with `[stage]` tags, which is the closest thing to a progress percentage.
enum Progress {
    /// Extracts "8/8" from `[16:05:44] [8/8] Mac app · signing (ad-hoc) …`.
    static func stage(from line: String) -> String? {
        var rest = Substring(line)
        // Drop a leading clock timestamp such as "[16:05:44] ", which is not a step tag.
        if rest.hasPrefix("["), let close = rest.firstIndex(of: "]") {
            let leader = rest[rest.index(after: rest.startIndex)..<close]
            if leader.contains(":") {
                rest = rest[rest.index(after: close)...].drop(while: { $0 == " " })
            }
        }
        guard rest.hasPrefix("["), let end = rest.firstIndex(of: "]") else { return nil }
        let tag = String(rest[rest.index(after: rest.startIndex)..<end])
        return tag.contains("/") ? tag : nil  // only "k/n" step tags count as progress
    }
}

/// Geometry of the scrollable selection lists in the Build tab (updates, DLCs):
/// the box grows with the number of items and is capped at five visible rows,
/// after which it scrolls.
enum SelectionList {
    /// Height of one row (name + remove button).
    static let rowHeight: CGFloat = 18
    /// Rows shown before the list starts scrolling.
    static let maxVisibleRows: Int = 5
    /// Container padding (5pt top + 5pt bottom).
    static let padding: CGFloat = 10

    /// Height for *count* rows: 0 when empty, growing one row at a time, capped.
    static func height(for count: Int) -> CGFloat {
        guard count > 0 else { return 0 }
        return CGFloat(min(count, maxVisibleRows)) * rowHeight + padding
    }

    /// Whether the list needs scrolling at this count.
    static func scrolls(at count: Int) -> Bool {
        count > maxVisibleRows
    }
}
