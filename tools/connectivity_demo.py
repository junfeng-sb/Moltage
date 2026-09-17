"""Temporary text-only validation tool for molecular connectivity inference."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure
from moltage.structure.connectivity import (
    DEFAULT_CONNECTIVITY_MULTIPLIER,
    infer_connectivity,
)
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.xyz import XYZParseError, read_xyz


class ConnectivityDemo:
    """Thin Tkinter interface around the tested connectivity service."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._source_path: Path | None = None
        self._structure: MolecularStructure | None = None

        root.title("Temporary validation tool: molecular connectivity")
        root.minsize(900, 580)

        frame = ttk.Frame(root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        frame.rowconfigure(6, weight=1)
        frame.columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text="Distance-based connectivity temporary validation tool",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Button(frame, text="Select XYZ file", command=self._select_xyz).grid(
            row=1, column=0, sticky="w", pady=(0, 10)
        )
        ttk.Label(frame, text="Multiplier:").grid(
            row=1, column=1, sticky="e", padx=(16, 4), pady=(0, 10)
        )
        self._multiplier = tk.StringVar(
            value=f"{DEFAULT_CONNECTIVITY_MULTIPLIER:.2f}"
        )
        ttk.Entry(frame, textvariable=self._multiplier, width=10).grid(
            row=1, column=2, sticky="w", pady=(0, 10)
        )

        self._file_text = tk.StringVar(value="File: not loaded")
        self._atom_text = tk.StringVar(value="Atoms: not loaded")
        self._bond_text = tk.StringVar(value="Inferred bonds: not calculated")
        ttk.Label(frame, textvariable=self._file_text).grid(
            row=2, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(frame, textvariable=self._atom_text).grid(
            row=3, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(frame, textvariable=self._bond_text).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )

        ttk.Label(frame, text="Zero-based inferred connection report:").grid(
            row=5, column=0, columnspan=3, sticky="w"
        )
        self._report = scrolledtext.ScrolledText(
            frame,
            height=24,
            width=108,
            wrap=tk.NONE,
            state=tk.DISABLED,
        )
        self._report.grid(
            row=6, column=0, columnspan=3, sticky="nsew", pady=(4, 10)
        )

        self._calculate_button = ttk.Button(
            frame,
            text="Calculate connectivity",
            command=self._calculate,
            state=tk.DISABLED,
        )
        self._calculate_button.grid(row=7, column=0, sticky="w")

    def _select_xyz(self) -> None:
        selected_path = filedialog.askopenfilename(
            parent=self._root,
            title="Select an XYZ file",
            filetypes=(("XYZ files", "*.xyz"), ("All files", "*.*")),
        )
        if not selected_path:
            return

        try:
            structure = read_xyz(selected_path)
        except (OSError, UnicodeError, XYZParseError) as error:
            self._clear_result()
            messagebox.showerror(
                "Unable to load XYZ",
                str(error),
                parent=self._root,
            )
            return

        self._source_path = Path(selected_path)
        self._structure = structure
        self._file_text.set(f"File: {self._source_path.name}")
        self._atom_text.set(f"Atoms: {len(structure)}")
        self._bond_text.set("Inferred bonds: not calculated")
        self._set_report("")
        self._calculate_button.configure(state=tk.NORMAL)

    def _calculate(self) -> None:
        if self._structure is None:
            messagebox.showerror(
                "Nothing to calculate",
                "Select and load a valid XYZ file first.",
                parent=self._root,
            )
            return

        try:
            multiplier = float(self._multiplier.get())
            connectivity = infer_connectivity(
                self._structure,
                load_default_covalent_radii(),
                multiplier=multiplier,
            )
        except (TypeError, ValueError) as error:
            self._bond_text.set("Inferred bonds: calculation failed")
            self._set_report("")
            messagebox.showerror(
                "Unable to calculate connectivity",
                str(error),
                parent=self._root,
            )
            return

        self._bond_text.set(f"Inferred bonds: {len(connectivity)}")
        self._set_report(format_connectivity_report(self._structure, connectivity))

    def _clear_result(self) -> None:
        self._source_path = None
        self._structure = None
        self._file_text.set("File: not loaded")
        self._atom_text.set("Atoms: not loaded")
        self._bond_text.set("Inferred bonds: not calculated")
        self._set_report("")
        self._calculate_button.configure(state=tk.DISABLED)

    def _set_report(self, text: str) -> None:
        self._report.configure(state=tk.NORMAL)
        self._report.delete("1.0", tk.END)
        self._report.insert("1.0", text)
        self._report.configure(state=tk.DISABLED)


def format_connectivity_report(
    structure: MolecularStructure,
    connectivity: Connectivity,
) -> str:
    """Format already-computed bonds without applying scientific rules."""

    lines = [
        "Index A | Element A | Index B | Element B | Distance (Å)",
        "--------+-----------+---------+-----------+-------------",
    ]
    lines.extend(
        f"{bond.first_index:7d} | "
        f"{structure[bond.first_index].element:9s} | "
        f"{bond.second_index:7d} | "
        f"{structure[bond.second_index].element:9s} | "
        f"{bond.distance:12.6f}"
        for bond in connectivity
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    root = tk.Tk()
    ConnectivityDemo(root)
    root.mainloop()


if __name__ == "__main__":
    main()
