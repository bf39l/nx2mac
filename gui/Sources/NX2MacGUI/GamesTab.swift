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
            cloudList
            Divider()
            HStack(alignment: .top) {
                Text("Games").font(.title2.weight(.semibold))
                Spacer()
                if !model.libraryDirs.isEmpty {
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

    /// The cloud-saves folder, shaped like the library list — but only one is allowed.
    @ViewBuilder
    private var cloudList: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text("Cloud saves").font(.callout.weight(.medium))
                Text(model.cloudDir.isEmpty ? "off — saves stay in the library" : "1 folder")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if model.cloudDir.isEmpty {
                    Button("Choose folder…") { chooseCloud() }
                }
            }
            if !model.cloudDir.isEmpty {
                VStack(spacing: 2) {
                    HStack(spacing: 6) {
                        Image(systemName: "arrow.triangle.2.circlepath.icloud")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(model.cloudDir)
                            .font(.caption.monospaced())
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .help(model.cloudDir)
                        Spacer(minLength: 4)
                        Button {
                            Task { await model.clearCloud() }
                        } label: {
                            Image(systemName: "minus.circle.fill")
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(.secondary)
                        .help("Stop syncing saves (the local saves stay)")
                    }
                    .padding(.vertical, 1)
                }
                .padding(.horizontal, 8)
                .padding(.vertical, SelectionList.padding / 2)
                .frame(height: SelectionList.height(for: 1))
                .background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
            }
            Text("One folder: the game keeps playing from its local save and copies it to "
                 + "<folder>/<title id>/save.dmsync (per player below it).")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(9)
        .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
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
    @State private var showingLog = false

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

            Button {
                showingLog = true
            } label: {
                Image(systemName: "doc.text.magnifyingglass")
            }
            .help("The game's own log (the engine writes it on every run)")
            .sheet(isPresented: $showingLog) {
                GameLogSheet(game: game)
            }

            Menu {
                Button("Saves") { open(game.saves) }
                Button("Open log folder") { open(game.logs) }
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

            if !state.masterOff, builds.contains(where: { $0.cheats.contains { $0.enabled } }) {
                HStack(spacing: 8) {
                    Text("\(builds.flatMap(\.cheats).filter(\.enabled).count) cheat(s) on — "
                         + "if the game crashes, turn them off and try one at a time.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Turn all off") {
                        Task { await model.setCheatsMaster(for: game, off: true); await load() }
                    }
                }
                .padding(8)
                .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
            }

            // Two switches the engine itself keeps, so they are toggles here rather than actions
            // you have to remember: "cheats on" is the master switch file, "safe mode" is the marker
            // a crashed run leaves behind. Turning safe mode off re-applies the saved switches.
            VStack(alignment: .leading, spacing: 6) {
                Toggle(isOn: Binding(
                    get: { !state.masterOff },
                    set: { on in Task { await model.setCheatsMaster(for: game, off: !on); await load() } }
                )) {
                    Text("Cheats on").font(.callout)
                    Text(state.masterOff ? "the engine runs none at all" : "the switches below are what runs")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                Toggle(isOn: Binding(
                    get: { state.safeMode },
                    set: { on in
                        // Only the engine turns safe mode on (an unclean exit with cheats on); turning
                        // it off is ours: drop the marker so the saved switches apply again.
                        guard !on else { return }
                        Task { await model.restoreCheatSwitches(for: game); await load() }
                    }
                )) {
                    Text("Safe mode").font(.callout)
                    Text(state.safeMode
                         ? "the last run ended uncleanly with cheats on — every cheat starts off"
                         : "off: the saved switches apply at launch")
                        .font(.caption2)
                        .foregroundStyle(state.safeMode ? Color.orange : Color.secondary)
                }
                .disabled(!state.safeMode)
            }
            .padding(8)
            .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))

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
                                    if cheat.switchable && cheat.blocked == nil {
                                        Toggle(isOn: Binding(
                                            get: { cheat.enabled },
                                            set: { value in Task { await toggle(cheat, build, value) } }
                                        )) {
                                            HStack(spacing: 6) {
                                                Text(cheat.name).font(.callout)
                                                if cheat.unanchored {
                                                    Text("not working here — don't enable")
                                                        .font(.caption2)
                                                        .foregroundStyle(.orange)
                                                } else if let warning = cheat.warning, !warning.isEmpty {
                                                    // The compile anchored it, but it writes code: the port's
                                                    // own rewriter owns that code, so it can take the game down.
                                                    Image(systemName: "exclamationmark.triangle")
                                                        .font(.caption2)
                                                        .foregroundStyle(.orange)
                                                        .help(warning)
                                                }
                                            }
                                        }
                                        .toggleStyle(.switch)
                                    } else if cheat.kind != "label" {
                                        HStack {
                                            Text(cheat.name).font(.callout)
                                            if cheat.blocked != nil {
                                                Image(systemName: "nosign").font(.caption2).foregroundStyle(.orange)
                                            }
                                            Spacer()
                                            Text(cheat.blocked
                                                 ?? (cheat.kind == "master" ? "always on"
                                                     : cheat.broken ? "broken" : "one-pass"))
                                                .font(.caption2)
                                                .foregroundStyle(cheat.blocked == nil ? Color.secondary : Color.orange)
                                        }
                                    }
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

    /// Flip one switch: the CLI writes `.enabled`, and the local row is updated in place — the
    /// whole list is not re-read for one toggle.
    private func toggle(_ cheat: CheatEntry, _ build: CheatBuild, _ enabled: Bool) async {
        await model.setCheat(cheat, build: build, for: game, enabled: enabled)
        guard let index = builds.firstIndex(where: { $0.build == build.build }) else { return }
        let updated = builds[index].cheats.map { entry in
            entry.key == cheat.key
                ? CheatEntry(name: entry.name, key: entry.key, kind: entry.kind, section: entry.section,
                             broken: entry.broken, switchable: entry.switchable, enabled: enabled,
                             resolved: entry.resolved, unanchored: entry.unanchored,
                             codePatch: entry.codePatch, blocked: entry.blocked, warning: entry.warning)
                : entry
        }
        builds[index] = CheatBuild(build: build.build, file: build.file, enabledFile: build.enabledFile, cheats: updated)
    }
}


/// The game's own log, read straight from the library: the engine writes `<title id>.log` on every
/// run (and the previous one as `.prev.log`), which is what a crash has to be debugged from.
private struct GameLogSheet: View {
    let game: LibraryGame
    @Environment(\.dismiss) private var dismiss

    @State private var which: Which = .current
    @State private var text: String = ""

    private enum Which: String, CaseIterable, Identifiable {
        case current = "This run"
        case previous = "Previous run"
        var id: String { rawValue }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Log — \(game.name)").font(.title3.weight(.semibold))
                    Text(path).font(.caption.monospaced()).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle).help(path)
                }
                Spacer()
                Picker("", selection: $which) {
                    ForEach(Which.allCases) { option in Text(option.rawValue).tag(option) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 220)
                .onChange(of: which) { _, _ in load() }
            }

            ScrollView {
                Text(text.isEmpty ? "No log yet — run the game once." : text)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
            }
            .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary))

            HStack {
                Button("Refresh") { load() }
                Spacer()
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                } label: {
                    Label("Copy log", systemImage: "doc.on.doc")
                }
                .disabled(text.isEmpty)
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(16)
        .frame(width: 760, height: 520)
        .onAppear(perform: load)
    }

    private var titleID: String { game.titleID ?? (game.appURL?.deletingPathExtension().lastPathComponent ?? "") }

    private var path: String {
        let name = which == .current ? "\(titleID).log" : "\(titleID).prev.log"
        return URL(fileURLWithPath: game.logs).appendingPathComponent(name).path
    }

    private func load() {
        text = (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
    }
}
