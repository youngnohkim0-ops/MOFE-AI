import pytest

from app.parser import ArticleQueryParseError, parse_article_query


def test_law_name_and_article():
    q = parse_article_query("소득세법 제95조")
    assert q.law_name == "소득세법"
    assert q.article_no == 95
    assert q.article_sub_no is None
    assert q.clause_no is None
    assert q.item_no is None


def test_law_name_article_clause_with_space():
    q = parse_article_query("소득세법 제95조 제2항")
    assert q.law_name == "소득세법"
    assert q.article_no == 95
    assert q.clause_no == 2


def test_no_space_between_article_and_clause():
    q = parse_article_query("소득세법 제95조제2항")
    assert q.law_name == "소득세법"
    assert q.article_no == 95
    assert q.clause_no == 2


def test_no_space_between_law_name_and_article():
    q = parse_article_query("소득세법제95조")
    assert q.law_name == "소득세법"
    assert q.article_no == 95


def test_sub_article_gaji_ho():
    q = parse_article_query("소득세법 제95조의2")
    assert q.law_name == "소득세법"
    assert q.article_no == 95
    assert q.article_sub_no == 2


def test_sub_article_with_clause_and_item():
    q = parse_article_query("소득세법 제95조의2 제3항 제1호")
    assert q.article_sub_no == 2
    assert q.clause_no == 3
    assert q.item_no == 1


def test_missing_law_name_uses_default():
    q = parse_article_query("제95조 제2항", default_law_name="소득세법")
    assert q.law_name == "소득세법"
    assert q.article_no == 95
    assert q.clause_no == 2


def test_missing_law_name_no_default():
    q = parse_article_query("제95조")
    assert q.law_name is None
    assert q.article_no == 95


def test_enforcement_decree_name():
    q = parse_article_query("소득세법 시행령 제118조")
    assert q.law_name == "소득세법 시행령"
    assert q.article_no == 118


def test_article_without_je_prefix():
    q = parse_article_query("소득세법 95조")
    assert q.law_name == "소득세법"
    assert q.article_no == 95


def test_empty_input_raises():
    with pytest.raises(ArticleQueryParseError):
        parse_article_query("   ")


def test_no_article_number_raises():
    with pytest.raises(ArticleQueryParseError):
        parse_article_query("소득세법")


def test_label_formatting():
    q = parse_article_query("소득세법 제95조의2 제3항 제1호")
    assert q.label() == "소득세법 제95조의2 제3항 제1호"
