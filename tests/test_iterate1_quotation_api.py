"""Quotation API retains diagnostics while configured checks control confirmation."""
import copy
import pytest
from fastapi.testclient import TestClient
from app import db, provider, workflow, review_rules
from app.main import app


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('MX_TESTING', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.setattr(provider, 'key_configured', lambda: False)
    monkeypatch.setattr(provider, 'chat_json', lambda *a, **k: pytest.fail('no model calls allowed'))
    db.init()
    db.insert('projects', {'id': 'quote-p', 'name': '合成报价校验', 'company_name': '合成企业',
                          'created_at': db.now(), 'updated_at': db.now()})
    with TestClient(app) as client:
        yield client


def put(client, value, flags=None):
    config=client.get('/api/review-settings').json()
    response=client.patch('/api/review-settings',json={'revision':config['revision'],'enabled':{**{k:True for k in config['enabled']},**(flags or {})}})
    assert response.status_code==200
    return client.patch('/api/projects/quote-p', json={'quotation': value})


def quote(total='100', **extra):
    return {'confirmed': True, 'total_including_tax': total, **extra}


@pytest.mark.parametrize('literal', ['-10.123', '00100.123', '1,000.00', '1e3'])
def test_api_decimal_switch_retains_literal_and_findings(local, literal):
    assert put(local, quote(literal)).status_code == 400
    result = put(local, quote(literal), {'quote_decimal': False})
    assert result.status_code == 200, result.text
    stored = result.json()['project']['quotation']
    assert stored['confirmed'] is True
    assert stored['total_including_tax'] == literal
    assert stored['issues'] and not stored['active_issues']
    assert {item['rule_id'] for item in stored['issue_details']} == {'quote_decimal'}


def test_api_arithmetic_switch_does_not_correct_values(local):
    value = quote('350', items=[{'name': '许可', 'quantity': '2', 'unit_price': '100', 'subtotal': '300'}])
    assert put(local, value).status_code == 400
    result = put(local, value, {'quote_arithmetic': False})
    assert result.status_code == 200, result.text
    saved = result.json()['project']['quotation']
    assert saved['items'][0]['subtotal'] == '300' and saved['total_including_tax'] == '350'
    assert len(saved['issues']) == 2
    assert saved['active_issues'] == [] and saved['confirmed'] is True
    assert {item['rule_id'] for item in saved['issue_details']} == {'quote_arithmetic'}


def test_api_independent_switches_do_not_disable_other_rule(local):
    value = quote('-350', items=[{'name': '许可', 'quantity': '2', 'unit_price': '100', 'subtotal': '300'}])
    assert put(local, value, {'quote_decimal': False}).status_code == 400
    assert put(local, value, {'quote_arithmetic': False}).status_code == 400
    allowed = put(local, value, {'quote_decimal': False, 'quote_arithmetic': False})
    assert allowed.status_code == 200, allowed.text
    saved = allowed.json()['project']['quotation']
    assert {item['rule_id'] for item in saved['issue_details']} == {'quote_arithmetic', 'quote_decimal'}
    assert saved['issues'] and not saved['active_issues']


@pytest.mark.parametrize('literal', ['NaN', 'Infinity', 'not money'])
def test_non_numeric_execution_errors_do_not_create_confirmed_prices(local, literal):
    result = put(local, quote(literal), {'quote_decimal': False, 'quote_arithmetic': False})
    assert result.status_code == 400 and '无法处理' in result.json()['detail']
    assert not db.one('SELECT quotation FROM projects WHERE id=?', ('quote-p',))['quotation']


def test_api_draft_retains_active_issues_and_valid_default_still_passes(local):
    result = put(local, {'confirmed': False, 'total_including_tax': '0'})
    assert result.status_code == 200
    saved = result.json()['project']['quotation']
    assert saved['active_issues'] == saved['issues'] == ['含税总价必须大于0元']
    assert not saved['confirmed']
    result = put(local, quote('100.00'))
    assert result.status_code == 200
    saved = result.json()['project']['quotation']
    assert saved['confirmed'] and saved['confirmed_at'] and not saved['issues']


def test_historical_issues_remain_after_read_under_different_flags(local):
    value = quote('350', items=[{'name': '许可', 'quantity': '2', 'unit_price': '100', 'subtotal': '300'}])
    assert put(local, value, {'quote_arithmetic': False}).status_code == 200
    before = copy.deepcopy(db.one('SELECT quotation FROM projects WHERE id=?', ('quote-p',))['quotation'])
    assert before['issues']
    with review_rules.scope({'quote_arithmetic': True}):
        local.get('/api/projects/quote-p')
    after = db.one('SELECT quotation FROM projects WHERE id=?', ('quote-p',))['quotation']
    assert after == before
    # Reconfirmation with the rule restored is blocked; failed writes keep history.
    assert put(local, value).status_code == 400
    assert db.one('SELECT quotation FROM projects WHERE id=?', ('quote-p',))['quotation'] == before
