from app.validators.evidence_validator import validate_evidence_pack
from app.validators.output_validator import validate_report


def test_validate_evidence_pack_ok():
    pack = {
        'as_of': '2026-05-11 05:45 PT',
        'session': 'premarket',
        'market_regime': {},
        'candidates': {'A': [], 'B': [], 'C': [], 'D': []},
        'analysis_constraints': {'must_include_as_of_time': True},
    }
    assert validate_evidence_pack(pack) == []


def test_validate_evidence_pack_missing():
    assert 'missing_required_field:as_of' in validate_evidence_pack({})


def test_validate_report_banned_phrase():
    text = '截至时间：x\n今日市场结论：x\n交易风格建议：x\n一、市场环境\n风险：x\n稳赚'
    errors = validate_report(text)
    assert any(e.startswith('banned_phrase') for e in errors)
