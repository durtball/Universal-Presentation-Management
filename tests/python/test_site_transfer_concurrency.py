from threading import Event, Lock
from time import sleep
from uuid import uuid4

from upm_site.worker import PULL_TRANSFER, PUSH_TRANSFER, TransferExecutors


def test_pull_concurrency_one_remains_serialized() -> None:
    executors = TransferExecutors(1, 1)
    started = Event()
    release = Event()
    try:
        assert executors.submit(PULL_TRANSFER, uuid4(), lambda: (started.set(), release.wait()))
        assert started.wait(2)
        assert not executors.submit(PULL_TRANSFER, uuid4(), lambda: None)
        assert executors.available(PULL_TRANSFER) == 0
    finally:
        release.set()
        executors.shutdown()


def test_four_pulls_run_and_fifth_waits_for_a_slot() -> None:
    executors = TransferExecutors(4, 1)
    started = [Event() for _ in range(5)]
    releases = [Event() for _ in range(5)]
    active = 0
    maximum_active = 0
    lock = Lock()

    def operation(index: int) -> None:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        started[index].set()
        releases[index].wait()
        with lock:
            active -= 1

    try:
        for index in range(4):
            assert executors.submit(PULL_TRANSFER, uuid4(), lambda index=index: operation(index))
        assert all(item.wait(2) for item in started[:4])
        assert maximum_active == 4
        assert not executors.submit(PULL_TRANSFER, uuid4(), lambda: operation(4))

        releases[0].set()
        for _ in range(100):
            executors.reap()
            if executors.available(PULL_TRANSFER) == 1:
                break
            sleep(0.01)
        assert executors.submit(PULL_TRANSFER, uuid4(), lambda: operation(4))
        assert started[4].wait(2)
    finally:
        for release in releases:
            release.set()
        executors.shutdown()


def test_transfer_failure_does_not_stop_other_direction_or_slots() -> None:
    executors = TransferExecutors(2, 2)
    pull_running = Event()
    push_running = Event()
    release = Event()

    def failure() -> None:
        raise RuntimeError("one transfer failed")

    try:
        assert executors.submit(PULL_TRANSFER, uuid4(), failure)
        assert executors.submit(
            PULL_TRANSFER, uuid4(), lambda: (pull_running.set(), release.wait())
        )
        assert executors.submit(
            PUSH_TRANSFER, uuid4(), lambda: (push_running.set(), release.wait())
        )
        assert pull_running.wait(2)
        assert push_running.wait(2)
        failures = []
        for _ in range(100):
            failures.extend(executors.reap())
            if failures:
                break
            sleep(0.01)
        assert len(failures) == 1
        assert isinstance(failures[0][1], RuntimeError)
        assert executors.available(PULL_TRANSFER) == 1
        assert executors.available(PUSH_TRANSFER) == 1
    finally:
        release.set()
        executors.shutdown()
