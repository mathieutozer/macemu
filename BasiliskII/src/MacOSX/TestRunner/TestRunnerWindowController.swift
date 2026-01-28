import SwiftUI
import AppKit

// MARK: - Test Runner Window Controller

class TestRunnerWindowController: NSWindowController {
    static let shared = TestRunnerWindowController()

    private var testRunnerWindow: NSWindow?

    private init() {
        super.init(window: nil)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func showTestRunner() {
        if let existingWindow = testRunnerWindow {
            existingWindow.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let testRunnerView = TestRunnerView()
        let hostingController = NSHostingController(rootView: testRunnerView)

        let window = NSWindow(contentViewController: hostingController)
        window.title = "Test Runner"
        window.styleMask = [.titled, .closable, .resizable, .miniaturizable]
        window.setContentSize(NSSize(width: 750, height: 550))
        window.minSize = NSSize(width: 600, height: 400)
        window.center()

        // Use modern toolbar style
        window.toolbarStyle = .unified

        // Set window to not release when closed
        window.isReleasedWhenClosed = false

        self.testRunnerWindow = window
        self.window = window

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

// MARK: - Objective-C Bridge

@objc class TestRunnerBridge: NSObject {
    @objc static let shared = TestRunnerBridge()

    @objc func showTestRunnerWindow() {
        DispatchQueue.main.async {
            TestRunnerWindowController.shared.showTestRunner()
        }
    }
}
