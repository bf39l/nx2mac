// Generates the GUI's app icon (squircle + gradient + gamepad glyph) as an .icns.
//
//   swift gui/tools/make_icon.swift gui/Resources/AppIcon.icns
//
// Re-run it after changing the design; the result is committed so building the
// app never depends on this script.
import AppKit
import Foundation

/// Rounded-square body on Apple's 1024 grid (824x824 centred), Big Sur radius.
private func squircle(in rect: NSRect) -> NSBezierPath {
    let radius = rect.width * 0.2237
    return NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
}

/// Draw the icon at one pixel size.
private func render(size: Int) -> Data? {
    guard let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: size,
        pixelsHigh: size,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else { return nil }

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

    let scale = CGFloat(size) / 1024.0
    let inset = 100 * scale
    let body = NSRect(
        x: inset, y: inset,
        width: CGFloat(size) - 2 * inset,
        height: CGFloat(size) - 2 * inset
    )

    // Indigo -> teal gradient, the two colours of the CLI's rich output.
    let path = squircle(in: body)
    let gradient = NSGradient(colors: [
        NSColor(srgbRed: 0.35, green: 0.35, blue: 0.93, alpha: 1),
        NSColor(srgbRed: 0.18, green: 0.82, blue: 0.76, alpha: 1),
    ])!
    gradient.draw(in: path, angle: -60)

    // A hairline rim keeps the body readable on light and dark desktops.
    NSColor.white.withAlphaComponent(0.18).setStroke()
    path.lineWidth = max(1, 2 * scale)
    path.stroke()

    // Gamepad glyph (SF Symbols), white, centred.
    if let symbol = NSImage(systemSymbolName: "gamecontroller.fill", accessibilityDescription: "game controller") {
        let configuration = NSImage.SymbolConfiguration(pointSize: body.height, weight: .semibold)
            .applying(NSImage.SymbolConfiguration(paletteColors: [.white]))
        if let image = symbol.withSymbolConfiguration(configuration) {
            let target = body.height * 0.46
            let aspect = image.size.height > 0 ? image.size.width / image.size.height : 1.6
            let rect = NSRect(
                x: body.midX - target * aspect / 2,
                y: body.midY - target / 2,
                width: target * aspect,
                height: target
            )
            image.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1.0)
        }
    }

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])
}

/// The file names `iconutil` expects inside an .iconset folder.
private let variants: [(points: Int, scale: Int)] = [
    (16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2),
]

let arguments = CommandLine.arguments
guard arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: make_icon.swift <out.icns>\n".utf8))
    exit(2)
}

let destination = URL(fileURLWithPath: arguments[1])
let iconset = FileManager.default.temporaryDirectory
    .appendingPathComponent("nx2mac-\(UUID().uuidString).iconset")
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)

for variant in variants {
    let pixels = variant.points * variant.scale
    guard let data = render(size: pixels) else {
        FileHandle.standardError.write(Data("failed to render \(pixels)px\n".utf8))
        exit(1)
    }
    let suffix = variant.scale == 1 ? "" : "@2x"
    let name = "icon_\(variant.points)x\(variant.points)\(suffix).png"
    try data.write(to: iconset.appendingPathComponent(name))
}

// Pack the iconset into an .icns next to the requested destination.
let output = destination.deletingLastPathComponent()
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
let iconutil = Process()
iconutil.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
iconutil.arguments = ["-c", "icns", iconset.path, "-o", destination.path]
try iconutil.run()
iconutil.waitUntilExit()
try? FileManager.default.removeItem(at: iconset)

guard iconutil.terminationStatus == 0 else {
    FileHandle.standardError.write(Data("iconutil failed\n".utf8))
    exit(1)
}
print("wrote \(destination.path)")
