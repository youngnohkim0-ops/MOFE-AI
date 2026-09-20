"""법제처 Open API 키를 로컬에서 빠르게 검증하는 CLI.

이 원격 개발 환경은 www.law.go.kr로 나가는 네트워크가 막혀 있어 이 스크립트를
여기서 실행할 수 없다. 사용자가 본인 PC에서 .env에 LAW_API_OC를 설정한 뒤
실행해 실제 응답 스키마(태그명)가 app/law_api.py의 가정과 일치하는지 확인하는
용도다.

사용법:
    python -m scripts.verify_law_api "소득세법 제95조"
    python -m scripts.verify_law_api "소득세법 제95조 제2항" --raw   # 원본 XML도 출력
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET

from app.config import get_settings
from app.law_api import LawApiError, LawOpenApiClient, find_related_articles, find_subordinate_laws, resolve_article
from app.parser import ArticleQueryParseError, parse_article_query
from app.precedent_api import PrecedentClient, TaxTribunalClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", help='예: "소득세법 제95조 제2항"')
    parser.add_argument("--default-law", default="소득세법", help="법령명 생략 시 기본값")
    parser.add_argument("--raw", action="store_true", help="법령 본문 원본 XML도 출력")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.law_api_oc:
        print("[오류] LAW_API_OC가 설정되지 않았습니다. .env를 확인하세요.", file=sys.stderr)
        return 1

    try:
        query = parse_article_query(args.query, default_law_name=args.default_law)
    except ArticleQueryParseError as exc:
        print(f"[오류] 입력 파싱 실패: {exc}", file=sys.stderr)
        return 1

    print(f"조회 대상: {query.label()}")
    law_client = LawOpenApiClient(oc=settings.law_api_oc)

    try:
        law, article = resolve_article(law_client, query)
    except LawApiError as exc:
        print(f"[오류] 법령 조회 실패: {exc}", file=sys.stderr)
        print(
            "-> XML 태그명이 다를 수 있습니다. --raw 옵션으로 원본 응답을 확인해 "
            "app/law_api.py의 _text(...) 후보 태그를 조정하세요.",
            file=sys.stderr,
        )
        return 1

    print(f"\n[법령] {law.name} ({law.law_type}, MST={law.mst}, 시행일 {law.enforcement_date})")
    print(f"[조문] 제{article.article_no}조 {article.title or ''}")
    print(article.full_text)

    if args.raw:
        print("\n--- 원본 조문 XML(일부) ---")
        raw_root = law_client.get_law_xml(law.mst)
        print(ET.tostring(raw_root, encoding="unicode")[:3000])

    print("\n[관련 법령] 시행령/시행규칙 추정 매칭 조회 중...")
    subs = find_subordinate_laws(law_client, law.name) if law.law_type == "법률" else []
    related = find_related_articles(law_client, subs, query.article_no, query.article_sub_no) if subs else []
    if related:
        for r in related:
            print(f" - {r.law_name} 제{r.article_no}조: {r.full_text[:60]}...")
    else:
        print(" (매칭된 관련 조문 없음)")

    print("\n[판례/해석례] 조회 중...")
    prec_client = PrecedentClient(oc=settings.law_api_oc)
    search_q = f"{law.name} 제{query.article_no}조"
    try:
        precedents = prec_client.search_precedents(search_q)
        interpretations = prec_client.search_statutory_interpretations(search_q)
        print(f" - 판례 {len(precedents)}건, 법령해석례 {len(interpretations)}건")
    except Exception as exc:  # noqa: BLE001
        print(f" [경고] 판례/해석례 조회 실패: {exc}")

    print("\n[조세심판원 결정례] best-effort 조회 중 (미구현이면 빈 목록 예상)...")
    tribunal_client = TaxTribunalClient()
    rulings = tribunal_client.search_rulings(search_q)
    print(f" - {len(rulings)}건 (직접 확인: {tribunal_client.manual_search_url()})")

    print("\n검증 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
