"""Durable model request/response records without credentials or transport headers."""
import hashlib
import json
import re
from pathlib import Path

from . import db, provider, telemetry


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(db.uid()[:16] + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def request_identity(system, prompt, inputs):
    settings = db.get_settings()
    request = {"system": system, "prompt": prompt, "inputs": inputs,
               "model_settings": {key: settings.get(key) for key in ("base_url", "model", "temperature", "max_tokens")}}
    digest = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return digest, request


def diagnostic(job_id, mode, unit_key, stage, details):
    # IDs and modes are supplied by the application, never by a model document.
    safe = lambda value: "".join(c for c in str(value) if c.isalnum() or c in "-_")[:100]
    path = db.DATA / "diagnostics" / safe(mode) / safe(job_id) / f"{safe(unit_key)[:24]}-{safe(stage)[:24]}.json"
    write_json(path, {"created_at": db.now(), "unit_key": unit_key, **details})
    return str(path.relative_to(db.DATA))


@telemetry.model_call
def chat_json(job_id, mode, unit_key, system, prompt, inputs, cancel=None):
    """Cache only normal complete responses, then let callers fully validate them."""
    if cancel and cancel():
        raise provider.Cancelled("任务已取消")
    digest, request = request_identity(system, prompt, inputs)
    path = db.DATA / "cache" / "model-responses" / mode / (digest + ".json")
    cached = None
    if path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise provider.ProviderError("已保存模型响应缓存无法读取，请保留该文件并检查；未自动重复计费") from exc
    record = {"request_sha256": digest, "request": request}
    reusable = not cached or not cached.get("validation_failed") or cached.get("failed_in_job") == job_id
    if cached and reusable and cached.get("request_sha256") == digest and cached.get("request") == request and isinstance(cached.get("response"), dict):
        result = cached["response"]
        reference = diagnostic(job_id, mode, unit_key, digest[:12], {**record, "state": "cached_response", "response": result})
        return {**result, "_request_diagnostic": reference, "_request_sha256": digest, "_cached_response": True}
    diagnostic(job_id, mode, unit_key, digest[:12], {**record, "state": "request_prepared"})
    try:
        result = provider.chat_json(system, prompt, cancel=cancel)
    except (provider.OutputTruncatedError, provider.OutputInterruptedError) as exc:
        state = "truncated" if isinstance(exc, provider.OutputTruncatedError) else "interrupted"
        diagnostic(job_id, mode, unit_key, digest[:12], {**record, "state": state, "partial_text": exc.partial_text, "usage": exc.usage, "error": str(exc)})
        raise
    except Exception as exc:
        error = str(exc) if isinstance(exc, (provider.ProviderError, provider.Cancelled)) else type(exc).__name__
        diagnostic(job_id, mode, unit_key, digest[:12], {**record, "state": "failed", "error": error})
        raise
    write_json(path, {**record, "created_at": db.now(), "response": result})
    reference = diagnostic(job_id, mode, unit_key, digest[:12], {**record, "state": "response_received", "response": result})
    return {**result, "_request_diagnostic": reference, "_request_sha256": digest, "_cached_response": False}


def mark_invalid(job_id, mode, result, reason):
    """An explicit later job may refresh invalid output; its original audit stays."""
    digest = result.get("_request_sha256") if isinstance(result, dict) else None
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return
    telemetry.validation(job_id, mode, result, reason)
    path = db.DATA / "cache" / "model-responses" / mode / (digest + ".json")
    cached = json.loads(path.read_text(encoding="utf-8"))
    if cached.get("request_sha256") == digest:
        write_json(path, {**cached, "validation_failed": reason, "failed_in_job": job_id})


def read_response_for_repair(job_id, mode, unit_key, system, prompt, inputs):
    """Read an exact prior request, including invalid output, for full revalidation.

    This never calls the model or turns invalid output into an accepted result.
    A caller must validate it and, when needed, request bounded actual repairs.
    """
    digest, request = request_identity(system, prompt, inputs)
    path = db.DATA / "cache" / "model-responses" / mode / (digest + ".json")
    if not path.is_file():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise provider.ProviderError("已保存模型响应缓存无法读取，请保留文件并检查；未自动重复计费") from exc
    if cached.get("request_sha256") != digest or cached.get("request") != request or not isinstance(cached.get("response"), dict):
        return None
    result = cached["response"]
    reference = diagnostic(job_id, mode, unit_key, digest[:12], {"request_sha256": digest, "request": request,
        "state": "cached_response_for_revalidation", "response": result, "previous_validation_error": cached.get("validation_failed", "")})
    return {**result, "_request_diagnostic": reference, "_request_sha256": digest, "_cached_response": True}
