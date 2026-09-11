"""Versioned purchase-order contracts for the Daysmart PO sync (DEV-305).

Self-contained by design — shares the CIS-103 discipline with the catalogue
pipeline contracts, but none of their code. See ``base.py`` for why.
"""

from .base import ContractModel, get_contract_model, register_contract
from .commands_v1 import (
    CreateCommandParseResult,
    CreatePurchaseOrderCommandV1,
    CreatePurchaseOrderLineCommandV1,
    LineCommandsParseResult,
    UpdateCommandParseResult,
    UpdatePurchaseOrderCommandV1,
    parse_create_command,
    parse_line_commands,
    parse_update_command,
)
from .daysmart_created_order_v1 import (
    DaysmartCreatedOrderParseResult,
    DaysmartCreatedOrderV1,
    parse_daysmart_created_order,
)
from .daysmart_order_v1 import (
    STATUS_VOCABULARY,
    DaysmartOrderPageParseResult,
    DaysmartOrderParseResult,
    DaysmartOrderV1,
    DaysmartPageMetaV1,
    DaysmartSupplierRefV1,
    parse_daysmart_order,
    parse_daysmart_order_page,
)
from .issues import NON_BLOCKING_CODES, IssueCode, PoValidationIssue
from .mapper import (
    build_daysmart_create_payload,
    build_daysmart_line_payloads,
    build_daysmart_update_payload,
)

__all__ = [
    "NON_BLOCKING_CODES",
    "STATUS_VOCABULARY",
    "ContractModel",
    "CreateCommandParseResult",
    "CreatePurchaseOrderCommandV1",
    "CreatePurchaseOrderLineCommandV1",
    "LineCommandsParseResult",
    "UpdateCommandParseResult",
    "UpdatePurchaseOrderCommandV1",
    "build_daysmart_update_payload",
    "parse_update_command",
    "DaysmartCreatedOrderParseResult",
    "DaysmartCreatedOrderV1",
    "build_daysmart_create_payload",
    "build_daysmart_line_payloads",
    "parse_create_command",
    "parse_line_commands",
    "parse_daysmart_created_order",
    "DaysmartOrderPageParseResult",
    "DaysmartOrderParseResult",
    "DaysmartOrderV1",
    "DaysmartPageMetaV1",
    "DaysmartSupplierRefV1",
    "IssueCode",
    "PoValidationIssue",
    "get_contract_model",
    "parse_daysmart_order",
    "parse_daysmart_order_page",
    "register_contract",
]

from .daysmart_order_item_v1 import (  # noqa: E402
    DaysmartOrderItemPageMeta,
    DaysmartOrderItemPageParseResult,
    DaysmartOrderItemParseResult,
    DaysmartOrderItemV1,
    parse_daysmart_order_item,
    parse_daysmart_order_item_page,
)

__all__ += [
    "DaysmartOrderItemPageMeta",
    "DaysmartOrderItemPageParseResult",
    "DaysmartOrderItemParseResult",
    "DaysmartOrderItemV1",
    "parse_daysmart_order_item",
    "parse_daysmart_order_item_page",
]

from .daysmart_receipt_v1 import (  # noqa: E402
    RECEIPT_STATUS_VOCABULARY,
    ROW_IDENTITY_PATHS,
    DaysmartReceiptDetailParseResult,
    DaysmartReceiptPageParseResult,
    DaysmartReceiptParseResult,
    DaysmartReceiptRowParseResult,
    DaysmartReceiptRowsParseResult,
    DaysmartReceiptRowV1,
    DaysmartReceiptV1,
    parse_daysmart_receipt,
    parse_daysmart_receipt_detail,
    parse_daysmart_receipt_page,
    parse_daysmart_receipt_row,
    parse_daysmart_receipt_rows,
)

__all__ += [
    "RECEIPT_STATUS_VOCABULARY",
    "ROW_IDENTITY_PATHS",
    "DaysmartReceiptDetailParseResult",
    "DaysmartReceiptPageParseResult",
    "DaysmartReceiptParseResult",
    "DaysmartReceiptRowParseResult",
    "DaysmartReceiptRowsParseResult",
    "DaysmartReceiptRowV1",
    "DaysmartReceiptV1",
    "parse_daysmart_receipt",
    "parse_daysmart_receipt_detail",
    "parse_daysmart_receipt_page",
    "parse_daysmart_receipt_row",
    "parse_daysmart_receipt_rows",
]
