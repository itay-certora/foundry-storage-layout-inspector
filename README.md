# foundry-storage-layout-inspector

A lightweight CLI for **diffing the storage layout** of all contracts in a Foundry
project between any two Git commits.  
Use it to detect—before deploying—whether an upgrade will:

* overwrite an existing slot  
* shift packed variables, or  
* append new state safely.

---

## ✨ Features

- **One‑command diff** – `layout-check <OLD> <NEW>`  
- **Colour output**  
  - red `−` removed  
  - green `+` added  
  - yellow `↷` variable moved (same label / type, different slot)  
- **Large‑project aware** – skips `test/` and `script/` by default  
- **Path filters** – restrict to part of the repo with `-p`  
- **Noise free** – strips compiler‑internal type IDs  
- **Pure Foundry** – no Hardhat or Truffle artefacts required

---

## 1  Installation

```bash
git clone https://github.com/your-org/foundry-storage-layout-inspector.git
cd foundry-storage-layout-inspector

# optional: create a virtual‑env
python -m venv .venv
source .venv/bin/activate

# install CLI and dependencies
pip install -r requirements.txt
pip install -e .         # installs the 'layout-check' entry‑point
```

**Prerequisites**

* Python ≥ 3.9  
* Foundry (`forge`, `cast`, `anvil`) on your `$PATH`

---

## 2  Usage

```text
layout-check [OPTIONS] OLD_COMMIT NEW_COMMIT
```

| Option            | Description                                                          |
|-------------------|----------------------------------------------------------------------|
| `-p, --path TEXT` | Include only contracts whose identifier starts with this prefix.<br>May be given more than once. |
| `--help`          | Show CLI help.                                                       |

### Example

```bash
# Compare HEAD with its parent, only under src/
layout-check HEAD~1 HEAD -p src/
```

Output:

```text
⏳  Collecting layouts at 2e0fefc …
      [1/6] src/Token.sol:MyToken
      [2/6] src/Vault.sol:Vault
…

Contract: MyToken
− [slot   3 | off  0] paused : bool
+ [slot   3 | off  0] owner  : address
+ [slot   4 | off  0] paused : bool

Contract: Vault
↷ totalSupply : uint256  2/0 → 5/0
− [slot   4 | off  0] emergencyAdmin : address

✅  Done.
```

Legend:  
`−` removed `+` added `↷` moved (old slot / off → new slot / off)

---

## 3  How it works

1. Checks out each commit; refuses if the work‑tree is dirty.  
2. Runs `forge clean` and `forge build` (skipping tests and scripts).  
3. Gets contract identifiers via `forge build --names`.  
4. For each contract, parses `forge inspect … storageLayout --json`.  
5. Computes removals, additions and moves, then prints a coloured diff.

---

## 4  Tips & gotchas

* Use full SHAs or tags in CI to avoid surprises after force‑pushes.  
* The program exits with status `0` even when risky changes exist; wrap it
  in your own check if you want to fail the pipeline on red or yellow lines.  
* For very large repos start with `-p src/` and refine from there.

---
