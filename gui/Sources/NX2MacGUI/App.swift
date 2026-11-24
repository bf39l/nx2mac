import AppKit
import SwiftUI

@main
struct NX2MacGUIApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = BuildModel()

    var body: some Scene {
        Window("nx2mac", id: "main") {
            ContentView()
                .environmentObject(model)
        }
        .commands {
            CommandGroup(replacing: .newItem) {}  // no "New Window" for a single-window tool
            CommandGroup(after: .appInfo) {
                Button("Re-check prerequisites") {
                    Task { await model.refresh() }
                }
                .keyboardShortcut("r", modifiers: .command)
            }
        }

        Settings {
            SettingsView().environmentObject(model)
        }
    }
}

/// A tool, not a document app: quit when the window closes, and come to the front.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}

/// Minimal settings: where the `nx2mac` CLI lives (auto-detected by default).
struct SettingsView: View {
    @EnvironmentObject private var model: BuildModel
    @AppStorage("cliPath") private var cliPath: String = ""

    var body: some View {
        Form {
            Section("Command line tool") {
                TextField("nx2mac path", text: $cliPath, prompt: Text("bundled with the app"))
                    .onSubmit { model.refreshCLI(); Task { await model.refresh() } }
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(model.cli?.display ?? "not found")
                            .font(.caption.monospaced())
                            .foregroundStyle(model.cli == nil ? .red : .secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        if let cli = model.cli {
                            Text(cli.origin).font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                    Spacer()
                    Button("Re-detect") {
                        cliPath = ""
                        model.refreshCLI()
                        Task { await model.refresh() }
                    }
                }
            }
            Section("Versions") {
                LabeledContent("nx2mac app", value: appVersion)
                LabeledContent("nx2mac CLI", value: model.cliVersion ?? "unknown")
                if let cliVersion = model.cliVersion, cliVersion != appVersion {
                    Text("The app and the CLI disagree; reinstall the CLI or rebuild the app.")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
            Section {
                Text("The app carries its own CLI (Contents/Resources/cli) and only runs it "
                     + "(`nx2mac doctor|engine|build --json`). Only set a path above to test a different "
                     + "build, e.g. a development checkout.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(18)
        .frame(width: 480)
    }

    /// The bundle's version, e.g. "0.0.1".
    private var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"
    }
}
