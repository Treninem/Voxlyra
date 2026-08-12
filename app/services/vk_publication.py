"""Compatibility import for the canonical cross-platform publication service.

New code should import from ``app.services.cross_platform_publication`` directly.
This shim remains temporarily because the shared publication workflow already
imports this module in deployed v1.16.1 builds.
"""

from app.services.cross_platform_publication import post_book_to_vk_wall, vk_book_url

__all__ = ["post_book_to_vk_wall", "vk_book_url"]
