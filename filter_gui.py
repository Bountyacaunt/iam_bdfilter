"""Instagram users filter GUI.

Filters a SQLite database (`Users` table) by privacy, avatar, ASCII-only name,
stop-words on full_name/username, and country (with multilingual mapping).
Writes matching `user_id` values to a plain text file (one per line).
"""

from __future__ import annotations

import queue
import sqlite3
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import ahocorasick

from countries import COUNTRIES, EMPTY_COUNTRY_VALUES, TIERS, build_allowed_set

PRIVACY_ANY = "any"
PRIVACY_OPEN = "open"
PRIVACY_PRIVATE = "private"

AVATAR_ANY = "any"
AVATAR_WITH = "with"
AVATAR_WITHOUT = "without"

PROGRESS_EVERY = 500_000


@dataclass
class FilterConfig:
    db_path: str
    stopwords_path: str
    output_path: str
    privacy: str
    avatar: str
    ascii_only: bool
    check_full_name: bool
    check_username: bool
    checked_country_keys: list[str]
    custom_countries: str


def is_ascii_with_letter(s: str) -> bool:
    if not s:
        return False
    has_letter = False
    for ch in s:
        if ord(ch) > 127:
            return False
        if ch.isalpha():
            has_letter = True
    return has_letter


def load_automaton(path: str) -> tuple[ahocorasick.Automaton, int]:
    A = ahocorasick.Automaton()
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            w = line.strip().lower()
            if w:
                A.add_word(w, w)
                n += 1
    A.make_automaton()
    return A, n


def has_bad_word(automaton: ahocorasick.Automaton, text: str) -> bool:
    try:
        next(automaton.iter(text))
        return True
    except StopIteration:
        return False


class FilterWorker(threading.Thread):
    def __init__(
        self, cfg: FilterConfig, msg_q: queue.Queue, stop_flag: threading.Event
    ):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.q = msg_q
        self.stop_flag = stop_flag

    def log(self, text: str) -> None:
        self.q.put(("log", text))

    def progress(self, processed: int, kept: int, total: int, rate: float) -> None:
        self.q.put(("progress", processed, kept, total, rate))

    def done(self, ok: bool, message: str) -> None:
        self.q.put(("done", ok, message))

    def run(self) -> None:
        try:
            self._run()
        except Exception as e:
            self.done(False, f"Error: {e}")

    def _run(self) -> None:
        cfg = self.cfg

        self.log("Loading stop-words...")
        automaton, n_words = load_automaton(cfg.stopwords_path)
        self.log(f"Loaded {n_words:,} stop-words")

        allowed_countries = build_allowed_set(
            cfg.checked_country_keys, cfg.custom_countries
        )
        if not allowed_countries:
            self.done(False, "No countries selected (and custom field empty)")
            return
        self.log(f"Country variants in filter: {len(allowed_countries):,}")

        self.log("Connecting to database...")
        conn = sqlite3.connect(cfg.db_path)
        conn.execute("PRAGMA cache_size=-1048576")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=2147483648")
        cur = conn.cursor()

        where_parts = []
        if cfg.privacy == PRIVACY_OPEN:
            where_parts.append("is_private=0")
        elif cfg.privacy == PRIVACY_PRIVATE:
            where_parts.append("is_private=1")
        if cfg.avatar == AVATAR_WITH:
            where_parts.append("have_avatar=1")
        elif cfg.avatar == AVATAR_WITHOUT:
            where_parts.append("have_avatar=0")

        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        self.log("Counting rows to scan...")
        cur.execute(f"SELECT COUNT(*) FROM Users{where_sql}")
        total = cur.fetchone()[0]
        self.log(f"Rows to scan: {total:,}")

        query = (
            "SELECT user_id, username, full_name, country_account "
            f"FROM Users{where_sql}"
        )
        self.log(f"Query: {query}")
        cur.execute(query)

        t0 = time.time()
        processed = kept = 0

        with open(cfg.output_path, "w", encoding="ascii", buffering=1024 * 1024) as out:
            for user_id, username, full_name, country in cur:
                if self.stop_flag.is_set():
                    self.log("Stopped by user")
                    break
                processed += 1

                country_norm = (country or "").lower().strip()
                if country_norm in EMPTY_COUNTRY_VALUES:
                    pass_country = False
                else:
                    pass_country = country_norm in allowed_countries
                if not pass_country:
                    if processed % PROGRESS_EVERY == 0:
                        elapsed = time.time() - t0
                        rate = processed / elapsed if elapsed > 0 else 0.0
                        self.progress(processed, kept, total, rate)
                    continue

                if cfg.ascii_only and not is_ascii_with_letter(full_name):
                    if processed % PROGRESS_EVERY == 0:
                        elapsed = time.time() - t0
                        rate = processed / elapsed if elapsed > 0 else 0.0
                        self.progress(processed, kept, total, rate)
                    continue

                bad = False
                if cfg.check_full_name and full_name:
                    if has_bad_word(automaton, full_name.lower()):
                        bad = True
                if not bad and cfg.check_username and username:
                    if has_bad_word(automaton, username.lower()):
                        bad = True
                if bad:
                    if processed % PROGRESS_EVERY == 0:
                        elapsed = time.time() - t0
                        rate = processed / elapsed if elapsed > 0 else 0.0
                        self.progress(processed, kept, total, rate)
                    continue

                out.write(f"{user_id}\n")
                kept += 1

                if processed % PROGRESS_EVERY == 0:
                    elapsed = time.time() - t0
                    rate = processed / elapsed if elapsed > 0 else 0.0
                    self.progress(processed, kept, total, rate)

        conn.close()
        elapsed = time.time() - t0
        rate = processed / elapsed if elapsed > 0 else 0.0
        self.progress(processed, kept, total, rate)
        if self.stop_flag.is_set():
            self.done(
                True, f"Stopped. Kept {kept:,} of {processed:,} in {elapsed:.1f}s"
            )
        else:
            self.done(True, f"Done. Kept {kept:,} of {processed:,} in {elapsed:.1f}s")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("IAM BD Filter")
        root.geometry("820x820")

        self.db_var = tk.StringVar()
        self.sw_var = tk.StringVar()
        self.out_var = tk.StringVar(value=str(Path.cwd() / "ids.txt"))

        self.privacy_var = tk.StringVar(value=PRIVACY_OPEN)
        self.avatar_var = tk.StringVar(value=AVATAR_ANY)
        self.ascii_var = tk.BooleanVar(value=True)
        self.check_full_name_var = tk.BooleanVar(value=True)
        self.check_username_var = tk.BooleanVar(value=True)

        self.country_vars: dict[str, tk.BooleanVar] = {
            key: tk.BooleanVar(value=False) for key in COUNTRIES
        }

        self.msg_q: queue.Queue = queue.Queue()
        self.worker: FilterWorker | None = None
        self.stop_flag = threading.Event()

        self._build_ui()
        self.root.after(150, self._poll_queue)

    def _build_ui(self) -> None:
        files = ttk.LabelFrame(self.root, text="Files")
        files.pack(fill="x", padx=8, pady=6)
        self._file_row(files, "Database:", self.db_var, self._pick_db, 0)
        self._file_row(files, "Stop-words:", self.sw_var, self._pick_sw, 1)
        self._file_row(files, "Output:", self.out_var, self._pick_out, 2)

        filt = ttk.LabelFrame(self.root, text="Account filters")
        filt.pack(fill="x", padx=8, pady=6)

        ttk.Label(filt, text="Privacy:").grid(
            row=0, column=0, sticky="w", padx=6, pady=3
        )
        ttk.Radiobutton(
            filt, text="Any", value=PRIVACY_ANY, variable=self.privacy_var
        ).grid(row=0, column=1, sticky="w", padx=6, pady=3)
        ttk.Radiobutton(
            filt, text="Open only", value=PRIVACY_OPEN, variable=self.privacy_var
        ).grid(row=0, column=2, sticky="w", padx=6, pady=3)
        ttk.Radiobutton(
            filt, text="Private only", value=PRIVACY_PRIVATE, variable=self.privacy_var
        ).grid(row=0, column=3, sticky="w", padx=6, pady=3)

        ttk.Label(filt, text="Avatar:").grid(
            row=1, column=0, sticky="w", padx=6, pady=3
        )
        ttk.Radiobutton(
            filt, text="Any", value=AVATAR_ANY, variable=self.avatar_var
        ).grid(row=1, column=1, sticky="w", padx=6, pady=3)
        ttk.Radiobutton(
            filt, text="With avatar", value=AVATAR_WITH, variable=self.avatar_var
        ).grid(row=1, column=2, sticky="w", padx=6, pady=3)
        ttk.Radiobutton(
            filt, text="No avatar", value=AVATAR_WITHOUT, variable=self.avatar_var
        ).grid(row=1, column=3, sticky="w", padx=6, pady=3)

        ttk.Checkbutton(
            filt,
            text="ASCII-only full_name (latin + at least one letter)",
            variable=self.ascii_var,
        ).grid(row=2, column=0, columnspan=4, sticky="w", padx=6, pady=3)

        ttk.Label(filt, text="Stop-words check in:").grid(
            row=3, column=0, sticky="w", padx=6, pady=3
        )
        ttk.Checkbutton(filt, text="full_name", variable=self.check_full_name_var).grid(
            row=3, column=1, sticky="w", padx=6, pady=3
        )
        ttk.Checkbutton(filt, text="username", variable=self.check_username_var).grid(
            row=3, column=2, sticky="w", padx=6, pady=3
        )

        geo = ttk.LabelFrame(self.root, text="Geo (empty country is always rejected)")
        geo.pack(fill="x", padx=8, pady=6)

        top_btns = ttk.Frame(geo)
        top_btns.pack(fill="x", padx=4, pady=2)
        ttk.Button(
            top_btns, text="Select all", command=lambda: self._toggle_all(True)
        ).pack(side="left", padx=4)
        ttk.Button(
            top_btns, text="Clear all", command=lambda: self._toggle_all(False)
        ).pack(side="left", padx=4)

        notebook = ttk.Notebook(geo)
        notebook.pack(fill="x", padx=6, pady=4)

        for tier_name, keys in TIERS.items():
            tab = ttk.Frame(notebook)
            notebook.add(tab, text=tier_name)

            tier_btns = ttk.Frame(tab)
            tier_btns.pack(fill="x", padx=4, pady=4)
            short_name = tier_name.split("—")[0].strip()
            ttk.Button(
                tier_btns,
                text=f"Select {short_name}",
                command=lambda ks=keys: self._toggle_tier(ks, True),
            ).pack(side="left", padx=4)
            ttk.Button(
                tier_btns,
                text=f"Clear {short_name}",
                command=lambda ks=keys: self._toggle_tier(ks, False),
            ).pack(side="left", padx=4)

            grid = ttk.Frame(tab)
            grid.pack(fill="x", padx=4, pady=2)
            cols = 5
            for i, key in enumerate(keys):
                r, c = divmod(i, cols)
                ttk.Checkbutton(grid, text=key, variable=self.country_vars[key]).grid(
                    row=r, column=c, sticky="w", padx=6, pady=1
                )

        ttk.Label(geo, text="Custom countries (latin, comma-separated):").pack(
            anchor="w", padx=6, pady=(6, 0)
        )
        self.custom_geo_text = tk.Text(geo, height=2)
        self.custom_geo_text.pack(fill="x", padx=6, pady=4)

        run_frame = ttk.Frame(self.root)
        run_frame.pack(fill="x", padx=8, pady=6)
        self.run_btn = ttk.Button(run_frame, text="Run", command=self._on_run)
        self.run_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(
            run_frame, text="Stop", command=self._on_stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=4)

        self.progress = ttk.Progressbar(run_frame, mode="determinate", length=380)
        self.progress.pack(side="left", padx=8, fill="x", expand=True)
        self.progress_label = ttk.Label(run_frame, text="0 / 0")
        self.progress_label.pack(side="left", padx=4)

        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.log_text = tk.Text(log_frame, height=12, state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

    def _file_row(self, parent, label, var, cmd, row) -> None:
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=6, pady=3
        )
        ttk.Entry(parent, textvariable=var, width=70).grid(
            row=row, column=1, padx=6, pady=3, sticky="we"
        )
        ttk.Button(parent, text="Browse...", command=cmd).grid(
            row=row, column=2, padx=6, pady=3
        )
        parent.columnconfigure(1, weight=1)

    def _pick_db(self) -> None:
        path = filedialog.askopenfilename(
            title="Select database file",
            filetypes=[
                ("Database", "*.pb *.db *.sqlite *.sqlite3"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.db_var.set(path)

    def _pick_sw(self) -> None:
        path = filedialog.askopenfilename(
            title="Select stop-words file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.sw_var.set(path)

    def _pick_out(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Select output file",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.out_var.set(path)

    def _toggle_all(self, state: bool) -> None:
        for v in self.country_vars.values():
            v.set(state)

    def _toggle_tier(self, keys: list[str], state: bool) -> None:
        for key in keys:
            if key in self.country_vars:
                self.country_vars[key].set(state)

    def _append_log(self, text: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _on_run(self) -> None:
        db = self.db_var.get().strip()
        sw = self.sw_var.get().strip()
        out = self.out_var.get().strip()
        if not db or not Path(db).exists():
            messagebox.showerror("Error", "Database file not found")
            return
        if not sw or not Path(sw).exists():
            messagebox.showerror("Error", "Stop-words file not found")
            return
        if not out:
            messagebox.showerror("Error", "Output path is empty")
            return

        checked = [k for k, v in self.country_vars.items() if v.get()]
        custom = self.custom_geo_text.get("1.0", "end").strip()
        if not checked and not custom:
            messagebox.showerror(
                "Error", "Select at least one country or fill custom field"
            )
            return

        if not self.check_full_name_var.get() and not self.check_username_var.get():
            if not messagebox.askyesno(
                "Confirm", "Stop-words check is off for both fields. Continue?"
            ):
                return

        cfg = FilterConfig(
            db_path=db,
            stopwords_path=sw,
            output_path=out,
            privacy=self.privacy_var.get(),
            avatar=self.avatar_var.get(),
            ascii_only=self.ascii_var.get(),
            check_full_name=self.check_full_name_var.get(),
            check_username=self.check_username_var.get(),
            checked_country_keys=checked,
            custom_countries=custom,
        )

        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")
        self.progress["value"] = 0
        self.progress_label.config(text="0 / 0")

        self.stop_flag.clear()
        self.worker = FilterWorker(cfg, self.msg_q, self.stop_flag)
        self.worker.start()
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    def _on_stop(self) -> None:
        self.stop_flag.set()
        self.stop_btn.config(state="disabled")

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self.msg_q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1])
                elif kind == "progress":
                    _, processed, kept, total, rate = msg
                    if total > 0:
                        self.progress["maximum"] = total
                        self.progress["value"] = processed
                    self.progress_label.config(
                        text=f"{processed:,} / {total:,} kept {kept:,} ({rate:,.0f}/s)"
                    )
                elif kind == "done":
                    _, ok, message = msg
                    self._append_log(message)
                    self.run_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    if ok:
                        messagebox.showinfo("Finished", message)
                    else:
                        messagebox.showerror("Failed", message)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
