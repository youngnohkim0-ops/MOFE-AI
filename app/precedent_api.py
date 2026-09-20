"""판례, 법령해석례, 조세심판원 결정례 조회.

- 대법원 판례 / 법령해석례: 법제처 국가법령정보센터 Open API(law.go.kr/DRF)를 사용한다.
  (target=prec: 판례, target=expc: 법령해석례)
- 조세심판원 결정례: 국세청 "국세법령정보시스템"(taxlaw.nts.go.kr)은 공식 Open API를
  공개하고 있지 않다. 아래 TaxTribunalClient는 best-effort 검색을 시도하되, 실패 시
  예외를 던지지 않고 빈 목록 + 수동 확인 링크로 안전하게 degrade한다.
  실제 서비스에서는 (1) 국세청에 API 이용 문의를 하거나 (2) 결정례를 정기적으로 수집해
  Supabase 등에 적재하는 배치를 별도로 구축하는 것을 권장한다.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import requests

from app.models import Precedent, RulingCase

logger = logging.getLogger(__name__)

LAW_SEARCH_URL = "http://www.law.go.kr/DRF/lawSearch.do"

NTS_TAXLAW_HOME_URL = "https://taxlaw.nts.go.kr"


def _text(elem: ET.Element | None, *tag_candidates: str) -> str | None:
    if elem is None:
        return None
    for tag in tag_candidates:
        found = elem.find(tag)
        if found is not None and found.text:
            return found.text.strip()
    return None


class PrecedentClient:
    """법제처 Open API 기반 판례/법령해석례 클라이언트."""

    def __init__(self, oc: str, session: requests.Session | None = None, timeout: float = 10.0):
        if not oc:
            raise ValueError("법제처 Open API 인증키(OC)가 설정되지 않았습니다.")
        self.oc = oc
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, params: dict) -> ET.Element:
        params = {"OC": self.oc, "type": "XML", **params}
        resp = self.session.get(LAW_SEARCH_URL, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return ET.fromstring(resp.content)

    def search_precedents(self, query: str, display: int = 10) -> list[Precedent]:
        root = self._get({"target": "prec", "query": query, "display": display})
        results: list[Precedent] = []
        for node in root.findall("prec"):
            case_name = _text(node, "사건명")
            if not case_name:
                continue
            results.append(
                Precedent(
                    case_id=_text(node, "판례일련번호") or "",
                    case_no=_text(node, "사건번호") or "",
                    court=_text(node, "법원명") or "",
                    decision_date=_text(node, "선고일자") or "",
                    case_name=case_name,
                    summary=_text(node, "판결요지", "판시사항") or "",
                    url=_text(node, "판례상세링크"),
                )
            )
        return results

    def search_statutory_interpretations(self, query: str, display: int = 10) -> list[RulingCase]:
        root = self._get({"target": "expc", "query": query, "display": display})
        results: list[RulingCase] = []
        for node in root.findall("expc"):
            title = _text(node, "안건명")
            if not title:
                continue
            results.append(
                RulingCase(
                    ruling_id=_text(node, "법령해석례일련번호") or "",
                    title=title,
                    agency=_text(node, "회신기관명") or "법제처(법령해석례)",
                    decision_date=_text(node, "회신일자") or "",
                    summary=title,
                    url=_text(node, "해석례상세링크"),
                    verified=True,
                )
            )
        return results


class TaxTribunalClient:
    """국세청 국세법령정보시스템의 조세심판원 결정례 best-effort 검색.

    공식 API가 없어 정확한 결과를 보장하지 않는다. 실패 시 예외 대신 빈 리스트를
    반환하며, 호출자는 manual_search_url()을 함께 안내해야 한다.
    """

    def __init__(self, session: requests.Session | None = None, timeout: float = 8.0):
        self.session = session or requests.Session()
        self.timeout = timeout

    def manual_search_url(self) -> str:
        return NTS_TAXLAW_HOME_URL

    def search_rulings(self, query: str) -> list[RulingCase]:
        try:
            return self._search_rulings_unsafe(query)
        except Exception:  # noqa: BLE001 - 보조 데이터 소스이므로 실패해도 앱은 계속 동작해야 함
            logger.warning("조세심판원 결정례 검색 실패(query=%s) — 수동 확인 링크로 대체합니다.", query, exc_info=True)
            return []

    def _search_rulings_unsafe(self, query: str) -> list[RulingCase]:
        # NOTE: taxlaw.nts.go.kr는 공식 Open API가 없어 검증되지 않은 best-effort 구현이다.
        # 실제 연동 시 사이트의 검색 API/HTML 구조를 확인해 이 메서드를 교체해야 한다.
        raise NotImplementedError(
            "국세법령정보시스템 자동 검색은 아직 연동되지 않았습니다. "
            f"'{query}' 관련 조세심판원 결정례는 {NTS_TAXLAW_HOME_URL} 에서 직접 확인하세요."
        )
