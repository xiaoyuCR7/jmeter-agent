"""Project knowledge boundaries; only disposable databases and fake embeddings."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.schemas import FactSheet, Hypothesis, HypothesisStatus
from app.services.project_scope import AccessScope, bind_scope, internal_scope
from app.services.ai.rag import store as rag_module
from app.services.ai.rag.store import LanceRAGStore, RAGDocument
from app.services.ai.memory.fact_ledger import FactLedger
from app.services.ai.memory.hypothesis_ledger import HypothesisLedger


@pytest.fixture
def scope(tmp_path):
    authorization = SimpleNamespace(user_id=str(tmp_path), revision=1, session_key='knowledge-test', global_role='operator',
        project_modes={'a': 'private', 'b': 'private'},
        allows=lambda project, action: project == 'a')
    return AccessScope(authorization,
        plans={'pa': {'project_id': 'a'}, 'pb': {'project_id': 'b'}},
        executions={name: {'project_id': project, 'report_dir': str(tmp_path / name)}
                    for name, project in [('ea', 'a'), ('eb', 'b')]})


def document(identity, source, **kwargs):
    return RAGDocument(id=identity, doc_type='analysis_conclusion', module='api', version=identity,
        title='Project measurement', content='Measured response latency needs additional investigation.',
        keywords=['latency'], source_execution_id=source, **kwargs)


@pytest.fixture(params=['lance', 'fallback'])
def rag(request, tmp_path, monkeypatch):
    monkeypatch.setattr(rag_module, '_compute_embedding', lambda text: [0.01] * 384)
    if request.param == 'fallback':
        monkeypatch.setattr(LanceRAGStore, '_init_db', lambda self: None)
    with internal_scope():
        result = LanceRAGStore(str(tmp_path / request.param))
        if request.param == 'lance':
            assert result.available
        result.add(document('allowed', 'ea', created_at='2025-01-01'))
        result.add(document('hidden', 'eb', created_at='2026-01-01'))
        result.add(document('unknown', 'missing', created_at='2026-02-01'))
        result.add(document('mixed', 'ea', source_plan_id='pb', created_at='2026-03-01'))
        result.add(document('unscoped', None, created_at='2026-04-01'))
    return result


@pytest.mark.parametrize('operation', [
    lambda s: s.list_all(limit=1),
    lambda s: s.hybrid_search(module='api', limit=1),
    lambda s: s.hybrid_search(query_text='latency', limit=1),
    lambda s: s.search_by_keywords(['latency'], limit=1),
    lambda s: s.search_by_module('api', limit=1),
    lambda s: s.search_by_type('analysis_conclusion', limit=1),
    lambda s: s.full_text_search('latency', limit=1),
    lambda s: [s.get_latest_analysis('api')],
])
def test_rag_filters_before_search_limit(rag, scope, operation):
    with bind_scope(scope):
        assert [doc.id for doc in operation(rag)] == ['allowed']
        assert rag.list_all(limit=1, offset=1) == []


def test_rag_get_counts_versions_and_stats_use_authorized_sources(rag, scope):
    with bind_scope(scope):
        assert rag.get('allowed').id == 'allowed'
        for identity in ('hidden', 'unknown', 'mixed', 'unscoped'):
            assert rag.get(identity) is None
        assert rag.count() == 1
        assert rag.get_module_versions('api') == ['allowed']
        stats = rag.get_stats()
        assert stats['total_documents'] == 1
        assert stats['by_module'] == {'api': 1}
        assert stats['by_type'] == {'analysis_conclusion': 1}


@pytest.mark.parametrize('operation', [
    lambda s: s.add(document('hidden', 'ea')),
    lambda s: s.add(document('new', 'eb')),
    lambda s: s.add(document('new', 'missing')),
    lambda s: s.update('hidden', content='Unauthorized replacement must never enter the document store.'),
    lambda s: s.delete('hidden'),
    lambda s: s.update('allowed', source_execution_id='eb'),
    lambda s: s.update('allowed', source_plan_id='pa'),
    lambda s: s.update('allowed', context_scope='spoofed'),
])
def test_rag_rejects_hidden_mutations_and_source_changes(rag, scope, operation):
    with bind_scope(scope), pytest.raises((HTTPException, ValueError)):
        operation(rag)
    with internal_scope():
        assert rag.get('hidden').source_execution_id == 'eb'
        assert rag.get('allowed').source_execution_id == 'ea'
        assert rag.get('allowed').context_scope == ''
        assert rag.get('new') is None


def test_rag_without_scope_is_not_internal(rag):
    with bind_scope(None), pytest.raises(HTTPException):
        rag.list_all()


def test_rag_migration_requires_explicit_internal_scope(rag, tmp_path):
    source = tmp_path / 'import.json'
    source.write_text('{"documents": []}', encoding='utf-8')
    with bind_scope(None), pytest.raises(HTTPException):
        rag.migrate_from_json(str(source))


def test_rag_startup_recovers_fallback_without_ambient_scope(tmp_path, monkeypatch, scope):
    import json
    from app.services.project_scope import current_scope
    monkeypatch.setattr(rag_module, '_compute_embedding', lambda text: [0.01] * 384)
    path = tmp_path / 'startup-rag'
    with internal_scope():
        existing = LanceRAGStore(str(path))
        assert existing.available
        existing.add(document('hidden', 'eb'))
    fallback = path / 'fallback.json'
    fallback.write_text(json.dumps({'documents': [document('allowed', 'ea').to_dict(),
        document('hidden', 'ea').to_dict()]}), encoding='utf-8')
    with bind_scope(None):
        restored = LanceRAGStore(str(path))
        with pytest.raises(HTTPException):
            current_scope()
    assert not fallback.exists()
    assert (path / 'fallback.json.migrated').exists()
    with internal_scope():
        assert restored.get('hidden').source_execution_id == 'eb'
    with bind_scope(scope):
        assert [item.id for item in restored.list_all()] == ['allowed']
        with pytest.raises(HTTPException):
            restored.migrate_from_json(str(path / 'fallback.json.migrated'))


@pytest.mark.asyncio
async def test_rag_vector_maintenance_without_ambient_scope(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.project_scope import current_scope
    monkeypatch.setattr(rag_module, '_compute_embedding', lambda text: None)
    monkeypatch.setattr(rag_module, '_ZERO_VECTOR_IDS', set())
    with internal_scope():
        storage = LanceRAGStore(str(tmp_path / 'zero-rag'))
        assert storage.available
        storage.add(document('zero-vector', 'eb'))
    monkeypatch.setattr(rag_module, 'rag_store', storage)
    monkeypatch.setattr(rag_module, '_compute_embedding_async', AsyncMock(return_value=[0.01] * 384))
    with bind_scope(None):
        await rag_module._repair_zero_vectors()
        with pytest.raises(HTTPException):
            current_scope()
    assert 'zero-vector' not in rag_module._ZERO_VECTOR_IDS
    with internal_scope():
        rows = storage._table.search().where("id = 'zero-vector'").limit(1).to_list()
        assert any(rows[0]['vector'])
        assert storage.get('zero-vector').source_execution_id == 'eb'


@pytest.mark.asyncio
async def test_fact_history_filters_before_hash_and_limit(tmp_path, scope, monkeypatch):
    ledger = FactLedger(str(tmp_path / 'facts.db'))
    try:
        with internal_scope():
            for execution in ('ea', 'eb', 'missing'):
                assert await ledger.save(FactSheet(execution_id=execution, module='api'))
        calls = []
        original = ledger._verified_row
        async def verify(row):
            calls.append(row[2])
            return await original(row)
        monkeypatch.setattr(ledger, '_verified_row', verify)
        with bind_scope(scope):
            assert [fact.execution_id for fact in await ledger.get_facts_for_module_version('api', limit=1)] == ['ea']
        assert calls == ['ea']
    finally:
        await ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['load', 'save', 'compute'])
async def test_fact_denial_precedes_any_jtl_read(tmp_path, scope, monkeypatch, operation):
    ledger = FactLedger(str(tmp_path / 'facts.db'))
    with internal_scope():
        assert await ledger.save(FactSheet(execution_id='eb', module='api'))
    calls = []
    monkeypatch.setattr(FactLedger, '_jtl_hash', staticmethod(lambda path: calls.append(path) or 'digest'))
    try:
        with bind_scope(scope), pytest.raises(HTTPException):
            if operation == 'load':
                await ledger.load('eb')
            elif operation == 'save':
                await ledger.save(FactSheet(execution_id='eb', module='api', jtl_path='forbidden.jtl'))
            else:
                ledger.compute_from_jtl('forbidden.jtl', 'eb')
        assert calls == []
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_fact_rechecks_revocation_after_hash_work(tmp_path, scope, monkeypatch):
    from app.services.project_scope import note_revocation
    ledger = FactLedger(str(tmp_path / 'facts.db'))
    jtl = tmp_path / 'ea' / 'result.jtl'
    jtl.parent.mkdir()
    jtl.write_text('isolated source content', encoding='utf-8')
    with internal_scope():
        assert await ledger.save(FactSheet(execution_id='ea', module='api', jtl_path=str(jtl)))
    original = ledger._jtl_hash
    def hash_then_revoke(path):
        digest = original(path)
        note_revocation(scope.authorization.user_id, 2)
        return digest
    monkeypatch.setattr(ledger, '_jtl_hash', hash_then_revoke)
    try:
        with bind_scope(scope), pytest.raises(HTTPException):
            await ledger.load('ea')
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_hypothesis_history_filters_before_decode_and_limit(tmp_path, scope, monkeypatch):
    ledger = HypothesisLedger(str(tmp_path / 'hyp.db'))
    try:
        with internal_scope():
            for execution in ('ea', 'eb', 'missing'):
                await ledger.add(Hypothesis(hypothesis_id=execution, source_execution_id=execution,
                                           module='api', statement='Observed latency may increase.'))
        calls = []
        original = ledger._row_to_hypothesis
        def decode(row):
            calls.append(row[1])
            return original(row)
        monkeypatch.setattr(ledger, '_row_to_hypothesis', decode)
        with bind_scope(scope):
            assert [hyp.hypothesis_id for hyp in await ledger.list_for_module('api', limit=1)] == ['ea']
            assert await ledger.get('eb') is None
            assert await ledger.get('missing') is None
        assert calls == ['ea']
    finally:
        await ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['update', 'insert-hidden-id', 'insert-unknown-source', 'verify-hidden'])
async def test_hypothesis_writes_require_old_and_new_sources(tmp_path, scope, operation):
    ledger = HypothesisLedger(str(tmp_path / 'hyp.db'))
    try:
        with internal_scope():
            for execution in ('ea', 'eb'):
                await ledger.add(Hypothesis(hypothesis_id=execution, source_execution_id=execution,
                                           module='api', statement='Observed latency may increase.'))
        with bind_scope(scope), pytest.raises(HTTPException):
            if operation == 'update':
                await ledger.update_status('eb', HypothesisStatus.CONFIRMED)
            elif operation == 'verify-hidden':
                await ledger.update_status('ea', HypothesisStatus.CONFIRMED, verified_by='eb')
            else:
                await ledger.add(Hypothesis(hypothesis_id='eb' if operation == 'insert-hidden-id' else 'new',
                    source_execution_id='ea' if operation == 'insert-hidden-id' else 'missing', module='api',
                    statement='New assertion must not bypass authorization.'), if_absent=True)
    finally:
        await ledger.close()
