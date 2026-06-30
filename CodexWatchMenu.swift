import Cocoa
import Foundation

struct CodexSession: Decodable {
    let thread_id: String
    let title: String
    let status: String
    let updated_at: String
    let cwd: String?
}

struct NotificationRequest: Decodable {
    let id: String
    let title: String
    let message: String
    let sound: String?
    let thread_id: String?
    let cwd: String?
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSUserNotificationCenterDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private var timer: Timer?
    private var notificationTimer: Timer?
    private var sessions: [CodexSession] = []
    private var lastError: String?
    private var seenNotificationIDs = Set<String>()

    private var watcherPath: String {
        "\(NSHomeDirectory())/.codex/codex_notify_watch.py"
    }

    private var notificationPath: String {
        "\(NSHomeDirectory())/.codex/codex-watch-menu-notifications.jsonl"
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        NSUserNotificationCenter.default.delegate = self
        statusItem.button?.title = "Codex"
        rememberExistingNotifications()
        rebuildMenu()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        notificationTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            self?.pollNotifications()
        }
    }

    @objc private func refresh() {
        do {
            sessions = try loadSessions()
            lastError = nil
        } catch {
            sessions = []
            lastError = error.localizedDescription
        }
        updateTitle()
        rebuildMenu()
    }

    @objc private func showStatus() {
        showCommandOutput(title: "Watcher Status", arguments: [watcherPath, "--status"])
    }

    @objc private func showLogs() {
        showCommandOutput(title: "Log Paths", arguments: [watcherPath, "--logs"])
    }

    @objc private func openSession(_ sender: NSMenuItem) {
        let index = sender.tag
        guard sessions.indices.contains(index) else {
            return
        }
        openSession(threadID: sessions[index].thread_id, cwd: sessions[index].cwd)
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func updateTitle() {
        let approvals = sessions.filter { $0.status == "needs approval" }.count
        if approvals > 0 {
            statusItem.button?.title = "Codex !\(approvals)"
            return
        }
        let running = sessions.filter { $0.status == "running" }.count
        statusItem.button?.title = running > 0 ? "Codex \(running)" : "Codex"
    }

    private func rebuildMenu() {
        let menu = NSMenu()
        let title = NSMenuItem(title: "Codex Watch", action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)
        menu.addItem(NSMenuItem.separator())

        if let lastError {
            let item = NSMenuItem(title: "Error: \(lastError)", action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
        } else if sessions.isEmpty {
            let item = NSMenuItem(title: "No sessions found", action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
        } else {
            for (index, session) in sessions.enumerated() {
                let item = NSMenuItem(title: "\(label(for: session.status))  \(short(session.title))", action: #selector(openSession(_:)), keyEquivalent: "")
                item.tag = index
                item.target = self
                item.toolTip = session.cwd ?? session.thread_id
                menu.addItem(item)
            }
        }

        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Refresh", action: #selector(refresh), keyEquivalent: "r"))
        menu.addItem(NSMenuItem(title: "Print Watcher Status", action: #selector(showStatus), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Print Log Paths", action: #selector(showLogs), keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit Codex Watch", action: #selector(quit), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    private func label(for status: String) -> String {
        switch status {
        case "needs approval":
            return "Needs Approval"
        case "running":
            return "Running"
        case "completed":
            return "Done"
        default:
            return "Unknown"
        }
    }

    private func short(_ value: String) -> String {
        if value.count <= 42 {
            return value
        }
        return String(value.prefix(39)) + "..."
    }

    private func loadSessions() throws -> [CodexSession] {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [watcherPath, "--sessions-json", "--sessions-limit", "8"]

        let output = Pipe()
        let error = Pipe()
        process.standardOutput = output
        process.standardError = error
        try process.run()
        process.waitUntilExit()

        let data = output.fileHandleForReading.readDataToEndOfFile()
        if process.terminationStatus != 0 {
            let message = String(data: error.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "unknown error"
            throw NSError(domain: "CodexWatchMenu", code: Int(process.terminationStatus), userInfo: [
                NSLocalizedDescriptionKey: message.trimmingCharacters(in: .whitespacesAndNewlines)
            ])
        }
        return try JSONDecoder().decode([CodexSession].self, from: data)
    }

    private func pollNotifications() {
        guard let data = FileManager.default.contents(atPath: notificationPath),
              let text = String(data: data, encoding: .utf8) else {
            return
        }
        for line in text.split(separator: "\n") {
            guard let request = decodeNotificationLine(line),
                  !seenNotificationIDs.contains(request.id) else {
                continue
            }
            seenNotificationIDs.insert(request.id)
            deliver(request)
        }
    }

    private func rememberExistingNotifications() {
        guard let data = FileManager.default.contents(atPath: notificationPath),
              let text = String(data: data, encoding: .utf8) else {
            return
        }
        for line in text.split(separator: "\n") {
            if let request = decodeNotificationLine(line) {
                seenNotificationIDs.insert(request.id)
            }
        }
    }

    private func decodeNotificationLine(_ line: Substring) -> NotificationRequest? {
        guard let itemData = String(line).data(using: .utf8) else {
            return nil
        }
        return try? JSONDecoder().decode(NotificationRequest.self, from: itemData)
    }

    private func deliver(_ request: NotificationRequest) {
        let notification = NSUserNotification()
        notification.identifier = request.id
        notification.title = request.title
        notification.informativeText = request.message
        notification.hasActionButton = true
        notification.actionButtonTitle = "Show"
        notification.userInfo = [
            "thread_id": request.thread_id ?? "",
            "cwd": request.cwd ?? ""
        ]
        NSUserNotificationCenter.default.deliver(notification)
        playSound(request.sound)
    }

    func userNotificationCenter(_ center: NSUserNotificationCenter, didActivate notification: NSUserNotification) {
        let threadID = notification.userInfo?["thread_id"] as? String
        let cwd = notification.userInfo?["cwd"] as? String
        openSession(threadID: threadID, cwd: cwd)
    }

    func userNotificationCenter(_ center: NSUserNotificationCenter, shouldPresent notification: NSUserNotification) -> Bool {
        true
    }

    private func openSession(threadID: String?, cwd: String?) {
        if let cwd, !cwd.isEmpty {
            openVSCodeWorkspace(cwd)
        } else {
            openVSCodeApp()
        }
        if let threadID, !threadID.isEmpty {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                self.openCodexRoute(threadID)
            }
        }
    }

    private func openVSCodeWorkspace(_ cwd: String) {
        let workspaceURL = URL(fileURLWithPath: cwd)
        let appURL = URL(fileURLWithPath: "/Applications/Visual Studio Code.app")
        let config = NSWorkspace.OpenConfiguration()
        NSWorkspace.shared.open([workspaceURL], withApplicationAt: appURL, configuration: config)
    }

    private func openVSCodeApp() {
        let appURL = URL(fileURLWithPath: "/Applications/Visual Studio Code.app")
        let config = NSWorkspace.OpenConfiguration()
        NSWorkspace.shared.openApplication(at: appURL, configuration: config)
    }

    private func openCodexRoute(_ threadID: String) {
        guard let url = URL(string: "vscode://openai.chatgpt/local/\(threadID)") else {
            return
        }
        NSWorkspace.shared.open(url)
    }

    private func playSound(_ sound: String?) {
        guard let sound, !sound.isEmpty else {
            return
        }
        let soundURL = URL(fileURLWithPath: "/System/Library/Sounds/\(sound).aiff")
        guard FileManager.default.fileExists(atPath: soundURL.path) else {
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/afplay")
        process.arguments = [soundURL.path]
        try? process.run()
    }

    private func showCommandOutput(title: String, arguments: [String]) {
        let message: String
        do {
            message = try runPython(arguments: arguments)
        } catch {
            message = error.localizedDescription
        }
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.runModal()
    }

    private func runPython(arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = arguments

        let output = Pipe()
        let error = Pipe()
        process.standardOutput = output
        process.standardError = error
        try process.run()
        process.waitUntilExit()

        let data = output.fileHandleForReading.readDataToEndOfFile()
        if process.terminationStatus != 0 {
            let message = String(data: error.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "unknown error"
            throw NSError(domain: "CodexWatchMenu", code: Int(process.terminationStatus), userInfo: [
                NSLocalizedDescriptionKey: message.trimmingCharacters(in: .whitespacesAndNewlines)
            ])
        }
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
