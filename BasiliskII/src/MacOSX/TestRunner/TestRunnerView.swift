import SwiftUI

// MARK: - Main Test Runner View

struct TestRunnerView: View {
    @ObservedObject private var manager = TestRunnerManager.shared
    @State private var showingSaveDialog = false
    @State private var newSuiteName = ""
    @State private var newTestName = ""
    @State private var showingNewSuiteDialog = false

    var body: some View {
        VStack(spacing: 0) {
            // Toolbar area
            toolbarView
                .padding()
                .background(Color(NSColor.windowBackgroundColor))

            Divider()

            // Screen size warning
            if let warning = manager.screenSizeWarning {
                HStack {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(.orange)
                    Text(warning)
                        .font(.caption)
                    Spacer()
                    Button("Fix") {
                        PreferencesManager.shared.setScreenSize(
                            width: TestRunnerManager.standardScreenWidth,
                            height: TestRunnerManager.standardScreenHeight
                        )
                        manager.checkScreenSize()
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                }
                .padding(.horizontal)
                .padding(.vertical, 8)
                .background(Color.orange.opacity(0.1))
            }

            // Main content
            if manager.selectedApp == nil {
                emptyStateView
            } else {
                HSplitView {
                    // Test list
                    testListView
                        .frame(minWidth: 300)

                    // Details pane (could show test details, screenshots)
                    detailsView
                        .frame(minWidth: 200)
                }
            }

            Divider()

            // Recording controls
            recordingControlsView
                .padding()
                .background(Color(NSColor.windowBackgroundColor))
        }
        .frame(minWidth: 600, minHeight: 400)
        .onAppear {
            manager.checkScreenSize()
        }
        .sheet(isPresented: $showingSaveDialog) {
            saveTestDialog
        }
        .sheet(isPresented: $showingNewSuiteDialog) {
            newSuiteDialog
        }
    }

    // MARK: - Toolbar

    private var toolbarView: some View {
        HStack {
            // App selector
            Picker("App:", selection: Binding(
                get: { manager.selectedApp },
                set: { app in
                    if let app = app {
                        manager.loadTests(for: app)
                    }
                }
            )) {
                Text("Select an app...").tag(nil as App?)
                ForEach(manager.apps) { app in
                    Text(app.name).tag(app as App?)
                }
            }
            .frame(width: 200)

            Spacer()

            // Run all button
            if manager.selectedApp != nil && !manager.suites.isEmpty {
                if manager.isPlaying {
                    Button(action: { manager.stopPlayback() }) {
                        Label("Stop", systemImage: "stop.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.red)
                } else {
                    Button(action: { manager.runAllTests() }) {
                        Label("Run All", systemImage: "play.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(manager.isRecording)
                }
            }
        }
    }

    // MARK: - Empty State

    private var emptyStateView: some View {
        VStack(spacing: 20) {
            Image(systemName: "testtube.2")
                .font(.system(size: 64))
                .foregroundColor(.secondary)
            Text("Select an App")
                .font(.title2)
            Text("Choose an app from the dropdown to view and manage its UI tests.")
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }

    // MARK: - Test List

    private var testListView: some View {
        List {
            ForEach(manager.suites) { suite in
                Section {
                    ForEach(suite.tests) { test in
                        TestRowView(
                            test: test,
                            isSelected: manager.selectedTest?.id == test.id,
                            isPlaying: manager.playingTest?.id == test.id,
                            onSelect: {
                                manager.selectedTest = test
                                manager.selectedSuite = suite
                            },
                            onRun: {
                                manager.runTest(test)
                            },
                            onStop: {
                                manager.stopPlayback()
                            },
                            onReRecord: {
                                // TODO: Re-record functionality
                            }
                        )
                    }
                } header: {
                    SuiteHeaderView(
                        suite: suite,
                        isSelected: manager.selectedSuite?.id == suite.id,
                        isRecording: manager.isRecording,
                        isPlaying: manager.isPlaying,
                        onSelect: {
                            manager.selectedSuite = suite
                            manager.selectedTest = nil
                        },
                        onRun: { manager.runSuite(suite) },
                        onStop: { manager.stopPlayback() }
                    )
                }
            }

            // Add new suite button
            Section {
                Button(action: { showingNewSuiteDialog = true }) {
                    Label("New Test Suite", systemImage: "folder.badge.plus")
                }
                .buttonStyle(.borderless)
            }
        }
        .listStyle(.sidebar)
    }

    // MARK: - Details View

    private var detailsView: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                if manager.isRecording || !manager.liveEvents.isEmpty {
                    Text("Live Events")
                        .font(.headline)
                    Spacer()
                    Text("\(manager.liveEvents.count) events")
                        .font(.caption)
                        .foregroundColor(.secondary)
                } else if let test = manager.selectedTest {
                    Text(test.displayName)
                        .font(.headline)
                    Spacer()
                    if manager.playingTest?.id == test.id {
                        Button(action: { manager.stopPlayback() }) {
                            Label("Stop", systemImage: "stop.fill")
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.red)
                        .controlSize(.small)
                    } else {
                        Button(action: { manager.runTest(test) }) {
                            Label("Run", systemImage: "play.fill")
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                        .disabled(manager.isPlaying)
                    }
                } else {
                    Text("Test Details")
                        .font(.headline)
                        .foregroundColor(.secondary)
                    Spacer()
                }
            }
            .padding()

            Divider()

            // Content
            if manager.isRecording || !manager.liveEvents.isEmpty {
                liveEventsView
            } else if let test = manager.selectedTest {
                selectedTestDetailsView(test: test)
            } else {
                Spacer()
                Text("Select a test to view details")
                    .foregroundColor(.secondary)
                Spacer()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Selected Test Details

    private func selectedTestDetailsView(test: Test) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            // Test info
            Group {
                HStack {
                    Text("Suite:")
                        .foregroundColor(.secondary)
                    Text(test.suiteName.replacingOccurrences(of: "_", with: " ").capitalized)
                }
                HStack {
                    Text("Status:")
                        .foregroundColor(.secondary)
                    Text(test.status.rawValue.replacingOccurrences(of: "_", with: " ").capitalized)
                        .foregroundColor(statusColor(for: test.status))
                }
                if let lastRun = test.lastRun {
                    HStack {
                        Text("Last run:")
                            .foregroundColor(.secondary)
                        Text(lastRun, style: .relative)
                    }
                }
            }
            .font(.caption)

            Spacer()
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func statusColor(for status: TestStatus) -> Color {
        switch status {
        case .passed: return .green
        case .failed: return .red
        case .running: return .orange
        case .recording: return .red
        case .notRun: return .secondary
        }
    }

    // MARK: - Live Events View

    private var liveEventsView: some View {
        ScrollViewReader { proxy in
            List {
                ForEach(manager.liveEvents) { event in
                    HStack {
                        Text(String(format: "%0.2fs", Double(event.timestamp) / 1000.0))
                            .font(.caption.monospacedDigit())
                            .foregroundColor(.secondary)
                            .frame(width: 50, alignment: .trailing)

                        Image(systemName: iconForEventType(event.eventType))
                            .foregroundColor(colorForEventType(event.eventType))
                            .frame(width: 20)

                        Text(event.eventType)
                            .font(.caption)
                            .frame(width: 70, alignment: .leading)

                        Text(event.details)
                            .font(.caption)
                            .foregroundColor(.secondary)

                        Spacer()
                    }
                    .id(event.id)
                }
            }
            .listStyle(.plain)
            .onChange(of: manager.liveEvents.count) { _ in
                // Auto-scroll to latest event
                if let lastEvent = manager.liveEvents.last {
                    withAnimation {
                        proxy.scrollTo(lastEvent.id, anchor: .bottom)
                    }
                }
            }
        }
    }

    private func iconForEventType(_ type: String) -> String {
        switch type {
        case "Key Down": return "arrow.down.square"
        case "Key Up": return "arrow.up.square"
        case "Mouse Down": return "cursorarrow.click"
        case "Mouse Up": return "cursorarrow"
        default: return "questionmark.circle"
        }
    }

    private func colorForEventType(_ type: String) -> Color {
        switch type {
        case "Key Down", "Key Up": return .blue
        case "Mouse Down", "Mouse Up": return .green
        default: return .secondary
        }
    }

    // MARK: - Recording Controls

    private var recordingControlsView: some View {
        HStack {
            // Status
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    if manager.isRecording {
                        Circle()
                            .fill(.red)
                            .frame(width: 8, height: 8)
                    }
                    Text(manager.recordingStatus)
                        .foregroundColor(.secondary)

                    if manager.currentRecordingEvents > 0 {
                        Text("(\(manager.currentRecordingEvents) events)")
                            .foregroundColor(.secondary)
                            .font(.caption)
                    }
                }

                // Show selected suite for recording
                if let suite = manager.selectedSuite {
                    HStack(spacing: 4) {
                        Image(systemName: "folder.fill")
                            .font(.caption2)
                        Text("Recording to: \(suite.name)")
                            .font(.caption)
                    }
                    .foregroundColor(.red)
                } else if manager.selectedApp != nil && !manager.isRecording && manager.currentRecordingEvents == 0 {
                    Text("Select a suite to record to")
                        .font(.caption)
                        .foregroundColor(.orange)
                }
            }

            Spacer()

            // Recording buttons
            if manager.isRecording {
                Button(action: { manager.stopRecording() }) {
                    Label("Stop", systemImage: "stop.fill")
                }
                .buttonStyle(.bordered)
                .tint(.red)
            } else {
                Button(action: { manager.startRecording() }) {
                    Label("Record", systemImage: "record.circle")
                }
                .buttonStyle(.bordered)
                .tint(.red)
                .disabled(manager.selectedApp == nil || manager.screenSizeWarning != nil)
            }

            // Post-recording actions
            if !manager.isRecording && manager.currentRecordingEvents > 0 {
                Button(action: { manager.discardRecording() }) {
                    Label("Discard", systemImage: "xmark")
                }
                .buttonStyle(.bordered)

                Button(action: {
                    // Pre-fill with selected suite
                    if let suite = manager.selectedSuite {
                        newSuiteName = suite.id
                    }
                    showingSaveDialog = true
                }) {
                    Label("Save", systemImage: "checkmark")
                }
                .buttonStyle(.borderedProminent)
            }
        }
    }

    // MARK: - Save Test Dialog

    private var saveTestDialog: some View {
        VStack(spacing: 20) {
            Text("Save Test")
                .font(.headline)

            Form {
                Picker("Suite:", selection: $newSuiteName) {
                    Text("Select a suite...").tag("")
                    ForEach(manager.suites) { suite in
                        Text(suite.name).tag(suite.id)
                    }
                    Divider()
                    Text("+ New Suite").tag("__new__")
                }

                if newSuiteName == "__new__" {
                    TextField("New Suite Name:", text: $newSuiteName)
                }

                TextField("Test Name:", text: $newTestName)
            }
            .frame(width: 300)

            HStack {
                Button("Cancel") {
                    showingSaveDialog = false
                    newSuiteName = ""
                    newTestName = ""
                }
                .keyboardShortcut(.cancelAction)

                Button("Save") {
                    let suite = newSuiteName == "__new__" ? newSuiteName : newSuiteName
                    manager.saveRecording(suiteName: suite, testName: newTestName)
                    showingSaveDialog = false
                    newSuiteName = ""
                    newTestName = ""
                }
                .keyboardShortcut(.defaultAction)
                .disabled(newSuiteName.isEmpty || newTestName.isEmpty)
            }
        }
        .padding()
        .frame(width: 400)
    }

    // MARK: - New Suite Dialog

    private var newSuiteDialog: some View {
        VStack(spacing: 20) {
            Text("New Test Suite")
                .font(.headline)

            TextField("Suite Name:", text: $newSuiteName)
                .frame(width: 250)

            HStack {
                Button("Cancel") {
                    showingNewSuiteDialog = false
                    newSuiteName = ""
                }
                .keyboardShortcut(.cancelAction)

                Button("Create") {
                    manager.createSuite(name: newSuiteName)
                    showingNewSuiteDialog = false
                    newSuiteName = ""
                }
                .keyboardShortcut(.defaultAction)
                .disabled(newSuiteName.isEmpty)
            }
        }
        .padding()
        .frame(width: 350)
    }
}

// MARK: - Suite Header View

struct SuiteHeaderView: View {
    let suite: TestSuite
    let isSelected: Bool
    let isRecording: Bool
    let isPlaying: Bool
    let onSelect: () -> Void
    let onRun: () -> Void
    let onStop: () -> Void

    var body: some View {
        HStack {
            // Selection indicator
            if isSelected {
                Image(systemName: "record.circle.fill")
                    .foregroundColor(.red)
                    .font(.caption)
            }

            Text(suite.name)
                .font(.headline)
                .foregroundColor(isSelected ? .primary : .secondary)

            Spacer()

            Text("\(suite.tests.count) tests")
                .font(.caption)
                .foregroundColor(.secondary)

            if isPlaying {
                Button(action: onStop) {
                    Image(systemName: "stop.fill")
                }
                .buttonStyle(.borderless)
                .tint(.red)
            } else {
                Button(action: onRun) {
                    Image(systemName: "play.fill")
                }
                .buttonStyle(.borderless)
                .disabled(isRecording)
            }
        }
        .padding(.vertical, 2)
        .contentShape(Rectangle())
        .onTapGesture {
            onSelect()
        }
        .background(isSelected ? Color.red.opacity(0.1) : Color.clear)
        .cornerRadius(4)
    }
}

// MARK: - Test Row View

struct TestRowView: View {
    let test: Test
    let isSelected: Bool
    let isPlaying: Bool
    let onSelect: () -> Void
    let onRun: () -> Void
    let onStop: () -> Void
    let onReRecord: () -> Void

    var body: some View {
        HStack {
            // Status indicator
            statusIcon
                .frame(width: 20)

            // Test name
            VStack(alignment: .leading) {
                Text(test.displayName)
                    .foregroundColor(isSelected ? .primary : .secondary)
                if let diff = test.diffPercent, test.status == .failed {
                    Text("Diff: \(String(format: "%.1f", diff))%")
                        .font(.caption)
                        .foregroundColor(.red)
                }
            }

            Spacer()

            // Action buttons
            if isPlaying {
                Button(action: onStop) {
                    Image(systemName: "stop.fill")
                }
                .buttonStyle(.borderless)
                .tint(.red)
            } else {
                Button(action: onRun) {
                    Image(systemName: "play.fill")
                }
                .buttonStyle(.borderless)
            }

            Button(action: onReRecord) {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .disabled(isPlaying)
        }
        .padding(.vertical, 4)
        .padding(.horizontal, 4)
        .background(isSelected ? Color.accentColor.opacity(0.2) : Color.clear)
        .cornerRadius(4)
        .contentShape(Rectangle())
        .onTapGesture {
            onSelect()
        }
    }

    @ViewBuilder
    private var statusIcon: some View {
        switch test.status {
        case .notRun:
            Image(systemName: "circle")
                .foregroundColor(.secondary)
        case .running:
            ProgressView()
                .scaleEffect(0.5)
        case .passed:
            Image(systemName: "checkmark.circle.fill")
                .foregroundColor(.green)
        case .failed:
            Image(systemName: "xmark.circle.fill")
                .foregroundColor(.red)
        case .recording:
            Image(systemName: "record.circle")
                .foregroundColor(.red)
        }
    }
}

// MARK: - Preview

#Preview {
    TestRunnerView()
        .frame(width: 700, height: 500)
}
