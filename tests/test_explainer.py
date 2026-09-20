from unittest.mock import MagicMock

from app.explainer import build_context_block, build_messages, explain_article
from app.models import ArticleText, ClauseText, Precedent, RulingCase

ARTICLE = ArticleText(
    law_name="소득세법",
    law_type="법률",
    mst="123456",
    article_no="95",
    article_sub_no=None,
    title="양도소득금액",
    full_text="제95조(양도소득금액) ① 양도소득금액은 다음과 같다.",
    clauses=[ClauseText(clause_no="1", content="① 양도소득금액은 다음과 같다.", items=["1. 토지"])],
)

RELATED_ARTICLE = ArticleText(
    law_name="소득세법 시행령",
    law_type="시행령",
    mst="654321",
    article_no="95",
    article_sub_no=None,
    title=None,
    full_text="제95조(시행령 위임사항)",
    clauses=[],
)

PRECEDENT = Precedent(
    case_id="111", case_no="2020두12345", court="대법원", decision_date="20210315",
    case_name="양도소득세부과처분취소", summary="필요경비 인정 범위에 관한 판단",
)

RULING = RulingCase(
    ruling_id="222", title="필요경비 해당 여부", agency="기획재정부",
    decision_date="20220110", summary="필요경비 해당 여부 해석", verified=True,
)


def test_build_context_block_includes_all_sections():
    block = build_context_block(ARTICLE, [RELATED_ARTICLE], [PRECEDENT], [RULING])
    assert "조문 원문" in block
    assert "관련 법령" in block
    assert "관련 판례" in block
    assert "결정례" in block
    assert "대법원" in block
    assert "기획재정부" in block


def test_build_context_block_without_related_data():
    block = build_context_block(ARTICLE, [], [], [])
    assert "관련 법령" not in block
    assert "관련 판례" not in block


def test_build_messages_has_system_and_user():
    messages = build_messages("소득세법 제95조", ARTICLE, [], [], [])
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "소득세법 제95조" in messages[1]["content"]


def test_explain_article_calls_openai_and_returns_content():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="## 핵심 요약\n테스트 설명"))]
    )
    result = explain_article(fake_client, "gpt-4o-mini", "소득세법 제95조", ARTICLE, [], [], [])
    assert "핵심 요약" in result
    fake_client.chat.completions.create.assert_called_once()
    _, kwargs = fake_client.chat.completions.create.call_args
    assert kwargs["model"] == "gpt-4o-mini"
