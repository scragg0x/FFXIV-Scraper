"""Tests for FFXIvScraper.

We don't drive any real HTTP. ``make_request`` is patched to return canned
``requests.Response``-shaped objects with synthesized HTML, and we assert
the scraper extracts the expected fields and raises ``DoesNotExist`` in the
right places.
"""

from __future__ import annotations

import urllib.parse
from unittest.mock import MagicMock, patch

import pytest

from ffxivscraper import DoesNotExist, FFXIvScraper, strip_tags


def _fake_response(content: str | bytes, ok: bool = True) -> MagicMock:
    body = content.encode("utf-8") if isinstance(content, str) else content
    resp = MagicMock(spec=["content", "__bool__"])
    resp.content = body
    resp.__bool__.return_value = ok
    return resp


def test_strip_tags_removes_named_tags_keeping_text() -> None:
    soup = strip_tags("<p>before<br/>after</p>", ["br"])
    # the <br/> tag is replaced with its (empty) text content
    assert soup.text == "beforeafter"


def test_strip_tags_recurses_into_nested_tags() -> None:
    soup = strip_tags("<p>a<br>b<br>c</p>", ["br"])
    assert "abc" in soup.text


def test_constructor_sets_user_agent_and_lodestone_url() -> None:
    s = FFXIvScraper()
    assert s.lodestone_domain == "na.finalfantasyxiv.com"
    assert s.lodestone_url.endswith("/lodestone")
    assert "User-Agent" in s.s.headers


def test_validate_character_returns_none_when_no_results() -> None:
    s = FFXIvScraper()
    html = "<html><body>no results</body></html>"
    with patch.object(s, "make_request", return_value=_fake_response(html)):
        assert s.validate_character("Bahamut", "Tester McTesterson") is None


def test_validate_character_returns_match() -> None:
    s = FFXIvScraper()
    html = """
    <div class="player_name_area">
      <div class="player_name_gold">
        <a href="/lodestone/character/12345/">Tester McTesterson</a>
      </div>
    </div>
    """
    with patch.object(s, "make_request", return_value=_fake_response(html)) as mock:
        result = s.validate_character("Bahamut", "Tester McTesterson")
    assert result == {"lodestone_id": "12345", "name": "Tester McTesterson"}
    # confirm we URL-encoded the character name
    called_url = mock.call_args.kwargs["url"]
    assert urllib.parse.quote_plus("Tester McTesterson") in called_url


def test_validate_character_match_is_case_insensitive() -> None:
    s = FFXIvScraper()
    html = """
    <div class="player_name_area">
      <div class="player_name_gold">
        <a href="/lodestone/character/9/">Lower Case</a>
      </div>
    </div>
    """
    with patch.object(s, "make_request", return_value=_fake_response(html)):
        # query is mixed case, page text is lowercase
        result = s.validate_character("Bahamut", "LOWER case")
    assert result is not None
    assert result["lodestone_id"] == "9"


def test_validate_character_returns_none_on_missing_response() -> None:
    s = FFXIvScraper()
    with patch.object(s, "make_request", return_value=None):
        assert s.validate_character("Bahamut", "X") is None


def test_verify_character_raises_when_no_lodestone_id_and_lookup_fails() -> None:
    s = FFXIvScraper()
    with patch.object(s, "validate_character", return_value=None), pytest.raises(DoesNotExist):
        s.verify_character("Bahamut", "X", "code")


def test_verify_character_returns_lodestone_id_on_match() -> None:
    s = FFXIvScraper()
    html = """
    <div class="player_name_txt">
      <h2>
        <a href="/lodestone/character/1/">Tester McTesterson</a>
        <span>(Bahamut)</span>
      </h2>
    </div>
    <div class="txt_selfintroduction">SECRETCODE</div>
    """
    with patch.object(s, "make_request", return_value=_fake_response(html)):
        result = s.verify_character(
            "Bahamut",
            "Tester McTesterson",
            "SECRETCODE",
            lodestone_id="1",
        )
    assert result == "1"


def test_verify_character_returns_false_on_intro_mismatch() -> None:
    s = FFXIvScraper()
    html = """
    <div class="player_name_txt">
      <h2>
        <a href="/lodestone/character/1/">Tester McTesterson</a>
        <span>(Bahamut)</span>
      </h2>
    </div>
    <div class="txt_selfintroduction">WRONG</div>
    """
    with patch.object(s, "make_request", return_value=_fake_response(html)):
        result = s.verify_character(
            "Bahamut",
            "Tester McTesterson",
            "RIGHT",
            lodestone_id="1",
        )
    assert result is False


def test_verify_character_returns_false_on_name_mismatch() -> None:
    s = FFXIvScraper()
    html = """
    <div class="player_name_txt">
      <h2>
        <a href="/lodestone/character/1/">Wrong Name</a>
        <span>(Bahamut)</span>
      </h2>
    </div>
    <div class="txt_selfintroduction">CODE</div>
    """
    with patch.object(s, "make_request", return_value=_fake_response(html)):
        result = s.verify_character(
            "Bahamut",
            "Tester McTesterson",
            "CODE",
            lodestone_id="1",
        )
    assert result is False


def test_scrape_character_raises_when_response_empty() -> None:
    s = FFXIvScraper()
    with patch.object(s, "make_request", return_value=None), pytest.raises(DoesNotExist):
        s.scrape_character("12345")


def test_scrape_free_company_raises_on_removed_page() -> None:
    s = FFXIvScraper()
    html = b"<html>The page you are searching for has either been removed, sorry.</html>"
    with (
        patch.object(s, "make_request", return_value=_fake_response(html)),
        pytest.raises(DoesNotExist),
    ):
        s.scrape_free_company("99999")


def test_scrape_topics_returns_empty_when_no_response() -> None:
    s = FFXIvScraper()
    with patch.object(s, "make_request", return_value=None):
        assert s.scrape_topics() == []


def test_scrape_topics_extracts_news_entries() -> None:
    s = FFXIvScraper()
    html = """
    <ul class="topics_list">
      <li>
        <div class="topics_list_inner">
          <a href="/lodestone/topics/detail/abc123">Patch Notes</a>
        </div>
        <script>foo({timestamp: 1700000000, more: 'stuff'})</script>
        <div class="area_inner_cont">
          <p>body text with <a href="/relative/link">a link</a></p>
        </div>
      </li>
    </ul>
    """
    with patch.object(s, "make_request", return_value=_fake_response(html)):
        news = s.scrape_topics()
    assert len(news) == 1
    entry = news[0]
    assert entry["id"] == "abc123"
    assert entry["timestamp"] == 1700000000
    assert entry["link"] == "//na.finalfantasyxiv.com/lodestone/topics/detail/abc123"
    assert entry["title"] == "Patch Notes"
    assert "//na.finalfantasyxiv.com/relative/link" in entry["body"]
    assert entry["lang"] == "en"


def test_make_request_returns_none_for_none_url() -> None:
    s = FFXIvScraper()
    assert s.make_request(None) is None
