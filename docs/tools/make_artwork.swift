// Draws the artwork the README uses: the header banner, the pipeline diagram and a
// plain PNG of the app icon.
//
//   swift docs/tools/make_artwork.swift docs/images
//
// Same palette as the app icon (indigo -> teal, see gui/tools/make_icon.swift), so the
// banner, the icon and the diagrams look like one family. The PNGs are committed, so
// building the app never needs this script.
import AppKit
import Foundation

private let indigo = NSColor(srgbRed: 0.35, green: 0.35, blue: 0.93, alpha: 1)
private let teal = NSColor(srgbRed: 0.18, green: 0.82, blue: 0.76, alpha: 1)
private let backdrop = NSColor(srgbRed: 0.055, green: 0.067, blue: 0.086, alpha: 1)  // #0E1116

private func bitmap(_ width: Int, _ height: Int) -> NSBitmapImageRep {
    guard let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: width,
        pixelsHigh: height,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else {
        fatalError("cannot allocate a \(width)x\(height) bitmap")
    }
    rep.size = NSSize(width: width, height: height)
    return rep
}

private func drawing(in rep: NSBitmapImageRep, _ body: () -> Void) {
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    NSGraphicsContext.current?.imageInterpolation = .high
    body()
    NSGraphicsContext.restoreGraphicsState()
}

private func write(_ rep: NSBitmapImageRep, to url: URL) {
    guard let data = rep.representation(using: .png, properties: [:]) else { fatalError("png encode failed") }
    try! data.write(to: url)
    print("wrote \(url.lastPathComponent) (\(rep.pixelsWide)x\(rep.pixelsHigh), \(data.count / 1024) KB)")
}

private func attributes(_ size: CGFloat, weight: NSFont.Weight, color: NSColor, tracking: CGFloat = 0) -> [NSAttributedString.Key: Any] {
    let font = NSFont.systemFont(ofSize: size, weight: weight)
    return [
        .font: font,
        .foregroundColor: color,
        .kern: tracking,
    ]
}

/// One soft light behind the artwork, in the icon's indigo.
private func glow(center: CGPoint, radius: CGFloat, color: NSColor, alpha: CGFloat) {
    guard let ctx = NSGraphicsContext.current?.cgContext else { return }
    let colors = [color.withAlphaComponent(alpha).cgColor, color.withAlphaComponent(0).cgColor] as CFArray
    guard let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: [0, 1]) else { return }
    ctx.drawRadialGradient(
        gradient,
        startCenter: center, startRadius: 0,
        endCenter: center, endRadius: radius,
        options: [.drawsAfterEndLocation]
    )
}

private func appIcon(size: CGFloat) -> NSImage? {
    let path = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()  // docs/tools
        .deletingLastPathComponent()  // docs
        .deletingLastPathComponent()  // repo root
        .appendingPathComponent("gui/Resources/AppIcon.icns")
    guard let image = NSImage(contentsOf: path) else { return nil }
    image.size = NSSize(width: size, height: size)
    return image
}

private func rounded(_ rect: NSRect, radius: CGFloat) -> NSBezierPath {
    NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
}

// MARK: - banner

private func banner() -> NSBitmapImageRep {
    let width = 2400, height = 760
    let rep = bitmap(width, height)
    drawing(in: rep) {
        backdrop.setFill()
        NSRect(x: 0, y: 0, width: width, height: height).fill()

        // Two glows: indigo behind the icon, teal bleeding in from the right.
        glow(center: CGPoint(x: 380, y: 430), radius: 900, color: indigo, alpha: 0.55)
        glow(center: CGPoint(x: 2050, y: 120), radius: 780, color: teal, alpha: 0.28)

        if let icon = appIcon(size: 360) {
            icon.draw(in: NSRect(x: 150, y: 200, width: 360, height: 360))
        }

        let title = NSAttributedString(string: "nx2mac", attributes: attributes(200, weight: .bold, color: .white, tracking: -4))
        let tagline = NSAttributedString(
            string: "Nintendo Switch games, as native macOS apps",
            attributes: attributes(56, weight: .medium, color: NSColor.white.withAlphaComponent(0.90))
        )
        let meta = NSAttributedString(
            string: "Mac-only  ·  arm64  ·  CLI with --json  ·  SwiftUI app  ·  MIT",
            attributes: attributes(36, weight: .regular, color: NSColor.white.withAlphaComponent(0.55))
        )

        // draw(at:) takes the text box's bottom-left corner, so stack downwards from the top.
        let left: CGFloat = 570
        let titleSize = title.size()
        let taglineSize = tagline.size()
        let metaSize = meta.size()
        let gap1: CGFloat = 10, gap2: CGFloat = 30
        let block = titleSize.height + gap1 + taglineSize.height + gap2 + metaSize.height
        let top = (CGFloat(height) + block) / 2

        let titleY = top - titleSize.height
        title.draw(at: NSPoint(x: left, y: titleY))
        let taglineY = titleY - gap1 - taglineSize.height
        tagline.draw(at: NSPoint(x: left, y: taglineY))
        let metaY = taglineY - gap2 - metaSize.height
        meta.draw(at: NSPoint(x: left, y: metaY))

        // A hairline of the brand gradient along the bottom edge.
        let stripe = NSRect(x: 0, y: 0, width: width, height: 6)
        NSGradient(colors: [indigo, teal])!.draw(in: stripe, angle: 0)
    }
    return rep
}

// MARK: - pipeline

private struct Node {
    let title: String
    let detail: String
    let symbol: String
}

private func pipeline() -> NSBitmapImageRep {
    let width = 2400, height = 620
    let nodes = [
        Node(title: "Your game", detail: "dump folder, .nsp, .xci", symbol: "folder.fill"),
        Node(title: "Probe + stage", detail: "base, update, DLC", symbol: "square.stack.3d.up.fill"),
        Node(title: "Engine pin", detail: "nxnative @ commit", symbol: "shippingbox.fill"),
        Node(title: "Build", detail: "Metal host + shaders", symbol: "hammer.fill"),
        Node(title: "Native app", detail: "in your library", symbol: "gamecontroller.fill"),
    ]
    let boxWidth: CGFloat = 400, boxHeight: CGFloat = 240, gap: CGFloat = 60
    let total = CGFloat(nodes.count) * boxWidth + CGFloat(nodes.count - 1) * gap
    let startX = (CGFloat(width) - total) / 2
    let boxY = (CGFloat(height) - boxHeight) / 2 + 30

    let rep = bitmap(width, height)
    drawing(in: rep) {
        backdrop.setFill()
        NSRect(x: 0, y: 0, width: width, height: height).fill()
        glow(center: CGPoint(x: 200, y: 560), radius: 700, color: indigo, alpha: 0.20)
        glow(center: CGPoint(x: 2250, y: 60), radius: 700, color: teal, alpha: 0.16)

        let heading = NSAttributedString(
            string: "one command:  nx2mac build <game> --library <folder>",
            attributes: attributes(40, weight: .semibold, color: NSColor.white.withAlphaComponent(0.92))
        )
        let headingSize = heading.size()
        heading.draw(at: NSPoint(x: (CGFloat(width) - headingSize.width) / 2, y: CGFloat(height) - 110))

        for (index, node) in nodes.enumerated() {
            let x = startX + CGFloat(index) * (boxWidth + gap)
            let rect = NSRect(x: x, y: boxY, width: boxWidth, height: boxHeight)

            let body = rounded(rect, radius: 30)
            NSColor.white.withAlphaComponent(0.055).setFill()
            body.fill()
            NSColor.white.withAlphaComponent(0.16).setStroke()
            body.lineWidth = 2
            body.stroke()

            // number badge
            let badge = NSRect(x: x + 26, y: boxY + boxHeight - 74, width: 48, height: 48)
            NSGradient(colors: [indigo, teal])!.draw(in: rounded(badge, radius: 14), angle: -60)
            let number = NSAttributedString(
                string: "\(index + 1)",
                attributes: attributes(26, weight: .bold, color: .white)
            )
            let numberSize = number.size()
            number.draw(at: NSPoint(
                x: badge.midX - numberSize.width / 2,
                y: badge.midY - numberSize.height / 2
            ))

            if let symbol = NSImage(systemSymbolName: node.symbol, accessibilityDescription: nil) {
                let configuration = NSImage.SymbolConfiguration(pointSize: 74, weight: .regular)
                    .applying(NSImage.SymbolConfiguration(paletteColors: [NSColor.white.withAlphaComponent(0.22)]))
                if let glyph = symbol.withSymbolConfiguration(configuration) {
                    let glyphSize = glyph.size
                    glyph.draw(in: NSRect(
                        x: x + boxWidth - glyphSize.width - 34,
                        y: boxY + boxHeight - glyphSize.height - 30,
                        width: glyphSize.width,
                        height: glyphSize.height
                    ))
                }
            }

            let title = NSAttributedString(string: node.title, attributes: attributes(44, weight: .semibold, color: .white))
            title.draw(at: NSPoint(x: x + 28, y: boxY + 116))
            let detail = NSAttributedString(
                string: node.detail,
                attributes: attributes(28, weight: .regular, color: NSColor.white.withAlphaComponent(0.60))
            )
            detail.draw(at: NSPoint(x: x + 28, y: boxY + 66))

            if index < nodes.count - 1 {
                let arrowY = boxY + boxHeight / 2
                let from = NSPoint(x: x + boxWidth + 10, y: arrowY)
                let to = NSPoint(x: x + boxWidth + gap - 12, y: arrowY)
                let shaft = NSBezierPath()
                shaft.move(to: from)
                shaft.line(to: NSPoint(x: to.x - 14, y: to.y))
                shaft.lineWidth = 5
                shaft.lineCapStyle = .round
                (index.isMultiple(of: 2) ? indigo : teal).withAlphaComponent(0.85).setStroke()
                shaft.stroke()

                let head = NSBezierPath()
                head.move(to: NSPoint(x: to.x, y: to.y))
                head.line(to: NSPoint(x: to.x - 20, y: to.y + 12))
                head.line(to: NSPoint(x: to.x - 20, y: to.y - 12))
                head.close()
                (index.isMultiple(of: 2) ? indigo : teal).setFill()
                head.fill()
            }
        }
    }
    return rep
}

// MARK: - icon

private func icon(size: Int) -> NSBitmapImageRep {
    let rep = bitmap(size, size)
    drawing(in: rep) {
        NSColor.clear.setFill()
        NSRect(x: 0, y: 0, width: size, height: size).fill()
        appIcon(size: CGFloat(size))?.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
    }
    return rep
}

// MARK: - main

let outDir = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "docs/images")
try! FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
write(banner(), to: outDir.appendingPathComponent("banner.png"))
write(pipeline(), to: outDir.appendingPathComponent("pipeline.png"))
write(icon(size: 512), to: outDir.appendingPathComponent("icon.png"))
