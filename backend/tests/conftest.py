import os

import pytest

os.environ.setdefault("ACH_ENV", "sandbox")
os.environ.setdefault("ACH_SEED_DEMO_DATA", "false")   # tests seed explicitly
TEST_DB = os.environ.get("ACH_TEST_DATABASE_URL", "sqlite:///./test_ach.db")
os.environ["ACH_DATABASE_URL"] = TEST_DB

from fastapi.testclient import TestClient  # noqa: E402

from app import db as dbmod  # noqa: E402
from app.db import Base  # noqa: E402
from app.services.seed import seed  # noqa: E402

OPS = {"Authorization": "Bearer sk_ops_demo"}
ACME = {"Authorization": "Bearer sk_acme_demo"}
BRIGHT = {"Authorization": "Bearer sk_bright_demo"}
NORTH = {"Authorization": "Bearer sk_north_demo"}


@pytest.fixture()
def client():
    engine = dbmod.init_engine(TEST_DB)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = dbmod.SessionLocal()
    seed(s, demo_activity=True)
    s.commit()
    s.close()
    from app.main import app
    with TestClient(app) as c:
        yield c
    engine.dispose()


@pytest.fixture()
def db():
    s = dbmod.SessionLocal()
    yield s
    s.close()
