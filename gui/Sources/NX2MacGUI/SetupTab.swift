import AppKit
import SwiftUI

/// The Setup tab: every prerequisite, its state, and a button to install each
/// missing piece (`nx2mac setup --install <check> --json`), or a manual step we
/// must not automate (installing Xcode, updating macOS, Homebrew itself).
struct SetupTab: View {
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Setup").font(.title2.weight(.semibold))
                    Text("What this Mac needs to build a macOS app, and the fixes nx2mac can run for you.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                DoctorBadge()
            }

            Divider()

            HStack(spacing: 10) {
                Button {
                    Task { await model.refresh() }
                } label: {
                    Label("Re-check", systemImage: "arrow.clockwise")
                }
                .disabled(model.doctorBusy || model.installing != nil)

                Button {
                    Task { await model.installAllMissing() }
                } label: {
                    Label("Install all missing", systemImage: "arrow.down.circle")
                }
                .disabled(model.installing != nil || !hasInstallable)

                if let installing = model.installing {
                    ProgressView().controlSize(.small)
                    Text("installing \(installing)…").font(.callout.weight(.medium))
                }
                Spacer()
                if let doctor = model.doctor {
                    Text("\(doctor.checks.count - doctor.failedChecks.count)/\(doctor.checks.count) ready")
                        .font(.callout.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }

            ScrollView {
                LazyVStack(spacing: 6) {
                    ForEach(model.doctor?.checks ?? []) { check in
                        CheckRow(check: check)
                    }
                    if model.doctor == nil && !model.doctorBusy {
                        Text(model.cliError ?? "No results yet — press Re-check.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }

            if !model.setupLog.isEmpty {
                Text("Install output").font(.callout.weight(.medium))
                LogView(lines: model.setupLog).frame(height: 180)
            }
        }
        .task {
            if model.doctor == nil { await model.refresh() }
        }
    }

    private var hasInstallable: Bool {
        model.doctor?.failedChecks.contains(where: \.canInstall) ?? false
    }
}

/// One prerequisite row with its state and the action we can offer.
private struct CheckRow: View {
    let check: DoctorCheck
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: check.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(check.ok ? Color.green : Color.orange)
                .imageScale(.large)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(check.name)
                        .font(.callout.monospaced().weight(.medium))
                    if let result = model.installResults[check.name] {
                        Text(result)
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(result == "installed" ? Color.green : Color.red)
                    }
                }
                Text(check.detail.isEmpty ? "-" : check.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                if !check.ok, !check.note.isEmpty {
                    Text(check.note)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .lineLimit(2)
                }
            }

            Spacer(minLength: 8)

            if !check.ok {
                if check.canInstall, let install = check.install {
                    Text(install.joined(separator: " "))
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .frame(maxWidth: 260, alignment: .trailing)
                    Button(model.installing == check.name ? "Installing…" : "Install") {
                        Task { await model.install(check) }
                    }
                    .disabled(model.installing != nil)
                } else {
                    if !check.fix.isEmpty {
                        Text(check.fix)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .frame(maxWidth: 260, alignment: .trailing)
                            .help(check.fix)
                        Button("Copy") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(check.fix, forType: .string)
                        }
                    }
                    if let url = URL(string: check.link), !check.link.isEmpty {
                        Button("Open page") { NSWorkspace.shared.open(url) }
                    }
                }
            }
        }
        .padding(9)
        .background(check.ok ? Color.green.opacity(0.06) : Color.orange.opacity(0.09),
                    in: RoundedRectangle(cornerRadius: 8))
    }
}

/// "Ready to build" / "3 missing prerequisite(s)" badge, shared by both tabs.
struct DoctorBadge: View {
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        Group {
            if let error = model.cliError {
                badge(error, systemImage: "exclamationmark.triangle.fill", tint: .red)
            } else if model.doctorBusy {
                badge("Checking prerequisites…", systemImage: "hourglass", tint: .secondary)
            } else if let doctor = model.doctor {
                if doctor.ok {
                    badge("Ready to build", systemImage: "checkmark.seal.fill", tint: .green)
                } else {
                    badge("\(doctor.failedChecks.count) missing prerequisite(s)",
                          systemImage: "exclamationmark.triangle.fill", tint: .orange)
                }
            } else {
                badge("Unknown", systemImage: "questionmark.circle", tint: .secondary)
            }
        }
    }

    private func badge(_ text: String, systemImage: String, tint: Color) -> some View {
        Label(text, systemImage: systemImage)
            .font(.callout.weight(.medium))
            .foregroundStyle(tint)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
            .lineLimit(1)
    }
}

/// Monospaced, auto-scrolling log pane used by both tabs.
struct LogView: View {
    let lines: [String]

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 1) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                        Text(line)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .id(index)
                    }
                }
                .padding(8)
            }
            .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary))
            .onChange(of: lines.count) { _, count in
                guard count > 0 else { return }
                withAnimation(.linear(duration: 0.1)) { proxy.scrollTo(count - 1, anchor: .bottom) }
            }
        }
    }
}
