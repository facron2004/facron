"""WebSocket connection manager for real-time task updates."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Awaitable, Callable

from fastapi import WebSocket

from review_scraper.core.cache import get_redis

logger = logging.getLogger(__name__)

TASK_UPDATE_CHANNEL = "review_scraper:task_updates"


class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self) -> None:
        self.active_connections: dict[str, list[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, task_id: str) -> None:
        """Accept a new WebSocket connection for a specific task."""
        await websocket.accept()
        async with self._lock:
            if task_id not in self.active_connections:
                self.active_connections[task_id] = []
            self.active_connections[task_id].append(websocket)
        logger.info(f"WebSocket connected for task {task_id}")

    async def disconnect(self, websocket: WebSocket, task_id: str) -> None:
        """Remove a WebSocket connection."""
        async with self._lock:
            if task_id in self.active_connections:
                if websocket in self.active_connections[task_id]:
                    self.active_connections[task_id].remove(websocket)
                if not self.active_connections[task_id]:
                    del self.active_connections[task_id]
        logger.info(f"WebSocket disconnected for task {task_id}")

    async def send_update(self, task_id: str, message: dict[str, Any]) -> None:
        """Send an update to all connections watching a specific task."""
        async with self._lock:
            if task_id not in self.active_connections:
                return

            connections = self.active_connections[task_id].copy()

        disconnected = []
        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send update to connection: {e}")
                disconnected.append(connection)

        # Clean up disconnected clients
        if disconnected:
            async with self._lock:
                if task_id in self.active_connections:
                    for conn in disconnected:
                        if conn in self.active_connections[task_id]:
                            self.active_connections[task_id].remove(conn)
                    if not self.active_connections[task_id]:
                        del self.active_connections[task_id]

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all connected clients."""
        async with self._lock:
            all_connections = [
                conn for conns in self.active_connections.values() for conn in conns
            ]

        for connection in all_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to broadcast to connection: {e}")

    def get_connection_count(self, task_id: str | None = None) -> int:
        """Get the number of active connections for a task or all tasks."""
        if task_id:
            return len(self.active_connections.get(task_id, []))
        return sum(len(conns) for conns in self.active_connections.values())


class RedisTaskUpdatePublisher:
    def __init__(self) -> None:
        self._redis = get_redis()

    def publish(self, task_id: str, message: dict[str, Any]) -> None:
        client = self._redis
        if client is None:
            return
        payload = json.dumps({"task_id": task_id, "message": message}, default=str)
        try:
            client.publish(TASK_UPDATE_CHANNEL, payload)
        except Exception as exc:
            logger.warning("Failed to publish task update for %s: %s", task_id, exc)


class RedisTaskUpdateSubscriber:
    def __init__(self, on_message: Callable[[str, dict[str, Any]], Awaitable[None]]) -> None:
        self._redis = get_redis()
        self._on_message = on_message
        self._thread: threading.Thread | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._redis is None or self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()

        def _run() -> None:
            pubsub = self._redis.pubsub()
            try:
                pubsub.subscribe(TASK_UPDATE_CHANNEL)
                for message in pubsub.listen():
                    if not self._running:
                        break
                    if not message or message.get("type") != "message":
                        continue
                    data = message.get("data")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="ignore")
                    if not data:
                        continue
                    try:
                        decoded = json.loads(data)
                    except Exception:
                        continue
                    task_id = decoded.get("task_id")
                    payload = decoded.get("message")
                    if task_id and isinstance(payload, dict) and self._loop is not None:
                        future = asyncio.run_coroutine_threadsafe(self._on_message(task_id, payload), self._loop)
                        try:
                            future.result(timeout=5)
                        except Exception as exc:
                            logger.warning("Failed to fan out task update for %s: %s", task_id, exc)
            finally:
                try:
                    pubsub.close()
                except Exception:
                    pass

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._running = False
        self._loop = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


# Global connection manager instance
_manager: ConnectionManager | None = None
_update_publisher: RedisTaskUpdatePublisher | None = None
_subscriber: RedisTaskUpdateSubscriber | None = None


def get_connection_manager() -> ConnectionManager:
    """Get the global connection manager instance."""
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager


def get_task_update_publisher() -> RedisTaskUpdatePublisher:
    global _update_publisher
    if _update_publisher is None:
        _update_publisher = RedisTaskUpdatePublisher()
    return _update_publisher


def get_task_update_subscriber() -> RedisTaskUpdateSubscriber:
    global _subscriber
    if _subscriber is None:
        async def _fanout(task_id: str, payload: dict[str, Any]) -> None:
            await get_connection_manager().send_update(task_id, payload)

        _subscriber = RedisTaskUpdateSubscriber(_fanout)
    return _subscriber
