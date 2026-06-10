"""scan — multi-view "scan & lock" control (perception) + optional robot sweep.

Flow:
  • Start  → cup_fusion_node scan_lock_active=True (perception begins capturing
             at the scan waypoints; arrival is auto-detected by joint match) and,
             if `api_url_scan` is configured, POST to move the arm through the
             waypoints. Arrival timing needs no API↔perception handshake.
  • Pause  → scan_lock_active=False (capture stops; the latched lock is KEPT).
  • Clear  → cup_fusion_node/clear_scan (Trigger): wipe accumulation + lock,
             return to live detection.

The robot-motion endpoint (`api_url_scan`) may be blank (server contract TBD);
while blank the panel still drives the perception side and never POSTs.
"""
from __future__ import annotations

import threading
import tkinter as tk  # noqa: F401  (kept for parity with other panels)
from tkinter import ttk

from skill_manager import api_client
from skill_manager.skills._base import SkillPanel


class ScanPanel(SkillPanel):
    name = 'scan'
    label = 'Scan'

    def build(self) -> None:
        f = self.frame
        ttk.Label(f, text='Scan & Lock  (multi-view accumulate at waypoints)',
                  font=('Helvetica', 11, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(
            f, text='Start: 웨이포인트에서 멀티뷰 누적 → 안정된 lock.   '
                    'Pause: 캡처 정지(lock 유지).   Clear: 라이브 검출 복귀.',
            font=('Helvetica', 9), foreground='#666', justify='left').grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(2, 6))

        ttk.Button(f, text='▶  Start Scan & Lock',
                   command=self._on_start).grid(
            row=2, column=0, sticky='w', padx=(0, 4))
        ttk.Button(f, text='⏸  Pause (keep lock)',
                   command=self._on_pause).grid(
            row=2, column=1, sticky='w', padx=4)
        ttk.Button(f, text='⟲  Clear → live',
                   command=self._on_clear).grid(
            row=2, column=2, sticky='w', padx=4)

        self.build_status_row(row=3)
        self.set_status('ready', 'gray')

    def _on_start(self) -> None:
        if not self.guard_active():
            return
        ok = self.manager.set_scan_lock(True)
        url = self.manager.api_url('scan')
        if url:
            self.manager.log_info(f'[SCAN] → POST {url} {{}}')
            threading.Thread(target=self._post_scan, args=(url,),
                             daemon=True).start()
            self.set_status(
                'scan&lock ACTIVE + robot scan POSTed' if ok
                else 'POSTed scan, fusion param svc unavailable',
                'green' if ok else 'orange')
        else:
            # No endpoint resolved (api_url_scan empty AND _SKILL_PATHS['scan']
            # empty) → perception-only; warn loudly instead of silently no-op.
            self.manager.log_warn(
                '[SCAN] scan API url is empty — perception scan-lock only, no '
                'robot POST. Launch skill_manager with localhost:=true (→ '
                'http://localhost/api/robot/skill/scan) or set '
                'api_url_scan:=<url>.')
            self.set_status(
                'scan&lock ACTIVE (no robot POST — URL unset)' if ok
                else 'fusion param service unavailable',
                'orange' if ok else 'red')

    def _post_scan(self, url: str) -> None:
        res = api_client.post(url, {}, timeout_s=self.manager.api_timeout_s)
        if res.ok:
            self.manager.queue_status(self.name, 'robot scan started', 'green')
        else:
            self.manager.log_warn(f'[SCAN] {res.kind}: {res.detail}')
            self.manager.queue_status(
                self.name, f'robot scan POST failed: {res.short}', 'red')

    def _on_pause(self) -> None:
        if not self.guard_active():
            return
        ok = self.manager.set_scan_lock(False)
        self.set_status('paused (lock kept)' if ok
                        else 'param service unavailable',
                        'orange' if ok else 'red')

    def _on_clear(self) -> None:
        if not self.guard_active():
            return
        ok = self.manager.clear_scan_lock()
        self.set_status('cleared → live' if ok
                        else 'clear service unavailable',
                        'gray' if ok else 'red')
