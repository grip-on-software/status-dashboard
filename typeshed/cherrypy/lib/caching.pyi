from typing import Any

class MemoryCache:
    def get(self) -> Any: ...
    def put(self, data: Any, size: int) -> None: ...
    def clear(self) -> None: ...
