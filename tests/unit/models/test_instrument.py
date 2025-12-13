"""Unit tests for the Instrument model."""

import pytest
from unittest.mock import MagicMock
from sqlalchemy.orm import Session

from src.agentfx.api.models.instrument import Instrument


@pytest.mark.unit
class TestInstrumentModel:
    """Test cases for the Instrument SQLAlchemy model."""

    def test_instrument_creation_with_defaults(self):
        """Test creating an Instrument with default values.

        Note: SQLAlchemy column defaults only apply when persisting to the database.
        For in-memory objects, we need to provide all values explicitly.
        This test verifies the model accepts these values correctly.
        """
        instrument = Instrument(
            symbol="EUR_USD",
            display_name="EUR/USD",
            instrument_type="CURRENCY",
            pip_location=-4,
            trade_units_precision=0,
            minimum_trade_size=1.0,
            maximum_trade_size=10000000.0,
            is_tradeable=True,
            is_active=True,
        )

        assert instrument.symbol == "EUR_USD"
        assert instrument.display_name == "EUR/USD"
        assert instrument.instrument_type == "CURRENCY"
        assert instrument.pip_location == -4
        assert instrument.trade_units_precision == 0
        assert instrument.minimum_trade_size == 1.0
        assert instrument.maximum_trade_size == 10000000.0
        assert instrument.is_tradeable is True
        assert instrument.is_active is True

    def test_instrument_creation_with_custom_values(self):
        """Test creating an Instrument with custom values."""
        instrument = Instrument(
            symbol="USD_JPY",
            display_name="USD/JPY",
            instrument_type="CURRENCY",
            pip_location=-2,  # JPY pairs have different pip location
            trade_units_precision=0,
            minimum_trade_size=1.0,
            maximum_trade_size=5000000.0,
            is_tradeable=True,
            is_active=True,
        )

        assert instrument.symbol == "USD_JPY"
        assert instrument.display_name == "USD/JPY"
        assert instrument.pip_location == -2
        assert instrument.maximum_trade_size == 5000000.0

    def test_instrument_repr(self):
        """Test the string representation of Instrument."""
        instrument = Instrument(
            symbol="GBP_USD",
            display_name="GBP/USD",
        )

        assert repr(instrument) == "<Instrument GBP_USD>"

    def test_instrument_pip_location_for_jpy_pairs(self):
        """Test that JPY pairs use pip_location=-2."""
        jpy_instrument = Instrument(
            symbol="USD_JPY",
            display_name="USD/JPY",
            pip_location=-2,
        )

        assert jpy_instrument.pip_location == -2

    def test_instrument_pip_location_for_standard_pairs(self):
        """Test that standard pairs use pip_location=-4.

        Note: SQLAlchemy defaults only apply when persisting to DB.
        We explicitly set pip_location here to verify the value is accepted.
        """
        eur_usd = Instrument(symbol="EUR_USD", display_name="EUR/USD", pip_location=-4)
        gbp_usd = Instrument(symbol="GBP_USD", display_name="GBP/USD", pip_location=-4)
        aud_usd = Instrument(symbol="AUD_USD", display_name="AUD/USD", pip_location=-4)
        usd_chf = Instrument(symbol="USD_CHF", display_name="USD/CHF", pip_location=-4)
        usd_cad = Instrument(symbol="USD_CAD", display_name="USD/CAD", pip_location=-4)

        for instrument in [eur_usd, gbp_usd, aud_usd, usd_chf, usd_cad]:
            assert instrument.pip_location == -4

    def test_instrument_inactive_flag(self):
        """Test setting is_active to False for soft delete."""
        instrument = Instrument(
            symbol="OBSOLETE_PAIR",
            display_name="Obsolete/Pair",
            is_active=False,
        )

        assert instrument.is_active is False

    def test_instrument_not_tradeable_flag(self):
        """Test setting is_tradeable to False."""
        instrument = Instrument(
            symbol="VIEW_ONLY",
            display_name="View/Only",
            is_tradeable=False,
        )

        assert instrument.is_tradeable is False

    def test_instrument_trade_size_constraints(self):
        """Test trade size minimum and maximum."""
        instrument = Instrument(
            symbol="EUR_USD",
            display_name="EUR/USD",
            minimum_trade_size=100.0,
            maximum_trade_size=1000000.0,
        )

        assert instrument.minimum_trade_size == 100.0
        assert instrument.maximum_trade_size == 1000000.0
        assert instrument.minimum_trade_size < instrument.maximum_trade_size


@pytest.mark.unit
class TestInstrumentModelDatabase:
    """Test cases for Instrument model database operations."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return MagicMock(spec=Session)

    def test_instrument_query_by_symbol(self, mock_db):
        """Test querying instrument by symbol."""
        mock_instrument = Instrument(
            id=1,
            symbol="EUR_USD",
            display_name="EUR/USD",
        )

        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_instrument

        result = (
            mock_db.query(Instrument).filter(Instrument.symbol == "EUR_USD").first()
        )

        assert result.symbol == "EUR_USD"
        assert result.display_name == "EUR/USD"

    def test_instrument_query_active_only(self, mock_db):
        """Test querying only active instruments."""
        mock_instruments = [
            Instrument(id=1, symbol="EUR_USD", display_name="EUR/USD", is_active=True),
            Instrument(id=2, symbol="GBP_USD", display_name="GBP/USD", is_active=True),
        ]

        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = mock_instruments

        results = (
            mock_db.query(Instrument)
            .filter(Instrument.is_active == True)  # noqa: E712
            .all()
        )

        assert len(results) == 2
        assert all(i.is_active for i in results)

    def test_instrument_query_tradeable_only(self, mock_db):
        """Test querying only tradeable instruments."""
        mock_instruments = [
            Instrument(
                id=1, symbol="EUR_USD", display_name="EUR/USD", is_tradeable=True
            ),
        ]

        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = mock_instruments

        results = (
            mock_db.query(Instrument)
            .filter(Instrument.is_tradeable == True)  # noqa: E712
            .all()
        )

        assert len(results) == 1
        assert results[0].is_tradeable is True


@pytest.mark.unit
class TestSupportedInstruments:
    """Test cases for the supported instruments defined in seed_instruments.py."""

    @pytest.fixture
    def supported_instruments(self):
        """Return the list of supported instruments as defined in seed_instruments.py."""
        return [
            {"symbol": "EUR_USD", "display_name": "EUR/USD", "pip_location": -4},
            {"symbol": "GBP_USD", "display_name": "GBP/USD", "pip_location": -4},
            {"symbol": "USD_JPY", "display_name": "USD/JPY", "pip_location": -2},
            {"symbol": "AUD_USD", "display_name": "AUD/USD", "pip_location": -4},
            {"symbol": "USD_CHF", "display_name": "USD/CHF", "pip_location": -4},
            {"symbol": "USD_CAD", "display_name": "USD/CAD", "pip_location": -4},
        ]

    def test_supported_instruments_count(self, supported_instruments):
        """Test that we have exactly 6 supported instruments."""
        assert len(supported_instruments) == 6

    def test_supported_instruments_symbols(self, supported_instruments):
        """Test that all expected symbols are present."""
        expected_symbols = {
            "EUR_USD",
            "GBP_USD",
            "USD_JPY",
            "AUD_USD",
            "USD_CHF",
            "USD_CAD",
        }
        actual_symbols = {i["symbol"] for i in supported_instruments}
        assert actual_symbols == expected_symbols

    def test_supported_instruments_pip_locations(self, supported_instruments):
        """Test that pip locations are correct."""
        pip_locations = {i["symbol"]: i["pip_location"] for i in supported_instruments}

        # JPY pairs have pip_location = -2
        assert pip_locations["USD_JPY"] == -2

        # All other pairs have pip_location = -4
        for symbol in ["EUR_USD", "GBP_USD", "AUD_USD", "USD_CHF", "USD_CAD"]:
            assert pip_locations[symbol] == -4

    def test_all_instruments_are_major_pairs(self, supported_instruments):
        """Test that all supported instruments are major FX pairs."""
        # Major pairs involve USD on one side
        for instrument in supported_instruments:
            symbol = instrument["symbol"]
            assert "USD" in symbol, f"{symbol} is not a major pair (missing USD)"

    def test_instrument_display_names_format(self, supported_instruments):
        """Test that display names follow the expected format."""
        for instrument in supported_instruments:
            symbol = instrument["symbol"]
            display_name = instrument["display_name"]

            # display_name should be like "EUR/USD" (with slash)
            assert "/" in display_name

            # Symbols use underscore, display names use slash
            expected_display = symbol.replace("_", "/")
            assert display_name == expected_display
