"""agent — 자연어 명령을 LLM 에이전트 폐루프로 보내고 진행 로그를 본다.

send_command.sh / 대시보드 명령창과 동일 경로:
  POST {base}/api/robot/command {"text": "<명령>"}
    → rosbridge → /user_command → goal_state_publisher → llm_node(Ollama)
    → plan_executor / pick_node → 로봇 스킬

로그는 bringup 에이전트(:8099)의 GET /agent/log 를 직접 폴링한다 —
cup_stack_agent/logs/<RUN_ID>/ 의 노드별 로그(goal_state_publisher,
llm_node, plan_executor, pick_node)를 ts 커서로 증분 수신 (대시보드의
/ws/agent/log 가 프록시하는 것과 같은 소스).

전제: cup_stack_agent 의 LLM 그룹이 떠 있어야 한다 (start_isaac.sh
WITH_AGENT=true, 또는 cup_stack_agent 에서 WITH_VISION=false ./start.sh
--real-api). /user_command 구독자 수로 떠 있는지 휴리스틱 체크해 경고한다.
GPU 를 Isaac 과 공유하면 LLM decide 1회가 수 분 걸릴 수 있다.
"""
from __future__ import annotations

import json
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
from tkinter import ttk

from skill_manager import api_client
from skill_manager.skills._base import SkillPanel

LOG_POLL_S = 2.0
LOG_LIMIT = 60          # 첫 폴링에서 가져올 tail 줄 수
PRESETS = ("3단 쌓아줘", "2단 쌓아줘")


class AgentPanel(SkillPanel):
    name = 'agent'
    label = 'Agent'

    def build(self) -> None:
        f = self.frame
        ttk.Label(f, text='Agent  (자연어 명령 → LLM 폐루프)',
                  font=('Helvetica', 11, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(f,
                  text='POST /api/robot/command → /user_command → GSP → LLM →'
                       ' executor.\nLLM 그룹(WITH_AGENT)이 떠 있어야 동작 —'
                       ' GPU 공유 시 decide 1회가 수 분 걸릴 수 있다.',
                  font=('Helvetica', 9), foreground='#666',
                  justify='left').grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(2, 6))

        row = ttk.Frame(f)
        row.grid(row=2, column=0, columnspan=3, sticky='ew')
        self.cmd_var = tk.StringVar(value=PRESETS[0])
        entry = ttk.Entry(row, textvariable=self.cmd_var, width=32)
        entry.pack(side=tk.LEFT, padx=(0, 6))
        entry.bind('<Return>', lambda _e: self._on_send())
        ttk.Button(row, text='▶  명령 전송',
                   command=self._on_send).pack(side=tk.LEFT)
        for p in PRESETS:
            ttk.Button(row, text=p, width=10,
                       command=lambda v=p: self.cmd_var.set(v)).pack(
                side=tk.LEFT, padx=2)

        ttk.Label(f, text='agent 루프 로그 (bringup-agent :8099 폴링):',
                  font=('Helvetica', 9)).grid(
            row=3, column=0, columnspan=3, sticky='w', pady=(8, 0))
        self.log = tk.Text(f, width=86, height=14, font=('Courier', 9),
                           state='disabled', wrap='none')
        self.log.grid(row=4, column=0, columnspan=2, sticky='nsew')
        sb = ttk.Scrollbar(f, orient=tk.VERTICAL, command=self.log.yview)
        sb.grid(row=4, column=2, sticky='ns')
        self.log.config(yscrollcommand=sb.set)

        self.build_status_row(row=5)
        self.set_status('ready', 'gray')

        self._busy = False
        self._log_cursor = 0.0
        self._log_run_id: str | None = None
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        # UI 스레드만 Text 위젯을 만진다 — 폴링 스레드는 큐에 쌓고
        # refresh()(UI 틱)가 비운다.
        self._line_q: list[str] = []
        self._line_lock = threading.Lock()

    # ── lifecycle: 패널 활성일 때만 로그 폴링 ────────────────────────────

    def on_activate(self) -> None:
        self._poll_stop.clear()
        if self._poll_thread is None or not self._poll_thread.is_alive():
            self._poll_thread = threading.Thread(
                target=self._poll_log, daemon=True)
            self._poll_thread.start()

    def on_deactivate(self) -> None:
        self._poll_stop.set()

    def refresh(self) -> None:
        with self._line_lock:
            lines, self._line_q = self._line_q, []
        if not lines:
            return
        self.log.config(state='normal')
        for ln in lines:
            self.log.insert(tk.END, ln + '\n')
        # 과도한 누적 방지 (마지막 ~800줄 유지)
        n = int(float(self.log.index('end-1c').split('.')[0]))
        if n > 1000:
            self.log.delete('1.0', f'{n - 800}.0')
        self.log.config(state='disabled')
        self.log.see(tk.END)

    # ── 명령 전송 ──────────────────────────────────────────────────────────

    def _on_send(self) -> None:
        if not self.guard_active():
            return
        if self._busy:
            self.set_status('전송 중 — 잠시 후 다시', 'orange')
            return
        cmd = self.cmd_var.get().strip()
        if not cmd:
            self.set_status('명령이 비어 있음', 'orange')
            return
        url = self.manager.api_url('agent')
        if not url:
            self.set_status('URL 미설정 (api_url_agent)', 'red')
            return
        # /user_command 구독자(=GSP, LLM 그룹)가 없으면 명령이 허공에 사라진다
        if self.manager.count_subscribers('/user_command') == 0:
            self.set_status('⚠ agent LLM 그룹이 안 떠 있음 — WITH_AGENT=true '
                            '또는 cup_stack_agent start.sh 실행 후 재시도',
                            'red')
            return
        self._busy = True
        self.set_status(f'전송 중: {cmd}', 'blue')
        threading.Thread(target=self._send, args=(url, cmd),
                         daemon=True).start()

    def _send(self, url: str, cmd: str) -> None:
        res = api_client.post(url, {'text': cmd},
                              timeout_s=self.manager.api_timeout_s)
        if res.ok:
            self.manager.queue_status(
                self.name,
                f'✓ 전송됨: {cmd} — LLM 플랜 생성 대기 (로그 참조)', 'green')
        else:
            self.manager.queue_status(
                self.name, f'전송 실패: {res.short}', 'red')
        self._busy = False

    # ── agent 로그 폴링 (워커 스레드) ─────────────────────────────────────

    def _poll_log(self) -> None:
        while not self._poll_stop.wait(LOG_POLL_S):
            base = self.manager.api_url('agent_log')
            if not base:
                continue
            q = urllib.parse.urlencode(
                {'since': f'{self._log_cursor:.6f}', 'limit': LOG_LIMIT})
            try:
                req = urllib.request.Request(
                    f'{base}?{q}', headers={'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=4.0) as r:
                    data = json.loads(r.read())
            except Exception:
                continue            # bringup-agent 미가동 등 — 조용히 재시도
            run_id = data.get('run_id')
            if run_id and run_id != self._log_run_id:
                self._log_run_id = run_id
                self._log_cursor = 0.0
                with self._line_lock:
                    self._line_q.append(f'───── run {run_id} ─────')
                continue            # 다음 폴에서 새 run 의 tail 부터
            self._log_cursor = float(data.get('cursor') or self._log_cursor)
            new = data.get('lines') or []
            if not new:
                continue
            out = []
            for e in new:
                ts = time.strftime('%H:%M:%S',
                                   time.localtime(e.get('ts') or 0))
                text = (e.get('text') or '').split('\n')[0][:120]
                out.append(f"{ts} [{e.get('node', '?'):>14s}] {text}")
            with self._line_lock:
                self._line_q.extend(out)
