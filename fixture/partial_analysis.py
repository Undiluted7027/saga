"""Deliberate partial-analysis examples for the final POC demo."""


def partial_result(value):
    """Keep a simple return dependency visible beside unsupported context-manager behavior."""
    result = value + 1
    with open("ignored-by-the-fixture", "w", encoding="utf-8") as handle:
        handle.write(str(result))
    return result


async def unsupported_async(value):
    """Provide an explicit unsupported target for the demo."""
    return value
