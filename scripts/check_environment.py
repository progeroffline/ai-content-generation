"""Run with ComfyUI's own Python interpreter before spending time on model downloads."""
import argparse
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--comfy-dir',type=Path,required=True)
p.add_argument('--comfy-url',default='http://127.0.0.1:18188');args=p.parse_args()
import torch
if not torch.cuda.is_available():raise SystemExit('CUDA недоступна')
x=torch.ones((128,128),device='cuda');assert (x@x)[0,0].item()==128
report={'python':sys.version,'platform':platform.platform(),'torch':torch.__version__,
        'cuda_runtime':torch.version.cuda,'gpu':torch.cuda.get_device_name(0),
        'vram_GiB':torch.cuda.get_device_properties(0).total_memory/1024**3,
        'disk_free_GiB':shutil.disk_usage(args.comfy_dir).free/1024**3,
        'comfy_commit':subprocess.check_output(['git','-C',str(args.comfy_dir),'rev-parse','HEAD'],text=True).strip()}
with urllib.request.urlopen(args.comfy_url+'/system_stats',timeout=10) as r:report['comfy']=json.load(r)
print(json.dumps(report,indent=2,ensure_ascii=False))
