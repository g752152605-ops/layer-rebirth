from __future__ import annotations

import argparse
import base64
import json
import uuid
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from layer_rebirth.licensing import LicenseManager


def generate_keys(private_path: Path, public_path: Path) -> None:
    if private_path.exists() or public_path.exists():
        raise SystemExit("密钥文件已存在，拒绝覆盖。")
    private_key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    public_path.write_bytes(private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ))


def issue(private_path: Path, output_path: Path, customer: str, edition: str, expires_at: str | None) -> None:
    private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    payload = {
        "license_id": str(uuid.uuid4()),
        "customer": customer.strip(),
        "edition": edition,
        "issued_at": date.today().isoformat(),
        "expires_at": expires_at or None,
    }
    signature = private_key.sign(LicenseManager.canonical_payload(payload))
    document = {"license": payload, "signature": base64.b64encode(signature).decode("ascii")}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="图层重生许可证签发工具（仅卖家保管）")
    sub = parser.add_subparsers(dest="command", required=True)
    keys = sub.add_parser("generate-keys")
    keys.add_argument("--private", type=Path, default=Path(".license-private/private_key.pem"))
    keys.add_argument("--public", type=Path, default=Path("license_public_key.pem"))
    create = sub.add_parser("issue")
    create.add_argument("--private", type=Path, default=Path(".license-private/private_key.pem"))
    create.add_argument("--customer", required=True)
    create.add_argument("--edition", choices=["founder", "standard"], default="standard")
    create.add_argument("--expires-at")
    create.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "generate-keys":
        generate_keys(args.private, args.public)
    else:
        issue(args.private, args.output, args.customer, args.edition, args.expires_at)


if __name__ == "__main__":
    main()
