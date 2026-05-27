from glob import glob
from setuptools import find_packages, setup

package_name = 'skill_manager'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='EunwooSong',
    maintainer_email='song200348@gmail.com',
    description='Skill-level gateway between operators and the YARR robot '
                'HTTP API (pick / pyramid / update_input / recover / scan).',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'skill_manager = skill_manager.skill_manager_node:main',
        ],
    },
)
