import Foundation

/// State of the build button / progress area.
enum BuildState: Equatable {
    case idle
    case running
    case succeeded
    case failed(String)
}

/// The GUI's whole state: prerequisites, engine choice, chosen inputs and the
/// live build log. It shells out to `nx2mac ... --json` for everything, so no
/// engine or dependency logic is duplicated here.
@MainActor
final class BuildModel: ObservableObject {
    // MARK: CLI

    @Published private(set) var cli: CLICommand?
    @Published private(set) var cliError: String?

    // MARK: Prerequisites / setup

    @Published private(set) var doctor: DoctorReport?
    @Published private(set) var doctorBusy = false
    /// The check currently being installed (`nil` when idle).
    @Published private(set) var installing: String?
    /// Output of the last `nx2mac setup --install …` run.
    @Published private(set) var setupLog: [String] = []
    /// Per-check outcome text ("installed", "failed", …) shown next to the row.
    @Published private(set) var installResults: [String: String] = [:]

    // MARK: Engine

    @Published private(set) var engineStatus: EngineStatus?
    @Published var engineChoice: String = "upstream"
    @Published var engineRef: String = ""
    @Published private(set) var engineBusy = false
    @Published private(set) var pinned: ResolvedEngine?

    // MARK: Inputs

    @Published var gameURL: URL?
    @Published var keysURL: URL?
    /// Where the .app, the engine build folder and prod.keys should all live.
    /// Follows the game's directory until the user picks one themselves.
    @Published private(set) var outDirURL: URL?
    /// True while `outDirURL` is just "the game's directory" (not user-chosen).
    @Published private(set) var outDirIsAuto = true
    /// Optional update package(s) picked outside the game folder (staged by the CLI).
    @Published var updateURLs: [URL] = []
    /// Optional DLC packages/folders picked outside the game folder (staged by the CLI).
    @Published var dlcURLs: [URL] = []

    // MARK: Build

    @Published private(set) var state: BuildState = .idle
    @Published private(set) var log: [String] = []
    @Published private(set) var stage: String = ""
    @Published private(set) var appURL: URL?

    private var process: Process?

    init() {
        refreshCLI()
    }

    var isRunning: Bool { state == .running }

    /// The request the Build button would send right now.
    var request: BuildRequest? {
        guard let gameURL else { return nil }
        return BuildRequest(
            game: gameURL,
            keys: keysURL,
            outDir: outDirURL,
            updates: updateURLs,
            dlcs: dlcURLs,
            source: engineChoice,
            ref: engineRef
        )
    }

    /// Everything the CLI needs before a build can start.
    var readyToBuild: Bool {
        cli != nil && gameURL != nil && !isRunning
    }

    var firstFailure: DoctorCheck? { doctor?.failedChecks.first }

    /// Choose the game (a dump folder, a package file, or a .nup). The output
    /// folder follows it while the user has not picked one explicitly.
    func setGame(_ url: URL?) {
        gameURL = url
        if outDirIsAuto { outDirURL = url.map(Self.directory(of:)) }
    }

    /// The user picked an output folder: stop following the game.
    func chooseOutDir(_ url: URL) {
        outDirURL = url
        outDirIsAuto = false
    }

    /// Drop back to the default (the game's own directory).
    func resetOutDir() {
        outDirIsAuto = true
        outDirURL = gameURL.map(Self.directory(of:))
    }

    /// The directory holding *url*: itself for a folder, the parent for a file.
    static func directory(of url: URL) -> URL {
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue {
            return url
        }
        return url.deletingLastPathComponent()
    }

    // MARK: - CLI discovery

    func refreshCLI() {
        cli = CLILocator.locate(override: UserDefaults.standard.string(forKey: "cliPath"))
        cliError = cli == nil ? CLIError.notFound.localizedDescription : nil
    }

    // MARK: - Refresh (doctor + saved engine)

    func refresh() async {
        guard let cli else { return }
        await refreshDoctor(cli)
        await refreshEngine(cli)
    }

    private func refreshDoctor(_ cli: CLICommand) async {
        doctorBusy = true
        defer { doctorBusy = false }
        do {
            let result = try await Self.run(cli, ["doctor", "--json"])
            // doctor exits 1 when checks fail — the JSON is still valid and complete.
            guard let data = result.stdout.data(using: .utf8),
                  let report = try? JSONDecoder().decode(DoctorReport.self, from: data)
            else {
                doctor = nil
                return
            }
            doctor = report
        } catch {
            doctor = nil
        }
    }

    private func refreshEngine(_ cli: CLICommand) async {
        engineBusy = true
        defer { engineBusy = false }
        do {
            let result = try await Self.run(cli, EngineCommand.show())
            guard let data = result.stdout.data(using: .utf8),
                  let status = try? JSONDecoder().decode(EngineStatus.self, from: data)
            else { return }
            engineStatus = status
            engineChoice = status.saved.engineID ?? status.source.id
            engineRef = status.saved.engineRef ?? ""
        } catch {
            // A missing/odd config must not block the GUI.
        }
    }

    // MARK: - Setup (install missing prerequisites)

    /// Ask the CLI to install one check, streaming its output, then re-check.
    func install(_ check: DoctorCheck) async {
        await runSetup(["setup", "--install", check.name, "--json"], label: check.name)
    }

    /// Install every fixable missing prerequisite in one go.
    func installAllMissing() async {
        await runSetup(["setup", "--all", "--json"], label: "all missing")
    }

    private func runSetup(_ arguments: [String], label: String) async {
        guard let cli, installing == nil else { return }
        installing = label
        setupLog.append("$ nx2mac " + arguments.joined(separator: " "))
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            do {
                _ = try CLIRunner.stream(
                    cli,
                    arguments,
                    onLine: { [weak self] line in
                        Task { @MainActor in self?.handleSetupLine(line) }
                    },
                    onExit: { [weak self] code in
                        Task { @MainActor in
                            self?.setupLog.append(code == 0 ? "finished (exit 0)" : "finished with exit code \(code)")
                            self?.installing = nil
                            continuation.resume()
                        }
                    }
                )
            } catch {
                setupLog.append("error: \(error.localizedDescription)")
                installing = nil
                continuation.resume()
            }
        }
        await refreshDoctor(cli)
    }

    /// Parse the NDJSON events `setup --install/--all --json` emits.
    private func handleSetupLine(_ line: String) {
        guard let data = line.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let event = object["event"] as? String
        else {
            if !line.isEmpty { setupLog.append(line) }
            return
        }
        switch event {
        case "log":
            if let text = object["line"] as? String { setupLog.append(text) }
        case "install-start":
            if let command = object["command"] as? [String] {
                setupLog.append("→ " + command.joined(separator: " "))
            }
        case "install-done":
            if let check = object["check"] as? String {
                let ok = object["ok"] as? Bool ?? false
                installResults[check] = ok ? "installed" : "failed"
                setupLog.append("\(check): \(ok ? "installed" : "failed")")
            }
        case "setup":
            if let payload = object["doctor"] as? [String: Any],
               let data = try? JSONSerialization.data(withJSONObject: payload),
               let report = try? JSONDecoder().decode(DoctorReport.self, from: data) {
                doctor = report
            }
        default:
            break
        }
    }

    // MARK: - Engine pinning

    func pinEngine() async {
        guard let cli else { return }
        engineBusy = true
        state = .idle
        defer { engineBusy = false }
        let arguments = EngineCommand.pin(source: engineChoice, ref: engineRef)
        do {
            let result = try await Self.run(cli, arguments)
            guard let data = result.stdout.data(using: .utf8) else { return }
            if let pin = try? JSONDecoder().decode(EnginePin.self, from: data) {
                pinned = pin.engine
                stage = ""
                log = ["Engine pinned: \(pin.engine.summary)", "checkout: \(pin.engine.path)"]
            } else if let data = result.stdout.data(using: .utf8),
                      let error = try? JSONDecoder().decode(ErrorPayload.self, from: data) {
                state = .failed(error.message)
            }
            await refreshEngine(cli)
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    // MARK: - Build

    func startBuild() {
        guard let cli, let request, !isRunning else { return }
        log = []
        stage = ""
        appURL = nil
        state = .running
        log.append("Extras: \(request.extrasSummary)")

        do {
            process = try CLIRunner.stream(
                cli,
                request.arguments,
                onLine: { [weak self] line in
                    Task { @MainActor in self?.handle(line: line) }
                },
                onExit: { [weak self] code in
                    Task { @MainActor in self?.finish(exitCode: code) }
                }
            )
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    func cancelBuild() {
        process?.terminate()
        process = nil
    }

    private func handle(line: String) {
        guard let event = BuildEvent.parse(line) else {
            if !line.isEmpty { log.append(line) }
            return
        }
        switch event {
        case .engine(let engine):
            pinned = engine
            log.append("Engine: \(engine.source.label) — \(engine.summary)")
        case .log(let text):
            log.append(text)
            if let step = Progress.stage(from: text) { stage = step }
        case .error(let message):
            log.append("error: \(message)")
            state = .failed(message)
        case .result(let result):
            appURL = result.appPath
            if result.ok {
                state = .succeeded
                stage = "done"
            } else {
                state = .failed("the engine exited with code \(result.returncode)")
            }
        case .unknown(let raw):
            log.append(raw)
        }
    }

    private func finish(exitCode: Int32) {
        process = nil
        switch state {
        case .running where exitCode != 0:
            state = .failed("nx2mac exited with code \(exitCode)")
        case .running:
            state = .succeeded
        default:
            break  // a result/error event already decided the outcome
        }
    }

    // MARK: - Helpers

    private struct ErrorPayload: Decodable { let message: String }

    /// Bridge the blocking CLI call into async without blocking the main actor.
    private static func run(_ cli: CLICommand, _ arguments: [String]) async throws -> CLIResult {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    continuation.resume(returning: try CLIRunner.capture(cli, arguments))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }
}
