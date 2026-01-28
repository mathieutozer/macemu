import SwiftUI
import AppKit

// MARK: - Preferences Window Controller

class PreferencesWindowController: NSWindowController {
    static let shared = PreferencesWindowController()

    private var preferencesWindow: NSWindow?

    private init() {
        super.init(window: nil)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func showPreferences() {
        if let existingWindow = preferencesWindow {
            existingWindow.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let preferencesView = PreferencesView()
        let hostingController = NSHostingController(rootView: preferencesView)

        let window = NSWindow(contentViewController: hostingController)
        window.title = "BasiliskII Settings"
        window.styleMask = [.titled, .closable, .resizable, .miniaturizable]
        window.setContentSize(NSSize(width: 800, height: 550))
        window.minSize = NSSize(width: 700, height: 500)
        window.center()

        // Use modern toolbar style
        window.toolbarStyle = .unified

        // Set window to release when closed
        window.isReleasedWhenClosed = false

        self.preferencesWindow = window
        self.window = window

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

// MARK: - Objective-C Bridge

@objc class PreferencesBridge: NSObject {
    @objc static let shared = PreferencesBridge()

    @objc func showPreferencesWindow() {
        DispatchQueue.main.async {
            PreferencesWindowController.shared.showPreferences()
        }
    }

    @objc func getScreenWidth() -> Int {
        return PreferencesManager.shared.screenWidth
    }

    @objc func getScreenHeight() -> Int {
        return PreferencesManager.shared.screenHeight
    }

    @objc func setScreenSize(width: Int, height: Int) {
        PreferencesManager.shared.setScreenSize(width: width, height: height)
    }
}
