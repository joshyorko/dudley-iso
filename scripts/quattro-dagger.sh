#!/usr/bin/env bash
# Dakota is control-plane only. Devsy and its configured Harvester are reused.
set -euo pipefail
phase=${1:-probe}
case "$phase" in probe|build|live|install|desktop) ;; *) exit 2 ;; esac
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
workspace=omarchy-quattro
machine=devsy-shared-harvester-c577d
head=$(git rev-parse HEAD)
[[ -z $(git status --porcelain) ]] || { echo 'Commit the exact source checkpoint first' >&2; exit 1; }
run=${QUATTRO_RUN:-/home/kdlocpanda/omarchy-builds/quattro-$head}
[[ $run =~ ^/home/kdlocpanda/omarchy-builds/quattro-[a-zA-Z0-9.-]+$ ]]
# Do not provision, resize, or replace a machine on transport errors.
devsy workspace list --result-format json | jq -e --arg name "$workspace" --arg machine "$machine" \
    'any(.[]; .id == $name and .machine.machineId == $machine)' >/dev/null
remote() { devsy --no-status workspace ssh "$workspace" --user root --command "$1"; }
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
git bundle create "$scratch/source.bundle" HEAD
git archive HEAD >"$scratch/source.tar"
remote "mkdir -p $run/source"
tar -C "$scratch" -cf - source.bundle source.tar | remote "tar -xf - -C $run"
remote "tar -xf $run/source.tar -C $run/source; if [ ! -d $run/source/.git ]; then git clone $run/source.bundle $run/git-source; cp -a $run/git-source/.git $run/source/.git; fi; git -c safe.directory=$run/source -C $run/source fetch $run/source.bundle HEAD; git -c safe.directory=$run/source -C $run/source reset --mixed $head"
printf 'Retained Harvester run: %s\n' "$run"
remote "export PATH=/home/kdlocpanda/omarchy-builds/tools:\$PATH; export _EXPERIMENTAL_DAGGER_RUNNER_HOST=docker-container://omarchy-dagger-v0.20.6; cd $run/source; exec 9>/home/kdlocpanda/omarchy-builds/engine.lock; flock -n 9 || exit 1; dagger call host-phase --docker-socket=/var/run/docker.sock --run-path=$run --phase=$phase --execution-id=$head-$(date +%s)"
