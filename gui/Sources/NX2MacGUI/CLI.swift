import Foundation

/// How to invoke the Python CLI: a command prefix plus the directory to run it in.
///
/// Usually the installed console script (`["/opt/homebrew/bin/nx2mac"]`); during
/// development we can also drive the repo's venv as `python -m nx2mac`.
struct CLICommand: Hashable {
    let prefix: [String]
    let workingDirectory: URL?
    /// True when this is the copy frozen into the app bundle (no install needed).
    var isBundled: Bool = false

    var display: String { prefix.joined(separator: " ") }

    /// Short label for the Settings pane.
    var origin: String {
        if isBundled { return "bundled with this app" }
        return prefix.first.map { URL(fileURLWithPath: $0).deletingLastPathComponent().path } ?? "unknown"
    }
}

enum CLIError: LocalizedError {
    case notFound
    case launchFailed(String)
    case badOutput(String)

    var errorDescription: String? {
        switch self {
        case .notFound:
            return "Could not find the nx2mac command. Set it in Settings (or install it with `uv tool install nx2mac`)."
        case .launchFailed(let message):
            return "Could not run nx2mac: \(message)"
        case .badOutput(let message):
            return "nx2mac returned unexpected output: \(message)"
        }
    }
}

struct CLIResult {
    let stdout: String
    let stderr: String
    let code: Int32
}

/// Finds the `nx2mac` command, in order: explicit override, `NX2MAC_BIN`,
/// PATH, well-known install locations, then a development checkout.
enum CLILocator {
    /// Common install locations for a pip/uv/brew-installed console script.
    private static let wellKnown = [
        "/opt/homebrew/bin/nx2mac",
        "/usr/local/bin/nx2mac",
        "~/.local/bin/nx2mac",
        "~/npm/bin/nx2mac",
        "~/.cargo/bin/nx2mac",
    ]

    static func locate(override: String? = nil, bundle: Bundle = .main) -> CLICommand? {
        if let explicit = executable(override) { return CLICommand(prefix: [explicit], workingDirectory: nil) }
        if let fromEnv = executable(ProcessInfo.processInfo.environment["NX2MAC_BIN"]) {
            return CLICommand(prefix: [fromEnv], workingDirectory: nil)
        }
        // Preferred in a shipped app: the frozen CLI inside Contents/Resources/cli.
        if let bundled = bundledCommand(resources: bundle.resourceURL) { return bundled }
        for directory in searchPath() {
            if let found = executable(directory + "/nx2mac") { return CLICommand(prefix: [found], workingDirectory: nil) }
        }
        for candidate in wellKnown {
            if let found = executable(candidate) { return CLICommand(prefix: [found], workingDirectory: nil) }
        }
        return developmentCommand()
    }

    /// The CLI frozen into the app bundle, when present.
    static func bundledCommand(resources: URL?) -> CLICommand? {
        guard let resources else { return nil }
        let directory = resources.appendingPathComponent("cli")
        let cli = directory.appendingPathComponent("nx2mac")
        guard FileManager.default.isExecutableFile(atPath: cli.path) else { return nil }
        return CLICommand(prefix: [cli.path], workingDirectory: directory, isBundled: true)
    }

    /// Dev fallback: a repository checkout whose `.venv` can run `python -m nx2mac`.
    private static func developmentCommand() -> CLICommand? {
        var roots: [URL] = []
        if let root = ProcessInfo.processInfo.environment["NX2MAC_DEV_ROOT"] {
            roots.append(URL(fileURLWithPath: root))
        }
        // The app bundle normally lives in <repo>/gui/.build/NX2Mac.app — walk up from it,
        // and from the working directory (which is <repo>/gui during `swift test`).
        for start in [Bundle.main.bundleURL, URL(fileURLWithPath: FileManager.default.currentDirectoryPath)] {
            var probe = start
            for _ in 0..<8 {
                roots.append(probe)
                probe.deleteLastPathComponent()
            }
        }
        for root in roots where isCheckout(root) {
            let python = root.appendingPathComponent(".venv/bin/python")
            if FileManager.default.isExecutableFile(atPath: python.path) {
                return CLICommand(prefix: [python.path, "-m", "nx2mac"], workingDirectory: root)
            }
        }
        return nil
    }

    private static func isCheckout(_ root: URL) -> Bool {
        let marker = root.appendingPathComponent("src/nx2mac/__main__.py")
        return FileManager.default.fileExists(atPath: marker.path)
    }

    private static func searchPath() -> [String] {
        let path = ProcessInfo.processInfo.environment["PATH"] ?? ""
        return path.split(separator: ":").map(String.init).filter { !$0.isEmpty }
    }

    /// Expand `~` and return the path only when it is an executable file.
    private static func executable(_ raw: String?) -> String? {
        guard let raw else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let expanded = (trimmed as NSString).expandingTildeInPath
        if FileManager.default.isExecutableFile(atPath: expanded) { return expanded }
        // Allow pointing at a directory holding an `nx2mac` entry point.
        let nested = expanded + "/nx2mac"
        return FileManager.default.isExecutableFile(atPath: nested) ? nested : nil
    }
}

/// Runs the CLI either to completion (one JSON document) or streaming (NDJSON).
enum CLIRunner {
    /// Run to completion and return captured output. Blocking: call off the main actor.
    static func capture(_ command: CLICommand, _ arguments: [String]) throws -> CLIResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = command.prefix + arguments
        process.currentDirectoryURL = command.workingDirectory
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment

        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err

        do {
            try process.run()
        } catch {
            throw CLIError.launchFailed(error.localizedDescription)
        }

        let outData = out.fileHandleForReading.readDataToEndOfFile()
        let errData = err.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        return CLIResult(
            stdout: String(decoding: outData, as: UTF8.self),
            stderr: String(decoding: errData, as: UTF8.self),
            code: process.terminationStatus
        )
    }

    /// Run with live line-by-line output (NDJSON events). Returns the process so
    /// the caller can cancel it. Handlers are called on a background queue.
    static func stream(
        _ command: CLICommand,
        _ arguments: [String],
        onLine: @escaping (String) -> Void,
        onExit: @escaping (Int32) -> Void
    ) throws -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = command.prefix + arguments
        process.currentDirectoryURL = command.workingDirectory
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe  // engine progress goes to stdout; errors mix in

        var buffer = Data()
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let chunk = handle.availableData
            if chunk.isEmpty { return }
            buffer.append(chunk)
            while let newline = buffer.firstIndex(of: 0x0A) {
                let lineData = buffer[buffer.startIndex..<newline]
                buffer.removeSubrange(buffer.startIndex...newline)
                if let line = String(data: lineData, encoding: .utf8) {
                    onLine(line.trimmingCharacters(in: .whitespacesAndNewlines))
                }
            }
        }

        process.terminationHandler = { finished in
            pipe.fileHandleForReading.readabilityHandler = nil
            if !buffer.isEmpty, let tail = String(data: buffer, encoding: .utf8) {
                buffer.removeAll()
                onLine(tail.trimmingCharacters(in: .whitespacesAndNewlines))
            }
            onExit(finished.terminationStatus)
        }

        do {
            try process.run()
        } catch {
            throw CLIError.launchFailed(error.localizedDescription)
        }
        return process
    }
}
