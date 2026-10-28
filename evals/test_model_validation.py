import asyncio
import json
from types import SimpleNamespace

import pytest

import app.nebius as nebius_module
from app.config import Settings
from app.nebius import ModelError, NebiusClassifier


def _response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _classify(monkeypatch, response):
    class FakeCompletions:
        async def create(self, **kwargs):
            return response

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(nebius_module, "AsyncOpenAI", FakeOpenAI)
    classifier = NebiusClassifier(Settings(nebius_api_key="test-key"))
    return asyncio.run(
        classifier.classify(
            "Allow science",
            {"title": "Title", "description": "Description", "channel": "Channel"},
            "Transcript",
            [],
        )
    )


def test_valid_block_status_is_accepted(monkeypatch):
    content = json.dumps({"status": "block", "explanation": "Reason.", "confidence": 0.9})

    result = _classify(monkeypatch, _response(content))

    assert result == {"status": "block", "explanation": "Reason.", "confidence": 0.9}


@pytest.mark.parametrize(
    "status, confidence, expected_status, expected_confidence",
    [
        ("allow", 0.0, "ask_parent", 0.0),
        ("block", 1.0, "block", 1.0),
        ("block", -0.25, "ask_parent", 0.0),
        ("allow", 1.25, "allow", 1.0),
    ],
)
def test_confidence_boundaries_and_out_of_range_values_are_clamped(
    monkeypatch, status, confidence, expected_status, expected_confidence
):
    content = json.dumps({"status": status, "explanation": "Reason.", "confidence": confidence})

    result = _classify(monkeypatch, _response(content))

    assert result["status"] == expected_status
    assert result["confidence"] == expected_confidence


@pytest.mark.parametrize(
    "content",
    [
        '{"explanation":"Reason.","confidence":0.9}',
        '{"status":null,"explanation":"Reason.","confidence":0.9}',
        '{"status":7,"explanation":"Reason.","confidence":0.9}',
        '{"status":"allow","explanation":"Reason.","confidence":null}',
        '{"status":"allow","explanation":"Reason.","confidence":[]}',
        "",
        None,
    ],
)
def test_invalid_or_empty_model_content_raises_model_error(monkeypatch, content):
    with pytest.raises(ModelError):
        _classify(monkeypatch, _response(content))


@pytest.mark.parametrize(
    "response, exception_type",
    [
        (SimpleNamespace(), AttributeError),
        (SimpleNamespace(choices=[]), IndexError),
        (SimpleNamespace(choices=[SimpleNamespace()]), AttributeError),
        (SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace())]), AttributeError),
    ],
)
def test_missing_or_empty_response_envelope_raises(monkeypatch, response, exception_type):
    with pytest.raises(exception_type):
        _classify(monkeypatch, response)