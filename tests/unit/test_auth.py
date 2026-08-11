import pytest
from fastapi import HTTPException
from app.auth import verify_api_key
from app.config import settings


def test_verify_api_key_valid_header():
    # Setup single key
    settings.API_KEY = "sk-test-key"

    token = verify_api_key(api_key_header="Bearer sk-test-key")
    assert token == "sk-test-key"


def test_verify_api_key_valid_query():
    settings.API_KEY = "sk-test-key"

    token = verify_api_key(api_key_header=None, api_key_query="sk-test-key")
    assert token == "sk-test-key"


def test_verify_api_key_invalid():
    settings.API_KEY = "sk-test-key"

    with pytest.raises(HTTPException) as exc:
        verify_api_key(api_key_header="Bearer wrong-key")
    assert exc.value.status_code == 401


def test_verify_api_key_multi_key():
    settings.API_KEY = "key1, key2, key3"

    token = verify_api_key(api_key_header="Bearer key2")
    assert token == "key2"

    with pytest.raises(HTTPException):
        verify_api_key(api_key_header="Bearer key4")
