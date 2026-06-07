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

    # ── settled-only filter helper ─────────────────────────────────────────

    def build_settled_filter(self, row: int, default: bool = True) -> None:
        """Add a "settled-only" checkbox + a hidden-count label at `row`.

        "settled" == the depth track's KF estimate has converged (position
        1σ ≤ kf_settled_std_m), surfaced as the `[L]` tag in the box label.
        Sets `self.settled_only_var` (BooleanVar, default True) and
        `self.hidden_var` (StringVar the panel updates with the hidden count).
        Toggling re-runs `self.refresh()` so the list updates immediately.
        """
        self.settled_only_var = tk.BooleanVar(value=default)
        self.hidden_var = tk.StringVar(value='')
        fr = ttk.Frame(self.frame)
        fr.grid(row=row, column=0, columnspan=3, sticky='w', pady=(2, 2))
        ttk.Checkbutton(
            fr, text='settled([L])만 보기', variable=self.settled_only_var,
            command=self.refresh).pack(side=tk.LEFT)
        ttk.Label(fr, textvariable=self.hidden_var, foreground='#999',
                  font=('Helvetica', 9)).pack(side=tk.LEFT, padx=(8, 0))

    def settled_only(self) -> bool:
        """Current toggle state (True if the panel has no toggle = default on)."""
        var = getattr(self, 'settled_only_var', None)
        return bool(var.get()) if var is not None else True

    def set_hidden_count(self, hidden: int) -> None:
        # `hidden` is derived from two non-atomic snapshots (the spin thread may
        # mutate _cups between them), so clamp: only show a positive count.
        var = getattr(self, 'hidden_var', None)
        if var is not None:
            var.set(f'(미수렴 {hidden}개 숨김)' if hidden > 0 else '')
