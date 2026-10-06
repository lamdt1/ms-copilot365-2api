import pytest
from unittest.mock import MagicMock
from app.browser.camoufox_manager import CamoufoxManager


def test_is_authenticated_page_closed_or_none():
    cm = CamoufoxManager()
    assert cm.is_authenticated_page is False

    mock_page = MagicMock()
    mock_page.is_closed.return_value = True
    cm.page = mock_page
    assert cm.is_authenticated_page is False


def test_is_authenticated_page_login_redirect():
    cm = CamoufoxManager()
    mock_page = MagicMock()
    mock_page.is_closed.return_value = False

    # Login redirect URL
    mock_page.url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id=123"
    cm.page = mock_page
    assert cm.is_authenticated_page is False

    mock_page.url = "https://login.live.com/oauth20_authorize.srf"
    assert cm.is_authenticated_page is False


def test_is_authenticated_page_valid():
    cm = CamoufoxManager()
    mock_page = MagicMock()
    mock_page.is_closed.return_value = False

    mock_page.url = "https://m365.cloud.microsoft/chat"
    cm.page = mock_page
    assert cm.is_authenticated_page is True

    mock_page.url = "https://copilot.microsoft.com/chats/abc"
    assert cm.is_authenticated_page is True
