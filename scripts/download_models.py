"""Download only selected models, verify size and HF SHA256, then rename atomically."""
import argparse
import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

Z = 'Comfy-Org/z_image_turbo'
WAN = 'Comfy-Org/Wan_2.2_ComfyUI_Repackaged'
WAN21 = 'Comfy-Org/Wan_2.1_ComfyUI_repackaged'
MODELS = {
    'image':[(Z,'diffusion_models/z_image_turbo_bf16.safetensors'),
             (Z,'text_encoders/qwen_3_4b.safetensors'),(Z,'vae/ae.safetensors')],
    'video':[(WAN,'diffusion_models/wan2.2_ti2v_5B_fp16.safetensors'),
             (WAN,'vae/wan2.2_vae.safetensors'),(WAN21,'text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors')],
}


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(8*1024*1024):h.update(block)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--comfy-dir',type=Path,required=True)
    p.add_argument('--model',choices=MODELS,required=True);p.add_argument('--download',action='store_true')
    args=p.parse_args();root=args.comfy_dir.resolve()
    if not (root/'main.py').is_file():p.error('Укажите существующую установку ComfyUI')
    manifest={};plan=[]
    for repo,relative in MODELS[args.model]:
        if repo not in manifest:
            with urllib.request.urlopen('https://huggingface.co/api/models/'+repo+'?blobs=true',timeout=30) as r:manifest[repo]=json.load(r)
        info=manifest[repo];remote='split_files/'+relative
        entry=next(x for x in info['siblings'] if x['rfilename']==remote)
        lfs=entry.get('lfs',{});size=entry.get('size') or lfs.get('size');sha=lfs.get('sha256')
        if not size or not sha:raise RuntimeError('Нет размера или SHA256 в метаданных '+remote)
        dest=root/'models'/relative
        plan.append((repo,info['sha'],remote,dest,size,sha))
        print(f'{relative}: {size/1024**3:.2f} GiB; revision {info["sha"]}')
    total=sum(x[4] for x in plan)
    print(f'Всего {total/1024**3:.2f} GiB. Загрузка использует оплачиваемый трафик и время сервера.')
    if not args.download:
        print('Проверка завершена. Для загрузки добавьте --download.');return
    if shutil.disk_usage(root).free < sum(x[4] for x in plan if not x[3].exists())+5*1024**3:
        raise RuntimeError('Недостаточно свободного места (нужен запас 5 GiB)')
    receipt=[]
    for repo,revision,remote,dest,size,sha in plan:
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():
            if dest.stat().st_size==size and digest(dest)==sha:
                print('Проверен, пропускаем:',dest.name)
                receipt.append({'file':str(dest.relative_to(root)),'repo':repo,'revision':revision,'sha256':sha});continue
            raise RuntimeError(f'Существующий файл не совпадает с upstream: {dest}. Не перезаписываем автоматически.')
        url=f'https://huggingface.co/{repo}/resolve/{revision}/{remote}'
        part=dest.with_suffix(dest.suffix+'.part')
        h=hashlib.sha256();received=0
        with urllib.request.urlopen(url,timeout=120) as response,part.open('wb') as f:
            while chunk:=response.read(8*1024*1024):
                f.write(chunk);h.update(chunk);received+=len(chunk)
        if received!=size or h.hexdigest()!=sha:raise RuntimeError('Не прошла проверка файла '+dest.name)
        part.replace(dest);print('Скачан и проверен:',dest.name)
        receipt.append({'file':str(dest.relative_to(root)),'repo':repo,'revision':revision,'sha256':sha})
    (root/'models'/('studio-'+args.model+'-manifest.json')).write_text(json.dumps(receipt,indent=2))


if __name__=='__main__':main()
