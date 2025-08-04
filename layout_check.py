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
import re
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



# ─── Helpers ──────────────────────────────────────────────────────────
 # List of path prefixes we ignore when gathering contracts
_IGNORE_PREFIXES = ("lib/", "test/", "script/")

def _artifact_contract_ids() -> List[str]:
    """
    Scan `out/` for Foundry artifacts and yield identifiers accepted by
    `forge inspect`, in the canonical  `<relative-path>.sol:<Contract>` form.

    The logic works even when artifacts lack `sourcePath` / `sourceName`
    by reading the embedded solidity compiler metadata.

    Returns
    -------
    List[str]
        Ordered list without duplicates. Example:
        ["src/Test.sol:Test", "lib/forge-std/src/console.sol:console"]
    """
    seen: Set[str] = set()
    id_list: List[str] = []

    for art in Path("out").rglob("*.json"):
        # Skip debug and build-info blobs
        if art.name.endswith(".dbg.json") or "build-info" in art.parts:
            continue

        try:
            meta = json.loads(art.read_text())
        except Exception:
            continue  # unreadable

        # 1. Try legacy keys
        source = meta.get("sourcePath") or meta.get("sourceName")
        name   = meta.get("contractName")

        # 2. Prefer metadata.settings.compilationTarget
        md = meta.get("metadata")
        if md:
            try:
                md_obj = json.loads(md) if isinstance(md, str) else md
                comp_target = md_obj.get("settings", {}).get("compilationTarget", {})
                if comp_target:
                    # there should be exactly one entry
                    source, name = next(iter(comp_target.items()))
            except Exception:
                pass  # keep any legacy data we already grabbed

        # 3. Derive from artefact path if still missing
        if not source and art.parent.name.endswith(".sol"):
            source = Path(*art.parent.parts[1:]).as_posix()
        if not name:
            name = art.stem

        if not source or not name:
            continue  # cannot form identifier

        ident = f"{source}:{name}"
        # Skip external libraries / tests / scripts unless explicitly requested
        if any(ident.startswith(p) for p in _IGNORE_PREFIXES):
            continue
        if ident not in seen:
            seen.add(ident)
            id_list.append(ident)

    return id_list


def _collect_layouts(repo: git.Repo, ref: str, include_paths: List[str] | None) -> Dict[str, List[Tuple[int, int, str, str]]]:
    """
    • checkout `ref`
    • compile with Foundry
    • return {contract → [(slot, offset, label, type), …]}
    """
    repo.git.checkout(ref)
    # ensure submodules match that revision
    repo.git.submodule("update", "--init", "--recursive")

    _run(["forge", "clean", "--silent"])
    _run(["forge", "build", "--silent", "--skip", "test", "--skip", "script"])

    all_idents = _artifact_contract_ids()
    if include_paths:
        def keep(i: str) -> bool:
            return any(i.startswith(p) for p in include_paths)
        all_idents = [i for i in all_idents if keep(i)]

    total = len(all_idents)
    layouts: Dict[str, List[Tuple[int, int, str, str]]] = {}
    for idx, ident in enumerate(all_idents, 1):
        typer.echo(f"      [{idx}/{total}] {ident}", err=True)
        try:
            # Ask forge for JSON; some older versions need the --json flag.
            raw = _run(["forge", "inspect", ident, "storageLayout", "--json"]).strip()
            if not raw:
                continue

            try:
                data = json.loads(raw)
                items = data["storage"] if isinstance(data, dict) and "storage" in data else data
                entries: List[Tuple[int, int, str, str]] = []
                for it in items:
                    slot_raw = it["slot"]
                    slot = int(slot_raw, 0) if isinstance(slot_raw, str) else int(slot_raw)
                    offset = int(it.get("offset", 0))
                    label = it.get("label", "")
                    typ_raw = it.get("type", "")
                    # remove all ')<digits>' sequences globally
                    typ = re.sub(r"\)\d+", ")", typ_raw)
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
            layouts[ident] = entries

    return layouts


# ──────────────────────────────────────────────
# Formatting & diff
# ──────────────────────────────────────────────
def _fmt(entry: Tuple[int, int, str, str]) -> str:
    slot, offs, lab, typ = entry
    return f"[slot {slot:>3} | offset {offs:>2}] {lab} : {typ}"

def _entry_key(e: Tuple[int, int, str, str]) -> Tuple[str, str]:
    """Return (label, type) – enough to recognise a variable across commits."""
    return (e[2], e[3])  # label, type

def _diff_one(contract: str,
              old_: List[Tuple[int, int, str, str]],
              new_: List[Tuple[int, int, str, str]]) -> None:
    """
    Show removals (red), additions (green), and *moves* (yellow ↷).
    A move = same (label,type) but different (slot,offset).
    """
    # quick sets for ± diff
    removed = set(old_) - set(new_)
    added   = set(new_) - set(old_)

    # detect moves
    old_by_key = {_entry_key(e): e for e in old_}
    new_by_key = {_entry_key(e): e for e in new_}

    moves: List[Tuple[Tuple[int,int,str,str], Tuple[int,int,str,str]]] = []
    for k in set(old_by_key) & set(new_by_key):
        o, n = old_by_key[k], new_by_key[k]
        if (o[0], o[1]) != (n[0], n[1]):        # coords differ
            moves.append((o, n))
            removed.discard(o)
            added.discard(n)

    if not removed and not added and not moves:
        return

    typer.secho(f"\nContract: {contract.split(':')[-1]}",
                fg=typer.colors.CYAN, bold=True)

    for old_e, new_e in sorted(moves, key=lambda p: (p[0][0], p[0][1])):
        o_slot, o_off, lab, typ = old_e
        n_slot, n_off, _, _     = new_e
        typer.echo(
            Fore.YELLOW + f"↷ {lab} : {typ}  {o_slot}/{o_off} → {n_slot}/{n_off}"
            + Style.RESET_ALL
        )

    for e in sorted(removed):
        typer.echo(Fore.RED + "− " + _fmt(e) + Style.RESET_ALL)

    for e in sorted(added):
        typer.echo(Fore.GREEN + "+ " + _fmt(e) + Style.RESET_ALL)

# ──────────────────────────────────────────────
# CLI command
# ──────────────────────────────────────────────
@app.command()
def diff(
    old_commit: str = typer.Argument(..., help="older git commit / tag / branch"),
    new_commit: str = typer.Argument(..., help="newer git commit / tag / branch"),
    path: List[str] = typer.Option(
        None,
        "--path",
        "-p",
        help="Source‑file prefix(es) to include, e.g. 'src/' or 'contracts/MyLib.sol'. "
             "If omitted, every contract in the project is inspected.",
    ),
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
        old_layouts = _collect_layouts(repo, old_commit, path)

        typer.echo(f"⏳  Collecting layouts at {new_commit} …")
        new_layouts = _collect_layouts(repo, new_commit, path)
    finally:
        repo.git.checkout(current)
        try:
            repo.git.submodule("update", "--init", "--recursive")
        except Exception:
            # if project has no submodules, ignore
            pass

    for c in sorted(set(old_layouts) | set(new_layouts)):
        _diff_one(c, old_layouts.get(c, []), new_layouts.get(c, []))

    typer.echo("\n✅  Done.")


# ──────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────
if __name__ == "__main__":
    app()