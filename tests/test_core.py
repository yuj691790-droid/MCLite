from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mclite import config, net
from mclite.auth import AuthSession
from mclite.java import JavaInfo, pick_java, required_java_major
from mclite.launch import AuthData, build_command
from mclite.meta import (
    OsContext,
    Version,
    library_downloads,
    maven_path,
    merge_versions,
    rules_allow,
)


class RuleTests(unittest.TestCase):
    def test_empty_rules_allow(self):
        self.assertTrue(rules_allow(None, OsContext()))
        self.assertTrue(rules_allow([], OsContext()))

    def test_os_rules(self):
        rules = [{"action": "allow", "os": {"name": "windows"}}]
        self.assertTrue(rules_allow(rules, OsContext(name="windows")))
        self.assertFalse(rules_allow(rules, OsContext(name="linux")))

    def test_last_matching_rule_wins(self):
        rules = [
            {"action": "allow"},
            {"action": "disallow", "os": {"name": "osx"}},
        ]
        self.assertTrue(rules_allow(rules, OsContext(name="windows")))
        self.assertFalse(rules_allow(rules, OsContext(name="osx")))

    def test_feature_rules(self):
        rules = [{"action": "allow", "features": {"is_demo_user": True}}]
        self.assertFalse(rules_allow(rules, OsContext(features={})))
        self.assertTrue(rules_allow(rules, OsContext(features={"is_demo_user": True})))

    def test_arch_rule(self):
        rules = [{"action": "allow", "os": {"arch": "x86"}}]
        self.assertFalse(rules_allow(rules, OsContext(arch="x86_64")))


class MavenTests(unittest.TestCase):
    def test_path(self):
        self.assertEqual(
            maven_path("net.fabricmc:fabric-loader:0.16.0"),
            "net/fabricmc/fabric-loader/0.16.0/fabric-loader-0.16.0.jar",
        )

    def test_classifier(self):
        self.assertEqual(
            maven_path("org.lwjgl:lwjgl:3.3.1:natives-windows"),
            "org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1-natives-windows.jar",
        )

    def test_library_downloads_split(self):
        libs = [
            {
                "name": "a.b:c:1.0",
                "downloads": {"artifact": {"path": "a/b/c/1.0/c-1.0.jar", "url": "https://x/c.jar"}},
            },
            {
                "name": "n.n:n:1.0",
                "natives": {"windows": "natives-windows"},
                "downloads": {
                    "classifiers": {
                        "natives-windows": {"path": "n/n/n/1.0/n-1.0-natives-windows.jar", "url": "https://x/n.jar"}
                    }
                },
            },
            {"name": "linux-only:l:1.0", "rules": [{"action": "allow", "os": {"name": "linux"}}]},
        ]
        artifacts, natives = library_downloads(libs, OsContext())
        self.assertEqual([p for p, _ in artifacts], ["a/b/c/1.0/c-1.0.jar"])
        self.assertEqual([p for p, _ in natives], ["n/n/n/1.0/n-1.0-natives-windows.jar"])


class MergeTests(unittest.TestCase):
    def test_child_scalars_and_libraries(self):
        parent = {
            "id": "1.20.1",
            "mainClass": "net.minecraft.client.main.Main",
            "libraries": [
                {"name": "org.ow2.asm:asm:9.5"},
                {"name": "com.mojang:brigadier:1.0.18"},
            ],
            "arguments": {"game": ["--gameDir", "${game_directory}"], "jvm": ["-cp", "${classpath}"]},
        }
        child = {
            "id": "fabric-loader-0.15-1.20.1",
            "inheritsFrom": "1.20.1",
            "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
            "libraries": [
                {"name": "org.ow2.asm:asm:9.6"},
                {"name": "net.fabricmc:fabric-loader:0.15"},
            ],
            "arguments": {"game": [], "jvm": ["-DFabricMcEmu=net.minecraft.client.main.Main"]},
        }
        merged = merge_versions(parent, child)
        self.assertEqual(merged["id"], child["id"])
        self.assertEqual(merged["mainClass"], child["mainClass"])
        names = [lib["name"] for lib in merged["libraries"]]
        self.assertEqual(names, ["org.ow2.asm:asm:9.6", "com.mojang:brigadier:1.0.18", "net.fabricmc:fabric-loader:0.15"])
        self.assertEqual(merged["arguments"]["jvm"][0], "-cp")
        self.assertEqual(merged["arguments"]["jvm"][-1], "-DFabricMcEmu=net.minecraft.client.main.Main")


class ArgumentTests(unittest.TestCase):
    def test_modern_arguments_filtered(self):
        version = Version(
            id="1.20.1",
            raw={
                "arguments": {
                    "game": [
                        "--username", "${auth_player_name}",
                        {"rules": [{"action": "allow", "features": {"is_demo_user": True}}], "value": "--demo"},
                    ],
                    "jvm": ["-Djava.library.path=${natives_directory}"],
                }
            },
        )
        jvm, game = version.arguments(OsContext())
        self.assertEqual(jvm, ["-Djava.library.path=${natives_directory}"])
        self.assertNotIn("--demo", game)
        self.assertEqual(game[1], "${auth_player_name}")

    def test_legacy_arguments(self):
        version = Version(id="1.6.4", raw={"minecraftArguments": "--username ${auth_player_name} --gameDir ${game_directory}"})
        jvm, game = version.arguments(OsContext())
        self.assertEqual(jvm, [])
        self.assertEqual(game[:2], ["--username", "${auth_player_name}"])


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name)
        (self.game / "versions" / "1.20.1").mkdir(parents=True)
        (self.game / "versions" / "1.20.1" / "1.20.1.jar").write_bytes(b"jar")
        self.java = JavaInfo(path="/usr/bin/java", version="17.0.2", major=17, arch="x86_64")
        self.auth = AuthData(username="Steve", uuid="00000000-0000-0000-0000-000000000000", access_token="tok")
        self.raw = {
            "id": "1.20.1",
            "mainClass": "net.minecraft.client.main.Main",
            "assetIndex": {"id": "5"},
            "assets": "5",
            "libraries": [
                {
                    "name": "org.lwjgl:lwjgl:3.3.1",
                    "downloads": {"artifact": {"path": "org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar", "url": "https://x/l.jar"}},
                }
            ],
            "arguments": {
                "jvm": ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"],
                "game": ["--username", "${auth_player_name}", "--uuid", "${auth_uuid}", "--accessToken", "${auth_access_token}", "--gameDir", "${game_directory}"],
            },
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_command_placeholders(self):
        spec = build_command(Version(id="1.20.1", raw=self.raw), self.game, self.java, self.auth, memory_mb=1024)
        joined = spec.command
        self.assertEqual(joined[0], "/usr/bin/java")
        self.assertIn("-Xmx1024m", joined)
        self.assertIn("net.minecraft.client.main.Main", joined)
        text = " ".join(joined)
        self.assertNotIn("${", text)
        self.assertIn("--username Steve", text)
        self.assertIn("--uuid 00000000000000000000000000000000", text)
        self.assertIn(str(self.game / "libraries" / "org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar"), spec.classpath)
        self.assertIn(str(self.game / "versions" / "1.20.1" / "1.20.1.jar"), spec.classpath)

    def test_memory_not_duplicated(self):
        raw = dict(self.raw)
        raw["arguments"] = {"jvm": ["-Xmx4G", "-cp", "${classpath}"], "game": []}
        spec = build_command(Version(id="1.20.1", raw=raw), self.game, self.java, self.auth, memory_mb=1024)
        self.assertEqual([a for a in spec.command if a.startswith("-Xmx")], ["-Xmx4G"])

    def test_inherited_jar_and_main_class(self):
        child = Version(
            id="fabric-loader-0.15-1.20.1",
            raw={
                **self.raw,
                "id": "fabric-loader-0.15-1.20.1",
                "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
            },
            base_id="1.20.1",
        )
        spec = build_command(child, self.game, self.java, self.auth)
        self.assertEqual(spec.main_class, "net.fabricmc.loader.impl.launch.knot.KnotClient")
        self.assertIn(str(self.game / "versions" / "1.20.1" / "1.20.1.jar"), spec.classpath)


class JavaTests(unittest.TestCase):
    def test_required_major(self):
        self.assertEqual(required_java_major("1.20.1"), 17)
        self.assertEqual(required_java_major("1.20.6"), 21)
        self.assertEqual(required_java_major("1.16.5"), 8)
        self.assertEqual(required_java_major("1.12.2"), 8)
        self.assertEqual(required_java_major("garbage"), None)

    def test_pick_prefers_exact_then_newer(self):
        javas = [
            JavaInfo("/j8", "1.8.0_401", 8),
            JavaInfo("/j17", "17.0.2", 17),
            JavaInfo("/j21", "21.0.1", 21),
        ]
        self.assertEqual(pick_java(javas, 17).major, 17)
        self.assertEqual(pick_java(javas, 16).major, 17)
        self.assertEqual(pick_java(javas, 21).major, 21)
        self.assertEqual(pick_java([], 17), None)


class ConfigTests(unittest.TestCase):
    def test_clamp_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = config.ConfigStore(Path(tmp))
            store.settings.memory_mb = 100
            store.settings.normalized()
            self.assertEqual(store.settings.memory_mb, 512)
            store.settings.memory_mb = 99999
            store.settings.normalized()
            self.assertEqual(store.settings.memory_mb, 32768)
            store.settings.last_version = "1.20.1"
            store.save()
            self.assertEqual(config.ConfigStore(Path(tmp)).settings.last_version, "1.20.1")

    def test_default_game_dir_is_under_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = config.ConfigStore(Path(tmp))
            self.assertTrue(store.settings.game_dir)
            self.assertEqual(Path(store.game_dir).name, "game")


class NetTests(unittest.TestCase):
    def test_mirror_rewrite(self):
        self.assertEqual(
            net.mirror_url("https://libraries.minecraft.net/a/b/c.jar"),
            "https://bmclapi2.bangbang93.com/maven/a/b/c.jar",
        )
        self.assertEqual(
            net.mirror_url("https://resources.download.minecraft.net/aa/bb"),
            "https://bmclapi2.bangbang93.com/assets/aa/bb",
        )
        self.assertIsNone(net.mirror_url("https://example.com/x"))

    def test_candidates_order(self):
        self.assertTrue(net.candidate_urls("https://libraries.minecraft.net/x", True)[0].startswith("https://bmclapi2.bangbang93.com"))
        self.assertEqual(net.candidate_urls("https://libraries.minecraft.net/x", False), ["https://libraries.minecraft.net/x"])


class AuthTests(unittest.TestCase):
    def test_session_roundtrip(self):
        session = AuthSession(
            username="Steve", uuid="abc", access_token="t", refresh_token="r",
            expires_at=time.time() + 3600, client_id="cid",
        )
        clone = AuthSession.from_dict(session.to_dict())
        self.assertEqual(clone, session)
        self.assertFalse(clone.expired)

    def test_expired(self):
        session = AuthSession("a", "b", "c", "d", 0.0, "cid")
        self.assertTrue(session.expired)


if __name__ == "__main__":
    unittest.main()


class GameLogTests(unittest.TestCase):
    def test_launch_writes_game_log(self):
        import sys
        import time as _time

        from mclite.launch import LaunchSpec, launch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "game.log"
            spec = LaunchSpec(
                command=[sys.executable, "-c", "print('hello from game')"],
                cwd=root,
                natives_dir=root,
                main_class="X",
                classpath=[],
            )
            process = launch(spec, log)
            self.assertEqual(process.wait(timeout=30), 0)
            _time.sleep(0.2)
            self.assertIn("hello from game", log.read_text(encoding="utf-8", errors="replace"))
