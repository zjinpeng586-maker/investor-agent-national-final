from __future__ import annotations

from typing import Any


DOCUMENT_TERMS = {
    '年报', '报告中', '披露', '原文', '提到', '描述', '管理层', '研发投入',
    '海外业务', '业务因素', '怎么解释', '如何解释', '原因', '为什么',
}
EXPLANATION_TERMS = {'为什么', '原因', '怎么解释', '如何解释', '哪些业务因素'}
STRUCTURED_INTENTS = {'finance_query', 'trend_analysis', 'company_compare'}


def plan_query(question: str, parsed_context: dict[str, Any]) -> dict[str, str]:
    """Select a deterministic, auditable data route without model dependency."""
    intent = parsed_context.get('intent') or 'unknown'
    has_metric = bool(parsed_context.get('metrics'))
    asks_document = any(term in question for term in DOCUMENT_TERMS)
    asks_explanation = any(term in question for term in EXPLANATION_TERMS)

    if asks_explanation and (has_metric or intent in STRUCTURED_INTENTS or len(parsed_context.get('companies') or []) > 1):
        return {'route': 'hybrid', 'reason': '问题同时需要结构化财务事实与报告中的原因解释。'}
    if asks_document or (intent == 'risk_warning' and ('年报' in question or '报告' in question)):
        return {'route': 'rag', 'reason': '问题要求从已接入报告中查找定性描述或原文依据。'}
    return {'route': 'sql', 'reason': '问题可由结构化财务数据或既有可信分析链路回答。'}
