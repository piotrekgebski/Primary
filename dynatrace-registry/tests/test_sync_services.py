"""Offline tests; never contact a real Dynatrace instance."""
import importlib.util
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'sync_services.py'
spec = importlib.util.spec_from_file_location('dt_registry', SOURCE)
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


def service(sid, name='Service'):
    return {
        'entityId': sid,
        'displayName': name,
        'firstSeenTms': 1700000000000,
        'lastSeenTms': 1700001000000,
        'tags': [{'stringRepresentation': 'Team:Operations'}],
        'managementZones': [{'name': 'PROD'}],
        'properties': {'serviceTechnologyTypes': ['JAVA']},
    }


def test_two_page_api_uses_cursor_only(monkeypatch):
    pages = [
        {'entities': [service('SERVICE-AAA')], 'totalCount': 2, 'nextPageKey': 'xyz'},
        {'entities': [service('SERVICE-BBB')], 'nextPageKey': None},
    ]
    responses = []
    for payload in pages:
        r = Mock(status_code=200)
        r.json.return_value = payload
        responses.append(r)
    session = Mock()
    session.get.side_effect = responses
    monkeypatch.setattr(registry.requests, 'Session', lambda: session)
    monkeypatch.setattr(registry, 'BASE', 'https://dynatrace.example')
    monkeypatch.setattr(registry, 'TOKEN', 'test')

    assert len(registry.fetch_all()) == 2
    assert session.get.call_args_list[1].kwargs['params'] == {'nextPageKey': 'xyz'}
    params = session.get.call_args_list[0].kwargs['params']
    assert params['entitySelector'] == 'type(SERVICE)'


def test_registry_new_rename_missing_and_return(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, 'ROOT', tmp_path)
    monkeypatch.setattr(registry, 'DB', tmp_path / 'services.sqlite')
    monkeypatch.setattr(registry, 'MISSING_LIMIT', 3)
    monkeypatch.setattr(registry, 'MIN_RESULT_RATIO', 0.0)
    current = [service('SERVICE-A', 'Alpha'), service('SERVICE-B', 'Beta')]
    monkeypatch.setattr(registry, 'fetch_all', lambda: current)
    registry.main()
    current[:] = [service('SERVICE-A', 'Alpha2')]
    registry.main()
    registry.main()
    registry.main()
    with sqlite3.connect(registry.DB) as conn:
        status = conn.execute(
            "SELECT status FROM services WHERE service_id='SERVICE-B'"
        ).fetchone()[0]
        assert status == 'NOT_OBSERVED'
        renamed = conn.execute(
            "SELECT count(*) FROM events WHERE event_type='RENAMED'"
        ).fetchone()[0]
        assert renamed == 1
    current.append(service('SERVICE-B', 'Beta'))
    registry.main()
    with sqlite3.connect(registry.DB) as conn:
        status = conn.execute(
            "SELECT status FROM services WHERE service_id='SERVICE-B'"
        ).fetchone()[0]
        assert status == 'OBSERVED'
        returned = conn.execute(
            "SELECT count(*) FROM events WHERE event_type='RETURNED'"
        ).fetchone()[0]
        assert returned == 1
    assert (tmp_path / 'services_current.csv').is_file()


def test_empty_fetch_keeps_previous_data(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, 'ROOT', tmp_path)
    monkeypatch.setattr(registry, 'DB', tmp_path / 'services.sqlite')
    current = [service('SERVICE-A')]
    monkeypatch.setattr(registry, 'fetch_all', lambda: current)
    registry.main()
    current.clear()
    with pytest.raises(RuntimeError, match='Zero Services'):
        registry.main()
    with sqlite3.connect(registry.DB) as conn:
        assert conn.execute('SELECT count(*) FROM runs').fetchone()[0] == 1


def test_abrupt_drop_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, 'ROOT', tmp_path)
    monkeypatch.setattr(registry, 'DB', tmp_path / 'services.sqlite')
    current = [service(f'SERVICE-{i}') for i in range(10)]
    monkeypatch.setattr(registry, 'fetch_all', lambda: current)
    registry.main()
    current[:] = [service('SERVICE-0')]
    with pytest.raises(RuntimeError, match='Abrupt drop'):
        registry.main()
    with sqlite3.connect(registry.DB) as conn:
        assert conn.execute('SELECT count(*) FROM runs').fetchone()[0] == 1
