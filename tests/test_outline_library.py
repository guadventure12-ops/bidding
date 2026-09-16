import copy
import pytest
from app import db, outline_library as library


@pytest.fixture
def isolated_library(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('MX_TESTING', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    db.init()


def test_read_defaults_without_writing_and_exactly_three_presets(isolated_library):
    value = library.get_library()
    assert len(value['presets']) == 3 and len(value['modules']) == 8
    assert db.one('SELECT * FROM settings WHERE key=?', (library.KEY,)) is None


def test_save_order_presets_and_repeated_save_is_idempotent(isolated_library):
    value = library.get_library(); modules = list(reversed(value['modules']))
    presets = copy.deepcopy(value['presets']); presets[0]['module_ids'] = [modules[0]['id'], modules[3]['id']]
    after = library.save_library(value['revision'], modules, presets)
    assert library.get_library() == after
    assert after['modules'][0]['id'] == modules[0]['id']
    assert library.save_library(after['revision'], modules, presets) == after
    with pytest.raises(ValueError, match='变化'):
        library.save_library(value['revision'], modules, presets)


@pytest.mark.parametrize('case', ['duplicate_id', 'duplicate_title', 'invalid_domain', 'missing_preset', 'bad_preset_ref', 'numbered_title'])
def test_reject_invalid_library_without_write(isolated_library, case):
    value = library.get_library(); before = copy.deepcopy(value)
    if case == 'duplicate_id': value['modules'][1]['id'] = value['modules'][0]['id']
    if case == 'duplicate_title': value['modules'][1]['title'] = value['modules'][0]['title']
    if case == 'invalid_domain': value['modules'][0]['domains'] = ['anything']
    if case == 'missing_preset': value['presets'].pop()
    if case == 'bad_preset_ref': value['presets'][0]['module_ids'] = ['nonexistent']
    if case == 'numbered_title': value['modules'][0]['title'] = '一、总体设计'
    with pytest.raises(ValueError): library.save_library(value['revision'], value['modules'], value['presets'])
    assert library.get_library() == before
