import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "daikin_ir"))

from sender import IRSender  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_burst_is_coalesced_into_last_code():
    async def main():
        sent = []

        async def publish(code):
            sent.append(code)

        sender = IRSender(publish, debounce=0.05, min_interval=0)
        for code in ("mode", "fan", "temp"):
            sender.schedule(code)
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.15)
        return sent

    assert _run(main()) == ["temp"]


def test_sends_are_spaced_and_never_overlap():
    async def main():
        stamps, active = [], 0

        async def publish(code):
            nonlocal active
            active += 1
            assert active == 1, "overlapping publish"
            stamps.append(time.monotonic())
            await asyncio.sleep(0.02)
            active -= 1

        sender = IRSender(publish, debounce=0.01, min_interval=0.1)
        sender.schedule("a")
        await asyncio.sleep(0.03)
        sender.schedule("b")
        await asyncio.sleep(0.03)
        sender.schedule("c")
        await asyncio.sleep(0.4)
        return stamps

    stamps = _run(main())
    assert len(stamps) >= 2
    assert all(b - a >= 0.095 for a, b in zip(stamps, stamps[1:]))


def test_publish_failure_does_not_stop_later_sends():
    async def main():
        sent = []

        async def publish(code):
            if code == "bad":
                raise RuntimeError("broker down")
            sent.append(code)

        sender = IRSender(publish, debounce=0.01, min_interval=0)
        sender.schedule("bad")
        await asyncio.sleep(0.05)
        sender.schedule("good")
        await asyncio.sleep(0.05)
        return sent

    assert _run(main()) == ["good"]


def test_cancel_drops_pending():
    async def main():
        sent = []

        async def publish(code):
            sent.append(code)

        sender = IRSender(publish, debounce=0.05, min_interval=0)
        sender.schedule("x")
        sender.cancel()
        await asyncio.sleep(0.1)
        return sent

    assert _run(main()) == []
