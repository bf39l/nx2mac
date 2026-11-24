import Foundation

/// Everything the Build button feeds to `nx2mac build`.
///
/// Kept free of UI and process code so the argument shape can be unit tested
/// (`--update`/`--dlc` are repeatable; extras are staged by the CLI).
struct BuildRequest: Equatable {
    var game: URL
    var keys: URL?
    var output: URL?
    var updates: [URL] = []
    var dlcs: [URL] = []
    var source: String = "upstream"
    var ref: String = ""

    /// argv for `nx2mac build … --json`.
    var arguments: [String] {
        var args = ["build", game.path, "--json", "--source", source]
        let trimmedRef = ref.trimmingCharacters(in: .whitespaces)
        if !trimmedRef.isEmpty { args += ["--ref", trimmedRef] }
        if let keys { args += ["--keys", keys.path] }
        if let output { args += ["-o", output.path] }
        for update in updates { args += ["--update", update.path] }
        for dlc in dlcs { args += ["--dlc", dlc.path] }
        return args
    }

    /// One-line summary of the chosen extras, for the UI.
    var extrasSummary: String {
        var parts: [String] = []
        if !updates.isEmpty { parts.append("\(updates.count) update") }
        if !dlcs.isEmpty { parts.append("\(dlcs.count) DLC") }
        return parts.isEmpty ? "game folder only" : parts.joined(separator: " + ")
    }
}
