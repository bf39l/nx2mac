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

    // MARK: Build queue

    /// The build setups, one per game; the Build tab edits them and the queue runs them in order.
    @Published var setups: [BuildSetup] = [BuildSetup()]
    /// The setup currently streaming a build, or nil while the queue is idle.
    @Published private(set) var runningIndex: Int? = nil

    init() {
        refreshCLI()
    }

    var isRunning: Bool { runningIndex != nil }

    /// The request the Build button would send for one setup.
    func request(for setup: BuildSetup) -> BuildRequest? {
        guard let game = buildPath(for: setup) else { return nil }
        // A default output folder means "let the CLI file it into the library".
        let chosenOutDir = setup.outDirIsAuto ? nil : setup.outDirURL
        return BuildRequest(
            game: game,
            keys: setup.keysURL,
            library: chosenOutDir == nil ? libraryURL : nil,
            outDir: chosenOutDir,
            updates: setup.updateURL.map { [$0] } ?? [],
            dlcs: setup.dlcURLs,
            source: engineChoice,
            ref: engineRef
        )
    }

    /// Everything the CLI needs before one setup can start.
    func readyToBuild(for setup: BuildSetup) -> Bool {
        cli != nil && setup.gameURL != nil && !setup.isRunning
    }

    var firstFailure: DoctorCheck? { doctor?.failedChecks.first }

    /// Choose a dump folder for one setup: the package inside it is picked for you.
    func setGameDir(_ setup: BuildSetup, _ url: URL?) async {
        setup.gameDirURL = url
        setup.packages = []
        guard let url else {
            setup.gameURL = nil
            setup.gameFileIsAuto = false
            if setup.outDirIsAuto { setup.outDirURL = nil }
            return
        }
        if setup.outDirIsAuto { setup.outDirURL = defaultOutDir(for: setup) }
        await scanGameDir(setup, url)
        await refreshCheatFile(setup)
    }

    /// Choose the update yourself (nil clears it): this wins over the folder scan.
    func setUpdate(_ setup: BuildSetup, _ url: URL?) {
        setup.updateURL = url
        setup.updateIsAuto = false
    }

    /// Pick the package file yourself (or a .nup): this wins over the folder scan.
    func setGameFile(_ setup: BuildSetup, _ url: URL?) {
        setup.gameURL = url
        setup.gameFileIsAuto = false
        if setup.outDirIsAuto { setup.outDirURL = defaultOutDir(for: setup) }
        Task { await refreshCheatFile(setup) }
    }

    /// Ask the CLI what the folder holds, and pre-select its game package.
    func scanGameDir(_ setup: BuildSetup, _ url: URL) async {
        guard let cli else { return }
        guard let result = try? await Self.run(cli, ["probe", url.path, "--json"]),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(ProbePayload.self, from: data)
        else {
            setup.packages = []
            return
        }
        setup.packages = payload.packages
        if let chosen = payload.gameFile {
            setup.gameURL = URL(fileURLWithPath: chosen)
            setup.gameFileIsAuto = true
        } else {
            setup.gameURL = nil
            setup.gameFileIsAuto = false
        }
        // The newest update beside the game is chosen for you; clear or add your own to change it.
        if setup.updateIsAuto || setup.updateURL == nil {
            if let newest = payload.update {
                setup.updateURL = URL(fileURLWithPath: newest)
                setup.updateIsAuto = true
            } else {
                setup.updateURL = nil
                setup.updateIsAuto = false
            }
        }
    }

    /// Which path the build receives for one setup: the file you chose, else the folder (so the
    /// engine still sees updates/DLC kept beside the game).
    func buildPath(for setup: BuildSetup) -> URL? {
        if let gameURL = setup.gameURL, !setup.gameFileIsAuto { return gameURL }
        return setup.gameDirURL ?? setup.gameURL
    }

    /// The user picked an output folder: stop following the library/game.
    func chooseOutDir(_ setup: BuildSetup, _ url: URL) {
        setup.outDirURL = url
        setup.outDirIsAuto = false
    }

    /// Drop back to the default: the game library when one is set, else the game's folder.
    func resetOutDir(_ setup: BuildSetup) {
        setup.outDirIsAuto = true
        setup.outDirURL = defaultOutDir(for: setup)
    }

    /// The library the build files into (the first one configured).
    var libraryURL: URL? { libraryDirs.first.map { URL(fileURLWithPath: $0) } }

    /// Apply the library folders (the CLI's answer, or a test's): keeps the shown
    /// output folders in step while they are still the default.
    func applyLibraries(_ directories: [String]) {
        libraryDirs = directories
        for setup in setups where setup.outDirIsAuto {
            setup.outDirURL = defaultOutDir(for: setup)
        }
    }

    /// The output folder to show/use for a setup while the user has not chosen one.
    func defaultOutDir(for setup: BuildSetup) -> URL? {
        if let libraryURL { return libraryURL }
        return setup.gameURL.map(Self.directory(of:))
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
        // The game may already have a cheat file in the library from an earlier build; show it
        // without making the user pick the game again.
        if let first = setups.first { await refreshCheatFile(first) }
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
    func cleanBuilds(_ setup: BuildSetup, game: String? = nil) async -> String? {
        guard let cli else { return nil }
        var arguments = ["clean", "--json", "--manifests"]
        if let game { arguments += ["--game", game] }
        guard let result = try? await Self.run(cli, arguments),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(CleanPayload.self, from: data)
        else { return nil }
        let freed = ByteCountFormatter.string(fromByteCount: Int64(payload.freed), countStyle: .file)
        setup.log.append(payload.removed.isEmpty ? "Nothing to clean." : "Cleaned build files: freed \(freed).")
        return freed
    }

    private struct CleanPayload: Decodable {
        let freed: Int
        let removed: [Removed]
        struct Removed: Decodable { let game: String; let path: String }
    }

    /// The file the Build tab shows: the first build's cheat file, which is the library copy
    /// (`<library>/nx2mac_game_lib/<title id>/mods/cheats/<build id>.txt`), not the file the user
    /// imported from.
    nonisolated static func cheatFile(in payload: CheatsPayload) -> URL? {
        payload.games.first?.builds.first.map { URL(fileURLWithPath: $0.file) }
    }

    /// Read a setup's cheat file (a build re-anchors it), if the game has one.
    func refreshCheatFile(_ setup: BuildSetup) async {
        guard let cli, let game = cheatGameName(for: setup) else {
            setup.cheatFileURL = nil
            return
        }
        var arguments = ["cheats", "--game", game, "--json"]
        if let library = libraryURL { arguments += ["--library", library.path] }
        guard let result = try? await Self.run(cli, arguments),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(CheatsPayload.self, from: data),
              let file = Self.cheatFile(in: payload)
        else {
            setup.cheatFileURL = nil
            return
        }
        setup.cheatFileURL = file
    }

    /// Delete a setup's cheat file (nx2mac keeps one).
    func clearCheats(_ setup: BuildSetup) async {
        await runCheats(setup, ["--remove"])
    }

    /// What the cheat commands address: the title ID (how the library finds the game), else its name.
    func cheatGameName(for setup: BuildSetup) -> String? {
        guard setup.gameURL != nil else { return nil }
        if let title = setup.gameTitleID { return title }
        if let dir = setup.gameDirURL { return dir.lastPathComponent }
        return setup.gameURL?.deletingPathExtension().lastPathComponent
    }

    /// Run a `cheats` sub-command for a setup and put whatever the CLI says into its log.
    @discardableResult
    private func runCheats(_ setup: BuildSetup, _ arguments: [String]) async -> Bool {
        guard let cli, let game = cheatGameName(for: setup) else {
            setup.log.append("Cheats: choose a game folder first.")
            return false
        }
        var arguments = ["cheats", "--game", game, "--json"] + arguments
        if let library = libraryURL { arguments += ["--library", library.path] }
        guard let result = try? await Self.run(cli, arguments) else {
            setup.log.append("Cheats: could not run the CLI.")
            return false
        }
        for line in (result.stdout + result.stderr).split(separator: "\n") {
            if let data = String(line).data(using: .utf8),
               let failure = try? JSONDecoder().decode(CheatErrorPayload.self, from: data) {
                setup.log.append("Cheats: \(failure.message)")
                return false
            }
        }
        await refreshCheatFile(setup)
        return true
    }

    private struct CheatErrorPayload: Decodable {
        let event: String
        let message: String
    }

    /// Copy cheat files into a setup's game cheats folder.
    func importCheats(_ setup: BuildSetup, _ urls: [URL]) async {
        for url in urls {
            await runCheats(setup, ["--import", url.path])
        }
        // `runCheats` already re-reads the game's file; do it once more so a multi-file import ends
        // on the file the CLI keeps (nx2mac keeps exactly one per game).
        await refreshCheatFile(setup)
    }

    /// Read a game's cheat files, their switches and the engine's master/safe flags.
    func cheats(for game: LibraryGame) async -> (builds: [CheatBuild], state: CheatsState) {
        guard let cli else { return ([], .unknown) }
        guard let result = try? await Self.run(cli, ["cheats", "--game", game.path, "--json"]),
              let data = result.stdout.data(using: .utf8),
              let payload = try? JSONDecoder().decode(CheatsPayload.self, from: data),
              let entry = payload.games.first
        else { return ([], .unknown) }
        return (entry.builds, entry.state)
    }

    /// Turn every cheat off (or back on) with the engine's master switch.
    func setCheatsMaster(for game: LibraryGame, off: Bool) async {
        guard let cli else { return }
        _ = try? await Self.run(cli, ["cheats", "--game", game.path, off ? "--off" : "--on", "--json"])
    }

    /// Drop the unclean-exit marker so the saved switches apply again.
    func restoreCheatSwitches(for game: LibraryGame) async {
        guard let cli else { return }
        _ = try? await Self.run(cli, ["cheats", "--game", game.path, "--restore-safe", "--json"])
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
        // The game may already have a cheat file in the library from an earlier build; show it
        // without making the user pick the game again.
        if let first = setups.first { await refreshCheatFile(first) }
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
        defer { engineBusy = false }
        let arguments = EngineCommand.pin(source: engineChoice, ref: engineRef)
        do {
            let result = try await Self.run(cli, arguments)
            if let data = result.stdout.data(using: .utf8),
               let pin = try? JSONDecoder().decode(EnginePin.self, from: data) {
                pinned = pin.engine
            }
            await refreshEngine(cli)
        } catch {
            await refreshEngine(cli)
        }
    }

    // MARK: - Build

    func startBuild(_ setup: BuildSetup, extra: [String] = [], cheatsOnly: Bool = false, onFinished: ((Int32) -> Void)? = nil) {
        guard let cli, let request = request(for: setup), !setup.isRunning else { return }
        setup.pendingLog.removeAll()
        setup.buildAnyway = nil
        setup.log = []
        setup.stage = ""
        setup.appURL = nil
        setup.state = .running
        setup.log.append("Extras: \(request.extrasSummary)")

        do {
            let arguments = request.arguments + (cheatsOnly ? ["--cheats-only"] : []) + extra
            setup.process = try CLIRunner.stream(
                cli,
                arguments,
                onLine: { [weak self] line in
                    Task { @MainActor in self?.handle(setup: setup, line: line) }
                },
                onExit: { [weak self] code in
                    Task { @MainActor in
                        self?.finish(setup: setup, exitCode: code)
                        onFinished?(code)
                    }
                }
            )
        } catch {
            setup.state = .failed(error.localizedDescription)
        }
    }

    /// Re-run one build with the engine flag that gets past the last stop ("Build anyway").
    func retryBuildAnyway(_ setup: BuildSetup, _ flag: String) async {
        setup.buildAnyway = nil
        startBuild(setup, extra: [flag])
    }

    func cancelBuild(_ setup: BuildSetup) {
        setup.process?.terminate()
        setup.process = nil
    }

    func cancelQueue() {
        for setup in setups {
            setup.process?.terminate()
            setup.process = nil
        }
        runningIndex = nil
    }

    /// Queue one line; the timer publishes the batch a moment later.
    private func appendLog(_ setup: BuildSetup, _ text: String) {
        setup.pendingLog.append(text)
        guard setup.logTimer == nil else { return }
        setup.logTimer = Timer.scheduledTimer(withTimeInterval: 0.12, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.flushLog(setup) }
        }
    }

    private func flushLog(_ setup: BuildSetup) {
        setup.logTimer?.invalidate()
        setup.logTimer = nil
        guard !setup.pendingLog.isEmpty else { return }
        let batch = setup.pendingLog
        setup.pendingLog.removeAll()
        setup.log.append(contentsOf: batch)
        if let last = batch.last, let step = Progress.stage(from: last) { setup.stage = step }
    }

    private func handle(setup: BuildSetup, line: String) {
        guard let event = BuildEvent.parse(line) else {
            if !line.isEmpty { appendLog(setup, line) }
            return
        }
        switch event {
        case .engine(let engine):
            pinned = engine
            appendLog(setup, "Engine: \(engine.source.label) — \(engine.summary)")
        case .progress(let update):
            // The engine's live counters: shaders compiled out of the total, files copied, …
            if let label = Optional(update.label), !label.isEmpty { setup.stage = label }
        case .log(let text):
            appendLog(setup, text)
        case .error(let message):
            appendLog(setup, "error: \(message)")
            flushLog(setup)
            setup.state = .failed(message)
        case .result(let result):
            flushLog(setup)
            setup.buildAnyway = (result.ok ? nil : result.failure).flatMap { $0.override == nil ? nil : $0 }
            setup.appURL = result.appPath
            if result.ok {
                setup.state = .succeeded
                setup.stage = "done"
            } else {
                setup.state = .failed("the engine exited with code \(result.returncode)")
            }
        case .unknown(let raw):
            appendLog(setup, raw)
        }
    }

    private func finish(setup: BuildSetup, exitCode: Int32) {
        flushLog(setup)
        setup.process = nil
        // A build anchors the game's cheat file and files it under the library, so the row now
        // points at `<library>/nx2mac_game_lib/<title id>/mods/cheats/<build>.txt`.
        Task { await refreshCheatFile(setup) }
        Task { await refreshLibrary() }
        switch setup.state {
        case .running where exitCode != 0:
            setup.state = .failed("nx2mac exited with code \(exitCode)")
        case .running:
            setup.state = .succeeded
        default:
            break  // a result/error event already decided the outcome
        }
    }

    // MARK: - Queue

    func addSetup() {
        setups.append(BuildSetup())
    }

    func removeSetup(_ setup: BuildSetup) {
        guard setups.count > 1 else { return }  // always keep at least one
        setups.removeAll { $0.id == setup.id }
    }

    /// Run every setup in order, one after another, stopping at the first failure.
    func startQueue() {
        guard runningIndex == nil else { return }
        runNext(from: 0)
    }

    private func runNext(from index: Int) {
        guard index < setups.count else {
            runningIndex = nil
            return
        }
        runningIndex = index
        let setup = setups[index]
        startBuild(setup) { [weak self] code in
            guard let self else { return }
            let ok = code == 0 || setup.state == .succeeded
            if ok {
                self.runNext(from: index + 1)
            } else {
                self.runningIndex = nil  // stop the queue; the failed row shows why
            }
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
