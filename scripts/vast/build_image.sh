#!/bin/bash
# Build steps for the studio image, run by .github/workflows/comfy-image.yml.
#   build_image.sh workflows                       fetch the workflows lines of models.txt into workflows/
#   build_image.sh models BASE TARGET DL_DIR TAR_DIR  push BASE plus one layer per model as TARGET
# Large files are downloaded on two disks: the file on DL_DIR, its layer tarball on TAR_DIR.
set -euo pipefail
cd "$(dirname "$0")"
log(){ echo "[build] $(date +%T) $*"; }

# $1 url, $2 destination file. Civitai redirects to a signed URL, which keeps the token away from aria2c.
fetch(){
    local url=$1
    case "$url" in *civitai.com*)
        printf 'header = "Authorization: Bearer %s"\n' "$CIVITAI_TOKEN" > /tmp/civitai.curl
        url=$(curl -fsS --config /tmp/civitai.curl -o /dev/null -w '%{redirect_url}' "$url");;
    esac
    # Storage hosts cap one connection (Civitai at ~15 MB/s); one connection per 64 MB keeps small files on one.
    aria2c -q -x 16 -s 16 -k 64M --max-tries=5 --retry-wait=5 --file-allocation=none --allow-overwrite=true \
        -d "$(dirname "$2")" -o "$(basename "$2")" "$url"
    [ -s "$2" ]
}

lines(){ grep -vE '^\s*(#|$)' models.txt; }

case "$1" in
workflows)
    lines | while read -r folder url name; do
        [ "$folder" = workflows ] || continue
        fetch "$url" "workflows/$name"
        log "workflow: $name"
    done
    # The author's workflow loads the int8_convrot variant; the image has Turbo fp16 (his pick for 24 GB cards).
    sed -i 's/lustify-v10-krea-turbo-int8_convrot\.safetensors/lustify-v10-krea-turbo-fp16.safetensors/' workflows/lustify-krea2.json
    ;;
models)
    base=$2 target=$3 dl=$4 tars=$5
    lines | while read -r folder url name; do
        [ "$folder" = workflows ] && continue
        rm -rf "$dl/opt"
        file="$dl/opt/model_store/$folder/$name"
        mkdir -p "$(dirname "$file")"
        start=$SECONDS
        fetch "$url" "$file"
        # Fixed owner and mtime make the tarball, and so the layer digest, repeatable: a rerun skips the upload.
        tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -C "$dl" -cf "$tars/layer.tar" "opt/model_store/$folder/$name"
        rm -rf "$dl/opt"
        crane append -b "$base" -f "$tars/layer.tar" -t "$target"
        rm -f "$tars/layer.tar"
        base=$target
        log "layer: $folder/$name ($((SECONDS - start))s)"
    done
    ;;
esac
