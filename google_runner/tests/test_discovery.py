from app.services.discovery import (
    LEGACY_CATEGORY_TO_TYPE,
    build_google_query,
    classify_facebook_url,
    extract_facebook_results,
    normalize_google_result_url,
)


def test_build_google_query_with_dates():
    assert (
        build_google_query("openai", after="2025-01-01", before="2025-12-31")
        == 'site:facebook.com "openai" after:2025-01-01 before:2025-12-31'
    )


def test_normalize_google_result_url_extracts_facebook_target():
    href = "/url?q=https://www.facebook.com/openai/posts/123&sa=U"
    assert normalize_google_result_url(href) == "https://www.facebook.com/openai/posts/123"


def test_classify_facebook_url_covers_supported_types():
    assert classify_facebook_url("https://www.facebook.com/openai/posts/123") == "Posts"
    assert classify_facebook_url("https://www.facebook.com/story.php?story_fbid=1&id=2") == "Stories"
    assert classify_facebook_url("https://www.facebook.com/reel/123") == "Reels"
    assert classify_facebook_url("https://www.facebook.com/photo.php?fbid=123") == "Fotos"
    assert classify_facebook_url("https://www.facebook.com/permalink.php?story_fbid=1&id=2") == "Permalinks"
    assert classify_facebook_url("https://www.facebook.com/openai/videos/123") == "Videos"
    assert classify_facebook_url("https://www.facebook.com/openai") == "Perfil"


def test_extract_facebook_results_deduplicates_and_maps_categories():
    html = """
    <html><body>
      <a href="/url?q=https://www.facebook.com/openai/posts/123&sa=U">post</a>
      <a href="/url?q=https://www.facebook.com/openai/posts/123&sa=U">dup</a>
      <a href="https://www.facebook.com/openai/reel/456">reel</a>
      <a href="https://www.google.com/search?q=test">google</a>
    </body></html>
    """

    results = extract_facebook_results(html)

    assert len(results) == 2
    assert results[0]["categoria"] == "Posts"
    assert results[1]["categoria"] == "Reels"
    assert LEGACY_CATEGORY_TO_TYPE[results[0]["categoria"]] == "post"
    assert LEGACY_CATEGORY_TO_TYPE[results[1]["categoria"]] == "reel"
