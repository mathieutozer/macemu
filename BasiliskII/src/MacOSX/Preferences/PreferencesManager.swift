import Foundation
import Combine

// MARK: - Preferences Manager

class PreferencesManager: ObservableObject {
    static let shared = PreferencesManager()

    // MARK: - Published Properties

    @Published var screenWidth: Int = 640
    @Published var screenHeight: Int = 870
    @Published var screenDepth: Int = 32

    @Published var ramSize: Int = 64 * 1024 * 1024  // 64 MB default
    @Published var romPath: String = ""
    @Published var diskImages: [String] = []
    @Published var sharedFolderPath: String = ""

    // MARK: - Preferences File Path

    private var prefsFilePath: String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/.basilisk_ii_prefs"
    }

    // MARK: - Initialization

    private init() {
        loadPreferences()
    }

    // MARK: - Load Preferences

    func loadPreferences() {
        guard FileManager.default.fileExists(atPath: prefsFilePath) else {
            print("PreferencesManager: No prefs file found at \(prefsFilePath)")
            return
        }

        do {
            let content = try String(contentsOfFile: prefsFilePath, encoding: .utf8)
            parsePreferences(content)
        } catch {
            print("PreferencesManager: Error reading prefs file: \(error)")
        }
    }

    private func parsePreferences(_ content: String) {
        let lines = content.components(separatedBy: .newlines)

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty, !trimmed.hasPrefix("#") else { continue }

            // Split on first space
            if let spaceIndex = trimmed.firstIndex(of: " ") {
                let key = String(trimmed[..<spaceIndex])
                let value = String(trimmed[trimmed.index(after: spaceIndex)...]).trimmingCharacters(in: .whitespaces)

                switch key {
                case "screen":
                    parseScreenValue(value)
                case "ramsize":
                    if let ram = Int(value) {
                        ramSize = ram
                    }
                case "rom":
                    romPath = value
                case "disk":
                    diskImages.append(value)
                case "extfs":
                    sharedFolderPath = value
                default:
                    break
                }
            }
        }
    }

    private func parseScreenValue(_ value: String) {
        // Format: "win/WIDTH/HEIGHT" or "win/WIDTH/HEIGHT/DEPTH"
        let components = value.components(separatedBy: "/")
        guard components.count >= 3, components[0] == "win" else { return }

        if let width = Int(components[1]), let height = Int(components[2]) {
            screenWidth = width
            screenHeight = height

            if components.count >= 4, let depth = Int(components[3]) {
                screenDepth = depth
            }
        }
    }

    // MARK: - Save Preferences

    func savePreferences() {
        guard FileManager.default.fileExists(atPath: prefsFilePath) else {
            print("PreferencesManager: Prefs file doesn't exist, cannot save")
            return
        }

        do {
            var content = try String(contentsOfFile: prefsFilePath, encoding: .utf8)
            content = updatePreference(in: content, key: "screen", value: "win/\(screenWidth)/\(screenHeight)")
            try content.write(toFile: prefsFilePath, atomically: true, encoding: .utf8)
            print("PreferencesManager: Saved preferences")
        } catch {
            print("PreferencesManager: Error saving prefs: \(error)")
        }
    }

    private func updatePreference(in content: String, key: String, value: String) -> String {
        var lines = content.components(separatedBy: .newlines)
        var found = false

        for i in 0..<lines.count {
            let trimmed = lines[i].trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("\(key) ") || trimmed == key {
                lines[i] = "\(key) \(value)"
                found = true
                break
            }
        }

        if !found {
            // Add the preference if it doesn't exist
            lines.append("\(key) \(value)")
        }

        return lines.joined(separator: "\n")
    }

    // MARK: - Convenience Methods

    func setScreenSize(width: Int, height: Int) {
        screenWidth = width
        screenHeight = height
        savePreferences()
    }

    func setScreenSize(width: Int, height: Int, depth: Int) {
        screenWidth = width
        screenHeight = height
        screenDepth = depth
        savePreferences()
    }

    // MARK: - Format Helpers

    var screenSizeString: String {
        "win/\(screenWidth)/\(screenHeight)"
    }

    var ramSizeInMB: Int {
        ramSize / (1024 * 1024)
    }
}
