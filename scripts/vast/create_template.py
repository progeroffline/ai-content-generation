"""Create a private Vast template: the recommended ComfyUI template running our image (VAST_IMAGE).

Run on the panel server: python3 scripts/vast/create_template.py /opt/ai-content-generation/.env
Prints the template hash to put into VAST_TEMPLATE_ID.
"""
import json
import re
import sys
import urllib.parse
import urllib.request

API = 'https://console.vast.ai/api/v0/template/'


def read_env(path):
    env = {}
    for line in open(path, encoding='utf-8'):
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('"\'')
    return env


def call(key, method, url, body=None):
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main():
    env = read_env(sys.argv[1] if len(sys.argv) > 1 else '.env')
    key = env['VAST_API_KEY']
    query = urllib.parse.urlencode({'select_filters': json.dumps({'recommended': {'eq': True}})})
    base = next(t for t in call(key, 'GET', API+'?'+query)['templates'] if t.get('image') == 'vastai/comfy')
    image, _, tag = env['VAST_IMAGE'].partition(':')
    # The image already has its workflows; the base template would download an example one.
    base_env = re.sub(r' -e PROVISIONING_COMFYUI_WORKFLOWS="[^"]*"', '', base.get('env') or '')
    filters = json.loads(base.get('extra_filters') or '{}') | {'cuda_max_good': {'gte': 12.9}}
    body = {k: base.get(k) for k in ('onstart', 'runtype', 'jup_direct', 'ssh_direct', 'use_ssh',
                                     'jupyter_dir', 'use_jupyter_lab')}
    body.update(name='Studio ComfyUI', desc='ComfyUI with the studio models and workflows', private=True,
                image=image, tag=tag or 'latest', env=base_env, extra_filters=filters,
                recommended_disk_space=150, docker_login_repo='docker.io')
    created = call(key, 'POST', API, body)
    template = created.get('template') or created
    print(template.get('hash_id') or created)


if __name__ == '__main__':
    main()
