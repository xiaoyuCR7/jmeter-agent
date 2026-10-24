"""Shared in-memory storage singleton.

Acts as a synchronized cache over the database-backed DBStore.
- All writes go through DBStore (persistent) first, then sync to this cache.
- All reads come from this cache for speed.
- On startup, cache is loaded from DBStore.

Used by both REST API routes and agent tools.
"""

import threading
import logging
from collections.abc import MutableMapping
from copy import deepcopy
from typing import Dict, Any, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


class _ProjectView(MutableMapping):
    """A fresh authorization check for every operation, including retained views."""
    def __init__(self, records, projects=False, on_change=None):
        self.records = records
        self.projects = projects
        self.on_change = on_change

    def _owner(self, key, value):
        return key if self.projects else (value.get('project_id') if isinstance(value, dict)
                                         else getattr(value, 'project_id', None))

    def __getitem__(self, key):
        return deepcopy(self.records[key])

    def __iter__(self):
        return iter(tuple(self.records))

    def __len__(self):
        return sum(1 for _ in self)

    def __setitem__(self, key, value):
        owner = self._owner(key, value)
        if key in self.records:
            old_owner = self._owner(key, self.records[key])
            if old_owner != owner:
                raise HTTPException(409, 'Resource project cannot be changed')
        self.records[key] = deepcopy(value)
        if self.on_change:
            self.on_change()

    def __delitem__(self, key):
        del self.records[key]
        if self.on_change:
            self.on_change()


def _view(records, *, projects=False, on_change=None):
    return records if True else _ProjectView(records, projects, on_change)


class SharedStore:
    """Thread-safe singleton cache synchronized with DBStore."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._projects: Dict[str, Any] = {}
        self._test_plans: Dict[str, Any] = {}
        self._executions: Dict[str, Any] = {}
        self._db_synced: bool = False
        self._revision: int = -1
        self._cache_lock = threading.RLock()
        self._initialized = True

    @property
    def db_synced(self) -> bool:
        return self._db_synced

    def _dirty(self):
        self._revision = -1

    # ── Projects ──────────────────────────────────────────

    @property
    def projects(self) -> Dict[str, Any]:
        return _view(self._projects, projects=True, on_change=self._dirty)

    def get_project(self, project_id: str):
        return self.projects.get(project_id)

    def set_project(self, project_id: str, project):
        self.projects[project_id] = project
        self._revision = -1

    def delete_project(self, project_id: str) -> bool:
        if project_id in self.projects:
            del self.projects[project_id]
            self._revision = -1
            return True
        return False

    def has_project(self, project_id: str) -> bool:
        return project_id in self.projects

    # ── Test Plans ────────────────────────────────────────

    @property
    def test_plans(self) -> Dict[str, Any]:
        return _view(self._test_plans, on_change=self._dirty)

    def get_test_plan(self, plan_id: str):
        return self.test_plans.get(plan_id)

    def set_test_plan(self, plan_id: str, plan):
        self.test_plans[plan_id] = plan
        self._revision = -1

    def delete_test_plan(self, plan_id: str) -> bool:
        if plan_id in self.test_plans:
            del self.test_plans[plan_id]
            self._revision = -1
            return True
        return False

    # ── Executions ────────────────────────────────────────

    @property
    def executions(self) -> Dict[str, Any]:
        return _view(self._executions, on_change=self._dirty)

    def get_execution(self, execution_id: str):
        return self.executions.get(execution_id)

    def set_execution(self, execution_id: str, execution):
        self.executions[execution_id] = execution
        self._revision = -1

    # ── DB sync ───────────────────────────────────────────

    async def _database_revision(self):
        from app.models import db
        async with db.get_session_factory()() as session:
            row = await session.get(db.BusinessDataRevisionModel, 1)
            if row is None or type(row.revision) is not int or row.revision < 0:
                raise RuntimeError('Business cache revision is unavailable')
            return row.revision

    @staticmethod
    async def _all_pages(method, key):
        rows, offset, total = [], 0, None
        while total is None or offset < total:
            page = await method(offset=offset, limit=1000)
            batch = page.get(key, [])
            total = page.get('total')
            if not isinstance(total, int) or total < 0 or not isinstance(batch, list):
                raise RuntimeError('Business cache snapshot is malformed')
            if not batch and offset < total:
                raise RuntimeError('Business cache snapshot ended before its declared total')
            rows.extend(batch)
            offset += len(batch)
        return rows

    @staticmethod
    def _project_value(value):
        from app.models.schemas import ProjectInfo, ProjectEnvironment
        environment = value.get('environment_data') or {}
        return ProjectInfo(
            id=value['id'], name=value['name'], description=value.get('description', ''),
            environment=ProjectEnvironment(
                base_url=value.get('base_url', environment.get('base_url', '')),
                variables=environment.get('variables', {}), headers=environment.get('headers', {})),
            environments=value.get('environments_data'), created_at=value.get('created_at'),
            updated_at=value.get('updated_at'), storage_version=value.get('storage_version', 1))

    @staticmethod
    def _plan_value(value):
        from app.models.schemas import TestPlanInfo, TestConfig, TestType
        config = value.get('test_config') or {}
        return TestPlanInfo(
            id=value['id'], project_id=value.get('project_id', 'default'), name=value['name'],
            description=value.get('description', ''), jmx_path=value.get('jmx_path', ''),
            created_at=value.get('created_at'),
            test_config=TestConfig(**{'test_type': TestType(config.get('test_type', 'baseline')), **config}),
            endpoints_count=value.get('endpoints_count', 0),
            storage_version=value.get('storage_version', 1))

    @staticmethod
    def _execution_value(value):
        from app.models.schemas import (
            DetailedTestSummary, ReportAnalysis, TestExecutionInfo, TestExecutionStatus,
        )
        result = TestExecutionInfo(
            id=value['id'], project_id=value.get('project_id', 'default'),
            test_plan_id=value.get('test_plan_id'),
            status=TestExecutionStatus(value.get('status', 'pending')),
            jmx_path=value.get('jmx_path', ''), start_time=value.get('start_time'),
            end_time=value.get('end_time'), report_dir=value.get('report_dir', ''),
            log_path=value.get('log_path', ''), error_message=value.get('error_message'),
            source_jmx_path=value.get('source_jmx_path'), source_sha256=value.get('source_sha256'),
            context_snapshot=value.get('context_snapshot'),
            report_status=value.get('report_status') or 'pending',
            report_error=value.get('report_error'),
            resource_snapshot_pre=value.get('resource_snapshot_pre'),
            resource_snapshot_post=value.get('resource_snapshot_post'))
        result.storage_version = value.get('storage_version', 1)
        if value.get('ai_analysis_data'):
            result.ai_analysis = ReportAnalysis(**value['ai_analysis_data'])
        if value.get('summary_data'):
            result.summary = DetailedTestSummary(**value['summary_data'])
        return result

    async def refresh_if_stale(self, *, force=False):
        """Replace one complete snapshot; retry if a writer commits during loading."""
        from app.services.db_store import db_store
        for _ in range(3):
            revision = await self._database_revision()
            if not force and self._db_synced and revision == self._revision:
                return False
            projects = await self._all_pages(db_store.list_projects, 'projects')
            plans = await self._all_pages(db_store.list_test_plans, 'test_plans')
            executions = await self._all_pages(db_store.list_executions, 'executions')
            if await self._database_revision() != revision:
                force = True
                continue
            new_projects = {item['id']: self._project_value(item) for item in projects}
            new_plans = {item['id']: self._plan_value(item) for item in plans}
            new_executions = {item['id']: self._execution_value(item) for item in executions}
            with self._cache_lock:
                self._projects = new_projects
                self._test_plans = new_plans
                self._executions = new_executions
                self._revision = revision
                self._db_synced = True
            logger.info('Loaded authoritative business snapshot at revision %s', revision)
            return True
        raise RuntimeError('Business data changed continuously while taking a cache snapshot')

    async def sync_from_dbstore(self):
        """Startup compatibility wrapper for an exact authoritative snapshot."""
        return await self.refresh_if_stale(force=True)

# Module-level singleton — import this everywhere
store = SharedStore()
