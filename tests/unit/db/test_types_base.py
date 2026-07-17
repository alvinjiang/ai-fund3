"""Types and Base — SPEC-DOMAIN §2.

Locks the money-path types (Numeric, never float), the cross-dialect JsonType, the
``utc_now`` helper, and the TimestampMixin behaviour on SQLite.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from core.db.base import Base, TimestampMixin
from core.db.types import Cost, JsonType, Money, Price, Prob, utc_now


def test_utc_now_is_aware_utc():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 0
    # close to "now" (sanity)
    assert abs((now - datetime.now(UTC)).total_seconds()) < 5


def test_money_types_have_fixed_precision():
    assert (Money().precision, Money().scale) == (20, 2)
    assert (Price().precision, Price().scale) == (20, 6)
    assert (Cost().precision, Cost().scale) == (12, 6)
    assert (Prob().precision, Prob().scale) == (5, 4)


def test_jsontype_uses_jsonb_on_postgres_and_json_on_sqlite():
    jt = JsonType()
    assert isinstance(jt.load_dialect_impl(postgresql.dialect()), JSONB)
    assert isinstance(jt.load_dialect_impl(sqlite.dialect()), JSON)


def test_timestamp_mixin_and_money_roundtrip_on_sqlite(session):
    class _Thing(Base, TimestampMixin):
        __tablename__ = "_thing"
        id: Mapped[int] = mapped_column(primary_key=True)
        price: Mapped[Decimal] = mapped_column(Price)
        data: Mapped[dict] = mapped_column(JsonType)

    Base.metadata.create_all(session.bind)

    thing = _Thing(price=Decimal("123.456789"), data={"a": 1})
    session.add(thing)
    session.commit()
    session.refresh(thing)

    assert thing.price == Decimal("123.456789")  # 6 dp survives exactly
    assert thing.data == {"a": 1}
    assert thing.created_at is not None  # server_default func.now()
    assert thing.updated_at is None  # no update yet
