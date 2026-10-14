"""Create a private Vast template: the recommended SwarmUI template plus our provisioning.

Run on the panel server: python3 scripts/vast/create_template.py /opt/ai-content-generation/.env
Prints the template hash to put into VAST_TEMPLATE_ID.
"""
import json
import sys
import urllib.parse
import urllib.request

API = 'https://console.vast.ai/api/v0/template/'
PROVISION = 'https://raw.githubusercontent.com/progeroffline/ai-content-generation/main/scripts/vast/provision-swarmui.sh'


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
    base = next(t for t in call(key, 'GET', API+'?'+query)['templates'] if t.get('image') == 'vastai/swarmui')
    extra = f' -e PROVISIONING_SCRIPT="{PROVISION}"'
    if env.get('CIVITAI_TOKEN'):
        extra += f' -e CIVITAI_TOKEN="{env["CIVITAI_TOKEN"]}"'
    body = {k: base.get(k) for k in ('image', 'tag', 'onstart', 'runtype', 'jup_direct', 'ssh_direct', 'use_ssh',
                                     'jupyter_dir', 'use_jupyter_lab', 'extra_filters', 'recommended_disk_space')}
    body.update(name='Studio SwarmUI', desc='SwarmUI with ComfyUI backend and studio models', private=True,
                env=(base.get('env') or '')+extra)
    created = call(key, 'POST', API, body)
    template = created.get('template') or created
    print(template.get('hash_id') or created)


if __name__ == '__main__':
    main()
