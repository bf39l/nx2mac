import AppKit
import SwiftUI

/// The window: Setup (what the machine is missing, and where the game library
/// lives), Games (everything built into that library) and Build (pick inputs and go).
struct ContentView: View {
    @EnvironmentObject private var model: BuildModel

    /// Which tab is showing, as an index so it survives a relaunch: a build run
    /// on the Build tab puts you back there next time, and Setup is the first
    /// launch's tab (prerequisites before the first build).
    @AppStorage("tab") private var tab = 0

    var body: some View {
        TabView(selection: $tab) {
            SetupTab()
                .tabItem { Label("Setup", systemImage: "wrench.and.screwdriver") }
                .tag(0)
            GamesTab()
                .tabItem { Label("Games", systemImage: "square.stack.3d.up") }
                .tag(1)
            BuildTab()
                .tabItem { Label("Build", systemImage: "hammer") }
                .tag(2)
        }
        .padding(14)
        .frame(minWidth: 780, minHeight: 660)
        .task { await model.refresh() }
    }
}

/// The Build tab: one engine choice, then a queue of expandable build setups run in order.
struct BuildTab: View {
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            Divider()
            engineSection
            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(Array(model.setups.enumerated()), id: \.element.id) { index, setup in
                        BuildSetupSection(setup: setup, number: index + 1)
                    }
                }
                .padding(.vertical, 2)
            }

            HStack(spacing: 10) {
                Button {
                    model.addSetup()
                } label: {
                    Label("Add build", systemImage: "plus")
                }

                Spacer()

                if model.isRunning {
                    Button(role: .cancel) { model.cancelQueue() } label: {
                        Label("Cancel", systemImage: "stop.circle")
                    }
                    ProgressView().controlSize(.small)
                } else {
                    Button { model.startQueue() } label: {
                        Label("Build queue", systemImage: "play.fill")
                    }
                    .keyboardShortcut(.return, modifiers: [])
                }
            }
        }
    }

    // MARK: Header / prerequisites

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("nx2mac").font(.title2.weight(.semibold))
                Text("Build Nintendo Switch games into native macOS apps")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            DoctorBadge()
        }
    }

    // MARK: Engine

    private var engineSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            buildRow(title: "Engine") {
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
}

/// One expandable build setup: its inputs, its build buttons and its own live log.
private struct BuildSetupSection: View {
    @ObservedObject var setup: BuildSetup
    let number: Int
    @EnvironmentObject private var model: BuildModel
    @State private var expanded = true

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 10) {
                inputs
                actions
                LogView(lines: setup.log)
                    .frame(maxHeight: 240)   // bound the log so it scrolls inside the setup instead of growing the window
            }
            .padding(.top, 8)
        } label: {
            HStack(spacing: 8) {
                Text("Build \(number)")
                    .font(.headline)
                if setup.gameURL != nil || setup.gameDirURL != nil {
                    Text(setup.title)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                statusBadge
                if model.setups.count > 1 {
                    Button { model.removeSetup(setup) } label: {
                        Image(systemName: "minus.circle.fill")
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .help("Remove this build")
                    .disabled(model.isRunning)
                }
            }
        }
        .padding(10)
        .background(Color.secondary.opacity(0.05), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary))
    }

    @ViewBuilder
    private var statusBadge: some View {
        switch setup.state {
        case .idle:
            EmptyView()
        case .running:
            ProgressView().controlSize(.small)
        case .succeeded:
            Label("Done", systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.caption)
        case .failed(let message):
            Label(message, systemImage: "xmark.octagon.fill").foregroundStyle(.red).font(.caption).lineLimit(1)
        }
    }

    // MARK: Inputs

    private var inputs: some View {
        VStack(alignment: .leading, spacing: 10) {
            buildRow(title: "prod.keys") {
                buildPathLabel(setup.keysURL?.path ?? "optional — auto-detected in/next to the game",
                               empty: setup.keysURL == nil)
                Button("Choose…") { chooseKeys() }
                if setup.keysURL != nil {
                    Button("Clear") { setup.keysURL = nil }
                }
            }
            gameDirRow
            gameFileRow
            packageList

            updateRow
            updateList
            buildRow(title: "DLC") {
                Text(setup.dlcURLs.isEmpty
                     ? "optional — pick one or more DLC .nsp/.nsz (or a folder)"
                     : SelectionList.scrolls(at: setup.dlcURLs.count)
                        ? "\(setup.dlcURLs.count) selected — scroll the list"
                        : "\(setup.dlcURLs.count) selected")
                    .font(.callout)
                    .foregroundStyle(setup.dlcURLs.isEmpty ? .tertiary : .primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("Add…") { chooseDLC() }
                if !setup.dlcURLs.isEmpty {
                    Button("Clear") { setup.dlcURLs.removeAll() }
                }
            }
            buildSelectionList(setup.dlcURLs) { index in setup.dlcURLs.remove(at: index) }

            cheatRow
            cheatLine

            buildRow(title: "Output folder") {
                HStack(spacing: 6) {
                    buildPathLabel(setup.outDirURL?.path ?? "the game library, or a folder you choose",
                                   empty: setup.outDirURL == nil)
                    if setup.outDirIsAuto {
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
                if !setup.outDirIsAuto {
                    Button("Default") { model.resetOutDir(setup) }
                }
            }
        }
    }

    /// The update package: one file, exactly like the game row (auto-filled from the folder).
    private var updateRow: some View {
        buildRow(title: "Update") {
            HStack(spacing: 6) {
                buildPathLabel(setup.updateURL?.path ?? "optional — the newest update beside the game is picked",
                               empty: setup.updateURL == nil)
                if setup.updateURL != nil, setup.updateIsAuto {
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
            if setup.updateURL != nil {
                Button("Clear") { model.setUpdate(setup, nil) }
            }
        }
    }

    /// The dump folder (optional): picking it auto-selects the package inside.
    private var gameDirRow: some View {
        buildRow(title: "Game dir") {
            buildPathLabel(setup.gameDirURL?.path ?? "optional — the dump folder holding the game",
                           empty: setup.gameDirURL == nil)
            Button("Choose…") { chooseGameDir() }
            if setup.gameDirURL != nil {
                Button("Clear") { Task { await model.setGameDir(setup, nil) } }
            }
        }
    }

    /// The package file itself: auto-filled from the folder, or picked by hand.
    private var gameFileRow: some View {
        buildRow(title: "Game") {
            HStack(spacing: 6) {
                buildPathLabel(setup.gameURL?.path ?? "a package file (.nsp/.nsz/.xci/.xcz) or a .nup",
                               empty: setup.gameURL == nil)
                if setup.gameURL != nil, setup.gameFileIsAuto {
                    autoBadge
                }
            }
            Button("Choose…") { chooseGameFile() }
            if setup.gameURL != nil {
                Button("Clear") { model.setGameFile(setup, nil) }
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

    /// The game's cheat file: one file, like the update, re-anchored by every build.
    private var cheatRow: some View {
        buildRow(title: "Switch Cheat") {
            HStack(spacing: 6) {
                buildPathLabel(setup.cheatFileURL?.path ?? "optional — one cheat file (.txt) for this game",
                               empty: setup.cheatFileURL == nil)
                if setup.cheatFileURL != nil {
                    Text("anchored on build")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.secondary.opacity(0.12), in: Capsule())
                        .help("Every build re-anchors this file to the build's own code")
                }
            }
            Button("Choose…") { importCheats() }
                .disabled(model.cheatGameName(for: setup) == nil)
            if setup.cheatFileURL != nil {
                Button("Clear") { Task { await model.clearCheats(setup) } }
            }
        }
    }

    /// The cheat file as one list line, the same shape as the game and update packages.
    @ViewBuilder
    private var cheatLine: some View {
        if let cheat = setup.cheatFileURL {
            buildPackageLine(url: cheat, packages: setup.packages, role: "cheat")
        }
    }

    /// The update package, listed the same way as the game package.
    @ViewBuilder
    private var updateList: some View {
        if let update = setup.updateURL {
            buildPackageLine(url: update, packages: setup.packages, role: nil)
        }
    }

    /// The one package this build uses — the auto-picked game.
    @ViewBuilder
    private var packageList: some View {
        if let game = setup.gameURL {
            buildPackageLine(url: game, packages: setup.packages, role: nil)
        }
    }

    // MARK: Actions

    private var actions: some View {
        HStack(spacing: 10) {
            if setup.isRunning {
                Button(role: .cancel) { model.cancelBuild(setup) } label: {
                    Label("Cancel", systemImage: "stop.circle")
                }
                ProgressView().controlSize(.small)
                Text(setup.stage.isEmpty ? "building…" : "Step \(setup.stage)")
                    .font(.callout.weight(.medium))
            } else {
                Button { model.startBuild(setup) } label: {
                    Label("Build", systemImage: "hammer.fill")
                }
                .disabled(!model.readyToBuild(for: setup))

                Button { model.startBuild(setup, cheatsOnly: true) } label: {
                    Label("Build cheats only", systemImage: "chevron.left.forwardslash.chevron.right")
                }
                .disabled(!model.readyToBuild(for: setup) || setup.cheatFileURL == nil)
                .help("Extract ExeFS/main and re-anchor the cheat file, skipping the shaders, the Mac host and the app")
            }

            Spacer()

            if case .succeeded = setup.state, let app = setup.appURL {
                Button("Reveal in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([app])
                }
                Button("Open") { NSWorkspace.shared.open(app) }
                Button {
                    Task { await model.cleanBuilds(setup) }
                } label: {
                    Label("Clean build files", systemImage: "trash")
                }
                .help("Delete what the build no longer needs: extracted files, shader caches, temp, "
                      + "the .app.key and the .nx2mac.json — the app and its saves keep working")
            } else if case .failed = setup.state {
                if let anyway = setup.buildAnyway, let flag = anyway.override {
                    Button("Build anyway") {
                        Task { await model.retryBuildAnyway(setup, flag) }
                    }
                    .help(anyway.note)
                }
            }
        }
    }

    // MARK: Panels

    func chooseGameDir() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Use this folder"
        panel.message = "Pick the folder holding the game's .nsp/.nsz/.xci files."
        if panel.runModal() == .OK, let url = panel.url {
            Task { await model.setGameDir(setup, url) }
        }
    }

    func chooseGameFile() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Use this game"
        panel.message = "Pick the package to build (.nsp/.nsz/.xci/.xcz) or a .nup container."
        if panel.runModal() == .OK, let url = panel.url { model.setGameFile(setup, url) }
    }

    func chooseKeys() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Use these keys"
        panel.message = "Pick prod.keys (one console's key set decrypts that console's games)."
        if panel.runModal() == .OK, let url = panel.url { setup.keysURL = url }
    }

    private func importCheats() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.message = "Cheat files are named after the game's build ID (16 hex digits)."
        panel.prompt = "Import"
        if panel.runModal() == .OK, let url = panel.url {
            Task { await model.importCheats(setup, [url]) }   // one cheat file per game
        }
    }

    func chooseUpdate() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.message = "Pick the update package for this game — it is staged with the base game."
        panel.prompt = "Use this update"
        if panel.runModal() == .OK, let url = panel.url { model.setUpdate(setup, url) }
    }

    func chooseDLC() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.message = "Pick DLC packages or a folder of them — every selected DLC is staged with the base game."
        panel.prompt = "Add DLC"
        if panel.runModal() == .OK {
            setup.dlcURLs.append(contentsOf: unique(panel.urls, into: setup.dlcURLs))
        }
    }

    func unique(_ candidates: [URL], into existing: [URL]) -> [URL] {
        candidates.filter { candidate in
            !existing.contains { $0.standardizedFileURL == candidate.standardizedFileURL }
        }
    }

    func chooseOutDir() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Use this folder"
        panel.message = "The app, the engine build folder (kept for fast rebuilds) and prod.keys go here."
        if panel.runModal() == .OK, let url = panel.url { model.chooseOutDir(setup, url) }
    }
}

// MARK: - Shared row helpers (Build tab)

private func buildRow<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
    HStack(spacing: 8) {
        Text(title)
            .frame(width: 96, alignment: .trailing)
            .foregroundStyle(.secondary)
        content()
    }
}

private func buildPathLabel(_ text: String, empty: Bool) -> some View {
    Text(text)
        .font(.callout)
        .lineLimit(1)
        .truncationMode(.middle)
        .foregroundStyle(empty ? .tertiary : .primary)
        .frame(maxWidth: .infinity, alignment: .leading)
}

/// One package as a single list row: name, role, version, size — like Finder's list view.
private func buildPackageLine(url: URL, packages: [GamePackage], role forcedRole: String?) -> some View {
    let known = packages.first { $0.path == url.path }
    return HStack(spacing: 6) {
        Image(systemName: "largecircle.fill.circle")
            .font(.caption2)
            .foregroundStyle(Color.accentColor)
        Text(url.lastPathComponent)
            .font(.caption.monospaced())
            .lineLimit(1)
            .truncationMode(.middle)
            .help(url.path)
        Text(forcedRole ?? known?.role ?? "package")
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
private func buildSelectionList(_ urls: [URL], remove: @escaping (Int) -> Void) -> some View {
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
        .frame(height: SelectionList.height(for: urls.count))
        .background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
        .padding(.leading, 104)
    }
}
