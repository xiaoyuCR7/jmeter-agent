"""EventAdapter — maps WorkflowAgent streaming events to frontend SSE format.

Frontend (AIChat.vue) expects events in this shape:
  {"step": "<phase>", "message": "...", "progress": <0-100>, "error": <bool>}

On completion:
  {"step": "complete", "message": "...", "progress": 100,
   "project_id": "...", "test_plan_ids": [...], "diagnostic_summary": "..."}

This adapter tracks phase transitions from AgentEvent stream and emits
the frontend-compatible format via async generator.
"""

import json
from typing import AsyncIterator, Optional

from app.services.ai.agent.types import AgentEvent


# Phase → (step_name, progress_base)
_PHASE_MAP = [
    ("planning",    "analyzing_doc",         10),
    ("plan_ready",  "extracting_info",       25),
    ("executing_1", "creating_project",      40),
    ("executing_2", "generating_test_plan",  60),
    ("executing_3", "generating_test_plan",  75),
    ("reflecting",  "diagnostic_summary",    90),
]


class EventAdapter:
    """Wraps a WorkflowAgent event stream and emits frontend SSE format.

    Usage:
        agent = WorkflowAgent(adapter)
        adapter = EventAdapter()
        async for sse_json in adapter.stream(agent.run_stream(task), result):
            yield f"data: {sse_json}\\n\\n"
    """

    def __init__(self):
        self._phase_index = 0
        self._tool_call_count = 0

    async def stream(
        self,
        events: AsyncIterator[AgentEvent],
        structured_result: Optional[dict] = None,
    ) -> AsyncIterator[str]:
        """Convert AgentEvent stream to frontend SSE JSON strings.

        Args:
            events: Async iterator of AgentEvent from agent.run_stream().
            structured_result: Dict with project_id, test_plan_ids, etc.
                Provided by WorkflowAgent.run_from_doc() after run completes.

        Yields:
            JSON strings in frontend SSE format.
        """
        async for event in events:
            if event.type == "done":
                # Final event — emit completion with structured data
                result = structured_result or {}
                step = "complete"
                if result.get("error"):
                    step = "error"
                yield json.dumps({
                    "step": step,
                    "message": result.get("output", event.message)[:200],
                    "progress": 100,
                    "project_id": result.get("project_id", ""),
                    "test_plan_ids": result.get("test_plan_ids", []),
                    "diagnostic_summary": result.get("diagnostic_summary", ""),
                    "error": step == "error",
                }, ensure_ascii=False)
                return

            # Map event type to frontend step
            phase_info = self._map_event(event)
            if phase_info:
                step_name, progress, message = phase_info
                yield json.dumps({
                    "step": step_name,
                    "message": message or event.message,
                    "progress": progress,
                }, ensure_ascii=False)

    def _map_event(self, event: AgentEvent) -> Optional[tuple[str, int, str]]:
        """Map an AgentEvent to (step_name, progress, message).

        Returns None if this event type should be skipped (no SSE emit).
        """
        etype = event.type

        if etype == "planning":
            self._phase_index = 0
            return ("analyzing_doc", 10, event.message)

        if etype == "plan_ready":
            self._phase_index = 1
            return ("extracting_info", 25, event.message)

        # Execution events: step_start, tool_call, tool_result
        if etype == "step_start":
            self._tool_call_count = 0
            data = event.data or {}
            step_id = data.get("step_id", 1)
            if step_id == 1:
                self._phase_index = 2
                return ("creating_project", 40, event.message)
            else:
                # Subsequent steps are test plan generation
                progress = min(55 + step_id * 12, 85)
                self._phase_index = 3
                return ("generating_test_plan", progress, event.message)

        if etype in ("tool_call", "tool_result"):
            self._tool_call_count += 1
            # Don't emit every tool call — too noisy. Emit on first tool_call per step.
            if etype == "tool_call" and self._tool_call_count == 1:
                data = event.data or {}
                tool_name = data.get("tool", "")
                if tool_name == "create_project":
                    return ("creating_project", 45, f"Creating project...")
                if tool_name in ("create_jmx", "generate_test_plan"):
                    return ("generating_test_plan", 65, f"Generating test plan...")
            return None  # Skip intermediate tool results to reduce noise

        if etype == "error":
            return ("error", self._current_progress(), event.message)

        # complete, reflect, react_done — handled by the "done" event
        return None

    def _current_progress(self) -> int:
        """Estimate current progress based on phase."""
        if self._phase_index < len(_PHASE_MAP):
            return _PHASE_MAP[self._phase_index][2]
        return 80
