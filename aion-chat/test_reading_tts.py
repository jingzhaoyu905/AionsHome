import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import reading_tts as reading


class ReadingTtsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = dict(chapter=dict(paragraphs=['第一段正文。', '第二段正文。'], char_count=12),
                         annotations={0: dict(text='第一位的批注。')},
                         connor_annotations={0: dict(text='第二位的批注。')},
                         ai_name='甲', connor_name='乙')
        self.config = dict(tts_aion_voice='edge:first', tts_connor_voice='edge:second')

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, narrator='aion'):
        return reading.build_plan(self.data, self.config, narrator, self.root)

    def test_paragraph_then_both_annotations_use_their_authors_voices(self):
        plan = self.plan('connor')
        self.assertEqual([(c['paragraph'], c['speaker'], c['kind']) for c in plan['clips']],
                         [(0, 'connor', 'body'), (0, 'aion', 'annotation'),
                          (0, 'connor', 'annotation'), (1, 'connor', 'body')])
        self.assertEqual([c['voice'] for c in plan['clips']],
                         ['edge:second', 'edge:first', 'edge:second', 'edge:second'])

    def test_long_finished_paragraphs_never_send_over_500_chars(self):
        for text in ['很长的章节正文。' * 1400, '正文【未闭合括号' + '很长的章节正文。' * 1400]:
            self.data['chapter']['paragraphs'] = [text]
            body = [c for c in self.plan()['clips'] if c['kind'] == 'body']
            self.assertLessEqual(max(c['chars'] for c in body), 500)
            self.assertGreater(len(body), 20)

    async def test_summaries_follow_segment_annotations_and_reuse_existing_audio(self):
        self.data['chapter']['segments_meta'] = [{'end_p': 0}, {'end_p': 1}]
        original = self.plan()
        async def synth(text, voice, path, **kwargs):
            path.write_bytes(b'audio')
        with patch.object(reading, 'synthesize_text_to_mp3', synth):
            await reading.synthesize_plan(original, self.root)
        self.data['summaries'] = [{'segment_index': 1, 'summary': '第二部分读后感。'},
                                  {'segment_index': 0, 'summary': '甲的读后感。'}]
        self.data['connor_summaries'] = [{'segment_index': 0, 'summary': '乙的读后感。'}]
        plan = self.plan()
        self.assertEqual([(c['paragraph'], c['kind'], c['speaker']) for c in plan['clips']],
                         [(0,'body','aion'), (0,'annotation','aion'), (0,'annotation','connor'),
                          (0,'summary','aion'), (0,'summary','connor'),
                          (1,'body','aion'), (1,'summary','aion')])
        summaries = [c for c in plan['clips'] if c['kind'] == 'summary']
        self.assertEqual([c['voice'] for c in summaries], ['edge:first','edge:second','edge:first'])
        self.assertEqual(plan['summary_chars'], sum(len(c['text']) for c in summaries))
        self.assertEqual(plan['missing_chars'], plan['summary_chars'])
        self.assertEqual(plan['missing_count'], 3)
        self.assertTrue(all((self.root / (c['key'] + '.mp3')).is_file() for c in original['clips']))

    def test_book_named_storage_copies_old_audio_without_undoing_manual_deletion(self):
        root = self.root / 'reading_audio'
        target = reading.book_audio_dir(root, 'book-one', '一本书：风/月?')
        target.mkdir(parents=True)
        self.assertTrue(target.name.startswith('一本书：风_月_--'))
        self.assertEqual(reading.book_audio_dir(root, 'book-one', '改名了'), target)
        self.assertNotEqual(reading.book_audio_dir(root, 'book-two', '一本书：风/月?'), target)
        legacy = self.root / 'books' / 'book-one' / 'tts'
        legacy.mkdir(parents=True)
        filename = 'a' * 64 + '.mp3'
        (legacy / filename).write_bytes(b'old audio')
        reading.preserve_legacy_audio(legacy, target)
        self.assertEqual((target / filename).read_bytes(), b'old audio')
        self.assertTrue((legacy / filename).is_file())
        (target / filename).unlink()  # Simulate the user's own manual removal.
        reading.preserve_legacy_audio(legacy, target)
        self.assertFalse((target / filename).exists())

    async def test_deleting_library_book_preserves_recordings_outside_book_directory(self):
        import aiosqlite
        import book as book_files
        from routes import book
        db_path = self.root / 'test.db'
        async with aiosqlite.connect(db_path) as db:
            await db.execute('CREATE TABLE books (book_id TEXT, title TEXT)')
            await db.execute("INSERT INTO books VALUES ('book-one','测试书名')")
            await db.commit()
        books = self.root / 'books'
        legacy = books / 'book-one' / 'tts'
        legacy.mkdir(parents=True)
        filename = 'b' * 64 + '.mp3'
        (legacy / filename).write_bytes(b'keep forever')
        audio = self.root / 'reading_audio'
        with patch.object(book, 'BOOKS_DIR', books), patch.object(book_files, 'BOOKS_DIR', books), \
             patch.object(book, 'READING_AUDIO_DIR', audio), \
             patch.object(book, 'get_db', lambda: aiosqlite.connect(db_path)), patch.object(book, '_speech_jobs', {}):
            await book.delete_book('book-one')
        self.assertFalse((books / 'book-one').exists())
        self.assertEqual((reading.book_audio_dir(audio, 'book-one') / filename).read_bytes(), b'keep forever')

    async def test_cache_survives_rebuild_and_only_changed_voice_or_text_is_missing(self):
        calls = []
        async def synth(text, voice, path, **kwargs):
            calls.append((text, voice))
            path.write_bytes(b'audio')
        plan = self.plan()
        with patch.object(reading, 'synthesize_text_to_mp3', synth):
            await reading.synthesize_plan(plan, self.root)
            await reading.synthesize_plan(self.plan(), self.root)
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.plan()['missing_chars'], 0)
        other = self.plan('connor')
        self.assertEqual(other['missing_count'], 2)
        self.data['annotations'][0]['text'] = '修改后的批注。'
        changed = self.plan()
        self.assertEqual(changed['missing_count'], 1)
        self.assertNotEqual(changed['signature'], plan['signature'])

    async def test_failure_keeps_completed_clips_and_retry_only_fills_missing(self):
        calls = []
        async def synth(text, voice, path, **kwargs):
            calls.append(text)
            if len(calls) == 2:
                path.write_bytes(b'partial')
                raise RuntimeError('provider unavailable')
            path.write_bytes(b'audio')
        with patch.object(reading, 'synthesize_text_to_mp3', synth):
            with self.assertRaises(RuntimeError):
                await reading.synthesize_plan(self.plan(), self.root)
            self.assertEqual(self.plan()['missing_count'], 3)
            await reading.synthesize_plan(self.plan(), self.root)
        self.assertEqual(len(calls), 5)
        self.assertEqual(self.plan()['missing_count'], 0)
        self.assertFalse(list(self.root.glob('*.part.mp3')))

    def test_missing_author_voice_blocks_synthesis_and_empty_text_is_skipped(self):
        self.config['tts_connor_voice'] = ''
        self.assertEqual(self.plan()['missing_voices'], ['乙'])
        self.data['connor_annotations'] = {}
        self.data['chapter']['paragraphs'].append('  ')
        self.assertEqual(self.plan()['missing_voices'], [])
        self.assertEqual(len(self.plan()['clips']), 3)

    async def test_api_preview_confirmation_background_job_and_cached_audio(self):
        import httpx
        from fastapi import FastAPI
        from routes import book
        app = FastAPI()
        app.include_router(book.router)
        calls = []
        ready_to_synthesize = asyncio.Event()
        async def synth(text, voice, path, **kwargs):
            await ready_to_synthesize.wait()
            calls.append(text)
            path.write_bytes(b'audio')
        with patch.object(book, 'BOOKS_DIR', self.root), \
             patch.object(book, 'READING_AUDIO_DIR', self.root / 'reading_audio'), \
             patch.object(book, 'get_chapter', AsyncMock(return_value=self.data)), \
             patch.object(book, 'load_chatroom_config', return_value=self.config), \
             patch.object(book, '_speech_jobs', {}), \
             patch.object(reading, 'synthesize_text_to_mp3', synth):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                url = '/api/books/book-one/chapters/0/speech'
                plan = (await client.get(url)).json()
                self.assertEqual(calls, [], 'Opening settings must never synthesize')
                self.assertEqual(plan['missing_count'], 4)
                self.assertNotIn('voice', plan['clips'][0])
                self.assertEqual((await client.post(url, json={'narrator':'aion', 'signature':'old'})).status_code, 409)
                self.assertEqual(calls, [])
                response = await client.post(url, json={'narrator':'aion', 'signature':plan['signature']})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['job']['state'], 'running')
                task = book._speech_jobs[('book-one', 0, 'aion')]['task']
                await client.post(url, json={'narrator':'aion', 'signature':plan['signature']})
                self.assertIs(book._speech_jobs[('book-one', 0, 'aion')]['task'], task)
                # Annotation regeneration must not hide the approved running job.
                self.data['annotations'][0]['text'] = '合成期间重新生成的批注。'
                active = (await client.get(url)).json()
                self.assertEqual(active['signature'], plan['signature'])
                self.assertEqual(active['job']['state'], 'running')
                ready_to_synthesize.set()
                await task
                ready = (await client.get(url + '?signature=' + plan['signature'])).json()
                self.assertEqual(ready['missing_count'], 0)
                self.assertEqual((await client.get(ready['clips'][0]['url'])).content, b'audio')
                latest = (await client.get(url)).json()
                self.assertEqual(latest['missing_count'], 1)
                self.assertEqual((await client.post(url, json={'narrator':'aion', 'signature':plan['signature']})).status_code,409)
                self.assertEqual(len(calls), 4, 'Cached playback must not call TTS again')
                self.assertEqual((await client.get(url + '?narrator=unknown')).status_code, 400)


if __name__ == '__main__':
    unittest.main()
