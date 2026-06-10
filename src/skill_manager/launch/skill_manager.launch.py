"""Stand-alone bringup for the skill_manager GUI.

Convenience launch — the manager works equally well via
`ros2 run skill_manager skill_manager`.  This file just declares the URL/
topic args so they can be overridden without typing six -p flags.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument('boxes_topic',
                              default_value='/digital_twin/boxes'),
        DeclareLaunchArgument('stack_track_ids_topic',
                              default_value='/stack_track_ids'),
        DeclareLaunchArgument('stack_topic',
                              default_value='/stack'),
        DeclareLaunchArgument('verifier_node',
                              default_value='cup_occupancy_verifier'),
        DeclareLaunchArgument('trigger_scan_service',
                              default_value='/point_cloud_node/trigger_scan'),
        # Flip to 'true' to point every skill at a local server on port 80
        # (http://localhost/api/robot/…) instead of the production API.
        DeclareLaunchArgument('localhost', default_value='false'),
        # Per-skill URL overrides. Empty ⇒ derive from the `localhost` toggle
        # in the node. recover / scan have no server endpoint; leave empty.
        DeclareLaunchArgument('api_url_pick',         default_value=''),
        DeclareLaunchArgument('api_url_pyramid',      default_value=''),
        DeclareLaunchArgument('api_url_update_input', default_value=''),
        DeclareLaunchArgument('api_url_recover',      default_value=''),
        DeclareLaunchArgument('api_url_scan',         default_value=''),
        DeclareLaunchArgument('api_timeout_s',        default_value='15.0'),
        DeclareLaunchArgument('cup_top_z_offset',     default_value='0.302'),

        Node(
            package='skill_manager', executable='skill_manager',
            name='skill_manager', output='screen',
            parameters=[{
                'localhost': ParameterValue(
                    LaunchConfiguration('localhost'), value_type=bool),
                'boxes_topic': LaunchConfiguration('boxes_topic'),
                'stack_track_ids_topic':
                    LaunchConfiguration('stack_track_ids_topic'),
                'stack_topic': LaunchConfiguration('stack_topic'),
                'verifier_node': LaunchConfiguration('verifier_node'),
                'trigger_scan_service':
                    LaunchConfiguration('trigger_scan_service'),
                'api_url_pick': LaunchConfiguration('api_url_pick'),
                'api_url_pyramid': LaunchConfiguration('api_url_pyramid'),
                'api_url_update_input':
                    LaunchConfiguration('api_url_update_input'),
                'api_url_recover': LaunchConfiguration('api_url_recover'),
                'api_url_scan': LaunchConfiguration('api_url_scan'),
                'api_timeout_s': LaunchConfiguration('api_timeout_s'),
                'cup_top_z_offset':
                    LaunchConfiguration('cup_top_z_offset'),
            }]),
    ])
