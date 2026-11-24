import AppKit
import SwiftUI

/// The window: Setup (what the machine is missing, and where the game library
/// lives), Games (everything built into that library) and Build (pick inputs and go).
struct ContentView: View {
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        TabView {
            // Setup first: prerequisites before the first build, and it is what
            // the app opens on.
            SetupTab()
                .tabItem { Label("Setup", systemImage: "wrench.and.screwdriver") }
            GamesTab()
                .tabItem { Label("Games", systemImage: "square.stack.3d.up") }
            BuildTab()
                .tabItem { Label("Build", systemImage: "hammer") }
        }
        .padding(14)
        .frame(minWidth: 780, minHeight: 660)
        .task { await model.refresh() }
    }
}

/// One screen: inputs, engine choice, build button, live engine log.
struct BuildTab: View {
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            Divider()
            engineSection
            inputs
            actions
            LogView(lines: model.log)
        }
    }

    // MARK: Header / prerequisites

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("nx2mac").font(.title2.weight(.semibold))
                Text("Build a Nintendo Switch game into a native macOS app")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            DoctorBadge()
        }
    }

    // MARK: Inputs

    private var inputs: some View {
        VStack(alignment: .leading, spacing: 10) {
            row(title: "prod.keys") {
                pathLabel(model.keysURL?.path ?? "optional — auto-detected in/next to the game",
                          empty: model.keysURL == nil)
                Button("Choose…") { chooseKeys() }
                if model.keysURL != nil {
                    Button("Clear") { model.keysURL = nil }
                }
            }
            gameDirRow
            gameFileRow
            packageList

            updateRow
            updateList
            row(title: "DLC") {
                Text(model.dlcURLs.isEmpty
                     ? "optional — pick one or more DLC .nsp/.nsz (or a folder)"
                     : SelectionList.scrolls(at: model.dlcURLs.count)
                        ? "\(model.dlcURLs.count) selected — scroll the list"
                        : "\(model.dlcURLs.count) selected")
                    .font(.callout)
                    .foregroundStyle(model.dlcURLs.isEmpty ? .tertiary : .primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("Add…") { chooseDLC() }
                if !model.dlcURLs.isEmpty {
                    Button("Clear") { model.dlcURLs.removeAll() }
                }
            }
            selectionList(model.dlcURLs) { index in model.dlcURLs.remove(at: index) }

            row(title: "Switch Cheat") {
                pathLabel(model.cheatFileName ?? "one cheat file for this game — a build anchors it to the build",
                          empty: model.cheatFileName == nil)
                Button("Import…") { importCheats() }
                    .disabled(model.cheatGameName == nil)
                if model.cheatFileName != nil {
                    Button("Clear") { Task { await model.clearCheats() } }
                }
            }

            row(title: "Output folder") {
                HStack(spacing: 6) {
                    pathLabel(model.outDirURL?.path ?? "the game library, or a folder you choose",
                              empty: model.outDirURL == nil)
                    if model.outDirIsAuto {
                        Text(model.libraryURL == nil ? "default" : "library")
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.secondary.opacity(0.12), in: Capsule())
                            .help(model.libraryURL == nil
                                  ? "Defaults to the game's own directory"
                                  : "The game library from Setup: the build is filed into <library>/<Game>/")
                    }
                }
                Button("Change…") { chooseOutDir() }
                if !model.outDirIsAuto {
                    Button("Default") { model.resetOutDir() }
                }
            }
        }
    }

    private func row<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 8) {
            Text(title)
                .frame(width: 96, alignment: .trailing)
                .foregroundStyle(.secondary)
            content()
        }
    }

    private func pathLabel(_ text: String, empty: Bool) -> some View {
        Text(text)
            .font(.callout)
            .lineLimit(1)
            .truncationMode(.middle)
            .foregroundStyle(empty ? .tertiary : .primary)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The update package: one file, exactly like the game row (auto-filled from the folder).
    private var updateRow: some View {
        row(title: "Update") {
            HStack(spacing: 6) {
                pathLabel(model.updateURL?.path ?? "optional — the newest update beside the game is picked",
                          empty: model.updateURL == nil)
                if model.updateURL != nil, model.updateIsAuto {
                    Text("newest")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.secondary.opacity(0.12), in: Capsule())
                        .help("Found beside the game; choose a file to override it")
                }
            }
            Button("Choose…") { chooseUpdate() }
            if model.updateURL != nil {
                Button("Clear") { model.setUpdate(nil) }
            }
        }
    }

    /// The dump folder (optional): picking it auto-selects the package inside.
    private var gameDirRow: some View {
        row(title: "Game dir") {
            pathLabel(model.gameDirURL?.path ?? "optional — the dump folder holding the game",
                      empty: model.gameDirURL == nil)
            Button("Choose…") { chooseGameDir() }
            if model.gameDirURL != nil {
                Button("Clear") { Task { await model.setGameDir(nil) } }
            }
        }
    }

    /// The package file itself: auto-filled from the folder, or picked by hand.
    private var gameFileRow: some View {
        row(title: "Game") {
            HStack(spacing: 6) {
                pathLabel(model.gameURL?.path ?? "a package file (.nsp/.nsz/.xci/.xcz) or a .nup",
                          empty: model.gameURL == nil)
                if model.gameURL != nil, model.gameFileIsAuto {
                    autoBadge
                }
            }
            Button("Choose…") { chooseGameFile() }
            if model.gameURL != nil {
                Button("Clear") { model.setGameFile(nil) }
            }
        }
    }

    private var autoBadge: some View {
        Text("auto")
            .font(.caption2.weight(.medium))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Color.secondary.opacity(0.12), in: Capsule())
            .help("Found in the folder above; pick a file to override it")
    }

    /// The update package, listed the same way as the game package.
    @ViewBuilder
    private var updateList: some View {
        if let update = model.updateURL {
            packageLine(url: update)
        }
    }

    /// The one package this build uses — the auto-picked game. Other packages in
    /// the folder (updates, DLC) are not listed: pick a different file with
    /// "Game → Choose…" if that is what you want to build.
    @ViewBuilder
    private var packageList: some View {
        if let game = model.gameURL {
            packageLine(url: game)
        }
    }

    /// One package as a single list row: name, role, version, size — like Finder's list view.
    private func packageLine(url: URL) -> some View {
        let known = model.packages.first { $0.path == url.path }
        return HStack(spacing: 6) {
            Image(systemName: "largecircle.fill.circle")
                .font(.caption2)
                .foregroundStyle(Color.accentColor)
            Text(url.lastPathComponent)
                .font(.caption.monospaced())
                .lineLimit(1)
                .truncationMode(.middle)
                .help(url.path)
            Text(known?.role ?? "package")
                .font(.caption2)
                .foregroundStyle(.tertiary)
            if let version = known?.version {
                Text(version.hasPrefix("v") ? version : "v\(version)")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 4)
            if let size = known?.size {
                Text(ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file))
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, SelectionList.padding / 2)
        .frame(height: SelectionList.height(for: 1))
        .background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
        .padding(.leading, 104)
    }

    /// The chosen files as a vertical, scrollable list — many DLCs must stay readable.
    @ViewBuilder
    private func selectionList(_ urls: [URL], remove: @escaping (Int) -> Void) -> some View {
        if !urls.isEmpty {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(urls.enumerated()), id: \.offset) { index, url in
                        HStack(spacing: 6) {
                            Image(systemName: "shippingbox")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            Text(url.lastPathComponent)
                                .font(.caption.monospaced())
                                .lineLimit(1)
                                .truncationMode(.middle)
                                .help(url.path)
                            Spacer(minLength: 4)
                            Button {
                                remove(index)
                            } label: {
                                Image(systemName: "minus.circle.fill")
                            }
                            .buttonStyle(.plain)
                            .foregroundStyle(.secondary)
                            .help("Remove")
                        }
                        .frame(height: SelectionList.rowHeight)
                    }
                }
                .padding(.horizontal, 8)
                .padding(.vertical, SelectionList.padding / 2)
            }
            // Auto height: one row per item, at most five visible rows, then it scrolls.
            .frame(height: SelectionList.height(for: urls.count))
            .background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
            .padding(.leading, 104)
        }
    }

    // MARK: Engine

    private var engineSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            row(title: "Engine") {
                Picker("", selection: $model.engineChoice) {
                    Text("upstream (seedds/nxnative)").tag("upstream")
                    Text("fork (bf39l/nxnative)").tag("fork")
                }
                .labelsHidden()
                .frame(width: 230)

                Text("ref")
                    .foregroundStyle(.secondary)
                TextField("main", text: $model.engineRef)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 190)
                Button(model.engineBusy ? "Pinning…" : "Save & Pin") {
                    Task { await model.pinEngine() }
                }
                .disabled(model.engineBusy || model.isRunning)
            }
            if let engine = model.pinned {
                Text("\(engine.source.label) — \(engine.summary)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 104)
            } else if let status = model.engineStatus, status.saved.engineID != nil {
                Text("saved: \(status.saved.engineID ?? "?") / \(status.saved.engineRef ?? "default ref")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 104)
            }
            if let failure = model.firstFailure {
                HStack(spacing: 6) {
                    Text("\(failure.name): \(failure.detail)")
                        .font(.caption)
                        .foregroundStyle(.orange)
                    if !failure.fix.isEmpty {
                        Button("Copy fix") { NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(failure.fix, forType: .string) }
                            .buttonStyle(.link)
                    }
                }
                .padding(.leading, 104)
            }
        }
    }

    // MARK: Actions

    private var actions: some View {
        HStack(spacing: 10) {
            if model.isRunning {
                Button(role: .cancel) { model.cancelBuild() } label: {
                    Label("Cancel", systemImage: "stop.circle")
                }
                ProgressView().controlSize(.small)
                Text(model.stage.isEmpty ? "building…" : model.stage)
                    .font(.callout.weight(.medium))
            } else {
                Button { model.startBuild() } label: {
                    Label("Build macOS app", systemImage: "hammer.fill")
                }
                .keyboardShortcut(.return, modifiers: [])
                .disabled(!model.readyToBuild)
            }

            Spacer()

            switch model.state {
            case .succeeded:
                Label("Done", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                if let app = model.appURL {
                    Button("Reveal in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting([app])
                    }
                    Button("Open") { NSWorkspace.shared.open(app) }
                    Button {
                        Task { await model.cleanBuilds() }
                    } label: {
                        Label("Clean build files", systemImage: "trash")
                    }
                    .help("Delete the extracted files and shader caches — the app keeps working, "
                          + "the next build extracts them again")
                }
            case .failed(let message):
                Label(message, systemImage: "xmark.octagon.fill")
                    .foregroundStyle(.red)
                    .font(.callout)
                    .lineLimit(2)
            case .idle, .running:
                EmptyView()
            }
        }
    }

}

// MARK: - Panels

extension BuildTab {
    /// The dump folder; its game package is picked for you.
    func chooseGameDir() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Use this folder"
        panel.message = "Pick the folder holding the game's .nsp/.nsz/.xci files."
        if panel.runModal() == .OK, let url = panel.url {
            Task { await model.setGameDir(url) }
        }
    }

    /// One package file, or a .nup container, chosen by hand.
    func chooseGameFile() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Use this game"
        panel.message = "Pick the package to build (.nsp/.nsz/.xci/.xcz) or a .nup container."
        if panel.runModal() == .OK, let url = panel.url { model.setGameFile(url) }
    }

    func chooseKeys() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Use these keys"
        panel.message = "Pick prod.keys (one console's key set decrypts that console's games)."
        if panel.runModal() == .OK, let url = panel.url { model.keysURL = url }
    }

    /// Copy one or more cheat files into the selected game's cheats folder.
    private func importCheats() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.message = "Cheat files are named after the game's build ID (16 hex digits)."
        panel.prompt = "Import"
        if panel.runModal() == .OK, let url = panel.url {
            Task {
                await model.importCheats([url])   // one cheat file per game: this replaces the old one
            }
        }
    }

    /// The update package (one file; a folder picks the newest update inside it).
    func chooseUpdate() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.message = "Pick the update package for this game — it is staged with the base game."
        panel.prompt = "Use this update"
        if panel.runModal() == .OK, let url = panel.url { model.setUpdate(url) }
    }

    /// Pick any number of DLC packages, or whole folders of them.
    func chooseDLC() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.message = "Pick DLC packages or a folder of them — every selected DLC is staged with the base game."
        panel.prompt = "Add DLC"
        if panel.runModal() == .OK { model.dlcURLs.append(contentsOf: unique(panel.urls, into: model.dlcURLs)) }
    }

    /// Skip re-adding something the user already picked.
    func unique(_ candidates: [URL], into existing: [URL]) -> [URL] {
        candidates.filter { candidate in
            !existing.contains { $0.standardizedFileURL == candidate.standardizedFileURL }
        }
    }

    /// One folder that collects the .app, the engine's build folder and prod.keys.
    func chooseOutDir() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Use this folder"
        panel.message = "The app, the engine build folder (kept for fast rebuilds) and prod.keys go here."
        if panel.runModal() == .OK, let url = panel.url { model.chooseOutDir(url) }
    }
}


