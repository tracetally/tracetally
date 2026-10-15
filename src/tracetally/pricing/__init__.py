# SPDX-License-Identifier: Apache-2.0
"""Versioned price tables. Prices are data, loaded locally, never fetched."""

from tracetally.pricing.table import PriceTable, load_prices

__all__ = ["PriceTable", "load_prices"]
