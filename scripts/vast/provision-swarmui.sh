#!/bin/bash
# Provisioning for the Vast SwarmUI image (run via PROVISIONING_SCRIPT on every start, see the boot hook below).
# Installs SwarmUI's ComfyUI backend, the node packs from custom_nodes.txt, the models and workflows from
# models.txt and the workflows from workflows/ (they show up in SwarmUI's "Comfy Workflow" tab).
# Anything already in place is skipped, so a restart only fills in what is missing.
set -uo pipefail
SWARM=${WORKSPACE:-/workspace}/SwarmUI
RAW=${STUDIO_REPO_RAW:-https://raw.githubusercontent.com/progeroffline/ai-content-generation/main}
log(){ echo "[studio-provision] $(date +%T) $*"; }

cd "$SWARM" || { log "SwarmUI not found in $SWARM"; exit 1; }

# Vast runs PROVISIONING_SCRIPT (boot step 75) only while /.provisioning_complete is absent;
# removing it in an earlier boot step makes every start run this script again.
printf '#!/bin/bash\nrm -f /.provisioning_complete\n' > /etc/vast_boot.d/70-studio-reprovision.sh
chmod +x /etc/vast_boot.d/70-studio-reprovision.sh
. /venv/main/bin/activate

# $1: "swarm" for SwarmUI model folders, "comfy" for comfy/<subfolder> and workflows (need the backend installed).
download_models(){
    curl -fsSL "$RAW/scripts/vast/models.txt" -o "/tmp/models-$1.txt" || { log "models.txt unavailable"; return; }
    grep -vE '^\s*(#|$)' "/tmp/models-$1.txt" | while read -r folder url name; do
        case "$folder" in
            comfy/*) [ "$1" = comfy ] || continue; dir="dlbackend/ComfyUI/models/${folder#comfy/}";;
            workflows) [ "$1" = comfy ] || continue; dir="dlbackend/ComfyUI/user/default/workflows";;
            *) [ "$1" = swarm ] || continue; dir="Models/$folder";;
        esac
        dest="$dir/$name"
        [ -s "$dest" ] && { log "present: $dest"; continue; }
        mkdir -p "$dir"
        auth=()
        case "$url" in *civitai.com*)
            [ -n "${CIVITAI_TOKEN:-}" ] || { log "skip $name: CIVITAI_TOKEN is not set"; continue; }
            # Passed through a config file so the token does not show up in the process list.
            printf 'header = "Authorization: Bearer %s"\n' "$CIVITAI_TOKEN" > /tmp/civitai.curl
            auth=(--config /tmp/civitai.curl);;
        esac
        log "downloading $dest"
        # Civitai redirects to a signed storage URL; resolving it here keeps the token away from aria2c
        # (curl, unlike wget, does not forward the header to the storage host either).
        [ ${#auth[@]} -gt 0 ] && signed=$(curl -fsS "${auth[@]}" -o /dev/null -w '%{redirect_url}' "$url") && [ -n "$signed" ] &&
            { url=$signed; auth=(); }
        # Storage hosts cap a single connection (Civitai at ~15 MB/s); aria2c pulls one file over 16.
        if [ ${#auth[@]} -eq 0 ] && command -v aria2c >/dev/null; then
            aria2c -q -x 16 -s 16 -k 1M --file-allocation=none --allow-overwrite=true -d "$dir" -o "$name.part" "$url"
        else
            curl -fsSL "${auth[@]}" -o "$dest.part" "$url"
        fi && [ -s "$dest.part" ] && { mv "$dest.part" "$dest"; log "done: $dest"; continue; }
        rm -f "$dest.part" "$dest.part.aria2"; log "FAILED: $dest"
    done
}

command -v aria2c >/dev/null || apt-get install -y -qq aria2 >/dev/null 2>&1 ||
    { apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq aria2 >/dev/null 2>&1; } || log "aria2 install FAILED, downloads use curl"
download_models swarm &

# The image ships SwarmUI 0.9.8-Beta, which forces ComfyUI frontend 1.37 onto current ComfyUI and
# breaks the workflows. The latest SwarmUI needs .NET 10 and NuGet, which some hosts cannot reach, so
# it is built here: on failure the checkout goes back to the build in place, and SwarmUI starts at once
# instead of retrying a doomed build on every launch.
log "updating SwarmUI"
[ -f src/bin/studio_image_commit ] || git rev-parse HEAD > src/bin/studio_image_commit
target=$(git fetch -q origin && git rev-parse origin/master) || log "SwarmUI fetch FAILED"
if [ -n "$target" ] && [ "$target" != "$(cat src/bin/studio_build 2>/dev/null)" ]; then
    # Both NuGet and the .NET downloads sit behind Microsoft's CDN; without NuGet a build is pointless.
    curl -fsS -m 20 -o /dev/null https://api.nuget.org/v3/index.json && nuget=yes || nuget=
    if [ -n "$nuget" ] && ! dotnet --list-sdks | grep -q '^10\.'; then
        curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh &&
            bash /tmp/dotnet-install.sh --channel 10.0 --install-dir "${DOTNET_ROOT:-/opt/dotnet}" >/dev/null || log ".NET 10 install FAILED"
    fi
    if [ -n "$nuget" ] && dotnet --list-sdks | grep -q '^10\.'; then
        log "building SwarmUI"
        rm -rf src/bin/studio_new
        if git checkout -q -B master "$target" &&
            timeout 1200 dotnet build src/SwarmUI.csproj --configuration Release -o src/bin/studio_new >/tmp/swarm-build.log 2>&1 &&
            [ -f src/bin/studio_new/SwarmUI.dll ]; then
            rm -rf src/bin/live_release src/bin/extensions && mv src/bin/studio_new src/bin/live_release
            echo "$target" > src/bin/studio_build
            log "SwarmUI built"
        else
            log "SwarmUI build FAILED (see /tmp/swarm-build.log)"
        fi
    else
        log "SwarmUI update skipped: .NET 10 or NuGet unreachable from this host"
    fi
fi
# Keep the checkout on the commit the running build came from, or SwarmUI rebuilds itself on launch.
built=$(cat src/bin/studio_build 2>/dev/null || cat src/bin/studio_image_commit)
git checkout -q -B master "$built" || log "SwarmUI checkout FAILED"
echo "$built" > src/bin/last_build

if [ ! -f dlbackend/ComfyUI/main.py ]; then
    log "installing ComfyUI backend"
    SWARM_NO_VENV=1 bash launchtools/comfy-install-linux.sh nv || log "backend install FAILED"
fi
# The install script does not stop on a partly failed requirements install; finish it.
pip install -q -r dlbackend/ComfyUI/requirements.txt || log "backend requirements FAILED"
python -c "import torchsde" || log "backend requirements still incomplete"

log "installing node packs"
curl -fsSL "$RAW/scripts/vast/custom_nodes.txt" | grep -vE '^\s*(#|$)' | while read -r repo; do
    dir="dlbackend/ComfyUI/custom_nodes/$(basename "$repo")"
    [ -d "$dir" ] && { log "node pack present: $(basename "$repo")"; continue; }
    git clone -q --depth 1 "$repo" "$dir" || { log "node pack FAILED: $repo"; continue; }
    # Only on a fresh clone: Impact-Pack's sam2 build pulls a whole new torch, which a restart must not redo.
    [ -f "$dir/requirements.txt" ] && { pip install -q -r "$dir/requirements.txt" || log "node pack requirements FAILED: $repo"; }
    log "node pack ready: $(basename "$repo")"
done
log "installing workflows"
download_models comfy
# The author's workflow loads the int8_convrot variant; servers get Turbo fp16 (his pick for 24 GB cards).
sed -i 's/lustify-v10-krea-turbo-int8_convrot\.safetensors/lustify-v10-krea-turbo-fp16.safetensors/' \
    dlbackend/ComfyUI/user/default/workflows/lustify-krea2.json 2>/dev/null
# raw.githubusercontent.com cannot list a folder; the GitHub API can (RAW is .../<owner>/<repo>/<ref>).
listing=$(echo "$RAW" | sed -E 's#^https://raw\.githubusercontent\.com/([^/]+/[^/]+)/(.+)$#https://api.github.com/repos/\1/contents/scripts/vast/workflows?ref=\2#')
workflows=$(curl -fsSL "$listing" | python -c 'import json, sys
for f in json.load(sys.stdin):
    if f["name"].endswith(".json"): print(f["name"] + "\t" + f["download_url"])' 2>/dev/null) || log "no workflows in the repo folder"
echo "$workflows" | while IFS=$'\t' read -r name url; do
    [ -n "$name" ] || continue
    # Kept once present, like models: a restart must not drop changes saved into the same file.
    [ -s "dlbackend/ComfyUI/user/default/workflows/$name" ] && { log "present: workflow $name"; continue; }
    curl -fsSL "$url" -o "dlbackend/ComfyUI/user/default/workflows/$name" && log "workflow ready: $name" || log "workflow FAILED: $name"
done
# Opens lustify-krea2.json on a fresh ComfyUI canvas instead of the stock Z-Image graph.
mkdir -p dlbackend/ComfyUI/custom_nodes/comfy-studio/web
for file in __init__.py web/default_workflow.js; do
    curl -fsSL "$RAW/scripts/vast/comfy-studio/$file" -o "dlbackend/ComfyUI/custom_nodes/comfy-studio/$file" || log "comfy-studio FAILED: $file"
done
mkdir -p Data
[ -f Data/Backends.fds ] || printf '0:\n\ttype: comfyui_selfstart\n\ttitle: ComfyUI\n\tenabled: true\n\tsettings:\n\t\tStartScript: dlbackend/ComfyUI/main.py\n\t\tAutoUpdate: false\n' > Data/Backends.fds
# The image's SwarmUI would pin ComfyUI's frontend to 1.37; keep the one ComfyUI itself requires then.
if [ -f src/bin/studio_build ]; then
    sed -i '/^\t\tFrontendVersion:/d' Data/Backends.fds
else
    grep -q '^\t\tFrontendVersion:' Data/Backends.fds || printf '\t\tFrontendVersion: None\n' >> Data/Backends.fds
fi
if [ -f Data/Settings.fds ]; then
    sed -i 's/^IsInstalled: false/IsInstalled: true/' Data/Settings.fds
else
    echo 'IsInstalled: true' > Data/Settings.fds
fi
# Keep model metadata in a JSON next to the model: rewriting a 25 GB model needs as much free disk again.
sed -i -E 's/^(\s*)EditMetadataWriteJSON: false/\1EditMetadataWriteJSON: true/; s/^(\s*)ImageMetadataIncludeModelHash: true/\1ImageMetadataIncludeModelHash: false/' Data/Settings.fds

wait
log "provisioning finished"
