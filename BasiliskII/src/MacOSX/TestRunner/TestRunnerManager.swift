import Foundation
import Combine

// MARK: - Test Models

struct TestSuite: Identifiable, Hashable {
    let id: String
    let name: String
    let path: URL
    var tests: [Test]

    var passedCount: Int { tests.filter { $0.status == .passed }.count }
    var failedCount: Int { tests.filter { $0.status == .failed }.count }
}

struct Test: Identifiable, Hashable {
    let id: String
    let name: String
    let suiteName: String
    let path: URL
    var status: TestStatus = .notRun
    var lastRun: Date?
    var diffPercent: Double?

    var displayName: String {
        name.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

enum TestStatus: String {
    case notRun = "not_run"
    case running = "running"
    case passed = "passed"
    case failed = "failed"
    case recording = "recording"
}

struct App: Identifiable, Hashable {
    let id: String
    let name: String
    let path: URL

    var uiTestsPath: URL {
        path.appendingPathComponent("ui_tests")
    }
}

// MARK: - Recorded Event (for live display)

struct RecordedEventInfo: Identifiable {
    let id = UUID()
    let timestamp: UInt32
    let eventType: String
    let details: String
}

// MARK: - Test Runner Manager

class TestRunnerManager: ObservableObject {
    static let shared = TestRunnerManager()

    // Configuration
    static let standardScreenWidth = 640
    static let standardScreenHeight = 870
    static let appsBasePath = "/Users/tozer/code/aiclassic/Apps"

    // MARK: - Published State

    @Published var apps: [App] = []
    @Published var selectedApp: App?
    @Published var suites: [TestSuite] = []
    @Published var selectedSuite: TestSuite?
    @Published var selectedTest: Test?
    @Published var isRecording = false
    @Published var recordingStatus = "Ready"
    @Published var currentRecordingEvents: Int = 0
    @Published var screenSizeWarning: String?
    @Published var liveEvents: [RecordedEventInfo] = []

    // Automation socket
    private let automationSocket = "basilisk_auto"

    // Timer for polling events during recording
    private var eventPollingTimer: Timer?

    // MARK: - Initialization

    private init() {
        loadApps()
    }

    // MARK: - App Discovery

    func loadApps() {
        let basePath = URL(fileURLWithPath: Self.appsBasePath)

        guard let contents = try? FileManager.default.contentsOfDirectory(
            at: basePath,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else {
            print("TestRunnerManager: Could not read Apps directory")
            return
        }

        apps = contents.compactMap { url -> App? in
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir),
                  isDir.boolValue else {
                return nil
            }

            // Check if it has a build folder (indicates it's an app project)
            let buildPath = url.appendingPathComponent("build")
            guard FileManager.default.fileExists(atPath: buildPath.path) else {
                return nil
            }

            return App(id: url.lastPathComponent, name: url.lastPathComponent, path: url)
        }.sorted { $0.name < $1.name }

        print("TestRunnerManager: Found \(apps.count) apps")
    }

    // MARK: - Test Discovery

    func loadTests(for app: App) {
        selectedApp = app
        suites = []

        let testsPath = app.uiTestsPath

        // Create ui_tests folder if it doesn't exist
        if !FileManager.default.fileExists(atPath: testsPath.path) {
            try? FileManager.default.createDirectory(at: testsPath, withIntermediateDirectories: true)
        }

        guard let contents = try? FileManager.default.contentsOfDirectory(
            at: testsPath,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else {
            print("TestRunnerManager: No ui_tests directory for \(app.name)")
            return
        }

        // Each subdirectory is a suite
        for suiteURL in contents {
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: suiteURL.path, isDirectory: &isDir),
                  isDir.boolValue else {
                continue
            }

            let suiteName = suiteURL.lastPathComponent
            var tests: [Test] = []

            // Find all .json test files in the suite
            if let testFiles = try? FileManager.default.contentsOfDirectory(
                at: suiteURL,
                includingPropertiesForKeys: nil,
                options: [.skipsHiddenFiles]
            ) {
                for testFile in testFiles where testFile.pathExtension == "json" {
                    let testName = testFile.deletingPathExtension().lastPathComponent
                    let test = Test(
                        id: "\(suiteName)/\(testName)",
                        name: testName,
                        suiteName: suiteName,
                        path: testFile
                    )
                    tests.append(test)
                }
            }

            // Show all suites, even empty ones (so users can add tests to them)
            let suite = TestSuite(
                id: suiteName,
                name: suiteName.replacingOccurrences(of: "_", with: " ").capitalized,
                path: suiteURL,
                tests: tests.sorted { $0.name < $1.name }
            )
            suites.append(suite)
        }

        suites.sort { $0.name < $1.name }
        print("TestRunnerManager: Loaded \(suites.count) suites for \(app.name)")
        for suite in suites {
            print("  - Suite: \(suite.name) (\(suite.tests.count) tests)")
        }
    }

    // MARK: - Screen Size Validation

    func checkScreenSize() {
        let prefs = PreferencesManager.shared
        if prefs.screenWidth != Self.standardScreenWidth || prefs.screenHeight != Self.standardScreenHeight {
            screenSizeWarning = "Screen size is \(prefs.screenWidth)x\(prefs.screenHeight). Tests require \(Self.standardScreenWidth)x\(Self.standardScreenHeight) (Apple Full Page Portrait)."
        } else {
            screenSizeWarning = nil
        }
    }

    // Temporary storage for recording screenshots
    private var recordingInitialScreenshot: Data?

    // MARK: - Recording

    func startRecording() {
        checkScreenSize()
        guard screenSizeWarning == nil else { return }

        isRecording = true
        recordingStatus = "Recording..."
        currentRecordingEvents = 0
        liveEvents = []

        // Call Python script or directly use automation socket
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            // Capture initial screenshot before starting recording
            self?.captureInitialScreenshot()
            self?.startRecordingSession()

            // Start polling for live events on main thread
            DispatchQueue.main.async {
                self?.startEventPolling()
            }
        }
    }

    private func startEventPolling() {
        eventPollingTimer?.invalidate()
        eventPollingTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.pollLiveEvents()
        }
    }

    private func stopEventPolling() {
        eventPollingTimer?.invalidate()
        eventPollingTimer = nil
    }

    private func pollLiveEvents() {
        guard isRecording else {
            stopEventPolling()
            return
        }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let events = self?.fetchCurrentEvents() ?? []
            DispatchQueue.main.async {
                self?.liveEvents = events
                self?.currentRecordingEvents = events.count
            }
        }
    }

    private func fetchCurrentEvents() -> [RecordedEventInfo] {
        let script = """
        import sys
        import json
        sys.path.insert(0, '/Users/tozer/code/macemu/BasiliskII/src/Unix')
        from basilisk_client import BasiliskClient, RECORD_EVENT_KEY_DOWN, RECORD_EVENT_KEY_UP, RECORD_EVENT_MOUSE_DOWN, RECORD_EVENT_MOUSE_UP

        client = BasiliskClient('basilisk_auto')
        events = client.get_events()
        client.close()

        result = []
        for ev in events:
            event_type = ev['event_type']
            if event_type == RECORD_EVENT_KEY_DOWN:
                result.append({'ts': ev['timestamp_ms'], 'type': 'Key Down', 'details': f"keycode {ev['param1']}"})
            elif event_type == RECORD_EVENT_KEY_UP:
                result.append({'ts': ev['timestamp_ms'], 'type': 'Key Up', 'details': f"keycode {ev['param1']}"})
            elif event_type == RECORD_EVENT_MOUSE_DOWN:
                result.append({'ts': ev['timestamp_ms'], 'type': 'Mouse Down', 'details': f"({ev['param1']}, {ev['param2']}) btn {ev['param3']}"})
            elif event_type == RECORD_EVENT_MOUSE_UP:
                result.append({'ts': ev['timestamp_ms'], 'type': 'Mouse Up', 'details': f"({ev['param1']}, {ev['param2']}) btn {ev['param3']}"})

        print(json.dumps(result))
        """

        guard let output = runPythonScript(script) else { return [] }
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let data = trimmed.data(using: .utf8),
              let parsed = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            return []
        }

        return parsed.compactMap { dict -> RecordedEventInfo? in
            guard let ts = dict["ts"] as? Int,
                  let type = dict["type"] as? String,
                  let details = dict["details"] as? String else { return nil }
            return RecordedEventInfo(timestamp: UInt32(ts), eventType: type, details: details)
        }
    }

    private func captureInitialScreenshot() {
        print("TestRunnerManager: captureInitialScreenshot starting...")
        let script = """
        import sys
        sys.path.insert(0, '/Users/tozer/code/macemu/BasiliskII/src/Unix')
        from basilisk_client import BasiliskClient
        print("Python screenshot: Connecting...", flush=True)
        client = BasiliskClient('basilisk_auto')
        print("Python screenshot: Taking screenshot...", flush=True)
        result = client.screenshot()
        client.close()
        print("Python screenshot: Done, closing", flush=True)
        if result:
            import base64
            print(base64.b64encode(result[4]).decode('utf-8'))
        """

        if let output = runPythonScript(script) {
            print("TestRunnerManager: captureInitialScreenshot got output length: \(output.count)")
            // The base64 data is the last line
            let lines = output.split(separator: "\n")
            if let lastLine = lines.last {
                let trimmed = String(lastLine).trimmingCharacters(in: .whitespacesAndNewlines)
                if let data = Data(base64Encoded: trimmed) {
                    recordingInitialScreenshot = data
                    print("TestRunnerManager: captureInitialScreenshot saved \(data.count) bytes")
                }
            }
        } else {
            print("TestRunnerManager: captureInitialScreenshot returned nil")
        }
    }

    func stopRecording() {
        stopEventPolling()
        isRecording = false
        recordingStatus = "Stopping..."

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.stopRecordingSession()
        }
    }

    func discardRecording() {
        stopEventPolling()
        isRecording = false
        recordingStatus = "Discarded"
        currentRecordingEvents = 0
        liveEvents = []

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.clearRecordingSession()
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
            self?.recordingStatus = "Ready"
        }
    }

    func saveRecording(suiteName: String, testName: String) {
        guard let app = selectedApp else { return }

        let sanitizedSuite = suiteName.lowercased().replacingOccurrences(of: " ", with: "_")
        let sanitizedTest = testName.lowercased().replacingOccurrences(of: " ", with: "_")

        let suitePath = app.uiTestsPath.appendingPathComponent(sanitizedSuite)
        let testPath = suitePath.appendingPathComponent("\(sanitizedTest).json")

        // Create suite directory if needed
        try? FileManager.default.createDirectory(at: suitePath, withIntermediateDirectories: true)

        recordingStatus = "Saving..."

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.saveRecordingSession(to: testPath)

            DispatchQueue.main.async {
                self?.recordingStatus = "Saved!"
                self?.liveEvents = []
                self?.currentRecordingEvents = 0
                self?.loadTests(for: app)

                DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                    self?.recordingStatus = "Ready"
                }
            }
        }
    }

    // MARK: - Playback

    func runTest(_ test: Test) {
        checkScreenSize()
        guard screenSizeWarning == nil else { return }

        updateTestStatus(test, status: .running)

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = self?.runTestPlayback(test)

            DispatchQueue.main.async {
                if let result = result {
                    self?.updateTestStatus(test, status: result.passed ? .passed : .failed, diffPercent: result.diffPercent)
                } else {
                    self?.updateTestStatus(test, status: .failed)
                }
            }
        }
    }

    func runSuite(_ suite: TestSuite) {
        for test in suite.tests {
            runTest(test)
        }
    }

    func runAllTests() {
        for suite in suites {
            runSuite(suite)
        }
    }

    private func updateTestStatus(_ test: Test, status: TestStatus, diffPercent: Double? = nil) {
        for i in 0..<suites.count {
            for j in 0..<suites[i].tests.count {
                if suites[i].tests[j].id == test.id {
                    suites[i].tests[j].status = status
                    suites[i].tests[j].lastRun = Date()
                    suites[i].tests[j].diffPercent = diffPercent
                    return
                }
            }
        }
    }

    // MARK: - Automation Socket Communication

    private func startRecordingSession() {
        let script = """
        import sys
        sys.path.insert(0, '/Users/tozer/code/macemu/BasiliskII/src/Unix')
        from basilisk_client import BasiliskClient
        print("Python: Connecting to automation socket...", flush=True)
        try:
            client = BasiliskClient('basilisk_auto')
            print("Python: Connected, calling start_recording...", flush=True)
            result = client.start_recording()
            print(f"Python: start_recording returned {result}", flush=True)
            client.close()
            print("Python: Done", flush=True)
        except Exception as e:
            print(f"Python ERROR: {e}", flush=True)
            import traceback
            traceback.print_exc()
        """

        if let output = runPythonScript(script) {
            print("TestRunnerManager: startRecordingSession output: \(output)")
        } else {
            print("TestRunnerManager: startRecordingSession returned nil")
        }
    }

    private func stopRecordingSession() {
        let script = """
        import sys
        sys.path.insert(0, '/Users/tozer/code/macemu/BasiliskII/src/Unix')
        from basilisk_client import BasiliskClient
        client = BasiliskClient('basilisk_auto')
        count = client.stop_recording()
        client.close()
        print(count)
        """

        if let output = runPythonScript(script), let count = Int(output.trimmingCharacters(in: .whitespacesAndNewlines)) {
            DispatchQueue.main.async { [weak self] in
                self?.currentRecordingEvents = count
                self?.recordingStatus = "Stopped (\(count) events)"
            }
        }
    }

    private func clearRecordingSession() {
        let script = """
        import sys
        sys.path.insert(0, '/Users/tozer/code/macemu/BasiliskII/src/Unix')
        from basilisk_client import BasiliskClient
        client = BasiliskClient('basilisk_auto')
        client.clear_events()
        client.close()
        """

        runPythonScript(script)
    }

    private func saveRecordingSession(to path: URL) {
        // Screenshots directory lives next to the test JSON, named {testname}_screenshots
        let testName = path.deletingPathExtension().lastPathComponent
        let screenshotsDir = "\(testName)_screenshots"
        let screenshotsDirPath = path.deletingLastPathComponent().appendingPathComponent(screenshotsDir)

        // Create screenshots directory in repo
        try? FileManager.default.createDirectory(at: screenshotsDirPath, withIntermediateDirectories: true)

        // Save initial screenshot if we captured it
        if let initialData = recordingInitialScreenshot {
            saveScreenshotFromRawData(initialData, to: screenshotsDirPath.appendingPathComponent("initial.png"))
        }

        let script = """
        import sys
        import json
        from datetime import datetime
        sys.path.insert(0, '/Users/tozer/code/macemu/BasiliskII/src/Unix')
        from basilisk_client import BasiliskClient, RECORD_EVENT_KEY_DOWN, RECORD_EVENT_KEY_UP, RECORD_EVENT_MOUSE_DOWN, RECORD_EVENT_MOUSE_UP
        from PIL import Image

        client = BasiliskClient('basilisk_auto')
        events = client.get_events()

        # Take final screenshot
        result = client.screenshot()
        client.close()

        screenshots_dir = '\(screenshotsDirPath.path)'

        # Save final screenshot to repo
        if result:
            width, height, depth, bpr, data = result
            img = Image.frombytes('RGBA', (width, height), data, 'raw', 'ARGB', bpr)
            img = img.convert('RGB')
            img.save(f'{screenshots_dir}/final.png', 'PNG')

        # Build test document with relative paths (so it works from repo)
        steps = [{"action": "screenshot", "label": "initial", "file": "\(screenshotsDir)/initial.png"}]

        for ev in events:
            step = {"timestamp_ms": ev['timestamp_ms']}
            if ev['event_type'] == RECORD_EVENT_KEY_DOWN:
                step["action"] = "key_down"
                step["keycode"] = ev['param1']
            elif ev['event_type'] == RECORD_EVENT_KEY_UP:
                step["action"] = "key_up"
                step["keycode"] = ev['param1']
            elif ev['event_type'] == RECORD_EVENT_MOUSE_DOWN:
                step["action"] = "mouse_down"
                step["x"] = ev['param1']
                step["y"] = ev['param2']
                step["button"] = ev['param3']
            elif ev['event_type'] == RECORD_EVENT_MOUSE_UP:
                step["action"] = "mouse_up"
                step["x"] = ev['param1']
                step["y"] = ev['param2']
                step["button"] = ev['param3']
            else:
                continue
            steps.append(step)

        steps.append({"action": "screenshot", "label": "final", "file": "\(screenshotsDir)/final.png"})

        test_doc = {
            "name": "\(testName)",
            "recorded": datetime.now().isoformat(),
            "screen_size": {"width": \(TestRunnerManager.standardScreenWidth), "height": \(TestRunnerManager.standardScreenHeight)},
            "event_count": len(events),
            "screenshots_dir": "\(screenshotsDir)",
            "steps": steps
        }

        with open('\(path.path)', 'w') as f:
            json.dump(test_doc, f, indent=2)

        print('saved')
        """

        runPythonScript(script)
        recordingInitialScreenshot = nil  // Clear after saving
    }

    private func saveScreenshotFromRawData(_ data: Data, to path: URL) {
        // The raw data is ARGB framebuffer, need to convert to PNG
        let script = """
        import sys
        import base64
        sys.path.insert(0, '/Users/tozer/code/macemu/BasiliskII/src/Unix')
        from PIL import Image

        data = base64.b64decode('\(data.base64EncodedString())')
        width = \(TestRunnerManager.standardScreenWidth)
        height = \(TestRunnerManager.standardScreenHeight)
        bpr = width * 4

        img = Image.frombytes('RGBA', (width, height), data, 'raw', 'ARGB', bpr)
        img = img.convert('RGB')
        img.save('\(path.path)', 'PNG')
        print('saved')
        """

        runPythonScript(script)
    }

    private func runTestPlayback(_ test: Test) -> (passed: Bool, diffPercent: Double)? {
        let script = """
        import sys
        import json
        sys.path.insert(0, '/Users/tozer/code/macemu/BasiliskII/src/Unix')

        # Run playback
        from playback_test import playback_test
        success, report = playback_test('\(test.path.path)', speed=1.0)

        diff = report.get('steps', [{}])[-1].get('diff_percent', 0)
        print(f"{'PASSED' if success else 'FAILED'}|{diff}")
        """

        if let output = runPythonScript(script) {
            let parts = output.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: "|")
            if parts.count == 2 {
                let passed = parts[0] == "PASSED"
                let diff = Double(parts[1]) ?? 0
                return (passed, diff)
            }
        }
        return nil
    }

    @discardableResult
    private func runPythonScript(_ script: String) -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = ["-c", script]

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe

        // Read output in background to avoid pipe buffer deadlock
        var outputData = Data()
        let readQueue = DispatchQueue(label: "python-output-reader")
        let readGroup = DispatchGroup()

        readGroup.enter()
        readQueue.async {
            outputData = pipe.fileHandleForReading.readDataToEndOfFile()
            readGroup.leave()
        }

        do {
            try process.run()
            process.waitUntilExit()
            readGroup.wait()

            return String(data: outputData, encoding: .utf8)
        } catch {
            print("TestRunnerManager: Python script error: \(error)")
            return nil
        }
    }

    // MARK: - Create New Suite

    func createSuite(name: String) {
        guard let app = selectedApp else {
            print("TestRunnerManager: Cannot create suite - no app selected")
            return
        }

        guard !name.isEmpty else {
            print("TestRunnerManager: Cannot create suite - name is empty")
            return
        }

        let sanitizedName = name.lowercased().replacingOccurrences(of: " ", with: "_")
        let suitePath = app.uiTestsPath.appendingPathComponent(sanitizedName)

        print("TestRunnerManager: Creating suite at \(suitePath.path)")

        do {
            try FileManager.default.createDirectory(at: suitePath, withIntermediateDirectories: true)
            print("TestRunnerManager: Suite created successfully")
        } catch {
            print("TestRunnerManager: Failed to create suite: \(error)")
        }

        // Ensure UI update happens on main thread
        DispatchQueue.main.async { [weak self] in
            if let self = self {
                self.loadTests(for: app)
            }
        }
    }
}
