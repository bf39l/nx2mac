import AppKit
import Foundation

/// One build's inputs and live state. The Build tab holds a queue of these, run one after another.
@MainActor
final class BuildSetup: ObservableObject, Identifiable {
    let id = UUID()

    // MARK: Game inputs

    /// The dump folder (optional) and the package file inside it. Picking a folder
    /// auto-selects its game package; picking a file by hand overrides that.
    @Published var gameURL: URL?
    @Published var gameDirURL: URL?
    /// True while `gameURL` is just what the folder scan found (not user-chosen).
    @Published var gameFileIsAuto = false
    /// What `nx2mac probe` found in the folder, for the list under the rows.
    @Published var packages: [GamePackage] = []
    @Published var keysURL: URL?
    /// Where the .app and its build folder go. Until the user picks one, this is
    /// the game library (when set) or the game's own directory.
    @Published var outDirURL: URL?
    /// True while `outDirURL` is the default rather than something the user chose.
    @Published var outDirIsAuto = true
    /// The update package to build with (one, like the game file). Empty = none.
    @Published var updateURL: URL?
    /// True while the update row is just the newest update found beside the game.
    @Published var updateIsAuto = false
    /// Optional DLC packages/folders picked outside the game folder (staged by the CLI).
    @Published var dlcURLs: [URL] = []

    // MARK: Cheat file (the Build tab's one cheat file, re-anchored by each build)

    @Published var cheatFileURL: URL?

    // MARK: Build state

    @Published var state: BuildState = .idle
    @Published var log: [String] = []
    @Published var stage: String = ""
    @Published var appURL: URL?
    /// A stop the engine can be told to ignore (`--allow-damaged`, `--allow-other-metal`, …).
    @Published var buildAnyway: BuildResult.BuildFailure?

    var process: Process?
    /// Lines waiting for the next UI tick: a build prints in bursts, and one published
    /// change per line would make SwiftUI re-lay-out hundreds of times a second.
    var pendingLog: [String] = []
    var logTimer: Timer?

    var isRunning: Bool { state == .running }

    /// A short label for the row: the game's name once picked, else its build number is shown by the list.
    var title: String {
        if let file = gameURL?.deletingPathExtension().lastPathComponent, !file.isEmpty { return file }
        if let dir = gameDirURL?.lastPathComponent, !dir.isEmpty { return dir }
        return "Build"
    }

    /// What the cheat commands address: the title ID (how the library finds the game), else its name.
    var gameTitleID: String? {
        chosenPackage?.titleID ?? packages.first(where: { $0.role == "base" })?.titleID
            ?? packages.compactMap(\.titleID).first
    }

    /// The package a UI should pre-select (the base game, else the biggest one).
    var chosenPackage: GamePackage? {
        packages.first { $0.path == gameURL?.path } ?? packages.first
    }
}
