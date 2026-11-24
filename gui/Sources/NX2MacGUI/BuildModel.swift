import AppKit
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
    /// Version the CLI reports (`nx2mac version --json`), for the Settings pane.
    @Published private(set) var cliVersion: String?

    // MARK: Prerequisites / setup

    @Published private(set) var doctor: DoctorReport?
    @Published private(set) var doctorBusy = false
    /// The check currently being installed (`nil` when idle).
    @Published private(set) var installing: String?
    /// Output of the last `nx2mac setup --install …` run.
    @Published private(set) var setupLog: [String] = []
    /// Per-check outcome text ("installed", "failed", …) shown next to the row.
    @Published private(set) var installResults: [String: String] = [:]

    // MARK: Library

    /// The game library folder (nil until the user picks one in Setup).
    @Published private(set) var libraryDirs: [String] = []
    @Published private(set) var games: [LibraryGame] = []
    @Published private(set) var libraryBusy = false
    /// Why the library folder was refused (or "" when it was accepted).
    @Published private(set) var libraryError: String = ""
    /// A synced folder the engine copies each game's save into ("" = off).
    @Published private(set) var cloudDir: String = ""

    /// True while a game is being launched (so its row can show a spinner).
    @Published private(set) var playing: String?

    /// The library's players (one list for every game) and which one plays.
    @Published private(set) var libraryPlayers: [PlayerEntry] = []
    @Published private(set) var activePlayerID: String?

    // MARK: Engine

    @Published private(set) var engineStatus: EngineStatus?
    @Published var engineChoice: String = "upstream"
    @Published var engineRef: String = ""
    @Published private(set) var engineBusy = false
    @Published private(set) var pinned: ResolvedEngine?

    // MARK: Inputs

    /// The dump folder (optional) and the package file inside it. Picking a folder
    /// auto-selects its game package; picking a file by hand overrides that.
    @Published private(set) var gameURL: URL?
    @Published private(set) var gameDirURL: URL?
    /// True while `gameURL` is just what the folder scan found (not user-chosen).
    @Published private(set) var gameFileIsAuto = false
    /// What `nx2mac probe` found in the folder, for the list under the rows.
    @Published private(set) var packages: [GamePackage] = []
    @Published var keysURL: URL?
    /// Where the .app and its build folder go. Until the user picks one, this is
    /// the game library (when set) or the game's own directory.
    @Published private(set) var outDirURL: URL?
    /// True while `outDirURL` is the default rather than something the user chose.
    @Published private(set) var outDirIsAuto = true
    /// The update package to build with (one, like the game file). Empty = none.
    @Published private(set) var updateURL: URL?
    /// True while the update row is just the newest update found beside the game.
    @Published private(set) var updateIsAuto = false
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
        guard let game = buildPath else { return nil }
        // A default output folder means "let the CLI file it into the library".
        let chosenOutDir = outDirIsAuto ? nil : outDirURL
        return BuildRequest(
            game: game,
            keys: keysURL,
            library: chosenOutDir == nil ? libraryURL : nil,
            outDir: chosenOutDir,
            updates: updateURL.map { [$0] } ?? [],
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

    /// Choose a dump folder: the package inside it is picked for you.
    func setGameDir(_ url: URL?) async {
        gameDirURL = url
        packages = []
        guard let url else {
            gameURL = nil
            gameFileIsAuto = false
            if outDirIsAuto { outDirURL = nil }
            return
        }
        if outDirIsAuto { outDirURL = defaultOutDir }
        await scanGameDir(url)
    }

    /// Choose the update yourself (nil clears it): this wins over the folder scan.
    func setUpdate(_ url: URL?) {
        updateURL = url
        updateIsAuto = false
    }

    /// Pick the package file yourself (or a .nup): this wins over the folder scan.
    func setGameFile(_ url: URL?) {
        gameURL = url
        gameFileIsAuto = false
        if outDirIsAuto { outDirURL = defaultOutDir }
    }

    /// Ask the CLI what the folder holds, and pre-select its game package.
    func scanGameDir(_ url: URL) async {
        guard let cli else { return }
        guard let result = try? await Self.run(cli, ["probe", url.path, "--json"]),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(ProbePayload.self, from: data)
        else {
            packages = []
            return
        }
        packages = payload.packages
        if let chosen = payload.gameFile {
            gameURL = URL(fileURLWithPath: chosen)
            gameFileIsAuto = true
        } else {
            gameURL = nil
            gameFileIsAuto = false
        }
        // The newest update beside the game is chosen for you; clear or add your own to change it.
        if updateIsAuto || updateURL == nil {
            if let newest = payload.update {
                updateURL = URL(fileURLWithPath: newest)
                updateIsAuto = true
            } else {
                updateURL = nil
                updateIsAuto = false
            }
        }
    }

    /// The package the build will use, when the folder scan knows it.
    var chosenPackage: GamePackage? {
        packages.first { $0.path == gameURL?.path }
    }

    /// Which path the build receives: the file you chose, else the folder (so the
    /// engine still sees updates/DLC kept beside the game).
    var buildPath: URL? {
        if let gameURL, !gameFileIsAuto { return gameURL }
        return gameDirURL ?? gameURL
    }

    /// The user picked an output folder: stop following the library/game.
    func chooseOutDir(_ url: URL) {
        outDirURL = url
        outDirIsAuto = false
    }

    /// Drop back to the default: the game library when one is set, else the game's folder.
    func resetOutDir() {
        outDirIsAuto = true
        outDirURL = defaultOutDir
    }

    /// The library the build files into (the first one configured).
    var libraryURL: URL? { libraryDirs.first.map { URL(fileURLWithPath: $0) } }

    /// Apply the library folders (the CLI's answer, or a test's): keeps the shown
    /// output folder in step while it is still the default.
    func applyLibraries(_ directories: [String]) {
        libraryDirs = directories
        if outDirIsAuto { outDirURL = defaultOutDir }
    }

    /// The output folder to show/use while the user has not chosen one.
    var defaultOutDir: URL? {
        if let libraryURL { return libraryURL }
        return gameURL.map(Self.directory(of:))
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
        await refreshVersion(cli)
        await refreshDoctor(cli)
        await refreshEngine(cli)
        // The output folder defaults to the library from Setup, so it has to be known
        // here too — not only when the Games tab is opened.
        await refreshLibrary()
        await refreshCloud()
    }

    private func refreshVersion(_ cli: CLICommand) async {
        guard let result = try? await Self.run(cli, ["version", "--json"]),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(VersionPayload.self, from: data)
        else { return }
        cliVersion = payload.version
    }

    /// `{"event": "error", "message": …}` from a refused command.
    private struct CliErrorPayload: Decodable {
        let event: String
        let message: String
    }

    private struct VersionPayload: Decodable {
        let name: String
        let version: String
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

    /// Read the library setting and the games built into it.
    func refreshLibrary() async {
        guard let cli else { return }
        libraryBusy = true
        defer { libraryBusy = false }
        guard let result = try? await Self.run(cli, ["library", "--json"]),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(LibraryPayload.self, from: data)
        else { return }
        applyLibraries(payload.dirs)
        games = payload.games
        await refreshPlayers()
    }

    /// Add another folder to the libraries (it must exist and must not be a game dump).
    func addLibrary(_ url: URL) async {
        await runLibraryCommand(["--add", url.path])
    }

    /// Forget one library folder (the games stay where they are).
    func removeLibrary(_ directory: String) async {
        await runLibraryCommand(["--remove", directory])
    }

    /// Forget every library folder (nothing on disk is removed).
    func clearLibraries() async {
        await runLibraryCommand(["--clear"])
    }

    /// Run a `library` sub-command and refresh what the tab shows.
    private func runLibraryCommand(_ extra: [String]) async {
        guard let cli else { return }
        libraryBusy = true
        libraryError = ""
        defer { libraryBusy = false }
        guard let result = try? await Self.run(cli, ["library", "--json"] + extra),
              let data = result.stdout.data(using: .utf8)
        else { return }
        if let problem = try? JSONDecoder().decode(CliErrorPayload.self, from: data) {
            libraryError = problem.message
            return
        }
        guard let payload = try? JSONDecoder().decode(LibraryPayload.self, from: data) else { return }
        applyLibraries(payload.dirs)
        games = payload.games
        await refreshPlayers()
        await refreshCloud()
    }

    /// Delete the extracted files and shader caches of finished builds (the app stays).
    @discardableResult
    func cleanBuilds(game: String? = nil) async -> String? {
        guard let cli else { return nil }
        var arguments = ["clean", "--json", "--manifests"]
        if let game { arguments += ["--game", game] }
        guard let result = try? await Self.run(cli, arguments),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(CleanPayload.self, from: data)
        else { return nil }
        let freed = ByteCountFormatter.string(fromByteCount: Int64(payload.freed), countStyle: .file)
        log.append(payload.removed.isEmpty ? "Nothing to clean." : "Cleaned build files: freed \(freed).")
        return freed
    }

    private struct CleanPayload: Decodable {
        let freed: Int
        let removed: [Removed]
        struct Removed: Decodable { let game: String; let path: String }
    }

    /// Read a game's cheat files and their switches (empty when it has none).
    func cheats(for game: LibraryGame) async -> [CheatBuild] {
        guard let cli else { return [] }
        guard let result = try? await Self.run(cli, ["cheats", "--game", game.path, "--json"]),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(CheatsPayload.self, from: data)
        else { return [] }
        return payload.games.first?.builds ?? []
    }

    /// Switch one cheat on/off (the same file the in-game Cheats menu writes).
    func setCheat(_ cheat: CheatEntry, build: CheatBuild, for game: LibraryGame, enabled: Bool) async {
        guard let cli else { return }
        let flag = enabled ? "--enable" : "--disable"
        _ = try? await Self.run(cli, ["cheats", "--game", game.path, flag, cheat.name, "--json"])
    }

    /// Keep saves local and copy them into a synced folder (iCloud Drive, Dropbox, …).
    func setCloud(_ url: URL) async {
        guard let cli else { return }
        if let result = try? await Self.run(cli, ["cloud", "--set", url.path, "--json"]),
           let data = result.stdout.data(using: .utf8),
           let payload = try? JSONDecoder().decode(CloudPayload.self, from: data) {
            cloudDir = payload.dir ?? url.path
        }
    }

    /// Stop syncing saves to a cloud folder.
    func clearCloud() async {
        guard let cli else { return }
        _ = try? await Self.run(cli, ["cloud", "--clear", "--json"])
        cloudDir = ""
    }

    /// Read the cloud setting (shown in Setup).
    func refreshCloud() async {
        guard let cli else { return }
        guard let result = try? await Self.run(cli, ["cloud", "--json"]),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(CloudPayload.self, from: data)
        else { return }
        cloudDir = payload.dir ?? ""
    }

    private struct CloudPayload: Decodable {
        let dir: String?
    }

    /// The name of the player who plays, for the picker's label.
    var activePlayerName: String? {
        libraryPlayers.first { $0.id == activePlayerID }?.name
    }

    /// Read the library's players (one list for every game).
    func refreshPlayers() async {
        guard let cli else { return }
        guard let result = try? await Self.run(cli, ["player", "--json"]),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(PlayersPayload.self, from: data)
        else { return }
        libraryPlayers = payload.players
        activePlayerID = payload.active
    }

    /// Add a player (library-wide) and make them the active one.
    @discardableResult
    func createPlayer(name: String) async -> Bool {
        await runPlayerCommand(["--create", name])
    }

    /// Make an existing player the one who plays, in every game.
    func selectPlayer(_ player: PlayerEntry) async {
        _ = await runPlayerCommand(["--select", player.id])
    }

    /// Forget a player; their save folders stay on disk.
    func deletePlayer(_ player: PlayerEntry) async {
        _ = await runPlayerCommand(["--delete", player.id])
    }

    /// Run `nx2mac player …` and keep the returned list.
    @discardableResult
    private func runPlayerCommand(_ extra: [String]) async -> Bool {
        guard let cli else { return false }
        guard let result = try? await Self.run(cli, ["player", "--json"] + extra),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(PlayersPayload.self, from: data)
        else { return false }
        libraryPlayers = payload.players
        activePlayerID = payload.active
        return true
    }

    /// Open a built game and record it as played (NX2Apple's "recently played").
    func play(_ game: LibraryGame) async {
        guard let cli, let app = game.appURL else { return }
        playing = game.name
        defer { playing = nil }
        _ = try? await Self.run(cli, ["library", "--played", game.path, "--json"])
        NSWorkspace.shared.open(app)
        await refreshLibrary()
        await refreshCloud()
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
