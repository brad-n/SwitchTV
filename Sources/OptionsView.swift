// A game's Options page, opened from its details: the game app's own settings (CatalogGame.options), grouped as the catalogue says (Picture, Performance, the game's own options, Controls, Language),
// in the style of the Apple TV's Settings app: the list on the right, and on the left the game and a few lines on the setting under focus. A toggle flips on Select; a choice or a range (in steps:
// tvOS has no slider) opens the list of its values. Each choice is kept at once (Shared/GameOptions.swift), and the game takes it the next time it starts.
import SwiftUI

@MainActor
final class OptionsModel: ObservableObject {
    let game: CatalogGame
    private let store: OptionsStore?
    @Published private(set) var values: [String: String] = [:]
    /// Why choices are not reaching the game, for the page; nil while they are.
    @Published private(set) var notice: String?

    static let noGroup = "This SwitchTV has no keychain group, so choices are not kept and the game starts with its defaults."

    init(game: CatalogGame, store: OptionsStore?) {
        self.game = game
        self.store = store
        let saved = store?.choices(game.titleID) ?? [:]
        for setting in game.options {   // the saved choice, unless the game no longer offers it; else the default (the build's value, where its dm/env.txt sets one)
            values[setting.env] = saved[setting.env].flatMap { setting.accepts($0) ? $0 : nil } ?? setting.def
        }
        if store == nil { notice = Self.noGroup }
    }

    func value(_ setting: GameSetting) -> String { values[setting.env] ?? setting.def }
    func text(_ setting: GameSetting, _ value: String? = nil) -> String { setting.text(value ?? self.value(setting), docked: (values["DM_OPMODE"] ?? "1") != "0") }

    /// A sub-setting shows only while the setting it needs has the value it needs (one this game lacks hides nothing).
    func visible(_ setting: GameSetting) -> Bool {
        guard let parent = setting.parent, let have = values[parent.env] else { return true }
        return have == parent.value
    }

    /// The groups and their visible rows, in the catalogue's order.
    var groups: [(title: String, rows: [GameSetting])] {
        var order: [String] = [], rows: [String: [GameSetting]] = [:]
        for setting in game.options where visible(setting) {
            if rows[setting.group] == nil { order.append(setting.group) }
            rows[setting.group, default: []].append(setting)
        }
        return order.map { ($0, rows[$0]!) }
    }

    var changed: Bool { game.options.contains { !$0.isDefault(value($0)) } }

    /// The Preset row's value: the preset the choices match, else Default or Custom; nil when the game has no presets.
    var preset: String? { game.presetName(values) }

    /// Sets what the preset names (each kept at once, as a choice on its own is); a setting it does not name that another preset does goes to its default.
    func choosePreset(_ name: String) {
        for (setting, value) in game.presetValues(name) { set(setting, value) }
        print("SwitchTV options: \(game.titleID) preset \(name)")
    }

    func set(_ setting: GameSetting, _ value: String) {
        guard setting.accepts(value) else { return }
        values[setting.env] = value
        guard let store else { print("SwitchTV options: \(game.titleID) \(setting.env)=\(value) not kept: no keychain group"); return }
        if store.choose(game.titleID, setting, value) {
            print("SwitchTV options: \(game.titleID) \(setting.env)=\(value)\(setting.isDefault(value) ? " (the default)" : "") kept")
            notice = nil
        } else {
            notice = "This choice could not be kept: the keychain refused it. The game keeps its earlier settings."
        }
    }

    func reset() {
        for setting in game.options { values[setting.env] = setting.def }
        guard let store else { return }
        if store.reset(game.titleID) { print("SwitchTV options: \(game.titleID) reset to its defaults"); notice = nil }
        else { notice = "The choices could not be forgotten: the keychain refused. The game keeps its earlier settings." }
    }
}

struct OptionsView: View {
    @StateObject private var model: OptionsModel
    @FocusState private var focused: String?

    init(game: CatalogGame, store: OptionsStore?) { _model = StateObject(wrappedValue: OptionsModel(game: game, store: store)) }

    private static let reset = "reset"
    private static let preset = "preset"
    private var game: CatalogGame { model.game }
    private var focusedSetting: GameSetting? { game.options.first { $0.env == focused } }

    var body: some View {
        ZStack(alignment: .topLeading) {
            Theme.background.ignoresSafeArea()
            if let image = Art.image(game.banner) {
                Image(uiImage: image).resizable().scaledToFill().frame(width: 1920, height: 1080).clipped().opacity(0.18).ignoresSafeArea()
            }
            Theme.split.opacity(0.9).ignoresSafeArea()
            HStack(alignment: .top, spacing: 70) {
                about.frame(width: 560, alignment: .topLeading)
                List {
                    if let name = model.preset {
                        Section {
                            NavigationLink { PresetValues(model: model) } label: { OptionRow(label: "Preset", value: name, changed: name == "Custom") }
                                .focused($focused, equals: Self.preset)
                        } footer: {
                            Text("Sets several options at once. Changing any of them afterwards shows Custom.")
                        }
                    }
                    ForEach(model.groups, id: \.title) { group in
                        Section {
                            ForEach(group.rows) { row($0) }
                        } header: {
                            Text(group.title)
                        } footer: {
                            if group.title == "Game options" { Text("\(game.name)\u{2019}s own options: less detail for a faster picture.") }
                        }
                    }
                    Section {
                        Button("Reset to Defaults") { model.reset() }
                            .disabled(!model.changed)
                            .focused($focused, equals: Self.reset)
                    }
                }
                .frame(maxWidth: .infinity)
            }
            .padding(.horizontal, Theme.pagePadding).padding(.top, 70)
        }
    }

    /// Left: the game, then what the setting under focus does.
    private var about: some View {
        VStack(alignment: .leading, spacing: 22) {
            if let image = Art.image(game.card) {
                Image(uiImage: image).resizable().scaledToFill().frame(width: 560, height: 315).clipShape(RoundedRectangle(cornerRadius: Theme.cardRadius))
            }
            Text(game.name).font(.title2.weight(.bold)).foregroundStyle(Theme.textPrimary).lineLimit(2).fixedSize(horizontal: false, vertical: true)
            Text("Options").font(.headline).foregroundStyle(Theme.accent)
            if let setting = focusedSetting {
                VStack(alignment: .leading, spacing: 10) {
                    Text(setting.label).font(.headline).foregroundStyle(Theme.textPrimary)
                    if let help = setting.help, !help.isEmpty { Text(help).font(.callout).foregroundStyle(Theme.textSecondary).fixedSize(horizontal: false, vertical: true) }
                    Text(defaultLine(setting)).font(.callout).foregroundStyle(Theme.textTertiary).fixedSize(horizontal: false, vertical: true)
                }
            } else if focused == Self.preset {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Preset").font(.headline).foregroundStyle(Theme.textPrimary)
                    Text("Performance turns off the options that cost the most GPU time, for a smoother picture. Quality adds sharper textures. Balanced is the middle. Each sets only the options it names; the rest stay as they are.")
                        .font(.callout).foregroundStyle(Theme.textSecondary).fixedSize(horizontal: false, vertical: true)
                    Text("Changing any option afterwards shows Custom.").font(.callout).foregroundStyle(Theme.textTertiary)
                }
            } else if focused == Self.reset {
                Text("Sets every option back to the game\u{2019}s defaults, the ones it was tested with.").font(.callout).foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 0)
            if let notice = model.notice {
                Text(notice).font(.callout.weight(.semibold)).foregroundStyle(Theme.accent).fixedSize(horizontal: false, vertical: true)
            }
            Text("The game takes your choices the next time it starts. If it is still open in the background, close it first.")
                .font(.footnote).foregroundStyle(Theme.textTertiary).fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxHeight: 900, alignment: .topLeading)
    }

    /// "Default: Docked", or for a setting TVPort chose when it built the game "Set by TVPort: Handheld" (the game starts with it unless a choice here says otherwise).
    private func defaultLine(_ setting: GameSetting) -> String {
        setting.fromBuild ? "Set by TVPort: \(model.text(setting, setting.def)). A choice here wins over it; Reset goes back to it." : "Default: \(model.text(setting, setting.def))"
    }

    @ViewBuilder private func row(_ setting: GameSetting) -> some View {
        let value = model.value(setting)
        if setting.kind == "toggle" {
            Button { model.set(setting, value == setting.onValue ? setting.offValue : setting.onValue) } label: {
                OptionRow(label: setting.label, value: model.text(setting), changed: !setting.isDefault(value))
            }
            .focused($focused, equals: setting.env)
        } else {
            NavigationLink { OptionValues(setting: setting, model: model) } label: {
                OptionRow(label: setting.label, value: model.text(setting), changed: !setting.isDefault(value))
            }
            .focused($focused, equals: setting.env)
        }
    }
}

/// A row: the setting's name, and its value on the right (in the accent colour when it is not the default).
private struct OptionRow: View {
    let label: String
    let value: String
    let changed: Bool
    @Environment(\.isFocused) private var isFocused
    var body: some View {
        HStack {
            Text(label)
            Spacer(minLength: 30)
            Text(value).foregroundStyle(changed && !isFocused ? Theme.accent : .secondary).lineLimit(1)
        }
    }
}

/// The values of a choice or a range, the current one ticked; Select keeps one and goes back.
private struct OptionValues: View {
    let setting: GameSetting
    @ObservedObject var model: OptionsModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            HStack(alignment: .top, spacing: 70) {
                VStack(alignment: .leading, spacing: 18) {
                    Text(setting.label).font(.title2.weight(.bold)).foregroundStyle(Theme.textPrimary)
                    if let help = setting.help, !help.isEmpty { Text(help).font(.callout).foregroundStyle(Theme.textSecondary).fixedSize(horizontal: false, vertical: true) }
                    Text(setting.fromBuild ? "Set by TVPort: \(model.text(setting, setting.def))" : "Default: \(model.text(setting, setting.def))").font(.callout).foregroundStyle(Theme.textTertiary)
                }
                .frame(width: 560, alignment: .topLeading)
                List {
                    ForEach(setting.values, id: \.self) { value in
                        Button { model.set(setting, value); dismiss() } label: {
                            HStack {
                                Text(model.text(setting, value) + (setting.isDefault(value) ? (setting.fromBuild ? "  (set by TVPort)" : "  (default)") : ""))
                                Spacer()
                                if model.value(setting) == value || (setting.kind == "range" && Double(model.value(setting)).map { abs($0 - (Double(value) ?? .nan)) < 1e-6 } == true) {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity)
            }
            .padding(.horizontal, Theme.pagePadding).padding(.top, 70)
        }
    }
}

/// The game's presets, the current one ticked; Select sets its options and goes back.
private struct PresetValues: View {
    @ObservedObject var model: OptionsModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            HStack(alignment: .top, spacing: 70) {
                VStack(alignment: .leading, spacing: 18) {
                    Text("Preset").font(.title2.weight(.bold)).foregroundStyle(Theme.textPrimary)
                    Text("Choosing one sets these options: \(model.game.presetSettings.map(\.label).joined(separator: ", ")). Other options are left alone.")
                        .font(.callout).foregroundStyle(Theme.textSecondary).fixedSize(horizontal: false, vertical: true)
                    Text("Now: \(model.preset ?? "")").font(.callout).foregroundStyle(Theme.textTertiary)
                }
                .frame(width: 560, alignment: .topLeading)
                List {
                    ForEach(model.game.presetList, id: \.name) { preset in
                        Button { model.choosePreset(preset.name); dismiss() } label: {
                            HStack {
                                Text(preset.name)
                                Spacer()
                                if model.preset == preset.name { Image(systemName: "checkmark") }
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity)
            }
            .padding(.horizontal, Theme.pagePadding).padding(.top, 70)
        }
    }
}
