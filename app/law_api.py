"""법제처 국가법령정보센터 Open API(law.go.kr/DRF) 클라이언트.

Open API 가이드: https://open.law.go.kr/LSO/openApi/guideList.do
인증키(OC)는 law.go.kr에 이메일로 가입한 ID(계정) 부분이다.

주의: 이 모듈의 XML 태그명은 공개된 Open API 가이드를 기준으로 작성했다.
법제처가 필드명을 변경하거나 법령 종류별로 스키마가 약간 다를 수 있으므로,
실제 인증키로 첫 호출을 해본 뒤 _TAG 상수들을 필요에 맞게 조정할 것.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import requests

from app.models import ArticleQuery, ArticleText, ClauseText, LawSummary

LAW_SEARCH_URL = "http://www.law.go.kr/DRF/lawSearch.do"
LAW_SERVICE_URL = "http://www.law.go.kr/DRF/lawService.do"

_LAW_TYPE_SUFFIXES = ("시행규칙", "시행령", "법")


class LawApiError(RuntimeError):
    pass


class ArticleNotFoundError(LawApiError):
    pass


def _text(elem: ET.Element | None, *tag_candidates: str) -> str | None:
    if elem is None:
        return None
    for tag in tag_candidates:
        found = elem.find(tag)
        if found is not None and found.text:
            return found.text.strip()
    return None


def infer_law_type(law_name: str) -> str:
    """법령명으로부터 법률/시행령/시행규칙 구분을 추정한다."""
    if law_name.endswith("시행규칙"):
        return "시행규칙"
    if law_name.endswith("시행령"):
        return "시행령"
    return "법률"


class LawOpenApiClient:
    def __init__(self, oc: str, session: requests.Session | None = None, timeout: float = 10.0):
        if not oc:
            raise LawApiError("법제처 Open API 인증키(OC)가 설정되지 않았습니다.")
        self.oc = oc
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, url: str, params: dict) -> ET.Element:
        params = {"OC": self.oc, "type": "XML", **params}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LawApiError(f"법제처 Open API 호출 실패: {exc}") from exc
        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise LawApiError(f"법제처 Open API 응답 파싱 실패: {exc}") from exc

    def search_law(self, law_name: str, display: int = 20) -> list[LawSummary]:
        root = self._get(LAW_SEARCH_URL, {"target": "law", "query": law_name, "display": display})
        results: list[LawSummary] = []
        for node in root.findall("law"):
            name = _text(node, "법령명한글", "법령명_한글")
            if not name:
                continue
            results.append(
                LawSummary(
                    law_id=_text(node, "법령ID") or "",
                    mst=_text(node, "법령일련번호") or "",
                    name=name,
                    law_type=_text(node, "법령구분명") or infer_law_type(name),
                    promulgation_date=_text(node, "공포일자") or "",
                    enforcement_date=_text(node, "시행일자") or "",
                )
            )
        return results

    def find_current_law(self, law_name: str) -> LawSummary | None:
        """이름이 정확히 일치하는 현행 법령을 찾는다(가장 최근 시행일자 우선)."""
        candidates = [law for law in self.search_law(law_name) if law.name == law_name]
        if not candidates:
            return None
        return sorted(candidates, key=lambda law: law.enforcement_date, reverse=True)[0]

    def get_law_xml(self, mst: str) -> ET.Element:
        return self._get(LAW_SERVICE_URL, {"target": "law", "MST": mst})

    def get_article(self, law: LawSummary, article_no: int, article_sub_no: int | None = None) -> ArticleText:
        root = self.get_law_xml(law.mst)
        for unit in root.iter("조문단위"):
            no = _text(unit, "조문번호")
            sub = _text(unit, "조문가지번호") or "0"
            if no is None or int(no) != article_no:
                continue
            expected_sub = article_sub_no or 0
            if int(sub) != expected_sub:
                continue
            return _parse_article_unit(law, unit)
        sub_label = f"의{article_sub_no}" if article_sub_no else ""
        raise ArticleNotFoundError(f"{law.name} 제{article_no}조{sub_label}를 찾을 수 없습니다.")


def _parse_article_unit(law: LawSummary, unit: ET.Element) -> ArticleText:
    clauses: list[ClauseText] = []
    for hang in unit.findall("항"):
        items = [
            item_content
            for ho in hang.findall("호")
            if (item_content := _text(ho, "호내용"))
        ]
        clause_content = _text(hang, "항내용") or ""
        clauses.append(
            ClauseText(clause_no=_text(hang, "항번호"), content=clause_content, items=items)
        )

    full_text = _text(unit, "조문내용") or ""
    if not full_text and clauses:
        full_text = "\n".join(c.content for c in clauses)

    return ArticleText(
        law_name=law.name,
        law_type=law.law_type,
        mst=law.mst,
        article_no=_text(unit, "조문번호") or "",
        article_sub_no=_text(unit, "조문가지번호"),
        title=_text(unit, "조문제목"),
        full_text=full_text,
        clauses=clauses,
        source_url=f"https://www.law.go.kr/법령/{law.name}",
    )


def resolve_article(client: LawOpenApiClient, query: ArticleQuery) -> tuple[LawSummary, ArticleText]:
    if not query.law_name:
        raise LawApiError("법령명이 지정되지 않았습니다.")
    law = client.find_current_law(query.law_name)
    if law is None:
        raise LawApiError(f"'{query.law_name}' 법령을 찾을 수 없습니다.")
    article = client.get_article(law, query.article_no, query.article_sub_no)
    return law, article


def find_subordinate_laws(client: LawOpenApiClient, base_law_name: str) -> list[LawSummary]:
    """base_law_name의 시행령/시행규칙을 이름 규칙으로 찾는다.

    법제처 Open API의 위임법령 연계 API를 쓰면 더 정확하지만, 여기서는
    "{법명} 시행령"/"{법명} 시행규칙" 이름 규칙에 기반한 근사 매칭을 사용한다.
    """
    base_name = base_law_name
    for suffix in ("시행규칙", "시행령"):
        if base_name.endswith(suffix):
            base_name = base_name[: -len(suffix)].strip()
            break

    subordinates = []
    for suffix in ("시행령", "시행규칙"):
        law = client.find_current_law(f"{base_name}{' ' if not base_name.endswith(' ') else ''}{suffix}".strip())
        if law is None:
            law = client.find_current_law(f"{base_name} {suffix}")
        if law:
            subordinates.append(law)
    return subordinates


def find_related_articles(
    client: LawOpenApiClient, subordinate_laws: list[LawSummary], article_no: int, article_sub_no: int | None = None
) -> list[ArticleText]:
    """시행령/시행규칙에서 동일 조번호의 조문을 추정 매칭한다(위임관계와 다를 수 있음)."""
    related = []
    for law in subordinate_laws:
        try:
            related.append(client.get_article(law, article_no, article_sub_no))
        except ArticleNotFoundError:
            continue
    return related
