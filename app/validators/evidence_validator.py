from __future__ import annotations

from typing import Any, Dict, List


def validate_evidence_pack(pack: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ['as_of', 'session', 'market_regime', 'candidates', 'analysis_constraints']:
        if key not in pack:
            errors.append(f'missing_required_field:{key}')
    if 'candidates' in pack:
        c = pack['candidates']
        if not isinstance(c, dict):
            errors.append('candidates_must_be_object')
        else:
            for tier in ['A', 'B', 'C', 'D']:
                if tier not in c:
                    errors.append(f'missing_candidate_tier:{tier}')
    if pack.get('analysis_constraints', {}).get('must_include_as_of_time') is not True:
        errors.append('analysis_constraints_missing_as_of_requirement')
    return errors
