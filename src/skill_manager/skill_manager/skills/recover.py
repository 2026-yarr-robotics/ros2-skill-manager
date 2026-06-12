"""recover — stand ONE fallen cup via the server's async recovery task.

Mirrors the agent's call contract (cup_stack_agent plan_executor_node
`_do_fallen_recovery`) exactly, so operator clicks and LLM interrupts behave
identically:

  1. ensure detection: GET  {base}/fallen-cup/state → detection_running?
                       else POST {base}/fallen-cup/detection/start (409 =
                       already running) and poll state up to
                       DETECTION_WARMUP_S (YOLO weight load).
  2. start task:       POST {base}/fallen-cup/recovery
                       {mode, multi_cup: false, dry_run: false, sim: false}
                       — returns at task START (async server task).
  3. poll completion:  GET /api/robot/status every POLL_S until the
                       `fallen_cup_recovery` task leaves running/stopping:
                       idle → success, failed → fail (/api/robot/task/log).

`multi_cup: false` keeps the contract ONE call = ONE cup — the task does its
own hand-eye sensing and stands the nearest fallen cup it sees (the API takes
no coordinates).  Remaining fallen cups need another click, exactly like the
LLM re-triggering `fallen_recovery` on its next cycle once the post-recovery
hand-eye sample still shows a fallen count > 0.  The candidates list below is
the exo-view sanity check only; it is NOT sent to the server.

NOTE (server side): the recovery task stops skill_api first (MoveItPy
controller contention) and the next pick/pyramid call lazily restarts it —
expect the first pick/pyramid after a recovery to take a few extra seconds.
"""
from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk

from skill_manager import api_client
from skill_manager.skills._base import SkillPanel

# Parity with the agent (plan_executor recovery_* parameter defaults).
RECOVERY_TIMEOUT_S = 240.0
POLL_S = 2.0
DETECTION_WARMUP_S = 15.0


class RecoverPanel(SkillPanel):
    name = 'recover'
    label = 'Recover'

    def build(self) -> None:
        f = self.frame
        ttk.Label(f, text='Recover  (fallen-cup → upright, 1회 = 1컵)',
                  font=('Helvetica', 11, 'bold')).grid(
            row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(f,
                  text='서버 비동기 태스크가 hand-eye로 직접 감지해 가장 가까운\n'
                       '넘어진 컵 1개를 세운다 (좌표 전송 없음). 남은 컵은 다시\n'
                       '클릭 — LLM 인터럽트의 재트리거와 동일한 계약.',
                  font=('Helvetica', 9), foreground='#666',
                  justify='left').grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(2, 6))

        mode_row = ttk.Frame(f)
        mode_row.grid(row=2, column=0, columnspan=2, sticky='w')
        ttk.Label(mode_row, text='mode:').pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value='place')
        for value, text in (
                ('place', 'place (옮겨 세움 — pick 후보로 재사용)'),
                ('drop', 'drop')):
            ttk.Radiobutton(mode_row, text=text, value=value,
                            variable=self.mode_var).pack(side=tk.LEFT, padx=4)

        self.listbox = tk.Listbox(
            f, width=58, height=6, font=('Courier', 10),
            selectmode=tk.SINGLE, activestyle='dotbox')
        self.listbox.grid(row=3, column=0, sticky='ew', pady=(6, 0))
        sb = ttk.Scrollbar(f, orient=tk.VERTICAL,
                           command=self.listbox.yview)
        sb.grid(row=3, column=1, sticky='ns', pady=(6, 0))
        self.listbox.config(yscrollcommand=sb.set)

        self._btn = ttk.Button(f, text='▶  Recover one fallen cup',
                               command=self._on_recover)
        self._btn.grid(row=4, column=0, sticky='w', pady=(6, 0))

        self.build_status_row(row=5)
        self.set_status('ready', 'gray')
        self._busy = False

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

    # ── action (UI thread) ─────────────────────────────────────────────────

    def _on_recover(self) -> None:
        if not self.guard_active():
            return
        if self._busy:
            self.set_status('recovery already running — wait for it', 'orange')
            return
        recover_url = self.manager.api_url('recover')
        status_url = self.manager.api_url('status')
        if not recover_url or not status_url:
            self.manager.log_warn(
                '[RECOVER] recovery/status API url is empty — launch with '
                'localhost:=true or set api_url_recover/api_url_status.')
            self.set_status('URL unset — no POST (see log)', 'red')
            return
        self._busy = True
        self._btn.state(['disabled'])
        mode = self.mode_var.get()
        self.set_status(f'recovery starting (mode={mode})…', 'blue')
        threading.Thread(
            target=self._run_recovery, args=(recover_url, status_url, mode),
            daemon=True).start()

    # ── worker thread (UI updates ONLY via manager.queue_status) ──────────

    def _qs(self, text: str, colour: str) -> None:
        self.manager.queue_status(self.name, text, colour)

    def _run_recovery(self, recover_url: str, status_url: str,
                      mode: str) -> None:
        try:
            text, colour = self._do_recovery(recover_url, status_url, mode)
        except Exception as exc:  # noqa: BLE001 — surface, never kill the UI
            text, colour = f'recovery exception: {exc}', 'red'
        self._qs(text, colour)
        self._busy = False
        try:
            self._btn.state(['!disabled'])
        except Exception:  # noqa: BLE001 — window may be closing
            pass

    def _do_recovery(self, recover_url: str, status_url: str,
                     mode: str) -> tuple[str, str]:
        base = recover_url.rsplit('/recovery', 1)[0]   # …/fallen-cup

        # 1. detection up? (YOLO 서비스 로드가 느리다 — warmup 폴링)
        if not self._ensure_detection(base):
            return 'fallen_cup_detect service unavailable', 'red'

        # 2. start the async task (returns at task START).
        body = {'mode': mode, 'multi_cup': False,
                'dry_run': False, 'sim': False}
        self.manager.log_info(f'[RECOVER] → POST {recover_url} {body}')
        res = api_client.post(recover_url, body,
                              timeout_s=self.manager.api_timeout_s)
        if not res.ok:
            return f'recovery start failed: {res.short}', 'red'

        # 3. poll /api/robot/status until the task leaves running/stopping.
        self._qs('recovery running… (polling /status)', 'blue')
        deadline = time.monotonic() + RECOVERY_TIMEOUT_S
        while time.monotonic() < deadline:
            time.sleep(POLL_S)
            res = api_client.get(status_url, timeout_s=5.0)
            if not res.ok:
                continue                     # transient — retry until deadline
            status = None
            for task in (res.data or {}).get('tasks') or []:
                if task.get('name') == 'fallen_cup_recovery':
                    status = str(task.get('status') or '')
                    break
            if status in ('running', 'stopping'):
                continue
            if status == 'idle':
                return ('✓ recovered 1 cup — 남은 컵이 있으면 다시 클릭',
                        'green')
            if status == 'failed':
                return ('recovery task failed (see /api/robot/task/log)',
                        'red')
            return 'fallen_cup_recovery task not found on server', 'red'
        return f'recovery timed out after {RECOVERY_TIMEOUT_S:.0f}s', 'red'

    def _ensure_detection(self, base: str) -> bool:
        """agent의 _ensure_fallen_detection 미러: state → start → warmup 폴링."""
        state_url = f'{base}/state'
        res = api_client.get(state_url, timeout_s=5.0)
        if res.ok and (res.data or {}).get('detection_running'):
            return True
        self._qs('starting fallen-cup detection (YOLO load)…', 'blue')
        start = api_client.post(f'{base}/detection/start', {},
                                timeout_s=self.manager.api_timeout_s)
        if not start.ok and start.code != 409:   # 409 = already running
            self.manager.log_warn(
                f'[RECOVER] detection start failed: {start.short}')
            return False
        deadline = time.monotonic() + DETECTION_WARMUP_S
        while time.monotonic() < deadline:
            res = api_client.get(state_url, timeout_s=5.0)
            if res.ok and (res.data or {}).get('detection_running'):
                return True
            time.sleep(1.0)
        return False
