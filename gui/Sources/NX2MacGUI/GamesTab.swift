import AppKit
import SwiftUI

/// The Games tab: every game built into the chosen library. Scanning is limited
/// to built games (a folder with an `nx2mac.json` manifest or a `.app`), so this
/// never looks at game dumps — see `nx2mac library --json`.
struct GamesTab: View {
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Games").font(.title2.weight(.semibold))
                    // No library set: say nothing here rather than an empty-state paragraph.
                    if !model.libraryDirs.isEmpty {
                        Text(model.libraryDirs.count == 1
                             ? model.libraryDirs[0]
                             : "\(model.libraryDirs.count) libraries")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .help(model.libraryDirs.joined(separator: "\n"))
                    }
                }
                Spacer()
                if !model.libraryDirs.isEmpty {
                    libraryMenu
                    cloudMenu
                    playerPicker
                    Button {
                        Task { await model.refreshLibrary() }
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                    }
                    .disabled(model.libraryBusy)
                }
            }

            Divider()

            content
        }
        .task { await model.refreshLibrary() }
    }

    /// The library folders: add another one, or forget one.
    private var libraryMenu: some View {
        Menu {
            ForEach(Array(model.libraryDirs.enumerated()), id: \.offset) { _, directory in
                Menu(directory) {
                    Button("Reveal in Finder") {
                        NSWorkspace.shared.open(URL(fileURLWithPath: directory))
                    }
                    Button("Remove from libraries") {
                        Task { await model.removeLibrary(directory) }
                    }
                }
            }
            Divider()
            Button("Add folder…") { chooseLibrary() }
            if !model.libraryError.isEmpty {
                Divider()
                Text(model.libraryError)
            }
        } label: {
            Label("Library", systemImage: "folder.badge.plus")
        }
        .menuStyle(.borderlessButton)
        .frame(width: 105)
        .help("Folders that hold your built games (more than one is fine)")
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
            EmptyView()  // nothing to list, and no library folder to talk about
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
