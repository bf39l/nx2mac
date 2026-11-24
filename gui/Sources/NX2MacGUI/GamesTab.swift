import AppKit
import SwiftUI

/// The Games tab: every game built into the chosen library. Scanning is limited
/// to built games (a folder with an `nx2mac.json` manifest or a `.app`), so this
/// never looks at game dumps — see `nx2mac library --json`.
struct GamesTab: View {
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // The library folders stay at the top, whether or not one is chosen yet.
            libraryList
            Divider()
            HStack(alignment: .top) {
                Text("Games").font(.title2.weight(.semibold))
                Spacer()
                if !model.libraryDirs.isEmpty {
                    cloudMenu
                    playerPicker
                }
                Button {
                    Task { await model.refreshLibrary() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(model.libraryBusy)
            }

            content
        }
        .task { await model.refreshLibrary() }
    }

    /// The library folders, one row each (like the DLC list): remove one, add another.
    @ViewBuilder
    private var libraryList: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text("Libraries").font(.callout.weight(.medium))
                Text(model.libraryDirs.isEmpty ? "none yet" : "\(model.libraryDirs.count)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Add…") { chooseLibrary() }
                if model.libraryDirs.count > 1 {
                    Button("Clear all…") { confirmClearLibraries() }
                }
            }
            if !model.libraryDirs.isEmpty {
                VStack(spacing: 2) {
                    ForEach(Array(model.libraryDirs.enumerated()), id: \.offset) { _, directory in
                        HStack(spacing: 6) {
                            Image(systemName: "folder")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            Text(directory)
                                .font(.caption.monospaced())
                                .lineLimit(1)
                                .truncationMode(.middle)
                                .help(directory)
                            Spacer(minLength: 4)
                            Button {
                                confirmRemoveLibrary(directory)
                            } label: {
                                Image(systemName: "minus.circle.fill")
                            }
                            .buttonStyle(.plain)
                            .foregroundStyle(.secondary)
                            .help("Remove this library (nothing on disk is deleted)")
                        }
                        .padding(.vertical, 1)
                    }
                }
                .padding(.horizontal, 8)
                .padding(.vertical, SelectionList.padding / 2)
                .frame(height: SelectionList.height(for: model.libraryDirs.count))
                .background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
            }
            if !model.libraryError.isEmpty {
                Text(model.libraryError).font(.caption2).foregroundStyle(.orange)
            }
        }
        .padding(9)
        .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
    }

    /// Forgetting a library never deletes anything: say so before it happens.
    private func confirmRemoveLibrary(_ directory: String) {
        let alert = NSAlert()
        alert.messageText = "Remove this library?"
        alert.informativeText = "\(directory)\n\nnx2mac forgets the folder; the games, saves and mods in it "
            + "stay exactly where they are."
        alert.addButton(withTitle: "Remove")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        Task { await model.removeLibrary(directory) }
    }

    private func confirmClearLibraries() {
        let alert = NSAlert()
        alert.messageText = "Clear every library?"
        alert.informativeText = "nx2mac forgets all \(model.libraryDirs.count) folders. Nothing on disk is deleted; "
            + "add a folder again to see its games."
        alert.addButton(withTitle: "Clear")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        Task { await model.clearLibraries() }
    }

    private func chooseLibrary() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Add as library"
        panel.message = "Pick a folder for built games (it must not be a game dump)."
        if panel.runModal() == .OK, let url = panel.url {
            Task { await model.addLibrary(url) }
        }
    }

    /// Where the engine copies each game's save (iCloud Drive, Dropbox, …); local first.
    private var cloudMenu: some View {
        Menu {
            if model.cloudDir.isEmpty {
                Text("Saves stay in the library only")
            } else {
                Text(model.cloudDir)
                Divider()
            }
            Button("Choose folder…") { chooseCloud() }
            if !model.cloudDir.isEmpty {
                Button("Off (local only)") { Task { await model.clearCloud() } }
            }
        } label: {
            Label(model.cloudDir.isEmpty ? "Cloud: off" : "Cloud saves", systemImage: "arrow.triangle.2.circlepath.icloud")
        }
        .menuStyle(.borderlessButton)
        .frame(width: 130)
        .help("Keep saves local and copy them into a synced folder")
    }

    private func chooseCloud() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Sync saves here"
        panel.message = "Pick a folder that syncs (iCloud Drive, Dropbox, …). The game keeps its local save."
        if panel.runModal() == .OK, let url = panel.url {
            Task { await model.setCloud(url) }
        }
    }

    /// The library's players (one list for every game), chosen here for all of them.
    private var playerPicker: some View {
        Menu {
            let players = model.libraryPlayers
            if players.isEmpty {
                Text("No players yet")
            } else {
                ForEach(players) { player in
                    Button {
                        Task { await model.selectPlayer(player) }
                    } label: {
                        if player.id == model.activePlayerID {
                            Label(player.name, systemImage: "checkmark")
                        } else {
                            Text(player.name)
                        }
                    }
                }
                Divider()
            }
            Button("New player…") { createPlayer() }
        } label: {
            Label(model.activePlayerName ?? "Players", systemImage: "person.crop.circle")
        }
        .menuStyle(.borderlessButton)
        .frame(width: 150)
        .help("Who plays: every game keeps a save store per player")
    }

    private func createPlayer() {
        let alert = NSAlert()
        alert.messageText = "New player"
        alert.informativeText = "The name the games show for this player. They get their own saves in every game."
        alert.addButton(withTitle: "Create")
        alert.addButton(withTitle: "Cancel")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 240, height: 24))
        field.placeholderString = "Player name"
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let name = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        Task { await model.createPlayer(name: name) }
    }

    @ViewBuilder
    private var content: some View {
        if model.libraryDirs.isEmpty {
            placeholder("No library folder yet.\nAdd one above and its built games appear here.")
        } else if model.games.isEmpty {
            placeholder("No games built into this library yet.\nGo to the Build tab and build one.")
        } else {
            ScrollView {
                LazyVStack(spacing: 6) {
                    ForEach(model.games) { game in
                        GameRow(game: game)
                    }
                }
            }
        }
    }

    private func placeholder(_ text: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Image(systemName: "square.stack.3d.up.slash")
                .font(.largeTitle)
                .foregroundStyle(.tertiary)
            Text(text)
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
        .multilineTextAlignment(.center)
    }
}

/// One built game: its own app icon, the name, and the version it was built at.
private struct GameRow: View {
    let game: LibraryGame
    @EnvironmentObject private var model: BuildModel
    @State private var showingCheats = false

    var body: some View {
        HStack(spacing: 12) {
            icon
            VStack(alignment: .leading, spacing: 1) {
                Text(game.name)
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)
                Text(versionLabel)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            if !game.hasApp {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                    .help("The app is missing from this folder")
            }

            if model.playing == game.name {
                ProgressView().controlSize(.small)
            } else {
                Button {
                    Task { await model.play(game) }
                } label: {
                    Image(systemName: "play.fill")
                }
                .help("Play")
                .disabled(!game.hasApp)
            }

            Button {
                showingCheats = true
            } label: {
                Image(systemName: "chevron.left.forwardslash.chevron.right")
            }
            .help("Cheats: list the switches and turn them on or off")
            .sheet(isPresented: $showingCheats) {
                CheatsSheet(game: game)
                    .environmentObject(model)
            }

            Menu {
                Button("Saves") { open(game.saves) }
                Button("Logs") { open(game.logs) }
                Button("Mods") { open(game.mods) }
                Button("Cheats") { open(game.cheats) }
                Button("Screenshots") { open(game.screenshots) }
                Divider()
                Button("Game folder") { open(game.path) }
                if let commit = game.engineCommit {
                    Divider()
                    Text("\(game.engineRef ?? "engine") @ \(commit.prefix(12))")
                }
                if let played = game.lastPlayed, let described = ISODate.describe(played) {
                    Text("last played \(described)")
                }
            } label: {
                Image(systemName: "folder")
            }
            .menuStyle(.borderlessButton)
            .frame(width: 40)
            .help("Saves, logs, mods, cheats and the game folder")

            Button {
                if let app = game.appURL { NSWorkspace.shared.activateFileViewerSelecting([app]) }
            } label: {
                Image(systemName: "magnifyingglass")
            }
            .help("Reveal the app in Finder")
            .disabled(!game.hasApp)
        }
        .padding(9)
        .background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 8))
    }

    /// "v1.25.0", or a plain note when the bundle carries no version.
    private var versionLabel: String {
        guard let version = game.version, !version.isEmpty else { return "version unknown" }
        return version.hasPrefix("v") ? version : "v\(version)"
    }

    /// The built app's own icon (Finder-style); a symbol when it is gone.
    private var icon: some View {
        Group {
            if let app = game.appURL, game.hasApp {
                Image(nsImage: NSWorkspace.shared.icon(forFile: app.path))
                    .resizable()
                    .frame(width: 44, height: 44)
            } else {
                Image(systemName: "app.dashed")
                    .font(.title2)
                    .foregroundStyle(.secondary)
                    .frame(width: 44, height: 44)
            }
        }
    }

    private func open(_ path: String) {
        let url = URL(fileURLWithPath: path)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }
}


/// The cheats of one game: the files found in its `mods/cheats`, with a switch per cheat.
private struct CheatsSheet: View {
    let game: LibraryGame
    @EnvironmentObject private var model: BuildModel
    @Environment(\.dismiss) private var dismiss

    @State private var builds: [CheatBuild] = []
    @State private var state: CheatsState = .unknown
    @State private var loading = true

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Cheats — \(game.name)").font(.title3.weight(.semibold))
                    Text("What the in-game Cheats menu shows; the switches are saved per game build.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }

            if state.safeMode || state.masterOff {
                HStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                    VStack(alignment: .leading, spacing: 1) {
                        if state.masterOff {
                            Text("Master switch off — no cheat runs.").font(.caption)
                        }
                        if state.safeMode {
                            Text("Safe mode: the last run ended uncleanly with cheats on, so every cheat starts off.")
                                .font(.caption)
                        }
                    }
                    Spacer()
                    if state.masterOff {
                        Button("Turn cheats on") { Task { await model.setCheatsMaster(for: game, off: false); await load() } }
                    } else {
                        Button("Turn all off") { Task { await model.setCheatsMaster(for: game, off: true); await load() } }
                    }
                    if state.safeMode {
                        Button("Restore switches") {
                            Task { await model.restoreCheatSwitches(for: game); await load() }
                        }
                    }
                }
                .padding(8)
                .background(Color.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
            }

            if loading {
                ProgressView().frame(maxWidth: .infinity, alignment: .center)
            } else if builds.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("No cheat files yet.").font(.callout)
                    Text("Put an Atmosphère cheat file at\n<game>/mods/cheats/<build id>.txt")
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                    Button("Open the cheats folder") {
                        try? FileManager.default.createDirectory(at: URL(fileURLWithPath: cheatsFolder),
                                                                 withIntermediateDirectories: true)
                        NSWorkspace.shared.open(URL(fileURLWithPath: cheatsFolder))
                    }
                }
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(builds) { build in
                            VStack(alignment: .leading, spacing: 4) {
                                Text("build \(build.build)")
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                                ForEach(build.cheats) { cheat in
                                    Toggle(isOn: Binding(
                                        get: { cheat.enabled },
                                        set: { value in Task { await toggle(cheat, build, value) } }
                                    )) {
                                        Text(cheat.name).font(.callout)
                                    }
                                    .toggleStyle(.switch)
                                }
                            }
                            .padding(8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                        }
                    }
                }
            }

            HStack {
                Button("Open the cheats folder") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: cheatsFolder))
                }
                Spacer()
                Button("Refresh") { Task { await load() } }
            }
        }
        .padding(16)
        .frame(width: 520, height: 420)
        .task { await load() }
    }

    private var cheatsFolder: String {
        URL(fileURLWithPath: game.mods).appendingPathComponent("cheats").path
    }

    private func load() async {
        loading = true
        let result = await model.cheats(for: game)
        builds = result.builds
        state = result.state
        loading = false
    }

    private func toggle(_ cheat: CheatEntry, _ build: CheatBuild, _ enabled: Bool) async {
        await model.setCheat(cheat, build: build, for: game, enabled: enabled)
        await load()
    }
}
