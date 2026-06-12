"""move — direct EE Cartesian move + HOME (operator utility).

Move:  POST /api/robot/move  body = {x, y, z, mode}
  • x, y, z   base_link metres (server clamps absolute targets to the
              workspace limits; relative = delta from the current pose)
  • mode      'absolute' | 'relative'
  The server executes a Doosan /motion/move_line with the standard top-down
  orientation [0, 180, 0].

Read pos:  GET /api/robot/position — prefills the x/y/z entries from the
  live EE pose (404 while bringup is down).

Home:  the REST API has no joint-move endpoint, so HOME calls the Doosan
  service `/dsr01/motion/move_joint` directly (same as the operator's
  `yarr_home` alias: pos [0,0,90,0,90,90] deg, vel/acc 40 — pulls a virtual
  robot out of the [0,…,0] boot singularity). Needs `dsr_msgs2` on the
  PYTHONPATH — run_skill_manager.sh sources the doosan workspace; without it
  the button is disabled with a hint.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from skill_manager import api_client
from skill_manager.skills._base import SkillPanel


class MovePanel(SkillPanel):
    name = 'move'
    label = 'Move'

    def build(self) -> None:
        f = self.frame
        ttk.Label(f, text='Direct EE move  (base_link, metres)',
                  font=('Helvetica', 11, 'bold')).grid(
            row=0, column=0, columnspan=4, sticky='w')
        ttk.Label(
            f, text='absolute: 워크스페이스로 클램프된 좌표로 move_line.   '
                    'relative: 현재 포즈 기준 Δ 이동.',
            font=('Helvetica', 9), foreground='#666').grid(
            row=1, column=0, columnspan=4, sticky='w', pady=(2, 6))

        self._vars = {}
        for col, axis in enumerate(('x', 'y', 'z')):
            ttk.Label(f, text=axis).grid(row=2, column=col, sticky='w')
            var = tk.StringVar(value='0.000')
            self._vars[axis] = var
            ttk.Entry(f, textvariable=var, width=10,
                      font=('Courier', 10)).grid(
                row=3, column=col, sticky='w', padx=(0, 8))

        ttk.Label(f, text='mode').grid(row=2, column=3, sticky='w')
        self.mode_var = tk.StringVar(value='absolute')
        ttk.Combobox(f, textvariable=self.mode_var,
                     values=['absolute', 'relative'], state='readonly',
                     width=9).grid(row=3, column=3, sticky='w')

        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, columnspan=4, sticky='w', pady=(10, 0))
        ttk.Button(btns, text='▶  Move', command=self._on_move).grid(
            row=0, column=0, padx=(0, 6))
        ttk.Button(btns, text='⟳  현재 위치 읽기',
                   command=self._on_read_pos).grid(row=0, column=1, padx=6)
        self.home_btn = ttk.Button(btns, text='⌂  Home (0,0,90,0,90,90)',
                                   command=self._on_home)
        self.home_btn.grid(row=0, column=2, padx=6)
        if not self.manager.home_available():
            self.home_btn.state(['disabled'])

        self.build_status_row(row=5)
        if self.manager.home_available():
            self.set_status('ready', 'gray')
        else:
            self.set_status(
                'ready (Home 비활성 — dsr_msgs2 미발견: doosan ws를 source '
                '하고 재실행)', 'orange')

    # ── helpers ───────────────────────────────────────────────────────────

    def _parse_xyz(self) -> tuple[float, float, float] | None:
        try:
            return tuple(float(self._vars[a].get()) for a in ('x', 'y', 'z'))
        except ValueError:
            self.set_status('⚠ x/y/z must be numbers', 'orange')
            return None

    # ── actions ───────────────────────────────────────────────────────────

    def _on_move(self) -> None:
        if not self.guard_active():
            return
        xyz = self._parse_xyz()
        if xyz is None:
            return
        mode = self.mode_var.get()
        # The server clamps ABSOLUTE targets to the workspace but applies
        # relative deltas UNCLAMPED (domains/robot.py move_to) — and the
        # prefill button writes absolute coordinates into these same
        # entries. Cap relative deltas so a stale 'relative' mode can never
        # replay an absolute coordinate as a half-metre lurch.
        if mode == 'relative' and any(abs(v) > 0.10 for v in xyz):
            self.set_status(
                '⚠ relative Δ > 0.10 m blocked — use absolute', 'orange')
            return
        url = self.manager.api_url('move')
        if not url:
            self.set_status('⚠ api_url_move is empty', 'orange')
            return
        payload = {'x': xyz[0], 'y': xyz[1], 'z': xyz[2], 'mode': mode}
        self.manager.log_info(f'[MOVE] → POST {url} {payload}')
        self.set_status(
            f'→ move {mode} ({xyz[0]:+.3f},{xyz[1]:+.3f},{xyz[2]:+.3f})…',
            '#0055cc')
        threading.Thread(target=self._move_worker, args=(url, payload),
                         daemon=True).start()

    def _move_worker(self, url: str, payload: dict) -> None:
        res = api_client.post(url, payload,
                              timeout_s=self.manager.api_timeout_s)
        if res.ok:
            # Response `position` is the last commanded ABSOLUTE target —
            # after a relative move it is stale (server does not track the
            # implied pose), so show it for absolute moves only.
            pos = (res.data or {}).get('position') or {}
            where = ''
            if pos and payload.get('mode') != 'relative':
                where = (f"({pos.get('x', float('nan')):+.3f},"
                         f"{pos.get('y', float('nan')):+.3f},"
                         f"{pos.get('z', float('nan')):+.3f})")
            elif payload.get('mode') == 'relative':
                where = "(Δ 적용됨 — '현재 위치 읽기'로 확인)"
            self.manager.log_info(f'[MOVE] OK  response={res.data}')
            self.manager.queue_status(
                self.name, f'✓ move OK {where}', '#2a7a2a')
        else:
            self.manager.log_warn(f'[MOVE] {res.kind}: {res.detail}')
            self.manager.queue_status(
                self.name, f'✗ move: {res.short}', '#cc0000')

    def _on_read_pos(self) -> None:
        if not self.guard_active():
            return
        url = self.manager.api_url('position')
        if not url:
            self.set_status('⚠ api_url_position is empty', 'orange')
            return
        self.set_status('→ GET position…', '#0055cc')
        threading.Thread(target=self._pos_worker, args=(url,),
                         daemon=True).start()

    def _pos_worker(self, url: str) -> None:
        res = api_client.get(url, timeout_s=5.0)
        if res.ok and isinstance(res.data, dict):
            # Entry StringVars are only READ by the UI thread on demand;
            # .set() from a worker is the same pattern queue_status relies on.
            for axis in ('x', 'y', 'z'):
                v = res.data.get(axis)
                if v is not None:
                    self._vars[axis].set(f'{float(v):.3f}')
            # The entries now hold an ABSOLUTE pose — force the mode to match
            # so a leftover 'relative' cannot replay it as a huge delta.
            self.mode_var.set('absolute')
            self.manager.queue_status(self.name, '✓ position loaded',
                                      '#2a7a2a')
        else:
            self.manager.log_warn(f'[MOVE] position {res.kind}: {res.detail}')
            self.manager.queue_status(
                self.name, f'✗ position: {res.short} (bringup down?)',
                '#cc0000')

    def _on_home(self) -> None:
        if not self.guard_active():
            return
        if self.manager.go_home():
            self.set_status('→ HOME (move_joint [0,0,90,0,90,90])…',
                            '#0055cc')
        else:
            self.set_status('✗ move_joint service unavailable '
                            '(bringup down?)', '#cc0000')
