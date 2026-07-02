<!-- This Source Code Form is subject to the terms of the Mozilla Public
     License, v. 2.0. If a copy of the MPL was not distributed with this
     file, You can obtain one at https://mozilla.org/MPL/2.0/. -->

# Scheduled one-shot backfill (macOS `launchd`)

For a backfill that must survive a session exit, sleep, or reboot, schedule it
as a `launchd` **LaunchAgent** rather than a shell `sleep` or an in-session
timer (both of which die when the terminal / Claude session ends).

## Layout

- `~/.aaq-backfill/run.sh` — wrapper: runs `run_backfill.py <start> <end>`, then
  removes its own plist and unloads the agent so it is **one-shot** (never
  re-fires the next day).
- `~/Library/LaunchAgents/net.thunderbird.aaq-backfill.plist` — the schedule
  (`StartCalendarInterval`). If the Mac is asleep at the trigger time, launchd
  runs it on the next wake.
- `~/.aaq-backfill/backfill.log` — combined stdout/stderr; `tail -f` to watch.

## Load / verify

```sh
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/net.thunderbird.aaq-backfill.plist
launchctl print "gui/$(id -u)/net.thunderbird.aaq-backfill"   # state = not running until it fires
```

## Cancel before it runs

Removing the agent before the trigger time stops it from ever firing:

```sh
launchctl bootout "gui/$(id -u)/net.thunderbird.aaq-backfill"
rm -f ~/Library/LaunchAgents/net.thunderbird.aaq-backfill.plist
```

(`$(id -u)` is the numeric user id, e.g. `501`.) The wrapper runs these same two
lines itself after a successful backfill, so no manual cleanup is needed once it
has completed.
