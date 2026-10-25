import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent

from config import normalize_custom_model_routes
from ai_providers import (
    build_gemini_contents,
    build_multimodal_messages,
    filter_audio_attachments_for_model,
)
from routes.chat import _process_voice_attachments_in_history
from routes.chatroom import _process_voice_attachments


def voice_attachment(url="/uploads/voice.wav", transcript="暗号内容"):
    return {
        "type": "voice",
        "url": url,
        "duration": 2.5,
        "transcript": transcript,
    }


class ModelAudioCapabilityConfigTests(unittest.TestCase):
    def test_missing_audio_capability_defaults_to_false(self):
        routes = normalize_custom_model_routes([
            {
                "id": "route-1",
                "name": "test",
                "base_url": "https://relay.example/v1",
                "models": [{"key": "legacy", "model": "legacy-model", "vision": True}],
            }
        ])

        self.assertFalse(routes[0]["models"][0]["audio"])

    def test_explicit_audio_capability_survives_normalization(self):
        routes = normalize_custom_model_routes([
            {
                "id": "route-1",
                "name": "test",
                "base_url": "https://relay.example/v1",
                "models": [{"key": "listener", "model": "gemini-model", "audio": True}],
            }
        ])

        self.assertTrue(routes[0]["models"][0]["audio"])

    def test_non_boolean_audio_value_cannot_enable_original_audio(self):
        routes = normalize_custom_model_routes([
            {
                "id": "route-1",
                "name": "test",
                "base_url": "https://relay.example/v1",
                "models": [{"key": "unsafe", "model": "unit-model", "audio": "false"}],
            }
        ])

        self.assertFalse(routes[0]["models"][0]["audio"])

    def test_settings_ui_has_unchecked_audio_toggle_and_persists_field(self):
        source = (ROOT / "static" / "settings.html").read_text(encoding="utf-8")

        self.assertIn("audio: false", source)
        self.assertIn("model.audio === true ? \"checked\" : \"\"", source)
        self.assertIn("audio: model.audio === true", source)


class ModelVideoCapabilityConfigTests(unittest.TestCase):
    def test_video_capability_defaults_off_and_preserves_explicit_enablement(self):
        routes = normalize_custom_model_routes([{
            "id": "route-1",
            "name": "test",
            "base_url": "https://relay.example/v1",
            "models": [
                {"key": "legacy", "model": "legacy-model"},
                {"key": "viewer", "model": "video-model", "video": True},
            ],
        }])

        self.assertEqual(
            [model.get("video") for model in routes[0]["models"]],
            [False, True],
        )


class ModelReasoningEffortConfigTests(unittest.TestCase):
    def test_reasoning_effort_defaults_to_api_and_migrates_medium_to_high(self):
        routes = normalize_custom_model_routes([{
            "id": "route-1",
            "name": "test",
            "base_url": "https://relay.example/v1",
            "models": [
                {"key": "legacy", "model": "legacy-model"},
                {
                    "key": "balanced",
                    "model": "balanced-model",
                    "use_default_reasoning_effort": False,
                    "reasoning_effort": "medium",
                },
            ],
        }])

        models = routes[0]["models"]
        self.assertEqual(
            [(model["use_default_reasoning_effort"], model["reasoning_effort"]) for model in models],
            [(True, "high"), (False, "high")],
        )


class LatestUserVoiceRetentionTests(unittest.TestCase):
    def test_both_group_speakers_receive_audio_before_and_after_other_ai_reply(self):
        from context_builder import render_merged_timeline

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'voice.wav'
            source.write_bytes(b'original-group-voice')
            user = {'source': 'group', 'sender': 'user', 'content': '暗号内容', 'created_at': 100,
                    'attachments': [voice_attachment()]}
            for who, other in [('aion', 'connor'), ('connor', 'aion')]:
                for replies in ([], [{'source': 'group', 'sender': other, 'content': '第一位已经回复',
                                     'created_at': 101, 'attachments': []}]):
                    with self.subTest(who=who, second=bool(replies)):
                        history = render_merged_timeline([user, *replies], who)
                        _process_voice_attachments(history)
                        with patch('ai_providers._resolve_attachment_path', return_value=source):
                            contents = build_gemini_contents(history, include_audio=True)
                        audio = [part['inline_data'] for message in contents for part in message['parts']
                                 if 'inline_data' in part]
                        self.assertEqual(len(audio), 1)
                        self.assertEqual(base64.b64decode(audio[0]['data']), b'original-group-voice')
                        self.assertTrue(any('暗号内容' in message['content'] for message in history))
            self.assertEqual(user['attachments'], [voice_attachment()])

    def test_next_real_user_message_expires_group_voice_after_both_ai_replies(self):
        from context_builder import render_merged_timeline

        messages = [
            {'source': 'group', 'sender': 'user', 'content': '暗号内容', 'created_at': 100,
             'attachments': [voice_attachment()]},
            {'source': 'group', 'sender': 'aion', 'content': '第一位回复', 'created_at': 101},
            {'source': 'group', 'sender': 'connor', 'content': '第二位回复', 'created_at': 102},
            {'source': 'group', 'sender': 'user', 'content': '下一轮', 'created_at': 103},
        ]
        for who in ('aion', 'connor'):
            with self.subTest(who=who):
                history = render_merged_timeline(messages, who)
                _process_voice_attachments(history)
                self.assertFalse(any(message.get('attachments') for message in history))
                self.assertTrue(any('暗号内容' in message['content'] for message in history))

    def test_selected_image_survives_later_ai_transcript_entry(self):
        for processor in (
            _process_voice_attachments_in_history,
            _process_voice_attachments,
        ):
            with self.subTest(processor=processor.__name__):
                history = [
                    {
                        "role": "user",
                        "content": "当前用户消息 - 用户：看照片",
                        "attachments": ["/uploads/photo.png"],
                    },
                    {
                        "role": "user",
                        "content": "历史消息 - AI：我看到了",
                    },
                ]

                processor(history)

                self.assertEqual(
                    history[0].get("attachments"),
                    ["/uploads/photo.png"],
                )

    def test_private_history_keeps_voice_on_latest_user_even_with_assistant_after_it(self):
        history = [
            {"role": "user", "content": "", "attachments": [voice_attachment()]},
            {"role": "assistant", "content": "回答", "attachments": []},
        ]

        _process_voice_attachments_in_history(history)

        self.assertEqual(history[0]["attachments"], [voice_attachment()])
        self.assertIn("[语音消息] 暗号内容", history[0]["content"])

    def test_group_history_does_not_resend_old_voice_after_newer_user_text(self):
        history = [
            {"role": "user", "content": "", "attachments": [voice_attachment()]},
            {"role": "assistant", "content": "回答"},
            {"role": "user", "content": "新的文字", "attachments": []},
        ]

        _process_voice_attachments(history)

        self.assertNotIn("attachments", history[0])
        self.assertIn("[语音消息] 暗号内容", history[0]["content"])
        self.assertEqual(history[2]["content"], "新的文字")

    def test_group_history_keeps_voice_when_latest_user_message_is_voice(self):
        history = [
            {"role": "user", "content": "旧消息", "attachments": []},
            {"role": "user", "content": "", "attachments": [voice_attachment()]},
            {"role": "assistant", "content": "第一位 AI 的回答"},
        ]

        _process_voice_attachments(history)

        self.assertEqual(history[1]["attachments"], [voice_attachment()])

    def test_ambient_context_is_appended_before_final_voice_pruning(self):
        source = (ROOT / "routes" / "chatroom.py").read_text(encoding="utf-8")
        aion_block = source[source.index("async def _reply_aion"):source.index("async def _reply_connor")]
        connor_block = source[source.index("async def _reply_connor"):source.index("@router.post(\"/rooms/{room_id}/ai-chat\")")]

        for block in (aion_block, connor_block):
            self.assertLess(block.index("if ambient_context:"), block.index("_process_voice_attachments("))


class ProviderAudioSerializationTests(unittest.TestCase):
    def _history(self):
        return [{
            "role": "user",
            "content": "[语音消息] 暗号内容",
            "attachments": ["/uploads/voice.wav"],
        }]

    def test_openai_audio_enabled_emits_real_input_audio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "voice.wav"
            audio_bytes = b"RIFF-unit-audio"
            audio_path.write_bytes(audio_bytes)

            with patch("ai_providers._resolve_attachment_path", return_value=audio_path):
                messages = build_multimodal_messages(self._history(), include_audio=True)

        parts = messages[0]["content"]
        audio_part = next(part for part in parts if part["type"] == "input_audio")
        self.assertEqual(audio_part["input_audio"]["format"], "wav")
        self.assertEqual(base64.b64decode(audio_part["input_audio"]["data"]), audio_bytes)

    def test_openai_audio_disabled_sends_transcript_only_without_placeholder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "voice.wav"
            audio_path.write_bytes(b"RIFF-unit-audio")

            with patch("ai_providers._resolve_attachment_path", return_value=audio_path):
                messages = build_multimodal_messages(self._history(), include_audio=False)

        self.assertEqual(messages, [{"role": "user", "content": "[语音消息] 暗号内容"}])

    def test_gemini_audio_is_inline_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "voice.wav"
            audio_path.write_bytes(b"RIFF-unit-audio")

            with patch("ai_providers._resolve_attachment_path", return_value=audio_path):
                disabled = build_gemini_contents(self._history(), include_audio=False)
                enabled = build_gemini_contents(self._history(), include_audio=True)

        self.assertFalse(any("inline_data" in part for part in disabled[0]["parts"]))
        self.assertTrue(any("inline_data" in part for part in enabled[0]["parts"]))

    def test_disabled_model_removes_audio_before_any_provider_dispatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "voice.wav"
            image_path = Path(tmpdir) / "photo.png"
            audio_path.write_bytes(b"RIFF-unit-audio")
            image_path.write_bytes(b"unit-image")

            def resolve(att):
                return audio_path if "voice" in str(att) else image_path

            history = [{
                "role": "user",
                "content": "transcript",
                "attachments": ["/uploads/voice.wav", "/uploads/photo.png"],
            }]
            with patch("ai_providers._resolve_attachment_path", side_effect=resolve):
                filtered = filter_audio_attachments_for_model(history, include_audio=False)

        self.assertEqual(filtered[0]["attachments"], ["/uploads/photo.png"])
        self.assertEqual(history[0]["attachments"], ["/uploads/voice.wav", "/uploads/photo.png"])


class RelayAudioRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_converter_reports_error_instead_of_silently_dropping_voice(self):
        import ai_providers
        from test_relay_provider_errors import FakeStreamResponse, fake_client_factory

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'voice.webm'
            source.write_bytes(b'browser-recording')
            factory = fake_client_factory(FakeStreamResponse())
            with patch('ai_providers._resolve_attachment_path', return_value=source), patch(
                'ai_providers.shutil.which', return_value=None
            ), patch('ai_providers.httpx.AsyncClient', new=factory):
                chunks = [chunk async for chunk in ai_providers.call_custom_openai(
                    [{'role': 'user', 'content': 'transcript', 'attachments': [voice_attachment()]}],
                    {'model': 'test', 'base_url': 'https://relay.example/v1', 'audio': True})]
            self.assertIn('FFmpeg', ''.join(chunks))
            self.assertIn('附件处理失败', ''.join(chunks))
            self.assertEqual(factory.clients, [])

    async def test_saved_audio_toggle_controls_actual_relay_payload(self):
        import ai_providers
        import config
        from test_relay_provider_errors import FakeStreamResponse, fake_client_factory

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'voice.wav'
            source.write_bytes(b'RIFF-original-voice')
            for enabled in (True, False):
                routes = normalize_custom_model_routes([{
                    'id': 'audio-test', 'base_url': 'https://relay.example/v1',
                    'models': [{'key': 'test-relay-audio', 'model': 'gemini-test', 'audio': enabled}],
                }])
                factory = fake_client_factory(FakeStreamResponse())
                history = [{'role': 'user', 'content': 'transcript', 'attachments': [voice_attachment()]}]
                with self.subTest(enabled=enabled), patch.dict(config.SETTINGS, {'custom_model_routes': routes}), patch.dict(
                    config.MODELS
                ), patch.object(ai_providers, 'UPLOADS_DIR', Path(directory)), patch(
                    'ai_providers.httpx.AsyncClient', new=factory
                ):
                    config.refresh_custom_models()
                    _ = [chunk async for chunk in ai_providers.stream_ai(
                        history, 'test-relay-audio', include_device_context=False)]
                content = factory.clients[0].calls[0][1]['json']['messages'][0]['content']
                if enabled:
                    audio = next(p['input_audio'] for p in content if p['type'] == 'input_audio')
                    self.assertEqual(base64.b64decode(audio['data']), b'RIFF-original-voice')
                    self.assertEqual(audio['format'], 'wav')
                else:
                    self.assertEqual(content, 'transcript')

    def test_browser_recordings_are_converted_to_wav_only_when_enabled(self):
        import io
        import shutil
        import subprocess
        import wave
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('FFmpeg not installed')
        with tempfile.TemporaryDirectory() as directory:
            wav = Path(directory) / 'source.wav'
            with wave.open(str(wav), 'wb') as output:
                output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                output.writeframes(b'\x00\x00' * 1600)
            for extension, codec in [('webm', 'libopus'), ('mp4', 'aac')]:
                source = Path(directory) / ('voice.' + extension)
                subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(wav),
                                '-c:a', codec, str(source)], check=True, capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                history = [{'role': 'user', 'content': 'transcript', 'attachments': [voice_attachment()]}]
                with self.subTest(extension=extension), patch('ai_providers._resolve_attachment_path', return_value=source):
                    enabled = build_multimodal_messages(history, include_audio=True)
                    disabled = build_multimodal_messages(history, include_audio=False, include_video=True)
                    self.assertEqual(disabled[0]['content'], 'transcript')
                    content = enabled[0]['content']
                    self.assertIsInstance(content, list)
                    audio = next(p['input_audio'] for p in content if p['type'] == 'input_audio')
                    self.assertEqual(audio['format'], 'wav')
                    with wave.open(io.BytesIO(base64.b64decode(audio['data'])), 'rb') as decoded:
                        self.assertGreater(len(decoded.readframes(1600)), 0)


class OfficialGeminiAudioRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_models_send_original_voice_bytes_in_api_request(self):
        import ai_providers
        from test_relay_provider_errors import FakeStreamResponse, fake_client_factory

        with tempfile.TemporaryDirectory() as directory:
            for model_key, filename, mime in [
                ('官Gem3.8flash', 'voice.webm', 'audio/webm'),
                ('官4-Argon', 'voice.wav', 'audio/wav'),
            ]:
                with self.subTest(model=model_key):
                    source = Path(directory) / filename
                    source.write_bytes(b'original-recording-bytes')
                    history = [{'role': 'user', 'content': '', 'attachments': [
                        voice_attachment('/uploads/' + filename)]}]
                    _process_voice_attachments_in_history(history)
                    factory = fake_client_factory(FakeStreamResponse())
                    with patch.object(ai_providers, 'UPLOADS_DIR', Path(directory)), patch(
                        'ai_providers.httpx.AsyncClient', new=factory
                    ), patch('ai_providers.get_key', return_value='test'):
                        _ = [chunk async for chunk in ai_providers.stream_ai(
                            history, model_key, include_device_context=False)]
                    payload = factory.clients[0].calls[0][1]['json']
                    parts = payload['contents'][0]['parts']
                    audio = [part['inline_data'] for part in parts if 'inline_data' in part]
                    self.assertEqual(len(audio), 1)
                    self.assertEqual(audio[0]['mime_type'], mime)
                    self.assertEqual(base64.b64decode(audio[0]['data']), b'original-recording-bytes')
                    self.assertIn('暗号内容', parts[0]['text'])

    def test_voice_containers_use_audio_mime_and_respect_disabled_audio(self):
        for filename, mime in [('voice.webm', 'audio/webm'), ('voice.mp4', 'audio/mp4')]:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / filename
                source.write_bytes(b'original-audio')
                history = [{'role': 'user', 'content': 'transcript', 'attachments': [voice_attachment()]}]
                with patch('ai_providers._resolve_attachment_path', return_value=source):
                    enabled = build_gemini_contents(history, include_audio=True)
                    disabled = build_gemini_contents(history, include_audio=False)
                self.assertEqual(enabled[0]['parts'][1]['inline_data']['mime_type'], mime)
                self.assertEqual(disabled[0]['parts'], [{'text': 'transcript'}])


if __name__ == "__main__":
    unittest.main()
