"""Bounded, secret-free runtime smoke test; no ports, volumes or database access."""
import argparse
import subprocess
import time
import uuid


def check(image, docker="docker"):
    name = "core-smoke-" + uuid.uuid4().hex
    def run(*args, timeout=20):
        return subprocess.run([docker, *args], capture_output=True, text=True, timeout=timeout)
    attempted = False
    try:
        if run("info", "--format", "{{.OSType}}").stdout.strip() != "linux":
            raise RuntimeError("Linux Docker engine unavailable")
        attempted = True
        result = run("run", "-d", "--name", name, "--network", "none", "--read-only",
                     "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                     "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", image)
        if result.returncode:
            raise RuntimeError("Could not start isolated runtime container")
        for _ in range(30):
            result = run("exec", name, "python", "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).close()")
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Runtime process health did not become ready")
        result = run("exec", name, "python", "-c",
            "import os,importlib.util; assert os.getuid()==10001; "
            "assert importlib.util.find_spec('pytest') is None; "
            "open('/tmp/core-smoke','w').close()")
        if result.returncode:
            raise RuntimeError("Runtime identity/dependency/tmpfs check failed")
        result = run("inspect", "--format", "{{.HostConfig.ReadonlyRootfs}}", name)
        if result.returncode or result.stdout.strip() != "true":
            raise RuntimeError("Read-only runtime check failed")
    finally:
        if attempted:
            # Only this uniquely named, volume-free smoke container is removed.
            if run("rm", "-f", name).returncode:
                raise RuntimeError("Could not remove owned smoke container")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="ai-platform-core:dev")
    args = parser.parse_args()
    try:
        check(args.image)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        # Do not echo container logs, commands or environment values.
        print(str(error) if isinstance(error, RuntimeError) else "Docker smoke command unavailable or timed out")
        raise SystemExit(1)
    print("Restricted Core runtime smoke passed; no database connection tested")
