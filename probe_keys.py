"""
probe_keys.py - Enumerate every configured API key, list each provider's
models, and test each (key, model) pair with a tiny chat completion. Render
the results as ASCII tables so we know exactly which (key, model) combos work.

Run:
    conda activate cv_conda
    python probe_keys.py                  # default: Groq matrix + AEVE-routed OR/Gemini
    python probe_keys.py --openrouter-full  # also test EVERY OpenRouter model (slow)
    python probe_keys.py --google-full      # also test every Google generateContent model
    python probe_keys.py --concurrency 8    # raise per-provider concurrency

Output:
    - ASCII tables to stdout
    - JSON dump at output/key_probe_<UTC_TIMESTAMP>.json (full keys, gitignored dir)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from pipeline.llm_clients.registry import ROUTES  # noqa: E402

PING_PROMPT = "Reply with the single word: OK"
PING_MAX_TOKENS = 5
TIMEOUT_S = 30.0
DEFAULT_CONCURRENCY = 4

# Slugs that are referenced in the AEVE 2.0 routing table — flagged with "*".
AEVE_SLUGS_BY_PROVIDER: dict[str, set[str]] = {"groq": set(), "openrouter": set(), "gemini": set()}
for _role, _specs in ROUTES.items():
    for _spec in _specs:
        AEVE_SLUGS_BY_PROVIDER.setdefault(_spec.provider, set()).add(_spec.model)


@dataclass
class ChatResult:
    ok: bool
    status: int | str
    latency_ms: int
    error: str | None = None

    def cell(self) -> str:
        if self.status == "n/a":
            return "--"
        if self.ok:
            return f"OK {self.latency_ms}ms"
        return f"FAIL {self.status}"


@dataclass
class ListResult:
    status: int
    model_ids: list[str] = field(default_factory=list)
    error: str | None = None
    extra: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------

async def _groq_list(client: httpx.AsyncClient, key: str) -> ListResult:
    try:
        r = await client.get(
            f"{config.GROQ_BASE_URL}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT_S,
        )
        if r.status_code != 200:
            return ListResult(status=r.status_code, error=r.text[:300])
        data = r.json()
        ids = sorted({m["id"] for m in data.get("data", [])})
        return ListResult(status=200, model_ids=ids)
    except Exception as exc:
        return ListResult(status=-1, error=f"{type(exc).__name__}: {exc}")


async def _groq_chat(client: httpx.AsyncClient, key: str, model: str) -> ChatResult:
    start = time.perf_counter()
    try:
        r = await client.post(
            f"{config.GROQ_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": PING_PROMPT}],
                "max_tokens": PING_MAX_TOKENS,
                "temperature": 0,
            },
            timeout=TIMEOUT_S,
        )
        latency_ms = round((time.perf_counter() - start) * 1000)
        if r.status_code == 200:
            return ChatResult(ok=True, status=200, latency_ms=latency_ms)
        return ChatResult(ok=False, status=r.status_code, latency_ms=latency_ms, error=r.text[:300])
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000)
        return ChatResult(ok=False, status=-1, latency_ms=latency_ms, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

async def _openrouter_key_info(client: httpx.AsyncClient, key: str) -> ListResult:
    try:
        r = await client.get(
            f"{config.OPENROUTER_BASE_URL}/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT_S,
        )
        if r.status_code == 200:
            return ListResult(status=200, extra=r.json())
        return ListResult(status=r.status_code, error=r.text[:300])
    except Exception as exc:
        return ListResult(status=-1, error=f"{type(exc).__name__}: {exc}")


async def _openrouter_list(client: httpx.AsyncClient, key: str) -> ListResult:
    try:
        r = await client.get(
            f"{config.OPENROUTER_BASE_URL}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT_S,
        )
        if r.status_code != 200:
            return ListResult(status=r.status_code, error=r.text[:300])
        data = r.json()
        ids = sorted({m["id"] for m in data.get("data", [])})
        return ListResult(status=200, model_ids=ids)
    except Exception as exc:
        return ListResult(status=-1, error=f"{type(exc).__name__}: {exc}")


async def _openrouter_chat(client: httpx.AsyncClient, key: str, model: str) -> ChatResult:
    start = time.perf_counter()
    try:
        r = await client.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://aeve.local/probe",
                "X-Title": "AEVE-probe",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": PING_PROMPT}],
                "max_tokens": PING_MAX_TOKENS,
                "temperature": 0,
            },
            timeout=TIMEOUT_S,
        )
        latency_ms = round((time.perf_counter() - start) * 1000)
        if r.status_code == 200:
            return ChatResult(ok=True, status=200, latency_ms=latency_ms)
        return ChatResult(ok=False, status=r.status_code, latency_ms=latency_ms, error=r.text[:300])
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000)
        return ChatResult(ok=False, status=-1, latency_ms=latency_ms, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Google Generative Language
# ---------------------------------------------------------------------------

GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta"


async def _google_list(client: httpx.AsyncClient, key: str) -> ListResult:
    try:
        r = await client.get(
            f"{GOOGLE_BASE}/models",
            params={"key": key},
            timeout=TIMEOUT_S,
        )
        if r.status_code != 200:
            return ListResult(status=r.status_code, error=r.text[:300])
        data = r.json()
        ids = sorted(
            m["name"].replace("models/", "")
            for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        )
        return ListResult(status=200, model_ids=ids)
    except Exception as exc:
        return ListResult(status=-1, error=f"{type(exc).__name__}: {exc}")


async def _google_chat(client: httpx.AsyncClient, key: str, model: str) -> ChatResult:
    start = time.perf_counter()
    try:
        r = await client.post(
            f"{GOOGLE_BASE}/models/{model}:generateContent",
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": PING_PROMPT}]}],
                "generationConfig": {"maxOutputTokens": PING_MAX_TOKENS, "temperature": 0},
            },
            timeout=TIMEOUT_S,
        )
        latency_ms = round((time.perf_counter() - start) * 1000)
        if r.status_code == 200:
            return ChatResult(ok=True, status=200, latency_ms=latency_ms)
        return ChatResult(ok=False, status=r.status_code, latency_ms=latency_ms, error=r.text[:300])
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000)
        return ChatResult(ok=False, status=-1, latency_ms=latency_ms, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _hr(width: int = 80) -> str:
    return "=" * width


def _print_keys() -> None:
    print(_hr())
    print("CONFIGURED API KEYS")
    print(_hr())
    print(f"\n[Groq]  {len(config.GROQ_API_KEYS)} key(s):")
    for i, key in enumerate(config.GROQ_API_KEYS, 1):
        print(f"  G{i}  {key}")
    print(f"\n[OpenRouter]")
    print(f"  OR  {config.OPENROUTER_API_KEY}")
    print(f"\n[Google]")
    print(f"  GG  {config.GOOGLE_API_KEY}")
    print()


async def _bounded_chat(sem: asyncio.Semaphore, coro_factory):
    async with sem:
        return await coro_factory()


async def probe_groq(client: httpx.AsyncClient, concurrency: int) -> dict[str, Any]:
    print(_hr())
    print("GROQ - listing models per key")
    print(_hr())
    keys: list[str] = list(config.GROQ_API_KEYS)
    listings: dict[str, ListResult] = {}
    for i, key in enumerate(keys, 1):
        res = await _groq_list(client, key)
        listings[key] = res
        msg = f"  G{i}: /models -> {res.status}, {len(res.model_ids)} model(s)"
        if res.error:
            msg += f"  [{res.error[:80]}]"
        print(msg)

    union = sorted({m for r in listings.values() for m in r.model_ids})
    print(f"\n  Union across all Groq keys: {len(union)} model(s)\n")

    matrix: dict[str, dict[str, ChatResult]] = {m: {} for m in union}
    sem = asyncio.Semaphore(concurrency)

    async def _one(model: str, key: str):
        if model not in listings[key].model_ids:
            matrix[model][key] = ChatResult(ok=False, status="n/a", latency_ms=0)
            return
        res = await _bounded_chat(sem, lambda: _groq_chat(client, key, model))
        matrix[model][key] = res

    tasks = [_one(m, k) for m in union for k in keys]
    if tasks:
        print(f"  Testing {len(tasks)} (key, model) pair(s) at concurrency={concurrency}...")
        await asyncio.gather(*tasks)

    _render_matrix("GROQ ACCESS MATRIX", union, keys, matrix, AEVE_SLUGS_BY_PROVIDER["groq"], key_label="G")

    return {
        "keys": keys,
        "listings": {k: {"status": v.status, "count": len(v.model_ids), "error": v.error} for k, v in listings.items()},
        "matrix": {
            model: {key: asdict(res) for key, res in row.items()}
            for model, row in matrix.items()
        },
    }


async def probe_openrouter(client: httpx.AsyncClient, full: bool, concurrency: int) -> dict[str, Any]:
    print(_hr())
    print("OPENROUTER - key auth, model list, chat probe")
    print(_hr())
    key = config.OPENROUTER_API_KEY
    info = await _openrouter_key_info(client, key)
    if info.status == 200 and info.extra:
        d = info.extra.get("data", info.extra) or {}
        print(f"  /key -> 200")
        for field_name in ("label", "limit", "limit_remaining", "usage", "is_free_tier", "rate_limit"):
            if field_name in d:
                print(f"    {field_name}: {d[field_name]}")
    else:
        print(f"  /key -> {info.status}")
        if info.error:
            print(f"    error: {info.error[:200]}")

    listing = await _openrouter_list(client, key)
    print(f"  /models -> {listing.status}, {len(listing.model_ids)} model(s)")
    if listing.error:
        print(f"    error: {listing.error[:200]}")

    aeve_slugs = sorted(AEVE_SLUGS_BY_PROVIDER["openrouter"])
    if full:
        models_to_test = listing.model_ids
        print(f"\n  --openrouter-full: testing all {len(models_to_test)} listed model(s)")
    else:
        # Test AEVE-routed slugs (whether or not they appear in /models)
        models_to_test = sorted(set(aeve_slugs) | set(m for m in listing.model_ids if m in aeve_slugs))
        if not models_to_test:
            models_to_test = aeve_slugs
        print(f"\n  Testing AEVE-routed slugs only: {len(models_to_test)} model(s)  (use --openrouter-full to test every listed model)")

    sem = asyncio.Semaphore(concurrency)
    results: dict[str, ChatResult] = {}

    async def _one(model: str):
        if info.status != 200:
            results[model] = ChatResult(ok=False, status=info.status, latency_ms=0, error="key auth failed; chat skipped")
            return
        res = await _bounded_chat(sem, lambda: _openrouter_chat(client, key, model))
        results[model] = res

    tasks = [_one(m) for m in models_to_test]
    if tasks:
        await asyncio.gather(*tasks)

    print()
    _render_single_column("OPENROUTER", models_to_test, results, AEVE_SLUGS_BY_PROVIDER["openrouter"], also_listed=set(listing.model_ids))

    return {
        "key": key,
        "key_info": {"status": info.status, "extra": info.extra, "error": info.error},
        "listing": {"status": listing.status, "count": len(listing.model_ids), "error": listing.error, "models": listing.model_ids},
        "tested": {m: asdict(r) for m, r in results.items()},
    }


async def probe_google(client: httpx.AsyncClient, full: bool, concurrency: int) -> dict[str, Any]:
    print(_hr())
    print("GOOGLE - listing generateContent models, chat probe")
    print(_hr())
    key = config.GOOGLE_API_KEY
    listing = await _google_list(client, key)
    print(f"  /models -> {listing.status}, {len(listing.model_ids)} generateContent model(s)")
    if listing.error:
        print(f"    error: {listing.error[:200]}")

    aeve_slugs = sorted(AEVE_SLUGS_BY_PROVIDER["gemini"])
    if full or not aeve_slugs:
        models_to_test = listing.model_ids
        if full:
            print(f"\n  --google-full: testing all {len(models_to_test)} listed model(s)")
        else:
            print(f"\n  No AEVE-routed Gemini models; testing all {len(models_to_test)} listed instead")
    else:
        models_to_test = sorted(set(aeve_slugs) & set(listing.model_ids)) or aeve_slugs
        print(f"\n  Testing AEVE-routed Gemini slugs: {len(models_to_test)}  (use --google-full to test all)")

    sem = asyncio.Semaphore(concurrency)
    results: dict[str, ChatResult] = {}

    async def _one(model: str):
        if listing.status != 200:
            results[model] = ChatResult(ok=False, status=listing.status, latency_ms=0, error="list failed; chat skipped")
            return
        res = await _bounded_chat(sem, lambda: _google_chat(client, key, model))
        results[model] = res

    tasks = [_one(m) for m in models_to_test]
    if tasks:
        await asyncio.gather(*tasks)

    print()
    _render_single_column("GOOGLE", models_to_test, results, AEVE_SLUGS_BY_PROVIDER["gemini"], also_listed=set(listing.model_ids))

    return {
        "key": key,
        "listing": {"status": listing.status, "count": len(listing.model_ids), "error": listing.error, "models": listing.model_ids},
        "tested": {m: asdict(r) for m, r in results.items()},
    }


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

def _render_matrix(
    title: str,
    models: list[str],
    keys: list[str],
    matrix: dict[str, dict[str, ChatResult]],
    aeve_slugs: set[str],
    key_label: str,
) -> None:
    print(_hr())
    print(f"{title}  (* = used by AEVE pipeline,  -- = model not listed for that key)")
    print(_hr())
    if not models:
        print("  (no models)")
        return
    name_w = max(len(m) for m in models) + 2
    name_w = max(name_w, 36)
    cell_w = 12
    header = f"  {'MODEL':<{name_w}}" + "".join(f"{key_label}{i:<{cell_w-2}}" for i in range(1, len(keys) + 1))
    print(header)
    print("-" * len(header))
    for model in models:
        marker = "* " if model in aeve_slugs else "  "
        cells = "".join(f"{matrix[model][k].cell():<{cell_w}}" for k in keys)
        print(f"{marker}{model:<{name_w}}{cells}")
    print()


def _render_single_column(
    title: str,
    models: list[str],
    results: dict[str, ChatResult],
    aeve_slugs: set[str],
    also_listed: set[str],
) -> None:
    print(_hr())
    print(f"{title} ACCESS  (* = used by AEVE pipeline,  + = present in /models)")
    print(_hr())
    if not models:
        print("  (no models tested)")
        return
    name_w = max(len(m) for m in models) + 2
    name_w = max(name_w, 36)
    print(f"  {'MODEL':<{name_w}}RESULT")
    print("-" * (name_w + 20))
    for model in models:
        flags = ""
        flags += "*" if model in aeve_slugs else " "
        flags += "+" if model in also_listed else " "
        res = results.get(model)
        cell = res.cell() if res else "(not tested)"
        print(f" {flags}{model:<{name_w}}{cell}")
    print()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_aeve_summary(groq: dict, openrouter: dict, google: dict) -> None:
    print(_hr())
    print("AEVE ROUTING HEALTH  (per-role primary -> fallback chain)")
    print(_hr())
    for role, specs in ROUTES.items():
        print(f"\n  [{role}]")
        for i, spec in enumerate(specs):
            label = "primary" if i == 0 else f"fallback {i}"
            ok = _aeve_spec_ok(spec, groq, openrouter, google)
            mark = "OK  " if ok else "DOWN"
            print(f"    {mark}  {label:<10} {spec.provider}/{spec.model}")
    print()


def _aeve_spec_ok(spec, groq: dict, openrouter: dict, google: dict) -> bool:
    if spec.provider == "groq":
        # OK if ANY Groq key successfully chatted with this model
        row = groq.get("matrix", {}).get(spec.model, {})
        return any(r.get("ok") for r in row.values())
    if spec.provider == "openrouter":
        r = openrouter.get("tested", {}).get(spec.model)
        return bool(r and r.get("ok"))
    if spec.provider == "gemini":
        r = google.get("tested", {}).get(spec.model)
        return bool(r and r.get("ok"))
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> int:
    _print_keys()

    async with httpx.AsyncClient(http2=False) as client:
        groq_data = await probe_groq(client, args.concurrency)
        or_data = await probe_openrouter(client, args.openrouter_full, args.concurrency)
        gg_data = await probe_google(client, args.google_full, args.concurrency)

    _print_aeve_summary(groq_data, or_data, gg_data)

    out_dir = ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"key_probe_{stamp}.json"
    payload = {
        "timestamp_utc": stamp,
        "groq": groq_data,
        "openrouter": or_data,
        "google": gg_data,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Full JSON written to: {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe every configured API key against every accessible model.")
    parser.add_argument("--openrouter-full", action="store_true", help="Test every model returned by OpenRouter /models (slow, may consume credit).")
    parser.add_argument("--google-full", action="store_true", help="Test every Google generateContent model.")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help=f"Per-provider concurrency (default {DEFAULT_CONCURRENCY}).")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
