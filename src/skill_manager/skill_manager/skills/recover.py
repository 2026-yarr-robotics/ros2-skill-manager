"""recover — pick a fallen-cup and reorient it (stub).

The depth pipeline already routes `fallen-cup` tracks through the OBB
fallback (no cone fit, since the axis is no longer vertical), so candidates
are simply the alive tracks whose class label parses to `fallen-cup`.  Once
the recover skill API stabilises this panel will resemble pyramid (cup id +
target slot), but for now it's a stub that:
  • lists fallen-cup candidates so the operator can see the data is present
  • disables the action button with a `not implemented yet` label
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from skill_manager.skills._base import SkillPanel


class RecoverPanel(SkillPanel):
    name = 'recover'
    label = 'Recover'
    stub = True

    def build(self) -> None:
        f = self.frame
        ttk.Label(f, text='Recover  (fallen-cup → upright)',
                  font=('Helvetica', 11, 'bold')).grid(
            row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(f,
                  text='Skill API not yet defined — panel is a stub.\n'
                       'Below: live fallen-cup candidates, for sanity-checking '
                       'that the detection class taxonomy is flowing.',
                  font=('Helvetica', 9), foreground='#666').grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(2, 6))


        self.listbox = tk.Listbox(
            f, width=58, height=6, font=('Courier', 10),
            selectmode=tk.SINGLE, activestyle='dotbox')
        self.listbox.grid(row=3, column=0, sticky='ew')
        sb = ttk.Scrollbar(f, orient=tk.VERTICAL,
                           command=self.listbox.yview)
        sb.grid(row=3, column=1, sticky='ns')
        self.listbox.config(yscrollcommand=sb.set)

        btn = ttk.Button(f, text='▶  Recover selected (NOT IMPLEMENTED)',
                         command=self._on_recover, state='disabled')
        btn.grid(row=4, column=0, sticky='w', pady=(6, 0))

        self.build_status_row(row=5)
        self.set_status('stub: action button intentionally disabled.', 'gray')

    def refresh(self) -> None:
        settled_only = self.settled_only()
        cups = self.manager.fallen_candidates(settled_only=settled_only)
        total = len(self.manager.fallen_candidates(settled_only=False))
        self.set_hidden_count(total - len(cups))
        self.listbox.delete(0, tk.END)
        for tid in sorted(cups.keys()):
            c = cups[tid]
            p = c.get('pos')
            if p is None:
                continue
            self.listbox.insert(
                tk.END,
                f"{'[L]' if c.get('locked') else '   '} "
                f"#{tid:>3}  {c.get('color', '?'):<8}  "
                f"({p[0]:+.3f}, {p[1]:+.3f}, {p[2]:+.3f})")

    def _on_recover(self) -> None:
        # Unreachable (button disabled) — guard anyway in case future code
        # enables it during testing.
        self.set_status('recover skill not implemented yet', 'orange')
