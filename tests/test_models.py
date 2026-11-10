"""The schema enforces business rules; these tests protect them from regressions."""

from sqlalchemy import BigInteger, UniqueConstraint

from subastacampo.db import models  # noqa: F401  (registers every table)
from subastacampo.db.base import Base


def test_all_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {"users", "products", "auctions", "bids", "auto_bids"}


def test_database_allows_only_one_active_auction_per_product() -> None:
    auctions = Base.metadata.tables["auctions"]
    index = next(i for i in auctions.indexes if i.name == "uq_auctions_one_active_per_product")

    assert index.unique
    assert str(index.dialect_options["postgresql"]["where"]) == "status = 'active'"


def test_bids_are_idempotent_per_bidder_and_request() -> None:
    bids = Base.metadata.tables["bids"]
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in bids.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("bidder_id", "request_id") in unique_columns


def test_money_is_stored_as_whole_pesos() -> None:
    money_columns = [
        ("auctions", "starting_price"),
        ("auctions", "min_increment"),
        ("auctions", "current_price"),
        ("bids", "amount"),
    ]
    for table, column in money_columns:
        assert isinstance(Base.metadata.tables[table].c[column].type, BigInteger)


def test_constraints_follow_the_naming_convention() -> None:
    check_names = {
        constraint.name
        for constraint in Base.metadata.tables["bids"].constraints
        if constraint.name and constraint.name.startswith("ck_")
    }

    assert "ck_bids_amount_positive" in check_names
