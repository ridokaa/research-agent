import pytest
from app.agent.planner import Planner

def test_build_reflection_input_prepends_reason_and_missing():
    planner = Planner()
    item = planner.build_reflection_input(missing="data sentimen", reason="belum lengkap")
    content = item["content"]
    assert item["type"] == "message"
    assert item["role"] == "user"
    
    # Values are properly prepended
    assert "belum lengkap" in content
    assert "data sentimen" in content
    
    # Check ordering: reason appears before missing in natural reading
    assert content.index("Alasan: belum lengkap") < content.index("Informasi yang masih kurang: data sentimen")
    
    # Raw placeholders must NOT leak
    assert "{reason}" not in content
    assert "{missing}" not in content
    
    # Original template step instructions are present
    assert "Tentukan satu langkah konkret berikutnya:" in content

def test_build_reflection_input_none_uses_template_only():
    planner = Planner()
    item = planner.build_reflection_input(missing=None, reason=None)
    content = item["content"]
    assert "{reason}" not in content
    assert "{missing}" not in content
    assert content.lstrip().startswith("Tentukan satu langkah konkret berikutnya:")
