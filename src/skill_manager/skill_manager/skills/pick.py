"""pick — pick a single standing cup.

Source data:  depth_digital_twin's /digital_twin/boxes (MarkerArray).  We
extract per-track:
  • box_top sphere    → world (= base_link) position
  • box_labels text   → parses `c=<color>_<class>_…`
The set of pick candidates is the alive tracks whose CLASS is `upright-cup`
AND whose id is NOT in the verifier's /stack_track_ids feed (= cups vision
has already assigned to a stack slot).  By default the list is further gated
to KF-settled tracks only (the `[L]` tag); the "settled([L])만 보기" checkbox
toggles that gate, and the hidden-count label shows how many unsettled cups
are being withheld.

Payload:  POST <pick_api_url>  {x, y, cup_top_z = box_top.z + cup_top_z_offset}
The cup_top_z_offset (default 0.302 m) is the operator-specified bias that
maps our `box_top` z (cup top surface in base) onto the API's `cup_top_z`
field (server then adds its own gripper offset).
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from skill_manager import api_client
from skill_manager.skills._base import SkillPanel


class PickPanel(SkillPanel):
    name = 'pick'
    label = 'Pick'

    def build(self) -> None:
        f = self.frame
        ttk.Label(f, text='Standing cups (upright-cup, NOT stacked)',
                  font=('Helvetica', 11, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 4))

        self.build_settled_filter(row=1)

        self.listbox = tk.Listbox(
            f, width=58, height=8, font=('Courier', 10),
            selectmode=tk.SINGLE, activestyle='dotbox')
        self.listbox.grid(row=2, column=0, columnspan=2, sticky='ew')
        sb = ttk.Scrollbar(f, orient=tk.VERTICAL,
                           command=self.listbox.yview)
        sb.grid(row=2, column=2, sticky='ns')
        self.listbox.config(yscrollcommand=sb.set)

        btns = ttk.Frame(f)
        btns.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(6, 0))
        self.pick_btn = ttk.Button(
            btns, text='▶  Pick selected', command=self._on_pick)
        self.pick_btn.grid(row=0, column=0, padx=(0, 6))
        ttk.Button(btns, text='⟳ Re-scan',
                   command=self.manager.trigger_scan).grid(row=0, column=1)

        self.build_status_row(row=4)
        self._cup_ids: list[int] = []

    # ── refresh ───────────────────────────────────────────────────────────
    def refresh(self) -> None:
        settled_only = self.settled_only()
        cups = self.manager.standing_candidates(settled_only=settled_only)
        total = len(self.manager.standing_candidates(settled_only=False))
        self.set_hidden_count(total - len(cups))
        sel = self.listbox.curselection()
        prev = self._cup_ids[sel[0]] if sel else None
        self.listbox.delete(0, tk.END)
        # Build _cup_ids only from rows actually inserted so its indices stay
        # aligned with listbox rows (a skipped row must not shift the mapping).
        self._cup_ids = []
        for tid in sorted(cups.keys()):
            c = cups[tid]
            pos = c.get('pos')
            if pos is None:
                continue
            self._cup_ids.append(tid)
            self.listbox.insert(
                tk.END,
                f"{'[L]' if c.get('locked') else '   '} "
                f"#{tid:>3}  {c.get('color', '?'):<8}  "
                f"({pos[0]:+.3f}, {pos[1]:+.3f}, {pos[2]:+.3f})")
        if prev is not None and prev in self._cup_ids:
            self.listbox.selection_set(self._cup_ids.index(prev))

    # ── action ────────────────────────────────────────────────────────────
    def _on_pick(self) -> None:
        if not self.guard_active():
            return
        sel = self.listbox.curselection()
        if not sel:
            self.set_status('⚠ select a standing cup first', 'orange')
            return
        tid = self._cup_ids[sel[0]]
        cups = self.manager.standing_candidates(settled_only=self.settled_only())
        if tid not in cups:
            self.set_status(f'⚠ #{tid} no longer a candidate', 'orange')
            return
        pos = cups[tid].get('pos')
        if pos is None:
            self.set_status(f'⚠ #{tid} has no position yet', 'orange')
            return
        offset = self.manager.cup_top_z_offset
        payload = {
            'x':         float(pos[0]),
            'y':         float(pos[1]),
            'cup_top_z': float(pos[2]) + float(offset),
        }
        url = self.manager.api_url('pick')
        self.manager.log_info(
            f'[PICK] #{tid} top=({pos[0]:.3f},{pos[1]:.3f},{pos[2]:.3f})  '
            f'+offset={offset:+.3f}  → POST {url} {payload}')
        self.set_status(f'→ POST pick #{tid}', '#0055cc')
        threading.Thread(
            target=self._worker, args=(tid, url, payload),
            daemon=True).start()

    def _worker(self, tid: int, url: str, payload: dict) -> None:
        res = api_client.post(url, payload,
                              timeout_s=self.manager.api_timeout_s)
        if res.ok:
            self.manager.log_info(
                f'[PICK] #{tid} OK  response={res.data}')
            self.manager.queue_status(self.name,
                                      f'✓ pick #{tid} OK', '#2a7a2a')
        else:
            self.manager.log_warn(
                f'[PICK] #{tid} {res.kind}: {res.detail}')
            self.manager.queue_status(self.name,
                                      f'✗ pick #{tid}: {res.short}', '#cc0000')
