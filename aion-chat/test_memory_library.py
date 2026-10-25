"""Small integration checks for the shared memory manager (temporary database only)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import memory_library as library
import memory_compression


class MemoryLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'library.db'
        self.connect = lambda: aiosqlite.connect(self.path)
        self.patches = [patch.object(m, 'get_db', self.connect) for m in (library, memory_compression)]
        self.patches += [patch.object(library, 'get_embedding', AsyncMock(return_value=None)),
                         patch.object(library, '_names', return_value=('用户','主AI','第二AI')),
                         patch.object(library, '_broadcast', AsyncMock())]
        for p in self.patches:
            p.start()
        async with self.connect() as db:
            for table in ('memories', 'chatroom_memories'):
                await db.execute(f'''CREATE TABLE {table} (
                    id TEXT PRIMARY KEY, content TEXT, type TEXT DEFAULT 'daily', scope TEXT DEFAULT 'connor',
                    room_id TEXT, source_conv TEXT, created_at REAL DEFAULT 100, source_start_ts REAL,
                    source_end_ts REAL, source_msg_id TEXT, keywords TEXT DEFAULT '', importance REAL DEFAULT .5,
                    embedding BLOB, unresolved INTEGER DEFAULT 0, memory_kind TEXT DEFAULT 'daily',
                    evidence_summary TEXT DEFAULT '', evidence_detail_level TEXT DEFAULT 'summary',
                    archive_state TEXT DEFAULT 'active', compression_stage INTEGER DEFAULT 0,
                    period_kind TEXT DEFAULT '', compression_batch_id TEXT DEFAULT '', source_memory_ids TEXT)''')
                await db.executemany(f'INSERT INTO {table} (id,content,archive_state,source_msg_id) VALUES (?,?,?,?)',
                                     [('one','花市','cold','["private:m"]'), ('two','早餐','active',None)])
                await db.execute(f"INSERT INTO {table} (id,content,compression_stage,compression_batch_id,source_memory_ids) VALUES ('summary','花市摘要',1,'batch','[\"one\"]')")
                await db.execute(f"INSERT INTO {table} (id,content,period_kind,compression_batch_id,source_memory_ids) VALUES ('fact','提炼事实','fact','batch','[\"one\"]')")
            await db.execute("INSERT INTO chatroom_memories (id,content,scope) VALUES ('other','旧群聊库','group')")
            await db.execute('CREATE TABLE memory_compression_batch_inputs (batch_id TEXT, store TEXT, memory_id TEXT)')
            await db.execute('CREATE TABLE messages (id TEXT, conv_id TEXT, role TEXT, content TEXT, created_at REAL)')
            await db.execute('CREATE TABLE chatroom_rooms (id TEXT, type TEXT)')
            await db.execute('CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT, content TEXT, created_at REAL)')
            await db.execute("INSERT INTO messages VALUES ('m','window','system','拍一拍',100)")
            await db.execute("INSERT INTO chatroom_rooms VALUES ('private','connor_1v1')")
            await db.execute("INSERT INTO chatroom_messages VALUES ('c','private','system','点歌',100)")
            await db.commit()
        app = FastAPI()
        app.include_router(library.router)
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.client.close()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    async def test_archive_filter_and_equal_time_paging_keep_stores_separate(self):
        for store in ('main','chatroom'):
            url = f'/api/memory-library/{store}?view=atoms&limit=1'
            first = self.client.get(url).json()
            second = self.client.get(url + '&page=2').json()
            self.assertEqual(first['total'], 2)
            self.assertEqual({first['items'][0]['id'], second['items'][0]['id']}, {'one','two'})
            cold = self.client.get(url + '&state=cold&q=花市').json()
            self.assertEqual([m['id'] for m in cold['items']], ['one'])
        self.assertEqual(self.client.get('/api/memory-library/chatroom/other').status_code, 404)

    async def test_large_library_returns_only_requested_page_without_vectors(self):
        async with self.connect() as db:
            await db.executemany('INSERT INTO memories (id,content,embedding) VALUES (?,?,?)',
                                 [(f'bulk-{i:04}', '分页测试', b'large-vector') for i in range(2138)])
            await db.commit()
        url = '/api/memory-library/main?view=atoms&limit=20'
        first = self.client.get(url).json()
        last = self.client.get(url + '&page=107').json()
        self.assertEqual((last['total'], last['page'], len(last['items'])), (2140, 107, 20))
        self.assertTrue({m['id'] for m in first['items']}.isdisjoint(m['id'] for m in last['items']))
        self.assertTrue(all('embedding' not in m for m in first['items'] + last['items']))
        self.assertEqual(self.client.get(url + '&page=999').json()['page'], 107)

    async def test_edit_preserves_sources_and_delete_requires_acknowledging_relations(self):
        async with self.connect() as db:
            await db.execute("UPDATE memories SET embedding=? WHERE id='one'", (b'old-vector',))
            await db.commit()
        response = self.client.put('/api/memory-library/main/one', json={'content':'新的花市记忆','memory_kind':'long_term','keywords':'花市'})
        self.assertEqual(response.status_code, 200)
        async with self.connect() as db:
            row = await (await db.execute("SELECT source_msg_id, archive_state, embedding FROM memories WHERE id='one'")).fetchone()
        self.assertEqual(row, ('["private:m"]','cold',None))
        self.assertEqual(self.client.delete('/api/memory-library/main/one').status_code, 409)
        self.assertEqual(self.client.delete('/api/memory-library/main/one?confirm_related=true').status_code, 200)
        self.assertEqual(self.client.get('/api/memory-library/chatroom/one').status_code, 200)
        async with self.connect() as db:
            self.assertEqual((await (await db.execute("SELECT content,source_memory_ids FROM memories WHERE id='summary'")).fetchone()), ('花市摘要','[]'))
            self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM messages')).fetchone())[0], 1)

    async def test_sources_include_system_events_and_reject_other_store_private_chat(self):
        result = self.client.get('/api/memory-library/main/one/sources').json()
        self.assertEqual([m['content'] for m in result['messages']], ['拍一拍'])
        self.assertEqual(self.client.get('/api/memory-library/chatroom/one/sources').json()['messages'], [])
        async with self.connect() as db:
            await db.execute("UPDATE memories SET source_msg_id='[\"chatroom:c\"]' WHERE id='two'")
            await db.execute("UPDATE chatroom_memories SET source_msg_id='[\"chatroom:c\"]' WHERE id='two'")
            await db.commit()
        self.assertEqual(self.client.get('/api/memory-library/main/two/sources').json()['messages'], [])
        self.assertEqual(self.client.get('/api/memory-library/chatroom/two/sources').json()['messages'][0]['content'], '点歌')

    async def test_source_paging_and_explicit_empty_selection(self):
        async with self.connect() as db:
            await db.execute("UPDATE memories SET source_msg_id='private:m' WHERE id='one'")
            await db.execute("UPDATE memories SET source_start_ts=100,source_end_ts=100 WHERE id='two'")
            await db.executemany('INSERT INTO messages VALUES (?,?,?,?,?)', [(f'page-{i:02}','window','user','来源',100) for i in range(52)])
            await db.commit()
        self.assertEqual(self.client.get('/api/memory-library/main/one/sources').json()['total'], 1)
        first = self.client.get('/api/memory-library/main/two/sources').json()
        second = self.client.get('/api/memory-library/main/two/sources?page=2').json()
        self.assertEqual((len(first['messages']), len(second['messages']), first['total']), (50,3,53))
        self.assertFalse(first['exact'])
        self.assertEqual(len({m['id'] for m in first['messages'] + second['messages']}), 53)
        self.assertEqual(self.client.put('/api/memory-library/main/two/sources', json={'source_message_ids':[]}).status_code, 200)
        self.assertEqual(self.client.get('/api/memory-library/main/two/sources').json()['total'], 0)

    async def test_create_and_input_validation(self):
        for store in ('main','chatroom'):
            response = self.client.post(f'/api/memory-library/{store}', json={'content':'新记忆','memory_kind':'daily','date':'2026-10-03','keywords':'生活'})
            self.assertEqual(response.status_code, 200)
            item = self.client.get(f'/api/memory-library/{store}/' + response.json()['id']).json()['item']
            self.assertEqual(item['memory_kind'], 'daily')
            self.assertEqual(item['keywords'], '生活')
        self.assertEqual(self.client.post('/api/memory-library/main', json={'content':'  '}).status_code, 422)
        self.assertEqual(self.client.get('/api/memory-library/main?start=2026-10-04&end=2026-10-03').status_code, 400)

    async def test_friend_category_is_separate_and_can_be_reclassified(self):
        for store in ('main','chatroom'):
            url = f'/api/memory-library/{store}'
            created = self.client.post(url, json={'content':'和朋友看兔子的约定','memory_kind':'friend'})
            self.assertEqual(created.status_code, 200)
            mem_id = created.json()['id']
            friends = self.client.get(url+'?kind=friend').json()['items']
            self.assertEqual([m['id'] for m in friends], [mem_id])
            self.assertEqual(friends[0]['memory_kind'], 'friend')
            self.assertNotIn(mem_id, [m['id'] for m in self.client.get(url+'?kind=long_term').json()['items']])
            self.assertEqual(self.client.put(url+'/'+mem_id, json={'content':'约定保留','memory_kind':'long_term'}).status_code, 200)
            self.assertEqual(self.client.get(url+'?kind=friend').json()['total'], 0)

    async def test_unresolved_view_and_category_find_old_records_in_both_stores(self):
        async with self.connect() as db:
            for table in ('memories', 'chatroom_memories'):
                await db.execute(f"UPDATE {table} SET unresolved=1 WHERE id IN ('one','summary')")
            await db.execute("UPDATE chatroom_memories SET unresolved=1 WHERE id='other'")
            await db.commit()
        for store in ('main', 'chatroom'):
            url = f'/api/memory-library/{store}'
            for query in ('view=unresolved', 'kind=unresolved'):
                response = self.client.get(url + '?' + query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual({m['id'] for m in response.json()['items']}, {'one', 'summary'})
                page = self.client.get(url + '?' + query + '&q=花市&limit=1&page=2').json()
                self.assertEqual((page['total'], page['page'], len(page['items'])), (2, 2, 1))
            self.assertEqual(self.client.get(url + '?view=unresolved&kind=friend').json()['total'], 0)
            response = self.client.put(url + '/one', json={'content':'花市','memory_kind':'daily','unresolved':False})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([m['id'] for m in self.client.get(url + '?view=unresolved').json()['items']], ['summary'])
            item = self.client.get(url + '/one').json()['item']
            self.assertEqual((item['archive_state'], item['source_msg_id']), ('cold', '["private:m"]'))
