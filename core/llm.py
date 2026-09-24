from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

import requests
from core.storage import is_public_deployment

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    'DeepSeek': {
        'base_url': 'https://api.deepseek.com/v1',
        'models': ['deepseek-chat', 'deepseek-reasoner'],
        'default_model': 'deepseek-chat',
    },
    '豆包': {
        'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
        'models': ['doubao-seed-1-6', 'doubao-seed-1-6-thinking', '自定义模型/Endpoint ID'],
        'default_model': 'doubao-seed-1-6',
    },
    '通义千问': {
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'models': ['qwen-plus', 'qwen-max', 'qwen-turbo', '自定义模型名称'],
        'default_model': 'qwen-plus',
    },
}

# Cloud models choose relevant educational notes; they never rewrite financial
# facts. The renderer owns every numeric value, company, period and conclusion.
EXPLANATION_NOTES = {
    'revenue': '营业收入反映经营规模，收入增长本身不等于盈利质量改善。',
    'profit': '净利润反映会计盈利，应结合经营现金流观察盈利的现金转化。',
    'cashflow': '经营现金流反映经营活动的现金收支，需要结合回款及营运资本分析。',
    'roe': '净资产收益率需结合盈利水平、资产效率和杠杆一起理解。',
    'debt': '资产负债率应结合行业特点、债务期限和偿债现金来源理解。',
    'period': '跨期比较需要保持报告期间、合并范围和指标口径一致。',
    'evidence': '企业经营原因应以对应报告原文为依据，指标变化本身不能证明因果关系。',
}
ENHANCEMENT_MARKER = '\n\n解读提示：\n'


def validate_enhancement(draft: str, candidate: str) -> dict[str, Any]:
    if not isinstance(candidate, str) or not candidate:
        return {'valid': False, 'reason': '云端输出为空。'}
    if candidate == draft:
        return {'valid': True, 'reason': ''}
    prefix = draft + ENHANCEMENT_MARKER
    if not candidate.startswith(prefix):
        return {'valid': False, 'reason': '云端输出试图改写原始事实；已拒绝并保留结构化结果。'}
    notes = candidate[len(prefix):].splitlines()
    allowed = set(EXPLANATION_NOTES.values())
    if not notes or len(notes) > 3 or any(note not in allowed for note in notes):
        return {'valid': False, 'reason': '云端输出包含未通过程序校验的事实或解释。'}
    return {'valid': True, 'reason': ''}


def _select_explanations(config_or_key: dict[str, str] | str, question: str, draft: str) -> str:
    prompt = (
        '你是阅读辅助模块。原始财务事实已经由程序渲染，不允许重写、计算或新增数字。'
        '从可用解释中选择与用户问题相关的至多三个编号，只返回JSON：{"explanation_ids":[]}。'
        '无法确定时返回空数组。可用解释：' + json.dumps(EXPLANATION_NOTES, ensure_ascii=False)
        + '\n用户问题：' + question + '\n已经确认的分析结果：' + draft
    )
    raw = _post_chat(config_or_key, [{'role': 'user', 'content': prompt}], temperature=0)
    data = _extract_json(raw)
    identifiers = data.get('explanation_ids')
    if set(data) != {'explanation_ids'} or not isinstance(identifiers, list) or len(identifiers) > 3:
        raise ValueError('云端解释未返回允许的结构，原始事实保持不变。')
    if any(not isinstance(item, str) or item not in EXPLANATION_NOTES for item in identifiers):
        raise ValueError('云端解释包含不受支持的内容。')
    notes = [EXPLANATION_NOTES[item] for item in dict.fromkeys(identifiers)]
    candidate = draft + (ENHANCEMENT_MARKER + '\n'.join(notes) if notes else '')
    validation = validate_enhancement(draft, candidate)
    if not validation['valid']:
        raise ValueError(validation['reason'])
    return candidate


def build_llm_config(provider: str, api_key: str, model: str | None = None, base_url: str | None = None) -> dict[str, str]:
    preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS['DeepSeek'])
    return {
        'provider': provider,
        'api_key': (api_key or '').strip(),
        'model': (model or preset.get('default_model') or '').strip(),
        'base_url': (base_url or preset.get('base_url') or '').strip().rstrip('/'),
    }


def _normalize_config(config_or_key: dict[str, str] | str | None) -> dict[str, str]:
    if isinstance(config_or_key, dict):
        provider = config_or_key.get('provider') or 'DeepSeek'
        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS['DeepSeek'])
        return {
            'provider': provider,
            'api_key': (config_or_key.get('api_key') or '').strip(),
            'model': (config_or_key.get('model') or preset.get('default_model') or '').strip(),
            'base_url': (config_or_key.get('base_url') or preset.get('base_url') or '').strip().rstrip('/'),
        }
    return build_llm_config('DeepSeek', config_or_key or '')


def llm_enabled(config_or_key: dict[str, str] | str | None) -> bool:
    cfg = _normalize_config(config_or_key)
    return bool(cfg.get('api_key') and cfg.get('base_url') and cfg.get('model'))


def _post_chat(config_or_key: dict[str, str] | str, messages: list[dict[str, str]], temperature: float = 0.2, timeout: int = 45) -> str:
    cfg = _normalize_config(config_or_key)
    if not llm_enabled(cfg):
        raise ValueError('云端模型配置不完整')
    validate_api_endpoint(cfg)
    payload = {
        'model': cfg['model'],
        'messages': messages,
        'temperature': temperature,
    }
    headers = {
        'Authorization': f"Bearer {cfg['api_key']}",
        'Content-Type': 'application/json',
    }
    resp = requests.post(f"{cfg['base_url']}/chat/completions", headers=headers, json=payload,
                         timeout=(5, min(timeout, 60)), allow_redirects=False)
    if 300 <= resp.status_code < 400:
        raise ValueError('模型API不允许重定向；请使用已验证的服务地址。')
    resp.raise_for_status()
    data = resp.json()
    return data['choices'][0]['message']['content'].strip()


def validate_api_endpoint(config_or_key: dict[str, str] | str) -> None:
    cfg = _normalize_config(config_or_key)
    value = cfg['base_url']
    url = urlsplit(value)
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('模型API必须是无凭据、无查询参数的HTTP(S)服务地址。')
    if any(char.isspace() for char in value) or '\\' in value:
        raise ValueError('模型API地址包含非法字符。')
    if is_public_deployment():
        preset = PROVIDER_PRESETS.get(cfg['provider'])
        if not preset or value.rstrip('/') != preset['base_url']:
            raise ValueError('公共部署仅允许所选服务商的官方HTTPS端点，不允许自定义或内网模型地址。')


def _extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'\{.*\}', text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
    return {}


def parse_question_with_llm(config_or_key: dict[str, str] | str, question: str, companies: list[str]) -> dict[str, Any]:
    company_text = '、'.join(companies)
    prompt = f'''
你是一个上市公司投资分析系统的问题解析模块。请只输出JSON，不要解释。
可选企业：{company_text}

请从用户问题中识别以下字段：
- intent：finance_query、trend_analysis、risk_warning、company_compare、investment_summary、metric_explain、report_generate、out_of_scope、refusal
- companies：问题涉及的企业名称列表，只能使用可选企业中的名称；如果没有明确企业则为空列表
- years：年份列表，例如[2024]；没有则为空列表
- metrics：指标列表，可选 revenue、net_profit、operating_cashflow、roe、debt_ratio、gross_margin、eps；没有则为空列表
- investor_profile：稳健型、平衡型、成长型、未知
- reason：简短说明判断理由

如果用户询问天气、闲聊、生活服务等非上市公司财务分析问题，intent用out_of_scope。
如果用户要求预测短期股价、直接买卖建议、保证收益，intent用refusal。

用户问题：{question}
'''
    text = _post_chat(config_or_key, [{'role': 'user', 'content': prompt}], temperature=0)
    data = _extract_json(text)
    return data if isinstance(data, dict) else {}


def polish_answer_with_llm(config_or_key: dict[str, str] | str, question: str, evidence: str, draft_answer: str, investor_profile: str = '平衡型') -> str:
    return _select_explanations(config_or_key, question, draft_answer)




def enhance_report_with_llm(config_or_key: dict[str, str] | str, report_text: str, company: str, investor_profile: str = '平衡型') -> str:
    return _select_explanations(config_or_key, f'{company}财务报告阅读辅助，{investor_profile}', report_text)




# Backward-compatible aliases for modules that may still import old names.
def parse_question_with_deepseek(api_key: str, question: str, companies: list[str]) -> dict[str, Any]:
    return parse_question_with_llm(build_llm_config('DeepSeek', api_key), question, companies)


def polish_answer_with_deepseek(api_key: str, question: str, evidence: str, draft_answer: str, investor_profile: str = '平衡型') -> str:
    return polish_answer_with_llm(build_llm_config('DeepSeek', api_key), question, evidence, draft_answer, investor_profile)


def rewrite_with_deepseek(api_key: str, user_question: str, system_facts: str, compare_facts: str | None = None) -> str:
    evidence = system_facts + ('\n' + compare_facts if compare_facts else '')
    return polish_answer_with_deepseek(api_key, user_question, evidence, system_facts)
