"""Chapter speech plans and durable, content/voice-addressed audio files."""

import asyncio
import hashlib
import json
import re
import shutil
from pathlib import Path
from weakref import WeakValueDictionary

from tts import split_text_for_tts, synthesize_text_to_mp3

_clip_locks = WeakValueDictionary()


def book_audio_dir(root: Path, book_id: str, title: str = '') -> Path:
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', book_id):
        raise ValueError('无效的书籍编号')
    # Keep a previously chosen folder stable even if the book is renamed.
    existing = sorted(p for p in root.glob(f'*--{book_id}') if p.is_dir())
    if existing:
        return existing[0]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title or '未命名书籍')[:60].strip(' .') or '未命名书籍'
    return root / f'{name}--{book_id}'


def preserve_legacy_audio(source: Path, destination: Path):
    """Copy old recordings once; never resurrect recordings the user later deletes."""
    if not source.is_dir():
        return
    marker = source / '.reading-audio-imported.json'
    imported = set(json.loads(marker.read_text(encoding='utf-8'))) if marker.exists() else set()
    updated = set(imported)
    for path in source.glob('*.mp3'):
        if not re.fullmatch(r'[a-f0-9]{64}\.mp3', path.name) or path.name in imported or not audio_ready(path):
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / path.name
        if not audio_ready(target):
            temporary = target.with_suffix('.copy')
            shutil.copy2(path, temporary)
            temporary.replace(target)
        updated.add(path.name)
    if updated != imported:
        temporary = marker.with_suffix('.tmp')
        temporary.write_text(json.dumps(sorted(updated)), encoding='utf-8')
        temporary.replace(marker)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def audio_ready(path: Path):
    return path.is_file() and path.stat().st_size > 0


def refresh_plan(plan: dict, cache_dir: Path):
    clips = [dict(clip, ready=audio_ready(cache_dir / f"{clip['key']}.mp3"))
             for clip in plan['clips']]
    missing = {clip['key']: clip for clip in clips if not clip['ready']}
    return dict(plan, clips=clips, missing_count=len(missing),
                missing_chars=sum(c['chars'] for c in missing.values()))


def reading_chunks(text):
    # Finished EPUB text can contain an unmatched bracket. The chat splitter
    # keeps that tail together for streaming; a book request must still be bounded.
    for chunk in split_text_for_tts(text):
        for offset in range(0, len(chunk), 500):
            if part := chunk[offset:offset + 500].strip():
                yield part


def build_plan(data: dict, config: dict, narrator: str, cache_dir: Path):
    names = {'aion': data.get('ai_name') or 'AI',
             'connor': data.get('connor_name') or '另一位伴侣'}
    voices = {who: (config.get(f'tts_{who}_voice') or '').strip() for who in names}
    clips = []
    missing_voices = []
    segments = data['chapter'].get('segments_meta') or []
    summaries_by_end = {}
    for who, field in [('aion', 'summaries'), ('connor', 'connor_summaries')]:
        for summary in data.get(field) or []:
            index = summary.get('segment_index')
            if isinstance(index, int) and 0 <= index < len(segments):
                end = segments[index].get('end_p')
                summaries_by_end.setdefault(end, []).append((who, 'summary', summary.get('summary') or ''))
    for paragraph, body in enumerate(data['chapter']['paragraphs']):
        parts = [(narrator, 'body', body)]
        for who, field in [('aion', 'annotations'), ('connor', 'connor_annotations')]:
            annotations = data.get(field) or {}
            annotation = annotations.get(paragraph, annotations.get(str(paragraph), {}))
            parts.append((who, 'annotation', annotation.get('text', '')))
        parts.extend(summaries_by_end.get(paragraph, []))
        for speaker, kind, text in parts:
            for chunk in reading_chunks(text):
                voice = voices[speaker]
                if not voice and names[speaker] not in missing_voices:
                    missing_voices.append(names[speaker])
                key = _digest(['reading-v1', voice, chunk])
                clips.append(dict(key=key, text=chunk, voice=voice, speaker=speaker,
                                  name=names[speaker], kind=kind, paragraph=paragraph,
                                  chars=len(chunk), ready=audio_ready(cache_dir / f'{key}.mp3')))
    missing = {clip['key']: clip for clip in clips if not clip['ready']}
    return dict(
        narrator=narrator, clips=clips,
        signature=_digest([narrator, [(c['key'], c['paragraph'], c['kind']) for c in clips]]),
        body_chars=data['chapter'].get('char_count') or sum(len(p) for p in data['chapter']['paragraphs']),
        annotation_chars=sum(c['chars'] for c in clips if c['kind'] == 'annotation'),
        summary_chars=sum(c['chars'] for c in clips if c['kind'] == 'summary'),
        missing_chars=sum(c['chars'] for c in missing.values()),
        missing_count=len(missing), missing_voices=missing_voices,
        narrators=[dict(id=who, name=names[who], configured=bool(voices[who])) for who in names],
    )


async def synthesize_plan(plan: dict, cache_dir: Path, progress=None):
    if plan['missing_voices']:
        raise ValueError('请先在群聊配置音色：' + '、'.join(plan['missing_voices']))
    cache_dir.mkdir(parents=True, exist_ok=True)
    for index, clip in enumerate(plan['clips']):
        path = cache_dir / f"{clip['key']}.mp3"
        lock = _clip_locks.setdefault(str(path), asyncio.Lock())
        async with lock:
            if not audio_ready(path):
                temporary = path.with_suffix('.part.mp3')
                try:
                    await synthesize_text_to_mp3(clip['text'], clip['voice'], temporary,
                                                 concurrency=1, segment_prefix=clip['key'])
                    if not audio_ready(temporary):
                        raise RuntimeError('语音服务没有返回音频')
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
                    for segment in cache_dir.glob(f"{clip['key']}_s*.mp3"):
                        segment.unlink(missing_ok=True)
        if progress:
            progress(index + 1)
