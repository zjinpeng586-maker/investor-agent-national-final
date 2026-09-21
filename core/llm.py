from __future__ import annotations

import json
import re
from typing import Any

import requests

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
    payload = {
        'model': cfg['model'],
        'messages': messages,
        'temperature': temperature,
    }
    headers = {
        'Authorization': f"Bearer {cfg['api_key']}",
        'Content-Type': 'application/json',
    }
    resp = requests.post(f"{cfg['base_url']}/chat/completions", headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data['choices'][0]['message']['content'].strip()


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
    cfg = _normalize_config(config_or_key)
    provider = cfg.get('provider', '云端模型')
    prompt = f'''
你是一个谨慎、专业、面向投资者的上市公司财务分析助手。请基于“系统证据”和“稳健分析结果”回答用户问题。
要求：
1. 只能使用系统证据中的数据，不得编造任何数字或事实。
2. 不预测短期股价，不给出买入、卖出或保证收益类指令。
3. 在保留数据结论的基础上，增强解释性、层次感和投资者视角。
4. 适当从财务表现、风险关注、投资者画像匹配三个角度组织语言。
5. 结尾保留“仅供研究参考，不构成投资建议”。
6. 当前投资者画像：{investor_profile}。
7. 当前云端模型服务：{provider}。

用户问题：{question}

系统证据：
{evidence}

稳健分析结果：
{draft_answer}
'''
    return _post_chat(config_or_key, [
        {'role': 'system', 'content': '你是严谨的上市公司财务分析助手，所有结论必须以系统证据为边界。'},
        {'role': 'user', 'content': prompt},
    ], temperature=0.25)


def enhance_report_with_llm(config_or_key: dict[str, str] | str, report_text: str, company: str, investor_profile: str = '平衡型') -> str:
    prompt = f'''
你是财报智问 V2.0 的报告润色模块。请在不改变原始数据、不新增未经证实数字的前提下，对以下报告做专业化润色。
要求：
1. 保留原报告的标题层级和全部关键数据。
2. 语言更像正式投资研究报告，适合正式分析报告展示。
3. 加强投资者画像匹配、风险关注点和数据来源可信性的表述。
4. 不给出买卖建议，不承诺收益。
5. 结尾保留“仅供研究参考，不构成投资建议”。

企业：{company}
投资者画像：{investor_profile}

原始报告：
{report_text}
'''
    return _post_chat(config_or_key, [
        {'role': 'system', 'content': '你是专业投资分析报告编辑，必须以原始报告事实为边界。'},
        {'role': 'user', 'content': prompt},
    ], temperature=0.25, timeout=60)


# Backward-compatible aliases for modules that may still import old names.
def parse_question_with_deepseek(api_key: str, question: str, companies: list[str]) -> dict[str, Any]:
    return parse_question_with_llm(build_llm_config('DeepSeek', api_key), question, companies)


def polish_answer_with_deepseek(api_key: str, question: str, evidence: str, draft_answer: str, investor_profile: str = '平衡型') -> str:
    return polish_answer_with_llm(build_llm_config('DeepSeek', api_key), question, evidence, draft_answer, investor_profile)


def rewrite_with_deepseek(api_key: str, user_question: str, system_facts: str, compare_facts: str | None = None) -> str:
    evidence = system_facts + ('\n' + compare_facts if compare_facts else '')
    return polish_answer_with_deepseek(api_key, user_question, evidence, system_facts)
