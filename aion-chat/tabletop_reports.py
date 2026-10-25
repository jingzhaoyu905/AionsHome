"""One public summary per closed table; hidden cards and dice stay at the table."""
import tabletop


async def last_active_group():
    from database import get_db
    async with get_db() as db:
        cur = await db.execute("""
            SELECT r.id FROM chatroom_rooms r JOIN chatroom_messages m ON m.room_id=r.id
            WHERE r.type='group' AND m.sender='user' AND r.id NOT GLOB 'billiards_*'
            ORDER BY m.created_at DESC LIMIT 1
        """)
        row = await cur.fetchone()
        if not row:
            cur = await db.execute("SELECT id FROM chatroom_rooms WHERE type='group' AND id NOT GLOB 'billiards_*' ORDER BY updated_at DESC LIMIT 1")
            row = await cur.fetchone()
        return {'type': 'chatroom', 'id': row[0]} if row else None


def result_card(state, names):
    wallet = state.get('wallet')
    title = tabletop.KINDS[state['kind']]
    dice = state['kind'] == 'liars-dice'
    rounds = len(state.get('rounds', [])) or state['round']
    unit = '轮' if dice else '手'
    outcome = f"{state.get('end_reason') or '已收桌'} · 共完成 {rounds} {unit}"
    lines = []
    for p in state['players']:
        if wallet:
            net = wallet['net'][p]
            returned = wallet['returned'].get(p, 0)
            lines.append(f'{names[p]}：本场 {net / 100:+.2f} 元 · 收桌余额 {returned / 100:.2f} 元')
        elif dice and 'dice_net' not in state:
            lines.append(f'{names[p]}：剩余 {state["counts"][p]} 颗骰子' + (' · 本场赢家' if state.get('winner') == p else ''))
        elif dice:
            lines.append(f'{names[p]}：本场 {state.get("dice_net", {}).get(p, 0):+d} 分')
        elif 'starting_stacks' not in state:
            lines.append(f'{names[p]}：剩余 {state["stacks"][p]} 筹码（旧局未记录起始筹码）')
        else:
            net = state['stacks'][p] - state['starting_stacks'][p]
            lines.append(f'{names[p]}：本场 {net:+d} 筹码 · 剩余 {state["stacks"][p]}')
    note = '免费练习 · 不变动钱包'
    if wallet:
        note = ('约定已取消，伴侣带入金额已全额退回。' if wallet['status'] == 'refunded'
                else '结算金额已从钱包自动扣除')
    card = dict(type='table_game_result', scope='table', title=title, round=state['round'],
                rounds_completed=rounds, outcome=outcome, lines=lines, note=note)
    content = f'【{title} · 本场收桌结算】\n{outcome}\n' + '\n'.join(lines) + '\n' + note
    return content, [card, {'type': 'system_model_context'}]
