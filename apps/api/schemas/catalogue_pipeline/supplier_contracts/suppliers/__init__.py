"""Registered supplier-source contract declarations."""

from .alfamedic import ALFAMEDIC_PRICE_LIST_V1
from .hills import HILLS_PRICE_LIST_V1
from .vetapet import VETAPET_NON_VET_PRICE_LIST_V1, VETAPET_VET_PRICE_LIST_V1

__all__ = [
    "ALFAMEDIC_PRICE_LIST_V1",
    "HILLS_PRICE_LIST_V1",
    "VETAPET_NON_VET_PRICE_LIST_V1",
    "VETAPET_VET_PRICE_LIST_V1",
]
