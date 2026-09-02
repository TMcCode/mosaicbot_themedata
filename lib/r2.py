"""Minimal Cloudflare R2 (S3-compatible) put/get via boto3."""

from __future__ import annotations

import json
from typing import Any

from lib.env import require_env


def _client():
    import boto3

    cfg = require_env(
        "R2_ENDPOINT_URL",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "MOSAIC_THEMES_BUCKET",
    )
    client = boto3.client(
        "s3",
        endpoint_url=cfg["R2_ENDPOINT_URL"],
        aws_access_key_id=cfg["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=cfg["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    return client, cfg["MOSAIC_THEMES_BUCKET"]


def object_uri(key: str, *, bucket: str | None = None) -> str:
    b = bucket or require_env("MOSAIC_THEMES_BUCKET")["MOSAIC_THEMES_BUCKET"]
    return f"r2://{b}/{key.lstrip('/')}"


def put_bytes(key: str, body: bytes, *, content_type: str = "application/octet-stream") -> str:
    client, bucket = _client()
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
    return object_uri(key, bucket=bucket)


def put_json(key: str, payload: Any, *, indent: int | None = 2) -> str:
    text = json.dumps(payload, indent=indent) + ("\n" if indent is not None else "")
    return put_bytes(key, text.encode("utf-8"), content_type="application/json")


def get_json(key: str) -> Any | None:
    """Return parsed JSON or None if the object is missing."""
    client, bucket = _client()
    from botocore.exceptions import ClientError

    try:
        resp = client.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    body = resp["Body"].read().decode("utf-8")
    return json.loads(body)
