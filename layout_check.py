#! /usr/bin/env python3 
"""
layout-check
~~~~~~~~~~~~

A tiny CLI for diffing storage layouts of Foundry projects.

Usage
-----
    layout-check diff <OLD_COMMIT> <NEW_COMMIT>

The tool:
1. checks out each commit in turn;
2. runs `forge clean && forge build`;
3. gathers every contract’s storage layout via `forge inspect <C> storage`;
4. prints a colour-coded diff (green additions, red removals) showing only
   the changes.

Hardhat artifacts are ignored – this is Foundry-only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple

import git
import typer
from colorama import Fore, Style, init as colorama_init

# ──────────────────────────────────────────────
# CLI set-up
# ──────────────────────────────────────────────
app = typer.Typer(help="Diff storage layout between two git commits")
colorama_init()  # enable ANSI colours on Windows too


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _run(cmd: List[str]) -> str:
    """Run `cmd`, return stdout, abort on non-zero exit."""
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        typer.secho(f"❌  {' '.join(cmd)} failed", fg=typer.colors.RED, err=True)
        typer.echo(res.stderr.strip(), err=True)
        raise typer.Exit(res.returncode)
    return res.stdout.strip()


def _artifact_contracts() -> Set[str]:
    """Return every contract name found in Foundry `out/` artifacts."""
    names: Set[str] = set()
    for p in Path("out").rglob("*.json"):
        try:
            data = json.loads(p.read_text())
            if "contractName" in data:
                names.add(data["contractName"])
        except Exception:
            continue  # skip odd files (e.g. build-info)
    return names


def _collect_layouts(repo: git.Repo, ref: str) -> Dict[str, List[Tuple[int, int, str, str]]]:
    """
    • checkout `ref`
    • compile with Foundry
    • return {contract → [(slot, offset, label, type), …]}
    """
    repo.git.checkout(ref)

    _run(["forge", "clean", "--silent"])
    _run(["forge", "build", "--silent"])

    layouts: Dict[str, List[Tuple[int, int, str, str]]] = {}
    for name in sorted(_artifact_contracts()):
        try:
            # Ask forge for JSON; some older versions need the --json flag.
            raw = _run(["forge", "inspect", name, "storage", "--json"])
            if not raw:
                continue

            try:
                items = json.loads(raw)
                entries: List[Tuple[int, int, str, str]] = []
                for it in items:
                    slot_raw = it["slot"]
                    slot = int(slot_raw, 0) if isinstance(slot_raw, str) else int(slot_raw)
                    offset = int(it.get("offset", 0))
                    label = it.get("label", "")
                    typ = it.get("type", "")
                    entries.append((slot, offset, label, typ))
            except json.JSONDecodeError:
                # Fallback: parse the pretty table output (| name | type | slot | offset | bytes |)
                entries = []
                for line in raw.splitlines():
                    if not line.startswith("|"):
                        continue
                    cols = [c.strip() for c in line.strip().split("|")[1:-1]]
                    if len(cols) < 5 or cols[0].lower() in ("variable", ""):
                        continue  # header / separator
                    try:
                        slot = int(cols[2])
                        offset = int(cols[3])
                    except ValueError:
                        continue
                    label, typ = cols[0], cols[1]
                    entries.append((slot, offset, label, typ))
        except Exception:
            continue  # ignore libraries / interfaces

        if entries:
            layouts[name] = entries

    return layouts


# ──────────────────────────────────────────────
# Formatting & diff
# ──────────────────────────────────────────────
def _fmt(entry: Tuple[int, int, str, str]) -> str:
    slot, offs, lab, typ = entry
    return f"[slot {slot:>3} | off {offs:>2}] {lab} : {typ}"


def _diff_one(contract: str,
              old_: List[Tuple[int, int, str, str]],
              new_: List[Tuple[int, int, str, str]]) -> None:
    """Print colour-coded diff for a single contract."""
    removed = set(old_) - set(new_)
    added   = set(new_) - set(old_)

    if not removed and not added:
        return

    typer.secho(f"\nContract: {contract}", fg=typer.colors.CYAN, bold=True)

    for e in sorted(removed):
        typer.echo(Fore.RED   + "− " + _fmt(e) + Style.RESET_ALL)
    for e in sorted(added):
        typer.echo(Fore.GREEN + "+ " + _fmt(e) + Style.RESET_ALL)


# ──────────────────────────────────────────────
# CLI command
# ──────────────────────────────────────────────
@app.command()
def diff(
    old_commit: str = typer.Argument(..., help="older git commit / tag / branch"),
    new_commit: str = typer.Argument(..., help="newer git commit / tag / branch"),
) -> None:
    """
    Compare storage layouts between *all* contracts at two git revisions.

    Prints only the differences (slot added/removed/changed).
    """
    repo = git.Repo(Path("."), search_parent_directories=True)

    if repo.is_dirty(untracked_files=True):
        typer.secho("⚠️  Please commit or stash your changes first.", fg=typer.colors.RED)
        raise typer.Exit(1)

    current = repo.head.commit.hexsha  # to restore later

    try:
        typer.echo(f"⏳  Collecting layouts at {old_commit} …")
        old_layouts = _collect_layouts(repo, old_commit)

        typer.echo(f"⏳  Collecting layouts at {new_commit} …")
        new_layouts = _collect_layouts(repo, new_commit)
    finally:
        repo.git.checkout(current)

    for c in sorted(set(old_layouts) | set(new_layouts)):
        _diff_one(c, old_layouts.get(c, []), new_layouts.get(c, []))

    typer.echo("\n✅  Done.")


# ──────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────
if __name__ == "__main__":
    app()