from core.conversation import confirm_clarification, new_conversation_context, resolve_turn
from core.analysis import rows_to_df
from core.db import fetch_companies, fetch_company_metrics, init_db, upsert_company, upsert_metric
from core.qa_engine import answer_question, parse_question
from core.storage import workspace_context


BYD = '比亚迪股份有限公司'
CATL = '宁德时代新能源科技股份有限公司'
CHANGAN = '长安汽车股份有限公司'
SAIC = '上海汽车集团股份有限公司'
COMPANIES = [BYD, CATL, CHANGAN, SAIC]


def _turn(context, question, page_company=BYD, page_compare=CATL):
    result = resolve_turn(
        context,
        question,
        COMPANIES,
        page_company=page_company,
        page_compare=page_compare,
    )
    assert result['status'] == 'ready'
    return result


def test_year_company_and_metric_follow_ups_inherit_missing_conditions():
    first = _turn(new_conversation_context(), '比亚迪2024年营业收入是多少？')
    second = _turn(first['context'], '那2023年呢？')
    assert second['resolved']['companies'] == [BYD]
    assert second['resolved']['years'] == [2023]
    assert second['resolved']['metrics'] == ['revenue']

    first = _turn(new_conversation_context(), '比亚迪2024年净利润是多少？')
    second = _turn(first['context'], '那宁德时代呢？')
    assert second['resolved']['companies'] == [CATL]
    assert second['resolved']['years'] == [2024]
    assert second['resolved']['metrics'] == ['net_profit']

    first = _turn(new_conversation_context(), '比亚迪2024年营业收入是多少？')
    second = _turn(first['context'], '净利润呢？')
    assert second['resolved']['companies'] == [BYD]
    assert second['resolved']['years'] == [2024]
    assert second['resolved']['metrics'] == ['net_profit']


def test_four_turn_follow_up_and_explicit_conditions_override_history():
    turn = _turn(new_conversation_context(), '比亚迪2024年营收多少？')
    turn = _turn(turn['context'], '那2023年呢？')
    turn = _turn(turn['context'], '宁德时代呢？')
    turn = _turn(turn['context'], '净利润呢？')
    assert turn['resolved']['companies'] == [CATL]
    assert turn['resolved']['years'] == [2023]
    assert turn['resolved']['metrics'] == ['net_profit']

    overridden = _turn(turn['context'], '比亚迪2025年营业收入是多少？')
    assert overridden['resolved']['companies'] == [BYD]
    assert overridden['resolved']['years'] == [2025]
    assert overridden['resolved']['metrics'] == ['revenue']


def test_clarification_triggers_and_confirmation_resumes_task():
    metric = resolve_turn(
        new_conversation_context(), '比亚迪财务数据是多少？', COMPANIES, page_company=None, page_compare=None
    )
    assert metric['status'] == 'needs_clarification'
    assert metric['field'] == 'metric'

    comparison = resolve_turn(
        new_conversation_context(), '请对比比亚迪', COMPANIES, page_company=None, page_compare=None
    )
    assert comparison['status'] == 'needs_clarification'
    assert comparison['field'] == 'compare_company'
    confirmed = confirm_clarification(comparison['context'], CATL, COMPANIES)
    assert confirmed['status'] == 'ready'
    assert confirmed['resolved']['companies'] == [BYD, CATL]

    ambiguous = resolve_turn(
        new_conversation_context(), '汽车2024年营业收入是多少？', COMPANIES,
        page_company=None, page_compare=None,
    )
    assert ambiguous['status'] == 'needs_clarification'
    assert ambiguous['field'] == 'company'
    assert {option['value'] for option in ambiguous['options']} == {CHANGAN, SAIC}

    pronoun = resolve_turn(
        new_conversation_context(), '它2024年净利润是多少？', COMPANIES,
        page_company=None, page_compare=None,
    )
    assert pronoun['status'] == 'needs_clarification'
    assert pronoun['field'] == 'company'

    other_context = new_conversation_context()
    other_context['primary_company'] = BYD
    other = resolve_turn(other_context, '另一家呢？', COMPANIES, page_company=None, page_compare=None)
    assert other['status'] == 'needs_clarification'
    assert other['field'] == 'company'


def test_other_company_clarification_keeps_history_conditions():
    first = _turn(new_conversation_context(), '比亚迪2024年营业收入是多少？')
    clarification = resolve_turn(
        first['context'], '另一家呢？', COMPANIES, page_company=BYD, page_compare=None
    )
    assert clarification['status'] == 'needs_clarification'
    confirmed = confirm_clarification(clarification['context'], CATL, COMPANIES)
    assert confirmed['resolved']['companies'] == [CATL]
    assert confirmed['resolved']['years'] == [2024]
    assert confirmed['resolved']['metrics'] == ['revenue']
    assert confirmed['resolved']['intent'] == 'finance_query'


def test_real_follow_up_inherits_history_year_before_page_year():
    first = resolve_turn(
        new_conversation_context(), '比亚迪2024年营业收入是多少？', COMPANIES,
        page_company=BYD, page_compare=CATL, page_year=2025,
    )
    second = resolve_turn(
        first['context'], '宁德时代呢？', COMPANIES,
        page_company=BYD, page_compare=CATL, page_year=2025,
    )
    assert second['resolved']['companies'] == [CATL]
    assert second['resolved']['years'] == [2024]
    assert second['resolved']['metrics'] == ['revenue']


def test_new_context_clears_all_inherited_and_clarification_state():
    context = new_conversation_context()
    assert context == {
        'primary_company': None,
        'compare_company': None,
        'years': [],
        'metrics': [],
        'intent': None,
        'awaiting_clarification': False,
        'clarification': None,
    }


def test_imported_company_name_code_relative_year_and_comparison_use_live_database(tmp_path):
    company = '测试科技股份有限公司'
    peer = '另一科技股份有限公司'
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        init_db()
        company_id = upsert_company(company, stock_code='620240')
        peer_id = upsert_company(peer, stock_code='600998')
        for year, revenue in [(2023, 80), (2024, 100)]:
            upsert_metric(company_id, {'year': year, 'revenue': revenue})
            upsert_metric(peer_id, {'year': year, 'revenue': revenue + 10})
        records = fetch_companies()
        names = [row['name'] for row in records]
        data = {row['name']: rows_to_df(fetch_company_metrics(row['id'])) for row in records}
        first = resolve_turn(None, '620240的2024年营业收入是多少？', names, page_company=peer, page_compare=company)
        assert first['status'] == 'ready'
        assert first['resolved']['companies'] == [company]
        assert first['resolved']['years'] == [2024]
        answer = answer_question(first['question'], peer, company, names, data, resolved_context=first['resolved'])
        assert answer['status'] == 'success'
        assert answer['sql_result']['rows'][0]['revenue'] == 100
        assert answer['company'] == company

        second = resolve_turn(first['context'], '那上一年呢？', names, page_company=peer, page_compare=company)
        assert second['resolved']['companies'] == [company]
        assert second['resolved']['years'] == [2023]
        assert second['resolved']['metrics'] == ['revenue']
        answer = answer_question(second['question'], peer, company, names, data, resolved_context=second['resolved'])
        assert answer['status'] == 'success'
        assert answer['sql_result']['rows'][0]['revenue'] == 80
        comparison = resolve_turn(second['context'], '和另一家企业相比呢？', names, page_company=company, page_compare=peer)
        assert comparison['status'] == 'ready'
        assert comparison['resolved']['companies'] == [company, peer]
        assert comparison['context']['primary_company'] == company
        answer = answer_question(comparison['question'], company, peer, names, data, resolved_context=comparison['resolved'])
        assert answer['status'] == 'success'
        assert {row['company'] for row in answer['sql_result']['rows']} == {company, peer}
        by_name = answer_question('测试科技2024年营业收入是多少？', peer, company, names, data)
        assert by_name['status'] == 'success'
        assert by_name['company'] == company
        assert parse_question('620240营业收入是多少？', names, peer, '平衡型')['years'] == []


def test_stock_code_resolution_excludes_companies_not_in_current_analysis_list(tmp_path):
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        init_db()
        upsert_company('仅文档企业', stock_code='600997')
        parsed = parse_question('600997的2024年营业收入是多少？', [BYD], BYD, '平衡型')
        assert parsed['companies'] == []
        assert parsed['unknown_companies']


def test_comparison_preserves_question_company_order_instead_of_database_order():
    result = resolve_turn(None, '宁德时代和比亚迪2024年营业收入相比呢？', COMPANIES)
    assert result['resolved']['companies'] == [CATL, BYD]


def test_other_company_comparison_clarification_keeps_primary_and_metric():
    first = _turn(new_conversation_context(), '比亚迪2024年营业收入是多少？')
    result = resolve_turn(first['context'], '和另一家企业相比呢？', COMPANIES, page_compare=None)
    assert result['status'] == 'needs_clarification'
    assert result['field'] == 'compare_company'
    confirmed = confirm_clarification(result['context'], CATL, COMPANIES)
    assert confirmed['resolved']['companies'] == [BYD, CATL]
    assert confirmed['context']['primary_company'] == BYD
    assert confirmed['resolved']['years'] == [2024]
    assert confirmed['resolved']['metrics'] == ['revenue']
