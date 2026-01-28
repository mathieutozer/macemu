import SwiftUI

// MARK: - Preferences Categories

enum PreferencesCategory: String, CaseIterable, Identifiable {
    case display = "Display"
    case storage = "Storage"
    case memory = "Memory"
    case network = "Network"
    case input = "Input"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .display: return "display"
        case .storage: return "internaldrive"
        case .memory: return "memorychip"
        case .network: return "network"
        case .input: return "keyboard"
        }
    }
}

// MARK: - Main Preferences View

struct PreferencesView: View {
    @State private var selectedCategory: PreferencesCategory = .display
    @StateObject private var prefsManager = PreferencesManager.shared

    var body: some View {
        NavigationSplitView {
            List(PreferencesCategory.allCases, selection: $selectedCategory) { category in
                Label(category.rawValue, systemImage: category.icon)
                    .tag(category)
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 250)
        } detail: {
            detailView(for: selectedCategory)
                .frame(minWidth: 500, minHeight: 400)
        }
        .navigationTitle(selectedCategory.rawValue)
        .frame(minWidth: 700, minHeight: 500)
    }

    @ViewBuilder
    private func detailView(for category: PreferencesCategory) -> some View {
        switch category {
        case .display:
            DisplaySettingsView()
        case .storage:
            PlaceholderSettingsView(title: "Storage", description: "Disk and CD-ROM settings will appear here.")
        case .memory:
            PlaceholderSettingsView(title: "Memory", description: "RAM and ROM settings will appear here.")
        case .network:
            PlaceholderSettingsView(title: "Network", description: "Ethernet and serial port settings will appear here.")
        case .input:
            PlaceholderSettingsView(title: "Input", description: "Keyboard and mouse settings will appear here.")
        }
    }
}

// MARK: - Placeholder View for unimplemented categories

struct PlaceholderSettingsView: View {
    let title: String
    let description: String

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "gearshape")
                .font(.system(size: 64))
                .foregroundColor(.secondary)
            Text(title)
                .font(.title)
            Text(description)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Preview

#Preview {
    PreferencesView()
}
