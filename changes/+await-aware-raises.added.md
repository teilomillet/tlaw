`tlaw.raises` awaits an `async` target and accepts any exception type, so a cancellation contract on a coroutine function needs no `asyncio.run` shim.
