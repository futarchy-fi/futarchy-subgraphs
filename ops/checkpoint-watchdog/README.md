# Checkpoint progress watchdog

The durable reorg-loop fix in PR #4 prevents poisoned near-tip block hashes
from persisting. This watchdog is the fallback for any later silent wedge. It
restarts only the affected running Checkpoint container when all of these are
true:

- its raw `_blocks` high-water mark has not advanced for 10 minutes;
- an independent free public chain head is at least 100 blocks ahead; and
- no restart has been attempted in the previous hour.

Adjacent-block reorg oscillation counts as stale rather than progress. A deep
intentional rewind starts a new baseline, as does the first successful sample
after a monitoring outage longer than the stale window.

It deliberately does not use event/candle table height, log text, or container
uptime as proof of progress. A database or chain-head query failure is logged
and never triggers a restart. A stopped container is never started. Active
indexer names are explicit: both production containers currently index Gnosis;
update this configuration when an additional chain is intentionally enabled.

## Verify

```bash
python3 test_checkpoint_watchdog.py
python3 checkpoint_watchdog.py --state /tmp/checkpoint-watchdog.json --dry-run
```

The first dry run creates a baseline. A later run reports progress without
restarting anything.

## Install on `futarchy-indexers`

```bash
sudo install -d -m 0755 /opt/futarchy-indexer-watchdog
sudo install -m 0755 checkpoint_watchdog.py /opt/futarchy-indexer-watchdog/
sudo install -m 0644 futarchy-indexer-watchdog.service /etc/systemd/system/
sudo install -m 0644 futarchy-indexer-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/futarchy-indexer-watchdog.service \
  /etc/systemd/system/futarchy-indexer-watchdog.timer
sudo systemctl start futarchy-indexer-watchdog.service
sudo journalctl -u futarchy-indexer-watchdog.service -n 20 --no-pager
sudo systemctl enable --now futarchy-indexer-watchdog.timer
```

Inspect runs with:

```bash
systemctl status futarchy-indexer-watchdog.timer
journalctl -u futarchy-indexer-watchdog.service
```

Disable automatic recovery with
`sudo systemctl disable --now futarchy-indexer-watchdog.timer`. The watchdog
does not send messages or change RPC configuration. Disable the timer before
planned indexer maintenance or a deliberate database rewind.
