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
                    Text(model.libraryDir ?? "No game library yet")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer()
                Button {
                    Task { await model.refreshLibrary() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(model.libraryBusy)
            }

            Divider()

            content
        }
        .task { await model.refreshLibrary() }
    }

    @ViewBuilder
    private var content: some View {
        if model.libraryDir == nil {
            placeholder(
                "Choose a game library in the Setup tab.\n"
                    + "nx2mac files every build into its own folder there: the app plus saves, logs, mods and cheats."
            )
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

/// One library row: the app's own icon, what it is, and the folders beside it.
private struct GameRow: View {
    let game: LibraryGame
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            icon
            VStack(alignment: .leading, spacing: 2) {
                Text(game.name).font(.callout.weight(.semibold))
                if !game.subtitle.isEmpty {
                    Text(game.subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                HStack(spacing: 10) {
                    Label(game.sizeLabel, systemImage: "internaldrive")
                    Text(game.builtLabel)
                    if let played = game.lastPlayed, let described = ISODate.describe(played) {
                        Label("played \(described)", systemImage: "clock")
                    }
                    if !game.hasApp {
                        Label("app missing", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                }
                .font(.caption2)
                .foregroundStyle(.tertiary)
            }

            Spacer(minLength: 8)

            if model.playing == game.name {
                ProgressView().controlSize(.small)
            } else {
                Button {
                    Task { await model.play(game) }
                } label: {
                    Label("Play", systemImage: "play.fill")
                }
                .disabled(!game.hasApp)
            }

            playersMenu

            Menu {
                Button("Saves") { open(game.saves) }
                Button("Logs") { open(game.logs) }
                Button("Mods") { open(game.mods) }
                Button("Cheats") { open(game.cheats) }
                Button("Screenshots") { open(game.screenshots) }
                Divider()
                Button("Game folder") { open(game.path) }
            } label: {
                Label("Data", systemImage: "folder")
            }
            .menuStyle(.borderlessButton)
            .frame(width: 88)

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

    /// Who plays this game: pick an existing player or add one with a preferred name.
    private var playersMenu: some View {
        Menu {
            let entry = model.players[game.path]
            if let entry, !entry.players.isEmpty {
                ForEach(entry.players) { player in
                    Button {
                        Task { await model.selectPlayer(player, for: game) }
                    } label: {
                        if player.id == entry.active {
                            Label(player.name, systemImage: "checkmark")
                        } else {
                            Text(player.name)
                        }
                    }
                }
                Divider()
            }
            Button("New player…") { createPlayer() }
            if let active = model.players[game.path]?.activeName {
                Divider()
                Text("playing as \(active)")
            }
        } label: {
            Label(playersLabel, systemImage: "person.crop.circle")
        }
        .menuStyle(.borderlessButton)
        .frame(width: 118)
        .help("Choose who plays this game; each player keeps their own saves")
    }

    private var playersLabel: String {
        model.players[game.path]?.activeName ?? "Players"
    }

    private func createPlayer() {
        let alert = NSAlert()
        alert.messageText = "New player"
        alert.informativeText = "The name the game shows for this player. They get their own saves."
        alert.addButton(withTitle: "Create")
        alert.addButton(withTitle: "Cancel")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 240, height: 24))
        field.placeholderString = "Player name"
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let name = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        Task { await model.createPlayer(for: game, name: name) }
    }

    /// The built app's own icon (Finder-style); a symbol when it is gone.
    private var icon: some View {
        Group {
            if let app = game.appURL, game.hasApp {
                Image(nsImage: NSWorkspace.shared.icon(forFile: app.path))
                    .resizable()
                    .frame(width: 40, height: 40)
            } else {
                Image(systemName: "app.dashed")
                    .font(.title2)
                    .foregroundStyle(.secondary)
                    .frame(width: 40, height: 40)
            }
        }
    }

    private func open(_ path: String) {
        let url = URL(fileURLWithPath: path)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }
}
