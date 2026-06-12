"""pyramid — pick a standing cup and place it at a fixed pyramid slot.

API:  POST /api/robot/skill/pyramid  body = {x, y, slot}
  • x, y    cup TOP centre in base_link (m) — same as Pick
  • slot    enum  '1l'|'1m'|'1r'|'2l'|'2r'|'3m'
The server's cp / degree / pick_z come from its OWN config
(`GET/POST /api/robot/config/pyramid`); use the update_input skill to keep
that config in sync with the verifier's cp / degree.

Vision-node slot keys (L1_L, L1_M, …, L3_T) are mapped to API keys via
`api_client.SLOT_KEY_TO_API` so the GUI keeps the human-friendly labels.

Source data:
  • standing cup candidates: depth /digital_twin/boxes, upright-cup class,
    not in /stack_track_ids (manager.standing_candidates())
  • vacant slot list: verifier's /stack JSON, slots whose value is null
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from skill_manager import api_client
from skill_manager.skills._base import SkillPanel


_SLOT_ORDER = ['L1_L', 'L1_M', 'L1_R', 'L2_L', 'L2_R', 'L3_T']


class PyramidPanel(SkillPanel):
    name = 'pyramid'
    label = 'Pyramid'

    def build(self) -> None:
        f = self.frame
        # ── source cup picker ──────────────────────────────────────────
        ttk.Label(f, text='Source cup (upright, not stacked)',
                  font=('Helvetica', 11, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w')
        self.listbox = tk.Listbox(
            f, width=58, height=6, font=('Courier', 10),
            selectmode=tk.SINGLE, activestyle='dotbox')
        self.listbox.grid(row=2, column=0, columnspan=2, sticky='ew')
        sb = ttk.Scrollbar(f, orient=tk.VERTICAL,
                           command=self.listbox.yview)
        sb.grid(row=2, column=2, sticky='ns')
        self.listbox.config(yscrollcommand=sb.set)

        # ── target slot picker ─────────────────────────────────────────
        ttk.Label(f, text='Target slot', font=('Helvetica', 11, 'bold')).grid(
            row=3, column=0, sticky='w', pady=(8, 0))
        self.slot_var = tk.StringVar(value='L1_L')
        self.slot_cb = ttk.Combobox(
            f, textvariable=self.slot_var,
            values=_SLOT_ORDER, state='readonly', width=10)
        self.slot_cb.grid(row=3, column=1, sticky='w', pady=(8, 0))
        self.slot_info = tk.StringVar(value='slot status: …')
        ttk.Label(f, textvariable=self.slot_info,
                  font=('Helvetica', 9), foreground='gray').grid(
            row=4, column=0, columnspan=3, sticky='w')

        # ── geometry display (cp/degree read-only) ─────────────────────
        self.geom_info = tk.StringVar(value='cp/degree: pending verifier…')
        ttk.Label(f, textvariable=self.geom_info,
                  font=('Courier', 9), foreground='#444').grid(
            row=5, column=0, columnspan=3, sticky='w', pady=(8, 0))

        # ── action ─────────────────────────────────────────────────────
        ttk.Button(f, text='▶  Place at selected slot',
                   command=self._on_place).grid(
            row=6, column=0, sticky='w', pady=(8, 0))

        self.slot_cb.bind('<<ComboboxSelected>>', lambda *_: self.refresh())
        self.build_status_row(row=7)
        self._cup_ids: list[int] = []

    # ── refresh ───────────────────────────────────────────────────────────
    def refresh(self) -> None:
        # standing cups list
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
            p = c.get('pos')
            if p is None:
                continue
            self._cup_ids.append(tid)
            self.listbox.insert(
                tk.END,
                f"#{tid:>3}  {c.get('color', '?'):<8}  "
                f"({p[0]:+.3f}, {p[1]:+.3f}, {p[2]:+.3f})")
        if prev is not None and prev in self._cup_ids:
            self.listbox.selection_set(self._cup_ids.index(prev))

        # slot status from /stack
        slot_map = self.manager.stack_state()
        sel_slot = self.slot_var.get()
        occ = slot_map.get(sel_slot)
        if occ is None:
            self.slot_info.set(f'slot {sel_slot}: empty (placeable)')
        else:
            self.slot_info.set(
                f'slot {sel_slot}: occupied by {occ!r} (overwrite)')

        # cp/degree from verifier
        cp = self.manager.verifier_cp
        deg = self.manager.verifier_degree
        if cp is None:
            self.geom_info.set('cp/degree: pending verifier…')
        else:
            self.geom_info.set(
                f'cp = ({cp[0]:.3f}, {cp[1]:.3f}, {cp[2]:.3f})   '
                f'degree = {deg:+.1f}°')

    # ── action ────────────────────────────────────────────────────────────
    def _on_place(self) -> None:
        if not self.guard_active():
            return
        sel = self.listbox.curselection()
        if not sel:
            self.set_status('⚠ select a source cup first', 'orange')
            return
        slot = self.slot_var.get()
        if slot not in _SLOT_ORDER:
            self.set_status(f'⚠ invalid slot {slot!r}', 'orange')
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
        api_slot = api_client.SLOT_KEY_TO_API.get(slot)
        if api_slot is None:
            self.set_status(
                f'⚠ slot {slot!r} has no API mapping', 'orange')
            return

        # API spec — POST /api/robot/skill/pyramid  body = {x, y, slot}.
        # Server pulls cp/degree/pick_z from its own /config/pyramid (kept
        # in sync by the update_input skill).
        payload = {
            'x':    float(pos[0]),
            'y':    float(pos[1]),
            'slot': api_slot,
        }
        url = self.manager.api_url('pyramid')
        self.manager.log_info(
            f'[PYRAMID] #{tid} → {slot} (api={api_slot})  '
            f'POST {url} {payload}')
        self.set_status(f'→ POST pyramid #{tid} → {slot}', '#0055cc')

        if not url:
            self.manager.log_warn(
                '[PYRAMID] api_url_pyramid is empty — dry-run only.')
            self.set_status(
                f'(dry-run) pyramid #{tid} → {slot}  URL unset', 'orange')
            return

        threading.Thread(
            target=self._worker, args=(tid, slot, url, payload),
            daemon=True).start()

    def _worker(self, tid: int, slot: str,
                url: str, payload: dict) -> None:
        res = api_client.post(url, payload,
                              timeout_s=self.manager.api_timeout_s)
        if res.ok:
            self.manager.log_info(
                f'[PYRAMID] #{tid}→{slot} OK  response={res.data}')
            self.manager.queue_status(
                self.name, f'✓ pyramid #{tid}→{slot} OK', '#2a7a2a')
        else:
            self.manager.log_warn(
                f'[PYRAMID] #{tid}→{slot} {res.kind}: {res.detail}')
            self.manager.queue_status(
                self.name,
                f'✗ pyramid #{tid}→{slot}: {res.short}', '#cc0000')
