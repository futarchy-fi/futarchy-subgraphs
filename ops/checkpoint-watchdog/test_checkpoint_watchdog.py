import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import checkpoint_watchdog as watchdog


class CheckpointWatchdogTests(unittest.TestCase):
    def test_parse_progress_requires_unique_numeric_rows(self):
        self.assertEqual(
            watchdog.parse_progress("gnosis|123\nmainnet|456\n"),
            {"gnosis": 123, "mainnet": 456},
        )
        for invalid in ("", "gnosis|nope\n", "gnosis|1\ngnosis|2\n"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(RuntimeError):
                    watchdog.parse_progress(invalid)

    def test_evaluate_stales_each_indexer_independently(self):
        previous = {
            "candles/gnosis": {
                "block": 100,
                "high_water": 100,
                "changed_at": 10,
                "observed_at": 90,
            },
            "candles/mainnet": {
                "block": 200,
                "high_water": 200,
                "changed_at": 90,
                "observed_at": 90,
            },
        }
        updated, stale = watchdog.evaluate(
            previous,
            "candles",
            {"gnosis": 100, "mainnet": 201},
            now=100,
            stale_seconds=60,
            deep_rewind_blocks=100,
        )
        self.assertEqual(stale, [("gnosis", 100, 100, 90)])
        self.assertEqual(updated["candles/gnosis"]["changed_at"], 10)
        self.assertEqual(updated["candles/mainnet"]["changed_at"], 100)

    def test_reorg_oscillation_does_not_reset_high_water_clock(self):
        previous = {
            "registry/gnosis": {
                "block": 100,
                "high_water": 101,
                "changed_at": 10,
                "observed_at": 90,
            }
        }
        updated, stale = watchdog.evaluate(
            previous,
            "registry",
            {"gnosis": 101},
            now=100,
            stale_seconds=60,
            deep_rewind_blocks=100,
        )
        self.assertEqual(stale, [("gnosis", 101, 101, 90)])
        self.assertEqual(updated["registry/gnosis"]["high_water"], 101)
        self.assertEqual(updated["registry/gnosis"]["changed_at"], 10)

    def test_deep_rewind_and_observation_gap_reset_the_baseline(self):
        previous = {
            "registry/gnosis": {
                "block": 1_000,
                "high_water": 1_000,
                "changed_at": 10,
                "observed_at": 90,
            }
        }
        rewound, stale = watchdog.evaluate(
            previous, "registry", {"gnosis": 800}, 100, 60, 100
        )
        self.assertEqual(stale, [])
        self.assertEqual(rewound["registry/gnosis"]["high_water"], 800)

        recovered, stale = watchdog.evaluate(
            previous, "registry", {"gnosis": 1_000}, 200, 60, 100
        )
        self.assertEqual(stale, [])
        self.assertEqual(recovered["registry/gnosis"]["changed_at"], 200)

    def test_state_round_trip_is_private_and_versioned(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            expected = {
                "registry/gnosis": {
                    "block": 123,
                    "high_water": 123,
                    "changed_at": 45,
                    "observed_at": 45,
                }
            }
            watchdog.save_state(path, expected)
            self.assertEqual(watchdog.load_state(path), expected)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)

            path.write_text('{"version": 1, "sources": {"bad": {}}}\n')
            with self.assertRaisesRegex(RuntimeError, "invalid watchdog state"):
                watchdog.load_state(path)

    def test_read_progress_uses_indexed_raw_block_query(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "gnosis|123\n", "")

        result = watchdog.read_progress(watchdog.TARGETS[0], run=run)
        self.assertEqual(result, {"gnosis": 123})
        self.assertEqual(calls[0][:3], ["docker", "exec", "futarchy-registry-postgres"])
        self.assertIn("_blocks", calls[0][-1])
        self.assertIn("ORDER BY block_number DESC LIMIT 1", calls[0][-1])

    def test_read_progress_rejects_missing_expected_indexer(self):
        target = {**watchdog.TARGETS[0], "indexers": {"gnosis": "x", "mainnet": "y"}}

        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "gnosis|123\n", "")

        with self.assertRaisesRegex(RuntimeError, "missing expected progress"):
            watchdog.read_progress(target, run=run)

    def test_chain_head_parser(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"jsonrpc":"2.0","id":1,"result":"0x64"}'

        self.assertEqual(watchdog.read_chain_head("https://example", lambda *a, **k: Response()), 100)

    def stale_state(self, target="registry"):
        return {
            f"{target}/gnosis": {
                "block": 100,
                "high_water": 100,
                "changed_at": 100,
                "observed_at": 999,
            }
        }

    def test_main_persists_cooldown_before_restart(self):
        target = watchdog.TARGETS[0]
        events = []
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(watchdog, "TARGETS", (target,)), \
                mock.patch.object(watchdog.time, "time", return_value=1_000), \
                mock.patch.object(watchdog, "load_state", return_value=self.stale_state()), \
                mock.patch.object(watchdog, "read_progress", return_value={"gnosis": 100}), \
                mock.patch.object(watchdog, "container_is_running", return_value=True), \
                mock.patch.object(watchdog, "read_chain_head", return_value=300), \
                mock.patch.object(watchdog, "save_state", side_effect=lambda *a: events.append("save")), \
                mock.patch.object(watchdog, "restart_container", side_effect=lambda *a: events.append("restart")):
            result = watchdog.main(["--state", str(Path(temp) / "state.json")])

        self.assertEqual(result, 0)
        self.assertEqual(events[:2], ["save", "restart"])

    def test_main_refuses_restart_when_cooldown_cannot_be_saved(self):
        target = watchdog.TARGETS[0]
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(watchdog, "TARGETS", (target,)), \
                mock.patch.object(watchdog.time, "time", return_value=1_000), \
                mock.patch.object(watchdog, "load_state", return_value=self.stale_state()), \
                mock.patch.object(watchdog, "read_progress", return_value={"gnosis": 100}), \
                mock.patch.object(watchdog, "container_is_running", return_value=True), \
                mock.patch.object(watchdog, "read_chain_head", return_value=300), \
                mock.patch.object(watchdog, "save_state", side_effect=OSError("read-only")), \
                mock.patch.object(watchdog, "restart_container") as restart:
            result = watchdog.main(["--state", str(Path(temp) / "state.json")])

        self.assertEqual(result, 1)
        restart.assert_not_called()

    def test_main_never_starts_a_stopped_container(self):
        target = watchdog.TARGETS[0]
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(watchdog, "TARGETS", (target,)), \
                mock.patch.object(watchdog.time, "time", return_value=1_000), \
                mock.patch.object(watchdog, "load_state", return_value=self.stale_state()), \
                mock.patch.object(watchdog, "read_progress", return_value={"gnosis": 100}), \
                mock.patch.object(watchdog, "container_is_running", return_value=False), \
                mock.patch.object(watchdog, "read_chain_head") as read_head, \
                mock.patch.object(watchdog, "restart_container") as restart:
            result = watchdog.main(["--state", str(Path(temp) / "state.json")])

        self.assertEqual(result, 1)
        read_head.assert_not_called()
        restart.assert_not_called()

    def test_main_honors_restart_cooldown(self):
        target = watchdog.TARGETS[0]
        state = self.stale_state()
        state["registry/gnosis"]["restart_attempted_at"] = 900
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(watchdog, "TARGETS", (target,)), \
                mock.patch.object(watchdog.time, "time", return_value=1_000), \
                mock.patch.object(watchdog, "load_state", return_value=state), \
                mock.patch.object(watchdog, "read_progress", return_value={"gnosis": 100}), \
                mock.patch.object(watchdog, "container_is_running", return_value=True), \
                mock.patch.object(watchdog, "read_chain_head") as read_head, \
                mock.patch.object(watchdog, "restart_container") as restart:
            result = watchdog.main(["--state", str(Path(temp) / "state.json")])

        self.assertEqual(result, 0)
        read_head.assert_not_called()
        restart.assert_not_called()

    def test_main_requires_material_chain_lag(self):
        target = watchdog.TARGETS[0]
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(watchdog, "TARGETS", (target,)), \
                mock.patch.object(watchdog.time, "time", return_value=1_000), \
                mock.patch.object(watchdog, "load_state", return_value=self.stale_state()), \
                mock.patch.object(watchdog, "read_progress", return_value={"gnosis": 100}), \
                mock.patch.object(watchdog, "container_is_running", return_value=True), \
                mock.patch.object(watchdog, "read_chain_head", return_value=150), \
                mock.patch.object(watchdog, "restart_container") as restart:
            result = watchdog.main(["--state", str(Path(temp) / "state.json")])

        self.assertEqual(result, 0)
        restart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
