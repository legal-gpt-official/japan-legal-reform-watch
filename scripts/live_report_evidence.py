"""Validate and whitelist exact LIVE evidence before it can enter the ledger."""
from __future__ import annotations

import re
from datetime import datetime


def validated_names(value):
    if (not isinstance(value, list) or not all(isinstance(n, str) and
            re.fullmatch(r"[a-z0-9][a-z0-9-]{2,79}", n) for n in value)
            or len(set(value)) != len(value)):
        raise ValueError("invalid LIVE dossier names")
    return list(value)


def validated_v3(value):
    if not isinstance(value, dict):
        raise ValueError("missing LIVE facts")
    names = validated_names(value.get("dossiers"))
    versions = value.get("dossier_versions")
    sha = value.get("source_sha")
    started = value.get("checked_started_at")
    if (not isinstance(versions, dict) or set(versions) != set(names)
            or not all(isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v) for v in versions.values())
            or not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha)
            or not isinstance(started, str)):
        raise ValueError("invalid version-bound LIVE facts")
    if datetime.fromisoformat(started).tzinfo is None:
        raise ValueError("LIVE timestamp lacks timezone")
    pub, page = value.get("publish_run_id"), value.get("pages_run_id")
    if (pub is None) != (page is None) or any(
            v is not None and (not isinstance(v, str) or not re.fullmatch(r"[0-9]+", v))
            for v in (pub, page)):
        raise ValueError("incomplete publisher identifiers")
    return {"dossiers": names, "dossier_versions": dict(versions),
            "checked_started_at": started, "source_sha": sha,
            "publish_run_id": pub, "pages_run_id": page}
