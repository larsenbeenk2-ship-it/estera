import AppKit
import WebKit

// The native window owns exactly one bundled backend. It never adopts a server
// already listening on localhost or terminates an unrelated process.
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var backend: Process?
    private var quitting = false
    private var ready = false
    private var failed = false
    private let identity = UUID().uuidString
    private let endpoint = URL(string: "http://127.0.0.1:3000")!
    private var deadline = Date()
    private var pendingDownloads = Set<WKDownload>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let menu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Estera", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Estera", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let appItem = NSMenuItem(); appItem.submenu = appMenu; menu.addItem(appItem)
        let editMenu = NSMenu(title: "Edit")
        for (title, action, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        let editItem = NSMenuItem(); editItem.submenu = editMenu; menu.addItem(editItem)
        NSApp.mainMenu = menu

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Estera"
        window.minSize = NSSize(width: 900, height: 620)
        window.center()
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.setFrameAutosaveName("EsteraMainWindow")
        let configuration = WKWebViewConfiguration()
        // Persistent local storage remembers the user's selected phone, never pairing secrets.
        configuration.websiteDataStore = .default()
        webView = WKWebView(frame: window.contentView!.bounds, configuration: configuration)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        window.backgroundColor = NSColor(calibratedWhite: 0.035, alpha: 1)
        window.contentView = webView
        webView.loadHTMLString("<html><body style='background:#090909;color:#eee;font:18px -apple-system;padding:64px'><h1>Estera.</h1><p>Starting your local app…</p></body></html>", baseURL: nil)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startBackend()
    }

    private func startBackend() {
        let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/EsteraBackend/estera-desktop")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            showFailure("The bundled backend is missing. Download a fresh copy of Estera and keep the application bundle intact.")
            return
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = ["--web", "--desktop-token", identity, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        process.currentDirectoryURL = executable.deletingLastPathComponent()
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self else { return }
                if self.quitting { NSApp.reply(toApplicationShouldTerminate: true) }
                else { self.showFailure("The local backend stopped. If the browser version is running, stop it with npm run stop, then reopen Estera. Phone location is unknown after an interrupted session.") }
            }
        }
        backend = process
        do {
            try process.run()
            deadline = Date().addingTimeInterval(35)
            pollBackend()
        } catch {
            showFailure("Estera could not start its bundled backend. Download a fresh copy of the app. \(error.localizedDescription)")
        }
    }

    private func pollBackend() {
        guard !quitting && !failed else { return }
        var request = URLRequest(url: endpoint.appendingPathComponent("api/bootstrap"))
        request.timeoutInterval = 2
        request.cachePolicy = .reloadIgnoringLocalCacheData
        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self, !self.quitting, !self.failed else { return }
                if let data, let http = response as? HTTPURLResponse, http.statusCode == 200,
                   let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    guard value["desktop_instance"] as? String == self.identity else {
                        self.showFailure("Another Estera session is using port 3000. Stop the browser version with npm run stop, or quit the other copy of Estera, then reopen this app.")
                        return
                    }
                    self.ready = true
                    self.webView.load(URLRequest(url: self.endpoint))
                } else if Date() > self.deadline {
                    self.showFailure("Estera did not finish starting. Another application may be using port 3000. Close the other session and reopen Estera.")
                } else {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.pollBackend() }
                }
            }
        }.resume()
    }

    private func showFailure(_ message: String) {
        guard !failed && !quitting else { return }
        failed = true
        let alert = NSAlert()
        alert.messageText = "Estera couldn’t continue"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Quit")
        alert.beginSheetModal(for: window) { _ in NSApp.terminate(nil) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateLater }
        quitting = true
        webView?.stopLoading()
        guard let process = backend, process.isRunning else { return .terminateNow }
        // SIGTERM invokes the server's bounded shutdown and helper location-clear attempt.
        process.terminate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 16) {
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if action.shouldPerformDownload && (isLocal(url) || url.scheme == "blob") { decisionHandler(.download); return }
        if isLocal(url) || (!ready && url.absoluteString == "about:blank") { decisionHandler(.allow); return }
        if action.navigationType == .linkActivated && ["https", "http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }

    private func isLocal(_ url: URL) -> Bool {
        url.scheme == "http" && url.host == "127.0.0.1" && url.port == 3000
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, action.navigationType == .linkActivated, ["https", "http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard let url = frame.request.url, isLocal(url) else { completionHandler(false); return }
        let alert = NSAlert()
        alert.messageText = "Confirm in Estera"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Continue")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { result in completionHandler(result == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        pendingDownloads.insert(download)
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.url : nil) }
    }

    func downloadDidFinish(_ download: WKDownload) { pendingDownloads.remove(download) }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) { pendingDownloads.remove(download) }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.run()
