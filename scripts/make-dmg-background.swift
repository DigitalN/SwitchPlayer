#!/usr/bin/env swift
// Draws the installer's arrow and first-launch instructions, adapted from Mac Engine Room.
// Icon centers used by package.sh: (170, 205) and (490, 205), in a 660 × 420 pt window.

import AppKit

let size = NSSize(width: 660, height: 420)
let appCenter = NSPoint(x: 170, y: 205)
let applicationsCenter = NSPoint(x: 490, y: 205)

func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255,
            green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: alpha)
}

func flipped(_ point: NSPoint) -> NSPoint {
    NSPoint(x: point.x, y: size.height - point.y)
}

func drawText(_ text: String, font: NSFont, color textColor: NSColor, centeredAt top: CGFloat) {
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = .center
    let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: textColor, .paragraphStyle: paragraph]
    let height = (text as NSString).size(withAttributes: attributes).height
    (text as NSString).draw(in: NSRect(x: 0, y: size.height - top - height, width: size.width, height: height),
                            withAttributes: attributes)
}

func render(scale: CGFloat) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width * scale), pixelsHigh: Int(size.height * scale),
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = size
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    NSGraphicsContext.current?.imageInterpolation = .high
    let bounds = NSRect(origin: .zero, size: size)
    NSGradient(colors: [color(0xFBFBFE), color(0xEEF0FB)])!.draw(in: bounds, angle: -90)
    for (center, radius, tint) in [
        (NSPoint(x: 120, y: 330), 260.0, color(0x7AA2FF, 0.16)),
        (NSPoint(x: 560, y: 80), 280.0, color(0x28A745, 0.10)),
    ] {
        NSGradient(colors: [tint, tint.withAlphaComponent(0)])!.draw(
            fromCenter: center, radius: 0, toCenter: center, radius: radius, options: [])
    }
    for center in [appCenter, applicationsCenter] {
        let point = flipped(center)
        color(0x0062CC, 0.08).setFill()
        NSBezierPath(ovalIn: NSRect(x: point.x - 58, y: point.y - 64, width: 116, height: 16)).fill()
    }
    drawText("Drag Switch Player into Applications", font: .systemFont(ofSize: 21, weight: .semibold),
             color: color(0x1D1D1F), centeredAt: 38)
    drawText("Then open it from your Applications folder.", font: .systemFont(ofSize: 14),
             color: color(0x48484A), centeredAt: 70)

    let start = flipped(NSPoint(x: appCenter.x + 82, y: appCenter.y - 6))
    let end = flipped(NSPoint(x: applicationsCenter.x - 84, y: applicationsCenter.y - 6))
    let control = NSPoint(x: (start.x + end.x) / 2, y: start.y + 34)
    let arrow = NSBezierPath()
    arrow.move(to: start)
    arrow.curve(to: end, controlPoint1: control, controlPoint2: control)
    arrow.lineWidth = 6
    arrow.lineCapStyle = .round
    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = color(0x0062CC, 0.25)
    shadow.shadowBlurRadius = 8
    shadow.shadowOffset = NSSize(width: 0, height: -2)
    shadow.set()
    color(0x007AFF).setStroke()
    arrow.stroke()
    let angle = atan2(end.y - control.y, end.x - control.x)
    let headLength: CGFloat = 22
    let spread: CGFloat = .pi / 5.5
    let head = NSBezierPath()
    head.move(to: NSPoint(x: end.x - headLength * cos(angle - spread), y: end.y - headLength * sin(angle - spread)))
    head.line(to: end)
    head.line(to: NSPoint(x: end.x - headLength * cos(angle + spread), y: end.y - headLength * sin(angle + spread)))
    head.lineWidth = 6
    head.lineCapStyle = .round
    head.lineJoinStyle = .round
    head.stroke()
    NSGraphicsContext.restoreGraphicsState()
    drawFirstLaunchNote()
    NSGraphicsContext.restoreGraphicsState()
    return rep
}

func drawFirstLaunchNote() {
    let card = NSRect(x: 36, y: 16, width: size.width - 72, height: 100)
    let cardPath = NSBezierPath(roundedRect: card, xRadius: 14, yRadius: 14)
    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = color(0x1D1D1F, 0.10)
    shadow.shadowBlurRadius = 12
    shadow.shadowOffset = NSSize(width: 0, height: -3)
    shadow.set()
    color(0xFFFFFF).setFill()
    cardPath.fill()
    NSGraphicsContext.restoreGraphicsState()
    color(0x0062CC, 0.14).setStroke()
    cardPath.lineWidth = 1
    cardPath.stroke()

    let badge = NSRect(x: card.minX + 20, y: card.midY - 22, width: 44, height: 44)
    color(0x007AFF, 0.12).setFill()
    NSBezierPath(roundedRect: badge, xRadius: 11, yRadius: 11).fill()
    let configuration = NSImage.SymbolConfiguration(pointSize: 21, weight: .semibold)
        .applying(NSImage.SymbolConfiguration(paletteColors: [color(0x0062CC)]))
    if let symbol = NSImage(systemSymbolName: "lock.shield.fill", accessibilityDescription: nil)?
        .withSymbolConfiguration(configuration) {
        let symbolSize = symbol.size
        symbol.draw(in: NSRect(x: badge.midX - symbolSize.width / 2, y: badge.midY - symbolSize.height / 2,
                               width: symbolSize.width, height: symbolSize.height))
    }

    let textX = badge.maxX + 16
    let textWidth = card.maxX - 22 - textX
    let paragraph = NSMutableParagraphStyle()
    paragraph.lineSpacing = 2
    let regular: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 13),
                                                  .foregroundColor: color(0x3A3A3C), .paragraphStyle: paragraph]
    var bold = regular
    bold[.font] = NSFont.systemFont(ofSize: 13, weight: .semibold)
    bold[.foregroundColor] = color(0x1D1D1F)
    let titleParagraph = NSMutableParagraphStyle()
    titleParagraph.paragraphSpacing = 5
    let text = NSMutableAttributedString(string: "Opening Switch Player for the first time?\n",
                                         attributes: [.font: NSFont.systemFont(ofSize: 15, weight: .semibold),
                                                      .foregroundColor: color(0x1D1D1F), .paragraphStyle: titleParagraph])
    for (part, isBold) in [
        ("If macOS says it can't be opened, click ", false), ("Done", true),
        (". Then go to ", false), ("System\u{A0}Settings\u{A0}→\u{A0}Privacy\u{A0}&\u{A0}Security", true),
        (", scroll down, and click ", false), ("Open Anyway", true),
        (". You only do this once.", false),
    ] {
        text.append(NSAttributedString(string: part, attributes: isBold ? bold : regular))
    }
    let textHeight = text.boundingRect(with: NSSize(width: textWidth, height: .greatestFiniteMagnitude),
                                       options: [.usesLineFragmentOrigin, .usesFontLeading]).height
    text.draw(with: NSRect(x: textX, y: card.midY - textHeight / 2, width: textWidth, height: textHeight),
              options: [.usesLineFragmentOrigin, .usesFontLeading])
}

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: make-dmg-background.swift <output.tiff>\n".utf8))
    exit(1)
}
let image = NSImage(size: size)
image.addRepresentation(render(scale: 1))
image.addRepresentation(render(scale: 2))
guard let tiff = image.tiffRepresentation(using: .lzw, factor: 0) else { exit(1) }
try tiff.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
