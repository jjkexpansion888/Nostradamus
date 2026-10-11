#!/usr/bin/env python3
"""Cyclist Sentinel — NYC egocentric bike safety investigation desk on VSS."""

from __future__ import annotations

import json
import os
import random
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

VSS_URL = os.environ.get("VSS_URL", "").rstrip("/")
VSS_USERNAME = os.environ.get("VSS_USERNAME", "")
VSS_PASSWORD = os.environ.get("VSS_PASSWORD", "")
WANDB_API_KEY = os.environ.get("WANDB_API_KEY", "")
WANDB_TEAM = os.environ.get("WANDB_TEAM", "")
WANDB_PROJECT = os.environ.get("WANDB_PROJECT", "")
WANDB_MODEL = os.environ.get("WANDB_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
MANIFOLD_API = "https://api.manifold.markets/v0"
MANIFOLD_API_KEY = os.environ.get("MANIFOLD_API_KEY", "")
MANIFOLD_BET_CAP = int(os.environ.get("MANIFOLD_BET_CAP", "25"))
STARTING_CASH = float(os.environ.get("STARTING_CASH", "100"))
DEFAULT_CONTRACTS = int(os.environ.get("DEFAULT_CONTRACTS", "10"))
MAX_CONTRACTS = int(os.environ.get("MAX_CONTRACTS", "250"))
PORT = int(os.environ.get("PORT", "8080"))

BIKE_CAM = "nyc_bike_gopro-1"

CASES: list[dict[str, Any]] = [
    {
        "id": "night_lane",
        "title": "Night bike lane",
        "blurb": "First-person night rides: reflective gear, traffic, and lane continuity.",
        "query": "cyclist on bike lane at night with traffic lights and vehicles",
        "camera_id": BIKE_CAM,
        "min_similarity": 0.2,
        "top_k": 8,
        "include_street_context": False,
    },
    {
        "id": "close_vehicle",
        "title": "Close vehicle pass",
        "blurb": "Cars, trucks, or buses sharing space uncomfortably close to the rider.",
        "query": "vehicle passing close to cyclist on city street truck taxi bus",
        "camera_id": BIKE_CAM,
        "min_similarity": 0.15,
        "top_k": 8,
        "include_street_context": False,
    },
    {
        "id": "obstruction",
        "title": "Lane obstruction",
        "blurb": "Barricades, construction, delivery trucks, and debris in the cyclist's path.",
        "query": "construction barricade orange cone parked truck blocking bike lane sidewalk",
        "camera_id": BIKE_CAM,
        "min_similarity": 0.15,
        "top_k": 8,
        "include_street_context": False,
    },
    {
        "id": "intersection",
        "title": "Intersection conflict",
        "blurb": "Rider approaching busy crossings — pair egocentric view with fixed street cams.",
        "query": "cyclist at busy city intersection with yellow taxi crosswalk traffic light",
        "camera_id": BIKE_CAM,
        "min_similarity": 0.15,
        "top_k": 6,
        "include_street_context": True,
        "street_query": "pedestrians and vehicles at busy New York intersection crosswalk taxi",
    },
]

_token: str | None = None
_token_lock = threading.Lock()
_token_fetched_at = 0.0


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 120,
) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "cyclist-sentinel/1.0")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw:
            return None
        return json.loads(raw.decode())


def _login(force: bool = False) -> str:
    global _token, _token_fetched_at
    with _token_lock:
        if not force and _token and (time.time() - _token_fetched_at) < 50 * 60:
            return _token
        if not VSS_URL or not VSS_USERNAME or not VSS_PASSWORD:
            raise RuntimeError("VSS credentials not configured")
        data = _http_json(
            "POST",
            f"{VSS_URL}/api/v1/auth/login",
            body={"username": VSS_USERNAME, "password": VSS_PASSWORD},
            timeout=30,
        )
        _token = data["access_token"]
        _token_fetched_at = time.time()
        return _token


def _vss(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    token = _login()
    try:
        return _http_json(
            method,
            f"{VSS_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            body=body,
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        token = _login(force=True)
        return _http_json(
            method,
            f"{VSS_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            body=body,
        )


def _search(
    query: str,
    *,
    camera_id: str | None = None,
    top_k: int = 8,
    min_similarity: float = 0.15,
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
        "query": query,
        "top_k": top_k,
        "min_similarity": min_similarity,
        "include_public": True,
    }
    if camera_id:
        payload["metadata_filters"] = {"camera_id": camera_id}
    data = _vss("POST", "/api/v1/search", payload)
    results = (data or {}).get("results") or []
    out = []
    for row in results:
        out.append(
            {
                "source": row.get("source") or row.get("preview_source"),
                "original_video": row.get("original_video"),
                "filename": row.get("filename"),
                "camera_id": row.get("camera_id"),
                "location": row.get("location"),
                "capture_type": row.get("capture_type"),
                "similarity_score": row.get("similarity_score"),
                "reasoning_content": (row.get("reasoning_content") or "").strip(),
                "object_classes": row.get("object_classes"),
                "best_match_start_sec": row.get("best_match_start_sec"),
                "best_match_end_sec": row.get("best_match_end_sec"),
                "lens": "egocentric" if row.get("camera_id") == BIKE_CAM else "street",
            }
        )
    return out


def _extractive_brief(case_title: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
    observations = []
    for h in hits[:5]:
        snippet = (h.get("reasoning_content") or "").replace("\n", " ")
        if len(snippet) > 220:
            snippet = snippet[:217] + "…"
        observations.append(
            f"[{h.get('camera_id')}, score={h.get('similarity_score')}] {snippet}"
        )
    return {
        "provider": "extractive-fallback",
        "model": None,
        "observations": observations,
        "assessments": [
            "Assessment is provisional: captions describe what is visible, not legal fault.",
            f"Case '{case_title}' returned {len(hits)} ranked clips from the indexed NYC archive.",
            "Prioritize highest-similarity egocentric clips, then street-cam context if present.",
        ],
        "summary": (
            f"{len(hits)} clips matched '{case_title}'. "
            "Review top egocentric evidence first; treat model text as observation only."
        ),
    }


def _parse_json_block(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return None


def _wandb_brief(case_title: str, query: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
    if not WANDB_API_KEY or not hits:
        return _extractive_brief(case_title, hits)

    evidence_lines = []
    for i, h in enumerate(hits[:6], 1):
        snippet = (h.get("reasoning_content") or "").replace("\n", " ")[:320]
        evidence_lines.append(
            f"{i}. camera={h.get('camera_id')} lens={h.get('lens')} "
            f"sim={h.get('similarity_score')} :: {snippet}"
        )
    user_prompt = (
        f"Case: {case_title}\nQuery: {query}\n\nEvidence clips:\n"
        + "\n".join(evidence_lines)
        + "\n\nReturn JSON with keys: summary (string), observations (array of strings "
        "that only restate visible evidence), assessments (array of strings that are "
        "cautious inferences for a bike-safety analyst). Do not invent objects not in evidence."
    )
    body = {
        "model": WANDB_MODEL,
        "temperature": 0.2,
        "max_tokens": 700,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a bike-safety analyst assistant. Separate observations "
                    "(what captions show) from assessments (cautious inferences). "
                    "Never claim certainty about collisions or violations."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {WANDB_API_KEY}"}
    if WANDB_TEAM and WANDB_PROJECT:
        headers["OpenAI-Project"] = f"{WANDB_TEAM}/{WANDB_PROJECT}"
    try:
        data = _http_json(
            "POST",
            "https://api.inference.wandb.ai/v1/chat/completions",
            headers=headers,
            body=body,
            timeout=90,
        )
        text = data["choices"][0]["message"]["content"]
        parsed = _parse_json_block(text)
        if not parsed:
            return {
                "provider": "wandb",
                "model": WANDB_MODEL,
                "observations": [(h.get("reasoning_content") or "")[:200] for h in hits[:4]],
                "assessments": ["Model returned free text; see summary."],
                "summary": text.strip()[:1200],
            }
        return {
            "provider": "wandb",
            "model": WANDB_MODEL,
            "summary": parsed.get("summary") or "",
            "observations": parsed.get("observations") or [],
            "assessments": parsed.get("assessments") or [],
        }
    except Exception as exc:  # noqa: BLE001
        brief = _extractive_brief(case_title, hits)
        brief["fallback_error"] = type(exc).__name__
        return brief


def _resolve_case(payload: dict[str, Any]) -> dict[str, Any]:
    case_id = payload.get("case_id")
    if case_id:
        for c in CASES:
            if c["id"] == case_id:
                return dict(c)
        raise ValueError(f"Unknown case_id: {case_id}")
    query = (payload.get("query") or "").strip()
    if not query:
        raise ValueError("Provide case_id or query")
    return {
        "id": "custom",
        "title": "Custom investigation",
        "blurb": "Ad-hoc rider-perspective query.",
        "query": query,
        "camera_id": payload.get("camera_id") or BIKE_CAM,
        "min_similarity": float(payload.get("min_similarity") or 0.15),
        "top_k": int(payload.get("top_k") or 8),
        "include_street_context": bool(payload.get("include_street_context")),
        "street_query": payload.get("street_query") or query,
    }


def investigate(payload: dict[str, Any]) -> dict[str, Any]:
    case = _resolve_case(payload)
    ego = _search(
        case["query"],
        camera_id=case.get("camera_id"),
        top_k=int(case.get("top_k") or 8),
        min_similarity=float(case.get("min_similarity") or 0.15),
    )
    street: list[dict[str, Any]] = []
    if case.get("include_street_context"):
        street = _search(
            case.get("street_query") or case["query"],
            camera_id="nyc_streets_cam-1",
            top_k=4,
            min_similarity=0.12,
        )
        if len(street) < 2:
            street.extend(
                _search(
                    case.get("street_query") or case["query"],
                    camera_id="nyc_streets_cam-2",
                    top_k=3,
                    min_similarity=0.12,
                )
            )
    combined = ego + street
    brief = _wandb_brief(case["title"], case["query"], combined)
    return {
        "case": {
            "id": case["id"],
            "title": case["title"],
            "blurb": case.get("blurb"),
            "query": case["query"],
        },
        "egocentric_hits": ego,
        "street_context_hits": street,
        "brief": brief,
        "sponsor_tools": [
            "VAST VSS (search, metadata, playback)",
            "NVIDIA Cosmos Reason / Embed / YOLO (via indexed archive)",
            "Weights & Biases Serverless Inference (briefing)",
        ],
    }


# Queries spread the feed across the whole archive, not one camera.
FEED_QUERIES = [
    "people walking on a city street",
    "cars and traffic at an intersection",
    "a cyclist riding through traffic",
    "a forklift moving inside a warehouse",
    "people inside a building or office",
    "a quiet residential street",
    "a busy downtown sidewalk with crowds",
]

# Used when the model cannot write a question for a clip.
FALLBACK_CLAIMS = [
    ("A person is visible.", ("person", "pedestrian", "people", "man", "woman", "worker")),
    ("A vehicle is visible.", ("car", "taxi", "truck", "bus", "van", "suv", "vehicle")),
]

_rounds: dict[str, dict[str, Any]] = {}
_rounds_lock = threading.Lock()
_pair_pool: list[tuple[dict[str, Any], dict[str, Any]]] = []
_pair_cursor = 0
_claim_cursor = 0
_ledger_lock = threading.Lock()
_ledger: dict[str, Any] = {
    "cash": float(STARTING_CASH),
    "staked": 0.0,
    "settled": 0,
    "won": 0,
    "history": [],
}


def _clip_public(seg: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": seg.get("source"),
        "filename": seg.get("filename"),
        "segment_number": seg.get("segment_number"),
        "total_segments": seg.get("total_segments"),
        "camera_id": seg.get("camera_id"),
        "location": seg.get("location"),
        "reasoning_content": (seg.get("reasoning_content") or "").strip(),
        "segment_start_sec": seg.get("segment_start_sec"),
        "segment_end_sec": seg.get("segment_end_sec"),
    }


def _list_segments(original_video: str) -> list[dict[str, Any]]:
    qs = urllib.parse.urlencode({"original_video": original_video})
    data = _vss("GET", f"/api/v1/tools/segments?{qs}")
    segs = (data or {}).get("segments") or []
    segs.sort(key=lambda s: int(s.get("segment_number") or 0))
    return segs


def _fallback_claim(caption: str) -> tuple[str, bool]:
    """Pick a generic claim and guess it by carrying the current caption forward."""
    low = (caption or "").lower()
    for text, words in FALLBACK_CLAIMS:
        if any(word in low for word in words):
            return text, True
    return FALLBACK_CLAIMS[0][0], False


def _claim_from_keywords(question: str, caption: str) -> bool:
    low = (caption or "").lower()
    for text, words in FALLBACK_CLAIMS:
        if text == question:
            return any(word in low for word in words)
    stop = {"a", "an", "the", "is", "are", "will", "be", "in", "next", "seconds", "visible", "there"}
    words = [w.strip(".,?") for w in (question or "").lower().split()]
    words = [w for w in words if len(w) > 3 and w not in stop]
    return any(w in low for w in words)


def _ensure_pairs() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    global _pair_pool
    if _pair_pool:
        return _pair_pool
    seen: list[str] = []
    for query in FEED_QUERIES:
        try:
            hits = _search(query, top_k=6, min_similarity=0.05)
        except Exception:  # noqa: BLE001
            continue
        for hit in hits:
            original = hit.get("original_video")
            if original and original not in seen:
                seen.append(original)
        if len(seen) >= 18:
            break
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for original in seen:
        try:
            segs = _list_segments(original)
        except Exception:  # noqa: BLE001
            continue
        for idx in range(len(segs) - 1):
            cur, nxt = segs[idx], segs[idx + 1]
            if cur.get("reasoning_content") and nxt.get("reasoning_content") and cur.get("source") and nxt.get("source"):
                pairs.append((cur, nxt))
    if not pairs:
        raise RuntimeError("No consecutive segments available")
    random.shuffle(pairs)
    _pair_pool = pairs
    return pairs


def _wandb_json(system: str, user: str, max_tokens: int = 400) -> dict[str, Any]:
    """One W&B chat call that must return a JSON object."""
    if not WANDB_API_KEY:
        raise RuntimeError("W&B not configured")
    headers = {"Authorization": f"Bearer {WANDB_API_KEY}"}
    if WANDB_TEAM and WANDB_PROJECT:
        headers["OpenAI-Project"] = f"{WANDB_TEAM}/{WANDB_PROJECT}"
    data = _http_json(
        "POST",
        "https://api.inference.wandb.ai/v1/chat/completions",
        headers=headers,
        body={
            "model": WANDB_MODEL,
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=90,
    )
    return _parse_json_block(data["choices"][0]["message"]["content"]) or {}


def _market_for_clip(clip: dict[str, Any]) -> dict[str, Any]:
    """The model writes one question about the next 5 seconds and prices it."""
    caption = (clip.get("reasoning_content") or "")[:1200]
    scene = ", ".join(str(x) for x in [clip.get("location"), clip.get("camera_id")] if x)
    system = (
        "You run a prediction market on short video clips. "
        "You see a description of the CURRENT 5 seconds and nothing else. "
        "Write ONE yes/no question about the NEXT 5 seconds of the same camera. "
        "It must be decidable from a written description of those next 5 seconds, "
        "so ask about visible things: people, vehicles, movement, objects, actions. "
        "No sound, no text on screen, no exact counts above three, nothing after the clip ends. "
        "Then price it: probability is your honest chance it resolves YES. "
        'Return JSON {"question":"...","probability":65,"reason":"one short sentence"}'
    )
    user = f"Scene: {scene or 'unknown'}\nCurrent 5 seconds:\n{caption}"
    try:
        parsed = _wandb_json(system, user)
        question = str(parsed.get("question") or "").strip()
        prob = float(parsed.get("probability"))
        if prob <= 1:
            prob *= 100
        if not question or not (1 <= prob <= 99):
            raise ValueError("unusable question")
        if not question.endswith("?"):
            question += "?"
        yes_cents = int(round(min(max(prob, 5), 95)))
        return {
            "question": question,
            "yes_cents": yes_cents,
            "no_cents": 100 - yes_cents,
            "reason": str(parsed.get("reason") or ""),
            "provider": "wandb",
        }
    except Exception:  # noqa: BLE001
        text, carried = _fallback_claim(caption)
        yes_cents = 70 if carried else 35
        return {
            "question": text,
            "yes_cents": yes_cents,
            "no_cents": 100 - yes_cents,
            "reason": "Carried forward from what the current clip shows.",
            "provider": "extractive-fallback",
        }


def _judge_next(question: str, caption: str) -> dict[str, Any]:
    """Settle one question against the revealed caption only."""
    system = (
        "You settle a prediction market. You are given a description of a 5-second clip "
        "and one yes/no question about it. Answer only from the description. "
        "If the description does not support the question, answer no. "
        'Return JSON {"yes":true,"reason":"one short sentence"}'
    )
    user = f"Clip description:\n{caption[:1200]}\n\nQuestion: {question}"
    try:
        parsed = _wandb_json(system, user, max_tokens=200)
        if "yes" not in parsed:
            raise ValueError("no verdict")
        return {
            "provider": "wandb",
            "yes": bool(parsed.get("yes")),
            "reason": str(parsed.get("reason") or ""),
        }
    except Exception:  # noqa: BLE001
        return {
            "provider": "extractive-fallback",
            "yes": _claim_from_keywords(question, caption),
            "reason": "Keyword match on the revealed description.",
        }


def _money(amount: float) -> str:
    return "M$%.2f" % float(amount)


def _ledger_public() -> dict[str, Any]:
    with _ledger_lock:
        return {
            "cash": round(_ledger["cash"], 2),
            "settled": _ledger["settled"],
            "won": _ledger["won"],
            "staked": round(_ledger["staked"], 2),
            "history": list(_ledger["history"][-8:])[::-1],
        }


def arena_new_round() -> dict[str, Any]:
    global _pair_cursor
    pairs = _ensure_pairs()
    with _rounds_lock:
        current, nxt = pairs[_pair_cursor % len(pairs)]
        _pair_cursor += 1
        round_id = secrets.token_hex(8)
    quote = _market_for_clip(current)
    record = {
        "id": round_id,
        "question": quote["question"],
        "context": current,
        "next": nxt,
        "quote": quote,
        "order": None,
        "judge": None,
    }
    with _rounds_lock:
        _rounds[round_id] = record
        if len(_rounds) > 60:
            for old in list(_rounds)[:-60]:
                _rounds.pop(old, None)
    return {
        "round_id": round_id,
        "question": quote["question"],
        "quote": quote,
        "default_contracts": DEFAULT_CONTRACTS,
        "max_contracts": MAX_CONTRACTS,
        "context": _clip_public(current),
        "hidden_next": True,
        "balance": _ledger_public(),
        # The contract is listed when a side is taken, so browsing costs no mana.
        "manifold_ready": bool(MANIFOLD_API_KEY),
    }


def arena_lock(payload: dict[str, Any]) -> dict[str, Any]:
    round_id = payload.get("round_id")
    outcome = str(payload.get("outcome") or "").strip().lower()
    if outcome not in ("yes", "no"):
        raise ValueError("outcome must be yes or no")
    try:
        contracts = int(payload.get("contracts") or 0)
    except (TypeError, ValueError):
        raise ValueError("contracts must be a whole number")
    if contracts < 1 or contracts > MAX_CONTRACTS:
        raise ValueError(f"Buy between 1 and {MAX_CONTRACTS} contracts")
    with _rounds_lock:
        record = _rounds.get(round_id)
        if not record:
            raise ValueError("Unknown round")
        if record.get("order"):
            raise ValueError("This clip already has an order")
        quote = record["quote"]
        question = record["question"]
        current = record["context"]
    price = (quote["yes_cents"] if outcome == "yes" else quote["no_cents"]) / 100
    cost = round(contracts * price, 2)
    with _ledger_lock:
        if _ledger["cash"] < cost:
            raise ValueError(
                "Not enough buying power. That order costs %s and you have %s."
                % (_money(cost), _money(_ledger["cash"]))
            )
        _ledger["cash"] -= cost
        _ledger["staked"] += cost
    order = {
        "outcome": outcome,
        "price_cents": int(round(price * 100)),
        "contracts": contracts,
        "cost": cost,
        "payout_if_right": float(contracts),
        "to_win": round(contracts - cost, 2),
    }
    market = _open_manifold_market(round_id, current, question, quote["yes_cents"])
    bet = _place_manifold_bet(market, outcome, cost)
    with _rounds_lock:
        if round_id in _rounds:
            _rounds[round_id]["order"] = order
            _rounds[round_id]["manifold"] = market
            _rounds[round_id]["manifold_bet"] = bet
    return {
        "round_id": round_id,
        "order": order,
        "balance": _ledger_public(),
        "manifold": _manifold_public(market),
        "manifold_bet": bet,
    }


def arena_resolve(payload: dict[str, Any]) -> dict[str, Any]:
    round_id = payload.get("round_id")
    with _rounds_lock:
        record = _rounds.get(round_id)
        if not record:
            raise ValueError("Unknown round")
        order = record.get("order")
        if not order:
            raise ValueError("Place an order before settling")
        nxt = record["next"]
        question = record["question"]
    judge = record.get("judge") or _judge_next(question, nxt.get("reasoning_content") or "")
    truth = bool(judge["yes"])
    won = (order["outcome"] == "yes") == truth
    payout = float(order["contracts"]) if won else 0.0
    with _rounds_lock:
        record["judge"] = judge
        already = bool(record.get("settled"))
        record["settled"] = True
        market = record.get("manifold") or {}
    if not already:
        with _ledger_lock:
            _ledger["cash"] += payout
            _ledger["settled"] += 1
            if won:
                _ledger["won"] += 1
            _ledger["history"].append(
                {
                    "claim": question,
                    "outcome": order["outcome"],
                    "price_cents": order["price_cents"],
                    "contracts": order["contracts"],
                    "truth": truth,
                    "won": won,
                    "profit": round(payout - order["cost"], 2),
                }
            )
            del _ledger["history"][:-40]
    resolution = _resolve_manifold(market, truth)
    with _rounds_lock:
        if round_id in _rounds:
            _rounds[round_id]["manifold_resolution"] = resolution
    return {
        "round_id": round_id,
        "next": _clip_public(nxt),
        "question": question,
        "truth": truth,
        "reason": judge.get("reason") or "",
        "judge_provider": judge["provider"],
        "order": order,
        "won": won,
        "payout": round(payout, 2),
        "profit": round(payout - order["cost"], 2),
        "balance": _ledger_public(),
        "manifold_resolution": resolution,
    }


def _manifold_public(market: dict[str, Any]) -> dict[str, Any]:
    return {
        "configured": bool(MANIFOLD_API_KEY),
        "id": market.get("id"),
        "url": market.get("url"),
        "question": market.get("question"),
        "probability": market.get("probability"),
        "error": market.get("error"),
    }


def _manifold_call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    if not MANIFOLD_API_KEY:
        raise RuntimeError("MANIFOLD_API_KEY is not set")
    try:
        return _http_json(
            method,
            MANIFOLD_API + path,
            headers={"Authorization": f"Key {MANIFOLD_API_KEY}"},
            body=body,
            timeout=45,
        )
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        raise RuntimeError(f"Manifold {exc.code}: {detail}") from exc


def _open_manifold_market(
    round_id: str,
    current: dict[str, Any],
    claim_text: str,
    yes_cents: int,
) -> dict[str, Any]:
    question = "Next 5 seconds of an NYC ride: " + claim_text
    filename = current.get("filename") or round_id
    description = (
        "Play-money market from Cyclist Sentinel. "
        f"The current clip is {filename}. "
        "This question is about the immediately following 5-second segment of the same ride, "
        "which is hidden until the round is settled. "
        "Resolution uses that next segment's caption, not this one."
    )
    if not MANIFOLD_API_KEY:
        return {"question": question, "error": "MANIFOLD_API_KEY is not set"}
    try:
        data = _manifold_call(
            "POST",
            "/market",
            {
                "outcomeType": "BINARY",
                "question": question,
                "descriptionMarkdown": description,
                "initialProb": min(max(yes_cents, 2), 98),
                "closeTime": int((time.time() + 45 * 60) * 1000),
                "visibility": "public",
                "liquidityTier": 100,
            },
        )
        return {
            "id": data.get("id"),
            "url": data.get("url"),
            "question": data.get("question") or question,
            "probability": data.get("probability"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"question": question, "error": str(exc)}


def _place_manifold_bet(market: dict[str, Any], outcome: str, cost: float) -> dict[str, Any]:
    market_id = market.get("id")
    if not market_id:
        return {"placed": False, "error": market.get("error") or "No Manifold market for this clip"}
    side = "YES" if outcome == "yes" else "NO"
    # Manifold stakes mana rather than contracts, and rejects anything under M$1.
    amount = max(1, min(int(round(cost)), MANIFOLD_BET_CAP))
    try:
        bet = _manifold_call(
            "POST",
            "/bet",
            {"contractId": market_id, "amount": amount, "outcome": side},
        )
    except Exception as exc:  # noqa: BLE001
        return {"placed": False, "outcome": side, "error": str(exc)}
    return {
        "placed": True,
        "outcome": side,
        "amount": amount,
        "probAfter": bet.get("probAfter") if isinstance(bet, dict) else None,
    }


def _resolve_manifold(market: dict[str, Any], yes: bool) -> dict[str, Any]:
    market_id = market.get("id")
    if not market_id:
        return {"resolved": False, "error": market.get("error") or "No Manifold market"}
    try:
        _manifold_call(
            "POST",
            f"/market/{market_id}/resolve",
            {"outcome": "YES" if yes else "NO"},
        )
        return {
            "resolved": True,
            "outcome": "YES" if yes else "NO",
            "id": market_id,
            "url": market.get("url"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"resolved": False, "id": market_id, "url": market.get("url"), "error": str(exc)}


def playback_info(source: str) -> dict[str, Any]:
    # Same-origin stream. Presigned S3 URLs are plain HTTP and get blocked
    # as mixed content when the app is opened over HTTPS.
    return {
        "mode": "proxy",
        "url": f"api/stream?source={urllib.parse.quote(source, safe='')}",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CyclistSentinel/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys_stderr = __import__("sys").stderr
        print("%s - %s" % (self.address_string(), fmt % args), file=sys_stderr)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, code: int, obj: Any) -> None:
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/health", "/health/"):
            self._send_json(
                200,
                {
                    "status": "ok",
                    "app": "cyclist-sentinel",
                    "vss_configured": bool(VSS_URL and VSS_USERNAME),
                    "wandb_configured": bool(WANDB_API_KEY),
                    "manifold_configured": bool(MANIFOLD_API_KEY),
                },
            )
            return

        if path in ("/", "/index.html"):
            self._send_file(ROOT / "index.html", "text/html; charset=utf-8")
            return
        if path == "/styles.css":
            self._send_file(ROOT / "styles.css", "text/css; charset=utf-8")
            return
        if path in ("/app.js", "/react.js", "/react-dom.js", "/htm.js"):
            self._send_file(ROOT / path.lstrip("/"), "application/javascript; charset=utf-8")
            return

        if path == "/api/cases":
            self._send_json(
                200,
                {
                    "cases": [
                        {
                            "id": c["id"],
                            "title": c["title"],
                            "blurb": c["blurb"],
                            "query": c["query"],
                            "camera_id": c["camera_id"],
                            "include_street_context": c.get(
                                "include_street_context", False
                            ),
                        }
                        for c in CASES
                    ],
                    "bike_camera": BIKE_CAM,
                    "street_cameras": ["nyc_streets_cam-1", "nyc_streets_cam-2"],
                },
            )
            return

        if path == "/api/playback":
            source = (qs.get("source") or [None])[0]
            if not source:
                self._send_json(400, {"error": "source required"})
                return
            try:
                self._send_json(200, playback_info(source))
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/api/stream":
            source = (qs.get("source") or [None])[0]
            if not source:
                self._send_json(400, {"error": "source required"})
                return
            try:
                self._proxy_stream(source)
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": str(exc)})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON"})
            return
        try:
            if parsed.path == "/api/arena/round":
                self._send_json(200, arena_new_round())
            elif parsed.path == "/api/arena/lock":
                self._send_json(200, arena_lock(payload))
            elif parsed.path == "/api/arena/resolve":
                self._send_json(200, arena_resolve(payload))
            elif parsed.path == "/api/investigate":
                self._send_json(200, investigate(payload))
            else:
                self._send_json(404, {"error": "not found"})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"error": str(exc)})

    def _proxy_stream(self, source: str) -> None:
        token = _login()
        qs = urllib.parse.urlencode({"source": source, "token": token})
        url = f"{VSS_URL}/api/v1/videos/stream?{qs}"
        headers = {"User-Agent": "cyclist-sentinel/1.0"}
        range_hdr = self.headers.get("Range")
        if range_hdr:
            headers["Range"] = range_hdr
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            upstream = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            token = _login(force=True)
            qs = urllib.parse.urlencode({"source": source, "token": token})
            url = f"{VSS_URL}/api/v1/videos/stream?{qs}"
            req = urllib.request.Request(url, headers=headers, method="GET")
            upstream = urllib.request.urlopen(req, timeout=120)

        self.send_response(upstream.status)
        ctype = upstream.headers.get("Content-Type") or ""
        if "mp4" in source.lower() or ctype in ("", "application/octet-stream", "binary/octet-stream"):
            ctype = "video/mp4"
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if upstream.headers.get("Content-Length"):
            self.send_header("Content-Length", upstream.headers["Content-Length"])
        if upstream.headers.get("Content-Range"):
            self.send_header("Content-Range", upstream.headers["Content-Range"])
        self._cors()
        self.end_headers()
        while True:
            chunk = upstream.read(64 * 1024)
            if not chunk:
                break
            self.wfile.write(chunk)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"cyclist-sentinel listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
