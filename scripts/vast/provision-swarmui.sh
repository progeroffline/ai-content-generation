#!/bin/bash
# First-boot provisioning for the Vast SwarmUI image (run via PROVISIONING_SCRIPT).
# Installs SwarmUI's ComfyUI backend and downloads the models from models.txt.
set -uo pipefail
SWARM=${WORKSPACE:-/workspace}/SwarmUI
RAW=${STUDIO_REPO_RAW:-https://raw.githubusercontent.com/progeroffline/ai-content-generation/main}
log(){ echo "[studio-provision] $(date +%T) $*"; }

cd "$SWARM" || { log "SwarmUI not found in $SWARM"; exit 1; }
. /venv/main/bin/activate

download_models(){
    curl -fsSL "$RAW/scripts/vast/models.txt" -o /tmp/models.txt || { log "models.txt unavailable"; return; }
    grep -vE '^\s*(#|$)' /tmp/models.txt | while read -r folder url name; do
        dest="Models/$folder/$name"
        [ -s "$dest" ] && { log "present: $dest"; continue; }
        mkdir -p "Models/$folder"
        auth=()
        case "$url" in *civitai.com*)
            [ -n "${CIVITAI_TOKEN:-}" ] || { log "skip $name: CIVITAI_TOKEN is not set"; continue; }
            auth=(--header "Authorization: Bearer $CIVITAI_TOKEN");;
        esac
        log "downloading $dest"
        if wget -q "${auth[@]}" -O "$dest.part" "$url" && [ -s "$dest.part" ]; then
            mv "$dest.part" "$dest"; log "done: $dest"
        else
            rm -f "$dest.part"; log "FAILED: $dest"
        fi
    done
}

download_models &

if [ ! -f dlbackend/ComfyUI/main.py ]; then
    log "installing ComfyUI backend"
    SWARM_NO_VENV=1 bash launchtools/comfy-install-linux.sh nv || log "backend install FAILED"
fi
mkdir -p Data
[ -f Data/Backends.fds ] || printf '0:\n\ttype: comfyui_selfstart\n\ttitle: ComfyUI\n\tenabled: true\n\tsettings:\n\t\tStartScript: dlbackend/ComfyUI/main.py\n\t\tAutoUpdate: false\n' > Data/Backends.fds
if [ -f Data/Settings.fds ]; then
    sed -i 's/^IsInstalled: false/IsInstalled: true/' Data/Settings.fds
else
    echo 'IsInstalled: true' > Data/Settings.fds
fi

wait
log "provisioning finished"
