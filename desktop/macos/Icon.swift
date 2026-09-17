import AppKit

let directory = CommandLine.arguments[1]
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                     bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                     isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        let factor = CGFloat(pixels) / 64
        let transform = NSAffineTransform()
        transform.scale(by: factor)
        transform.concat()
        NSColor(calibratedWhite: 0.035, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 2, y: 2, width: 60, height: 60), xRadius: 16, yRadius: 16).fill()
        let ghost = NSBezierPath()
        ghost.move(to: NSPoint(x: 20, y: 22))
        ghost.line(to: NSPoint(x: 20, y: 37))
        ghost.curve(to: NSPoint(x: 44, y: 37), controlPoint1: NSPoint(x: 20, y: 53), controlPoint2: NSPoint(x: 44, y: 53))
        for point in [NSPoint(x: 44, y: 22), NSPoint(x: 38, y: 26), NSPoint(x: 32, y: 22), NSPoint(x: 26, y: 26)] { ghost.line(to: point) }
        ghost.close()
        NSColor(calibratedRed: 0.73, green: 0.98, blue: 0.81, alpha: 1).setStroke()
        ghost.lineWidth = 3.2
        ghost.lineJoinStyle = .round
        ghost.stroke()
        NSColor.white.setFill()
        for x in [26, 34] { NSBezierPath(ovalIn: NSRect(x: x, y: 34, width: 4, height: 4)).fill() }
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "\(directory)/icon_\(size)x\(size)\(suffix).png"))
    }
}
