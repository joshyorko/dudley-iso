#!/usr/bin/env bash
# Run only on a GitHub runner or the dedicated Harvester Ubuntu machine.
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true || ${OMARCHY_REMOTE_BUILDER:-} == harvester ]] || {
    echo 'Refusing to start a Dagger engine on the workstation' >&2; exit 2;
}
root=${OMARCHY_ENGINE_ROOT:-/var/lib/containers/omarchy-dagger}
engine=omarchy-dagger-v0.20.6
# Resolved by the real Harvester pull of the official v0.20.6 engine.
image=registry.dagger.io/engine@sha256:78cdac5f0ab478a82deb9c8bc3490f56a5be9a9c8bc424584771f4634f04a3a0
cpus=4
memory_gib=12
swap_gib=14
if [[ ${OMARCHY_REMOTE_BUILDER:-} == harvester ]]; then
    cpus=2
    memory_gib=5
    swap_gib=6
fi
memory_bytes=$((memory_gib * 1024 * 1024 * 1024))
swap_bytes=$((swap_gib * 1024 * 1024 * 1024))
host_memory_kib=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
((host_memory_kib >= (memory_gib + 2) * 1024 * 1024)) || {
    echo 'Insufficient guest RAM for the engine plus 2 GiB host headroom' >&2; exit 1;
}
sudo -n mkdir -p "$root"
root=$(sudo -n realpath -- "$root")
if ! docker image inspect "$image" >/dev/null 2>&1; then docker pull "$image" >&2; fi
expected_image=$(docker image inspect "$image" --format '{{.Id}}')
free=$(sudo -n df -Pk "$root" | awk 'NR==2 {print $4}')
((free >= 30 * 1024 * 1024)) || { echo 'Need 30 GiB genuinely free for Dagger' >&2; exit 1; }
cpu_set=$(python3 -c 'import os,sys; n=int(sys.argv[1]); cpus=sorted(os.sched_getaffinity(0)); assert len(cpus)>=n; print(",".join(map(str, cpus[:n])))' "$cpus")
if ! docker container inspect "$engine" >/dev/null 2>&1; then
    docker create --name "$engine" --privileged --cgroupns=private \
        --cpus="$cpus" --cpuset-cpus="$cpu_set" --memory="${memory_gib}g" --memory-swap="${swap_gib}g" --pids-limit=4096 \
        --restart=unless-stopped -v "$root:/var/lib/dagger" "$image" --debug >/dev/null
fi
actual_image=$(docker inspect "$engine" --format '{{.Image}}')
[[ $actual_image == "$expected_image" ]] || { echo 'Existing Dagger engine image differs from pinned digest' >&2; exit 1; }
docker inspect "$engine" | python3 -c '
import json,sys
engine=json.load(sys.stdin)[0]
mounts=[m for m in engine["Mounts"] if m["Destination"]=="/var/lib/dagger"]
assert len(mounts)==1 and mounts[0]["Type"]=="bind" and mounts[0]["Source"]==sys.argv[1], "Dagger engine cache mount differs from requested storage root"
assert engine["HostConfig"]["CgroupnsMode"]=="private", "Dagger engine cgroup namespace differs"
' "$root"
limits=$(docker inspect "$engine" --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.PidsLimit}} {{.HostConfig.CpusetCpus}}')
expected_limits="$((cpus * 1000000000)) $memory_bytes $swap_bytes 4096 $cpu_set"
[[ $limits == "$expected_limits" ]] || {
    printf 'Existing Dagger engine limits differ: got=%s expected=%s\n' "$limits" "$expected_limits" >&2
    exit 1
}
docker start "$engine" >/dev/null
actual=$(docker exec "$engine" cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max /sys/fs/cgroup/cpu.max /sys/fs/cgroup/pids.max)
expected=$(printf '%s\n%s\n%s 100000\n4096' "$memory_bytes" "$((swap_bytes - memory_bytes))" "$((cpus * 100000))")
[[ $actual == "$expected" ]] || {
    printf 'Running Dagger engine cgroup limits differ:\ngot:\n%s\nexpected:\n%s\n' "$actual" "$expected" >&2
    exit 1
}
printf '_EXPERIMENTAL_DAGGER_RUNNER_HOST=docker-container://%s\n' "$engine"
