"""조세법 조문 검색·해설 앱.

법률/시행령/시행규칙 조문 번호를 입력하면 법제처 국가법령정보센터 Open API로
관련 조문(법률·시행령·시행규칙)과 판례·법령해석례를 조회하고, 조세심판원
결정례는 국세청 국세법령정보시스템에서 best-effort로 조회(실패 시 수동 확인
링크 제공)한 뒤 OpenAI로 쉬운 해설을 생성한다.
"""
from __future__ import annotations

import streamlit as st
from openai import OpenAI

from app.config import get_settings, missing_credentials
from app.explainer import explain_article
from app.law_api import (
    ArticleNotFoundError,
    LawApiError,
    LawOpenApiClient,
    find_related_articles,
    find_subordinate_laws,
    resolve_article,
)
from app.models import ArticleText, Precedent, RulingCase
from app.parser import ArticleQueryParseError, parse_article_query
from app.precedent_api import PrecedentClient, TaxTribunalClient

st.set_page_config(page_title="조세법 조문 해설 도우미", page_icon="⚖️", layout="wide")

DEFAULT_LAW_NAME = "소득세법"


def _safe(label: str, fn, *args, default=None, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - UI 레벨에서 부분 실패를 흡수
        st.warning(f"{label} 조회 중 문제가 발생했습니다: {exc}")
        return default


@st.cache_resource
def _law_client(oc: str) -> LawOpenApiClient:
    return LawOpenApiClient(oc=oc)


@st.cache_resource
def _precedent_client(oc: str) -> PrecedentClient:
    return PrecedentClient(oc=oc)


@st.cache_resource
def _tax_tribunal_client() -> TaxTribunalClient:
    return TaxTribunalClient()


@st.cache_resource
def _openai_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def render_article(article: ArticleText) -> None:
    heading = f"{article.law_name} 제{article.article_no}조"
    if article.article_sub_no and article.article_sub_no != "0":
        heading += f"의{article.article_sub_no}"
    if article.title:
        heading += f"({article.title})"
    st.subheader(heading)
    if article.clauses:
        for clause in article.clauses:
            prefix = f"**제{clause.clause_no}항** " if clause.clause_no else ""
            st.markdown(f"{prefix}{clause.content}")
            for item in clause.items:
                st.markdown(f"　{item}")
    else:
        st.markdown(article.full_text)
    if article.source_url:
        st.caption(f"출처: [국가법령정보센터]({article.source_url})")


def render_precedent(prec: Precedent) -> None:
    st.markdown(f"**[{prec.court}] {prec.case_name}** ({prec.case_no}, {prec.decision_date})")
    st.markdown(prec.summary)
    if prec.url:
        st.caption(f"[법제처 원문 보기](https://www.law.go.kr{prec.url})")
    st.divider()


def render_ruling(ruling: RulingCase) -> None:
    badge = "" if ruling.verified else " ⚠️미검증/직접확인"
    st.markdown(f"**[{ruling.agency}] {ruling.title}**{badge} ({ruling.decision_date})")
    st.markdown(ruling.summary)
    if ruling.url:
        st.caption(f"[법제처 원문 보기](https://www.law.go.kr{ruling.url})")
    st.divider()


def main() -> None:
    st.title("⚖️ 조세법 조문 검색·해설 도우미")
    st.caption(
        "법제처 국가법령정보센터 및 국세청 국세법령정보시스템 자료를 참고해 조문·관련법령·"
        "판례·결정례를 정리하고 쉬운 해설을 생성합니다. 본 앱의 설명은 참고용이며 법률 자문을 "
        "대체하지 않습니다."
    )

    settings = get_settings()
    missing = missing_credentials(settings)
    with st.sidebar:
        st.header("설정")
        if missing:
            st.error("다음 API 키가 설정되지 않았습니다: " + ", ".join(missing))
            st.caption(".env 파일에 LAW_API_OC(법제처 Open API 인증키), OPENAI_API_KEY를 설정하세요.")
        else:
            st.success("API 키 설정 확인됨")
        default_law_name = st.text_input("기본 법령명(조문 입력 시 법령명을 생략하면 사용)", value=DEFAULT_LAW_NAME)

    query_text = st.text_input(
        "조문을 입력하세요",
        placeholder="예: 소득세법 제95조, 소득세법 제95조 제2항, 제95조의2",
    )
    search_clicked = st.button("검색", type="primary", disabled=bool(missing))

    if not search_clicked:
        return

    try:
        query = parse_article_query(query_text, default_law_name=default_law_name)
    except ArticleQueryParseError as exc:
        st.error(str(exc))
        return

    law_client = _law_client(settings.law_api_oc)
    precedent_client = _precedent_client(settings.law_api_oc)
    tax_tribunal_client = _tax_tribunal_client()

    with st.spinner(f"'{query.label()}' 조문을 조회 중입니다..."):
        try:
            law, article = resolve_article(law_client, query)
        except (LawApiError, ArticleNotFoundError) as exc:
            st.error(str(exc))
            return

        related_articles: list[ArticleText] = []
        if law.law_type == "법률":
            subordinate_laws = _safe("시행령/시행규칙 검색", find_subordinate_laws, law_client, law.name, default=[])
            if subordinate_laws:
                related_articles = _safe(
                    "관련 시행령/시행규칙 조문 검색",
                    find_related_articles,
                    law_client,
                    subordinate_laws,
                    query.article_no,
                    query.article_sub_no,
                    default=[],
                )

        search_query = f"{law.name} 제{query.article_no}조"
        precedents = _safe("판례", precedent_client.search_precedents, search_query, default=[])
        interpretations = _safe(
            "법령해석례", precedent_client.search_statutory_interpretations, search_query, default=[]
        )
        tribunal_rulings = _safe("조세심판원 결정례", tax_tribunal_client.search_rulings, search_query, default=[])
        rulings = [*interpretations, *tribunal_rulings]

    with st.spinner("쉬운 해설을 생성하는 중입니다..."):
        openai_client = _openai_client(settings.openai_api_key)
        explanation = _safe(
            "AI 해설 생성",
            explain_article,
            openai_client,
            settings.openai_model,
            query.label(),
            article,
            related_articles,
            precedents,
            rulings,
            default=None,
        )

    tab_explain, tab_article, tab_related, tab_cases = st.tabs(
        ["✨ 쉬운 설명", "📖 조문 원문", "🔗 관련 법령", "⚖️ 판례·해석례·결정례"]
    )

    with tab_explain:
        if explanation:
            st.markdown(explanation)
        else:
            st.info("해설을 생성하지 못했습니다. 다른 탭에서 원문 자료를 확인해주세요.")

    with tab_article:
        render_article(article)

    with tab_related:
        if related_articles:
            for related in related_articles:
                render_article(related)
        else:
            st.info("동일 조번호로 추정 매칭되는 시행령/시행규칙 조문을 찾지 못했습니다.")
        st.caption("※ 관련 법령은 동일 조번호 기준 추정 매칭이며, 실제 위임관계와 다를 수 있으니 원문을 확인하세요.")

    with tab_cases:
        if precedents:
            st.markdown("#### 대법원 판례")
            for prec in precedents:
                render_precedent(prec)
        if interpretations:
            st.markdown("#### 법령해석례")
            for ruling in interpretations:
                render_ruling(ruling)
        if tribunal_rulings:
            st.markdown("#### 조세심판원 결정례")
            for ruling in tribunal_rulings:
                render_ruling(ruling)
        if not (precedents or rulings):
            st.info("자동 검색된 판례·해석례·결정례가 없습니다.")
        st.caption(
            f"조세심판원 결정례는 자동 연동이 제한적입니다. "
            f"[국세법령정보시스템]({tax_tribunal_client.manual_search_url()})에서 "
            f"'{search_query}'로 직접 검색해 보완하세요."
        )


if __name__ == "__main__":
    main()
