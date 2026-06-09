# ros2-skill-manager

Single-skill gateway between the operator and the YARR robot HTTP API.

A tkinter window exposes **five skills** behind a radio.  Picking a radio
makes that skill's panel the only one that can act — every other skill
`grid_remove()`s its panel AND refuses execute (`is_active` guard inside
each skill).  This way, mis-clicks (or stale callbacks bound before the
operator switched skills) cannot fire the wrong action.

Skills read **ROS-side state** from the manager (depth tracks, verifier
stack occupancy, verifier `cp`/`degree`) and POST to the robot API via a
shared `api_client` that carries the browser-shaped User-Agent needed to
get past Cloudflare's Browser Integrity Check.

---

## Skills

| Skill | Status | What it does |
|---|---|---|
| **Pick** | ✅ live | List `upright-cup` candidates (alive depth tracks NOT in any stack slot).  Selected cup → `POST /api/robot/skill/pick` with `{x, y, cup_top_z = box_top.z + cup_top_z_offset}` (operator bias, default 0.302 m). |
| **Pyramid** | ✅ live | Source cup picker + target slot dropdown (`L1_L..L3_T`).  → `POST /api/robot/skill/pyramid` with `{x, y, slot}` (slot key auto-mapped: `L1_L→1l`, … , `L3_T→3m`).  Server's cp/degree/pick_z come from `/api/robot/config/pyramid` — keep it synced via **Update Input** below. |
| **Update Input** | ✅ live (bidirectional) | **Pull + Apply**: `GET /api/robot/config/pyramid` → `cp=[center.x, center.y, pick_z]`, `degree` set on verifier via `SetParameters`.  **Push**: `POST /api/robot/config/pyramid {center, degree, pick_z}` — sends verifier's current pose UP to the server so /skill/pyramid uses the same layout the operator sees. |
| **Recover** | 🟡 stub | API **does not exist** on the server today (`POST /api/robot/skill/recover` is absent from the OpenAPI).  Panel lists `fallen-cup` candidates only; action button disabled. |
| **Scan** | 🟡 stub | API **does not exist** on the server today.  Planned: waypoint sweep + 3 s dwell. |

---

## ROS interfaces

| Direction | Topic / Service | Type | Purpose |
|---|---|---|---|
| Sub | `/digital_twin/boxes` | `visualization_msgs/MarkerArray` | depth tracks (box_top pose + box_labels text `c=<color>_<class>_…`) |
| Sub | `/stack_track_ids` | `std_msgs/Int32MultiArray` | verifier-detected stacked track ids (excluded from Pick/Pyramid candidates) |
| Sub | `/stack` | `std_msgs/String` (JSON) | per-slot occupancy `{L1_L..L3_T: <color> \| null}` |
| Cli | `/<verifier_node>/get_parameters` | `rcl_interfaces/srv/GetParameters` | poll cp + degree (2 Hz) |
| Cli | `/<verifier_node>/set_parameters` | `rcl_interfaces/srv/SetParameters` | push cp + degree (update_input) |
| Cli | `/<trigger_scan_service>` | `std_srvs/srv/Trigger` | depth_digital_twin re-scan |

---

## Parameters

| param | default | meaning |
|---|---|---|
| `boxes_topic` | `/digital_twin/boxes` | depth MarkerArray |
| `stack_track_ids_topic` | `/stack_track_ids` | verifier exclusion feed |
| `stack_topic` | `/stack` | verifier slot map |
| `verifier_node` | `cup_occupancy_verifier` | for {get,set}_parameters |
| `trigger_scan_service` | `/point_cloud_node/trigger_scan` | re-scan button target |
| `localhost` | `false` | `true` ⇒ point every skill at `http://localhost/api/robot/…` (port 80) instead of the production API |
| `api_url_pick` | `''` (derive) | empty ⇒ `<root>/skill/pick` from the `localhost` toggle; set to a full URL to override just this skill |
| `api_url_pyramid` | `''` (derive) | empty ⇒ `<root>/skill/pyramid` |
| `api_url_update_input` | `''` (derive) | empty ⇒ `<root>/config/pyramid` (GET pull + POST push) |
| `api_url_recover` | `''` (stub) | NO endpoint on either server; pass a URL to enable |
| `api_url_scan` | `''` (stub) | NO endpoint on either server; pass a URL to enable |
| `api_timeout_s` | `15.0` | per-call HTTP timeout |
| `cup_top_z_offset` | `0.302` | added to `box_top.z` before sending `cup_top_z` |

---

## Run

```bash
ros2 run skill_manager skill_manager
# or with overrides
ros2 launch skill_manager skill_manager.launch.py \
    api_url_pyramid:=https://yarr-api.simplyimg.com/api/robot/skill/pyramid

# localhost (port 80) — all skills hit http://localhost/api/robot/…
ros2 run skill_manager skill_manager --ros-args -p localhost:=true
# or
ros2 launch skill_manager skill_manager.launch.py localhost:=true
```

Prerequisites (in environment overlay): depth_digital_twin running
(`/digital_twin/boxes` flowing) and vision-node `cup_occupancy_verifier`
running (`/stack`, `/stack_track_ids`, cp/degree params).  The integrated
bringup `system_state_aggregator/digital_twin.launch.py` brings up both
plus this panel when added.
