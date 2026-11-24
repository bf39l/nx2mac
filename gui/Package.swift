// swift-tools-version: 6.0
import PackageDescription

// The GUI is deliberately a plain SwiftPM executable: no Xcode project to
// maintain, buildable from the terminal (`swift build`) and assembled into a
// real .app bundle by `build.sh`. It talks to the Python CLI as a subprocess
// and never imports its code — see AGENT.md ("Machine interface").
let package = Package(
    name: "NX2MacGUI",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "NX2MacGUI",
            path: "Sources/NX2MacGUI",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "NX2MacGUITests",
            dependencies: ["NX2MacGUI"],
            path: "Tests/NX2MacGUITests",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
