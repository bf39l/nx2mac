import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// The window: a Build tab (pick inputs and go) and a Setup tab (see what the
/// machine is missing and install it one piece at a time).
struct ContentView: View {
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        TabView {
            BuildTab()
                .tabItem { Label("Build", systemImage: "hammer") }
            SetupTab()
                .tabItem { Label("Setup", systemImage: "wrench.and.screwdriver") }
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
            row(title: "Game") {
                pathLabel(model.gameURL?.path ?? "choose a dump folder or a .nup file", empty: model.gameURL == nil)
                Button("Folder…") { chooseGameFolder() }
                Button("NSO (.nup)…") { chooseNUFile() }
            }
            row(title: "prod.keys") {
                pathLabel(model.keysURL?.path ?? "optional — auto-detected in/next to the game",
                          empty: model.keysURL == nil)
                Button("Choose…") { chooseKeys() }
                if model.keysURL != nil {
                    Button("Clear") { model.keysURL = nil }
                }
            }
            row(title: "Update") {
                chips(model.updateURLs, empty: "optional — pick an update .nsp/.nsz from anywhere") { index in
                    model.updateURLs.remove(at: index)
                }
                Button("Add…") { chooseUpdate() }
                if !model.updateURLs.isEmpty {
                    Button("Clear") { model.updateURLs.removeAll() }
                }
            }
            row(title: "DLC") {
                chips(model.dlcURLs, empty: "optional — pick one or more DLC .nsp/.nsz (or a folder)") { index in
                    model.dlcURLs.remove(at: index)
                }
                Button("Add…") { chooseDLC() }
                if !model.dlcURLs.isEmpty {
                    Button("Clear") { model.dlcURLs.removeAll() }
                }
            }
            row(title: "Save app to") {
                pathLabel(model.outputURL?.path ?? "default: next to the game", empty: model.outputURL == nil)
                Button("Choose…") { chooseOutput() }
                if model.outputURL != nil {
                    Button("Clear") { model.outputURL = nil }
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

    /// A compact list of chosen files: names with a remove button each.
    private func chips(_ urls: [URL], empty: String, remove: @escaping (Int) -> Void) -> some View {
        Group {
            if urls.isEmpty {
                Text(empty)
                    .font(.callout)
                    .foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(Array(urls.enumerated()), id: \.offset) { index, url in
                            HStack(spacing: 4) {
                                Text(url.lastPathComponent)
                                    .font(.caption)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                Button {
                                    remove(index)
                                } label: {
                                    Image(systemName: "xmark.circle.fill")
                                }
                                .buttonStyle(.plain)
                                .foregroundStyle(.secondary)
                            }
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3)
                            .background(Color.secondary.opacity(0.12), in: Capsule())
                            .help(url.path)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
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
    func chooseGameFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Use this dump"
        panel.message = "Pick the game's dump folder (the one holding .nsp/.nsz/.xci files)."
        if panel.runModal() == .OK, let url = panel.url { model.gameURL = url }
    }

    func chooseNUFile() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [UTType(filenameExtension: "nup")].compactMap { $0 }
        panel.prompt = "Use this .nup"
        if panel.runModal() == .OK, let url = panel.url { model.gameURL = url }
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

    func chooseOutput() {
        let panel = NSSavePanel()
        panel.canCreateDirectories = true
        panel.prompt = "Save app here"
        panel.nameFieldStringValue = "\(suggestedAppName).app"
        panel.message = "Where should the .app be written?"
        if panel.runModal() == .OK, let url = panel.url {
            model.outputURL = url.pathExtension == "app" ? url : url.appendingPathExtension("app")
        }
    }

    /// Best-effort app name from the game folder (engine uses the game's title).
    var suggestedAppName: String {
        guard let game = model.gameURL else { return "Game" }
        if game.pathExtension.lowercased() == "nup" {
            return game.deletingPathExtension().lastPathComponent
        }
        return game.lastPathComponent
    }
}
