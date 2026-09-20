from core.conversation import confirm_clarification, new_conversation_context, resolve_turn


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
