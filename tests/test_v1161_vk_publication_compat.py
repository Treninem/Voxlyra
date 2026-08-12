from app.services import cross_platform_publication as canonical
from app.services import vk_publication as compat


def test_vk_publication_compatibility_module_has_no_forked_logic():
    assert compat.post_book_to_vk_wall is canonical.post_book_to_vk_wall
    assert compat.should_retry_vk_wall_post is canonical.should_retry_vk_wall_post
    assert compat.build_vk_book_post is canonical.build_vk_book_post
    assert compat.vk_book_url is canonical.vk_book_url
    assert compat.vk_votes_from_stars is canonical.vk_votes_from_stars
