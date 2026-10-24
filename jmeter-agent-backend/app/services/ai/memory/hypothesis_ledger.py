"""Hypothesis Ledger — interpretations are stored as falsifiable hypotheses, not facts.

Each interpretation (bottleneck, root cause, recommendation) is a hypothesis with:
- source execution
- status: untested / confirmed / refuted / deprecated
- confidence
- supporting facts (metric field references)
- testable prediction + falsification design

Subsequent executions automatically evaluate predictions and update status.
"""

import os
import uuid
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional
from fastapi import HTTPException

from app.core.config import settings
from app.models.schemas import (
    Hypothesis, HypothesisStatus, HypothesisCategory, FactSheet,
)
from app.services.ai.memory.fact_ledger import fact_ledger
from app.services.ai.memory.read_errors import ledger_read_boundary

logger = logging.getLogger(__name__)


class HypothesisUpdateConflict(RuntimeError):
    """The hypothesis changed after the caller read it; no update was applied."""


class HypothesisLedger:
    """Stores and evaluates hypotheses."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.path.join(settings.BASE_DIR, "hypotheses.db")
        self.db_path = db_path
        self._db_conn = None  # Persistent connection
        # Lazy lock creation: creating asyncio.Lock at import time (no running loop)
        # on Windows can cause hangs with aiosqlite. The lock is created on first use.
        self._db_lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._db_lock is None:
            self._db_lock = asyncio.Lock()
        return self._db_lock

    async def _get_db(self):
        """Return persistent aiosqlite connection."""
        if self._db_conn is not None:
            return self._db_conn
        import aiosqlite
        db = await aiosqlite.connect(self.db_path)
        try:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
        except (Exception, asyncio.CancelledError):
            try:
                await db.close()
            except Exception:
                logger.warning('Failed to close uninitialized hypothesis connection')
            raise
        self._db_conn = db
        return db

    async def close(self):
        """Close the persistent database connection (safe to call multiple times)."""
        if self._db_conn is not None:
            try:
                await self._db_conn.close()
            except Exception:
                pass
            self._db_conn = None

    async def _ensure_table(self):
        async with self._get_lock():
            db = await self._get_db()
            await db.execute("""
                CREATE TABLE IF NOT EXISTS hypotheses (
                    hypothesis_id TEXT PRIMARY KEY,
                    source_execution_id TEXT NOT NULL,
                    module TEXT NOT NULL,
                    version TEXT,
                    test_type TEXT,
                    category TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    status TEXT NOT NULL DEFAULT 'untested',
                    supporting_facts TEXT NOT NULL DEFAULT '[]',
                    testable_prediction TEXT,
                    falsification_design TEXT,
                    verified_by_execution_id TEXT,
                    refuted_by_execution_id TEXT,
                    verification_notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_hyp_module_version
                ON hypotheses(module, version)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_hyp_status
                ON hypotheses(status)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_hyp_source_exec
                ON hypotheses(source_execution_id)
            """)
            await db.commit()

    @staticmethod
    def _row_to_hypothesis(row) -> Hypothesis:
        return Hypothesis(
            hypothesis_id=row[0],
            source_execution_id=row[1],
            module=row[2],
            version=row[3] or None,
            test_type=row[4] or None,
            category=HypothesisCategory(row[5]),
            statement=row[6],
            confidence=row[7],
            status=HypothesisStatus(row[8]),
            supporting_facts=json.loads(row[9]) if row[9] else [],
            testable_prediction=row[10] or None,
            falsification_design=row[11] or None,
            verified_by_execution_id=row[12] or None,
            refuted_by_execution_id=row[13] or None,
            verification_notes=row[14] or None,
            created_at=datetime.fromisoformat(row[15]),
            updated_at=datetime.fromisoformat(row[16]),
        )

    async def add(
        self,
        hypothesis: Hypothesis,
        *,
        if_absent: bool = False,
    ) -> str:
        """Add a hypothesis; optionally preserve an existing ID without replacing it."""
        if not hypothesis.hypothesis_id:
            hypothesis.hypothesis_id = str(uuid.uuid4())[:8]
        hypothesis_id = hypothesis.hypothesis_id
        now = datetime.now().isoformat()
        # Capture mutable input before initialization, lock, or commit can suspend.
        values = (
            hypothesis_id, hypothesis.source_execution_id, hypothesis.module,
            hypothesis.version, hypothesis.test_type, hypothesis.category.value,
            hypothesis.statement, hypothesis.confidence, hypothesis.status.value,
            json.dumps(hypothesis.supporting_facts, ensure_ascii=False),
            hypothesis.testable_prediction, hypothesis.falsification_design,
            hypothesis.verified_by_execution_id, hypothesis.refuted_by_execution_id,
            hypothesis.verification_notes, now, now,
        )
        conflict_clause = ' ON CONFLICT(hypothesis_id) DO NOTHING' if if_absent else ''
        await self._ensure_table()

        async with self._get_lock():
            db = await self._get_db()
            try:
                cursor = await db.execute(
                    'SELECT source_execution_id FROM hypotheses WHERE hypothesis_id = ?', (hypothesis_id,))
                existing = await cursor.fetchone()
                if existing and existing[0] != values[1]:
                    raise HTTPException(403, 'Hypothesis source identity cannot be changed')
                await db.execute(
                    """INSERT INTO hypotheses
                       (hypothesis_id, source_execution_id, module, version, test_type,
                        category, statement, confidence, status, supporting_facts,
                        testable_prediction, falsification_design,
                        verified_by_execution_id, refuted_by_execution_id,
                        verification_notes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""" + conflict_clause,
                    values,
                )
                await db.commit()
                return hypothesis_id
            except (Exception, asyncio.CancelledError) as e:
                logger.error(f"Failed to add hypothesis: {e}")
                try:
                    await db.rollback()
                except Exception:
                    await self.close()
                raise

    async def update_status(
        self,
        hypothesis_id: str,
        status: HypothesisStatus,
        verified_by: Optional[str] = None,
        refuted_by: Optional[str] = None,
        notes: Optional[str] = None,
        *,
        expected_status: Optional[HypothesisStatus] = None,
        expected_updated_at: Optional[datetime] = None,
    ) -> bool:
        """Update status; optional read-version guards reject stale decisions."""
        await self._ensure_table()

        async with self._get_lock():
            db = await self._get_db()
            try:
                fields = ["status = ?", "updated_at = ?"]
                params = [status.value, datetime.now().isoformat()]

                if status == HypothesisStatus.CONFIRMED:
                    fields.extend([
                        "verified_by_execution_id = COALESCE(?, CASE WHEN status = ? THEN verified_by_execution_id END)",
                        "refuted_by_execution_id = NULL",
                    ])
                    params.extend([verified_by, status.value])
                elif status == HypothesisStatus.REFUTED:
                    fields.extend([
                        "refuted_by_execution_id = COALESCE(?, CASE WHEN status = ? THEN refuted_by_execution_id END)",
                        "verified_by_execution_id = NULL",
                    ])
                    params.extend([refuted_by, status.value])
                elif status == HypothesisStatus.UNTESTED:
                    fields.extend(["verified_by_execution_id = NULL", "refuted_by_execution_id = NULL"])
                # A new state must not inherit the previous state's explanation.
                # Deprecation keeps the old evidence for historical reference.
                if notes is not None:
                    fields.append("verification_notes = ?")
                    params.append(notes)
                elif status != HypothesisStatus.DEPRECATED:
                    fields.append("verification_notes = CASE WHEN status = ? THEN verification_notes END")
                    params.append(status.value)

                params.append(hypothesis_id)
                where = 'hypothesis_id = ?'
                if expected_status is not None:
                    where += ' AND status = ?'
                    params.append(expected_status.value)
                if expected_updated_at is not None:
                    where += ' AND updated_at = ?'
                    params.append(expected_updated_at.isoformat())

                cursor = await db.execute(
                    f"UPDATE hypotheses SET {', '.join(fields)} WHERE {where}",
                    tuple(params),
                )
                await db.commit()
                if cursor.rowcount == 0 and (expected_status is not None or expected_updated_at is not None):
                    raise HypothesisUpdateConflict(hypothesis_id)
                return cursor.rowcount == 1
            except HypothesisUpdateConflict:
                raise
            except HTTPException:
                raise
            except (Exception, asyncio.CancelledError) as e:
                logger.error(f"Failed to update hypothesis {hypothesis_id}: {e}")
                try:
                    await db.rollback()
                except Exception:
                    # Do not reuse a connection with an unacknowledged write.
                    await self.close()
                if isinstance(e, asyncio.CancelledError):
                    raise
                return False

    async def get(self, hypothesis_id: str, *, strict: bool = False) -> Optional[Hypothesis]:
        """Get a single hypothesis by ID."""
        with ledger_read_boundary(strict, 'hypothesis', 'get'):
            await self._ensure_table()

            async with self._get_lock():
                db = await self._get_db()
                try:
                    cursor = await db.execute(
                        "SELECT * FROM hypotheses WHERE hypothesis_id = ?",
                        (hypothesis_id,),
                    )
                    row = await cursor.fetchone()
                    try:
                        return self._row_to_hypothesis(row) if row else None
                    except (ValueError, TypeError) as error:
                        if not strict:
                            raise
                        logger.warning('Skipping malformed hypothesis row: %s', type(error).__name__)
                        return None
                except HTTPException:
                    raise
                except Exception as e:
                    if strict:
                        raise
                    logger.error(f"Failed to get hypothesis {hypothesis_id}: {e}")
                    return None

    async def list_for_module(
        self,
        module: str,
        version: Optional[str] = None,
        status: Optional[HypothesisStatus] = None,
        limit: int = 50,
        source_execution_ids: Optional[list[str]] = None,
        *, strict: bool = False,
    ) -> list[Hypothesis]:
        """List hypotheses for a module/version, optionally filtered by status."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError('limit must be a non-negative integer')
        if limit == 0 or (source_execution_ids is not None and not source_execution_ids):
            return []
        with ledger_read_boundary(strict, 'hypothesis', 'history'):
            await self._ensure_table()

            async with self._get_lock():
                db = await self._get_db()
                try:
                    query = "SELECT * FROM hypotheses WHERE module = ?"
                    params = [module]
                    if version:
                        query += " AND version = ?"
                        params.append(version)
                    if status:
                        query += " AND status = ?"
                        params.append(status.value)
                    if source_execution_ids is not None:
                        query += ' AND source_execution_id IN (' + ','.join('?' for _ in source_execution_ids) + ')'
                        params.extend(source_execution_ids)
                    query += " ORDER BY updated_at DESC, hypothesis_id DESC"
                    hypotheses = []
                    async with db.execute(query, tuple(params)) as cursor:
                        async for row in cursor:
                            try:
                                hyp = self._row_to_hypothesis(row)
                            except (ValueError, TypeError) as error:
                                logger.warning('Skipping malformed hypothesis row: %s', type(error).__name__)
                                continue
                            hypotheses.append(hyp)
                            if len(hypotheses) == limit:
                                break
                    return hypotheses
                except HTTPException:
                    raise
                except Exception as e:
                    if strict:
                        raise
                    logger.error(f"Failed to list hypotheses for {module}: {e}")
                    return []

    async def evaluate_against_facts(
        self,
        hypothesis_id: str,
        new_execution_id: str,
        *, strict: bool = False,
    ) -> Optional[Hypothesis]:
        """Evaluate a hypothesis against a new execution's FactSheet.

        This is intentionally simple for now: if the new fact is for the same
        module/version and the hypothesis has a testable prediction, we check
        whether the prediction's direction holds.

        In the future, this can call an LLM to interpret the prediction against
        the new facts, but the goal is to keep the barrier high for confirming
        a hypothesis.
        """
        read_options = {'strict': True} if strict else {}
        hyp = await self.get(hypothesis_id, **read_options)
        if not hyp or hyp.status != HypothesisStatus.UNTESTED or not hyp.testable_prediction:
            return hyp
        if hyp.source_execution_id == new_execution_id:
            return hyp

        new_fact = await fact_ledger.load(new_execution_id, **read_options)
        if not new_fact:
            return hyp

        # Only evaluate if same module/version
        if hyp.module != new_fact.module or (hyp.version and hyp.version != new_fact.version):
            return hyp

        prev = await fact_ledger.load(hyp.source_execution_id, **read_options)
        if not prev or getattr(prev, 'context_scope', '') != getattr(new_fact, 'context_scope', ''):
            return hyp
        if prev.module != new_fact.module or prev.version != new_fact.version:
            return hyp
        if getattr(prev, 'test_type', None) != getattr(new_fact, 'test_type', None):
            return hyp
        if hyp.test_type and hyp.test_type != new_fact.test_type:
            return hyp
        if getattr(prev, 'total_requests', 0) <= 0 or getattr(new_fact, 'total_requests', 0) <= 0:
            return hyp  # Default zero metrics are not measurements from a test.
        # Matching a metric cannot establish a root cause or an intervention.
        if hyp.category in (HypothesisCategory.ROOT_CAUSE, HypothesisCategory.BOTTLENECK):
            return hyp

        # Simple keyword-based checks on the prediction
        prediction = hyp.testable_prediction.lower()
        import re
        import math
        directional = re.fullmatch(
            r'(p99|throughput|tps|吞吐量)\s*(?:should\s*)?(decrease|下降|降低|increase|提升|增加)'
            r'(?:\s*(?:by\s*)?(>=|>|至少|超过)?\s*(\d+(?:\.\d+)?)%)?', prediction.strip())
        error_bound = re.fullmatch(
            r'(?:error_rate|错误率)\s*(?:should\s*)?(?:drop\s*)?(?:below|低于)\s*(\d+(?:\.\d+)?)%',
            prediction.strip())
        if not directional and not error_bound:
            return hyp  # Unsupported/conditional predictions require an explicit experiment.
        notes = f"Evaluated against execution {new_execution_id}. "

        confirmed = None
        # Examples of supported simple predictions:
        # - "p99 should decrease by >20%"
        # - "error_rate should drop below 1%"
        # - "throughput should increase"
        if directional:
            # Need a previous fact to compare
            metric, direction, operator, threshold = directional.groups()
            old_value = prev.percentiles.get('p99') if metric == 'p99' else prev.throughput
            new_value = new_fact.percentiles.get('p99') if metric == 'p99' else new_fact.throughput
            if (old_value is None or new_value is None or old_value <= 0 or new_value < 0
                    or not math.isfinite(old_value) or not math.isfinite(new_value)):
                return hyp
            delta = (new_value - old_value) / old_value * 100
            if direction in ('decrease', '下降', '降低'):
                delta = -delta
            required = float(threshold or 0)
            if not math.isfinite(delta) or not math.isfinite(required):
                return hyp
            confirmed = delta >= required if operator in ('>=', '至少') else delta > required
            notes += f'Only the metric prediction was checked: {metric} {old_value} -> {new_value}; change={delta:.3f}%.'

        elif error_bound and math.isfinite(new_fact.error_rate) and 0 <= new_fact.error_rate <= 100:
            threshold = float(error_bound.group(1))
            if not 0 <= threshold <= 100:
                return hyp
            confirmed = new_fact.error_rate < threshold
            notes += f'Only the metric prediction was checked: error_rate={new_fact.error_rate}%, threshold={threshold}%.'

        if confirmed is None:
            return hyp
        try:
            saved = await self.update_status(
                hypothesis_id,
                HypothesisStatus.CONFIRMED if confirmed else HypothesisStatus.REFUTED,
                verified_by=new_execution_id if confirmed else None,
                refuted_by=None if confirmed else new_execution_id,
                notes=notes,
                expected_status=HypothesisStatus.UNTESTED,
                expected_updated_at=hyp.updated_at,
            )
        except HypothesisUpdateConflict:
            logger.info('Skipped stale evaluation for hypothesis %s', hypothesis_id)
            return await self.get(hypothesis_id, **read_options)
        if not saved:
            raise RuntimeError(f'Failed to persist hypothesis evaluation for {hypothesis_id}')

        return await self.get(hypothesis_id, **read_options)

    async def evaluate_module_hypotheses(
        self,
        module: str,
        new_execution_id: str,
        *, strict: bool = False,
    ) -> list[Hypothesis]:
        """Evaluate all untested hypotheses for a module against a new execution."""
        read_options = {'strict': True} if strict else {}
        untested = await self.list_for_module(module, status=HypothesisStatus.UNTESTED, **read_options)
        results = []
        for hyp in untested:
            updated = await self.evaluate_against_facts(hyp.hypothesis_id, new_execution_id, **read_options)
            if updated:
                results.append(updated)
        return results

    @staticmethod
    def format_for_prompt(
        hypotheses: list[Hypothesis],
        confirmed_limit: int = 5,
        untested_limit: int = 5,
    ) -> str:
        """Format hypotheses into a prompt context, separating confirmed vs untested."""
        confirmed = [h for h in hypotheses if h.status == HypothesisStatus.CONFIRMED][:confirmed_limit]
        untested = [h for h in hypotheses if h.status == HypothesisStatus.UNTESTED][:untested_limit]
        refuted = [h for h in hypotheses if h.status == HypothesisStatus.REFUTED][:3]

        parts = []

        if confirmed:
            parts.append("### 已记录的确认结果（以验证范围为准，不代表因果已证实）")
            for h in confirmed:
                parts.append(f"- [{h.category.value}] {h.statement} (置信度: {h.confidence:.0%}, 来源: {h.source_execution_id})")
                parts.append(f"  · 验证执行: {h.verified_by_execution_id or '未记录'}；预测: {h.testable_prediction or '未记录'}")
                parts.append(f"  · 验证范围: {h.verification_notes or '未记录，不可视为已完成独立实验'}")
            parts.append("")

        if untested:
            parts.append("### 未验证假设（仅供参考，不可直接当作事实引用）")
            for h in untested:
                parts.append(f"- [{h.category.value}] {h.statement} (来源: {h.source_execution_id})")
                if h.testable_prediction:
                    parts.append(f"  · 可验证预测: {h.testable_prediction}")
            parts.append("")

        if refuted:
            parts.append("### 已记录的证伪结果（以验证范围为准，注意避免重复）")
            for h in refuted:
                parts.append(f"- [{h.category.value}] {h.statement} (证伪于: {h.refuted_by_execution_id})")
                parts.append(f"  · 预测: {h.testable_prediction or '未记录'}；验证范围: {h.verification_notes or '未记录'}")
            parts.append("")

        return "\n".join(parts) if parts else "（无相关假设）"


# Module singleton
hypothesis_ledger = HypothesisLedger()
