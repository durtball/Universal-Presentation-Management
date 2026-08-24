import asyncio
from uuid import uuid4

from upm_central.presentation_media_api import stream_transfer_object


class RecordingStorage:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.calls: list[tuple[object, str, int, int]] = []
        self.closed = False

    async def stream_object(self, target_id, storage_key, offset, count):
        self.calls.append((target_id, storage_key, offset, count))
        try:
            for chunk in self.chunks:
                yield chunk
        finally:
            self.closed = True


def test_transfer_streams_requested_range_and_closes_storage_stream() -> None:
    async def exercise() -> None:
        target_id = uuid4()
        storage = RecordingStorage([b"part-one", b"part-two"])
        result = b"".join(
            [
                chunk
                async for chunk in stream_transfer_object(storage, target_id, "objects/deck", 7, 12)
            ]
        )

        assert result == b"part-onepart-two"
        assert storage.calls == [(target_id, "objects/deck", 7, 12)]
        assert storage.closed

    asyncio.run(exercise())


def test_closing_transfer_stream_early_closes_storage_stream() -> None:
    async def exercise() -> None:
        storage = RecordingStorage([b"first", b"second"])
        stream = stream_transfer_object(storage, uuid4(), "objects/deck", 0, 11)

        assert await anext(stream) == b"first"
        await stream.aclose()

        assert storage.closed

    asyncio.run(exercise())


def test_cancelling_transfer_stream_closes_storage_stream() -> None:
    async def exercise() -> None:
        started = asyncio.Event()
        closed = asyncio.Event()

        class BlockingStorage:
            async def stream_object(self, *_args):
                try:
                    started.set()
                    await asyncio.Event().wait()
                    yield b"unreachable"
                finally:
                    closed.set()

        stream = stream_transfer_object(BlockingStorage(), uuid4(), "objects/deck", 3, 8)
        pending = asyncio.create_task(anext(stream))
        await started.wait()
        pending.cancel()

        try:
            await pending
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancelled stream did not raise CancelledError")

        assert closed.is_set()

    asyncio.run(exercise())
