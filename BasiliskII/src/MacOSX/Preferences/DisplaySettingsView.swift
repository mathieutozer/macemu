import SwiftUI

// MARK: - Display Preset Model

struct DisplayPreset: Identifiable, Hashable {
    let id = UUID()
    let name: String
    let width: Int
    let height: Int
    let category: DisplayCategory
    let era: String?

    var resolution: String {
        "\(width) × \(height)"
    }

    var aspectRatio: String {
        let gcd = gcdFunc(width, height)
        return "\(width/gcd):\(height/gcd)"
    }

    private func gcdFunc(_ a: Int, _ b: Int) -> Int {
        b == 0 ? a : gcdFunc(b, a % b)
    }
}

enum DisplayCategory: String, CaseIterable {
    case classicMac = "Classic Macintosh"
    case modernDevices = "Modern Devices"
    case custom = "Custom"
}

// MARK: - Display Presets Data

struct DisplayPresets {
    static let all: [DisplayPreset] = [
        // Classic Macintosh Monitors
        DisplayPreset(name: "Macintosh 9\" (Original)", width: 512, height: 342, category: .classicMac, era: "1984"),
        DisplayPreset(name: "Macintosh 12\" RGB", width: 512, height: 384, category: .classicMac, era: "1987"),
        DisplayPreset(name: "Apple 13\" RGB", width: 640, height: 480, category: .classicMac, era: "1989"),
        DisplayPreset(name: "Macintosh Color Classic", width: 512, height: 384, category: .classicMac, era: "1993"),
        DisplayPreset(name: "Apple Portrait Display", width: 640, height: 870, category: .classicMac, era: "1989"),
        DisplayPreset(name: "Apple Two-Page Display", width: 1152, height: 870, category: .classicMac, era: "1989"),
        DisplayPreset(name: "Apple 14\" Color Plus", width: 640, height: 480, category: .classicMac, era: "1993"),
        DisplayPreset(name: "Apple 15\" Multiscan", width: 640, height: 480, category: .classicMac, era: "1995"),
        DisplayPreset(name: "Apple 16\" Color", width: 832, height: 624, category: .classicMac, era: "1992"),
        DisplayPreset(name: "Apple 17\" Multiscan", width: 832, height: 624, category: .classicMac, era: "1993"),
        DisplayPreset(name: "Apple 20\" Color", width: 1024, height: 768, category: .classicMac, era: "1992"),
        DisplayPreset(name: "Apple 21\" Color", width: 1152, height: 870, category: .classicMac, era: "1991"),
        DisplayPreset(name: "Apple AudioVision 14\"", width: 640, height: 480, category: .classicMac, era: "1993"),
        DisplayPreset(name: "Macintosh LC 12\"", width: 512, height: 384, category: .classicMac, era: "1990"),
        DisplayPreset(name: "PowerBook 140/170", width: 640, height: 400, category: .classicMac, era: "1991"),
        DisplayPreset(name: "PowerBook Duo", width: 640, height: 480, category: .classicMac, era: "1992"),

        // Modern Devices (for fun/practical use)
        DisplayPreset(name: "iPad mini", width: 1024, height: 768, category: .modernDevices, era: nil),
        DisplayPreset(name: "iPad (Standard)", width: 1024, height: 768, category: .modernDevices, era: nil),
        DisplayPreset(name: "iPad Pro 11\"", width: 1194, height: 834, category: .modernDevices, era: nil),
        DisplayPreset(name: "iPhone (Podcast 9:16)", width: 390, height: 844, category: .modernDevices, era: nil),
        DisplayPreset(name: "iPhone Landscape", width: 844, height: 390, category: .modernDevices, era: nil),
        DisplayPreset(name: "HD 720p", width: 1280, height: 720, category: .modernDevices, era: nil),
        DisplayPreset(name: "Full HD 1080p", width: 1920, height: 1080, category: .modernDevices, era: nil),
    ]

    static func preset(for category: DisplayCategory) -> [DisplayPreset] {
        all.filter { $0.category == category }
    }
}

// MARK: - Display Settings View

struct DisplaySettingsView: View {
    @StateObject private var prefsManager = PreferencesManager.shared
    @State private var selectedPreset: DisplayPreset?
    @State private var customWidth: String = "640"
    @State private var customHeight: String = "480"
    @State private var showingCustomSize = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                // Current Resolution Display
                currentResolutionSection

                Divider()

                // Classic Macintosh Monitors
                monitorSection(title: "Classic Macintosh Monitors", category: .classicMac)

                Divider()

                // Modern Devices
                monitorSection(title: "Modern Devices", category: .modernDevices)

                Divider()

                // Custom Size
                customSizeSection
            }
            .padding(24)
        }
    }

    // MARK: - Current Resolution Section

    private var currentResolutionSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Current Display", systemImage: "display")
                .font(.headline)

            HStack(spacing: 20) {
                // Monitor Preview
                MonitorPreviewView(width: prefsManager.screenWidth, height: prefsManager.screenHeight)
                    .frame(width: 120, height: 100)

                VStack(alignment: .leading, spacing: 8) {
                    Text("\(prefsManager.screenWidth) × \(prefsManager.screenHeight)")
                        .font(.title2)
                        .fontWeight(.medium)

                    if let preset = findMatchingPreset() {
                        Text(preset.name)
                            .foregroundColor(.secondary)
                    } else {
                        Text("Custom Resolution")
                            .foregroundColor(.secondary)
                    }

                    Text("Changes require restart")
                        .font(.caption)
                        .foregroundColor(.orange)
                }
            }
            .padding()
            .background(Color(nsColor: .controlBackgroundColor))
            .cornerRadius(8)
        }
    }

    // MARK: - Monitor Section

    private func monitorSection(title: String, category: DisplayCategory) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.headline)

            LazyVGrid(columns: [
                GridItem(.adaptive(minimum: 200, maximum: 250), spacing: 12)
            ], spacing: 12) {
                ForEach(DisplayPresets.preset(for: category)) { preset in
                    MonitorPresetCard(
                        preset: preset,
                        isSelected: prefsManager.screenWidth == preset.width && prefsManager.screenHeight == preset.height
                    ) {
                        selectPreset(preset)
                    }
                }
            }
        }
    }

    // MARK: - Custom Size Section

    private var customSizeSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Custom Size")
                .font(.headline)

            HStack(spacing: 16) {
                VStack(alignment: .leading) {
                    Text("Width")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    TextField("Width", text: $customWidth)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 100)
                }

                Text("×")
                    .font(.title2)
                    .foregroundColor(.secondary)

                VStack(alignment: .leading) {
                    Text("Height")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    TextField("Height", text: $customHeight)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 100)
                }

                Button("Apply") {
                    applyCustomSize()
                }
                .buttonStyle(.borderedProminent)
            }
            .padding()
            .background(Color(nsColor: .controlBackgroundColor))
            .cornerRadius(8)
        }
    }

    // MARK: - Helper Methods

    private func findMatchingPreset() -> DisplayPreset? {
        DisplayPresets.all.first {
            $0.width == prefsManager.screenWidth && $0.height == prefsManager.screenHeight
        }
    }

    private func selectPreset(_ preset: DisplayPreset) {
        prefsManager.setScreenSize(width: preset.width, height: preset.height)
    }

    private func applyCustomSize() {
        guard let width = Int(customWidth), let height = Int(customHeight),
              width > 0, height > 0, width <= 4096, height <= 4096 else {
            return
        }
        prefsManager.setScreenSize(width: width, height: height)
    }
}

// MARK: - Monitor Preset Card

struct MonitorPresetCard: View {
    let preset: DisplayPreset
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 8) {
                // Monitor icon
                MonitorPreviewView(width: preset.width, height: preset.height)
                    .frame(width: 60, height: 50)

                VStack(spacing: 2) {
                    Text(preset.name)
                        .font(.caption)
                        .fontWeight(.medium)
                        .lineLimit(1)

                    Text(preset.resolution)
                        .font(.caption2)
                        .foregroundColor(.secondary)

                    if let era = preset.era {
                        Text(era)
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
            }
            .frame(maxWidth: .infinity)
            .padding(12)
            .background(isSelected ? Color.accentColor.opacity(0.15) : Color(nsColor: .controlBackgroundColor))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(isSelected ? Color.accentColor : Color.clear, lineWidth: 2)
            )
            .cornerRadius(8)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Monitor Preview View

struct MonitorPreviewView: View {
    let width: Int
    let height: Int

    var body: some View {
        GeometryReader { geometry in
            let sizes = computePreviewSize(geometry: geometry)
            ZStack {
                // Monitor bezel
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color(nsColor: .darkGray))
                    .frame(width: sizes.width + 8, height: sizes.height + 8)

                // Screen
                RoundedRectangle(cornerRadius: 2)
                    .fill(
                        LinearGradient(
                            colors: [Color(nsColor: .systemTeal).opacity(0.3), Color(nsColor: .systemBlue).opacity(0.3)],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: sizes.width, height: sizes.height)

                // Stand
                MonitorStand(geometry: geometry, previewHeight: sizes.height)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func computePreviewSize(geometry: GeometryProxy) -> (width: CGFloat, height: CGFloat) {
        let aspectRatio = CGFloat(width) / CGFloat(height)
        let maxWidth = geometry.size.width * 0.8
        let maxHeight = geometry.size.height * 0.8

        if aspectRatio > 1 {
            let previewWidth = min(maxWidth, maxHeight * aspectRatio)
            let previewHeight = previewWidth / aspectRatio
            return (previewWidth, previewHeight)
        } else {
            let previewHeight = min(maxHeight, maxWidth / aspectRatio)
            let previewWidth = previewHeight * aspectRatio
            return (previewWidth, previewHeight)
        }
    }
}

// MARK: - Monitor Stand Shape

struct MonitorStand: View {
    let geometry: GeometryProxy
    let previewHeight: CGFloat

    var body: some View {
        Path { path in
            let standWidth: CGFloat = 20
            let standHeight: CGFloat = 8
            let centerX = geometry.size.width / 2
            let bottomY = (geometry.size.height + previewHeight) / 2 + 4

            path.move(to: CGPoint(x: centerX - standWidth/2, y: bottomY))
            path.addLine(to: CGPoint(x: centerX + standWidth/2, y: bottomY))
            path.addLine(to: CGPoint(x: centerX + standWidth/3, y: bottomY + standHeight))
            path.addLine(to: CGPoint(x: centerX - standWidth/3, y: bottomY + standHeight))
            path.closeSubpath()
        }
        .fill(Color(nsColor: .darkGray))
    }
}

// MARK: - Preview

#Preview {
    DisplaySettingsView()
        .frame(width: 600, height: 700)
}
