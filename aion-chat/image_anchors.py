"""Named portrait references shared by everyday chat and the theater."""
import json
import re
from pathlib import Path

from config import UPLOADS_DIR
from database import get_db


async def load_anchors(*, db_factory=None):
    # Keep the existing theater records and IDs; no migration or second copy.
    async with (db_factory or get_db)() as db:
        cursor = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='theater_studio'")
        if not await cursor.fetchone():
            return []
        cursor = await db.execute("SELECT data FROM theater_studio WHERE kind='anchor' ORDER BY rowid")
        return [json.loads(row[0]) for row in await cursor.fetchall()]


def reference_path(url, root=None):
    root = Path(root if root is not None else UPLOADS_DIR).resolve()
    if not str(url).startswith('/uploads/'):
        raise ValueError('请先上传人物锚点照片')
    path = (root / url.removeprefix('/uploads/')).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
        raise ValueError('人物锚点照片失效，请重新上传')
    return path


def identity_names():
    from chatroom import get_chatroom_names
    return dict(zip(('user', 'aion', 'connor'), get_chatroom_names()))


def anchor_identity(anchor, names):
    if anchor.get('identity'):
        return anchor['identity']
    labels = {name.strip().casefold() for name in
              [anchor['name'], *re.split(r'[,，;；]', anchor.get('aliases', ''))] if name.strip()}
    matches = [key for key, name in names.items() if name.casefold() in labels]
    return matches[0] if len(matches) == 1 else ''


async def chat_anchor_prompt(who='aion'):
    anchors = await load_anchors()
    if not anchors:
        return ''
    names = identity_names()
    roster = []
    for anchor in anchors:
        identity = anchor_identity(anchor, names)
        relation = '当前发言者（你）' if identity == who else ('聊天用户' if identity == 'user' else '另一位聊天伙伴' if identity else '已保存的人物')
        roster.append(f"{anchor['id']} = {anchor['name']}（别名：{anchor.get('aliases') or '无'}；{relation}）")
    return (
        '【聊天生图人物锚点】\n' + '\n'.join(roster) + '\n'
        '生图时根据当前对话明确实际出场人物，把“我、你、我们”等称呼转换成上面的人物。'
        '在原生图指令内写出人物编号，格式为 [DRAW: characters=人物编号1,人物编号2 | English scene prompt]；'
        '自拍也可使用 [SELFIE: characters=人物编号 | English scene prompt]。'
        '只列实际出场的人物，不要把未出场的人加入。没有上述人物时使用 [DRAW: characters=none | English scene prompt]。'
        '场景描述中用对应人物的名字明确动作与位置，不用编号或含糊代词代替名字；不要翻译人物姓名。'
        '系统会按编号带上各自照片；照片只固定外貌，服饰、动作、画风与场景服从本次要求。'
        '若当前发言者还没有对应人物锚点，单人自拍仍可用原来的 [SELFIE: English scene prompt]。'
    )


async def resolve_chat_references(prompt, is_selfie=False, source_identity=''):
    anchors = await load_anchors()
    names = identity_names()
    speaker = source_identity or 'aion'
    match = re.match(r'^\s*characters\s*=\s*([^|]*)\|\s*(.+)$', prompt, flags=re.I | re.S)
    selected = []
    if match:
        raw, prompt = match.groups()
        tokens = list(dict.fromkeys(token.strip() for token in re.split(r'[,，]', raw) if token.strip()))
        if tokens == ['none']:
            if is_selfie:
                raise ValueError('自拍需要明确出场人物')
            tokens = []
        elif not tokens:
            raise ValueError('请明确生图的出场人物')
        for token in tokens:
            candidates = [a for a in anchors if a['id'] == token]
            if len(candidates) != 1:
                raise ValueError('生图人物锚点不存在，请重新选择人物')
            selected.append(candidates[0])
    elif re.match(r'^\s*characters\s*=', prompt, re.I):
        raise ValueError('生图人物格式不完整，请重试')
    elif is_selfie:
        selected = [a for a in anchors if anchor_identity(a, names) == speaker]
        if len(selected) > 1:
            raise ValueError('多个锚点对应同一个聊天身份，请在人物锚点中调整')
    refs = [{'name': a['name'], 'path': str(reference_path(a.get('image', '')))} for a in selected]
    if selected:
        appearances = '\n'.join(f"{a['name']}：{a.get('appearance') or '以参考照片为准'}" for a in selected)
        prompt = f'人物固定外貌：\n{appearances}\n参考照片仅约束各自外貌；只画以下场景要求的出场人物，服饰、动作和环境按场景决定。\n{prompt}'
    return prompt, refs
