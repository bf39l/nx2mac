import XCTest

@testable import NX2MacGUI

/// Parsing of the CLI's machine interface. These fixtures are the exact payload
/// shapes `nx2mac ... --json` produces (see AGENT.md), so a CLI change that
/// breaks the GUI breaks these tests first.
final class BuildEventTests: XCTestCase {
    func testDecodesEngineEvent() throws {
        let line = """
        {"event": "engine", "engine": {"source": {"id": "fork", "label": "bf39l/nxnative (personal fork)", \
        "url": "https://github.com/bf39l/nxnative.git"}, "ref": "fork-customizations", \
        "branch": "fork-customizations", "commit": "69513b66656d0000", "short_commit": "69513b66656d", \
        "path": "/cache/engines/fork-fork-customizations", "patches": ["0001-fix.patch"]}}
        """

        guard case .engine(let engine)? = BuildEvent.parse(line) else {
            return XCTFail("expected an engine event")
        }
        XCTAssertEqual(engine.source.id, "fork")
        XCTAssertEqual(engine.ref, "fork-customizations")
        XCTAssertEqual(engine.shortCommit, "69513b66656d")
        XCTAssertEqual(engine.branch, "fork-customizations")
        XCTAssertEqual(engine.patches, ["0001-fix.patch"])
        XCTAssertTrue(engine.summary.contains("commit=69513b66656d"))
        XCTAssertTrue(engine.summary.contains("patches=0001-fix.patch"))
    }

    func testDecodesLogEvent() {
        XCTAssertEqual(BuildEvent.parse(#"{"event": "log", "line": "[8/8] Mac app · signing"}"#),
                       .log("[8/8] Mac app · signing"))
    }

    func testDecodesResultEvent() {
        let line = """
        {"event": "result", "ok": true, "returncode": 0, \
        "app_path": "/Users/bo/switch/Mercenaries Wings.app", "argv": ["python3", "build_ipa_from_nsp.py"]}
        """

        guard case .result(let result)? = BuildEvent.parse(line) else {
            return XCTFail("expected a result event")
        }
        XCTAssertTrue(result.ok)
        XCTAssertEqual(result.returncode, 0)
        XCTAssertEqual(result.appPath?.lastPathComponent, "Mercenaries Wings.app")
        XCTAssertEqual(result.argv.count, 2)
    }

    func testDecodesFailureResultWithNullAppPath() {
        let line = #"{"event": "result", "ok": false, "returncode": 3, "app_path": null, "argv": []}"#

        guard case .result(let result)? = BuildEvent.parse(line) else {
            return XCTFail("expected a result event")
        }
        XCTAssertFalse(result.ok)
        XCTAssertEqual(result.returncode, 3)
        XCTAssertNil(result.appPath)
    }

    func testDecodesErrorEvent() {
        XCTAssertEqual(BuildEvent.parse(#"{"event": "error", "message": "prerequisites are not met (zstd)"}"#),
                       .error("prerequisites are not met (zstd)"))
    }

    func testIgnoresBlankLinesAndKeepsUnknownEvents() {
        XCTAssertNil(BuildEvent.parse(""))
        XCTAssertNil(BuildEvent.parse("   "))
        XCTAssertNil(BuildEvent.parse("not json at all"))
        XCTAssertEqual(BuildEvent.parse(#"{"event": "future-thing", "x": 1}"#), .unknown(#"{"event": "future-thing", "x": 1}"#))
    }
}

final class ProgressTests: XCTestCase {
    func testExtractsStepTags() {
        XCTAssertEqual(Progress.stage(from: "[16:05:44] [8/8] Mac app · signing (ad-hoc) …"), "8/8")
        XCTAssertEqual(Progress.stage(from: "[4/7] shader split · splitting …"), "4/7")
    }

    func testIgnoresNonStepLines() {
        XCTAssertNil(Progress.stage(from: "[16:05:45] ✓ [8/8] Mac app (0:07): Mercenaries Wings.app"))
        XCTAssertNil(Progress.stage(from: "launcher: the host exited with 0"))
        XCTAssertNil(Progress.stage(from: "[nvn-mtl] present: picture shape 1920x1080"))
    }
}

final class DoctorReportTests: XCTestCase {
    func testDecodesDoctorDocument() throws {
        let json = """
        {"ok": false,
         "checks": [{"name": "macos", "ok": true, "detail": "darwin", "fix": ""},
                    {"name": "zstd", "ok": false, "detail": "'zstd' not found on PATH", "fix": "brew install zstd"}],
         "failures": ["zstd"],
         "fixes": ["brew install zstd"]}
        """

        let report = try JSONDecoder().decode(DoctorReport.self, from: Data(json.utf8))
        XCTAssertFalse(report.ok)
        XCTAssertEqual(report.failedChecks.map(\.name), ["zstd"])
        XCTAssertEqual(report.failedChecks.first?.fix, "brew install zstd")
        XCTAssertEqual(report.checks.count, 2)
    }

    func testDecodesInstallableRemedyFields() throws {
        let json = """
        {"ok": false,
         "checks": [{"name": "zstd", "ok": false, "detail": "'zstd' not found on PATH",
                     "fix": "brew install zstd", "installable": true,
                     "install": ["brew", "install", "zstd"], "kind": "brew", "note": "", "link": ""},
                    {"name": "xcode", "ok": false, "detail": "Command Line Tools only",
                     "fix": "sudo xcode-select -s /Applications/Xcode.app/Contents/Developer",
                     "installable": false, "install": null, "kind": "manual",
                     "note": "Install the full Xcode from the App Store",
                     "link": "https://apps.apple.com/app/xcode/id497799835"}],
         "failures": ["zstd", "xcode"], "fixes": []}
        """

        let report = try JSONDecoder().decode(DoctorReport.self, from: Data(json.utf8))
        let zstd = try XCTUnwrap(report.checks.first)
        let xcode = try XCTUnwrap(report.checks.last)

        XCTAssertTrue(zstd.canInstall)
        XCTAssertEqual(zstd.install, ["brew", "install", "zstd"])
        XCTAssertEqual(zstd.kind, "brew")
        XCTAssertFalse(xcode.canInstall)  // manual: no Install button
        XCTAssertEqual(xcode.kind, "manual")
        XCTAssertTrue(xcode.link.hasPrefix("https://apps.apple.com"))
        XCTAssertTrue(xcode.note.contains("App Store"))
    }

    func testDecodesOlderPayloadWithoutRemedyFields() throws {
        let json = """
        {"ok": true,
         "checks": [{"name": "macos", "ok": true, "detail": "darwin", "fix": ""}],
         "failures": [], "fixes": []}
        """

        let report = try JSONDecoder().decode(DoctorReport.self, from: Data(json.utf8))
        let check = try XCTUnwrap(report.checks.first)

        XCTAssertFalse(check.installable)
        XCTAssertFalse(check.canInstall)
        XCTAssertNil(check.install)
        XCTAssertEqual(check.kind, "manual")
    }

    func testDecodesEngineStatus() throws {
        let json = """
        {"source": {"id": "fork", "label": "bf39l/nxnative (personal fork)", "url": "https://x/y.git"},
         "ref": "fork-customizations",
         "saved": {"engine_id": "fork", "engine_ref": "fork-customizations"}}
        """

        let status = try JSONDecoder().decode(EngineStatus.self, from: Data(json.utf8))
        XCTAssertEqual(status.source.id, "fork")
        XCTAssertEqual(status.saved.engineID, "fork")
        XCTAssertEqual(status.saved.engineRef, "fork-customizations")
    }

    func testDecodesEmptyEngineStatus() throws {
        let json = """
        {"source": {"id": "upstream", "label": "seedds/nxnative (upstream)", "url": "https://x/z.git"},
         "ref": null, "saved": {"engine_id": null, "engine_ref": null}}
        """

        let status = try JSONDecoder().decode(EngineStatus.self, from: Data(json.utf8))
        XCTAssertNil(status.saved.engineID)
    }
}

/// End-to-end check against a real CLI: only runs when the developer opts in,
/// so `swift test` stays green on machines without a checkout.
final class LiveCLITests: XCTestCase {
    private var enabled: Bool {
        ProcessInfo.processInfo.environment["NX2MAC_LIVE_TESTS"] == "1"
    }

    func testLocateAndRunDoctor() throws {
        try XCTSkipUnless(enabled, "set NX2MAC_LIVE_TESTS=1 to run against the real CLI")
        guard let command = CLILocator.locate() else { return XCTFail("no nx2mac CLI found") }

        let result = try CLIRunner.capture(command, ["doctor", "--json"])
        let report = try JSONDecoder().decode(DoctorReport.self, from: Data(result.stdout.utf8))
        XCTAssertFalse(report.checks.isEmpty)
        XCTAssertEqual(report.checks.map(\.name).first, "macos")
        XCTAssertEqual(result.code, report.ok ? 0 : 1)
    }

    func testStreamsBuildEventsFromRealCLI() throws {
        try XCTSkipUnless(enabled, "set NX2MAC_LIVE_TESTS=1 to run against the real CLI")
        guard let command = CLILocator.locate() else { return XCTFail("no nx2mac CLI found") }

        // A bogus game path fails fast but still produces engine + error events.
        let exit = expectation(description: "stream finished")
        let lines = LockedLines()
        _ = try CLIRunner.stream(command, ["build", "/nonexistent-nx2mac-game", "--json"],
                                 onLine: { lines.appendLine($0) },
                                 onExit: { _ in exit.fulfill() })
        wait(for: [exit], timeout: 120)

        let events = lines.value.compactMap(BuildEvent.parse)
        XCTAssertTrue(events.contains { if case .error = $0 { return true } else { return false } },
                      "expected an error event, got: \(lines.value)")
    }
}

/// Tiny thread-safe line collector for the streaming tests.
private final class LockedLines {
    private let lock = NSLock()
    private var storage: [String] = []

    var value: [String] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    func appendLine(_ line: String) {
        lock.lock()
        storage.append(line)
        lock.unlock()
    }
}

final class ToolPathTests: XCTestCase {
    /// Regression: a Finder-launched app has PATH=/usr/bin:/bin:/usr/sbin:/sbin,
    /// which made doctor report brew/zstd/glslangValidator as missing.
    func testHomebrewIsAddedToTheLaunchdPath() {
        let path = ToolPath.augmented("/usr/bin:/bin:/usr/sbin:/sbin", home: "/Users/me").split(separator: ":").map(String.init)

        XCTAssertTrue(path.contains("/opt/homebrew/bin"))
        XCTAssertTrue(path.contains("/opt/homebrew/sbin"))
        XCTAssertTrue(path.contains("/usr/bin"))
        XCTAssertEqual(path.first, "/opt/homebrew/bin", "tool dirs must come first")
        XCTAssertEqual(path.last, "/sbin", "inherited order is preserved")
    }

    func testExistingEntriesAreNotDuplicated() {
        let path = ToolPath.augmented("/opt/homebrew/bin:/usr/bin:/opt/homebrew/bin", home: "/Users/me")

        XCTAssertEqual(path.components(separatedBy: ":").filter { $0 == "/opt/homebrew/bin" }.count, 1)
        XCTAssertEqual(path.components(separatedBy: ":").filter { $0 == "/usr/bin" }.count, 1)
    }

    func testEmptyInheritedPathStillGetsTools() {
        XCTAssertTrue(ToolPath.augmented(nil, home: "/Users/me").hasPrefix("/opt/homebrew/bin:/opt/homebrew/sbin"))
        XCTAssertTrue(ToolPath.augmented("", home: "/Users/me").contains("/usr/local/bin"))
    }

    func testTildeEntriesUseTheHomeDirectory() {
        XCTAssertTrue(ToolPath.augmented(nil, home: "/Users/me").contains("/Users/me/.local/bin"))
    }

    func testChildEnvironmentUsesTheAugmentedPath() {
        let environment = CLIRunner.childEnvironment()

        XCTAssertTrue(environment["PATH"]?.contains("/opt/homebrew/bin") ?? false)
        XCTAssertEqual(environment["PYTHONUNBUFFERED"], "1")
    }
}

final class BundledCLITests: XCTestCase {
    /// A fake app bundle's Resources folder holding a frozen CLI.
    private func resources(withCLI: Bool) throws -> URL {
        let resources = FileManager.default.temporaryDirectory
            .appendingPathComponent("nx2mac-bundle-\(UUID().uuidString)/Contents/Resources")
        let cli = resources.appendingPathComponent("cli")
        try FileManager.default.createDirectory(at: cli, withIntermediateDirectories: true)
        if withCLI {
            let binary = cli.appendingPathComponent("nx2mac")
            try Data("#!/bin/sh\nexit 0\n".utf8).write(to: binary)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: binary.path)
        }
        return resources
    }

    func testBundledCLIIsFoundInsideTheApp() throws {
        let resources = try resources(withCLI: true)

        let command = try XCTUnwrap(CLILocator.bundledCommand(resources: resources))

        XCTAssertTrue(command.isBundled)
        XCTAssertEqual(command.prefix.first, resources.appendingPathComponent("cli/nx2mac").path)
        XCTAssertEqual(command.workingDirectory?.lastPathComponent, "cli")
    }

    func testMissingBundleCLIIsNotReported() throws {
        XCTAssertNil(CLILocator.bundledCommand(resources: try resources(withCLI: false)))
        XCTAssertNil(CLILocator.bundledCommand(resources: nil))
    }

    /// The frozen copy must win over whatever happens to be on PATH, so a user
    /// without an installed CLI (or with an older one) still gets this app's CLI.
    func testBundledCLIWinsOverAnInstalledOne() throws {
        let app = FileManager.default.temporaryDirectory
            .appendingPathComponent("Fake-\(UUID().uuidString).app")
        let contents = app.appendingPathComponent("Contents")
        try FileManager.default.createDirectory(
            at: contents.appendingPathComponent("Resources/cli"), withIntermediateDirectories: true)
        let binary = contents.appendingPathComponent("Resources/cli/nx2mac")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: binary)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: binary.path)
        let plist: [String: Any] = [
            "CFBundleIdentifier": "org.nx2mac.fake",
            "CFBundleName": "fake",
            "CFBundlePackageType": "APPL",
            "CFBundleExecutable": "fake",
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try data.write(to: contents.appendingPathComponent("Info.plist"))

        let bundle = try XCTUnwrap(Bundle(url: app))
        guard let located = CLILocator.locate(bundle: bundle) else {
            return XCTFail("no CLI located")
        }

        XCTAssertTrue(located.isBundled, "expected the bundled CLI, got \(located.display)")
        XCTAssertTrue(located.prefix.first?.hasSuffix("Contents/Resources/cli/nx2mac") ?? false)
    }
}

final class ProbePayloadTests: XCTestCase {
    func testDecodesProbeDocument() throws {
        let json = """
        {"path": "/dumps/Dead Cells", "kind": "switch1",
         "game_file": "/dumps/Dead Cells/base_0100646009FBE000.nsp", "needs_prod_keys": true,
         "packages": [{"path": "/dumps/Dead Cells/base_0100646009FBE000.nsp", "extension": ".nsp",
                       "title_id": "0100646009FBE000", "role": "base", "size": 465011002},
                      {"path": "/dumps/Dead Cells/u_0100646009FBE800.nsp", "extension": ".nsp",
                       "title_id": "0100646009FBE800", "role": "update", "size": 2100541718}]}
        """

        let payload = try JSONDecoder().decode(ProbePayload.self, from: Data(json.utf8))

        XCTAssertEqual(payload.kind, "switch1")
        XCTAssertTrue(payload.needsProdKeys)
        XCTAssertEqual(payload.packages.map(\.role), ["base", "update"])
        XCTAssertEqual(payload.gameFile?.hasSuffix("base_0100646009FBE000.nsp"), true)
    }
}

@MainActor
final class OutputFolderDefaultTests: XCTestCase {
    /// A temp folder with a fake dump folder and a package file inside it.
    private func fixture() throws -> (root: URL, folder: URL, file: URL) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("nx2mac-gui-\(UUID().uuidString)")
        let folder = root.appendingPathComponent("My Game [0100]")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent("Game_0100.nsp")
        try Data([0]).write(to: file)
        return (root, folder, file)
    }

    func testOutputFolderFollowsTheGame() throws {
        let (root, folder, file) = try fixture()
        let model = BuildModel()

        model.setGameFile(folder)
        XCTAssertEqual(model.outDirURL?.standardizedFileURL.path, folder.standardizedFileURL.path)

        // a package file defaults to the directory holding it
        model.setGameFile(file)
        XCTAssertEqual(model.outDirURL?.standardizedFileURL.path, folder.standardizedFileURL.path)

        model.setGameFile(nil)
        XCTAssertNil(model.gameURL)
    }

    func testAChosenOutputFolderStopsFollowingTheGame() throws {
        let (root, folder, file) = try fixture()
        let chosen = root.appendingPathComponent("Project")
        let model = BuildModel()

        model.setGameFile(folder)
        model.chooseOutDir(chosen)
        model.setGameFile(file)  // switching games must not clobber the choice

        XCTAssertFalse(model.outDirIsAuto)
        XCTAssertEqual(model.outDirURL?.standardizedFileURL.path, chosen.standardizedFileURL.path)
    }

    func testResetReturnsToTheGameDirectory() throws {
        let (root, folder, file) = try fixture()
        let model = BuildModel()
        model.setGameFile(file)
        model.chooseOutDir(root.appendingPathComponent("Project"))

        model.resetOutDir()

        XCTAssertTrue(model.outDirIsAuto)
        XCTAssertEqual(model.outDirURL?.standardizedFileURL.path, folder.standardizedFileURL.path)
    }
}

final class SelectionListTests: XCTestCase {
    func testHeightGrowsOneRowPerItem() {
        XCTAssertEqual(SelectionList.height(for: 0), 0)
        XCTAssertEqual(SelectionList.height(for: 1), SelectionList.rowHeight + SelectionList.padding)
        XCTAssertEqual(SelectionList.height(for: 3), 3 * SelectionList.rowHeight + SelectionList.padding)
    }

    func testHeightStopsAfterFiveRows() {
        let five = SelectionList.height(for: 5)
        XCTAssertEqual(SelectionList.height(for: 6), five)
        XCTAssertEqual(SelectionList.height(for: 200), five)
        XCTAssertFalse(SelectionList.scrolls(at: 5))
        XCTAssertTrue(SelectionList.scrolls(at: 6))
    }
}

final class EngineCommandTests: XCTestCase {
    func testPinKeepsRefOutOfTheSourceOption() {
        let arguments = EngineCommand.pin(source: "fork", ref: " fork-customizations ")

        XCTAssertEqual(arguments, ["engine", "fork-customizations", "--source", "fork", "--json"])
        // regression: the ref used to land between --source and its value
        if let sourceIndex = arguments.firstIndex(of: "--source") {
            XCTAssertEqual(arguments[sourceIndex + 1], "fork")
        }
    }

    func testPinOmitsEmptyRef() {
        XCTAssertEqual(EngineCommand.pin(source: "upstream", ref: "   "),
                       ["engine", "--source", "upstream", "--json"])
    }

    func testShowDoesNotTouchTheNetwork() {
        XCTAssertEqual(EngineCommand.show(), ["engine", "--show", "--json"])
    }
}

final class BuildRequestTests: XCTestCase {
    func testArgumentsIncludeKeysOutDirAndExtras() {
        let request = BuildRequest(
            game: URL(fileURLWithPath: "/games/My Game"),
            keys: URL(fileURLWithPath: "/games/prod.keys"),
            outDir: URL(fileURLWithPath: "/out/MyGameProject"),
            updates: [URL(fileURLWithPath: "/extras/update.nsp")],
            dlcs: [URL(fileURLWithPath: "/extras/dlc-a.nsp"), URL(fileURLWithPath: "/extras/dlc-b.nsp")],
            source: "fork",
            ref: " fork-customizations "
        )

        XCTAssertEqual(request.arguments, [
            "build", "/games/My Game", "--json", "--source", "fork",
            "--ref", "fork-customizations",
            "--keys", "/games/prod.keys",
            "--out-dir", "/out/MyGameProject",
            "--update", "/extras/update.nsp",
            "--dlc", "/extras/dlc-a.nsp",
            "--dlc", "/extras/dlc-b.nsp",
        ])
        XCTAssertEqual(request.extrasSummary, "1 update + 2 DLC")
    }

    func testLibraryIsPassedInsteadOfAnOutDir() {
        let request = BuildRequest(
            game: URL(fileURLWithPath: "/games/My Game"),
            library: URL(fileURLWithPath: "/Users/me/Games"),
            outDir: nil
        )

        XCTAssertTrue(request.arguments.contains("--library"))
        XCTAssertFalse(request.arguments.contains("--out-dir"))
        XCTAssertEqual(request.arguments[request.arguments.firstIndex(of: "--library")! + 1], "/Users/me/Games")
    }

    func testAChosenOutDirWinsOverTheLibrary() {
        let request = BuildRequest(
            game: URL(fileURLWithPath: "/games"),
            library: nil,
            outDir: URL(fileURLWithPath: "/tmp/Project")
        )

        XCTAssertTrue(request.arguments.contains("--out-dir"))
        XCTAssertFalse(request.arguments.contains("--library"))
    }

    func testArgumentsOmitOptionalInputs() {
        let request = BuildRequest(game: URL(fileURLWithPath: "/g"), source: "upstream", ref: "")

        XCTAssertEqual(request.arguments, ["build", "/g", "--json", "--source", "upstream"])
        XCTAssertEqual(request.extrasSummary, "game folder only")
    }

    func testMultipleUpdatesAreRepeatable() {
        let request = BuildRequest(
            game: URL(fileURLWithPath: "/g"),
            updates: [URL(fileURLWithPath: "/u/1.nsp"), URL(fileURLWithPath: "/u/2.nsz")]
        )

        XCTAssertEqual(request.arguments.filter { $0 == "--update" }.count, 2)
        XCTAssertEqual(request.extrasSummary, "2 update")
    }
}

/// The full path the Build button takes: stream `build --json` for a real game
/// and require a result event pointing at an existing `.app`.
///
/// Opt-in, because it builds for real:
/// ```
/// NX2MAC_LIVE_BUILD=1 NX2MAC_TEST_GAME="/path/to/dump" NX2MAC_TEST_KEYS=/path/prod.keys \
///   swift test --package-path gui --filter LiveBuildTests
/// ```
final class LiveBuildTests: XCTestCase {
    func testBuildsRealGameThroughStreamingBridge() throws {
        let environment = ProcessInfo.processInfo.environment
        try XCTSkipUnless(environment["NX2MAC_LIVE_BUILD"] == "1", "set NX2MAC_LIVE_BUILD=1 to build for real")
        guard let game = environment["NX2MAC_TEST_GAME"] else { return XCTFail("NX2MAC_TEST_GAME is unset") }
        guard let command = CLILocator.locate() else { return XCTFail("no nx2mac CLI found") }

        var arguments = ["build", game, "--json"]
        if let keys = environment["NX2MAC_TEST_KEYS"] { arguments += ["--keys", keys] }
        if let output = environment["NX2MAC_TEST_OUTPUT"] { arguments += ["-o", output] }
        if let source = environment["NX2MAC_TEST_SOURCE"] { arguments += ["--source", source] }
        if let ref = environment["NX2MAC_TEST_REF"] { arguments += ["--ref", ref] }

        let finished = expectation(description: "build finished")
        let lines = LockedLines()
        var status: Int32 = -1
        _ = try CLIRunner.stream(
            command,
            arguments,
            onLine: { lines.appendLine($0) },
            onExit: { code in status = code; finished.fulfill() }
        )
        wait(for: [finished], timeout: 3 * 60 * 60)

        let events = lines.value.compactMap(BuildEvent.parse)
        let engineEvent = events.compactMap { if case .engine(let engine) = $0 { return engine } else { return nil } }.first
        XCTAssertNotNil(engineEvent, "expected an engine event first")
        XCTAssertTrue(events.contains { if case .log = $0 { return true } else { return false } }, "expected engine log lines")

        guard let result = events.compactMap({ if case .result(let result) = $0 { return result } else { return nil } }).last else {
            return XCTFail("no result event; tail: \(lines.value.suffix(5))")
        }
        XCTAssertEqual(status, result.returncode)
        if result.ok {
            let app = try XCTUnwrap(result.appPath, "a successful build must report app_path")
            XCTAssertTrue(FileManager.default.fileExists(atPath: app.path), "missing \\(app.path)")
        }
    }
}
