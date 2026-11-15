"""The code SwitchTV and its Top Shelf extension share (Shared/): the catalogue, the shared store, a game's options and the Top Shelf plan, compiled for the Mac and run on their own.
Also that a record in the format game apps write ("recently played": one file per title ID in capitals holding the Unix time as text, README "What a game build needs") is read by the store,
and that a game's options are kept in the format the game app reads at boot (one item per title ID in capitals holding a JSON object of DM_ variable to value, README "Game options")."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SHARED = REPO / 'Shared'

CHECKS = r'''
import Foundation
func game(_ id: String, _ name: String, genres: [String] = [], card: String = "") -> String {
    """
    {"titleID":"\(id)","name":"\(name)","publisher":"Nintendo","releaseDate":"2017-03-03","numberOfPlayers":4,"genres":[\(genres.map { "\"\($0)\"" }.joined(separator: ","))],"description":"d","intro":null,
     "bundleID":"b","scheme":"switchtv-\(id.lowercased())","card":"\(card)","banner":"","screenshots":[]}
    """
}
@main struct Checks {
    static func main() throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1]), game_writer_root = URL(fileURLWithPath: CommandLine.arguments[2])
        let fm = FileManager.default
        try fm.createDirectory(at: game_writer_root, withIntermediateDirectories: true)
        try "7777".write(to: game_writer_root.appendingPathComponent("0100000000000004"), atomically: true, encoding: .utf8)
        let catalogFolder = root.appendingPathComponent("Catalog"), images = catalogFolder.appendingPathComponent("images")
        try fm.createDirectory(at: images, withIntermediateDirectories: true)
        let ids = ["0100000000000001": "Zelda", "0100000000000002": "Arms", "0100000000000003": "Mario", "0100000000000004": "Kirby"]
        for id in ids.keys.sorted() { try Data([1]).write(to: images.appendingPathComponent("\(id)-card.jpg")) }
        let list = ids.keys.sorted().map { game($0, ids[$0]!, genres: $0 <= "0100000000000002" ? ["Action", "Rare"] : ["Action"], card: "images/\($0)-card.jpg") }.joined(separator: ",")
        try "{\"generated\":\"x\",\"games\":[\(list)]}".write(to: catalogFolder.appendingPathComponent("catalog.json"), atomically: true, encoding: .utf8)

        // catalogue: A-Z, genres with at least two games, display strings
        let catalog = Catalog.load(from: catalogFolder)
        assert(catalog.games.map(\.name) == ["Arms", "Kirby", "Mario", "Zelda"])
        assert(catalog.genreRows.map(\.genre) == ["Action", "Rare"] && catalog.genreRows.map(\.games.count) == [4, 2])
        let arms = catalog.game("0100000000000002")!
        assert(arms.releaseYear == "2017" && arms.metaText == "Nintendo  \u{2022}  2017  \u{2022}  1-4 players")
        assert(arms.playURL?.absoluteString == "switchtv-0100000000000002://play")

        // the store: records, a later one wins, launchable, junk ignored
        let store = SharedStore(backing: FolderBacking(root: root.appendingPathComponent("Keychain")))
        assert(store.lastPlayed().isEmpty && store.launchable() == nil)   // never written: nil, not empty
        store.record("0100000000000001", at: Date(timeIntervalSince1970: 1000))
        store.record("0100000000000001", at: Date(timeIntervalSince1970: 500))     // older: ignored
        store.record("0100000000000003", at: Date(timeIntervalSince1970: 3000))
        store.record("not-a-title-id", at: Date())
        assert(store.lastPlayed() == ["0100000000000001": 1000, "0100000000000003": 3000])
        store.setLaunchable(["0100000000000002"])
        assert(store.launchable() == ["0100000000000002"])
        let fallback = Shelf.sections(catalog: catalog, lastPlayed: store.lastPlayed(), launchable: store.launchable(), imageFolder: catalogFolder)
        let deleted = Dictionary(uniqueKeysWithValues: fallback[1].items.map { ($0.name, $0.action.absoluteString) })
        assert(deleted["Zelda"] == "switchtv://game/0100000000000001")                // played once, but not installed now: no dead tile
        store.setLaunchable([])
        assert(store.launchable() == [])                                                // an empty set is written and read back as empty, not nil
        assert(Shelf.sections(catalog: catalog, lastPlayed: store.lastPlayed(), launchable: store.launchable(), imageFolder: catalogFolder)[1].items.allSatisfy { $0.action.scheme == "switchtv" })
        store.setLaunchable(["0100000000000002"])

        // a record in the format a game app writes (account = title ID in capitals, value = Unix time as text) is read by the same store
        let fromGame = SharedStore(backing: FolderBacking(root: game_writer_root))
        assert(fromGame.lastPlayed() == ["0100000000000004": 7777])

        // the Top Shelf plan: Recently Played (newest first) then All Games (A-Z); launchable or played = opens the game, else SwitchTV on that game
        // without a launchable record (SwitchTV has not refreshed yet) a played game counts as installed
        let noRecord = Shelf.sections(catalog: catalog, lastPlayed: store.lastPlayed(), launchable: nil, imageFolder: catalogFolder)
        assert(noRecord[1].items.first { $0.name == "Zelda" }!.action.absoluteString == "switchtv-0100000000000001://play" && noRecord[1].items.first { $0.name == "Arms" }!.action.scheme == "switchtv")
        let sections = Shelf.sections(catalog: catalog, lastPlayed: store.lastPlayed(), launchable: store.launchable(), imageFolder: catalogFolder)
        assert(sections.map(\.title) == ["Recently Played", "All Games"])
        assert(sections[0].items.map(\.name) == ["Mario", "Zelda"])
        assert(sections[1].items.map(\.name) == ["Arms", "Kirby", "Mario", "Zelda"])
        let actions = Dictionary(uniqueKeysWithValues: sections[1].items.map { ($0.name, $0.action.absoluteString) })
        assert(actions["Arms"] == "switchtv-0100000000000002://play")                  // launchable.json
        assert(actions["Zelda"] == "switchtv://game/0100000000000001")                 // played but no longer installed (not in launchable)
        assert(actions["Kirby"] == "switchtv://game/0100000000000004")                 // neither: opens SwitchTV on the game
        assert(sections[0].items[0].image?.lastPathComponent == "0100000000000003-card.jpg")
        assert(Set(sections.flatMap(\.items).map(\.identifier)).count == 6)            // identifiers are unique across sections
        assert(Shelf.titleID(from: URL(string: "switchtv://game/0100000000000004")!) == "0100000000000004")
        assert(Shelf.titleID(from: URL(string: "switchtv://game/zzz")!) == nil && Shelf.titleID(from: URL(string: "other://game/0100000000000004")!) == nil)
        assert(Shelf.titleID(from: URL(string: "switchtv://options/0400486024dec000")!, host: "options") == "0400486024DEC000" && Shelf.titleID(from: URL(string: "switchtv://options/0400486024DEC000")!) == nil)
        // nothing played: only All Games; many played: at most 8
        assert(Shelf.sections(catalog: catalog, lastPlayed: [:], launchable: [], imageFolder: catalogFolder).map(\.title) == ["All Games"])
        assert(Shelf.sections(catalog: Catalog(generated: "", games: []), lastPlayed: [:], launchable: [], imageFolder: catalogFolder).isEmpty)
        let many = (1...12).map { String(format: "01000000000000%02d", $0) }
        let big = Catalog(generated: "", games: many.map { try! JSONDecoder().decode(CatalogGame.self, from: game($0, "G\($0)").data(using: .utf8)!) })
        let bigShelf = Shelf.sections(catalog: big, lastPlayed: Dictionary(uniqueKeysWithValues: many.enumerated().map { ($1, Double($0)) }), launchable: [], imageFolder: catalogFolder)
        assert(bigShelf[0].items.count == 8 && bigShelf[0].items[0].titleID == "0100000000000012")
        // a game's Options rows (as the catalogue writes them) and the store of its choices
        let rowsJSON = """
        [{"env":"DM_RENDER_SCALE","kind":"range","label":"Render scale","default":"1.0","min":0.5,"max":1.0,"step":0.05,"group":"Picture","help":"h","base":{"docked":[1920,1080],"handheld":[1280,720]}},
         {"env":"DM_FOLIAGE","kind":"toggle","label":"Grass and ground cover","default":"on","on":"on","off":"off","group":"Game options","help":"g"},
         {"env":"DM_ANISO","kind":"choice","label":"Anisotropic filtering","default":"0","options":[["0","Auto"],["16","16x"]],"group":"Picture"},
         {"env":"DM_RUMBLE_GAIN","kind":"range","label":"Rumble strength","default":"1.0","min":0,"max":3,"step":0.1,"group":"Controls","needs":"DM_RUMBLE=1"},
         {"env":"DM_OPMODE","kind":"choice","label":"Console mode","default":"0","options":[["1","Docked"],["0","Handheld"]],"group":"Picture","setByBuild":true}]
        """
        let rows = try JSONDecoder().decode([GameSetting].self, from: Data(rowsJSON.utf8))
        let (scale, grass, aniso, gain, opmode) = (rows[0], rows[1], rows[2], rows[3], rows[4])
        assert(scale.values.count == 11 && scale.values.first == "0.50" && scale.values.last == "1.00")   // tvOS has no slider: a range is its steps
        assert(gain.values.count == 31 && gain.values[10] == "1.00" && gain.parent! == ("DM_RUMBLE", "1"))
        assert(scale.isDefault("1.00") && !scale.isDefault("0.95") && scale.accepts("0.75") && !scale.accepts("0.25") && !scale.accepts("0x1") && !scale.accepts("nan"))
        assert(scale.text("0.50") == "960 \u{d7} 540 (50 %)" && scale.text("0.50", docked: false) == "640 \u{d7} 360 (50 %)" && gain.text("1.50") == "150 %")
        assert(grass.values == ["on", "off"] && grass.text("off") == "Off" && grass.text("on") == "On" && grass.accepts("off") && !grass.accepts("0"))
        assert(aniso.text("16") == "16x" && !aniso.accepts("8") && aniso.parent == nil && opmode.fromBuild && !scale.fromBuild && opmode.isDefault("0"))

        let optionsRoot = root.appendingPathComponent("Options")
        let options = OptionsStore(backing: FolderBacking(root: optionsRoot))
        let ocarina = "0400486024DEC000"
        assert(options.choices(ocarina).isEmpty)
        assert(options.choose(ocarina, scale, "0.50") && options.choose(ocarina, grass, "off") && options.choose(ocarina, aniso, "16"))
        assert(!options.choose(ocarina, aniso, "8") && !options.choose("not-a-title-id", grass, "off"))   // a value the game does not offer; not a game
        assert(options.choices(ocarina) == ["DM_RENDER_SCALE": "0.50", "DM_FOLIAGE": "off", "DM_ANISO": "16"])
        assert(options.choose(ocarina, aniso, "0") && options.choose(ocarina, scale, "1.00"))           // back to the default: forgotten, so the game's next defaults apply
        assert(options.choose(ocarina, opmode, "1") && options.choices(ocarina)["DM_OPMODE"] == "1")    // the build chose handheld: docked is kept, and the game takes it over its dm/env.txt
        assert(options.choose(ocarina, opmode, "0"))                                                    // back to the build's value: forgotten, so the build's dm/env.txt applies
        assert(options.choices("0400486024dec000") == ["DM_FOLIAGE": "off"])                            // any letter case
        // the item the game app reads at boot (TVPort's SourcesTV/TVOptions.m): account = the title ID in capitals, value = a JSON object of strings
        let item = try JSONSerialization.jsonObject(with: Data(contentsOf: optionsRoot.appendingPathComponent(ocarina))) as! [String: Any]
        assert(item.count == 1 && item["DM_FOLIAGE"] as? String == "off")
        // one written by hand or by another tool is read the same way; a value that is not text is ignored
        try #"{"DM_MODEL_LOD":"4","DM_X":3}"#.write(to: optionsRoot.appendingPathComponent("0100000000000001"), atomically: true, encoding: .utf8)
        assert(options.choices("0100000000000001") == ["DM_MODEL_LOD": "4"])
        assert(options.reset(ocarina) && options.choices(ocarina).isEmpty && !fm.fileExists(atPath: optionsRoot.appendingPathComponent(ocarina).path))   // Reset deletes the item
        assert(options.reset(ocarina))                                                                  // nothing to forget is no failure
        assert(OptionsStore.service == "dev.tvport.switchtv.options" && OptionsStore(group: nil) == nil && OptionsStore(group: "") == nil)   // no group: no store
        assert(store.lastPlayed() == ["0100000000000001": 1000, "0100000000000003": 3000])              // options are kept apart from the recently played records

        // the catalogue: a game with a settings list has its rows, one from before options has none
        let withOptions = #"{"generated":"x","games":[{"titleID":"0400486024DEC000","name":"Ocarina","genres":[],"bundleID":"b","scheme":"switchtv-0400486024dec000","card":"","banner":"","screenshots":[],"settings":\#(rowsJSON)}]}"#
        let optionsCatalog = try JSONDecoder().decode(Catalog.self, from: Data(withOptions.utf8))
        assert(optionsCatalog.games[0].options.map(\.env) == rows.map(\.env) && catalog.game("0100000000000002")!.options.isEmpty)
        print("PASS")
    }
}
'''

@unittest.skipUnless(sys.platform == 'darwin', 'macOS Swift required')
class SwitchTVSharedCodeTests(unittest.TestCase):
    def test_catalogue_store_and_top_shelf_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'Checks.swift').write_text(CHECKS)
            subprocess.run(['swiftc', '-parse-as-library', '-Onone', '-o', str(root / 'check'), str(root / 'Checks.swift'), *map(str, sorted(SHARED.glob('*.swift')))], check=True)
            # asserts are on without -O
            result = subprocess.run([str(root / 'check'), str(root), str(root / 'game')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
