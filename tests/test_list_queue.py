"""queue_render must use the connection that created the list."""

from __future__ import annotations

import uuid
from typing import Any

from personal_context_mcp.lists.service import ListService
from personal_context_mcp.obsidian.paths import VaultPaths

LIST_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
ITEM_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
ROOTS = {
    "inbox": "00-Inbox",
    "project": "20-Projects",
    "resource": "60-Resources",
    "atlas": "70-Atlas",
}


class Pool:
    """Stand-in for the shared pool. A transaction's connection must not fall through to it."""

    async def fetchrow(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("queue read the pool")

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("queue wrote through the pool")

    async def fetchval(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("queue read the pool")

    async def fetch(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("queue read the pool")


class Conn:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.writes: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.statements.append(sql)
        if "INSERT INTO lists" in sql:
            return {"id": LIST_ID, "created": True}
        if "INSERT INTO list_items" in sql:
            return {"id": ITEM_ID, "created": True}
        if "FROM lists" in sql:
            return {"slug": "groceries", "vault_path": None, "project_slug": None}
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        self.statements.append(sql)
        self.writes.append((sql, args))
        return "UPDATE 1"

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.statements.append(sql)
        if "max(position)" in sql:
            return 1
        return ITEM_ID


class Queue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    async def enqueue(self, **kwargs: Any) -> uuid.UUID:
        self.jobs.append(kwargs)
        return uuid.uuid4()


def _service(conn_pool: Any, queue: Queue) -> ListService:
    return ListService(conn_pool, sync_queue=queue, vault_paths=VaultPaths(ROOTS))


def _vault_update(conn: Conn) -> tuple[Any, ...]:
    return next(args for sql, args in conn.writes if "vault_path" in sql)


async def test_create_queues_the_render_on_the_callers_connection() -> None:
    conn = Conn()
    queue = Queue()
    list_id, created = await _service(Pool(), queue).create(name="Groceries", conn=conn)
    assert created is True
    assert list_id == LIST_ID
    assert len(queue.jobs) == 1
    assert queue.jobs[0]["conn"] is conn
    assert queue.jobs[0]["job_type"] == "write_list"
    assert queue.jobs[0]["vault_path"] == "60-Resources/Lists/groceries.md"
    assert _vault_update(conn) == (LIST_ID, "60-Resources/Lists/groceries.md")


async def test_add_item_queues_the_render_on_the_callers_connection() -> None:
    conn = Conn()
    queue = Queue()
    item_id, created = await _service(Pool(), queue).add_item(
        list_id=LIST_ID, text="Oat milk", conn=conn
    )
    assert created is True
    assert item_id == ITEM_ID
    assert queue.jobs[0]["conn"] is conn
    assert _vault_update(conn)[0] == LIST_ID


async def test_queue_render_without_a_connection_uses_the_pool() -> None:
    class Recording:
        def __init__(self) -> None:
            self.calls = 0

        async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any]:
            self.calls += 1
            return {
                "slug": "todo",
                "vault_path": "60-Resources/Lists/todo.md",
                "project_slug": None,
            }

        async def execute(self, *_args: Any) -> None:
            raise AssertionError("vault path was already set")

    db = Recording()
    queue = Queue()
    await _service(db, queue).queue_render(LIST_ID)
    assert db.calls == 1
    assert queue.jobs[0]["conn"] is None
    assert queue.jobs[0]["vault_path"] == "60-Resources/Lists/todo.md"
