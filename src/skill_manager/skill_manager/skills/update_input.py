"""update_input — sync pyramid config (cp / degree / pick_z) with the server.

Endpoint:  /api/robot/config/pyramid  (GET + POST)

  GET  /api/robot/config/pyramid    → {center:{x,y}, degree, pick_z, slots:{…}}
                                       ⇒  verifier `cp = [center.x, center.y, pick_z]`,
                                          verifier `degree = degree`
                                       The `slots` dict carries the server-computed
                                       per-slot absolute coords (informational —
                                       not pushed into the verifier, which derives
                                       slots geometrically from cp + degree).

  POST /api/robot/config/pyramid    body = {center:{x,y}, degree, pick_z}
                                       ⇒  push the verifier's current cp + degree
                                          INTO the server so /skill/pyramid uses
                                          the same layout the operator sees.

Two buttons:
  ⟳ Pull + Apply       GET → set verifier params (cp, degree)
  ⇪ Push verifier→server  POST verifier cp / degree to the server
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from skill_manager import api_client
from skill_manager.skills._base import SkillPanel


class UpdateInputPanel(SkillPanel):
    name = 'update_input'
    label = 'Update Input'

    def build(self) -> None:
        f = self.frame
        ttk.Label(f, text='Sync pyramid config — verifier ↔ server',
                  font=('Helvetica', 11, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w')

        # current verifier state (cached by manager via 2 Hz GetParameters)
        self.cur_var = tk.StringVar(value='verifier: pending…')
        ttk.Label(f, textvariable=self.cur_var, font=('Courier', 9),
                  foreground='#444').grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(4, 0))

        # last server snapshot
        self.srv_var = tk.StringVar(value='server  : not yet fetched')
        ttk.Label(f, textvariable=self.srv_var, font=('Courier', 9),
                  foreground='#0055cc').grid(
            row=2, column=0, columnspan=3, sticky='w')

        # buttons
        btns = ttk.Frame(f)
        btns.grid(row=3, column=0, columnspan=3, sticky='w', pady=(8, 0))
        ttk.Button(btns, text='⟳ Pull + Apply',
                   command=self._on_pull_apply).grid(
            row=0, column=0, padx=(0, 6))
        ttk.Button(btns, text='Pull only',
                   command=self._on_pull_only).grid(
            row=0, column=1, padx=(0, 6))
        ttk.Button(btns, text='⇪ Push verifier → server',
                   command=self._on_push).grid(row=0, column=2)

        self.build_status_row(row=4)

    # ── refresh ───────────────────────────────────────────────────────────
    def refresh(self) -> None:
        cp = self.manager.verifier_cp
        deg = self.manager.verifier_degree
        if cp is None:
            self.cur_var.set('verifier: pending…')
        else:
            self.cur_var.set(
                f'verifier cp=({cp[0]:+.3f},{cp[1]:+.3f},{cp[2]:+.3f})  '
                f'deg={deg:+.1f}°')

    # ── actions ───────────────────────────────────────────────────────────
    def _on_pull_only(self) -> None:
        if not self.guard_active():
            return
        threading.Thread(target=self._pull_worker, args=(False,),
                         daemon=True).start()

    def _on_pull_apply(self) -> None:
        if not self.guard_active():
            return
        threading.Thread(target=self._pull_worker, args=(True,),
                         daemon=True).start()

    def _on_push(self) -> None:
        if not self.guard_active():
            return
        threading.Thread(target=self._push_worker, daemon=True).start()

    # ── GET /config/pyramid → verifier ───────────────────────────────────
    def _pull_worker(self, apply: bool) -> None:
        url = self.manager.api_url('update_input')
        if not url:
            self.manager.log_warn(
                '[UPDATE_INPUT] api_url_update_input not configured.')
            self.manager.queue_status(
                self.name, '⚠ update_input URL not configured', 'orange')
            return

        self.manager.log_info(f'[UPDATE_INPUT] GET {url}')
        res = api_client.get(url, timeout_s=self.manager.api_timeout_s)
        if not res.ok:
            self.manager.log_warn(
                f'[UPDATE_INPUT] pull failed: {res.kind} {res.detail}')
            self.manager.queue_status(
                self.name, f'✗ pull: {res.short}', '#cc0000')
            return

        data = res.data or {}
        try:
            center = data.get('center') or {}
            cx = float(center['x'])
            cy = float(center['y'])
            deg = float(data['degree'])
            pick_z = float(data['pick_z'])
        except (KeyError, TypeError, ValueError) as e:
            self.manager.log_warn(
                f'[UPDATE_INPUT] unexpected schema {data!r}: {e}')
            self.manager.queue_status(
                self.name, '✗ server schema missing required fields',
                '#cc0000')
            return

        cp = [cx, cy, pick_z]
        n_slots = len((data.get('slots') or {}))
        self.srv_var.set(
            f'server   cp=({cp[0]:+.3f},{cp[1]:+.3f},{cp[2]:+.3f})  '
            f'deg={deg:+.1f}°  slots={n_slots}')
        self.manager.log_info(
            f'[UPDATE_INPUT] pulled cp={cp} degree={deg} '
            f'(slots: {n_slots})')

        if not apply:
            self.manager.queue_status(
                self.name, '✓ pulled (apply skipped)', '#2a7a2a')
            return

        ok = self.manager.apply_verifier_cp_degree(cp, deg)
        if ok:
            self.manager.queue_status(
                self.name, '✓ pulled + applied to verifier', '#2a7a2a')
        else:
            self.manager.queue_status(
                self.name,
                '✗ apply failed — verifier set_parameters unavailable',
                '#cc0000')

    # ── POST /config/pyramid (verifier → server) ──────────────────────────
    def _push_worker(self) -> None:
        url = self.manager.api_url('update_input')
        if not url:
            self.manager.queue_status(
                self.name, '⚠ update_input URL not configured', 'orange')
            return
        cp = self.manager.verifier_cp
        deg = self.manager.verifier_degree
        if cp is None:
            self.manager.queue_status(
                self.name, '⚠ verifier cp/degree not yet known', 'orange')
            return

        # POST schema: {center:{x,y}, degree, pick_z}.  All three are optional
        # in the API; we send all three so the server's config is fully
        # aligned with what the operator sees in the verifier.
        payload = {
            'center': {'x': float(cp[0]), 'y': float(cp[1])},
            'degree': float(deg),
            'pick_z': float(cp[2]),
        }
        self.manager.log_info(
            f'[UPDATE_INPUT] POST {url} {payload}')
        res = api_client.post(url, payload,
                              timeout_s=self.manager.api_timeout_s)
        if res.ok:
            self.manager.log_info(
                f'[UPDATE_INPUT] push OK  response={res.data}')
            self.manager.queue_status(
                self.name, '✓ pushed verifier → server', '#2a7a2a')
        else:
            self.manager.log_warn(
                f'[UPDATE_INPUT] push {res.kind}: {res.detail}')
            self.manager.queue_status(
                self.name, f'✗ push: {res.short}', '#cc0000')
