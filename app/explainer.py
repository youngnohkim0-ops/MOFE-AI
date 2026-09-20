"""조문·관련법령·판례·결정례를 바탕으로 LLM이 쉬운 설명을 생성."""
from __future__ import annotations

from app.models import ArticleText, Precedent, RulingCase

SYSTEM_PROMPT = """당신은 한국 조세법 전문 해설가입니다. 세무 지식이 없는 일반 납세자도
이해할 수 있도록 쉬운 말로 설명하되, 법률적으로 부정확한 내용을 넣지 마세요.

반드시 아래 형식(마크다운)을 지키세요:

## 핵심 요약
(조문이 무엇을 규정하는지 2~3문장)

## 입법 취지
(왜 이런 규정을 두었는지)

## 실무 적용 포인트
(실제로 어떤 상황에 적용되는지, 주의할 점)

## 관련 판례·결정례가 주는 시사점
(제공된 판례/해석례/결정례가 있으면 조문 해석에 어떤 의미를 주는지 요약. 없으면
"제공된 판례·결정례 자료가 없습니다"라고 명시)

## 확인이 필요한 사항
(이 설명이 제공된 원문 발췌를 근거로 하므로, 최신 개정 여부·전문가 확인이 필요함을
안내)

제공된 원문에 없는 내용을 추측해서 단정적으로 서술하지 마세요."""


def _format_article(article: ArticleText) -> str:
    lines = [f"[{article.law_type}] {article.law_name} 제{article.article_no}조"]
    if article.article_sub_no and article.article_sub_no != "0":
        lines[0] += f"의{article.article_sub_no}"
    if article.title:
        lines[0] += f"({article.title})"
    lines.append(article.full_text)
    return "\n".join(lines)


def _format_precedent(prec: Precedent) -> str:
    return f"- [{prec.court} {prec.decision_date} {prec.case_no}] {prec.case_name}: {prec.summary}"


def _format_ruling(ruling: RulingCase) -> str:
    tag = "" if ruling.verified else "(비공식/확인필요) "
    return f"- {tag}[{ruling.agency} {ruling.decision_date}] {ruling.title}: {ruling.summary}"


def build_context_block(
    article: ArticleText,
    related_articles: list[ArticleText],
    precedents: list[Precedent],
    rulings: list[RulingCase],
) -> str:
    sections = ["### 조문 원문", _format_article(article)]

    if related_articles:
        sections.append("### 관련 법령(시행령·시행규칙 등, 동일 조번호 추정 매칭)")
        sections.extend(_format_article(a) for a in related_articles)

    if precedents:
        sections.append("### 관련 판례")
        sections.extend(_format_precedent(p) for p in precedents)

    if rulings:
        sections.append("### 관련 법령해석례·조세심판원 결정례")
        sections.extend(_format_ruling(r) for r in rulings)

    return "\n\n".join(sections)


def build_messages(
    query_label: str,
    article: ArticleText,
    related_articles: list[ArticleText],
    precedents: list[Precedent],
    rulings: list[RulingCase],
) -> list[dict]:
    context = build_context_block(article, related_articles, precedents, rulings)
    user_prompt = f"다음은 '{query_label}'에 대한 자료입니다.\n\n{context}\n\n위 자료를 바탕으로 해설해주세요."
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def explain_article(
    openai_client,
    model: str,
    query_label: str,
    article: ArticleText,
    related_articles: list[ArticleText],
    precedents: list[Precedent],
    rulings: list[RulingCase],
) -> str:
    messages = build_messages(query_label, article, related_articles, precedents, rulings)
    response = openai_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
    )
    return response.choices[0].message.content
