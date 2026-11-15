// The games SwitchTV lists: Catalog/catalog.json and its images, written by `backend.py switchtv build` into the app and into its Top Shelf extension. Nothing is fetched at run time except screenshots.
// Foundation only: SwitchTV, the Top Shelf extension and the command-line checks (tests/test_shared_code.py) all compile this file.
import Foundation

struct CatalogGame: Codable, Identifiable, Hashable {
    let titleID: String
    let name: String
    let publisher: String?
    let releaseDate: String?     // yyyy-MM-dd
    let numberOfPlayers: Int?
    let genres: [String]
    let summary: String?
    let intro: String?
    let bundleID: String
    let scheme: String           // switchtv-<title ID in lowercase>
    let card: String             // image paths, relative to Catalog
    let banner: String
    let screenshots: [String]    // URLs
    let settings: [GameSetting]? // its Options page; absent in a catalogue from before options

    var id: String { titleID }
    var playURL: URL? { URL(string: "\(scheme)://play") }

    enum CodingKeys: String, CodingKey {
        case titleID, name, publisher, releaseDate, numberOfPlayers, genres, summary = "description", intro, bundleID, scheme, card, banner, screenshots, settings
    }

    /// The rows of its Options page, in the order to show them; empty when the game app has no settings list.
    var options: [GameSetting] { settings ?? [] }

    var releaseYear: String? {
        guard let releaseDate, releaseDate.count >= 4, releaseDate.prefix(4).allSatisfy(\.isNumber) else { return nil }
        return String(releaseDate.prefix(4))
    }

    var releaseText: String? {
        guard let releaseDate else { return nil }
        let parse = DateFormatter(); parse.dateFormat = "yyyy-MM-dd"; parse.locale = Locale(identifier: "en_US_POSIX")
        guard let date = parse.date(from: releaseDate) else { return nil }
        return date.formatted(date: .long, time: .omitted)
    }

    var playersText: String? {
        guard let n = numberOfPlayers, n > 0 else { return nil }
        return n == 1 ? "1 player" : "1-\(n) players"
    }

    /// The text under the title: publisher, year, players.
    var metaText: String { [publisher, releaseYear, playersText].compactMap { $0 }.joined(separator: "  \u{2022}  ") }

    /// The description, as long as the shop gave it.
    var blurb: String? { summary ?? intro }
}

/// One setting of a game's Options page: a row of the game app's own settings list (TVPort's dm/launcher.json; switchtv_cli/catalog.py tv_settings adds its group and help line). Its value is the text the
/// game puts in its environment variable `env` when it starts (Shared/GameOptions.swift). The checks agree with the game's launcher (TVPort's dm/ios/Sources/LauncherSettings.swift) and its boot (TVOptions.m).
struct GameSetting: Codable, Hashable, Identifiable {
    let env, kind, label, def: String
    let group: String
    let help: String?
    let options: [[String]]?     // choice: [value, label]
    let min, max, step: Double?  // range
    let on, off: String?         // toggle: the values the variable takes (1 / 0 when not given)
    let needs: String?           // "<VAR>=<value>": shown only while that setting has that value
    let base: Base?              // render scale: the 3D picture's size at 1.0, docked and handheld
    let setByBuild: Bool?        // `def` is the value the game app's own dm/env.txt sets (TVPort's build choice), which the game starts with when nothing is chosen here
    struct Base: Codable, Hashable { let docked, handheld: [Int] }

    var id: String { env }
    var fromBuild: Bool { setByBuild ?? false }
    var onValue: String { on ?? "1" }
    var offValue: String { off ?? "0" }

    enum CodingKeys: String, CodingKey { case env, kind, label, def = "default", group, help, options, min, max, step, on, off, needs, base, setByBuild }

    /// A range compares as numbers (its steps are written with two decimals: 1.00 is the default 1.0).
    func isDefault(_ value: String) -> Bool {
        kind == "range" ? abs((Double(value) ?? .nan) - (Double(def) ?? .nan)) < 1e-6 : value == def
    }

    /// A value the game offers (a saved choice the game no longer offers is dropped).
    func accepts(_ value: String) -> Bool {
        switch kind {
        case "choice": return options?.contains { $0.first == value } ?? false
        case "range":
            guard !value.contains(where: { $0 == "x" || $0 == "X" }), let n = Double(value), n.isFinite, let lo = min, let hi = max else { return false }
            return n >= lo - 1e-9 && n <= hi + 1e-9
        case "toggle": return value == onValue || value == offValue
        default: return false
        }
    }

    /// The values the page offers, in order: a choice's options, a toggle's on and off, a range's steps (tvOS has no slider).
    var values: [String] {
        switch kind {
        case "choice": return (options ?? []).compactMap(\.first)
        case "toggle": return [onValue, offValue]
        case "range":
            guard let lo = min, let hi = max, let step, step > 0 else { return [def] }
            let count = Int(((hi - lo) / step).rounded(.down) + 1e-6)
            return (0...Swift.max(count, 0)).map { String(format: "%.2f", lo + Double($0) * step) }
        default: return [def]
        }
    }

    /// What a value is called on screen. A range is a percentage, with the picture's size in pixels for the console mode when the list has it (`docked`: DM_OPMODE is not 0).
    func text(_ value: String, docked: Bool = true) -> String {
        switch kind {
        case "choice": return options?.first { $0.first == value }.map { $0.count > 1 ? $0[1] : $0[0] } ?? value
        case "toggle": return value == onValue ? "On" : "Off"
        case "range":
            guard let n = Double(value) else { return value }
            let percent = "\(Int((n * 100).rounded())) %"
            guard let size = base.map({ docked ? $0.docked : $0.handheld }), size.count == 2 else { return percent }
            return "\(Int((Double(size[0]) * n).rounded())) \u{d7} \(Int((Double(size[1]) * n).rounded())) (\(percent))"
        default: return value
        }
    }

    /// The setting `needs` names and the value it must have; nil for a setting that is always shown.
    var parent: (env: String, value: String)? {
        guard let needs, let eq = needs.firstIndex(of: "=") else { return nil }
        return (String(needs[..<eq]), String(needs[needs.index(after: eq)...]))
    }
}

struct Catalog: Codable {
    let generated: String
    let games: [CatalogGame]

    static var folder: URL { Bundle.main.resourceURL!.appendingPathComponent("Catalog", isDirectory: true) }

    /// A-Z by name.
    static func load(from folder: URL = Catalog.folder) -> Catalog {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("catalog.json")),
              let catalog = try? JSONDecoder().decode(Catalog.self, from: data) else { return Catalog(generated: "", games: []) }
        return Catalog(generated: catalog.generated, games: catalog.games.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending })
    }

    func game(_ titleID: String) -> CatalogGame? { games.first { $0.titleID.caseInsensitiveCompare(titleID) == .orderedSame } }

    /// Genres that have at least two games, most games first.
    var genreRows: [(genre: String, games: [CatalogGame])] {
        var byGenre: [String: [CatalogGame]] = [:]
        for game in games { for genre in game.genres { byGenre[genre, default: []].append(game) } }
        return byGenre.filter { $0.value.count >= 2 }.sorted { ($0.value.count, $1.key) > ($1.value.count, $0.key) }.map { ($0.key, $0.value) }
    }
}
