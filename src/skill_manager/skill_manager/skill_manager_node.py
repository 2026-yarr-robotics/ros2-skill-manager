"""skill_manager_node — single-skill gateway between operator + robot API.

A tkinter window with a radio for the active skill and a container that shows
only the active skill's panel.  Skills (modules under `skills/`) read shared
ROS state from the manager (cup tracks, stack occupancy, verifier cp/degree)
and POST to the robot API via `api_client`.  Only the active skill can
execute — others' buttons are hidden (panel grid_removed) AND each skill
re-checks `manager.is_active(self.name)` before doing work.

ROS interfaces:
  Subscribers:
    /digital_twin/boxes        (visualization_msgs/MarkerArray) — depth tracks
    /stack_track_ids           (std_msgs/Int32MultiArray) — verifier stacked ids
    /stack                     (std_msgs/String JSON) — verifier slot→color
  Service clients:
    /<verifier_node>/get_parameters, /<verifier_node>/set_parameters
    /<scan_service>            (std_srvs/Trigger) — depth re-scan trigger
"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

import rclpy
from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
from rcl_interfaces.srv import GetParameters, SetParameters
from rclpy.node import Node
from std_msgs.msg import Int32MultiArray, String
from std_srvs.srv import Trigger
from visualization_msgs.msg import Marker, MarkerArray

from skill_manager.skills.pick import PickPanel
from skill_manager.skills.pyramid import PyramidPanel
from skill_manager.skills.update_input import UpdateInputPanel
from skill_manager.skills.recover import RecoverPanel
from skill_manager.skills.scan import ScanPanel


# Color tokens depth_digital_twin writes into the box_labels text.  Kept in
# sync with vision-node's bridge.
_KNOWN_COLORS = {
    'red', 'orange', 'yellow', 'green', 'blue', 'purple',
    'white', 'black', 'gray', 'unknown',
}
_KNOWN_CLASSES = {'upright-cup', 'fallen-cup', 'cup'}


def _parse_label(text: str) -> tuple[str, str, bool]:
    """Extract (color, class_name, settled) from a depth box_labels text.

    The returned bool comes from the "[L]_" prefix. NOTE: since the depth node
    moved from scan-and-lock to a Kalman filter, "[L]" no longer means the pose
    is frozen — it means the KF estimate is SETTLED (position 1σ ≤
    kf_settled_std_m, ~6 mm). It keeps updating; it is not locked in place.

    Label format examples:
      "[L]_#7_c=red_upright-cup_0.87_r=12mm_(0.31,0.04,0.18)"
      "#3_c=blue_fallen-cup_0.65_[lie]_(0.42,0.10,0.05)"
    """
    if not text:
        return 'unknown', 'unknown', False
    locked = text.startswith('[L]')
    color, cls = 'unknown', 'unknown'
    for tok in text.replace('\n', '_').split('_'):
        t = tok.strip().lower()
        if t.startswith('c=') and t[2:] in _KNOWN_COLORS:
            color = t[2:]
        elif t in _KNOWN_COLORS and color == 'unknown':
            color = t
        if t in _KNOWN_CLASSES:
            cls = t
    return color, cls, locked


# ── ROS node ────────────────────────────────────────────────────────────────

class SkillManager(Node):
    SKILL_CLASSES = [PickPanel, PyramidPanel, UpdateInputPanel,
                     RecoverPanel, ScanPanel]

    def __init__(self) -> None:
        super().__init__('skill_manager')

        # ── parameters ────────────────────────────────────────────────────
        self.declare_parameter('boxes_topic', '/digital_twin/boxes')
        self.declare_parameter(
            'stack_track_ids_topic', '/stack_track_ids')
        self.declare_parameter('stack_topic', '/stack')
        self.declare_parameter('verifier_node', 'cup_occupancy_verifier')
        self.declare_parameter(
            'trigger_scan_service', '/point_cloud_node/trigger_scan')

        # Real endpoints (from https://yarr-api.simplyimg.com/api/robot/openapi.json).
        # recover / scan endpoints DO NOT EXIST on the server today, so those
        # two are left blank — RecoverPanel / ScanPanel are stubs that show
        # candidate data but never POST.
        _API_ROOT = 'https://yarr-api.simplyimg.com/api/robot'
        self.declare_parameter(
            'api_url_pick',         f'{_API_ROOT}/skill/pick')
        self.declare_parameter(
            'api_url_pyramid',      f'{_API_ROOT}/skill/pyramid')
        self.declare_parameter(
            'api_url_update_input', f'{_API_ROOT}/config/pyramid')
        self.declare_parameter('api_url_recover', '')   # not exposed by server
        self.declare_parameter('api_url_scan',    '')   # not exposed by server
        self.declare_parameter('api_timeout_s', 15.0)
        # Bias applied to box_top.z before sending as cup_top_z (operator spec).
        self.declare_parameter('cup_top_z_offset', 0.302)

        boxes_topic = str(self.get_parameter('boxes_topic').value)
        stack_ids_topic = str(self.get_parameter('stack_track_ids_topic').value)
        stack_topic = str(self.get_parameter('stack_topic').value)
        verifier = str(self.get_parameter('verifier_node').value).strip('/')
        scan_svc = str(self.get_parameter('trigger_scan_service').value)

        self._api_urls: dict[str, str] = {
            'pick': str(self.get_parameter('api_url_pick').value),
            'pyramid': str(self.get_parameter('api_url_pyramid').value),
            'update_input': str(
                self.get_parameter('api_url_update_input').value),
            'recover': str(self.get_parameter('api_url_recover').value),
            'scan': str(self.get_parameter('api_url_scan').value),
        }
        self.api_timeout_s: float = float(
            self.get_parameter('api_timeout_s').value)
        self.cup_top_z_offset: float = float(
            self.get_parameter('cup_top_z_offset').value)

        # ── shared state (read by panels in the UI thread) ────────────────
        self._state_lock = threading.Lock()
        self._cups: dict[int, dict] = {}        # tid → pos/color/cls/locked
        self._stacked_ids: set[int] = set()
        self._stack_slot_map: dict[str, Any] = {}
        self.verifier_cp: list[float] | None = None
        self.verifier_degree: float = 0.0

        # Status messages from worker threads → UI.
        self._status_q: queue.Queue[tuple[str, str, str]] = queue.Queue()

        # ── pub/sub/clients ───────────────────────────────────────────────
        self.create_subscription(MarkerArray, boxes_topic, self._on_boxes, 10)
        self.create_subscription(Int32MultiArray, stack_ids_topic,
                                 self._on_stack_ids, 10)
        self.create_subscription(String, stack_topic, self._on_stack_json, 10)

        self._scan_cli = self.create_client(Trigger, scan_svc)
        self._get_param_cli = self.create_client(
            GetParameters, f'/{verifier}/get_parameters')
        self._set_param_cli = self.create_client(
            SetParameters, f'/{verifier}/set_parameters')

        # Poll the verifier's cp/degree periodically (catches pose_tuner edits).
        self.create_timer(2.0, self._poll_verifier)

        self.get_logger().info(
            f'skill_manager ready  boxes={boxes_topic}  '
            f'stack_ids={stack_ids_topic}  stack={stack_topic}  '
            f'verifier=/{verifier}  scan_svc={scan_svc}')

    # ── ROS callbacks ─────────────────────────────────────────────────────

    def _on_boxes(self, msg: MarkerArray) -> None:
        with self._state_lock:
            for m in msg.markers:
                if m.action == Marker.DELETEALL:
                    self._cups.clear()
                    continue
                if m.action == Marker.DELETE:
                    if m.ns in ('box_top', 'boxes', 'box_labels'):
                        self._cups.pop(m.id, None)
                    continue
                entry = self._cups.setdefault(m.id, {})
                if m.ns == 'box_top':
                    entry['pos'] = (float(m.pose.position.x),
                                    float(m.pose.position.y),
                                    float(m.pose.position.z))
                elif m.ns == 'box_labels':
                    color, cls, locked = _parse_label(m.text)
                    entry['color'] = color
                    entry['class'] = cls
                    entry['locked'] = locked
                    entry['label'] = m.text

    def _on_stack_ids(self, msg: Int32MultiArray) -> None:
        with self._state_lock:
            self._stacked_ids = {int(x) for x in msg.data}

    def _on_stack_json(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        with self._state_lock:
            self._stack_slot_map = data

    # ── verifier param poll ───────────────────────────────────────────────

    def _poll_verifier(self) -> None:
        if not self._get_param_cli.service_is_ready():
            return
        req = GetParameters.Request(names=['cp', 'degree'])
        fut = self._get_param_cli.call_async(req)
        fut.add_done_callback(self._on_verifier_params)

    def _on_verifier_params(self, fut) -> None:
        try:
            resp = fut.result()
        except Exception:
            return
        if resp is None or len(resp.values) < 2:
            return
        try:
            cp = list(resp.values[0].double_array_value)
            deg = float(resp.values[1].double_value)
        except Exception:
            return
        if len(cp) == 3:
            with self._state_lock:
                self.verifier_cp = cp
                self.verifier_degree = deg

    # ── shared-state accessors (panels read these from UI thread) ────────

    def standing_candidates(self, settled_only: bool = True) -> dict[int, dict]:
        """Alive `upright-cup` tracks NOT currently in any stack slot.

        When `settled_only` (default), also require the depth track's KF
        estimate to be settled — i.e. the `[L]` tag (position 1σ ≤
        kf_settled_std_m, ~6 mm) parsed into `locked`. This keeps the robot
        from acting on a not-yet-converged (high-uncertainty) pose. Pass
        settled_only=False to include unsettled tracks too.
        """
        with self._state_lock:
            stacked = set(self._stacked_ids)
            out = {}
            for tid, c in self._cups.items():
                if c.get('class') != 'upright-cup':
                    continue
                if tid in stacked:
                    continue
                if 'pos' not in c:
                    continue
                if settled_only and not c.get('locked'):
                    continue
                out[tid] = dict(c)
            return out

    def fallen_candidates(self, settled_only: bool = True) -> dict[int, dict]:
        """Alive `fallen-cup` tracks. `settled_only` gates on the `[L]`
        (KF-settled) tag, same as standing_candidates."""
        with self._state_lock:
            out = {}
            for tid, c in self._cups.items():
                if c.get('class') != 'fallen-cup':
                    continue
                if 'pos' not in c:
                    continue
                if settled_only and not c.get('locked'):
                    continue
                out[tid] = dict(c)
            return out

    def stack_state(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._stack_slot_map)

    # ── actions exposed to panels ─────────────────────────────────────────

    def api_url(self, skill_name: str) -> str:
        return self._api_urls.get(skill_name, '')

    def trigger_scan(self) -> None:
        if not self._scan_cli.wait_for_service(timeout_sec=0.3):
            self.get_logger().warn('trigger_scan: service not available')
            return
        self._scan_cli.call_async(Trigger.Request())
        self.get_logger().info('trigger_scan called')

    def apply_verifier_cp_degree(self, cp: list[float], deg: float) -> bool:
        """Push cp+degree to the verifier via SetParameters.  Returns False
        if the service is unavailable."""
        if not self._set_param_cli.service_is_ready():
            self._set_param_cli.wait_for_service(timeout_sec=0.5)
            if not self._set_param_cli.service_is_ready():
                return False
        req = SetParameters.Request(parameters=[
            Parameter(name='cp', value=ParameterValue(
                type=ParameterType.PARAMETER_DOUBLE_ARRAY,
                double_array_value=[float(v) for v in cp])),
            Parameter(name='degree', value=ParameterValue(
                type=ParameterType.PARAMETER_DOUBLE,
                double_value=float(deg))),
        ])
        self._set_param_cli.call_async(req)
        return True

    # ── status routing (worker threads → UI) ─────────────────────────────

    def queue_status(self, skill_name: str, text: str, colour: str) -> None:
        """Thread-safe — UI loop drains the queue every refresh tick."""
        self._status_q.put((skill_name, text, colour))

    def drain_status(self) -> list[tuple[str, str, str]]:
        out = []
        while True:
            try:
                out.append(self._status_q.get_nowait())
            except queue.Empty:
                break
        return out

    # ── skill-active gating ───────────────────────────────────────────────

    def set_active_skill(self, name: str) -> None:
        with self._state_lock:
            self._active_skill = name

    def is_active(self, name: str) -> bool:
        with self._state_lock:
            return getattr(self, '_active_skill', None) == name

    # ── logging shortcuts ─────────────────────────────────────────────────

    def log_info(self, msg: str) -> None:
        self.get_logger().info(msg)

    def log_warn(self, msg: str) -> None:
        self.get_logger().warn(msg)

    def log_error(self, msg: str) -> None:
        self.get_logger().error(msg)


# ── tkinter UI ───────────────────────────────────────────────────────────────

class SkillManagerUI:
    REFRESH_MS = 300

    def __init__(self, mgr: SkillManager) -> None:
        self.mgr = mgr
        self.root = tk.Tk()
        self.root.title('Skill Manager')
        self.root.resizable(False, False)

        outer = ttk.Frame(self.root, padding=12)
        outer.grid(row=0, column=0, sticky='nsew')

        # ── radio row ──────────────────────────────────────────────────
        radio_row = ttk.LabelFrame(outer, text='Active skill', padding=6)
        radio_row.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        self.active_var = tk.StringVar(value='')

        # ── panel container (only the active panel is grid()-ed) ────────
        self.panel_box = ttk.LabelFrame(outer, text='Skill panel', padding=6)
        self.panel_box.grid(row=1, column=0, sticky='nsew')

        # Build panels
        self.panels: dict[str, Any] = {}
        for cls in SkillManager.SKILL_CLASSES:
            p = cls(mgr, self.panel_box)
            p.build()
            self.panels[p.name] = p

        # Build radios pointing at the panels' name
        for col, cls in enumerate(SkillManager.SKILL_CLASSES):
            rb = ttk.Radiobutton(
                radio_row, text=cls.label, value=cls.name,
                variable=self.active_var,
                command=self._on_radio)
            rb.grid(row=0, column=col, padx=6, sticky='w')

        # Default to pick
        self.active_var.set('pick')
        self._on_radio()

        # global status (bottom)
        self.global_status = tk.StringVar(value='ready')
        ttk.Label(outer, textvariable=self.global_status,
                  font=('Helvetica', 9), foreground='gray').grid(
            row=2, column=0, sticky='w', pady=(8, 0))

        self.root.after(self.REFRESH_MS, self._tick)

    # ── lifecycle ─────────────────────────────────────────────────────────

    def _on_radio(self) -> None:
        active = self.active_var.get()
        self.mgr.set_active_skill(active)
        for name, panel in self.panels.items():
            if name == active:
                panel.show()
            else:
                panel.hide()

    def _tick(self) -> None:
        # Drain worker status messages — only show ones for the active skill
        # (others are still logged via ROS).
        active = self.active_var.get()
        for skill_name, text, colour in self.mgr.drain_status():
            if skill_name == active and skill_name in self.panels:
                self.panels[skill_name].set_status(text, colour)
            else:
                self.global_status.set(f'[{skill_name}] {text}')
        # Refresh active panel only — non-active panels are hidden anyway.
        if active in self.panels:
            try:
                self.panels[active].refresh()
            except Exception as e:  # noqa: BLE001
                self.mgr.log_warn(f'panel {active}.refresh raised: {e!r}')
        self.root.after(self.REFRESH_MS, self._tick)

    def run(self) -> None:
        self.root.mainloop()


# ── entry point ─────────────────────────────────────────────────────────────

def main(args=None) -> None:
    rclpy.init(args=args)
    node = SkillManager()
    spin_thread = threading.Thread(
        target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()
    ui = SkillManagerUI(node)
    try:
        ui.run()
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
