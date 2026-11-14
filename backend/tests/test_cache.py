import asyncio

import pytest

from wda.cache import TTLCache


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def run(coro):
    return asyncio.run(coro)


def test_hit_miss_refresh_and_expiry():
    clock = Clock()
    cache = TTLCache(ttl=10, clock=clock)
    calls = []

    async def loader():
        calls.append(1)
        return len(calls)

    async def scenario():
        assert await cache.get_or_load("k", loader) == (1, False)
        assert await cache.get_or_load("k", loader) == (1, True)
        assert await cache.get_or_load("k", loader, refresh=True) == (2, False)
        assert await cache.get_or_load("k", loader) == (2, True)
        clock.now += 10.1
        assert await cache.get_or_load("k", loader) == (3, False)

    run(scenario())


def test_concurrent_identical_requests_share_one_load():
    cache = TTLCache(ttl=10)
    calls = []

    async def loader():
        calls.append(1)
        await asyncio.sleep(0.01)
        return "wert"

    async def scenario():
        results = await asyncio.gather(*[cache.get_or_load("k", loader) for _ in range(10)])
        assert [r[0] for r in results] == ["wert"] * 10
        assert sum(1 for r in results if r[1] is False) >= 1

    run(scenario())
    assert len(calls) == 1


def test_errors_are_not_cached_and_reach_all_waiters():
    cache = TTLCache(ttl=10)
    calls = []

    async def failing():
        calls.append(1)
        await asyncio.sleep(0.01)
        raise RuntimeError("kaputt")

    async def ok():
        return "gut"

    async def scenario():
        results = await asyncio.gather(
            *[cache.get_or_load("k", failing) for _ in range(3)], return_exceptions=True)
        assert all(isinstance(r, RuntimeError) for r in results)
        assert await cache.get_or_load("k", ok) == ("gut", False)

    run(scenario())
    assert len(calls) == 1


def test_max_entries_evicts_oldest():
    clock = Clock()
    cache = TTLCache(ttl=100, max_entries=2, clock=clock)

    async def scenario():
        for key in ("a", "b", "c"):
            clock.now += 1

            async def loader(key=key):
                return key

            await cache.get_or_load(key, loader)
        assert len(cache) == 2

        async def reload():
            return "neu"

        assert await cache.get_or_load("a", reload) == ("neu", False)
        assert (await cache.get_or_load("c", reload))[1] is True

    run(scenario())


def test_expired_entries_are_swept():
    clock = Clock()
    cache = TTLCache(ttl=10, max_entries=4, clock=clock)

    async def scenario():
        async def loader():
            return 1

        await cache.get_or_load("a", loader)
        await cache.get_or_load("b", loader)
        clock.now += 11
        await cache.get_or_load("c", loader)
        assert len(cache) == 1

    run(scenario())


def test_ttl_zero_disables_caching():
    cache = TTLCache(ttl=0)
    calls = []

    async def loader():
        calls.append(1)
        return 1

    async def scenario():
        await cache.get_or_load("k", loader)
        await cache.get_or_load("k", loader)

    run(scenario())
    assert len(calls) == 2
    assert len(cache) == 0


def test_cancelled_waiter_does_not_break_others():
    cache = TTLCache(ttl=10)

    async def loader():
        await asyncio.sleep(0.05)
        return "wert"

    async def scenario():
        first = asyncio.create_task(cache.get_or_load("k", loader))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(cache.get_or_load("k", loader))
        await asyncio.sleep(0.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await second)[0] == "wert"

    run(scenario())
