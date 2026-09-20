from unittest.mock import MagicMock

import pytest

from app.law_api import (
    ArticleNotFoundError,
    LawApiError,
    LawOpenApiClient,
    find_related_articles,
    find_subordinate_laws,
    resolve_article,
)
from app.models import ArticleQuery, LawSummary

SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LawSearch>
  <totalCnt>1</totalCnt>
  <law id="1">
    <법령일련번호>123456</법령일련번호>
    <법령명한글>소득세법</법령명한글>
    <법령ID>001234</법령ID>
    <공포일자>20230101</공포일자>
    <시행일자>20230701</시행일자>
    <법령구분명>법률</법령구분명>
  </law>
</LawSearch>
"""

LAW_DETAIL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<법령>
  <조문>
    <조문단위>
      <조문번호>94</조문번호>
      <조문가지번호>0</조문가지번호>
      <조문제목>양도소득의 범위</조문제목>
      <조문내용>제94조(양도소득의 범위) 다른 조문</조문내용>
    </조문단위>
    <조문단위>
      <조문번호>95</조문번호>
      <조문가지번호>0</조문가지번호>
      <조문제목>양도소득금액</조문제목>
      <조문내용>제95조(양도소득금액) ① 양도소득금액은 다음과 같다.</조문내용>
      <항>
        <항번호>1</항번호>
        <항내용>① 양도소득금액은 다음과 같다.</항내용>
        <호>
          <호번호>1</호번호>
          <호내용>1. 토지</호내용>
        </호>
        <호>
          <호번호>2</호번호>
          <호내용>2. 건물</호내용>
        </호>
      </항>
      <항>
        <항번호>2</항번호>
        <항내용>② 제1항의 계산은 대통령령으로 정한다.</항내용>
      </항>
    </조문단위>
  </조문>
</법령>
"""


def _search_xml_for(name: str) -> str:
    return SEARCH_XML.replace("소득세법", name)


def _make_client(get_responses: list[str]) -> LawOpenApiClient:
    client = LawOpenApiClient(oc="test-oc")
    fake_session = MagicMock()

    responses = iter(get_responses)

    def fake_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.content = next(responses).encode("utf-8")
        resp.raise_for_status = MagicMock()
        return resp

    fake_session.get.side_effect = fake_get
    client.session = fake_session
    return client


def test_search_law_parses_summary():
    client = _make_client([SEARCH_XML])
    results = client.search_law("소득세법")
    assert len(results) == 1
    assert results[0].name == "소득세법"
    assert results[0].mst == "123456"
    assert results[0].law_type == "법률"


def test_find_current_law_exact_match():
    client = _make_client([SEARCH_XML])
    law = client.find_current_law("소득세법")
    assert law is not None
    assert law.mst == "123456"


def test_get_article_returns_matching_unit():
    client = _make_client([LAW_DETAIL_XML])
    law = LawSummary(
        law_id="001234", mst="123456", name="소득세법", law_type="법률",
        promulgation_date="20230101", enforcement_date="20230701",
    )
    article = client.get_article(law, 95)
    assert article.title == "양도소득금액"
    assert len(article.clauses) == 2
    assert article.clauses[0].items == ["1. 토지", "2. 건물"]


def test_get_article_not_found_raises():
    client = _make_client([LAW_DETAIL_XML])
    law = LawSummary(
        law_id="001234", mst="123456", name="소득세법", law_type="법률",
        promulgation_date="20230101", enforcement_date="20230701",
    )
    with pytest.raises(ArticleNotFoundError):
        client.get_article(law, 999)


def test_resolve_article_end_to_end():
    client = _make_client([SEARCH_XML, LAW_DETAIL_XML])
    query = ArticleQuery(law_name="소득세법", article_no=95)
    law, article = resolve_article(client, query)
    assert law.name == "소득세법"
    assert article.article_no == "95"


def test_resolve_article_missing_law_name_raises():
    client = _make_client([])
    query = ArticleQuery(law_name=None, article_no=95)
    with pytest.raises(LawApiError):
        resolve_article(client, query)


def test_find_subordinate_laws_and_related_articles():
    client = _make_client(
        [
            _search_xml_for("소득세법 시행령"),
            _search_xml_for("소득세법 시행규칙"),
            LAW_DETAIL_XML,
            LAW_DETAIL_XML,
        ]
    )
    subs = find_subordinate_laws(client, "소득세법")
    assert len(subs) == 2
    assert {s.name for s in subs} == {"소득세법 시행령", "소득세법 시행규칙"}
    related = find_related_articles(client, subs, 95)
    assert len(related) == 2
