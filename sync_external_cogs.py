#!/usr/bin/env python3
"""Sync selected third-party cogs into this repository.

The script reads external_cogs.json, sparse-checks out each upstream
repository into a local cache, then copies the selected cog folder into this
repo. By default it only prints what it would do; pass --apply to replace files.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = REPO_ROOT / "external_cogs.json"
DEFAULT_CACHE_DIR = REPO_ROOT / ".external-cogs-cache"
IGNORE_PATTERNS = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
)


def run_git(args: list[str], *, cwd: Path | None = None) -> str:
    command = ["git", *args]
    if cwd is not None:
        command = ["git", "-c", f"safe.directory={cwd.resolve().as_posix()}", *args]

    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        location = f" in {cwd}" if cwd else ""
        raise RuntimeError(f"git {' '.join(args)} failed{location}:\n{result.stdout}")
    return result.stdout.strip()


def safe_child(base: Path, relative_path: str, *, label: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError(f"{label} must be a relative path.")

    base = base.resolve()
    target = (base / relative_path).resolve()
    if target == base or base not in target.parents:
        raise ValueError(f"{label} must stay inside {base}.")
    return target


def load_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8") as fp:
        entries = json.load(fp)

    if not isinstance(entries, list):
        raise ValueError("Manifest must be a list of cog entries.")

    required = {"name", "repo", "branch", "source_path", "target_path"}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each manifest entry must be an object.")
        missing = required - set(entry)
        if missing:
            raise ValueError(f"Manifest entry is missing: {', '.join(sorted(missing))}")
    return entries


def selected_entries(entries: list[dict[str, str]], only: Iterable[str]) -> list[dict[str, str]]:
    requested = {item.lower() for item in only}
    if not requested:
        return entries

    selected = [entry for entry in entries if entry["name"].lower() in requested]
    found = {entry["name"].lower() for entry in selected}
    missing = requested - found
    if missing:
        available = ", ".join(entry["name"] for entry in entries)
        raise ValueError(f"Unknown cog(s): {', '.join(sorted(missing))}. Available: {available}")
    return selected


def ensure_sparse_checkout(entry: dict[str, str], cache_dir: Path) -> Path:
    cache_path = safe_child(cache_dir, entry["name"], label="cache path")
    source_path = entry["source_path"].strip("/")

    if not cache_path.exists():
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        run_git(
            [
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--sparse",
                "--branch",
                entry["branch"],
                entry["repo"],
                str(cache_path),
            ]
        )
    else:
        run_git(["fetch", "--depth", "1", "origin", entry["branch"]], cwd=cache_path)
        run_git(["checkout", "FETCH_HEAD"], cwd=cache_path)

    run_git(["sparse-checkout", "set", source_path], cwd=cache_path)
    return safe_child(cache_path, source_path, label="source path")


def sync_entry(entry: dict[str, str], cache_dir: Path, *, apply: bool) -> None:
    source = ensure_sparse_checkout(entry, cache_dir)
    target = safe_child(REPO_ROOT, entry["target_path"], label="target path")

    if not source.exists():
        raise FileNotFoundError(f"Upstream path does not exist: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"Upstream path is not a directory: {source}")

    print(f"{entry['name']}: {entry['repo']}#{entry['branch']}:{entry['source_path']} -> {target}")
    if not apply:
        return

    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=IGNORE_PATTERNS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--only", nargs="*", default=[], help="Only sync these manifest names.")
    parser.add_argument("--list", action="store_true", help="List configured cogs and exit.")
    parser.add_argument("--apply", action="store_true", help="Actually replace local target folders.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entries = selected_entries(load_manifest(args.manifest), args.only)

    if args.list:
        for entry in entries:
            print(
                f"{entry['name']}: {entry['repo']}#{entry['branch']} "
                f"{entry['source_path']} -> {entry['target_path']}"
            )
        return 0

    if not args.apply:
        print("Dry run only. Pass --apply to replace local target folders.")

    try:
        for entry in entries:
            sync_entry(entry, args.cache_dir.resolve(), apply=args.apply)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
