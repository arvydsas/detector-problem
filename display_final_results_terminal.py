import json
import os
from math import comb
from itertools import combinations

FINAL_RESULTS_FILE = "final_results.json"


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_final_results_records(path: str = FINAL_RESULTS_FILE):
    """
    Supports both formats:
    - list of records (new format)
    - single dict (legacy format)
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Could not find {path}")

    payload = read_json(path)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]

    raise RuntimeError(
        f"Unexpected format in {path}: expected a list or a dict, got {type(payload)}"
    )


def bitstring(x: int, k: int) -> str:
    return format(x, f"0{k}b")


def detectors_for_column(x: int, k: int):
    """
    Leftmost bit = detector 1, ..., rightmost bit = detector k.
    """
    return [i + 1 for i in range(k) if (x >> (k - 1 - i)) & 1]


def normalize_records(records):
    """
    Keep one best record per (n, d).
    Preference order:
      1) smallest k
      2) latest written_at_epoch
    """
    best = {}
    for rec in records:
        if "n" not in rec or "d" not in rec or "k" not in rec:
            continue
        key = (rec["n"], rec["d"])
        if key not in best:
            best[key] = rec
            continue

        incumbent = best[key]
        candidate_key = (rec.get("k", 10**18), -float(rec.get("written_at_epoch", 0)))
        incumbent_key = (incumbent.get("k", 10**18), -float(incumbent.get("written_at_epoch", 0)))
        if candidate_key < incumbent_key:
            best[key] = rec

    return best


def print_summary_table(best_records, *, filter_d=None, start_n=None, end_n=None):
    rows = []
    for (n, d), rec in best_records.items():
        if filter_d is not None and d != filter_d:
            continue
        if start_n is not None and n < start_n:
            continue
        if end_n is not None and n > end_n:
            continue
        rows.append((n, d, rec.get("k"), float(rec.get("total_runtime_seconds", 0.0))))

    rows.sort()

    if not rows:
        print("No matching results found.")
        return

    print("\nSummary of stored k(n,d) results")
    print("-" * 72)
    print(f"{'n':>6} {'d':>6} {'k(n,d)':>10} {'stored_runtime_s':>18}")
    print("-" * 72)
    for n, d, k, runtime in rows:
        print(f"{n:>6} {d:>6} {k:>10} {runtime:>18.4f}")
    print("-" * 72)
    print(f"Total displayed rows: {len(rows)}")


def print_structure(rec):
    n = rec["n"]
    d = rec["d"]
    k = rec["k"]
    columns = rec.get("columns", [])

    print(f"\nDetailed structure for k({n},{d}) = {k}")
    print("=" * 72)
    print(f"Stored runtime: {float(rec.get('total_runtime_seconds', 0.0)):.4f} seconds")
    print(f"Resumed from checkpoint: {rec.get('resumed_from_checkpoint', False)}")

    print("\nBulb codewords / detector connections:")
    for bulb_idx, col in enumerate(columns, start=1):
        print(
            f"  bulb {bulb_idx:>2}: {bitstring(col, k)}   "
            f"connected to detectors {detectors_for_column(col, k)}"
        )

    subset_outputs = rec.get("subset_outputs")
    if subset_outputs:
        print(f"\nStored size-{d} subset detector outputs:")
        for row in subset_outputs:
            print(f"  {tuple(row['subset'])} -> {row['output']}")
    else:
        # Reconstruct if not saved and not too large
        if comb(n, d) <= 100:
            print(f"\nReconstructed size-{d} subset detector outputs:")
            for subset in combinations(range(n), d):
                o = 0
                for idx in subset:
                    o |= columns[idx]
                human_subset = tuple(i + 1 for i in subset)
                print(f"  {human_subset} -> {bitstring(o, k)}")
        else:
            print(
                f"\nSubset outputs are not stored for this entry and C({n},{d}) = {comb(n,d)} is large, "
                "so they are omitted from the display."
            )


def get_best_record(best_records, n, d):
    return best_records.get((n, d))


def main():
    path = input(f"Enter path to final results file [{FINAL_RESULTS_FILE}]: ").strip()
    if not path:
        path = FINAL_RESULTS_FILE

    records = load_final_results_records(path)
    best_records = normalize_records(records)

    while True:
        print("\nChoose display mode:")
        print("  [1] Show all stored k(n,d) results")
        print("  [2] Show all stored results for one fixed d")
        print("  [3] Show stored results for one fixed d and n-interval")
        print("  [4] Show one specific k(n,d)")
        print("  [5] Exit")

        choice = input("Enter choice: ").strip()

        if choice == "1":
            print_summary_table(best_records)

        elif choice == "2":
            d = int(input("Enter d: ").strip())
            print_summary_table(best_records, filter_d=d)

        elif choice == "3":
            d = int(input("Enter d: ").strip())
            start_n = int(input("Enter start_n: ").strip())
            end_n = int(input("Enter end_n: ").strip())
            print_summary_table(best_records, filter_d=d, start_n=start_n, end_n=end_n)

        elif choice == "4":
            n = int(input("Enter n: ").strip())
            d = int(input("Enter d: ").strip())
            rec = get_best_record(best_records, n, d)
            if rec is None:
                print(f"No stored result found for k({n},{d}).")
                continue

            print(f"\nStored result: k({n},{d}) = {rec['k']}")
            print(f"Stored runtime: {float(rec.get('total_runtime_seconds', 0.0)):.4f} seconds")

            show_structure = input("Display full structure? [y/N]: ").strip().lower()
            if show_structure == "y":
                print_structure(rec)

        elif choice == "5":
            print("Exiting.")
            break

        else:
            print("Invalid choice. Please enter 1, 2, 3, 4, or 5.")


if __name__ == "__main__":
    main()
