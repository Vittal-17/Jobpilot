import pytest
import os
import importlib

def test_auth_secret_key_validation_weak_prod():
    os.environ["ENVIRONMENT"] = "production"
    os.environ["AUTH_SECRET_KEY"] = "weak"
    with pytest.raises(ValueError, match="must be at least 16 characters long"):
        import main
        importlib.reload(main)

def test_auth_secret_key_validation_unsafe_default_prod():
    os.environ["ENVIRONMENT"] = "production"
    os.environ["AUTH_SECRET_KEY"] = "your_strong_internal_api_secret_key_here"
    with pytest.raises(ValueError, match="known unsafe default"):
        import main
        importlib.reload(main)

def test_auth_secret_key_validation_valid_prod():
    os.environ["ENVIRONMENT"] = "production"
    os.environ["AUTH_SECRET_KEY"] = "this_is_a_sufficiently_long_key_for_testing"
    import main
    importlib.reload(main) # Should not raise

def test_auth_secret_key_validation_test_env():
    os.environ["ENVIRONMENT"] = "test"
    os.environ["AUTH_SECRET_KEY"] = "test"
    import main
    importlib.reload(main) # Should not raise

def test_auth_secret_key_validation_test_prod():
    os.environ["ENVIRONMENT"] = "production"
    os.environ["AUTH_SECRET_KEY"] = "test"
    with pytest.raises(ValueError, match="must be at least 16 characters long"):
        import main
        importlib.reload(main)
