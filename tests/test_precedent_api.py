from unittest.mock import MagicMock

from app.precedent_api import PrecedentClient, TaxTribunalClient

PREC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PrecSearch>
  <prec id="1">
    <판례일련번호>111</판례일련번호>
    <사건명>양도소득세부과처분취소</사건명>
    <사건번호>2020두12345</사건번호>
    <법원명>대법원</법원명>
    <선고일자>20210315</선고일자>
    <판결요지>양도소득금액 산정 시 필요경비 인정 범위에 관한 판단</판결요지>
    <판례상세링크>/DRF/lawService.do?target=prec&amp;ID=111</판례상세링크>
  </prec>
</PrecSearch>
"""

EXPC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Expc>
  <expc id="1">
    <법령해석례일련번호>222</법령해석례일련번호>
    <안건명>소득세법 제95조 관련 필요경비 해당 여부</안건명>
    <회신기관명>기획재정부</회신기관명>
    <회신일자>20220110</회신일자>
    <해석례상세링크>/DRF/lawService.do?target=expc&amp;ID=222</해석례상세링크>
  </expc>
</Expc>
"""


def _make_client(cls, xml: str, oc: str | None = "test-oc"):
    kwargs = {"oc": oc} if oc is not None else {}
    client = cls(**kwargs)
    fake_session = MagicMock()
    resp = MagicMock()
    resp.content = xml.encode("utf-8")
    resp.raise_for_status = MagicMock()
    fake_session.get.return_value = resp
    client.session = fake_session
    return client


def test_search_precedents():
    client = _make_client(PrecedentClient, PREC_XML)
    results = client.search_precedents("소득세법 제95조")
    assert len(results) == 1
    assert results[0].court == "대법원"
    assert results[0].case_no == "2020두12345"
    assert "필요경비" in results[0].summary


def test_search_statutory_interpretations():
    client = _make_client(PrecedentClient, EXPC_XML)
    results = client.search_statutory_interpretations("소득세법 제95조")
    assert len(results) == 1
    assert results[0].agency == "기획재정부"
    assert results[0].verified is True


def test_tax_tribunal_client_degrades_gracefully():
    client = TaxTribunalClient()
    results = client.search_rulings("소득세법 제95조")
    assert results == []
    assert client.manual_search_url().startswith("https://")
