from layer_rebirth.models import BoundingBox, Layer, ProjectDocument, QualityReport


def make_project() -> ProjectDocument:
    return ProjectDocument(
        id="12345678-1234-1234-1234-123456789abc",
        name="测试工程",
        mode="marketing",
        recommended_mode="marketing",
        canvas={"width": 320, "height": 180, "unit": "px", "background": "transparent"},
        source={"name": "source.png", "format": "PNG", "asset": "source.png"},
        layers=[Layer("text-1", "文字 1", "text", BoundingBox(10, 20, 100, 30), content="可编辑")],
        quality=QualityReport("review", 82, {"layer_count": 1}),
    )


def test_project_round_trip():
    source = make_project()
    restored = ProjectDocument.from_dict(source.to_dict())
    assert restored.to_dict() == source.to_dict()


def test_project_rejects_invalid_canvas():
    payload = make_project().to_dict()
    payload["canvas"]["width"] = 0
    try:
        ProjectDocument.from_dict(payload)
    except ValueError as exc:
        assert "画布" in str(exc)
    else:
        raise AssertionError("invalid canvas was accepted")

