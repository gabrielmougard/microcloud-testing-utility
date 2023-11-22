#!/usr/bin/env python3

import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import time

PROJECT_NAME = "microcloud-test"
PROFILE_NAME = "microcloud-profile"
DEFAULT_MICROCLOUD_SNAP_PATH = "/home/gab/go/src/github.com/microcloud-pkg-snap"
DEFAULT_CACHE_PATH = os.getcwd() + "/.cache"
VMS = {
    f"micro{i}": {
        "config": (
            {"limits.cpu": "2", "limits.memory": "2GiB", "migration.stateful": "true"},
            f"local{i}+remote{i}" if i < 4 else f"local{i}",
        ),
        "desired_status": "RUNNING" if i < 4 else "STOPPED",
        "new": False,
    } for i in range(1, 5)
}

DISKS = {
    disk_name: "20GiB" if disk_name.startswith("remote") else ""
    for disk_name in ["local1", "local2", "local3", "local4", "remote1", "remote2", "remote3"]
}

NETWORKS = ["microbr0"]
# This is the snapshot that will be used to backup a VM before installing microcloud on it.
# It should already contain all the other needed deps (microovn, microceph, lxd, etc).
SNAPSHOT_BASE_NAME = "microcloud-base-snapshot"


def create_profile():
    try:
        subprocess.run(["lxc", "profile", "copy", "default", PROFILE_NAME, "--target-project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error copying default profile to {PROFILE_NAME}: {e}")
        raise e


def create_disk(disk_name: str, size: str):
        if size != "":
            size = f"size={size}"
            try:
                subprocess.run(["lxc", "storage", "volume", "create", "disks", disk_name, "--type", "block", size, "--project", PROJECT_NAME], check=True)
            except subprocess.CalledProcessError as e:
                print(f"Error creating disk {disk_name}: {e}")
                raise e
        else:
            try:
                subprocess.run(["lxc", "storage", "volume", "create", "disks", disk_name, "--type", "block", "--project", PROJECT_NAME], check=True)
            except subprocess.CalledProcessError as e:
                print(f"Error creating disk {disk_name}: {e}")
                raise e


def remove_disk(disk_name: str):
    try:
        subprocess.run(["lxc", "storage", "volume", "delete", "disks", disk_name, "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error removing disk {disk_name}: {e}")
        raise e


def init_vm(vm_name: str, conf):
    config = []
    stateful_size_state = ""
    for key, value in conf.items():
        if key == "limits.memory":
            stateful_size_state = value # Use the same amount of allocated VM memory for size of the state volume

        config.append("--config")
        config.append(f"{key}={value}")

    cmd_create = ["lxc", "init", "ubuntu:22.04", vm_name, "--profile", PROFILE_NAME, "--vm"]
    cmd_create.extend(config)
    cmd_create.extend(["--project", PROJECT_NAME])

    try:
        subprocess.run(cmd_create, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error creating VM {vm_name}: {e}")
        raise e

    # Set the size of the state volume if stateful migration is needed
    if stateful_size_state != "":
        try:
            subprocess.run(["lxc", "config", "device", "override", vm_name, "root", f"size.state={stateful_size_state}", "--project", PROJECT_NAME], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error creating VM {vm_name}: {e}")
            raise e


def restore_vm_from_cache(vm_name: str, cache_path: str):
    if not os.path.exists(f"{cache_path}/{vm_name}.tar.gz"):
        print(f"Error: VM {vm_name} does not exist in the cache")
        raise FileNotFoundError

    try:
        subprocess.run(["lxc", "import", f"{cache_path}/{vm_name}.tar.gz", vm_name, "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error restoring VM {vm_name} from cache: {e}")
        raise e


def attach_disk_to_vm(vm_name: str, disk_name: str):
    try:
        subprocess.run(["lxc", "storage", "volume", "attach", "disks", disk_name, vm_name, "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error attaching disk {disk_name} to VM {vm_name}: {e}")
        raise e


def start_vm(vm_name: str):
    try:
        subprocess.run(["lxc", "start", vm_name, "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error starting VM {vm_name}: {e}")
        raise e


def delete_image(fingerprint: str):
    try:
        subprocess.run(["lxc", "image", "delete", fingerprint, "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error deleting image {fingerprint}: {e}")
        raise e


def delete_images():
    try:
        res = subprocess.run(["lxc", "image", "list", "--project", PROJECT_NAME, "-c", "f", "-f", "csv"], check=True, stdout=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print(f"Error listing images: {e}")
        raise e

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(delete_image, fingerprint) for fingerprint in res.stdout.decode("utf-8").strip().splitlines()]
        concurrent.futures.wait(futures)


def delete_vm(vm_name: str):
    try:
        subprocess.run(["lxc", "delete", vm_name, "--force", "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error deleting VM {vm_name}: {e}")
        raise e


def add_network_interface_to_vm(vm_name: str, net_iface: str):
    try:
        subprocess.run(["lxc", "config", "device", "add", vm_name, net_iface, "nic", f"network={NETWORKS[0]}", f"name={net_iface}", "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error adding network interface {net_iface} to VM {vm_name}: {e}")
        raise e


def is_valid_ipv4(ip):
    pattern = r"^\d{1,3}(\.\d{1,3}){3}$"
    if re.match(pattern, ip):
        return all(0 <= int(octet) <= 255 for octet in ip.split('.'))

    return False


def get_vms_status():
    try:
        res = subprocess.run(["lxc", "list", "--project", PROJECT_NAME, "--format", "csv", "--columns", "ns4"], stdout=subprocess.PIPE)
        return res.stdout.decode("utf-8")
    except subprocess.CalledProcessError as e:
        print(f"Error while getting the status of the VMs: {e}")
        raise e


def push_snap(vm_name: str, snap_path):
    try:
        subprocess.run(["lxc", "file", "push", "--project", PROJECT_NAME, f"{snap_path}", f"{vm_name}/root/"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error pushing snap to {vm_name}: {e}")
        raise e


def install_snap(vm_name: str, snap_name: str):
    try:
        res = subprocess.run(["lxc", "exec", vm_name, "--project", PROJECT_NAME, "--", "snap", "install", "--dangerous", f"/root/{snap_name}"], check=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print(f"Error resetting snap from {vm_name}: {e}")
        print(res.stderr.decode("utf-8"))
        raise e


def cache_populated(cache_path):
    if cache_path == DEFAULT_CACHE_PATH and not os.path.isdir(cache_path):
        os.mkdir(cache_path)
        return False

    if not os.path.isdir(cache_path):
        return False

    return sorted(os.listdir(cache_path)) == sorted([f"{vm_name}.tar.gz" for vm_name in VMS.keys()])


def purge_cache(cache_path):
    if not os.path.exists(cache_path):
        print("Folder does not exist:", cache_path)
        raise FileNotFoundError

    for item_name in os.listdir(cache_path):
        item_path = os.path.join(cache_path, item_name)

        if os.path.isfile(item_path):
            os.remove(item_path)
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)


def wait_for_vms_ready():
    vms_ready = []
    while True:
        vms_status = get_vms_status()
        if len(vms_status.splitlines()) != len(VMS):
            time.sleep(2)
            continue

        for line in vms_status.splitlines():
            parts = line.split(",")
            if len(parts) >= 2:
                vm_name, status, ipv4_with_iface = parts[0], parts[1], parts[2]
                if vm_name in VMS.keys() and status == "RUNNING" and is_valid_ipv4(ipv4_with_iface.split(" ")[0]):
                    if vm_name not in vms_ready:
                        vms_ready.append(vm_name)
                        print(f"VM {vm_name} is ready")
            else:
                break

        if len(vms_ready) == len(VMS):
            time.sleep(20) # Before leaving, wait for the VM agents to be ready
            break

        time.sleep(2)


def setup_infra(cache_path: str, args: dict):
    # Create the microcloud project.
    print(f"Creating {PROJECT_NAME} project...")
    try:
        subprocess.run(["lxc", "project", "create", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error creating microcloud project: {e}")
        raise e

    # Create the disks storage pool.
    try:
        subprocess.run(["lxc", "storage", "create", "disks", "zfs", "size=100GiB", "--project", PROJECT_NAME], check=True)
        subprocess.run(["lxc", "storage", "set", "disks", "volume.size", "10GiB", "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error creating disks storage pool: {e}")
        raise e

    # Create the disks
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(create_disk, disk_name, conf) for disk_name, conf in DISKS.items()]
        concurrent.futures.wait(futures)

    # Create the microbr0 network.
    for network in NETWORKS:
        try:
            subprocess.run(["lxc", "network", "create", network, "--project", PROJECT_NAME], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error creating {network} network: {e}")
            raise e

    # Before initializing the VMs, we need to copy the default project settings to PROJECT_NAME project.
    create_profile()

    if cache_populated(cache_path) and not args["--purge-cache"]:
        # Restore the VMs from the cache (restoring should also attach disks and add network interfaces to the VMs)
        print("Restoring VMs from cache...")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(restore_vm_from_cache, vm_name, cache_path) for vm_name in VMS.keys()]
            concurrent.futures.wait(futures)
    else:
        if args["--purge-cache"]:
            # Purge the cache.
            print("Purging cache...")
            purge_cache(cache_path)

        # Init the VMs.
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(init_vm, vm_name, vm_data["config"][0]) for vm_name, vm_data in VMS.items()]
            concurrent.futures.wait(futures)

        # Mark VMs as new.
        for vm in VMS.values():
            vm["new"] = True

        # Attach disks (local and remote ones) to the VMs.
        for vm_name, vm_data in VMS.items():
            for disk in vm_data["config"][1].split("+"):
                attach_disk_to_vm(vm_name, disk)

        # Add network interfaces to the VMs.
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(add_network_interface_to_vm, vm_name, "eth1") for vm_name in VMS.keys()]
            concurrent.futures.wait(futures)

    # Start VMs.
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(start_vm, vm_name) for vm_name in VMS.keys()]
        concurrent.futures.wait(futures)

    # Wait for all the VMs to be ready.
    print("Waiting for VMs to be ready...")
    wait_for_vms_ready()


def enable_ovn_net_iface(vm_name: str):
    # Configure the network interface connected to microbr0 to not accept any IP addresses (because MicroCloud
    # requires a network interface that doesn’t have an IP address assigned)
    try:
        subprocess.run(["lxc", "exec", vm_name, "--project", PROJECT_NAME, "--", "echo", "0", ">", "/proc/sys/net/ipv6/conf/enp6s0/accept_ra"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error configuring network interface for {vm_name}: {e}")
        raise e

    # Bring the network interface connected to microbr0 up
    try:
        subprocess.run(["lxc", "exec", vm_name, "--project", PROJECT_NAME, "--", "ip", "link", "set", "enp6s0", "up"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error bringing network interface up for {vm_name}: {e}")
        raise e


def install_snap_deps(vm_name: str):
    # Install the required snap packages (except microcloud that we want to side load)
    try:
        subprocess.run(["lxc", "exec", vm_name, "--project", PROJECT_NAME, "--", "snap", "install", "lxd"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error installing lxd snap packages for {vm_name}: {e}")
        raise e

    try:
        subprocess.run(["lxc", "exec", vm_name, "--project", PROJECT_NAME, "--", "snap", "install", "microceph"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error installing microceph snap packages for {vm_name}: {e}")
        raise e

    try:
        subprocess.run(["lxc", "exec", vm_name, "--project", PROJECT_NAME, "--", "snap", "install", "microovn"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error installing microovn snap packages for {vm_name}: {e}")
        raise e


def snapshot_vm(vm_name: str):
    try:
        subprocess.run(["lxc", "snapshot", vm_name, SNAPSHOT_BASE_NAME, "--reuse", "--no-expiry", "--stateful", "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error snapshotting VM {vm_name}: {e}")
        raise e


def export_vm_to_cache(vm_name: str, cache_path: str):
    if not os.path.exists(f"{cache_path}/{vm_name}.tar.gz"):
        try:
            # Export the VM to the cache. Use `--optimized-storage` because we'll always use the same storage pool for our VMs. (zfs)
            subprocess.run(["lxc", "export", vm_name, f"{cache_path}/{vm_name}.tar.gz", "--optimized-storage", "--project", PROJECT_NAME], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error exporting VM {vm_name} to cache: {e}")
            raise e
    else:
        print(f"VM {vm_name} already exists in the cache")


def full_vms_setup(cache_path: str):
    # Enable OVN net interface on the VMs.
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(enable_ovn_net_iface, vm_name) for vm_name in VMS.keys()]
        concurrent.futures.wait(futures)

    # Install external snap deps on the VMs.
    time.sleep(10) # Wait for snapd to be ready
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(install_snap_deps, vm_name) for vm_name in VMS.keys()]
        concurrent.futures.wait(futures)

    # Snapshot the VMs.
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(snapshot_vm, vm_name) for vm_name in VMS.keys()]
        concurrent.futures.wait(futures)

    # Export the VMs to the cache.
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(export_vm_to_cache, vm_name, cache_path) for vm_name in VMS.keys()]
        concurrent.futures.wait(futures)


def existing_microcloud_infra():
    print("Inspecting machines...")

    vms_status = get_vms_status()
    if len(vms_status.splitlines()) != len(VMS):
        print(f"Expected {len(VMS)} VMs but found {len(vms_status.splitlines())}")
        return False

    for line in vms_status.splitlines():
        parts = line.split(",")
        if len(parts) >= 2:
            vm_name, status = parts[0], parts[1]
            if vm_name in VMS.keys() and status != VMS[vm_name]["desired_status"]:
                print(f"VM {vm_name} is not in the expected state {VMS[vm_name]['desired_status']} but in {status}")
                return False
            else:
                if len(parts) == 3:
                    ip_with_iface = parts[2]
                    if ip_with_iface == "":
                        continue

                    ip = ip_with_iface.split(" ")[0]
                    if not is_valid_ipv4(ip):
                        print(f"VM {vm_name} has an invalid IP address: {ip}")
                        return False
                    else:
                        print(f"VM {vm_name} has IP address {ip}")

    print("MicroCloud infrastructure detected: all machines are in the expected state")
    return True


def purge_microcloud_infra():
    print("Purging microcloud infrastructure...")

    print("Deleting VMs...")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(delete_vm, vm_name) for vm_name in VMS.keys()]
        concurrent.futures.wait(futures)

    print("Deleting disks...")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(remove_disk, disk_name) for disk_name in DISKS.keys()]
        concurrent.futures.wait(futures)

    print("Deleting storage pool...")
    try:
        subprocess.run(["lxc", "storage", "delete", "disks", "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error removing storage pool 'disks': {e}")
        raise e

    print("Deleting networks...")
    for network in NETWORKS:
        try:
            subprocess.run(["lxc", "network", "delete", network, "--project", PROJECT_NAME], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error deleting {network} network: {e}")
            raise e

    print("Deleting profile...")
    try:
        subprocess.run(["lxc", "profile", "delete", PROFILE_NAME, "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error deleting {PROFILE_NAME} profile: {e}")
        raise e

    print("Deleting images...")
    delete_images()

    print("Deleting project...")
    try:
        subprocess.run(["lxc", "project", "delete", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error deleting microcloud project: {e}")
        raise e


def check_vm_snapshotted(vm_name: str) -> bool:
    try:
        res = subprocess.run(["lxc", "list", vm_name, "--project", PROJECT_NAME, "--format", "csv", "-c", "S"], stdout=subprocess.PIPE)
        if int(res.stdout.decode("utf-8").strip()) >= 1:
            return True

        return False
    except subprocess.CalledProcessError as e:
        print(f"Error checking if VM {vm_name} is snapshotted: {e}")
        raise e


def vms_snapshotted():
    print("Checking if VMs are snapshotted...")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(check_vm_snapshotted, vm_name) for vm_name in VMS.keys()]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]

    return all(results)


def restore_vm_from_local_snapshot(vm_name: str):
    try:
        subprocess.run(["lxc", "restore", vm_name, SNAPSHOT_BASE_NAME, "--project", PROJECT_NAME], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error restoring VM {vm_name} from local snapshot: {e}")
        raise e


def restore_local_snapshots():
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(restore_vm_from_local_snapshot, vm_name) for vm_name in VMS.keys()]
        concurrent.futures.wait(futures)

    time.sleep(5)


def side_load_microcloud_snap():
    # Check local microccloud-pkg-snap folder
    if not os.path.exists(DEFAULT_MICROCLOUD_SNAP_PATH):
        print(f"Error: {DEFAULT_MICROCLOUD_SNAP_PATH} does not exist")
        raise FileNotFoundError

    snap_files = []
    for filename in os.listdir(DEFAULT_MICROCLOUD_SNAP_PATH):
        if filename.endswith('.snap'):
            snap_files.append(os.path.join(DEFAULT_MICROCLOUD_SNAP_PATH, filename))

    if len(snap_files) == 0:
        # If no .snap file is found, try to recreate a snap package
        try:
            wd = os.getcwd()
            os.chdir(DEFAULT_MICROCLOUD_SNAP_PATH)
            subprocess.Popen("snapcraft", shell=True).wait()
            os.chdir(wd)
        except subprocess.CalledProcessError as e:
            print(f"Error while packaging microcloud snap: {e}")
            raise e

        # Update the list of snap files
        for filename in os.listdir(DEFAULT_MICROCLOUD_SNAP_PATH):
            if filename.endswith('.snap'):
                snap_files.append(os.path.join(DEFAULT_MICROCLOUD_SNAP_PATH, filename))

    if len(snap_files) > 1 or len(snap_files) == 0:
        print(f"Error: Wrong number of .snap files in {DEFAULT_MICROCLOUD_SNAP_PATH}.")
        raise FileNotFoundError
    else:
        snap_file = snap_files[0]
        print(f"Found snap file in microcloud-pkg-snap folder: {snap_file}. Using it for side-loading.")

    # Push the snap to the VMs.
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(push_snap, vm_name, snap_file) for vm_name in VMS.keys()]
        concurrent.futures.wait(futures)

    # Install the snap on the VMs.
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(install_snap, vm_name, snap_file.split("/")[-1]) for vm_name in VMS.keys()]
        concurrent.futures.wait(futures)

    print("MicroCloud binaries side-loaded successfully")


def main():
    args = {
        # Where to find the cache for the instances to be used. If not provided, the `<CWD>/.cache` is used. In the folder, we have the exported .tgz instances.
        "--cache": None,
        # Whether to destroy the cache before starting side loading any snap in the VMs.
        "--purge-cache": False,
        # Path to the microcloud snap. If not provided, the `DEFAULT_MICROCLOUD_SNAP_PATH` folder is used and takes the first available .snap file if found.
        "--microcloud-snap": None,
        # Whether to initialize the microcloud before starting side loading any snap in the VMs.
        "--init": False,
        # Just purge the microcloud infrastructure and exit.
        "--nuke": False,
    }

    # No parameters means you have existing VMs with the right devices and you want to reset the snaps inside them and load the new ones.
    # (for that we'll have a snapshot of each VM before microcloud is installed)

    it = iter(sys.argv[1:])
    for arg in it:
        if arg in args:
            if arg == "--nuke" or arg == "--purge-cache" or arg == "--init":
                args[arg] = True
            else:
                try:
                    args[arg] = next(it)
                except StopIteration:
                    print(f"Error: Argument {arg} expects a value")
                    raise e
        else:
            print(f"Error: Unknown argument {arg}")
            sys.exit(1)

    if args["--nuke"]:
        # Purge the microcloud infrastructure and exit.
        purge_microcloud_infra()
        sys.exit(0)

    cache_path = args["--cache"] if args["--cache"] else DEFAULT_CACHE_PATH
    try:
        if existing_microcloud_infra():
            if args["--init"]:
                purge_microcloud_infra()
                setup_infra(cache_path, args)

            if vms_snapshotted():
                # Start the last VM (micro4 in our case) before restoring it
                try:
                    subprocess.run(["lxc", "start", f"micro{len(VMS)}", "--project", PROJECT_NAME])
                except subprocess.CalledProcessError as e:
                    print(f"Error starting micro{len(VMS)} VM: {e}")
                    raise e

                wait_for_vms_ready()
                print("VMs are already snapshotted. Restoring them from local snapshots...")
                restore_local_snapshots()
                wait_for_vms_ready()

                # We still need to enable the OVN net interface on the VMs.
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    futures = [executor.submit(enable_ovn_net_iface, vm_name) for vm_name in VMS.keys()]
                    concurrent.futures.wait(futures)
            else:
                full_vms_setup(cache_path)
        else:
            setup_infra(cache_path, args)
            if all([vm["new"] for vm in VMS.values()]): # all VMs are new (i.e. they were just created from scratch without the cache)
                full_vms_setup(cache_path)
            else:
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    futures = [executor.submit(enable_ovn_net_iface, vm_name) for vm_name in VMS.keys()]
                    concurrent.futures.wait(futures)

                if not vms_snapshotted():
                    # Snapshot the VMs.
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        futures = [executor.submit(snapshot_vm, vm_name) for vm_name in VMS.keys()]
                        concurrent.futures.wait(futures)

        side_load_microcloud_snap()
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # The last VM (micro4 in our case) needs to be stopped because it is meant to be used for a scale-up scenario
    try:
        subprocess.run(["lxc", "stop", f"micro{len(VMS)}", "--project", PROJECT_NAME])
    except subprocess.CalledProcessError as e:
        print(f"Error stopping micro{len(VMS)} VM: {e}")
        raise e

    # Before leaving, print some useful information needed for the microcloud init process
    # to help this script user. (like the ipv4 and ipv6 address of the microbr0 network)
    try:
        res = subprocess.run(["lxc", "network", "get", NETWORKS[0], "ipv4.address", "--project", PROJECT_NAME], stdout=subprocess.PIPE)
        ipv4_address = res.stdout.decode("utf-8").strip()
        res = subprocess.run(["lxc", "network", "get", NETWORKS[0], "ipv6.address", "--project", PROJECT_NAME], stdout=subprocess.PIPE)
        ipv6_address = res.stdout.decode("utf-8").strip()
    except subprocess.CalledProcessError as e:
        print(f"Error getting {NETWORKS[0]} network's ipv4 and ipv6 address: {e}")
        raise e

    print(f"IPv4 address of {NETWORKS[0]} network: {ipv4_address}")
    print(f"IPv6 address of {NETWORKS[0]} network: {ipv6_address}")

    sys.exit(0)


if __name__ == "__main__":
    main()