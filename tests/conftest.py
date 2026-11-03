import os

import pytest


@pytest.fixture
def db_url() -> str:
    """Connection string for integration tests; skip when no database is configured."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip("DATABASE_URL not set: run `make up` and create .env from .env.example")
    return url
