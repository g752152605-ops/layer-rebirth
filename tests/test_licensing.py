import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from layer_rebirth.licensing import LicenseManager


def signed_document(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    public_path = tmp_path / "public.pem"
    public_path.write_bytes(private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    payload = {
        "license_id": "test-license",
        "customer": "测试客户",
        "edition": "founder",
        "issued_at": "2026-09-04",
        "expires_at": None,
    }
    signature = private_key.sign(LicenseManager.canonical_payload(payload))
    return public_path, {"license": payload, "signature": base64.b64encode(signature).decode("ascii")}


def test_license_install_and_offline_status(tmp_path):
    public_path, document = signed_document(tmp_path)
    manager = LicenseManager(public_path, tmp_path / "data")
    assert manager.status()["licensed"] is True
    assert manager.install(document)["licensed"] is True
    assert manager.status()["edition"] == "standard"


def test_tampered_license_is_rejected(tmp_path):
    public_path, document = signed_document(tmp_path)
    document = json.loads(json.dumps(document))
    document["license"]["edition"] = "standard"
    manager = LicenseManager(public_path, tmp_path / "data")
    try:
        manager.install(document)
    except ValueError as exc:
        assert "签名" in str(exc)
    else:
        raise AssertionError("tampered license was accepted")


def test_old_invalid_license_does_not_restrict_features(tmp_path):
    manager = LicenseManager(tmp_path / "missing.pem", tmp_path)
    manager.license_path.write_text("invalid old license", encoding="utf-8")
    assert manager.status()["licensed"] is True
