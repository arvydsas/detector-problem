import json
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from math import comb
from itertools import combinations

DEFAULT_FINAL_RESULTS_FILE = "final_results.json"


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
    o = 0
    for idx in subset:
        o |= columns[idx]
    return o


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


class FinalResultsViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Final Results Viewer for k(n,d)")
        self.geometry("1320x820")
        self.minsize(1080, 700)

        self.records = []
        self.best_records = {}
        self.filtered_keys = []
        self.current_file = tk.StringVar(value=DEFAULT_FINAL_RESULTS_FILE)
        self.filter_d = tk.StringVar(value="")
        self.start_n = tk.StringVar(value="")
        self.end_n = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Load a final_results.json file to begin.")
        self.show_subset_outputs = tk.BooleanVar(value=True)

        self._build_ui()
        self._try_initial_load()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(3, weight=1)

        # Top file bar
        file_frame = ttk.Frame(self, padding=(10, 10, 10, 6))
        file_frame.grid(row=0, column=0, sticky="ew")
        file_frame.columnconfigure(1, weight=1)

        ttk.Label(file_frame, text="Final results file:").grid(row=0, column=0, padx=(0, 8), sticky="w")
        ttk.Entry(file_frame, textvariable=self.current_file).grid(row=0, column=1, sticky="ew")
        ttk.Button(file_frame, text="Browse...", command=self.browse_file).grid(row=0, column=2, padx=6)
        ttk.Button(file_frame, text="Load", command=self.load_file).grid(row=0, column=3, padx=6)
        ttk.Button(file_frame, text="Reload", command=self.reload_file).grid(row=0, column=4)

        # Filters
        filter_frame = ttk.LabelFrame(self, text="Filters", padding=(10, 8, 10, 8))
        filter_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        for c in range(9):
            filter_frame.columnconfigure(c, weight=0)
        filter_frame.columnconfigure(8, weight=1)

        ttk.Label(filter_frame, text="d:").grid(row=0, column=0, sticky="w")
        ttk.Entry(filter_frame, width=8, textvariable=self.filter_d).grid(row=0, column=1, padx=(4, 16), sticky="w")

        ttk.Label(filter_frame, text="start_n:").grid(row=0, column=2, sticky="w")
        ttk.Entry(filter_frame, width=10, textvariable=self.start_n).grid(row=0, column=3, padx=(4, 16), sticky="w")

        ttk.Label(filter_frame, text="end_n:").grid(row=0, column=4, sticky="w")
        ttk.Entry(filter_frame, width=10, textvariable=self.end_n).grid(row=0, column=5, padx=(4, 16), sticky="w")

        ttk.Checkbutton(filter_frame, text="Show subset outputs in structure view when available", variable=self.show_subset_outputs).grid(row=0, column=6, padx=(0, 16), sticky="w")
        ttk.Button(filter_frame, text="Apply", command=self.apply_filters).grid(row=0, column=7, padx=(0, 6))
        ttk.Button(filter_frame, text="Clear", command=self.clear_filters).grid(row=0, column=8, sticky="w")

        # Table
        table_frame = ttk.LabelFrame(self, text="Stored k(n,d) results", padding=(8, 8, 8, 8))
        table_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 8))
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

        # Bottom split: summary + structure
        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.grid(row=3, column=0, sticky="nsew")
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=2)
        bottom.rowconfigure(0, weight=1)

        summary_frame = ttk.LabelFrame(bottom, text="Selection summary", padding=(8, 8, 8, 8))
        summary_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        summary_frame.columnconfigure(0, weight=1)
        summary_frame.rowconfigure(0, weight=1)

        self.summary_text = tk.Text(summary_frame, wrap="word", height=12)
        self.summary_text.grid(row=0, column=0, sticky="nsew")
        summary_scroll = ttk.Scrollbar(summary_frame, orient="vertical", command=self.summary_text.yview)
        self.summary_text.configure(yscrollcommand=summary_scroll.set)
        summary_scroll.grid(row=0, column=1, sticky="ns")

        structure_frame = ttk.LabelFrame(bottom, text="Structure view", padding=(8, 8, 8, 8))
        structure_frame.grid(row=0, column=1, sticky="nsew")
        structure_frame.columnconfigure(0, weight=1)
        structure_frame.rowconfigure(0, weight=1)

        self.structure_text = tk.Text(structure_frame, wrap="none")
        self.structure_text.grid(row=0, column=0, sticky="nsew")
        structure_scroll_y = ttk.Scrollbar(structure_frame, orient="vertical", command=self.structure_text.yview)
        structure_scroll_x = ttk.Scrollbar(structure_frame, orient="horizontal", command=self.structure_text.xview)
        self.structure_text.configure(yscrollcommand=structure_scroll_y.set, xscrollcommand=structure_scroll_x.set)
        structure_scroll_y.grid(row=0, column=1, sticky="ns")
        structure_scroll_x.grid(row=1, column=0, sticky="ew")

        # Bottom status bar
        status_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        status_frame.grid(row=4, column=0, sticky="ew")
        status_frame.columnconfigure(0, weight=1)

        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="ew")
        ttk.Button(status_frame, text="Display structure for selected row", command=self.display_selected_structure).grid(row=0, column=1, padx=(10, 0))

    def _try_initial_load(self):
        if os.path.exists(self.current_file.get()):
            self.load_file()

    def browse_file(self):
        path = filedialog.askopenfilename(
            title="Select final_results.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.current_file.set(path)

    def reload_file(self):
        self.load_file()

    def load_file(self):
        path = self.current_file.get().strip()
        if not path:
            messagebox.showerror("Missing path", "Please provide a path to final_results.json.")
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

    def _parse_optional_int(self, value, field_name):
        value = value.strip()
        if value == "":
            return None
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{field_name} must be an integer or left empty.")

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

        rows.sort(key=lambda x: (x[0], x[1]))
        self.filtered_keys = [(n, d) for n, d, _ in rows]

        for item in self.tree.get_children():
            self.tree.delete(item)

        for idx, (n, d, rec) in enumerate(rows):
            has_structure = "Yes" if rec.get("columns") else "No"
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

        self.summary_text.delete("1.0", tk.END)
        self.structure_text.delete("1.0", tk.END)

        self.status_var.set(f"Displaying {len(rows)} result(s).")

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
        if rec is None:
            return
        self.render_summary(rec)

    def on_row_double_click(self, event=None):
        self.display_selected_structure()

    def render_summary(self, rec):
        self.summary_text.delete("1.0", tk.END)
        lines = []
        lines.append(f"k({rec['n']},{rec['d']}) = {rec['k']}")
        lines.append(f"Stored runtime: {float(rec.get('total_runtime_seconds', 0.0)):.4f} seconds")
        lines.append(f"Resumed from checkpoint: {rec.get('resumed_from_checkpoint', False)}")
        lines.append(f"Stored codewords: {len(rec.get('columns', []))}")
        lines.append(f"Has subset_outputs: {'Yes' if 'subset_outputs' in rec else 'No'}")
        lines.append(f"written_at_epoch: {float(rec.get('written_at_epoch', 0.0)):.0f}")
        lines.append("")
        lines.append("Double-click the row or use the button below to display full structure.")
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

        lines = []
        lines.append(f"Detailed structure for k({n},{d}) = {k}")
        lines.append("=" * 90)
        lines.append(f"Stored runtime: {float(rec.get('total_runtime_seconds', 0.0)):.4f} seconds")
        lines.append(f"Resumed from checkpoint: {rec.get('resumed_from_checkpoint', False)}")
        lines.append("")
        lines.append("Bulb codewords / detector connections:")
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
                        o = subset_or(columns, subset)
                        human_subset = tuple(i + 1 for i in subset)
                        lines.append(f"  {human_subset} -> {bitstring(o, k)}")
                else:
                    lines.append("")
                    lines.append(
                        f"Subset outputs are not stored for this entry and C({n},{d}) = {c} is large, so they are omitted."
                    )

        return "\n".join(lines)


if __name__ == "__main__":
    app = FinalResultsViewer()
    app.mainloop()
