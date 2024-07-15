from typing import BinaryIO, Optional

class Entity:
    content_type: Optional[str] = None
    filename: Optional[str] = None

class Part(Entity):
    file: Optional[BinaryIO] = None
