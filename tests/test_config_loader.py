from src.config_loader import default_redaction_config


def test_default_redaction_config_has_generic_local_rules_only():
    config = default_redaction_config()

    assert config.client == "local"
    assert config.keyword_rules == []
    assert config.address_rules == []
    assert config.filename_rules == []
    assert {"email", "phone", "account_number", "client_id", "abn", "tfn", "dob"}.issubset(config.field_rules)
