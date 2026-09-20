"""조문/판례/결정례 등을 표현하는 데이터 모델."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArticleQuery:
    """사용자 입력에서 파싱한 조문 지정 정보."""

    law_name: str | None
    article_no: int
    article_sub_no: int | None = None
    clause_no: int | None = None
    item_no: int | None = None

    def label(self) -> str:
        parts = [self.law_name or ""]
        art = f"제{self.article_no}조"
        if self.article_sub_no:
            art += f"의{self.article_sub_no}"
        parts.append(art)
        if self.clause_no:
            parts.append(f"제{self.clause_no}항")
        if self.item_no:
            parts.append(f"제{self.item_no}호")
        return " ".join(p for p in parts if p)


@dataclass(frozen=True)
class LawSummary:
    law_id: str
    mst: str
    name: str
    law_type: str
    promulgation_date: str
    enforcement_date: str
    status: str = ""
    """법제처 현행연혁코드(예: "현행", "연혁"). API가 값을 안 주면 빈 문자열."""


@dataclass(frozen=True)
class ClauseText:
    clause_no: str | None
    content: str
    items: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ArticleText:
    law_name: str
    law_type: str
    mst: str
    article_no: str
    article_sub_no: str | None
    title: str | None
    full_text: str
    clauses: list[ClauseText] = field(default_factory=list)
    source_url: str | None = None


@dataclass(frozen=True)
class Precedent:
    case_id: str
    case_no: str
    court: str
    decision_date: str
    case_name: str
    summary: str
    url: str | None = None


@dataclass(frozen=True)
class RulingCase:
    """법령해석례·조세심판원 결정례 등 유권해석/행정심판 성격의 자료."""

    ruling_id: str
    title: str
    agency: str
    decision_date: str
    summary: str
    url: str | None = None
    verified: bool = True
    """공식 API로 확인된 자료이면 True, 비공식/크롤링 추정 자료면 False."""
