# Software half of the studio image: Vast's ComfyUI image plus our node packs, workflows and default graph.
# The models are not built here: .github/workflows/comfy-image.yml rebases this image onto the models image
# (BASE plus one layer per file of models.txt), so a software change never re-uploads 75 GB.
ARG BASE=vastai/comfy:v0.40.0-cuda-12.9-py312
FROM ${BASE}
SHELL ["/bin/bash", "-c"]

COPY custom_nodes.txt /tmp/custom_nodes.txt
RUN set -euo pipefail && \
    . /venv/main/bin/activate && \
    # Node packs must not replace the image's torch (Impact-Pack's sam2 would pull a whole new one).
    pip list --format=freeze | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' > /tmp/torch.txt && \
    cd /opt/workspace-internal/ComfyUI/custom_nodes && \
    for repo in $(grep -vE '^\s*(#|$)' /tmp/custom_nodes.txt); do \
        git clone -q --depth 1 "$repo" && dir=$(basename "$repo") && \
        if [ -f "$dir/requirements.txt" ]; then uv pip install --no-cache-dir -c /tmp/torch.txt -r "$dir/requirements.txt"; fi || exit 1; \
    done && \
    rm -f /tmp/custom_nodes.txt /tmp/torch.txt

# Opens lustify-krea2.json on a fresh canvas instead of ComfyUI's stock graph.
COPY comfy-studio /opt/workspace-internal/ComfyUI/custom_nodes/comfy-studio
COPY workflows /opt/workspace-internal/ComfyUI/user/default/workflows
# Models stay in /opt/model_store: the image's first boot copies /opt/workspace-internal to /workspace.
COPY extra_model_paths.yaml /opt/workspace-internal/ComfyUI/extra_model_paths.yaml

RUN set -euo pipefail && \
    . /venv/main/bin/activate && \
    cd /opt/workspace-internal/ComfyUI && \
    python main.py --cpu --listen 127.0.0.1 --port 11404 --disable-auto-launch --quick-test-for-ci 2>&1 | tee /tmp/quick-test.log && \
    ! grep -q 'IMPORT FAILED' /tmp/quick-test.log && rm /tmp/quick-test.log
