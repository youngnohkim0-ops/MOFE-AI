"""사용자 입력("소득세법 제95조 제2항" 등)을 ArticleQuery로 파싱."""
from __future__ import annotations

import re

from app.models import ArticleQuery

# 예: "소득세법 제95조의2 제3항 제1호", "제95조제2항", "소득세법 시행령 95조"
_ARTICLE_RE = re.compile(
    r"^\s*(?P<law_name>.*?)\s*"
    r"제?\s*(?P<article>\d+)\s*조"
    r"(?:\s*의\s*(?P<sub>\d+))?"
    r"(?:\s*제?\s*(?P<clause>\d+)\s*항)?"
    r"(?:\s*제?\s*(?P<item>\d+)\s*호)?"
    r"\s*$"
)


class ArticleQueryParseError(ValueError):
    pass


def parse_article_query(text: str, default_law_name: str | None = None) -> ArticleQuery:
    """조문 지정 텍스트를 파싱한다.

    법령명이 입력에 없으면 default_law_name을 사용한다(둘 다 없으면 law_name=None).
    """
    if not text or not text.strip():
        raise ArticleQueryParseError("조문을 입력해주세요. 예: 소득세법 제95조 제2항")

    match = _ARTICLE_RE.match(text.strip())
    if not match:
        raise ArticleQueryParseError(
            f"'{text}'에서 조문 번호를 찾을 수 없습니다. 예: 소득세법 제95조, 제95조 제2항"
        )

    law_name = match.group("law_name").strip() or None
    if law_name is None:
        law_name = default_law_name

    return ArticleQuery(
        law_name=law_name,
        article_no=int(match.group("article")),
        article_sub_no=int(match.group("sub")) if match.group("sub") else None,
        clause_no=int(match.group("clause")) if match.group("clause") else None,
        item_no=int(match.group("item")) if match.group("item") else None,
    )
