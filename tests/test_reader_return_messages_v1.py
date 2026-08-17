import json
from pathlib import Path


def test_reader_return_message_pack_has_fifty_messages_and_gender_forms():
    path = Path(__file__).resolve().parents[1] / "app" / "data" / "reader_return_messages_v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["messages"]) == 50
    assert set(data["address_forms"]) == {"female", "male", "neutral"}
    for forms in data["address_forms"].values():
        assert forms
        assert all(isinstance(item, str) and item.strip() for item in forms)
