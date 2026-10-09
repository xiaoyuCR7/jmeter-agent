"""Opt-in receipt adapters; unkeyed Agent routes retain their original behavior."""
import asyncio
import json
from contextlib import aclosing

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from app.services.ai import analysis_receipts as receipts_module
from app.services.ai.analysis_receipts import ReceiptError, metadata, result_payload
from app.services.ai.agent.types import AgentEvent, AgentResult
from app.services.ai.agents.analysis_agent import AnalysisExecutionError


def _event(event):
    return f'data: {json.dumps(event.__dict__, ensure_ascii=False, allow_nan=False)}\n\n'


def _done(payload, receipt):
    return AgentEvent('done', 'Analysis receipt available', dict(
        success=payload['success'], output=payload['output'], iterations=payload['iterations'],
        error=payload['error'], error_traceback=None, receipt=metadata(receipt),
        **({'analysis': payload['analysis']} if 'analysis' in payload else {})))


async def get_receipt(request_id):
    try:
        receipt = await receipts_module.analysis_receipts.get(request_id)
        return JSONResponse(content=receipt, headers={'Cache-Control': 'no-store'})
    except ReceiptError as error:
        raise HTTPException(error.status_code, str(error)) from error


async def agent_run_keyed(request, agent_type, adapter_factory, *, stream=False):
    service = receipts_module.analysis_receipts
    try:
        handle, receipt = await service.claim(request)
    except ReceiptError as error:
        raise HTTPException(error.status_code, str(error)) from error
    if handle is None:
        payload = receipt['result']
        if payload is None:
            return JSONResponse(status_code=202 if receipt['state'] == 'running' else 409,
                                content={'receipt': receipt})
        if stream:
            async def replay():
                yield _event(_done(payload, receipt))
            return StreamingResponse(replay(), media_type='text/event-stream')
        return {**payload, 'task': request.task, 'receipt': metadata(receipt)}

    # A new key is reserved before preflight. Failure never releases it for replay.
    try:
        evidence = await agent_type.resolve_execution(request.execution_id)
        agent = agent_type(adapter_factory.create(request.model), session_id=request.session_id)
    except BaseException as error:
        try:
            await service.abandon(handle, 'cancelled' if isinstance(error, asyncio.CancelledError) else 'failed')
        except ReceiptError as storage_error:
            if isinstance(error, asyncio.CancelledError):
                raise error
            raise HTTPException(storage_error.status_code, str(storage_error)) from storage_error
        if isinstance(error, asyncio.CancelledError):
            raise
        if isinstance(error, AnalysisExecutionError):
            raise HTTPException(error.status_code, str(error)) from error
        raise HTTPException(400 if isinstance(error, ValueError) else 500, 'Analysis initialization failed') from error

    async def operation():
        return await service.run(handle, lambda: agent.run_analysis(request.task, evidence, context=request.context))

    if not stream:
        try:
            result = await operation()
            return {**result_payload(result), 'task': request.task, 'receipt': result.receipt}
        except ReceiptError as error:
            raise HTTPException(error.status_code, str(error)) from error
        except Exception as error:
            raise HTTPException(500, 'Analysis request interrupted; inspect its receipt before taking further action') from error

    async def generate():
        try:
            yield _event(AgentEvent('analysis_receipt', 'Analysis request reserved', metadata(receipt)))
            async with aclosing(agent._stream_operation(operation)) as events:
                async for event in events:
                    if event.type == 'complete':
                        # Model/archive completion is not yet durable receipt completion.
                        event = AgentEvent('analysis_ready', 'Saving final analysis receipt')
                    if event.type == 'done' and 'receipt' not in (event.data or {}):
                        # _stream_operation can reject a busy Session before invoking operation.
                        async def busy_result():
                            return AgentResult(False, request.task, error=(event.data or {}).get('error', 'Agent busy'))
                        result = await service.run(handle, busy_result)
                        event = _done(result_payload(result), result.receipt)
                    yield _event(event)
        except ReceiptError as error:
            yield _event(AgentEvent('error', str(error), {'request_id': request.request_id,
                'error_code': 'receipt_unavailable'}))
        except Exception:
            yield _event(AgentEvent('error', 'Analysis interrupted; query its receipt; do not retry automatically',
                                   {'request_id': request.request_id, 'error_code': 'receipt_outcome_unknown'}))
        finally:
            # Handles a disconnect before the runner starts as well as stream cancellation.
            if handle in service._active:
                try:
                    await service.abandon(handle, 'cancelled')
                except ReceiptError:
                    pass
    return StreamingResponse(generate(), media_type='text/event-stream')
