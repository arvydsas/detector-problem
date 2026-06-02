# Group Testing / Separable Code Search Tools

This repository contains Python tools for computing and viewing exact values of:

\[
k(n,d)
\]

where:

- `n` = number of bulbs / items,
- `d` = exact number of active bulbs / defectives,
- `k(n,d)` = minimum number of detectors required to identify the active set with certainty,
- detectors return the **bitwise OR** over all connected active bulbs.

These scripts search for exact **d-separable codes** / **union-free families** for small and moderate problem sizes.

---

## Repository Contents

### 1. `group_testing_range_resume_append_cache.py`
Main computation script.

It can:

- compute `k(n,d)` for a **single** value of `n`,
- compute `k(n,d)` over a **range** of `n`,
- cache solved instances in `final_results.json`,
- append new solved results instead of overwriting,
- skip recomputation if a result already exists,
- periodically write intermediate checkpoint data to `intermediate_results.json`,
- resume unfinished searches from checkpoint data.

This script supports both the `d=2` specialized search path and the general `d` search path.

---

### 2. `display_final_results_terminal.py`
Terminal-based viewer for `final_results.json`.

It can:

- display all stored `k(n,d)` results,
- filter by a fixed `d`,
- filter by `d` and an interval of `n`,
- display one specific stored result `k(n,d)`,
- optionally show the full structure (codewords / detector connections / subset outputs if available).
---

### 3. `display_final_results_gui.py`
GUI-based viewer for `final_results.json` built with **Tkinter**.

It provides:

- file browse / load / reload actions,
- a tabular view of stored results,
- filters for `d`, `start_n`, and `end_n`,
- a summary panel for the selected entry,
- a structure view for the selected result,
- optional display of subset outputs when available. 

---

## Mathematical Problem

We assign each bulb `i` a binary codeword:

\[
c_i \in \{0,1\}^k
\]

where coordinate `j` indicates whether bulb `i` is connected to detector `j`.

If the active bulbs form a set \(S\) of size exactly `d`, then the detector output is:

\[
y = \bigvee_{i \in S} c_i
\]

The goal is to choose codewords so that all size-`d` subsets produce distinct OR outputs.  
The smallest such `k` is `k(n,d)`.

---

## File Formats

### `final_results.json`
Stores solved instances.

The computation script uses **append semantics**: each newly solved instance is appended as a new record instead of overwriting previous results. Existing solutions for the same `(n, d)` are reused instead of recomputed. 

Typical fields in a stored record include:

- `n`
- `d`
- `k`
- `total_runtime_seconds`
- `columns`
- `columns_bitstrings`
- `detector_connections`
- `subset_outputs` (for smaller cases)
- `written_at_epoch`

The viewer scripts normalize the records and, if multiple entries exist for the same `(n, d)`, choose the one with the **smallest `k`** and then the **latest timestamp**. 

---

### `intermediate_results.json`
Stores checkpoint data during long exact searches.  
The main computation script writes checkpoint data periodically and can resume from it later. 

---

## Requirements

- Python 3.10+ recommended
- Standard library only:
  - `json`
  - `os`
  - `time`
  - `math`
  - `itertools`
  - `typing`
  - `tkinter` (for the GUI viewer)

No third-party packages are required based on the attached scripts. 
---

## Usage

### Compute a single instance or a range

Run:

```bash
python group_testing_range_resume_append_cache.py
readme.txt
Displaying readme.txt.