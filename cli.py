"""
RunPod Management CLI
=====================

A lightweight command-line wrapper around the RunPod API for creating,
inspecting, and terminating GPU pods.

Quick start
-----------
List all pods
    python cli.py list_pods

Inspect a specific pod
    python cli.py get_pod --pod_id <POD_ID>

Spin up a 1×A40 dev pod for one hour
    python cli.py create_pod --name my-pod --gpu_type "NVIDIA A40" --network_volume_id "t3uq90cdfb" --runtime 60

Run an arbitrary command (here: a W&B agent) and let the pod self-terminate
afterwards
    python cli.py create_pod --name spd-agent --gpu_type "NVIDIA A100-SXM4-80GB" --args "python my_script.py"

Key ``create_pod`` flags
------------------------
name                 Human-readable pod name (default: "test")
image_name           Docker image (default:
                     "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04")
gpu_type             GPU model (default: "NVIDIA A40")
cloud_type           "SECURE" or "COMMUNITY" (default: "SECURE")
gpu_count            Number of GPUs (default: 1)
volume_in_gb         Ephemeral storage size in GB (default: 10)
container_disk_in_gb Persistent container disk size in GB (default: 30)
min_vcpu_count       Minimum CPU cores (default: 1)
min_memory_in_gb     Minimum RAM in GB (default: 1)
args                 Command(s) executed inside the container.
                     If omitted, the pod runs ``start.sh`` → sleeps → ``terminate.sh``.
volume_mount_path    Mount point of the network volume (default: "/ssd")
network_volume_id    RunPod network-volume ID (default: "t3uq90cdfb")
runtime              Max run-time in minutes; ``0`` disables auto-termination
                     (default: 120)
update_ssh_config    Write an entry to ``~/.ssh/runpod_config`` (default: True)
forward_agent        Enable SSH agent forwarding (default: False)

Terminate a pod
---------------
    python cli.py terminate_pod --pod_id <POD_ID>
"""

import os
import textwrap
import time
from pathlib import Path

import fire
import runpod
from dotenv import load_dotenv


class RunPodManager:
    """Manages RunPod operations including creation and termination of pods."""

    def __init__(self) -> None:
        """
        Initialize the RunPod manager.
        """
        # Load environment variables from .env file
        load_dotenv(override=True)

        # Get API key from environment
        self.api_key = os.getenv("RUNPOD_API_KEY")
        if not self.api_key:
            raise ValueError("RUNPOD_API_KEY not found in environment. Set it in your .env file.")

        # Set up RunPod client
        runpod.api_key = self.api_key

        # Dictionary to store pod host IDs
        self.pod_host_ids: dict[str, str] = {}

    def list_pods(self) -> None:
        """
        List all pods in the account.

        Returns:
            List of pod dictionaries
        """
        pods = runpod.get_pods()

        for i, pod in enumerate(pods):
            print(f"Pod {i + 1}:")
            print(f"  ID: {pod.get('id')}")
            print(f"  Name: {pod.get('name')}")
            print(f"  Machine Type: {pod.get('machine', {}).get('gpuDisplayName')}")
            print(f"  Status: {pod.get('desiredStatus')}")
            public_ip = [i for i in pod.get("runtime", {}).get("ports", []) if i["isIpPublic"]]
            assert len(public_ip) == 1
            print(f"  Public IP: {public_ip[0].get('ip')}")
            print(f"  Public port: {public_ip[0].get('publicPort')}")
            print()

    def get_pod(self, pod_id: str) -> None:
        """
        Get detailed information about a specific pod.

        Args:
            pod_id: The ID of the pod to retrieve

        Returns:
            Pod information dictionary
        """
        pod = runpod.get_pod(pod_id)

        print("Pod Details:")
        print(f"  ID: {pod.get('id')}")
        print(f"  Name: {pod.get('name')}")
        print(f"  Machine Type: {pod.get('machine', {}).get('gpuDisplayName')}")
        print(f"  Status: {pod.get('desiredStatus')}")
        print(f"  Pod Host ID: {pod.get('machine', {}).get('podHostId')}")
        public_ip = [i for i in pod.get("runtime", {}).get("ports", []) if i["isIpPublic"]]
        assert len(public_ip) == 1
        print(f"  Public IP: {public_ip[0].get('ip')}")
        print(f"  Public port: {public_ip[0].get('publicPort')}")
        print(f"  Full Data: {pod}")

    def generate_ssh_config(
        self, ip: str, port: int, user: str, forward_agent: bool = False
    ) -> str:
        """
        Update the ~/.ssh/runpod_config file with the given IP, port, and user.

        Args:
            ip: IP address of the pod
            port: Port number of the pod
            user: User name to use for SSH connection
            forward_agent: Whether to forward the agent
        Returns:
            SSH config string
        """
        return textwrap.dedent(f"""
            Host runpod
              HostName {ip}
              User {user}
              Port {port}
              {"ForwardAgent yes" if forward_agent else ""}
        """).strip()

    def create_pod(
        self,
        name: str = "test",
        image_name: str = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
        gpu_type: str = "NVIDIA A40",
        cloud_type: str = "SECURE",
        gpu_count: int = 1,
        volume_in_gb: int = 10,
        min_vcpu_count: int = 1,
        min_memory_in_gb: int = 1,
        container_disk_in_gb: int = 30,
        args: str = "",
        volume_mount_path: str = "/ssd",
        env: dict[str, str] | None = None,
        runtime: int = 120,
        update_ssh_config: bool = True,
        forward_agent: bool = False,
        network_volume_id: str = "t3uq90cdfb",
    ) -> None:
        """
        Create a new pod with the specified parameters.

        Args:
            name: Name for the pod
            image_name: Docker image to use
            gpu_type: Type of GPU to request (e.g., "NVIDIA A40")
            cloud_type: Cloud type ("SECURE" or "COMMUNITY")
            gpu_count: Number of GPUs to allocate
            volume_in_gb: Size of ephemeral storage volume in GB
            min_vcpu_count: Minimum vCPU count
            min_memory_in_gb: Minimum RAM in GB
            container_disk_in_gb: Size of container disk in GB
            args: Arguments passed to Docker (by default runs ./start.sh and ./terminate.sh with
                sleep in between). If provided, will replace the sleep in between with the provided
                arguments.
            volume_mount_path: Path where volume will be mounted
            env: Environment variables to set in the container
            runtime: Time in minutes for pod to run. Default is 120 minutes.
            update_ssh_config: Whether to update the ~/.ssh/runpod_config file
            forward_agent: Whether to forward the agent
            network_volume_id: RunPod network-volume ID (default `"t3uq90cdfb"`)
        """
        print("Creating pod with:")
        print(f"  Name: {name}")
        print(f"  Image: {image_name}")
        print(f"  GPU Type: {gpu_type}")
        print(f"  Cloud Type: {cloud_type}")
        print(f"  GPU Count: {gpu_count}")
        print(f"  Time limit: {runtime} minutes")
        print(f"  Network volume ID: {network_volume_id}")

        # NOTE: Must use this structure in order to work (i.e. with -c and commands separated by ;)
        # We sleep for a minimum of 20 seconds to ensure that this script does not error due to the
        # pod terminating too quickly
        args = f" {args};" if args else ""
        args = (
            f"/bin/bash -c '{volume_mount_path}/start.sh;{args} sleep {max(runtime * 60, 20)}; "
            f"{volume_mount_path}/terminate.sh'"
        )

        print(f"  Pod start command: {args}")

        pod = runpod.create_pod(
            name=name,
            image_name=image_name,
            gpu_type_id=gpu_type,
            cloud_type=cloud_type,
            gpu_count=gpu_count,
            volume_in_gb=volume_in_gb,
            container_disk_in_gb=container_disk_in_gb,
            min_vcpu_count=min_vcpu_count,
            min_memory_in_gb=min_memory_in_gb,
            docker_args=args,
            env=env,
            ports="8888/http,22/tcp",
            volume_mount_path=volume_mount_path,
            network_volume_id=network_volume_id,
        )
        pod_id = pod.get("id")
        # Store the pod host ID when we create a pod
        pod_host_id = pod.get("machine", {}).get("podHostId")
        if pod_host_id:
            self.pod_host_ids[pod_id] = pod_host_id

        print("Pod created:")
        print(f"  Instance ID: {pod_id}")
        print(f"  Pod Host ID: {pod_host_id}")
        print("  Provisioning...")

        # Try deploying the pod for 36*5=180 seconds
        n_attempts = 36
        i = 1
        while True:
            pod = runpod.get_pod(pod_id)
            runtime = pod.get("runtime")
            if runtime is None or not runtime.get("ports"):
                if i > n_attempts:
                    raise RuntimeError("Pod provisioning failed")
                i += 1
                time.sleep(5)
            else:
                break

        print("  Pod provisioned")
        public_ip = [i for i in pod["runtime"]["ports"] if i["isIpPublic"]]
        assert len(public_ip) == 1, f"Expected 1 public IP, got {len(public_ip)}"
        ip = public_ip[0].get("ip")
        port = public_ip[0].get("publicPort")
        print(f"  Public IP: {ip}")
        print(f"  Public port: {port}")
        print(f"  basic SSH command:\nssh {pod_host_id}@ssh.runpod.io")
        print(f"  full SSH command:\nssh root@{ip} -p {port}")
        if update_ssh_config:
            runpod_config = self.generate_ssh_config(
                ip=ip,
                port=port,
                user="root",
                forward_agent=forward_agent,
            )
            Path.home().joinpath(".ssh", "runpod_config").write_text(runpod_config)
            print("SSH config updated")

    def terminate_pod(self, pod_id: str) -> None:
        """
        Terminate a specific pod.

        Args:
            pod_id: ID of the pod to terminate

        Returns:
            Termination result dictionary
        """
        print(f"Terminating pod {pod_id}...")
        result = runpod.terminate_pod(pod_id)
        print(f"Pod terminated: {result}")


def main() -> None:
    """
    Main entry point for the CLI application.
    """
    fire.Fire(RunPodManager)


if __name__ == "__main__":
    main()
