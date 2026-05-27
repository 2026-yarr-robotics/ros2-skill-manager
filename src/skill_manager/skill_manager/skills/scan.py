"""scan — sweep two waypoints, dwell 3 s each so the point cloud accumulates.

Planned flow:  POST <scan_api_url> with {waypoints: [...], dwell_s: 3.0}.
While the robot dwells, depth_digital_twin's accumulating window scoops up
extra observations and locks more tracks.  Whether the server emits a "scan
done" event (so we don't have to poll) is still TBD; the panel keeps the
action button disabled until both questions are settled.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from skill_manager.skills._base import SkillPanel


class ScanPanel(SkillPanel):
    name = 'scan'
    label = 'Scan'
    stub = True

    def build(self) -> None:
        f = self.frame
        ttk.Label(f, text='Scan  (sweep waypoints for point-cloud accumulation)',
                  font=('Helvetica', 11, 'bold')).grid(
            row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(f,
                  text='Skill API + server-side event channel not yet defined '
                       '— panel is a stub.\nThe planned flow is documented in '
                       'skills/scan.py.',
                  font=('Helvetica', 9), foreground='#666',
                  justify='left').grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(2, 6))

        ttk.Button(f, text='▶  Run scan (NOT IMPLEMENTED)',
                   command=self._noop, state='disabled').grid(
            row=2, column=0, sticky='w')

        self.build_status_row(row=3)
        self.set_status('stub: action button intentionally disabled.', 'gray')

    def _noop(self) -> None:
        self.set_status('scan skill not implemented yet', 'orange')
