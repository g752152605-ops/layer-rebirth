from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization


class LicenseManager:
    def __init__(self, public_key_path: Path, data_root: Path):
        self.public_key_path = public_key_path
        self.license_path = data_root / "license.json"

    @staticmethod
    def canonical_payload(payload: dict) -> bytes:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def verify(self, document: dict) -> dict:
        payload = document.get("license")
        signature_text = document.get("signature")
        if not isinstance(payload, dict) or not isinstance(signature_text, str):
            raise ValueError("许可证格式无效")
        if not self.public_key_path.is_file():
            raise ValueError("软件缺少许可证公钥")
        try:
            public_key = serialization.load_pem_public_key(self.public_key_path.read_bytes())
            signature = base64.b64decode(signature_text, validate=True)
            public_key.verify(signature, self.canonical_payload(payload))
        except (ValueError, InvalidSignature) as exc:
            raise ValueError("许可证签名无效") from exc
        expires_at = payload.get("expires_at")
        if expires_at and date.fromisoformat(str(expires_at)) < date.today():
            raise ValueError("许可证已过期")
        if not str(payload.get("customer", "")).strip():
            raise ValueError("许可证缺少客户名称")
        return payload

    def status(self) -> dict:
        # Compatibility response for older local clients; no activation is required.
        return {"licensed": True, "edition": "standard", "customer": "本地用户"}

    def install(self, document: dict) -> dict:
        payload = self.verify(document)
        self.license_path.parent.mkdir(parents=True, exist_ok=True)
        self.license_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"licensed": True, "edition": str(payload.get("edition", "standard")), "customer": str(payload["customer"])}
