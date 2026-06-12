#!/usr/bin/env bash
# Manual operator GUI for YARR ros2-skill-manager.
# NOT for use during an automated LLM closed-loop (./start.sh --real-api):
# GUI skill commands collide with plan_executor/pick_node on the real robot.
#
# The node default api root is the PUBLIC host (https://yarr-api.simplyimg.com),
# which Cloudflare times out (~60s) on long robot motions. We override to
# localhost. api_url_{pick,pyramid,update_input,scan,move,position} are NODE
# params (not launch args), so they are passed via `ros2 run ... --ros-args -p`.
set -eo pipefail
# (no -u: ROS setup.bash references unbound vars like AMENT_TRACE_SETUP_FILES)

if [[ -z "${DISPLAY:-}" ]]; then
  echo "[skill-manager] DISPLAY is not set; tkinter GUI cannot start." >&2
  exit 1
fi

ROBOT_API_BASE="${ROBOT_API_BASE:-http://localhost}"
API_TIMEOUT_S="${API_TIMEOUT_S:-180.0}"

# Resolve the integration checkout from this script's location (works for any
# clone path: ~/cup-stack-integration on the 31 host, ~/Projects/... locally).
# This file lives at ros2-skill-manager/run_skill_manager.sh, so ../ is root.
INTEG_ROOT=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)
source /opt/ros/humble/setup.bash
# Doosan workspace (dsr_msgs2) — needed by the move skill's HOME button
# (/dsr01/motion/move_joint, same call as the yarr_home alias). Optional:
# without it the Home button is disabled, everything else works.
for cand in \
    "$INTEG_ROOT/ros2-cup-stack/install/setup.bash" \
    "$INTEG_ROOT/ros2-cup-stack/ros2/install/setup.bash" \
    "$HOME/ros2_ws/install/setup.bash"; do
  if [[ -f "$cand" ]]; then source "$cand"; break; fi
done
source "$INTEG_ROOT/ros2-depth-point-cloude/install/setup.bash"
source "$INTEG_ROOT/vision-node/install/setup.bash"
source "$INTEG_ROOT/ros2-skill-manager/install/setup.bash"

echo "[skill-manager] api base=${ROBOT_API_BASE}  timeout=${API_TIMEOUT_S}s"
echo "[skill-manager] WARNING: do NOT press Pick/Pyramid while ./start.sh --real-api is running."

# Every endpoint is pinned to ROBOT_API_BASE: an api_url_* left blank silently
# falls back to the node's Cloudflare PROD default, which times out on long
# motions (this is exactly how scan used to break). The node declares all six.
exec ros2 run skill_manager skill_manager --ros-args \
  -p api_url_pick:="${ROBOT_API_BASE}/api/robot/skill/pick" \
  -p api_url_pyramid:="${ROBOT_API_BASE}/api/robot/skill/pyramid" \
  -p api_url_update_input:="${ROBOT_API_BASE}/api/robot/config/pyramid" \
  -p api_url_scan:="${ROBOT_API_BASE}/api/robot/skill/scan" \
  -p api_url_move:="${ROBOT_API_BASE}/api/robot/move" \
  -p api_url_position:="${ROBOT_API_BASE}/api/robot/position" \
  -p api_timeout_s:="${API_TIMEOUT_S}"
