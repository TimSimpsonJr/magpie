from tests.golden._adapters import extract_state, reason_category


def test_extract_state_last_token_and_sc_special_case():
    assert extract_state("Houston TX Police Department") == "TX"
    assert extract_state("Greenville County SC Sheriff") == "SC"
    assert extract_state("South Carolina Law Enforcement Div") == "SC"  # spelled-out
    assert extract_state("Springfield Police") is None                  # no token
    assert extract_state(None) is None
    # last token wins when several appear
    assert extract_state("Kansas City MO PD KS") == "KS"


def test_reason_category_splits_on_dash():
    assert reason_category("Immigration (criminal) - assist ICE") == "Immigration (criminal)"
    assert reason_category("Traffic Infraction") == "Traffic Infraction"
    assert reason_category("") == ""
    assert reason_category(None) == ""
