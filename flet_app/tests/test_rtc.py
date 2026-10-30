import pytest

from videochat.services import rtc
from videochat.services.errors import RtcUnavailable

from conftest import FAKE_APP_ID, make_settings


def test_token_is_an_agora_v006_token_for_the_app_and_expires_after_the_ttl():
    settings = make_settings(rtc_token_ttl=900)
    creds = rtc.build_rtc_token(settings, "LOBBY", 4242, now=1_800_000_000)
    assert creds.app_id == FAKE_APP_ID
    assert creds.token.startswith("006" + FAKE_APP_ID)
    assert creds.expires_at == 1_800_000_900


def test_tokens_are_bound_to_channel_and_uid():
    settings = make_settings()
    base = rtc.build_rtc_token(settings, "LOBBY", 1, now=1_800_000_000).token
    assert rtc.build_rtc_token(settings, "OTHER", 1, now=1_800_000_000).token != base
    assert rtc.build_rtc_token(settings, "LOBBY", 2, now=1_800_000_000).token != base


def test_certificate_is_not_embedded_in_the_token():
    settings = make_settings()
    token = rtc.build_rtc_token(settings, "LOBBY", 1).token
    assert settings.agora_app_certificate not in token


@pytest.mark.parametrize("overrides", [{"agora_app_id": ""}, {"agora_app_certificate": ""}, {"agora_app_id": "", "agora_app_certificate": ""}])
def test_missing_configuration_is_reported_not_crashed(overrides):
    with pytest.raises(RtcUnavailable):
        rtc.build_rtc_token(make_settings(**overrides), "LOBBY", 1)
