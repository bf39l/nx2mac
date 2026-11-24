import Foundation

/// Everything the Build button feeds to `nx2mac build`.
///
/// Kept free of UI and process code so the argument shape can be unit tested
/// (`--update`/`--dlc` are repeatable; extras are staged by the CLI).
struct BuildRequest: Equatable {
    var game: URL
    var keys: URL?
    /// The game library to file into (`--library`): the CLI then uses `<library>/<Game>/`.
    var library: URL?
    /// An explicitly chosen output folder (`--out-dir`); wins over the library.
    var outDir: URL?
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
        if let library { args += ["--library", library.path] }
        if let outDir { args += ["--out-dir", outDir.path] }
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

/// argv for the `nx2mac engine` subcommand.
///
/// The ref is a *positional* argument, so it must sit right after the
/// subcommand — putting options first and the positional last is fine for click,
/// but keeping this pure and tested avoids the "ref swallowed by --source" bug.
enum EngineCommand {
    /// `nx2mac engine [REF] --source <id> --json`
    static func pin(source: String, ref: String) -> [String] {
        var arguments = ["engine"]
        let trimmed = ref.trimmingCharacters(in: .whitespaces)
        if !trimmed.isEmpty { arguments.append(trimmed) }
        arguments += ["--source", source, "--json"]
        return arguments
    }

    /// `nx2mac engine --show --json` — read the saved selection, no network.
    static func show() -> [String] { ["engine", "--show", "--json"] }
}
