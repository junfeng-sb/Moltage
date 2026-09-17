"""Temporary validation tool for the XYZ-to-geometry.in transformation."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from moltage.aims.geometry_writer import (
    render_geometry_in,
    write_geometry_in,
)
from moltage.domain.structure import MolecularStructure
from moltage.structure.xyz import XYZParseError, read_xyz


class XYZToGeometryDemo:
    """Thin Tkinter interface around the tested core transformation."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._source_path: Path | None = None
        self._structure: MolecularStructure | None = None

        root.title("Temporary validation tool: XYZ to FHI-aims geometry.in")
        root.minsize(760, 560)

        frame = ttk.Frame(root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        frame.rowconfigure(6, weight=1)
        frame.columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text="XYZ → FHI-aims geometry.in temporary validation tool",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        ttk.Button(frame, text="Select XYZ file", command=self._select_xyz).grid(
            row=1, column=0, sticky="w", pady=(0, 10)
        )

        self._file_text = tk.StringVar(value="File: not loaded")
        self._atom_text = tk.StringVar(value="Atoms: not loaded")
        self._comment_text = tk.StringVar(value="Comment: not loaded")
        ttk.Label(frame, textvariable=self._file_text).grid(
            row=2, column=0, sticky="w"
        )
        ttk.Label(frame, textvariable=self._atom_text).grid(
            row=3, column=0, sticky="w"
        )
        ttk.Label(
            frame,
            textvariable=self._comment_text,
            wraplength=720,
        ).grid(row=4, column=0, sticky="w", pady=(0, 10))

        ttk.Label(frame, text="geometry.in preview:").grid(
            row=5, column=0, sticky="w"
        )
        self._preview = scrolledtext.ScrolledText(
            frame,
            height=24,
            width=92,
            wrap=tk.NONE,
            state=tk.DISABLED,
        )
        self._preview.grid(row=6, column=0, sticky="nsew", pady=(4, 10))

        self._save_button = ttk.Button(
            frame,
            text="Save geometry.in",
            command=self._save_geometry,
            state=tk.DISABLED,
        )
        self._save_button.grid(row=7, column=0, sticky="w")

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
            rendered_geometry = render_geometry_in(structure)
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
        self._comment_text.set(f"Comment: {structure.comment}")
        self._set_preview(rendered_geometry)
        self._save_button.configure(state=tk.NORMAL)

    def _save_geometry(self) -> None:
        if self._structure is None:
            messagebox.showerror(
                "Nothing to save",
                "Select and load a valid XYZ file first.",
                parent=self._root,
            )
            return

        initial_directory = (
            str(self._source_path.parent) if self._source_path is not None else None
        )
        selected_path = filedialog.asksaveasfilename(
            parent=self._root,
            title="Save FHI-aims geometry.in",
            initialdir=initial_directory,
            initialfile="geometry.in",
            defaultextension=".in",
            filetypes=(("FHI-aims geometry", "*.in"), ("All files", "*.*")),
        )
        if not selected_path:
            return

        output_path = Path(selected_path)
        if output_path.exists() and not messagebox.askyesno(
            "Confirm overwrite",
            f"Overwrite existing file?\n{output_path}",
            parent=self._root,
        ):
            return

        try:
            write_geometry_in(self._structure, output_path)
        except OSError as error:
            messagebox.showerror(
                "Unable to save geometry.in",
                str(error),
                parent=self._root,
            )
            return

        messagebox.showinfo(
            "geometry.in saved",
            f"Saved to:\n{output_path}",
            parent=self._root,
        )

    def _clear_result(self) -> None:
        self._source_path = None
        self._structure = None
        self._file_text.set("File: not loaded")
        self._atom_text.set("Atoms: not loaded")
        self._comment_text.set("Comment: not loaded")
        self._set_preview("")
        self._save_button.configure(state=tk.DISABLED)

    def _set_preview(self, text: str) -> None:
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.insert("1.0", text)
        self._preview.configure(state=tk.DISABLED)


def main() -> None:
    root = tk.Tk()
    XYZToGeometryDemo(root)
    root.mainloop()


if __name__ == "__main__":
    main()
