"""Check persona roles at context and provider boundaries without live model calls."""
import ast
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import ai_providers
import chatroom
from test_relay_provider_errors import FakeStreamResponse, fake_client_factory


WORLD = {
    'ai_name': 'Sol', 'user_name': 'Mira',
    'ai_persona': 'AI persona', 'user_persona': 'User profile',
    'system_prompt': 'Creative rules',
}


class SystemMessageRolesTest(unittest.IsolatedAsyncioTestCase):
    def test_private_and_followup_prefixes_use_system_without_fake_replies(self):
        # Execute the actual prefix blocks, without starting route side effects.
        for filename in ('routes/chat.py', 'routes/chatroom.py'):
            tree = ast.parse((Path(__file__).parent / filename).read_text(encoding='utf-8'))
            blocks = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                      and isinstance(node.test, (ast.Call, ast.BoolOp))
                      and any(f'wb.get({key!r})' in ast.unparse(node.test)
                              for key in ('ai_persona', 'user_persona', 'system_prompt'))]
            self.assertTrue(blocks)
            for enabled in (True, False):
                for block in blocks:
                    messages = []
                    env = dict(wb={**WORLD, 'system_prompt_enabled': enabled},
                               ai_name='Sol', user_name='Mira', sender='aion',
                               prefix=messages, prefix_msgs=messages, messages=messages)
                    exec(compile(ast.Module(body=[block], type_ignores=[]), filename, 'exec'), env)
                    with self.subTest(filename=filename, line=block.lineno, enabled=enabled):
                        key = next(key for key in ('ai_persona', 'user_persona', 'system_prompt')
                                   if f'wb.get({key!r})' in ast.unparse(block.test))
                        if key == 'system_prompt' and not enabled:
                            self.assertEqual(messages, [])
                        else:
                            self.assertEqual(len(messages), 1)
                            self.assertEqual(messages[0]['role'], 'system')
                            self.assertIn(WORLD[key], messages[0]['content'])

    async def test_group_and_companion_private_personas_are_system_messages(self):
        with (
            patch.object(chatroom, 'load_worldbook', return_value=WORLD),
            patch.object(chatroom, 'get_chatroom_names', return_value=('Mira', 'Sol', 'Nova')),
            patch.object(chatroom, '_read_connor_persona', return_value='Companion persona'),
            patch.object(chatroom, 'build_ability_block', AsyncMock(return_value='')),
            patch.object(chatroom, 'append_intimacy_prompt'),
            patch.object(chatroom, 'with_current_device_context', side_effect=lambda messages, **kw: messages),
            patch.object(chatroom, 'fetch_merged_timeline', AsyncMock(return_value=[])),
            patch.object(chatroom, 'build_memory_blocks', AsyncMock(return_value={
                'time_block': 'time', 'memory_block': '', 'digest_result': {},
            })),
        ):
            for builder in (chatroom.build_aion_group_context, chatroom.build_connor_group_context,
                            chatroom.build_connor_1v1_context):
                messages, _ = await builder('room', [])
                persona = 'AI persona' if builder == chatroom.build_aion_group_context else 'Companion persona'
                expected = [persona, 'User profile']
                if builder == chatroom.build_aion_group_context:
                    expected.append('Creative rules')
                with self.subTest(builder=builder.__name__):
                    for index, text in enumerate(expected):
                        self.assertEqual(messages[index]['role'], 'system')
                        self.assertIn(text, messages[index]['content'])
                    self.assertFalse(any(m['content'].startswith(('收到，我会按照设定', '收到，我会记住',
                                                                  '收到，我会遵循')) for m in messages))

    async def test_gemini_separates_system_instructions_from_conversation(self):
        messages = [{'role': 'system', 'content': 'persona'}, {'role': 'system', 'content': 'rules'},
                    {'role': 'user', 'content': 'hello'}, {'role': 'assistant', 'content': 'hi'},
                    {'role': 'user', 'content': 'continue'}]
        factory = fake_client_factory(FakeStreamResponse())
        with patch('ai_providers.httpx.AsyncClient', new=factory), patch('ai_providers.get_key', return_value='test'):
            _ = [chunk async for chunk in ai_providers.call_gemini.__wrapped__(messages, 'test')]
        payload = factory.clients[0].calls[0][1]['json']
        self.assertEqual(payload.get('systemInstruction'), {'parts': [{'text': 'persona'}, {'text': 'rules'}]})
        self.assertEqual([m['role'] for m in payload['contents']], ['user', 'model', 'user'])
        self.assertEqual(ai_providers.build_multimodal_messages(messages), messages)

    def test_cli_keeps_system_prefix_and_legacy_ability_pairs_together(self):
        prompt = ai_providers._build_cli_prompt([
            {'role': 'system', 'content': 'persona'},
            {'role': 'system', 'content': 'rules'},
            {'role': 'user', 'content': 'abilities'},
            {'role': 'assistant', 'content': '好的，需要时我会使用这些指令。'},
            {'role': 'user', 'content': 'hello'},
        ])
        self.assertIn('[System Instruction]\npersona\n\nrules\n\nabilities', prompt)
        self.assertNotIn('好的，需要时', prompt)
        self.assertIn('[User]\nhello', prompt)

    async def test_companion_persona_is_added_even_with_other_system_messages(self):
        captured = []

        async def provider(messages, *args):
            captured.extend(messages)
            yield 'reply'

        with (patch.object(chatroom, '_read_connor_persona', return_value='Companion persona'),
              patch.object(chatroom, 'call_codex_cli', new=provider),
              patch.object(chatroom, 'with_current_device_context', side_effect=lambda messages: messages)):
            for initial in ([{'role': 'system', 'content': 'User profile'}],
                            [{'role': 'system', 'content': '[角色设定]\nCompanion persona'}]):
                captured.clear()
                _ = [chunk async for chunk in chatroom.stream_connor_cli(messages=initial)]
                self.assertEqual(sum('Companion persona' in m['content'] for m in captured), 1)


if __name__ == '__main__':
    unittest.main()
