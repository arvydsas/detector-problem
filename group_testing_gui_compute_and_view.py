import contextlib
import io
import json
import os
import queue
import threading
import time
import tkinter as tk
from itertools import combinations
from math import comb
from tkinter import filedialog, messagebox, ttk

import group_testing_range_resume_append_cache as solver

DEFAULT_FINAL_RESULTS_FILE = "final_results.json"
DEFAULT_CHECKPOINT_FILE = "intermediate_results.json"


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_final_results_records(path: str):
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
    return [i + 1 for i in range(k) if (x >> (k - 1 - i)) & 1]


def subset_or(columns, subset):
    out = 0
    for idx in subset:
        out |= columns[idx]
    return out


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
        candidate_key = (
            rec.get("k", 10**18),
            -float(rec.get("written_at_epoch", 0)),
        )
        incumbent_key = (
            incumbent.get("k", 10**18),
            -float(incumbent.get("written_at_epoch", 0)),
        )
        if candidate_key < incumbent_key:
            best[key] = rec
    return best


class QueueWriter(io.TextIOBase):
    def __init__(self, ui_queue: queue.Queue):
        self.ui_queue = ui_queue

    def write(self, text: str):
        if text:
            self.ui_queue.put(("log", text))
        return len(text)

    def flush(self):
        return None


class GroupTestingGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Group Testing Results Viewer + Solver")
        self.geometry("1420x920")
        self.minsize(1180, 760)

        self.records = []
        self.best_records = {}
        self.filtered_keys = []

        self.current_file = tk.StringVar(value=DEFAULT_FINAL_RESULTS_FILE)
        self.checkpoint_file = tk.StringVar(value=DEFAULT_CHECKPOINT_FILE)
        self.checkpoint_interval = tk.StringVar(value=str(solver.CHECKPOINT_INTERVAL_SECONDS))

        self.filter_d = tk.StringVar(value="")
        self.start_n = tk.StringVar(value="")
        self.end_n = tk.StringVar(value="")

        self.compute_mode = tk.StringVar(value="single")
        self.compute_n = tk.StringVar(value="")
        self.compute_start_n = tk.StringVar(value="")
        self.compute_end_n = tk.StringVar(value="")
        self.compute_d = tk.StringVar(value="2")

        self.status_var = tk.StringVar(value="Load a final_results.json file or start a computation.")
        self.show_subset_outputs = tk.BooleanVar(value=True)

        self.ui_queue: queue.Queue = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.run_start_time: float | None = None

        self._build_ui()
        self._try_initial_load()
        self.after(100, self._poll_ui_queue)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.rowconfigure(4, weight=1)

        file_frame = ttk.Frame(self, padding=(10, 10, 10, 6))
        file_frame.grid(row=0, column=0, sticky="ew")
        file_frame.columnconfigure(1, weight=1)
        file_frame.columnconfigure(4, weight=1)

        ttk.Label(file_frame, text="Final results file:").grid(row=0, column=0, padx=(0, 8), sticky="w")
        ttk.Entry(file_frame, textvariable=self.current_file).grid(row=0, column=1, sticky="ew")
        ttk.Button(file_frame, text="Browse...", command=self.browse_results_file).grid(row=0, column=2, padx=6)
        ttk.Button(file_frame, text="Load", command=self.load_file).grid(row=0, column=3, padx=(0, 14))

        ttk.Label(file_frame, text="Checkpoint file:").grid(row=0, column=4, padx=(0, 8), sticky="e")
        ttk.Entry(file_frame, textvariable=self.checkpoint_file).grid(row=0, column=5, sticky="ew")
        ttk.Button(file_frame, text="Browse...", command=self.browse_checkpoint_file).grid(row=0, column=6, padx=6)
        ttk.Button(file_frame, text="Reload", command=self.reload_file).grid(row=0, column=7)

        compute_frame = ttk.LabelFrame(self, text="Compute k(n,d)", padding=(10, 8, 10, 8))
        compute_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        for c in range(16):
            compute_frame.columnconfigure(c, weight=0)
        compute_frame.columnconfigure(15, weight=1)

        ttk.Label(compute_frame, text="Mode:").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(compute_frame, text="Single n", variable=self.compute_mode, value="single").grid(
            row=0, column=1, sticky="w", padx=(4, 8)
        )
        ttk.Radiobutton(compute_frame, text="Range of n", variable=self.compute_mode, value="range").grid(
            row=0, column=2, sticky="w", padx=(0, 16)
        )

        ttk.Label(compute_frame, text="d:").grid(row=0, column=3, sticky="w")
        ttk.Entry(compute_frame, width=8, textvariable=self.compute_d).grid(row=0, column=4, padx=(4, 16), sticky="w")

        ttk.Label(compute_frame, text="n:").grid(row=0, column=5, sticky="w")
        ttk.Entry(compute_frame, width=10, textvariable=self.compute_n).grid(row=0, column=6, padx=(4, 16), sticky="w")

        ttk.Label(compute_frame, text="start_n:").grid(row=0, column=7, sticky="w")
        ttk.Entry(compute_frame, width=10, textvariable=self.compute_start_n).grid(row=0, column=8, padx=(4, 16), sticky="w")

        ttk.Label(compute_frame, text="end_n:").grid(row=0, column=9, sticky="w")
        ttk.Entry(compute_frame, width=10, textvariable=self.compute_end_n).grid(row=0, column=10, padx=(4, 16), sticky="w")

        ttk.Label(compute_frame, text="Checkpoint interval (s):").grid(row=0, column=11, sticky="w")
        ttk.Entry(compute_frame, width=10, textvariable=self.checkpoint_interval).grid(
            row=0, column=12, padx=(4, 16), sticky="w"
        )

        self.compute_button = ttk.Button(compute_frame, text="Compute", command=self.start_compute)
        self.compute_button.grid(row=0, column=13, padx=(0, 6))

        self.open_structure_button = ttk.Button(
            compute_frame,
            text="Display structure for selected row",
            command=self.display_selected_structure,
        )
        self.open_structure_button.grid(row=0, column=14, padx=(0, 6))

        filter_frame = ttk.LabelFrame(self, text="Filters", padding=(10, 8, 10, 8))
        filter_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        for c in range(9):
            filter_frame.columnconfigure(c, weight=0)
        filter_frame.columnconfigure(8, weight=1)

        ttk.Label(filter_frame, text="d:").grid(row=0, column=0, sticky="w")
        ttk.Entry(filter_frame, width=8, textvariable=self.filter_d).grid(row=0, column=1, padx=(4, 16), sticky="w")
        ttk.Label(filter_frame, text="start_n:").grid(row=0, column=2, sticky="w")
        ttk.Entry(filter_frame, width=10, textvariable=self.start_n).grid(row=0, column=3, padx=(4, 16), sticky="w")
        ttk.Label(filter_frame, text="end_n:").grid(row=0, column=4, sticky="w")
        ttk.Entry(filter_frame, width=10, textvariable=self.end_n).grid(row=0, column=5, padx=(4, 16), sticky="w")
        ttk.Checkbutton(
            filter_frame,
            text="Show subset outputs in structure view when available",
            variable=self.show_subset_outputs,
        ).grid(row=0, column=6, padx=(0, 16), sticky="w")
        ttk.Button(filter_frame, text="Apply", command=self.apply_filters).grid(row=0, column=7, padx=(0, 6))
        ttk.Button(filter_frame, text="Clear", command=self.clear_filters).grid(row=0, column=8, sticky="w")

        table_frame = ttk.LabelFrame(self, text="Stored k(n,d) results", padding=(8, 8, 8, 8))
        table_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 8))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("n", "d", "k", "stored_runtime", "written_at", "has_structure")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("n", text="n")
        self.tree.heading("d", text="d")
        self.tree.heading("k", text="k(n,d)")
        self.tree.heading("stored_runtime", text="Stored runtime (s)")
        self.tree.heading("written_at", text="written_at_epoch")
        self.tree.heading("has_structure", text="Structure")
        self.tree.column("n", width=80, anchor="center")
        self.tree.column("d", width=80, anchor="center")
        self.tree.column("k", width=100, anchor="center")
        self.tree.column("stored_runtime", width=150, anchor="e")
        self.tree.column("written_at", width=180, anchor="e")
        self.tree.column("has_structure", width=100, anchor="center")

        tree_scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        tree_scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self.on_row_select)
        self.tree.bind("<Double-1>", self.on_row_double_click)

        bottom = ttk.Panedwindow(self, orient="horizontal")
        bottom.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 8))

        left_panel = ttk.Frame(bottom)
        right_panel = ttk.Frame(bottom)
        bottom.add(left_panel, weight=2)
        bottom.add(right_panel, weight=3)

        left_panel.columnconfigure(0, weight=1)
        left_panel.rowconfigure(0, weight=1)
        left_panel.rowconfigure(1, weight=1)

        summary_frame = ttk.LabelFrame(left_panel, text="Selection summary", padding=(8, 8, 8, 8))
        summary_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        summary_frame.columnconfigure(0, weight=1)
        summary_frame.rowconfigure(0, weight=1)
        self.summary_text = tk.Text(summary_frame, wrap="word", height=10)
        self.summary_text.grid(row=0, column=0, sticky="nsew")
        summary_scroll = ttk.Scrollbar(summary_frame, orient="vertical", command=self.summary_text.yview)
        self.summary_text.configure(yscrollcommand=summary_scroll.set)
        summary_scroll.grid(row=0, column=1, sticky="ns")

        log_frame = ttk.LabelFrame(left_panel, text="Computation log", padding=(8, 8, 8, 8))
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap="word", height=14)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")

        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(0, weight=1)
        structure_frame = ttk.LabelFrame(right_panel, text="Structure view", padding=(8, 8, 8, 8))
        structure_frame.grid(row=0, column=0, sticky="nsew")
        structure_frame.columnconfigure(0, weight=1)
        structure_frame.rowconfigure(0, weight=1)
        self.structure_text = tk.Text(structure_frame, wrap="none")
        self.structure_text.grid(row=0, column=0, sticky="nsew")
        structure_scroll_y = ttk.Scrollbar(structure_frame, orient="vertical", command=self.structure_text.yview)
        structure_scroll_x = ttk.Scrollbar(structure_frame, orient="horizontal", command=self.structure_text.xview)
        self.structure_text.configure(
            yscrollcommand=structure_scroll_y.set,
            xscrollcommand=structure_scroll_x.set,
        )
        structure_scroll_y.grid(row=0, column=1, sticky="ns")
        structure_scroll_x.grid(row=1, column=0, sticky="ew")

        status_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        status_frame.grid(row=5, column=0, sticky="ew")
        status_frame.columnconfigure(0, weight=1)
        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="ew")

    def _try_initial_load(self):
        if os.path.exists(self.current_file.get()):
            self.load_file()

    def browse_results_file(self):
        path = filedialog.asksaveasfilename(
            title="Select or enter final_results.json",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=os.path.basename(self.current_file.get()) or DEFAULT_FINAL_RESULTS_FILE,
        )
        if path:
            self.current_file.set(path)

    def browse_checkpoint_file(self):
        path = filedialog.asksaveasfilename(
            title="Select or enter checkpoint JSON file",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=os.path.basename(self.checkpoint_file.get()) or DEFAULT_CHECKPOINT_FILE,
        )
        if path:
            self.checkpoint_file.set(path)

    def reload_file(self):
        self.load_file()

    def load_file(self):
        path = self.current_file.get().strip()
        if not path:
            messagebox.showerror("Missing path", "Please provide a path to final_results.json.")
            return
        if not os.path.exists(path):
            self.records = []
            self.best_records = {}
            self.filtered_keys = []
            self.refresh_table([])
            self.summary_text.delete("1.0", tk.END)
            self.structure_text.delete("1.0", tk.END)
            self.status_var.set(f"Results file does not yet exist: {path}")
            return
        try:
            self.records = load_final_results_records(path)
            self.best_records = normalize_records(self.records)
            self.apply_filters()
            self.status_var.set(
                f"Loaded {len(self.records)} raw record(s); displaying {len(self.best_records)} best (n,d) result(s) from {path}."
            )
        except Exception as exc:
            messagebox.showerror("Failed to load file", str(exc))
            self.status_var.set(f"Failed to load file: {exc}")

    def _parse_optional_int(self, value: str, field_name: str):
        value = value.strip()
        if value == "":
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an integer or left empty.") from exc

    def _parse_required_int(self, value: str, field_name: str) -> int:
        value = value.strip()
        if value == "":
            raise ValueError(f"{field_name} is required.")
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an integer.") from exc

    def apply_filters(self):
        try:
            filter_d = self._parse_optional_int(self.filter_d.get(), "d")
            start_n = self._parse_optional_int(self.start_n.get(), "start_n")
            end_n = self._parse_optional_int(self.end_n.get(), "end_n")
        except ValueError as exc:
            messagebox.showerror("Invalid filter", str(exc))
            return

        if start_n is not None and end_n is not None and start_n > end_n:
            messagebox.showerror("Invalid interval", "start_n must be <= end_n.")
            return

        rows = []
        for (n, d), rec in self.best_records.items():
            if filter_d is not None and d != filter_d:
                continue
            if start_n is not None and n < start_n:
                continue
            if end_n is not None and n > end_n:
                continue
            rows.append((n, d, rec))

        rows.sort(key=lambda item: (item[0], item[1]))
        self.filtered_keys = [(n, d) for n, d, _ in rows]
        self.refresh_table(rows)
        self.summary_text.delete("1.0", tk.END)
        self.structure_text.delete("1.0", tk.END)
        self.status_var.set(f"Displaying {len(rows)} result(s).")

    def refresh_table(self, rows):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for idx, (n, d, rec) in enumerate(rows):
            has_structure = "Yes" if rec.get("columns") is not None else "No"
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    n,
                    d,
                    rec.get("k", ""),
                    f"{float(rec.get('total_runtime_seconds', 0.0)):.4f}",
                    f"{float(rec.get('written_at_epoch', 0.0)):.0f}",
                    has_structure,
                ),
            )

    def clear_filters(self):
        self.filter_d.set("")
        self.start_n.set("")
        self.end_n.set("")
        self.apply_filters()

    def get_selected_record(self):
        selection = self.tree.selection()
        if not selection:
            return None
        idx = int(selection[0])
        if idx < 0 or idx >= len(self.filtered_keys):
            return None
        key = self.filtered_keys[idx]
        return self.best_records.get(key)

    def on_row_select(self, event=None):
        rec = self.get_selected_record()
        if rec is not None:
            self.render_summary(rec)

    def on_row_double_click(self, event=None):
        self.display_selected_structure()

    def render_summary(self, rec):
        self.summary_text.delete("1.0", tk.END)
        lines = [
            f"k({rec['n']},{rec['d']}) = {rec['k']}",
            f"Stored runtime: {float(rec.get('total_runtime_seconds', 0.0)):.4f} seconds",
            f"Resumed from checkpoint: {rec.get('resumed_from_checkpoint', False)}",
            f"Stored codewords: {len(rec.get('columns', []))}",
            f"Has subset_outputs: {'Yes' if 'subset_outputs' in rec else 'No'}",
            f"written_at_epoch: {float(rec.get('written_at_epoch', 0.0)):.0f}",
            "",
            "Double-click the row or use the button above to display full structure.",
        ]
        self.summary_text.insert("1.0", "\n".join(lines))

    def display_selected_structure(self):
        rec = self.get_selected_record()
        if rec is None:
            messagebox.showinfo("No selection", "Please select a row first.")
            return
        self.structure_text.delete("1.0", tk.END)
        self.structure_text.insert("1.0", self.build_structure_text(rec))

    def build_structure_text(self, rec):
        n = rec["n"]
        d = rec["d"]
        k = rec["k"]
        columns = rec.get("columns", [])
        lines = [
            f"Detailed structure for k({n},{d}) = {k}",
            "=" * 90,
            f"Stored runtime: {float(rec.get('total_runtime_seconds', 0.0)):.4f} seconds",
            f"Resumed from checkpoint: {rec.get('resumed_from_checkpoint', False)}",
            "",
            "Bulb codewords / detector connections:",
        ]
        for bulb_idx, col in enumerate(columns, start=1):
            lines.append(
                f"  bulb {bulb_idx:>2}: {bitstring(col, k)}   connected to detectors {detectors_for_column(col, k)}"
            )

        if self.show_subset_outputs.get():
            subset_outputs = rec.get("subset_outputs")
            if subset_outputs:
                lines.append("")
                lines.append(f"Stored size-{d} subset detector outputs:")
                for row in subset_outputs:
                    lines.append(f"  {tuple(row['subset'])} -> {row['output']}")
            else:
                c = comb(n, d)
                if c <= 100:
                    lines.append("")
                    lines.append(f"Reconstructed size-{d} subset detector outputs:")
                    for subset in combinations(range(n), d):
                        out = subset_or(columns, subset)
                        human_subset = tuple(i + 1 for i in subset)
                        lines.append(f"  {human_subset} -> {bitstring(out, k)}")
                else:
                    lines.append("")
                    lines.append(
                        f"Subset outputs are not stored for this entry and C({n},{d}) = {c} is large, so they are omitted."
                    )
        return "\n".join(lines)

    def start_compute(self):
        if self.worker_thread is not None and self.worker_thread.is_alive():
            messagebox.showinfo("Computation already running", "Please wait until the current computation finishes.")
            return

        try:
            compute_d = self._parse_required_int(self.compute_d.get(), "d")
            interval = self._parse_required_int(self.checkpoint_interval.get(), "Checkpoint interval")
            if interval <= 0:
                raise ValueError("Checkpoint interval must be a positive integer.")
            mode = self.compute_mode.get()
            if mode == "single":
                compute_n = self._parse_required_int(self.compute_n.get(), "n")
                payload = {
                    "mode": "single",
                    "n": compute_n,
                    "d": compute_d,
                    "interval": interval,
                }
            else:
                start_n = self._parse_required_int(self.compute_start_n.get(), "start_n")
                end_n = self._parse_required_int(self.compute_end_n.get(), "end_n")
                if start_n > end_n:
                    raise ValueError("start_n must be <= end_n.")
                payload = {
                    "mode": "range",
                    "start_n": start_n,
                    "end_n": end_n,
                    "d": compute_d,
                    "interval": interval,
                }
        except ValueError as exc:
            messagebox.showerror("Invalid computation settings", str(exc))
            return

        final_results_path = self.current_file.get().strip()
        checkpoint_path = self.checkpoint_file.get().strip()
        if not final_results_path:
            messagebox.showerror("Missing results file", "Please provide a path for final_results.json.")
            return
        if not checkpoint_path:
            messagebox.showerror("Missing checkpoint file", "Please provide a path for the checkpoint JSON file.")
            return

        os.makedirs(os.path.dirname(final_results_path) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(checkpoint_path) or ".", exist_ok=True)

        payload["final_results_path"] = final_results_path
        payload["checkpoint_path"] = checkpoint_path

        self.log_text.insert(tk.END, "\n" + "=" * 80 + "\n")
        self.log_text.insert(tk.END, f"Starting computation with settings: {payload}\n")
        self.log_text.see(tk.END)

        self.compute_button.state(["disabled"])
        self.run_start_time = time.perf_counter()
        self.status_var.set("Computation started...")

        self.worker_thread = threading.Thread(target=self._run_compute_worker, args=(payload,), daemon=True)
        self.worker_thread.start()

    def _run_compute_worker(self, payload):
        writer = QueueWriter(self.ui_queue)
        try:
            solver.FINAL_RESULTS_FILE = payload["final_results_path"]
            solver.CHECKPOINT_FILE = payload["checkpoint_path"]
            solver.CHECKPOINT_INTERVAL_SECONDS = payload["interval"]

            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                if payload["mode"] == "single":
                    self._compute_single(payload["n"], payload["d"])
                else:
                    self._compute_range(payload["start_n"], payload["end_n"], payload["d"])

            elapsed = time.perf_counter() - self.run_start_time if self.run_start_time is not None else 0.0
            self.ui_queue.put(("done", f"Computation finished in {elapsed:.4f} seconds."))
        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))

    def _compute_single(self, n: int, d: int):
        call_started = time.perf_counter()
        k, columns, recorded_runtime, resumed_from_checkpoint, from_cache = solver.search_optimal_structure_with_resume(n, d)
        call_elapsed = time.perf_counter() - call_started

        if from_cache:
            source = "cached final_results.json"
        elif resumed_from_checkpoint:
            source = "resumed checkpoint and computed"
        else:
            source = "newly computed"

        message = (
            f"Finished k({n},{d}) = {k} | source={source} | "
            f"stored_runtime={recorded_runtime:.4f}s | wall_clock={call_elapsed:.4f}s | columns={len(columns)}"
        )
        print(message)
        self.ui_queue.put(("reload", None))

    def _compute_range(self, start_n: int, end_n: int, d: int):
        if start_n > end_n:
            raise ValueError("start_n must be <= end_n.")
        for n in range(start_n, end_n + 1):
            print("-" * 80)
            print(f"Processing n={n}, d={d}")
            self._compute_single(n, d)

    def _poll_ui_queue(self):
        while True:
            try:
                msg_type, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if msg_type == "log":
                self.log_text.insert(tk.END, payload)
                self.log_text.see(tk.END)
            elif msg_type == "reload":
                self.load_file()
            elif msg_type == "done":
                self.compute_button.state(["!disabled"])
                self.status_var.set(payload)
                self.load_file()
                self.log_text.insert(tk.END, payload + "\n")
                self.log_text.see(tk.END)
            elif msg_type == "error":
                self.compute_button.state(["!disabled"])
                self.status_var.set(f"Computation failed: {payload}")
                self.log_text.insert(tk.END, f"ERROR: {payload}\n")
                self.log_text.see(tk.END)
                messagebox.showerror("Computation failed", payload)

        self.after(100, self._poll_ui_queue)


if __name__ == "__main__":
    app = GroupTestingGui()
    app.mainloop()
