# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Integration test for deploy/entrypoint.sh using a local bare repo.

Exercises the clone -> (fake) refresh -> commit -> push loop without a network
or a real token. The token is stripped from a file:// URL by git, so a dummy
value is fine."""
import os
import subprocess
from pathlib import Path


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, check=True,
                          capture_output=True, text=True)


def _git_env():
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
    })
    return env


def test_entrypoint_commits_new_csv(tmp_path):
    genv = _git_env()
    # A bare "origin" with one commit on main containing an empty data dir marker.
    origin = tmp_path / "origin.git"
    _run(["git", "init", "--bare", "-b", "main", str(origin)], env=genv)
    seed = tmp_path / "seed"
    _run(["git", "clone", str(origin), str(seed)], env=genv)
    (seed / "2099").mkdir()
    (seed / "2099" / ".keep").write_text("")
    _run(["git", "add", "-A"], cwd=seed, env=genv)
    _run(["git", "commit", "-m", "seed"], cwd=seed, env=genv)
    _run(["git", "push", "origin", "main"], cwd=seed, env=genv)

    repo_root = Path(__file__).resolve().parent.parent
    env = _git_env()
    env.update({
        "GIT_REPO_URL": f"file://{origin}",
        "GITHUB_TOKEN": "dummy",
        "GIT_BRANCH": "main",
        # Fake refresh: write a CSV under 2099/ so there is something to commit.
        "REFRESH_CMD": "echo id > 2099/questions-test-2099-01-01.csv",
    })
    _run(["bash", str(repo_root / "deploy" / "entrypoint.sh")], env=env)

    # The bare origin now has the new file on main.
    log = _run(["git", "log", "--oneline", "-1", "main"], cwd=origin, env=genv)
    assert "Hourly refresh" in log.stdout
    show = _run(["git", "show",
                 "main:2099/questions-test-2099-01-01.csv"], cwd=origin, env=genv)
    assert show.stdout.strip() == "id"


def test_entrypoint_no_changes_no_commit(tmp_path):
    genv = _git_env()
    origin = tmp_path / "origin.git"
    _run(["git", "init", "--bare", "-b", "main", str(origin)], env=genv)
    seed = tmp_path / "seed"
    _run(["git", "clone", str(origin), str(seed)], env=genv)
    (seed / "2099").mkdir()
    (seed / "2099" / ".keep").write_text("")
    _run(["git", "add", "-A"], cwd=seed, env=genv)
    _run(["git", "commit", "-m", "seed"], cwd=seed, env=genv)
    _run(["git", "push", "origin", "main"], cwd=seed, env=genv)

    repo_root = Path(__file__).resolve().parent.parent
    env = _git_env()
    env.update({
        "GIT_REPO_URL": f"file://{origin}", "GITHUB_TOKEN": "dummy",
        "GIT_BRANCH": "main", "REFRESH_CMD": "true",  # writes nothing
    })
    _run(["bash", str(repo_root / "deploy" / "entrypoint.sh")], env=env)

    count = _run(["git", "rev-list", "--count", "main"], cwd=origin, env=genv)
    assert count.stdout.strip() == "1"  # still just the seed commit
