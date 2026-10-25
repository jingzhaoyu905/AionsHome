"""Generate through the logged-in Codex CLI and retain named photo references."""
import asyncio
import base64
import json
import shutil
import time
from contextlib import aclosing
from pathlib import Path

from ai_providers import (
    _CODEX_SCRIPT, _build_codex_chat_command, _build_codex_chat_environment,
)
from album import save_generated_image
from codex_app_server import stream_codex_app_server

CODEX_IMAGE_AGENT = 'gpt-6.1-sol'
CODEX_IMAGE_MODEL = 'codex/image-generation'
CODEX_IMAGE_TIMEOUT = 600


def image_command(workspace):
    command = _build_codex_chat_command(
        shutil.which('node') or 'node', _CODEX_SCRIPT, workspace, CODEX_IMAGE_AGENT)
    # Reuse the small companion profile, but replace its chat-only instructions.
    filtered = []
    index = 0
    while index < len(command):
        if command[index] == '-c' and command[index + 1].split('=', 1)[0] in {
            'model_instructions_file', 'developer_instructions', 'features.image_generation',
        }:
            index += 2
        else:
            filtered.append(command[index])
            index += 1
    instructions = (
        'You are the AionsHome image generator. Generate exactly one image using the built-in '
        'image generation tool. Treat the supplied local images as ordered visual references. '
        'Preserve the explicit mapping between each reference number and character name. '
        'Never substitute a text description for generating an image. Do not use APIs, shell '
        'commands, or external services. The user has requested and authorized this generation.'
    )
    return filtered[:-2] + [
        '-c', 'features.image_generation=true',
        '-c', 'forced_login_method="chatgpt"',
        '-c', 'project_doc_max_bytes=0',
        '-c', 'model_reasoning_effort="low"',
        '-c', 'developer_instructions=' + json.dumps(instructions),
    ] + filtered[-2:]


async def generate_codex_image(prompt, is_selfie=False, source_identity='', *, references=None):
    if not _CODEX_SCRIPT:
        raise RuntimeError('未安装 Codex CLI')
    refs = list(references or [])
    reference_bytes = None
    if is_selfie and not references:
        from image_gen import _selfie_reference_path
        path = _selfie_reference_path(source_identity)
        # A missing anchor must not silently turn a requested portrait into a new face.
        reference_bytes = path.read_bytes()
        refs.append({'name': '自拍主角', 'path': str(path)})
    paths = []
    labels = []
    for index, ref in enumerate(refs, 1):
        path = Path(ref['path']).resolve(strict=True)
        paths.append(str(path))
        labels.append(f"参考图 {index} 对应人物：{ref['name']}。保留此人的面部特征，不与其他角色混淆。")
    request = '\n'.join([
        '请使用内置生图工具生成一张图片。',
        *labels,
        '参考照片仅约束各自人物的外貌；服饰、场景、动作按下方要求。只画场景要求的出场人物。',
        prompt,
    ])
    started = time.monotonic()
    print(f'[image_gen] Codex 请求开始，参考图 {len(paths)} 张')
    env = _build_codex_chat_environment()
    # Never inherit an API key into this subscription-only route.
    env.pop('OPENAI_API_KEY', None)
    env.pop('CODEX_API_KEY', None)
    workspace = str(Path(env['CODEX_HOME']))
    image_bytes = None
    explanation = ''
    async with asyncio.timeout(CODEX_IMAGE_TIMEOUT):
        async with aclosing(stream_codex_app_server(
            image_command(workspace), env=env, cwd=workspace, model=CODEX_IMAGE_AGENT,
            prompt=request, image_paths=paths, reasoning_summary='none', image_results=True,
        )) as events:
            async for event in events:
                if event.kind == 'text_delta':
                    explanation = (explanation + event.text)[:1000]
                if event.kind == 'image_started':
                    print(f'[image_gen] Codex 已进入生图阶段（{time.monotonic()-started:.1f}s）')
                if event.kind != 'image':
                    continue
                item = event.image or {}
                if item.get('status') != 'completed' or item.get('failure'):
                    failure = item.get('failure')
                    detail = json.dumps(failure, ensure_ascii=False) if isinstance(failure, dict) else str(failure or item.get('status') or '')
                    raise RuntimeError('Codex 生图未完成：' + detail[:500])
                if item.get('result'):
                    image_bytes = base64.b64decode(item['result'], validate=True)
                elif item.get('savedPath'):
                    path = Path(item['savedPath']).resolve(strict=True)
                    if not path.is_relative_to(Path(workspace).resolve() / 'generated_images'):
                        raise ValueError('Codex 返回了非生图目录的文件')
                    image_bytes = path.read_bytes()
    if not image_bytes:
        raise RuntimeError('Codex 未返回生成图片' + ('：' + explanation if explanation else ''))
    print(f'[image_gen] Codex 已收到图片（{time.monotonic()-started:.1f}s）')
    return await save_generated_image(
        image_bytes, prompt=prompt, model=CODEX_IMAGE_MODEL, actor=source_identity,
        kind='selfie' if is_selfie else 'draw', reference_bytes=reference_bytes,
    )
