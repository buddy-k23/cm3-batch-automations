"""Smoke test: alembic and sqlalchemy are importable and installed."""
from importlib.metadata import version


def test_alembic_importable():
    import alembic  # noqa: F401
    ver = version("alembic")
    assert ver


def test_sqlalchemy_importable():
    import sqlalchemy
    assert sqlalchemy.__version__
