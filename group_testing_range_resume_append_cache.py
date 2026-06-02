from itertools import combinations
from math import comb
import time
import json
import os
from typing import Any

# ============================================================
# Configuration
# ============================================================

FINAL_RESULTS_FILE = "final_results.json"
CHECKPOINT_FILE = "intermediate_results.json"
CHECKPOINT_INTERVAL_SECONDS = 300  # 5 minutes


# ============================================================
# Utilities
# ============================================================

def info_lower_bound(n: int, d: int) -> int:
    if not (0 <= d <= n):
        raise ValueError("Need 0 <= d <= n.")
    c = comb(n, d)
    if c <= 1:
        return 0
    return (c - 1).bit_length()


def bitstring(x: int, k: int) -> str:
    return format(x, f"0{k}b")


def detectors_for_column(x: int, k: int):
    return [i + 1 for i in range(k) if (x >> (k - 1 - i)) & 1]


def subset_or(columns, subset):
    o = 0
    for idx in subset:
        o |= columns[idx]
    return o


def validate_solution(columns, d: int):
    seen = {}
    for subset in combinations(range(len(columns)), d):
        o = 0
        for i in subset:
            o |= columns[i]
        if o in seen:
            return False, (seen[o], subset, o)
        seen[o] = subset
    return True, None


def values_for_k(k: int):
    vals = list(range(1 << k))
    vals.sort(key=lambda x: (x.bit_count(), x))
    return vals


# ============================================================
# JSON helpers
# ============================================================

def write_json(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# Final-results cache helpers (APPEND semantics)
# ============================================================

def load_final_results_records() -> list[dict[str, Any]]:
    """
    Returns the full list of final result records.

    Backward-compatible behavior:
    - if final_results.json does not exist -> []
    - if file contains a single dict (old format) -> [that dict]
    - if file contains a list -> that list
    """
    if not os.path.exists(FINAL_RESULTS_FILE):
        return []

    payload = read_json(FINAL_RESULTS_FILE)

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]

    raise RuntimeError(
        f"Unexpected format in {FINAL_RESULTS_FILE}: expected list or dict, got {type(payload)}"
    )


def save_final_results_records(records: list[dict[str, Any]]) -> None:
    write_json(FINAL_RESULTS_FILE, records)


def find_existing_final_result(n: int, d: int) -> dict[str, Any] | None:
    """
    If final_results.json already contains a solved record for the same (n, d),
    return it so the script can print it immediately instead of re-running.

    If multiple matching results exist, prefer the one with the smallest k,
    and among ties prefer the latest written_at_epoch.
    """
    records = load_final_results_records()
    matches = [
        r for r in records
        if r.get("n") == n and r.get("d") == d and "k" in r and "columns" in r
    ]
    if not matches:
        return None

    matches.sort(key=lambda r: (r.get("k", 10**18), -float(r.get("written_at_epoch", 0))))
    return matches[0]


def append_final_result_record(record: dict[str, Any]) -> None:
    records = load_final_results_records()
    records.append(record)
    save_final_results_records(records)


# ============================================================
# Checkpoint persistence
# ============================================================

def write_checkpoint(
    *,
    n: int,
    d: int,
    current_k: int,
    mode: str,
    stack: list[dict[str, Any]],
    elapsed_before_current_k: float,
    current_k_elapsed: float,
    started_from_checkpoint: bool,
) -> None:
    payload = {
        "version": 1,
        "n": n,
        "d": d,
        "current_k": current_k,
        "mode": mode,
        "stack": stack,
        "elapsed_before_current_k": elapsed_before_current_k,
        "current_k_elapsed": current_k_elapsed,
        "checkpoint_interval_seconds": CHECKPOINT_INTERVAL_SECONDS,
        "started_from_checkpoint": started_from_checkpoint,
        "saved_at_epoch": time.time(),
    }
    write_json(CHECKPOINT_FILE, payload)


def try_load_checkpoint(n: int, d: int):
    if not os.path.exists(CHECKPOINT_FILE):
        return None

    payload = read_json(CHECKPOINT_FILE)
    if payload.get("n") != n or payload.get("d") != d:
        print(
            f"Ignoring existing checkpoint because it is for "
            f"(n,d)=({payload.get('n')},{payload.get('d')}) instead of ({n},{d})."
        )
        return None

    return payload


def create_final_result_record(
    *,
    n: int,
    d: int,
    k: int,
    columns: list[int],
    total_runtime_seconds: float,
    resumed_from_checkpoint: bool,
) -> dict[str, Any]:
    valid, witness = validate_solution(columns, d)
    if not valid:
        raise RuntimeError(f"Internal error: invalid solution, collision = {witness}")

    payload = {
        "version": 1,
        "n": n,
        "d": d,
        "k": k,
        "total_runtime_seconds": total_runtime_seconds,
        "resumed_from_checkpoint": resumed_from_checkpoint,
        "columns": columns,
        "columns_bitstrings": [bitstring(c, k) for c in columns],
        "detector_connections": [
            {
                "bulb": i + 1,
                "codeword": bitstring(c, k),
                "detectors": detectors_for_column(c, k),
            }
            for i, c in enumerate(columns)
        ],
        "written_at_epoch": time.time(),
    }

    if comb(n, d) <= 200:
        payload["subset_outputs"] = [
            {
                "subset": [i + 1 for i in subset],
                "output": bitstring(subset_or(columns, subset), k),
            }
            for subset in combinations(range(n), d)
        ]

    return payload


def store_final_result(
    *,
    n: int,
    d: int,
    k: int,
    columns: list[int],
    total_runtime_seconds: float,
    resumed_from_checkpoint: bool,
) -> dict[str, Any]:
    record = create_final_result_record(
        n=n,
        d=d,
        k=k,
        columns=columns,
        total_runtime_seconds=total_runtime_seconds,
        resumed_from_checkpoint=resumed_from_checkpoint,
    )
    append_final_result_record(record)
    return record


# ============================================================
# Iterative exact solver for d = 2 (checkpoint-friendly)
# ============================================================

def search_fixed_k_d2_iterative(
    *,
    n: int,
    k: int,
    stack: list[dict[str, Any]] | None,
    elapsed_before_current_k: float,
    resumed_from_checkpoint: bool,
):
    vals = values_for_k(k)
    start_time = time.perf_counter()

    if stack is None:
        stack = [{"start_idx": 0, "chosen": [], "used_pair_ors": []}]

    last_checkpoint_time = time.perf_counter()

    while stack:
        now = time.perf_counter()
        if now - last_checkpoint_time >= CHECKPOINT_INTERVAL_SECONDS:
            current_k_elapsed = now - start_time
            write_checkpoint(
                n=n,
                d=2,
                current_k=k,
                mode="d2",
                stack=stack,
                elapsed_before_current_k=elapsed_before_current_k,
                current_k_elapsed=current_k_elapsed,
                started_from_checkpoint=resumed_from_checkpoint,
            )
            print(
                f"Checkpoint written for k={k} "
                f"(elapsed in current k: {current_k_elapsed:.2f}s, stack size: {len(stack)})"
            )
            last_checkpoint_time = now

        state = stack.pop()
        start_idx = state["start_idx"]
        chosen = state["chosen"]
        used_pair_ors = set(state["used_pair_ors"])

        m = len(chosen)
        if m == n:
            elapsed = time.perf_counter() - start_time
            return {
                "solution": chosen,
                "elapsed_current_k": elapsed,
                "resumed": resumed_from_checkpoint,
            }

        if m + (len(vals) - start_idx) < n:
            continue

        children = []
        for idx in range(len(vals) - 1, start_idx - 1, -1):
            x = vals[idx]
            local = set()
            ok = True
            for a in chosen:
                o = a | x
                if o in used_pair_ors or o in local:
                    ok = False
                    break
                local.add(o)

            if not ok:
                continue

            child = {
                "start_idx": idx + 1,
                "chosen": chosen + [x],
                "used_pair_ors": sorted(used_pair_ors | local),
            }
            children.append(child)

        stack.extend(children)

    elapsed = time.perf_counter() - start_time
    return {
        "solution": None,
        "elapsed_current_k": elapsed,
        "resumed": resumed_from_checkpoint,
    }


# ============================================================
# Iterative exact solver for general d (checkpoint-friendly)
# ============================================================

def search_fixed_k_general_iterative(
    *,
    n: int,
    d: int,
    k: int,
    stack: list[dict[str, Any]] | None,
    elapsed_before_current_k: float,
    resumed_from_checkpoint: bool,
):
    vals = values_for_k(k)
    start_time = time.perf_counter()

    if stack is None:
        empty_layers = [[] for _ in range(d + 1)]
        stack = [{"start_idx": 0, "chosen": [], "layers": empty_layers}]

    last_checkpoint_time = time.perf_counter()

    while stack:
        now = time.perf_counter()
        if now - last_checkpoint_time >= CHECKPOINT_INTERVAL_SECONDS:
            current_k_elapsed = now - start_time
            write_checkpoint(
                n=n,
                d=d,
                current_k=k,
                mode="general",
                stack=stack,
                elapsed_before_current_k=elapsed_before_current_k,
                current_k_elapsed=current_k_elapsed,
                started_from_checkpoint=resumed_from_checkpoint,
            )
            print(
                f"Checkpoint written for k={k} "
                f"(elapsed in current k: {current_k_elapsed:.2f}s, stack size: {len(stack)})"
            )
            last_checkpoint_time = now

        state = stack.pop()
        start_idx = state["start_idx"]
        chosen = state["chosen"]
        layers = state["layers"]

        m = len(chosen)
        if m == n:
            elapsed = time.perf_counter() - start_time
            return {
                "solution": chosen,
                "elapsed_current_k": elapsed,
                "resumed": resumed_from_checkpoint,
            }

        if m + (len(vals) - start_idx) < n:
            continue

        if d >= 2 and m >= d - 1:
            layer_dm1 = layers[d - 1]
            if len(layer_dm1) != len(set(layer_dm1)):
                continue

        children = []
        for idx in range(len(vals) - 1, start_idx - 1, -1):
            x = vals[idx]
            new_layers = [list(layer) for layer in layers]
            ok = True

            new_layers[1].append(x)
            max_t = min(d, m + 1)

            for t in range(max_t, 1, -1):
                prev = layers[t - 1]
                if not prev:
                    continue

                generated = [o | x for o in prev]

                if t == d:
                    if len(generated) != len(set(generated)):
                        ok = False
                        break
                    existing_set = set(layers[d])
                    if existing_set.intersection(generated):
                        ok = False
                        break

                new_layers[t].extend(generated)

            if not ok:
                continue

            child = {
                "start_idx": idx + 1,
                "chosen": chosen + [x],
                "layers": new_layers,
            }
            children.append(child)

        stack.extend(children)

    elapsed = time.perf_counter() - start_time
    return {
        "solution": None,
        "elapsed_current_k": elapsed,
        "resumed": resumed_from_checkpoint,
    }


# ============================================================
# Driver with cache + checkpoint-resume support
# ============================================================

def search_optimal_structure_with_resume(n: int, d: int):
    if not (0 <= d <= n):
        raise ValueError("Need 0 <= d <= n.")

    cached = find_existing_final_result(n, d)
    if cached is not None:
        print(f"Found cached final result for (n,d)=({n},{d}) in {FINAL_RESULTS_FILE}.")
        return cached["k"], cached["columns"], float(cached.get("total_runtime_seconds", 0.0)), False, True

    if d == 0:
        record = store_final_result(
            n=n,
            d=d,
            k=0,
            columns=[],
            total_runtime_seconds=0.0,
            resumed_from_checkpoint=False,
        )
        return 0, [], record["total_runtime_seconds"], False, False

    if d == n:
        cols = [0] * n
        record = store_final_result(
            n=n,
            d=d,
            k=0,
            columns=cols,
            total_runtime_seconds=0.0,
            resumed_from_checkpoint=False,
        )
        return 0, cols, record["total_runtime_seconds"], False, False

    if d == 1:
        k = (n - 1).bit_length()
        cols = list(range(n))
        record = store_final_result(
            n=n,
            d=d,
            k=k,
            columns=cols,
            total_runtime_seconds=0.0,
            resumed_from_checkpoint=False,
        )
        return k, cols, record["total_runtime_seconds"], False, False

    resumed_from_checkpoint = False
    checkpoint = try_load_checkpoint(n, d)

    if checkpoint is not None:
        current_k = checkpoint["current_k"]
        elapsed_before_current_k = checkpoint.get("elapsed_before_current_k", 0.0)
        mode = checkpoint["mode"]
        stack = checkpoint["stack"]
        resumed_from_checkpoint = True
        print(
            f"Resuming from checkpoint: n={n}, d={d}, "
            f"k={current_k}, mode={mode}, stack size={len(stack)}"
        )
    else:
        current_k = info_lower_bound(n, d)
        elapsed_before_current_k = 0.0
        stack = None
        mode = "d2" if d == 2 else "general"
        print(f"Starting fresh from lower bound k={current_k}")

    while True:
        print(f"Searching k={current_k} ...")

        if d == 2:
            result = search_fixed_k_d2_iterative(
                n=n,
                k=current_k,
                stack=stack if mode == "d2" else None,
                elapsed_before_current_k=elapsed_before_current_k,
                resumed_from_checkpoint=resumed_from_checkpoint,
            )
        else:
            result = search_fixed_k_general_iterative(
                n=n,
                d=d,
                k=current_k,
                stack=stack if mode == "general" else None,
                elapsed_before_current_k=elapsed_before_current_k,
                resumed_from_checkpoint=resumed_from_checkpoint,
            )

        solution = result["solution"]
        elapsed_current_k = result["elapsed_current_k"]

        print(f"Checked k={current_k} in {elapsed_current_k:.4f} seconds")
        total_elapsed = elapsed_before_current_k + elapsed_current_k

        if solution is not None:
            record = store_final_result(
                n=n,
                d=d,
                k=current_k,
                columns=solution,
                total_runtime_seconds=total_elapsed,
                resumed_from_checkpoint=resumed_from_checkpoint,
            )
            if os.path.exists(CHECKPOINT_FILE):
                os.remove(CHECKPOINT_FILE)
            return current_k, solution, record["total_runtime_seconds"], resumed_from_checkpoint, False

        current_k += 1
        elapsed_before_current_k = total_elapsed
        stack = None
        mode = "d2" if d == 2 else "general"

        write_checkpoint(
            n=n,
            d=d,
            current_k=current_k,
            mode=mode,
            stack=[{"start_idx": 0, "chosen": [], "used_pair_ors": []}] if d == 2 else [{"start_idx": 0, "chosen": [], "layers": [[] for _ in range(d + 1)]}],
            elapsed_before_current_k=elapsed_before_current_k,
            current_k_elapsed=0.0,
            started_from_checkpoint=resumed_from_checkpoint,
        )


# ============================================================
# Pretty-printing
# ============================================================

def print_solution(n: int, d: int, k: int, columns, total_runtime_seconds: float, source: str):
    print(f"\nOptimal value: k({n},{d}) = {k}")
    print(f"Result source: {source}")
    print(f"Total runtime recorded: {total_runtime_seconds:.4f} seconds\n")

    print("Bulb codewords / detector connections:")
    for bulb_idx, col in enumerate(columns, start=1):
        print(
            f"  bulb {bulb_idx:>2}: {bitstring(col, k)}   "
            f"connected to detectors {detectors_for_column(col, k)}"
        )

    if comb(n, d) <= 100:
        print(f"\nAll size-{d} active subsets and their detector outputs:")
        for subset in combinations(range(len(columns)), d):
            o = subset_or(columns, subset)
            human_subset = tuple(i + 1 for i in subset)
            print(f"  {human_subset} -> {bitstring(o, k)}")


def process_single_instance(n: int, d: int):
    run_start = time.perf_counter()
    k, columns, recorded_runtime, resumed_from_checkpoint, from_cache = search_optimal_structure_with_resume(n, d)
    wall_runtime = time.perf_counter() - run_start
    source = "cached final_results.json" if from_cache else ("newly computed" if not resumed_from_checkpoint else "resumed checkpoint and computed")
    print_solution(n, d, k, columns, recorded_runtime, source)
    print(f"\nWall-clock time for this script invocation: {wall_runtime:.4f} seconds")
    print(f"Final results file: {FINAL_RESULTS_FILE}")
    print(f"Checkpoint file:    {CHECKPOINT_FILE}")


def process_range(start_n: int, end_n: int, d: int):
    if start_n > end_n:
        raise ValueError("start_n must be <= end_n.")

    batch_start = time.perf_counter()
    summary = []

    for current_n in range(start_n, end_n + 1):
        print("\n" + "=" * 72)
        print(f"Processing n={current_n}, d={d}")
        print("=" * 72)
        instance_start = time.perf_counter()

        k, columns, recorded_runtime, resumed_from_checkpoint, from_cache = search_optimal_structure_with_resume(current_n, d)
        wall_runtime = time.perf_counter() - instance_start

        source = (
            "cached final_results.json"
            if from_cache
            else ("newly computed" if not resumed_from_checkpoint else "resumed checkpoint and computed")
        )

        print_solution(current_n, d, k, columns, recorded_runtime, source)
        print(f"\nWall-clock time for this n in this invocation: {wall_runtime:.4f} seconds")

        summary.append(
            {
                "n": current_n,
                "d": d,
                "k": k,
                "source": source,
                "recorded_runtime_seconds": recorded_runtime,
                "wall_runtime_seconds_this_invocation": wall_runtime,
            }
        )

    total_wall = time.perf_counter() - batch_start

    print("\n" + "#" * 72)
    print("Batch summary")
    print("#" * 72)
    for row in summary:
        print(
            f"n={row['n']}, d={row['d']} -> k={row['k']} | "
            f"source={row['source']} | "
            f"stored_runtime={row['recorded_runtime_seconds']:.4f}s | "
            f"this_run_wall={row['wall_runtime_seconds_this_invocation']:.4f}s"
        )
    print(f"\nTotal wall-clock time for batch invocation: {total_wall:.4f} seconds")
    print(f"Final results file: {FINAL_RESULTS_FILE}")
    print(f"Checkpoint file:    {CHECKPOINT_FILE}")


# ============================================================
# Main
# ============================================================

def main():
    mode = input("Choose mode: [1] single n  [2] range of n : ").strip()
    d = int(input("Enter d (exactly how many bulbs are lit): ").strip())

    if mode == "2":
        start_n = int(input("Enter start_n: ").strip())
        end_n = int(input("Enter end_n: ").strip())
        process_range(start_n, end_n, d)
    else:
        n = int(input("Enter n (number of bulbs): ").strip())
        process_single_instance(n, d)


if __name__ == "__main__":
    main()
