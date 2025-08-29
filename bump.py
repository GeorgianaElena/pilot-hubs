import json
import subprocess
import sys
from pathlib import Path

cluster_name = sys.argv[1]
hub_name = sys.argv[2]

# elcamino
if hub_name == "all":
    # hubs = ["authoring", "chaffey", "chabot", "deanza", "dvc", "elac", "elcamino", "foothill", "glendale", "golden", "high", "lacc", "lahc", "lavc", "lbcc", "mendocino", "merced", "merritt", "miracosta", "mission"] # cloudbank
    # hubs = ["staging", "lis"] # 2i2c-uk
    # hubs = ["staging", "prod", "workshop"] # awi-ciroh
    hubs = [
        "staging",
        "unitefa-conicet",
        "cicada",
        "gita",
        "iner",
        "plnc",
        "unam",
        "cabana",
        "nnb-ccg",
        "labi",
        "areciboc3",
        "valledellili",
    ]
else:
    hubs = [hub_name]

config_dir = Path(__file__).parent.parent / "config/clusters"

for hub_name in hubs:
    # Let's make sure no active users
    active_users = len(
        [
            l
            for l in subprocess.check_output(
                [
                    "kubectl",
                    "--namespace",
                    hub_name,
                    "get",
                    "pod",
                    "-l",
                    "component=singleuser-server",
                    "-o",
                    "name",
                ]
            )
            .decode()
            .strip()
            .split("\n")
            if l.startswith("pod/")
        ]
    )

    if active_users != 0:
        print(f"{active_users} users are currently on, kick them out or wait")
        sys.exit(1)
    else:
        print(f"No active users on {hub_name}, good to go")

    pv_obj = json.loads(
        subprocess.check_output(
            ["kubectl", "get", "pv", f"{hub_name}-home-nfs", "-o", "json"]
        ).decode()
    )

    print(pv_obj["spec"]["persistentVolumeReclaimPolicy"])
    if pv_obj["spec"]["persistentVolumeReclaimPolicy"] != "Retain":
        print(
            f"PV {hub_name}-home-nfs doesn't have reclaimpolicy set to retain. Fix that and try again"
        )
        continue
    # Bring the hub down
    subprocess.check_call(
        ["kubectl", "--namespace", hub_name, "delete", "svc", "proxy-public"]
    )

    ###############
    subprocess.check_call(
        ["kubectl", "delete", "pv", f"{hub_name}-home-nfs", "--wait=false"]
    )
    subprocess.check_call(
        [
            "kubectl",
            "--namespace",
            hub_name,
            "delete",
            "pvc",
            "home-nfs",
            "--wait=false",
        ]
    )
    ###############

    subprocess.check_call(
        [
            "kubectl",
            "--namespace",
            hub_name,
            "delete",
            "pod",
            "-l",
            "component=shared-dirsize-metrics",
        ]
    )
    subprocess.check_call(
        [
            "kubectl",
            "--namespace",
            hub_name,
            "delete",
            "pod",
            "-l",
            "component=shared-volume-metrics",
        ]
    )

    ############
    subprocess.check_call(
        [
            "deployer",
            "deploy",
            cluster_name,
            hub_name,
        ]
    )
    subprocess.check_call(
        [
            "deployer",
            "run-hub-health-check",
            cluster_name,
            hub_name,
        ]
    )
