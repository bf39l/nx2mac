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
            inputs
            engineSection
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
            row(title: "Game") {
                pathLabel(model.gameURL?.path ?? "choose a dump folder, a package file, or a .nup", empty: model.gameURL == nil)
                Button("Choose…") { chooseGame() }
            }
            row(title: "Update") {
                Text(model.updateURLs.isEmpty
                     ? "optional — pick an update .nsp/.nsz from anywhere"
                     : "\(model.updateURLs.count) selected")
                    .font(.callout)
                    .foregroundStyle(model.updateURLs.isEmpty ? .tertiary : .primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("Add…") { chooseUpdate() }
                if !model.updateURLs.isEmpty {
                    Button("Clear") { model.updateURLs.removeAll() }
                }
            }
            selectionList(model.updateURLs) { index in model.updateURLs.remove(at: index) }

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

            row(title: "Output folder") {
                HStack(spacing: 6) {
                    pathLabel(model.outDirURL?.path ?? "choose a game first",
                              empty: model.outDirURL == nil)
                    if model.outDirIsAuto {
                        Text("default")
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.secondary.opacity(0.12), in: Capsule())
                            .help("Defaults to the game's own directory")
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
    /// One picker for either shape of input: a dump folder, a single package
    /// file (.nsp/.nsz/.xci/.xcz — staged for the engine) or a .nup container.
    func chooseGame() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        // No content-type filter: there is no system type for .nsp/.nup, and a
        // folder has to stay selectable. The CLI validates whatever we hand it.
        panel.prompt = "Use this game"
        panel.message = "Pick the dump folder holding the game's .nsp/.nsz/.xci files, "
            + "a single package file, or a .nup container."
        if panel.runModal() == .OK, let url = panel.url { model.setGame(url) }
    }

    func chooseKeys() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Use these keys"
        panel.message = "Pick prod.keys (one console's key set decrypts that console's games)."
        if panel.runModal() == .OK, let url = panel.url { model.keysURL = url }
    }

    /// Pick one or more update packages (multi-select: a folder may hold several).
    func chooseUpdate() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.message = "Pick the update package(s) for this game — they are staged with the base game."
        panel.prompt = "Add update"
        if panel.runModal() == .OK { model.updateURLs.append(contentsOf: unique(panel.urls, into: model.updateURLs)) }
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
