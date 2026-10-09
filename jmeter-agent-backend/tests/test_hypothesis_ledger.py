"""Tests for HypothesisLedger — falsifiable interpretation ledger."""

import os
import tempfile
import pytest
import pytest_asyncio

from app.services.ai.memory.hypothesis_ledger import HypothesisLedger
from app.services.ai.memory.fact_ledger import FactLedger
from app.models.schemas import Hypothesis, HypothesisCategory, HypothesisStatus


@pytest_asyncio.fixture
async def read_contract_evaluation(temp_hypothesis_ledger, temp_fact_ledger, monkeypatch):
    import importlib
    from app.models.schemas import FactSheet
    module = importlib.import_module('app.services.ai.memory.hypothesis_ledger')
    monkeypatch.setattr(module, 'fact_ledger', temp_fact_ledger)
    for key, value in [('old', 100), ('new', 50)]:
        assert await temp_fact_ledger.save(FactSheet(execution_id=key, module='api', version='v1',
            context_scope='scope', total_requests=10, percentiles={'p99': value}))
    hyp = Hypothesis(hypothesis_id='first', source_execution_id='old', module='api', version='v1',
                     category=HypothesisCategory.RECOMMENDATION, statement='prediction',
                     testable_prediction='p99 decrease')
    await temp_hypothesis_ledger.add(hyp)
    return temp_hypothesis_ledger, temp_fact_ledger, hyp


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['list', 'get', 'new_fact', 'old_fact', 'final_get', 'conflict_get'])
async def test_ledger_read_contract_evaluation_propagates_each_read_failure(read_contract_evaluation, monkeypatch, phase):
    from unittest.mock import AsyncMock
    from app.services.ai.memory.read_errors import LedgerReadError
    from app.services.ai.memory.hypothesis_ledger import HypothesisUpdateConflict
    ledger, facts, hyp = read_contract_evaluation
    target = facts if phase.endswith('_fact') else ledger
    db = await target._get_db()
    original = db.execute
    reads = 0
    def execute(sql, args=()):
        nonlocal reads
        if sql.startswith('SELECT'):
            if phase == 'list' and 'WHERE module' in sql:
                raise OSError('PRIVATE_SENTINEL list')
            if 'WHERE hypothesis_id' in sql:
                reads += 1
                if (phase == 'get' and reads == 1) or (phase in ('final_get', 'conflict_get') and reads == 2):
                    raise OSError('PRIVATE_SENTINEL get')
            if phase.endswith('_fact') and args == ('new' if phase == 'new_fact' else 'old',):
                raise OSError('PRIVATE_SENTINEL fact')
        return original(sql, args)
    update = AsyncMock(wraps=ledger.update_status)
    with monkeypatch.context() as patch:
        patch.setattr(db, 'execute', execute)
        if phase == 'conflict_get':
            update.side_effect = HypothesisUpdateConflict('fixture concurrent update')
        patch.setattr(ledger, 'update_status', update)
        with pytest.raises(LedgerReadError) as failure:
            await ledger.evaluate_module_hypotheses('api', 'new', strict=True)
        assert 'PRIVATE_SENTINEL' not in str(failure.value)
    stored = await ledger.get(hyp.hypothesis_id)
    assert stored.status == (HypothesisStatus.CONFIRMED if phase == 'final_get' else HypothesisStatus.UNTESTED)
    assert update.await_count == (1 if phase in ('final_get', 'conflict_get') else 0)


@pytest.mark.asyncio
async def test_ledger_read_contract_evaluation_keeps_prior_commits_without_replay(read_contract_evaluation, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.ai.memory.read_errors import LedgerReadError
    ledger, _, hyp = read_contract_evaluation
    await ledger.add(hyp.model_copy(update={'hypothesis_id': 'second'}))
    db = await ledger._get_db()
    await db.execute("UPDATE hypotheses SET updated_at='2000-01-01' WHERE hypothesis_id='second'")
    await db.commit()
    original = db.execute
    def execute(sql, args=()):
        if sql.startswith('SELECT') and 'WHERE hypothesis_id' in sql and args == ('second',):
            raise OSError('PRIVATE_SENTINEL second')
        return original(sql, args)
    update = AsyncMock(wraps=ledger.update_status)
    with monkeypatch.context() as patch:
        patch.setattr(db, 'execute', execute)
        patch.setattr(ledger, 'update_status', update)
        with pytest.raises(LedgerReadError):
            await ledger.evaluate_module_hypotheses('api', 'new', strict=True)
    assert (await ledger.get('first')).status == HypothesisStatus.CONFIRMED
    assert (await ledger.get('second')).status == HypothesisStatus.UNTESTED
    update.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['initialize', 'connection', 'commit'])
@pytest.mark.parametrize('generated_id', [False, True])
async def test_hypothesis_add_returns_persisted_input_snapshot(temp_hypothesis_ledger, monkeypatch, phase, generated_id):
    ledger = temp_hypothesis_ledger
    hyp = Hypothesis(hypothesis_id='' if generated_id else 'original', source_execution_id='source',
                     module='api', statement='prediction', supporting_facts=['p99'])
    await ledger._ensure_table()
    db = await ledger._get_db()
    original_id = None
    target, name = {'initialize': (ledger, '_ensure_table'), 'connection': (ledger, '_get_db'),
                    'commit': (db, 'commit')}[phase]
    original = getattr(target, name)
    calls = 0

    async def suspend_and_mutate(*args, **kwargs):
        nonlocal original_id, calls
        value = await original(*args, **kwargs)
        calls += 1
        if phase in ('connection', 'commit') and calls != 2:
            return value
        original_id = original_id or hyp.hypothesis_id
        hyp.hypothesis_id = 'other'
        hyp.source_execution_id = 'other-source'
        hyp.module = 'other-api'
        hyp.statement = 'changed'
        hyp.supporting_facts.append('throughput')
        return value

    with monkeypatch.context() as patch:
        patch.setattr(target, name, suspend_and_mutate)
        saved_id = await ledger.add(hyp)
    assert saved_id and saved_id != 'other'
    assert saved_id == original_id
    loaded = await ledger.get(saved_id)
    assert loaded.source_execution_id == 'source' and loaded.module == 'api'
    assert loaded.statement == 'prediction' and loaded.supporting_facts == ['p99']
    assert await ledger.get('other') is None


@pytest.mark.asyncio
async def test_hypothesis_add_if_absent_is_atomic_and_preserves_verification(temp_hypothesis_ledger):
    import asyncio
    import sqlite3
    ledger = temp_hypothesis_ledger
    other = HypothesisLedger(ledger.db_path)
    hyp = Hypothesis(hypothesis_id='same', source_execution_id='source', module='api', statement='prediction')
    try:
        await ledger._ensure_table()
        await other._ensure_table()
        assert await asyncio.gather(ledger.add(hyp, if_absent=True), other.add(hyp, if_absent=True)) == ['same', 'same']
        assert len(await ledger.list_for_module('api')) == 1
        assert await ledger.update_status('same', HypothesisStatus.CONFIRMED, verified_by='proof', notes='metric only')
        assert await other.add(hyp, if_absent=True) == 'same'
        loaded = await ledger.get('same')
        assert loaded.status == HypothesisStatus.CONFIRMED
        assert loaded.verified_by_execution_id == 'proof' and loaded.verification_notes == 'metric only'
        with pytest.raises(sqlite3.IntegrityError):
            await ledger.add(hyp)  # Normal inserts still reject duplicate IDs.
        assert (await ledger.get('same')).status == HypothesisStatus.CONFIRMED
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_hypothesis_add_if_absent_does_not_swallow_other_constraints(temp_hypothesis_ledger):
    import sqlite3
    hyp = Hypothesis(hypothesis_id='invalid', source_execution_id='source', module='api', statement='prediction')
    hyp.module = None
    with pytest.raises(sqlite3.IntegrityError):
        await temp_hypothesis_ledger.add(hyp, if_absent=True)
    assert await temp_hypothesis_ledger.get('invalid') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
@pytest.mark.parametrize('rollback_fails', [False, True])
async def test_failed_hypothesis_add_is_not_inserted_by_later_read(temp_hypothesis_ledger, monkeypatch, cancelled, rollback_fails):
    import asyncio
    from unittest.mock import AsyncMock
    ledger = temp_hypothesis_ledger
    await ledger._ensure_table()
    db = await ledger._get_db()
    original_commit = db.commit
    commits = 0
    async def fail_write_commit():
        nonlocal commits
        commits += 1
        if commits == 2:
            if cancelled:
                raise asyncio.CancelledError()
            raise RuntimeError('simulated commit failure')
        await original_commit()
    monkeypatch.setattr(db, 'commit', fail_write_commit)
    if rollback_fails:
        monkeypatch.setattr(db, 'rollback', AsyncMock(side_effect=RuntimeError('rollback failed')))
    hyp = Hypothesis(hypothesis_id='new', source_execution_id='source', module='api', statement='prediction')
    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
        await ledger.add(hyp)
    assert await ledger.get('new') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_count', [1, 70])
@pytest.mark.parametrize('strict', [False, True])
async def test_hypothesis_history_skips_bad_rows_and_fills_filtered_limit(temp_hypothesis_ledger, invalid_count, strict):
    ledger = temp_hypothesis_ledger
    for i in range(invalid_count + 1):
        await ledger.add(Hypothesis(hypothesis_id=f'h-{i:03}', source_execution_id='allowed',
                                    module='api', version='v1', statement='prediction'))
    await ledger.add(Hypothesis(hypothesis_id='other', source_execution_id='other-source', module='api', statement='unrelated'))
    db = await ledger._get_db()
    await db.execute("UPDATE hypotheses SET supporting_facts = 'broken-json', updated_at = '2099-01-01' WHERE hypothesis_id != 'h-000'")
    await db.commit()
    result = await ledger.list_for_module('api', version='v1', limit=1, status=HypothesisStatus.UNTESTED,
                                         source_execution_ids=['allowed'], **({'strict': True} if strict else {}))
    assert [hyp.hypothesis_id for hyp in result] == ['h-000']
    assert await ledger.get('h-001', **({'strict': True} if strict else {})) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [-1, 1.5, True])
@pytest.mark.parametrize('strict', [False, True])
async def test_hypothesis_history_invalid_limit_is_rejected_before_database(temp_hypothesis_ledger, monkeypatch, limit, strict):
    from unittest.mock import AsyncMock
    ensure = AsyncMock(side_effect=AssertionError('should not open database'))
    monkeypatch.setattr(temp_hypothesis_ledger, '_ensure_table', ensure)
    with pytest.raises(ValueError, match='limit'):
        await temp_hypothesis_ledger.list_for_module('api', limit=limit, **({'strict': True} if strict else {}))
    ensure.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [{'limit': 0}, {'source_execution_ids': []}])
@pytest.mark.parametrize('strict', [False, True])
async def test_empty_hypothesis_query_does_not_open_database(temp_hypothesis_ledger, monkeypatch, kwargs, strict):
    from unittest.mock import AsyncMock
    ensure = AsyncMock(side_effect=AssertionError('should not open database'))
    monkeypatch.setattr(temp_hypothesis_ledger, '_ensure_table', ensure)
    assert await temp_hypothesis_ledger.list_for_module('api', **kwargs, **({'strict': True} if strict else {})) == []
    ensure.assert_not_awaited()


@pytest.fixture
def sample_jtl():
    return os.path.join(os.path.dirname(__file__), "fixtures", "sample.jtl")


@pytest_asyncio.fixture
async def temp_hypothesis_ledger():
    # Use the project directory for temp files to avoid Windows Unicode path issues
    # with aiosqlite (system temp may contain non-ASCII characters).
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=".") as f:
        path = f.name
    ledger = HypothesisLedger(db_path=path)
    try:
        yield ledger
    finally:
        await ledger.close()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(path + ext)
            except OSError:
                pass


@pytest_asyncio.fixture
async def temp_fact_ledger():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=".") as f:
        path = f.name
    ledger = FactLedger(db_path=path)
    try:
        yield ledger
    finally:
        await ledger.close()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(path + ext)
            except OSError:
                pass


@pytest.mark.asyncio
async def test_status_transitions_do_not_keep_contradictory_verification(temp_hypothesis_ledger):
    ledger = temp_hypothesis_ledger
    hid = await ledger.add(Hypothesis(hypothesis_id='', source_execution_id='source',
                                      module='api', statement='prediction'))
    assert await ledger.update_status(hid, HypothesisStatus.CONFIRMED, verified_by='pass', notes='first evidence')
    assert await ledger.update_status(hid, HypothesisStatus.REFUTED, refuted_by='fail', notes='second evidence')
    hyp = await ledger.get(hid)
    assert hyp.verified_by_execution_id is None and hyp.refuted_by_execution_id == 'fail'
    assert await ledger.update_status(hid, HypothesisStatus.CONFIRMED, verified_by='pass-again')
    hyp = await ledger.get(hid)
    assert hyp.refuted_by_execution_id is None and hyp.verified_by_execution_id == 'pass-again'
    assert hyp.verification_notes is None
    assert await ledger.update_status(hid, HypothesisStatus.UNTESTED)
    hyp = await ledger.get(hid)
    assert hyp.verified_by_execution_id is None and hyp.refuted_by_execution_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [HypothesisStatus.CONFIRMED, HypothesisStatus.REFUTED, HypothesisStatus.DEPRECATED])
async def test_automatic_evaluation_skips_resolved_or_deprecated_hypotheses(temp_hypothesis_ledger, monkeypatch, status):
    import importlib
    from unittest.mock import AsyncMock
    module = importlib.import_module('app.services.ai.memory.hypothesis_ledger')
    ledger = temp_hypothesis_ledger
    hid = await ledger.add(Hypothesis(hypothesis_id='', source_execution_id='old', module='api',
        category=HypothesisCategory.RECOMMENDATION, statement='prediction', testable_prediction='p99 decrease'))
    assert await ledger.update_status(hid, status, notes='retained decision')
    before = await ledger.get(hid)
    load = AsyncMock(side_effect=AssertionError('resolved hypothesis must not read facts'))
    monkeypatch.setattr(module.fact_ledger, 'load', load)
    result = await ledger.evaluate_against_facts(hid, 'new')
    assert result.model_dump() == before.model_dump()
    load.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['fact_read', 'write_wait'])
@pytest.mark.parametrize('new_status', list(HypothesisStatus))
async def test_automatic_evaluation_preserves_a_newer_decision(temp_hypothesis_ledger, monkeypatch, phase, new_status):
    import importlib
    from unittest.mock import AsyncMock
    from app.models.schemas import FactSheet
    module = importlib.import_module('app.services.ai.memory.hypothesis_ledger')
    ledger = temp_hypothesis_ledger
    other = HypothesisLedger(ledger.db_path)
    hid = await ledger.add(Hypothesis(hypothesis_id='', source_execution_id='old', module='api', version='v1',
        category=HypothesisCategory.RECOMMENDATION, statement='prediction', testable_prediction='p99 decrease'))
    db = await ledger._get_db()
    await db.execute("UPDATE hypotheses SET updated_at = '2000-01-01T00:00:00' WHERE hypothesis_id = ?", (hid,))
    await db.commit()
    facts = {key: FactSheet(execution_id=key, module='api', version='v1', context_scope='same',
                           total_requests=10, percentiles={'p99': value}) for key, value in [('old', 100), ('new', 50)]}
    newer = None

    async def change_decision():
        nonlocal newer
        assert await other.update_status(hid, new_status,
            verified_by='manual-proof' if new_status == HypothesisStatus.CONFIRMED else None,
            refuted_by='manual-proof' if new_status == HypothesisStatus.REFUTED else None,
            notes='newer decision')
        newer = await other.get(hid)

    async def load(execution_id):
        if phase == 'fact_read' and execution_id == 'new':
            await change_decision()
        return facts[execution_id]

    original_ensure, calls = ledger._ensure_table, 0
    async def ensure():
        nonlocal calls
        await original_ensure()
        calls += 1
        if phase == 'write_wait' and calls == 2:
            await change_decision()

    monkeypatch.setattr(module.fact_ledger, 'load', AsyncMock(side_effect=load))
    monkeypatch.setattr(ledger, '_ensure_table', ensure)
    try:
        result = await ledger.evaluate_against_facts(hid, 'new')
        assert newer is not None
        assert result.model_dump() == newer.model_dump()
        assert (await ledger.get(hid)).model_dump() == newer.model_dump()
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_update_missing_hypothesis_does_not_report_success(temp_hypothesis_ledger):
    assert not await temp_hypothesis_ledger.update_status('missing', HypothesisStatus.CONFIRMED, verified_by='run')


@pytest.mark.asyncio
async def test_concurrent_evaluations_do_not_overwrite_the_first_decision(temp_hypothesis_ledger, monkeypatch):
    import asyncio
    import importlib
    from unittest.mock import AsyncMock
    from app.models.schemas import FactSheet
    module = importlib.import_module('app.services.ai.memory.hypothesis_ledger')
    ledger = temp_hypothesis_ledger
    hid = await ledger.add(Hypothesis(hypothesis_id='', source_execution_id='old', module='api', version='v1',
        category=HypothesisCategory.RECOMMENDATION, statement='prediction', testable_prediction='p99 decrease'))
    facts = {key: FactSheet(execution_id=key, module='api', version='v1', context_scope='same',
                           total_requests=10, percentiles={'p99': value})
             for key, value in [('old', 100), ('improved', 50), ('regressed', 150)]}
    monkeypatch.setattr(module.fact_ledger, 'load', AsyncMock(side_effect=lambda key: facts[key]))
    original_get, original_update = ledger.get, ledger.update_status
    both_read, first_written = asyncio.Event(), asyncio.Event()
    reads = 0

    async def get(key):
        nonlocal reads
        value = await original_get(key)
        reads += 1
        if reads <= 2:
            if reads == 2:
                both_read.set()
            await both_read.wait()
        return value

    async def update(*args, **kwargs):
        if kwargs.get('refuted_by') == 'regressed':
            await first_written.wait()
        result = await original_update(*args, **kwargs)
        if kwargs.get('verified_by') == 'improved':
            first_written.set()
        return result

    monkeypatch.setattr(ledger, 'get', get)
    monkeypatch.setattr(ledger, 'update_status', update)
    results = await asyncio.wait_for(asyncio.gather(
        ledger.evaluate_against_facts(hid, 'improved'),
        ledger.evaluate_against_facts(hid, 'regressed')), timeout=10)
    final = await original_get(hid)
    assert final.status == HypothesisStatus.CONFIRMED and final.verified_by_execution_id == 'improved'
    assert final.refuted_by_execution_id is None
    assert all(result.model_dump() == final.model_dump() for result in results)


@pytest.mark.asyncio
async def test_same_status_note_update_preserves_existing_evidence(temp_hypothesis_ledger):
    ledger = temp_hypothesis_ledger
    hid = await ledger.add(Hypothesis(hypothesis_id='', source_execution_id='source',
                                      module='api', statement='prediction'))
    await ledger.update_status(hid, HypothesisStatus.CONFIRMED, verified_by='pass', notes='old note')
    await ledger.update_status(hid, HypothesisStatus.CONFIRMED, notes='amended note')
    hyp = await ledger.get(hid)
    assert hyp.verified_by_execution_id == 'pass' and hyp.verification_notes == 'amended note'
    await ledger.update_status(hid, HypothesisStatus.DEPRECATED)
    hyp = await ledger.get(hid)
    assert hyp.verified_by_execution_id == 'pass' and hyp.verification_notes == 'amended note'


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
@pytest.mark.parametrize('rollback_fails', [False, True])
async def test_failed_status_commit_is_not_committed_by_a_later_read(temp_hypothesis_ledger, monkeypatch, cancelled, rollback_fails):
    import asyncio
    from unittest.mock import AsyncMock
    ledger = temp_hypothesis_ledger
    hid = await ledger.add(Hypothesis(hypothesis_id='', source_execution_id='source',
                                      module='api', statement='prediction'))
    db = await ledger._get_db()
    original_commit = db.commit
    commits = 0
    async def fail_status_commit():
        nonlocal commits
        commits += 1
        if commits == 2:  # First commit belongs to _ensure_table, second to UPDATE.
            if cancelled:
                raise asyncio.CancelledError()
            raise RuntimeError('simulated commit failure')
        await original_commit()
    monkeypatch.setattr(db, 'commit', fail_status_commit)
    if rollback_fails:
        monkeypatch.setattr(db, 'rollback', AsyncMock(side_effect=RuntimeError('rollback unavailable')))
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await ledger.update_status(hid, HypothesisStatus.CONFIRMED, verified_by='run')
    else:
        assert not await ledger.update_status(hid, HypothesisStatus.CONFIRMED, verified_by='run')
    hyp = await ledger.get(hid)  # _ensure_table commits again; failed UPDATE must be gone.
    assert hyp.status == HypothesisStatus.UNTESTED and hyp.verified_by_execution_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize('new_p99', [50, 150])
async def test_evaluation_write_failure_is_not_silently_reported_as_processed(temp_hypothesis_ledger, monkeypatch, new_p99):
    import importlib
    from unittest.mock import AsyncMock
    from app.models.schemas import FactSheet
    module = importlib.import_module('app.services.ai.memory.hypothesis_ledger')
    facts = {key: FactSheet(execution_id=key, module='api', version='v1', test_type='baseline',
                           total_requests=10, percentiles={'p99': value})
             for key, value in [('old', 100), ('new', new_p99)]}
    monkeypatch.setattr(module.fact_ledger, 'load', AsyncMock(side_effect=lambda key: facts[key]))
    monkeypatch.setattr(temp_hypothesis_ledger, 'update_status', AsyncMock(return_value=False))
    hid = await temp_hypothesis_ledger.add(Hypothesis(hypothesis_id='', source_execution_id='old',
        module='api', version='v1', category=HypothesisCategory.RECOMMENDATION,
        statement='prediction', testable_prediction='p99 decrease'))
    with pytest.raises(RuntimeError, match='persist hypothesis evaluation'):
        await temp_hypothesis_ledger.evaluate_against_facts(hid, 'new')


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['no_old_samples', 'no_new_samples', 'negative_error', 'error_above_100',
                                  'source_module', 'source_version', 'hypothesis_test_type', 'overflow_change',
                                  'overflow_threshold'])
async def test_unusable_facts_do_not_confirm_or_refute_predictions(temp_hypothesis_ledger, monkeypatch, case):
    import importlib
    from unittest.mock import AsyncMock
    from app.models.schemas import FactSheet
    module = importlib.import_module('app.services.ai.memory.hypothesis_ledger')
    facts = {key: FactSheet(execution_id=key, module='api', version='v1', test_type='baseline',
                           context_scope='same', total_requests=10, error_rate=0,
                           percentiles={'p99': 100 if key == 'old' else 50}) for key in ('old', 'new')}
    prediction = 'error_rate below 1%'
    test_type = 'baseline'
    if case == 'no_old_samples': facts['old'].total_requests = 0
    elif case == 'no_new_samples': facts['new'].total_requests = 0
    elif case == 'negative_error': facts['new'].error_rate = -1
    elif case == 'error_above_100': facts['new'].error_rate = 101
    elif case == 'source_module': facts['old'].module = 'other'
    elif case == 'source_version': facts['old'].version = 'v0'
    elif case == 'hypothesis_test_type': test_type = 'stress'
    elif case == 'overflow_change':
        prediction = 'p99 increase'
        facts['old'].percentiles['p99'] = 1e-308
        facts['new'].percentiles['p99'] = 1e308
    elif case == 'overflow_threshold': prediction = 'p99 decrease by >' + '9' * 400 + '%'
    monkeypatch.setattr(module.fact_ledger, 'load', AsyncMock(side_effect=lambda key: facts[key]))
    hid = await temp_hypothesis_ledger.add(Hypothesis(hypothesis_id='', source_execution_id='old',
        module='api', version='v1', test_type=test_type, statement='prediction',
        category=HypothesisCategory.RECOMMENDATION, testable_prediction=prediction))
    hyp = await temp_hypothesis_ledger.evaluate_against_facts(hid, 'new')
    assert hyp.status == HypothesisStatus.UNTESTED
    assert hyp.verified_by_execution_id is None and hyp.refuted_by_execution_id is None


@pytest.mark.parametrize('status', [HypothesisStatus.CONFIRMED, HypothesisStatus.REFUTED])
def test_prompt_preserves_prediction_verification_scope_and_execution(status):
    hyp = Hypothesis(hypothesis_id='h', source_execution_id='source', module='api',
        category=HypothesisCategory.RECOMMENDATION, statement='pool changes improve latency', status=status,
        testable_prediction='p99 decrease', verified_by_execution_id='verified-run' if status == HypothesisStatus.CONFIRMED else None,
        refuted_by_execution_id='refuted-run' if status == HypothesisStatus.REFUTED else None,
        verification_notes='Only the metric prediction was checked; no intervention was verified.')
    prompt = HypothesisLedger.format_for_prompt([hyp])
    assert hyp.verification_notes in prompt and hyp.testable_prediction in prompt
    assert ('verified-run' if status == HypothesisStatus.CONFIRMED else 'refuted-run') in prompt
    assert '已被实验确认的结论' not in prompt


@pytest.mark.asyncio
async def test_add_and_get_hypothesis(temp_hypothesis_ledger):
    hyp = Hypothesis(
        hypothesis_id="",
        source_execution_id="exec-001",
        module="api",
        version="v1",
        category=HypothesisCategory.BOTTLENECK,
        statement="瓶颈在数据库连接池",
        confidence=0.6,
        status=HypothesisStatus.UNTESTED,
        supporting_facts=["avg_response_time", "error_rate"],
        testable_prediction="把连接池翻倍后 p99 应下降 >20%",
        falsification_design="调整连接池大小后重测同一接口",
    )

    hid = await temp_hypothesis_ledger.add(hyp)
    assert hid

    loaded = await temp_hypothesis_ledger.get(hid)
    assert loaded is not None
    assert loaded.statement == "瓶颈在数据库连接池"
    assert loaded.status == HypothesisStatus.UNTESTED
    assert loaded.supporting_facts == ["avg_response_time", "error_rate"]


@pytest.mark.asyncio
async def test_hypothesis_cannot_verify_itself(temp_hypothesis_ledger, monkeypatch):
    from app.services.ai.memory import hypothesis_ledger as singleton
    from unittest.mock import AsyncMock
    import importlib
    module = importlib.import_module('app.services.ai.memory.hypothesis_ledger')
    fact = type('Fact', (), dict(module='api', version='v1', error_rate=0))()
    monkeypatch.setattr(module.fact_ledger, 'load', AsyncMock(return_value=fact))
    hyp = Hypothesis(hypothesis_id='', source_execution_id='same-run', module='api', version='v1',
                     statement='error rate prediction', testable_prediction='error_rate below 1%')
    hid = await temp_hypothesis_ledger.add(hyp)
    assert (await temp_hypothesis_ledger.evaluate_against_facts(hid, 'same-run')).status == HypothesisStatus.UNTESTED


@pytest.mark.asyncio
@pytest.mark.parametrize('prediction,old,new,scope', [
    ('p99 should decrease by >20%', 100, 99, 'same'),
    ('p99 should decrease', 100, 50, 'different'),
])
async def test_hypothesis_respects_threshold_and_context(temp_hypothesis_ledger, monkeypatch, prediction, old, new, scope):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import importlib
    module = importlib.import_module('app.services.ai.memory.hypothesis_ledger')
    facts = {key: SimpleNamespace(module='api', version='v1', context_scope=context, test_type='baseline',
                                  total_requests=10, percentiles={'p99': value})
             for key, context, value in [('old', 'same', old), ('new', scope, new)]}
    monkeypatch.setattr(module.fact_ledger, 'load', AsyncMock(side_effect=lambda key: facts[key]))
    hyp = Hypothesis(hypothesis_id='', source_execution_id='old', module='api', version='v1',
                     statement='testable prediction', category=HypothesisCategory.RECOMMENDATION,
                     testable_prediction=prediction)
    hid = await temp_hypothesis_ledger.add(hyp)
    result = await temp_hypothesis_ledger.evaluate_against_facts(hid, 'new')
    assert result.status == (HypothesisStatus.REFUTED if scope == 'same' else HypothesisStatus.UNTESTED)


@pytest.mark.asyncio
async def test_hypothesis_status_transition(temp_hypothesis_ledger):
    hyp = Hypothesis(
        hypothesis_id="",
        source_execution_id="exec-001",
        module="api",
        version="v1",
        category=HypothesisCategory.ROOT_CAUSE,
        statement="根因是锁竞争",
    )
    hid = await temp_hypothesis_ledger.add(hyp)

    await temp_hypothesis_ledger.update_status(
        hid, HypothesisStatus.CONFIRMED, verified_by="exec-002", notes="retest passed"
    )
    loaded = await temp_hypothesis_ledger.get(hid)
    assert loaded.status == HypothesisStatus.CONFIRMED
    assert loaded.verified_by_execution_id == "exec-002"


@pytest.mark.asyncio
async def test_evaluate_hypothesis_against_new_fact(
    temp_hypothesis_ledger, temp_fact_ledger, sample_jtl
):
    # Use the module-level fact_ledger singleton for hypothesis evaluation
    from app.services.ai.memory import hypothesis_ledger as hl
    from app.services.ai.memory import fact_ledger as fl

    # Save original paths and restore after test
    original_hl_db = hl.db_path
    original_fl_db = fl.db_path
    hl.db_path = temp_hypothesis_ledger.db_path
    fl.db_path = temp_fact_ledger.db_path

    try:
        # First execution: p99 ~250ms
        fact1 = fl.compute_from_jtl(
            sample_jtl, execution_id="exec-old", module="api", version="v1"
        )
        await fl.save(fact1)

        hyp = Hypothesis(
            hypothesis_id="",
            source_execution_id="exec-old",
            module="api",
            version="v1",
            category=HypothesisCategory.RECOMMENDATION,
            statement="增加连接池后 p99 会下降",
            testable_prediction="p99 should decrease",
        )
        hid = await hl.add(hyp)

        # Second execution with lower p99 (simulate improvement)
        # We reuse the same JTL for the test, but manually tweak the fact
        fact2 = fact1.model_copy()
        fact2.execution_id = "exec-new"
        fact2.percentiles = {**fact1.percentiles, "p99": fact1.percentiles["p99"] * 0.5}
        await fl.save(fact2)

        updated = await hl.evaluate_against_facts(hid, "exec-new")
        assert updated.status == HypothesisStatus.CONFIRMED
        assert updated.verified_by_execution_id == "exec-new"
    finally:
        await fl.close()
        await hl.close()
        hl.db_path = original_hl_db
        fl.db_path = original_fl_db


@pytest.mark.asyncio
async def test_prompt_formatting(temp_hypothesis_ledger):
    for status, stmt in [
        (HypothesisStatus.CONFIRMED, "已确认的结论"),
        (HypothesisStatus.UNTESTED, "未验证假设"),
        (HypothesisStatus.REFUTED, "已被证伪"),
    ]:
        hyp = Hypothesis(
            hypothesis_id="",
            source_execution_id="exec-001",
            module="api",
            version="v1",
            category=HypothesisCategory.BOTTLENECK,
            statement=stmt,
            status=status,
        )
        await temp_hypothesis_ledger.add(hyp)

    hyps = await temp_hypothesis_ledger.list_for_module("api")
    prompt = HypothesisLedger.format_for_prompt(hyps)

    assert "已记录的确认结果" in prompt and "不代表因果已证实" in prompt
    assert "未验证假设" in prompt
    assert "已记录的证伪结果" in prompt
