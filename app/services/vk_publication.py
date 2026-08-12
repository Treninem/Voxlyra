"""Compatibility imports for the canonical cross-platform publication service.

New code should import from ``app.services.cross_platform_publication`` directly.
This shim remains temporarily because deployed v1.16.1 code may still import the
older module path. It must not contain its own publication logic.
"""

from app.services.cross_platform_publication import (
    post_book_to_vk_wall,
    should_retry_vk_wall_post,
    vk_book_url,
)

__all__ = [
    "post_book_to_vk_wall",
    "should_retry_vk_wall_post",
    "vk_book_url",
]
