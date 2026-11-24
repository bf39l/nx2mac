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
                TextField("nx2mac path", text: $cliPath, prompt: Text("auto-detect"))
                    .onSubmit { model.refreshCLI(); Task { await model.refresh() } }
                HStack {
                    Text(model.cli?.display ?? "not found")
                        .font(.caption.monospaced())
                        .foregroundStyle(model.cli == nil ? .red : .secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
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
                Text("The GUI only runs the CLI (`nx2mac doctor|engine|build --json`) and renders its output. "
                     + "Install it with `uv tool install nx2mac`, or point this at a development checkout's "
                     + "`.venv/bin/python`.")
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
