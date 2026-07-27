# -*- coding: utf-8 -*-
"""File cache test case for Read/Write/Edit tools."""
import os
import tempfile
import time
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.state import AgentState
from agentscope.tool import Edit, Read, Write


class FileCacheTest(IsolatedAsyncioTestCase):
    """Test file version cache behavior for Read/Write/Edit tools."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.read_tool = Read()
        self.write_tool = Write()
        self.edit_tool = Edit()
        self.state = AgentState()

        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "test.txt")

    async def asyncTearDown(self) -> None:
        """Clean up temporary files."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    async def test_edit_without_known_version(self) -> None:
        """Edit fails when the current file version is not known."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Hello World\n")

        chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="Hello",
            new_string="Hi",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state, "error")
        self.assertIn("not known", chunk.content[0].text)

    async def test_write_without_known_version(self) -> None:
        """Write fails for existing files without a known version."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Existing content\n")

        chunk = await self.write_tool(
            file_path=self.test_file,
            content="New content\n",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state, "error")
        self.assertIn("not known", chunk.content[0].text)

    async def test_write_new_file_without_read(self) -> None:
        """Write succeeds for a new file and caches its version."""
        new_file = os.path.join(self.temp_dir, "new_file.txt")

        chunk = await self.write_tool(
            file_path=new_file,
            content="New file content\n",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state, "success")
        self.assertTrue(os.path.exists(new_file))

        cache = await self.state.tool_context.get_cache(new_file)
        self.assertIsNotNone(cache)
        self.assertEqual(cache.source_kind, "write")

    async def test_edit_after_read(self) -> None:
        """Edit succeeds after Read establishes the file version."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Hello World\n")

        read_chunk = await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )
        self.assertEqual(read_chunk.state, "success")

        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="Hello",
            new_string="Hi",
            _agent_state=self.state,
        )
        self.assertEqual(edit_chunk.state, "success")

        with open(self.test_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Hi World\n")

    async def test_partial_read_still_establishes_version(self) -> None:
        """A partial Read still establishes the known file version."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")

        read_chunk = await self.read_tool(
            file_path=self.test_file,
            offset=2,
            limit=1,
            _agent_state=self.state,
        )
        self.assertEqual(read_chunk.state, "success")
        self.assertIn("line2", read_chunk.content[0].text)
        self.assertNotIn("line1", read_chunk.content[0].text)

        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="line3",
            new_string="final",
            _agent_state=self.state,
        )
        self.assertEqual(edit_chunk.state, "success")

    async def test_write_after_read(self) -> None:
        """Write succeeds after Read establishes the file version."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Old content\n")

        read_chunk = await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )
        self.assertEqual(read_chunk.state, "success")

        write_chunk = await self.write_tool(
            file_path=self.test_file,
            content="New content\n",
            _agent_state=self.state,
        )
        self.assertEqual(write_chunk.state, "success")

        with open(self.test_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "New content\n")

        cache = await self.state.tool_context.get_cache(self.test_file)
        self.assertIsNotNone(cache)
        self.assertEqual(cache.source_kind, "write")

    async def test_write_refreshes_cache_for_followup_edit(self) -> None:
        """Write refreshes the known version for a later Edit."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("original content\n")

        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )

        write_chunk = await self.write_tool(
            file_path=self.test_file,
            content="new content\n",
            _agent_state=self.state,
        )
        self.assertEqual(write_chunk.state, "success")

        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="new content",
            new_string="updated content",
            _agent_state=self.state,
        )
        self.assertEqual(edit_chunk.state, "success")

        with open(self.test_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "updated content\n")

    async def test_edit_refreshes_cache_for_followup_edit(self) -> None:
        """Edit refreshes the known version for another Edit."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("first version\n")

        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )

        first_edit = await self.edit_tool(
            file_path=self.test_file,
            old_string="first",
            new_string="second",
            _agent_state=self.state,
        )
        self.assertEqual(first_edit.state, "success")

        second_edit = await self.edit_tool(
            file_path=self.test_file,
            old_string="second",
            new_string="final",
            _agent_state=self.state,
        )
        self.assertEqual(second_edit.state, "success")

        with open(self.test_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "final version\n")

        cache = await self.state.tool_context.get_cache(self.test_file)
        self.assertIsNotNone(cache)
        self.assertEqual(cache.source_kind, "edit")

    async def test_cache_invalidation_after_file_modification(self) -> None:
        """External content changes invalidate the cached version."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Original content\n")

        read_chunk = await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )
        self.assertEqual(read_chunk.state, "success")

        time.sleep(0.01)
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Modified content\n")

        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="Original",
            new_string="New",
            _agent_state=self.state,
        )

        self.assertEqual(edit_chunk.state, "error")
        self.assertIn("not known", edit_chunk.content[0].text)
        self.assertIsNone(await self.state.tool_context.get_cache(self.test_file))

    async def test_digest_fallback_accepts_mtime_only_change(self) -> None:
        """Digest fallback keeps a cache valid when only mtime changes."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("same content\n")

        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )

        time.sleep(0.01)
        os.utime(self.test_file, None)

        write_chunk = await self.write_tool(
            file_path=self.test_file,
            content="rewritten\n",
            _agent_state=self.state,
        )
        self.assertEqual(write_chunk.state, "success")

    async def test_cache_lru_eviction(self) -> None:
        """Version cache still respects LRU file count limits."""
        self.state.tool_context.max_cache_files = 3

        files = []
        for i in range(4):
            file_path = os.path.join(self.temp_dir, f"file{i}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"Content {i}\n")
            files.append(file_path)

            await self.read_tool(
                file_path=file_path,
                _agent_state=self.state,
            )

        self.assertEqual(len(self.state.tool_context.read_file_cache), 3)
        cached_paths = [
            entry.file_path for entry in self.state.tool_context.read_file_cache
        ]
        self.assertNotIn(files[0], cached_paths)
        self.assertIn(files[1], cached_paths)
        self.assertIn(files[2], cached_paths)
        self.assertIn(files[3], cached_paths)

    async def test_cache_without_state(self) -> None:
        """Tools still work without state-injected cache support."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Hello World\n")

        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="Hello",
            new_string="Hi",
            _agent_state=None,
        )

        self.assertEqual(edit_chunk.state, "success")
        with open(self.test_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Hi World\n")

    async def test_read_caches_version_metadata(self) -> None:
        """Read caches version metadata instead of full file lines."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")

        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )

        cache = await self.state.tool_context.get_cache(self.test_file)
        self.assertIsNotNone(cache)
        self.assertEqual(cache.file_path, self.test_file)
        self.assertEqual(cache.source_kind, "read")
        self.assertGreater(cache.size_bytes, 0)
        self.assertTrue(cache.sha256)
