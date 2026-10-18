// ds-chat 菜单栏余额：每 2 秒读一次文件，在屏幕顶部菜单栏显示“橘色钱袋图标 + 余额数字”。
// 文件为空就隐藏；文件超过 20 秒没更新（ai 都关了）就退出。
import Cocoa

let path = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""
let orange = NSColor(srgbRed: 0.93, green: 0.48, blue: 0.29, alpha: 1)

// 画在 18x18 的格子里（y 轴朝上）：外圈圆环 + 实心钱袋，袋身上的 $ 镂空
func drawBag(_ rect: NSRect) {
    let u = rect.width / 18
    func r(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat) -> NSRect {
        NSRect(x: rect.minX + x * u, y: rect.minY + y * u, width: w * u, height: h * u)
    }
    orange.set()
    let ring = NSBezierPath(ovalIn: r(0.9, 0.9, 16.2, 16.2))
    ring.lineWidth = 1.5 * u
    ring.stroke()
    // 袋身：下宽上窄的圆润形状
    let body = NSBezierPath()
    body.move(to: NSPoint(x: rect.minX + 7.3 * u, y: rect.minY + 11.2 * u))
    body.curve(to: NSPoint(x: rect.minX + 4.2 * u, y: rect.minY + 5.6 * u),
               controlPoint1: NSPoint(x: rect.minX + 5.2 * u, y: rect.minY + 9.8 * u),
               controlPoint2: NSPoint(x: rect.minX + 4.2 * u, y: rect.minY + 7.8 * u))
    body.curve(to: NSPoint(x: rect.minX + 9 * u, y: rect.minY + 3.6 * u),
               controlPoint1: NSPoint(x: rect.minX + 4.2 * u, y: rect.minY + 4.2 * u),
               controlPoint2: NSPoint(x: rect.minX + 6 * u, y: rect.minY + 3.6 * u))
    body.curve(to: NSPoint(x: rect.minX + 13.8 * u, y: rect.minY + 5.6 * u),
               controlPoint1: NSPoint(x: rect.minX + 12 * u, y: rect.minY + 3.6 * u),
               controlPoint2: NSPoint(x: rect.minX + 13.8 * u, y: rect.minY + 4.2 * u))
    body.curve(to: NSPoint(x: rect.minX + 10.7 * u, y: rect.minY + 11.2 * u),
               controlPoint1: NSPoint(x: rect.minX + 13.8 * u, y: rect.minY + 7.8 * u),
               controlPoint2: NSPoint(x: rect.minX + 12.8 * u, y: rect.minY + 9.8 * u))
    body.close()
    body.fill()
    // 扎口和袋口两只“耳朵”
    NSBezierPath(roundedRect: r(7.1, 11.0, 3.8, 1.1), xRadius: 0.5 * u, yRadius: 0.5 * u).fill()
    let top = NSBezierPath()
    top.move(to: NSPoint(x: rect.minX + 7.4 * u, y: rect.minY + 12.1 * u))
    top.line(to: NSPoint(x: rect.minX + 6.3 * u, y: rect.minY + 14.0 * u))
    top.line(to: NSPoint(x: rect.minX + 9.0 * u, y: rect.minY + 13.1 * u))
    top.line(to: NSPoint(x: rect.minX + 11.7 * u, y: rect.minY + 14.0 * u))
    top.line(to: NSPoint(x: rect.minX + 10.6 * u, y: rect.minY + 12.1 * u))
    top.close()
    top.fill()
    // 镂空的 $
    NSGraphicsContext.current?.compositingOperation = .destinationOut
    let dollar = NSAttributedString(string: "$", attributes: [
        .font: NSFont.systemFont(ofSize: 6.4 * u, weight: .heavy), .foregroundColor: NSColor.black])
    let s = dollar.size()
    dollar.draw(at: NSPoint(x: rect.midX - s.width / 2, y: rect.minY + 7.0 * u - s.height / 2))
    NSGraphicsContext.current?.compositingOperation = .sourceOver
}

func bagIcon(size: CGFloat) -> NSImage {
    let image = NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
        drawBag(rect)
        return true
    }
    image.isTemplate = false
    return image
}

final class Bar: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!

    func applicationDidFinishLaunching(_ notification: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = bagIcon(size: 18)
        item.button?.imagePosition = .imageLeft
        item.button?.font = NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .semibold)
        tick()
        Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.tick() }
    }

    func tick() {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
              let modified = attrs[.modificationDate] as? Date,
              Date().timeIntervalSince(modified) < 20,
              let text = try? String(contentsOfFile: path, encoding: .utf8) else {
            NSApp.terminate(nil)
            return
        }
        // 兼容旧版写进来的 💲
        let number = text.replacingOccurrences(of: "💲", with: "").trimmingCharacters(in: .whitespacesAndNewlines)
        item.isVisible = !number.isEmpty
        item.button?.title = " " + number
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // 不在程序坞里显示图标
let bar = Bar()
app.delegate = bar
app.run()
