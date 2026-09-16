"""Fontdata-backed collision screening for proposed typeface family names."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


FONTDATA_API_URL = os.environ.get(
    "GLYPHS_MCP_FONTDATA_API_URL",
    "https://namecheck.fontdata.com/api/",
)
FONTDATA_WEB_URL = "https://namecheck.fontdata.com/"
FONTDATA_TIMEOUT_SECONDS = 10
DISCLAIMER = (
    "This is a collision screen, not legal clearance. Absence from Fontdata "
    "does not establish that the name is available to use or register."
)


def _error_result(name: str, error_type: str, message: str) -> dict:
    return {
        "ok": False,
        "name": name,
        "status": "error",
        "error": {"type": error_type, "message": message},
        "source": {
            "name": "Fontdata Namecheck",
            "resultUrl": _result_url(name) if name else FONTDATA_WEB_URL,
        },
        "disclaimer": DISCLAIMER,
    }


def _result_url(name: str) -> str:
    return f"{FONTDATA_WEB_URL}?{urllib.parse.urlencode({'q': name})}"


def _api_url(name: str) -> str:
    separator = "&" if "?" in FONTDATA_API_URL else "?"
    return f"{FONTDATA_API_URL}{separator}{urllib.parse.urlencode({'q': name})}"


def _count(confidence: dict, bucket: str) -> int:
    value = confidence.get(bucket, 0)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return 0


def interpret_fontdata_response(name: str, payload: dict) -> dict:
    """Normalize Fontdata's confidence buckets into a stable MCP result."""
    data = payload.get("data") if isinstance(payload, dict) else None
    confidence = data.get("confidence") if isinstance(data, dict) else None
    if not isinstance(confidence, dict):
        return _error_result(
            name,
            "invalid_response",
            "Fontdata returned an unexpected response schema.",
        )

    normalized_confidence = {
        bucket: _count(confidence, bucket)
        for bucket in ("1.0", "0.9", "0.8", "0.5", "0.4", "0.3")
    }
    exact_matches = normalized_confidence["1.0"]
    close_matches = normalized_confidence["0.9"] + normalized_confidence["0.8"]
    partial_matches = sum(
        normalized_confidence[bucket] for bucket in ("0.5", "0.4", "0.3")
    )
    trademark_value = data.get("trademark")
    trademark = str(trademark_value).strip() if trademark_value else None

    if exact_matches:
        status = "collision"
        summary = f"Fontdata reports {exact_matches} exact font-name match(es)."
    elif trademark or close_matches:
        status = "review"
        reasons = []
        if close_matches:
            reasons.append(f"{close_matches} close font-name match(es)")
        if trademark:
            reasons.append(f'a trademark associated with "{trademark}"')
        summary = (
            "No exact font-name match was found, but "
            + " and ".join(reasons)
            + " require review."
        )
    else:
        status = "no_known_collision"
        summary = "Fontdata reports no exact, close, or trademark collision for this name."

    return {
        "ok": True,
        "name": str(data.get("query") or name),
        "status": status,
        "summary": summary,
        "matches": {
            "exact": exact_matches,
            "close": close_matches,
            "partial": partial_matches,
            "confidence": normalized_confidence,
        },
        "trademark": trademark,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "name": "Fontdata Namecheck",
            "resultUrl": _result_url(name),
        },
        "disclaimer": DISCLAIMER,
    }


def check_font_name(name: str) -> dict:
    """Check one proposed typeface family name against Fontdata."""
    normalized_name = " ".join(name.split())
    if not normalized_name:
        return _error_result("", "invalid_name", "Name must not be empty.")
    if len(normalized_name) > 200:
        return _error_result(
            normalized_name,
            "invalid_name",
            "Name must be 200 characters or fewer.",
        )

    api_url = _api_url(normalized_name)
    request = urllib.request.Request(
        api_url,
        headers={"User-Agent": "GlyphsMCP font-name-check/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=FONTDATA_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return _error_result(
            normalized_name,
            "http_error",
            f"Fontdata returned HTTP {error.code}.",
        )
    except urllib.error.URLError as error:
        return _error_result(
            normalized_name,
            "network_error",
            f"Could not reach Fontdata: {error.reason}",
        )
    except OSError as error:
        return _error_result(
            normalized_name,
            "network_error",
            f"Could not reach Fontdata: {error}",
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error_result(
            normalized_name,
            "invalid_response",
            "Fontdata returned a response that was not valid UTF-8 JSON.",
        )

    return interpret_fontdata_response(normalized_name, payload)
