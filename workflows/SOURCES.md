# Источники workflow

Проверено 2026-10-13. API-графы в `studio/workflows.py` адаптированы из официальных шаблонов:

- https://github.com/Comfy-Org/workflow_templates/blob/e28fce217e4fd410b7e782ba55e15df1c6d1f2a6/templates/image_z_image_turbo.json
- https://github.com/Comfy-Org/workflow_templates/blob/e28fce217e4fd410b7e782ba55e15df1c6d1f2a6/templates/video_wan2_2_5B_ti2v.json
- https://docs.comfy.org/tutorials/image/z-image/z-image-turbo
- https://docs.comfy.org/tutorials/video/wan/wan2_2

Проверенная при разработке ревизия исходников ComfyUI:
`8010b48d2c1be986613f8e8c761527d205bd756f`.
Это ссылка на исходники, не утверждение о проведённом GPU-тесте.

Z-Image: 8 шагов, CFG 1, res_multistep/simple, AuraFlow shift 3.
Wan: 20 шагов, CFG 5, uni_pc/simple, SD3 shift 8, 24 fps,
121 или 241 кадр. Итоговая длительность немного больше 5/10 секунд.
Исходный кадр подключается к Wan22ImageToVideoLatent для I2V.

Workflow каждого отправленного задания сохраняется в STUDIO_DATA/workflows
и скачивается из карточки результата в формате ComfyUI API.
Предварительно проверяются наличие узлов и имена файлов моделей через /object_info;
полную валидацию графа выполняет /prompt на реальном ComfyUI.

Лицензия шаблонов: https://github.com/Comfy-Org/workflow_templates/blob/main/LICENSE
Модели Z-Image-Turbo и Wan 2.2 распространяются под Apache-2.0.
