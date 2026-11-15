// A game's options: what the player chose on its Options page (Sources/OptionsView.swift), kept where the game app reads them when it starts. One generic-password item per game in the keychain access
// group SwitchTV shares with the games (Shared/SharedStore.swift says why a keychain group), beside the recently played records but under a service of its own:
//   service dev.tvport.switchtv.options, account <TITLE ID IN CAPITALS>, value a JSON object {"DM_<NAME>": "<value>", ...}
// It holds only the choices that differ from the game's defaults, so a setting left at its default follows the defaults of the game's next build; with none left the item is deleted (Reset to defaults).
// The game reads it at boot and puts each value in its environment where neither its launch nor an env.txt set the variable (TVPort's dm/ios/SourcesTV/TVOptions.m; README, "Game options": keep them in step).
// Without the group (an unsigned build) there is no store: nothing is saved, and SwitchTV says so in its log.
import Foundation

struct OptionsStore {
    static let service = "dev.tvport.switchtv.options"
    let backing: SharedBacking

    init(backing: SharedBacking) { self.backing = backing }

    /// The store of this app's Info.plist group; nil when the plist names none.
    init?(group: String? = Bundle.main.object(forInfoDictionaryKey: SharedStore.infoKey) as? String) {
        guard let group, !group.isEmpty else { return nil }
        backing = KeychainBacking(service: Self.service, group: group)
    }

    private static func account(_ titleID: String) -> String? {
        let id = titleID.uppercased()
        return id.count == 16 && id.allSatisfy(\.isHexDigit) ? id : nil
    }

    /// The saved choices of a game, variable -> value; empty when it has none.
    func choices(_ titleID: String) -> [String: String] {
        guard let account = Self.account(titleID), let data = backing.all()[account],
              let saved = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return saved.compactMapValues { $0 as? String }
    }

    /// Keeps `value` for `setting` (forgets it when it is the default). False when the store refused, or the game does not offer the value.
    @discardableResult func choose(_ titleID: String, _ setting: GameSetting, _ value: String) -> Bool {
        guard setting.accepts(value) else { return false }
        var saved = choices(titleID)
        saved[setting.env] = setting.isDefault(value) ? nil : value
        return save(titleID, saved)
    }

    /// Forgets every choice of the game: it starts with its defaults.
    @discardableResult func reset(_ titleID: String) -> Bool { save(titleID, [:]) }

    @discardableResult func save(_ titleID: String, _ saved: [String: String]) -> Bool {
        guard let account = Self.account(titleID) else { return false }
        if saved.isEmpty { return backing.remove(account) }
        guard let data = try? JSONSerialization.data(withJSONObject: saved, options: [.sortedKeys]) else { return false }
        return backing.put(account, data)
    }
}
