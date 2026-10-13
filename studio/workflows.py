"""API graphs adapted from the official Comfy-Org templates (see workflows/SOURCES.md)."""
import secrets


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Ожидается JSON-объект')
    prompt = data.get('prompt', '')
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000:
        raise ValueError('Промпт должен содержать от 1 до 4000 символов')
    mode = data.get('mode', 'image')
    if mode not in ('image', 'video', 'image_video'):
        raise ValueError('Неизвестный режим')
    size = data.get('size', 'square' if mode == 'image' else 'landscape')
    sizes = {'square': (1024, 1024), 'landscape': (1216, 832), 'portrait': (832, 1216)}
    if mode != 'image':
        sizes = {'landscape': (1280, 704), 'portrait': (704, 1280)}
    if size not in sizes:
        raise ValueError('Недопустимое соотношение сторон')
    seconds = data.get('seconds', 5)
    if type(seconds) is not int or seconds not in (5, 10):
        raise ValueError('Длительность: 5 или 10 секунд')
    seed = data.get('seed')
    if seed is None:
        seed = secrets.randbelow(2**32)
    if type(seed) is not int or not 0 <= seed <= 2**32-1:
        raise ValueError('Seed: целое число от 0 до 4294967295')
    source = data.get('source_id')
    if mode == 'image_video' and (not isinstance(source, str) or len(source) != 32):
        raise ValueError('Выберите исходное изображение')
    return dict(mode=mode, prompt=prompt.strip(), size=size, width=sizes[size][0],
                height=sizes[size][1], seconds=seconds, seed=seed, source_id=source)


def node(kind, **inputs):
    return {'class_type': kind, 'inputs': inputs}


def build_graph(p, job_id, source_name=None):
    prefix = 'studio/' + job_id
    if p['mode'] == 'image':
        return {
            '1': node('UNETLoader', unet_name='z_image_turbo_bf16.safetensors', weight_dtype='default'),
            '2': node('CLIPLoader', clip_name='qwen_3_4b.safetensors', type='lumina2', device='default'),
            '3': node('VAELoader', vae_name='ae.safetensors'),
            '4': node('CLIPTextEncode', text=p['prompt'], clip=['2', 0]),
            '5': node('ConditioningZeroOut', conditioning=['4', 0]),
            '6': node('EmptySD3LatentImage', width=p['width'], height=p['height'], batch_size=1),
            '7': node('ModelSamplingAuraFlow', model=['1', 0], shift=3.0),
            '8': node('KSampler', model=['7', 0], positive=['4', 0], negative=['5', 0],
                      latent_image=['6', 0], seed=p['seed'], steps=8, cfg=1.0,
                      sampler_name='res_multistep', scheduler='simple', denoise=1.0),
            '9': node('VAEDecode', samples=['8', 0], vae=['3', 0]),
            '10': node('SaveImage', images=['9', 0], filename_prefix=prefix),
        }
    g = {
        '1': node('UNETLoader', unet_name='wan2.2_ti2v_5B_fp16.safetensors', weight_dtype='default'),
        '2': node('CLIPLoader', clip_name='umt5_xxl_fp8_e4m3fn_scaled.safetensors', type='wan', device='default'),
        '3': node('VAELoader', vae_name='wan2.2_vae.safetensors'),
        '4': node('CLIPTextEncode', text=p['prompt'], clip=['2', 0]),
        '5': node('CLIPTextEncode', text='overexposed, blurry, subtitles, static, deformed hands, distorted face, extra limbs', clip=['2', 0]),
        '6': node('Wan22ImageToVideoLatent', vae=['3', 0], width=p['width'], height=p['height'],
                  length=p['seconds']*24+1, batch_size=1),
        '7': node('ModelSamplingSD3', model=['1', 0], shift=8.0),
        '8': node('KSampler', model=['7', 0], positive=['4', 0], negative=['5', 0],
                  latent_image=['6', 0], seed=p['seed'], steps=20, cfg=5.0,
                  sampler_name='uni_pc', scheduler='simple', denoise=1.0),
        '9': node('VAEDecode', samples=['8', 0], vae=['3', 0]),
        '10': node('CreateVideo', images=['9', 0], fps=24.0),
        '11': node('SaveVideo', video=['10', 0], filename_prefix=prefix, format='mp4', codec='h264'),
    }
    if p['mode'] == 'image_video':
        if not source_name:
            raise ValueError('Исходный кадр не загружен в ComfyUI')
        g['12'] = node('LoadImage', image=source_name)
        g['6']['inputs']['start_image'] = ['12', 0]
    return g
