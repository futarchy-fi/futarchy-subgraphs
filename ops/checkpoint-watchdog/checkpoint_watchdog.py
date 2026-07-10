#!/usr/bin/env python3
"""Restart a Checkpoint indexer after sustained block-progress staleness."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


TARGETS = (
    {
        "name": "registry",
        "db_container": "futarchy-registry-postgres",
        "database": "checkpoint_registry",
        "indexer_container": "futarchy-registry-checkpoint",
        "indexers": {"gnosis": "https://rpc.gnosischain.com"},
    },
    {
        "name": "candles",
        "db_container": "futarchy-candles-postgres-1",
        "database": "checkpoint_candles",
        "indexer_container": "futarchy-candles-checkpoint-1",
        "indexers": {"gnosis": "https://rpc.gnosischain.com"},
    },
)
DEFAULT_STATE = Path("/var/lib/futarchy-indexer-watchdog/state.json")
DEFAULT_STALE_SECONDS = 10 * 60
DEFAULT_MIN_LAG_BLOCKS = 100
DEFAULT_RESTART_COOLDOWN_SECONDS = 60 * 60
DEFAULT_DEEP_REWIND_BLOCKS = 100


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parse_progress(output):
    progress = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            indexer, raw_block = line.split("|", 1)
            block = int(raw_block)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid progress row: {line!r}") from exc
        if not indexer or block < 0 or indexer in progress:
            raise RuntimeError(f"invalid progress row: {line!r}")
        progress[indexer] = block
    if not progress:
        raise RuntimeError("no last_indexed_block rows")
    return progress


def progress_query(indexers):
    if not indexers or any(not name.replace("_", "").isalnum() for name in indexers):
        raise RuntimeError("invalid expected indexer set")
    values = ", ".join(f"('{name}')" for name in sorted(indexers))
    return (
        "SELECT wanted.indexer || '|' || latest.block_number "
        f"FROM (VALUES {values}) AS wanted(indexer) "
        "CROSS JOIN LATERAL ("
        "SELECT block_number FROM _blocks WHERE indexer = wanted.indexer "
        "ORDER BY block_number DESC LIMIT 1"
        ") AS latest ORDER BY wanted.indexer;"
    )


def read_progress(target, run=subprocess.run):
    query = progress_query(target["indexers"])
    command = [
        "docker", "exec", target["db_container"],
        "psql", "-XAtq", "-v", "ON_ERROR_STOP=1",
        "-U", "checkpoint", "-d", target["database"], "-c", query,
    ]
    try:
        result = run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"progress query failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or ["unknown docker/psql error"]
        raise RuntimeError(detail[0])
    progress = parse_progress(result.stdout)
    expected = set(target["indexers"])
    if set(progress) != expected:
        missing = ", ".join(sorted(expected - set(progress))) or "none"
        raise RuntimeError(f"missing expected progress rows: {missing}")
    return progress


def read_chain_head(url, opener=urllib.request.urlopen):
    request = urllib.request.Request(
        url,
        data=b'{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=10) as response:
            payload = json.loads(response.read())
        head = int(payload["result"], 16)
    except (
        OSError,
        TimeoutError,
        TypeError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ) as exc:
        raise RuntimeError("chain head unavailable") from exc
    if head < 0:
        raise RuntimeError("invalid chain head")
    return head


def container_is_running(container, run=subprocess.run):
    try:
        result = run(
            ["docker", "inspect", "--format={{.State.Running}}", container],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("container state unavailable") from exc
    if result.returncode != 0:
        raise RuntimeError("container state unavailable")
    return result.stdout.strip() == "true"


def evaluate(previous, target_name, progress, now, stale_seconds, deep_rewind_blocks):
    updated = dict(previous)
    stale = []
    for indexer, block in progress.items():
        key = f"{target_name}/{indexer}"
        prior = previous.get(key)
        prior_high_water = None if prior is None else prior.get("high_water", prior.get("block"))
        observation_gap = 0 if prior is None else now - int(prior.get("observed_at", 0))
        deep_rewind = (
            prior_high_water is not None
            and int(prior_high_water) - block >= deep_rewind_blocks
        )
        if prior_high_water is None or observation_gap > stale_seconds or deep_rewind:
            changed_at = now
            high_water = block
        elif block > prior_high_water:
            changed_at = now
            high_water = block
        else:
            changed_at = int(prior.get("changed_at", now))
            high_water = int(prior_high_water)
        age = max(0, now - changed_at)
        updated[key] = {
            "block": block,
            "high_water": high_water,
            "changed_at": changed_at,
            "observed_at": now,
        }
        if prior is not None and "restart_attempted_at" in prior:
            updated[key]["restart_attempted_at"] = int(prior["restart_attempted_at"])
        if age >= stale_seconds:
            stale.append((indexer, block, high_water, age))
    return updated, stale


def load_state(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if data.get("version") != 1 or not isinstance(data.get("sources"), dict):
        raise RuntimeError("unsupported watchdog state")
    sources = data["sources"]
    for key, source in sources.items():
        if not isinstance(key, str) or not isinstance(source, dict):
            raise RuntimeError("invalid watchdog state")
        values = [source.get(field) for field in ("block", "high_water", "changed_at", "observed_at")]
        if (
            any(not isinstance(value, int) or value < 0 for value in values)
            or (
                "restart_attempted_at" in source
                and (
                    not isinstance(source["restart_attempted_at"], int)
                    or source["restart_attempted_at"] < 0
                )
            )
        ):
            raise RuntimeError("invalid watchdog state")
    return sources


def save_state(path, sources):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    payload = json.dumps({"version": 1, "sources": sources}, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix="state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o640)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restart_container(container, run=subprocess.run):
    try:
        result = run(
            ["docker", "restart", container],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"container restart failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or ["unknown docker error"]
        raise RuntimeError(detail[0])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path(os.environ.get("WATCHDOG_STATE_PATH", DEFAULT_STATE)),
    )
    parser.add_argument(
        "--stale-seconds",
        type=positive_int,
        default=positive_int(os.environ.get("WATCHDOG_STALE_SECONDS", DEFAULT_STALE_SECONDS)),
    )
    parser.add_argument(
        "--min-lag-blocks",
        type=positive_int,
        default=positive_int(os.environ.get("WATCHDOG_MIN_LAG_BLOCKS", DEFAULT_MIN_LAG_BLOCKS)),
    )
    parser.add_argument(
        "--restart-cooldown-seconds",
        type=positive_int,
        default=positive_int(
            os.environ.get(
                "WATCHDOG_RESTART_COOLDOWN_SECONDS",
                DEFAULT_RESTART_COOLDOWN_SECONDS,
            )
        ),
    )
    parser.add_argument(
        "--deep-rewind-blocks",
        type=positive_int,
        default=positive_int(
            os.environ.get("WATCHDOG_DEEP_REWIND_BLOCKS", DEFAULT_DEEP_REWIND_BLOCKS)
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    now = int(time.time())
    try:
        sources = load_state(args.state)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"watchdog: cannot load state: {exc}", file=sys.stderr)
        return 1

    errors = 0
    for target in TARGETS:
        try:
            progress = read_progress(target)
            sources, stale = evaluate(
                sources,
                target["name"],
                progress,
                now,
                args.stale_seconds,
                args.deep_rewind_blocks,
            )
        except RuntimeError as exc:
            print(f"watchdog: {target['name']} progress unavailable: {exc}", file=sys.stderr)
            errors += 1
            continue

        summary = ", ".join(f"{indexer}={block}" for indexer, block in progress.items())
        if not stale:
            print(f"watchdog: {target['name']} progress {summary}")
            continue

        try:
            running = container_is_running(target["indexer_container"])
        except RuntimeError as exc:
            print(f"watchdog: {target['name']} container check failed: {exc}", file=sys.stderr)
            errors += 1
            continue
        if not running:
            print(
                f"watchdog: {target['name']} container is stopped; refusing to start it",
                file=sys.stderr,
            )
            errors += 1
            continue

        lagging = []
        for indexer, block, high_water, age in stale:
            state = sources[f"{target['name']}/{indexer}"]
            last_attempt = int(state.get("restart_attempted_at", 0))
            cooldown_left = args.restart_cooldown_seconds - (now - last_attempt)
            if last_attempt and cooldown_left > 0:
                print(
                    f"watchdog: {target['name']}/{indexer} restart cooldown "
                    f"{cooldown_left}s"
                )
                continue
            try:
                head = read_chain_head(target["indexers"][indexer])
            except RuntimeError as exc:
                print(
                    f"watchdog: {target['name']}/{indexer} {exc}; refusing restart",
                    file=sys.stderr,
                )
                errors += 1
                continue
            lag = head - high_water
            if lag < args.min_lag_blocks:
                print(
                    f"watchdog: {target['name']}/{indexer} stale {age}s but lag={lag}; "
                    "refusing restart"
                )
                continue
            lagging.append((indexer, block, high_water, age, head, lag))

        if not lagging:
            continue
        stale_summary = ", ".join(
            f"{indexer}={block} high={high_water} head={head} lag={lag} stale={age}s"
            for indexer, block, high_water, age, head, lag in lagging
        )
        if args.dry_run:
            print(
                f"watchdog: would restart {target['indexer_container']}: {stale_summary}"
            )
            continue

        for indexer in progress:
            source = sources[f"{target['name']}/{indexer}"]
            source["changed_at"] = now
            source["restart_attempted_at"] = now
        try:
            save_state(args.state, sources)
        except OSError as exc:
            print(
                f"watchdog: cannot persist restart cooldown for {target['name']}: {exc}",
                file=sys.stderr,
            )
            errors += 1
            continue

        try:
            restart_container(target["indexer_container"])
            print(f"watchdog: restarted {target['indexer_container']}: {stale_summary}")
        except RuntimeError as exc:
            print(f"watchdog: restart failed for {target['name']}: {exc}", file=sys.stderr)
            errors += 1

    try:
        save_state(args.state, sources)
    except OSError as exc:
        print(f"watchdog: cannot save state: {exc}", file=sys.stderr)
        return 1
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
