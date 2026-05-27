"""Shared skill-panel base.

Each concrete skill lives in its own module (pick, pyramid, update_input,
recover, scan) and subclasses `SkillPanel`.  The manager owns one `tk.Frame`
container; only the active skill's panel is `grid()`-ed at a time, so
non-active skills are visually hidden AND non-interactive (the manager also
refuses `execute()` when the skill is not active — defence-in-depth).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skill_manager.skill_manager_node import SkillManager


class SkillPanel:
    name: str = ''        # 'pick' | 'pyramid' | 'update_input' | 'recover' | 'scan'
    label: str = ''       # human-readable for the radio button
    stub: bool = False    # True → panel shows a placeholder, no action

    def __init__(self, manager: 'SkillManager', parent: tk.Widget) -> None:
        self.manager = manager
        self.frame: ttk.Frame = ttk.Frame(parent, padding=8)
        self.status_var = tk.StringVar(value='')

    # ── lifecycle ─────────────────────────────────────────────────────────

    def build(self) -> None:
        """Subclasses build their widgets into self.frame."""
        raise NotImplementedError

    def show(self) -> None:
        self.frame.grid(row=0, column=0, sticky='nsew')
        self.on_activate()

    def hide(self) -> None:
        self.on_deactivate()
        self.frame.grid_remove()

    def on_activate(self) -> None:
        """Called when this skill becomes active.  Default: no-op."""

    def on_deactivate(self) -> None:
        """Called when leaving this skill.  Default: no-op."""

    def refresh(self) -> None:
        """Called by the UI loop every ~300 ms; subclasses can override to
        re-render from manager state.  Default: no-op."""

    # ── gating helper ─────────────────────────────────────────────────────

    def guard_active(self) -> bool:
        """Return True if this skill is currently the active one.  Skills
        call this at the start of every execute() so a button bound before
        the skill became active cannot fire stale work."""
        if not self.manager.is_active(self.name):
            self.set_status(f'⚠ {self.label} is not the active skill', 'orange')
            return False
        return True

    # ── status helper ─────────────────────────────────────────────────────

    def set_status(self, text: str, colour: str = 'gray') -> None:
        self.status_var.set(text)
        self._status_lbl.configure(foreground=colour)

    def build_status_row(self, row: int) -> None:
        """Append a status label to the panel at the given grid row.
        Subclasses call this last from build()."""
        self._status_lbl = ttk.Label(
            self.frame, textvariable=self.status_var, foreground='gray',
            font=('Helvetica', 9))
        self._status_lbl.grid(row=row, column=0, columnspan=4,
                              sticky='w', pady=(8, 0))
