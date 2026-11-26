import Cocoa
// Draws the link app's icon (SwitchboardLinks.icns; 1024 px, transparent corners) with CoreGraphics: Switchboard's mark,
// a dark tile with a white S that runs from a white end to a blue dot, on the same 24-unit grid as the page's mark
// (web/index.html, BRAND), with a thin #3a3a3c edge so the tile holds on dark grounds. `sh build.sh --icon` makes the
// icns from it.
let size = 1024
let space = CGColorSpace(name: CGColorSpace.sRGB)!                    // the page's colours, exactly
let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0, space: space,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.translateBy(x: 0, y: CGFloat(size)); ctx.scaleBy(x: 1, y: -1)     // top-left coordinates, as in the page's SVG
let k: CGFloat = 824 / 24                                              // the tile is macOS's 824 px of the 1024
func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: 100 + x * k, y: 100 + y * k) }
func rgb(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat) -> CGColor { CGColor(srgbRed: r / 255, green: g / 255, blue: b / 255, alpha: 1) }
let tile = CGPath(roundedRect: CGRect(x: 100, y: 100, width: 824, height: 824), cornerWidth: 186, cornerHeight: 186, transform: nil)
ctx.addPath(tile); ctx.setFillColor(rgb(0x1d, 0x1d, 0x1f)); ctx.fillPath()
let rim = CGPath(roundedRect: CGRect(x: 103, y: 103, width: 818, height: 818), cornerWidth: 183, cornerHeight: 183, transform: nil)
ctx.addPath(rim); ctx.setStrokeColor(rgb(0x3a, 0x3a, 0x3c)); ctx.setLineWidth(6); ctx.strokePath()
// The S: from the dot along the top, round the left, along the middle, round the right, along the bottom to its end.
ctx.move(to: p(16.3, 7)); ctx.addLine(to: p(10.05, 7))
ctx.addArc(tangent1End: p(7.55, 7), tangent2End: p(7.55, 9.5), radius: 2.5 * k)
ctx.addArc(tangent1End: p(7.55, 12), tangent2End: p(10.05, 12), radius: 2.5 * k)
ctx.addLine(to: p(14.3, 12))
ctx.addArc(tangent1End: p(16.8, 12), tangent2End: p(16.8, 14.5), radius: 2.5 * k)
ctx.addArc(tangent1End: p(16.8, 17), tangent2End: p(14.3, 17), radius: 2.5 * k)
ctx.addLine(to: p(7.55, 17))
ctx.setStrokeColor(rgb(255, 255, 255)); ctx.setLineWidth(2.2 * k); ctx.setLineCap(.round); ctx.setLineJoin(.round)
ctx.strokePath()
func dot(_ x: CGFloat, _ y: CGFloat, _ r: CGFloat, _ c: CGColor) {
    let o = p(x, y)
    ctx.setFillColor(c); ctx.fillEllipse(in: CGRect(x: o.x - r * k, y: o.y - r * k, width: 2 * r * k, height: 2 * r * k))
}
dot(7.55, 17, 2.2, rgb(255, 255, 255))      // the S's end
dot(16.3, 7, 2.3, rgb(0x3b, 0x9e, 0xff))    // the blue dot, at the S's top-right end
let rep = NSBitmapImageRep(cgImage: ctx.makeImage()!)
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
