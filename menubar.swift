// ds-chat 菜单栏余额：每 2 秒读一次文件，在屏幕顶部菜单栏显示“💰 + 余额数字”。
// 文件为空就隐藏；文件超过 20 秒没更新（ai 都关了）就退出。
import Cocoa

let path = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""

final class Bar: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!

    func applicationDidFinishLaunching(_ notification: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
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
        item.button?.title = "💰" + number
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // 不在程序坞里显示图标
let bar = Bar()
app.delegate = bar
app.run()
