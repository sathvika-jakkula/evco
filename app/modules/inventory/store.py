import threading
from typing import Dict, List, Optional
from app.modules.inventory.schemas import (
    AkaRecordData,
    AkaStateBeforeAfter,
    BomCandidateData,
    CreateAkaRequest,
    CreateAkaResponseData,
    InventoryPartData,
    UpdateAkaRequest,
    UpdateAkaResponseData,
)


class InventoryMockStore:
    """In-memory mock store for EVCO Inventory AKA workflow with dynamic fallback generation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._parts: Dict[str, InventoryPartData] = {}
        self._boms: Dict[str, List[BomCandidateData]] = {}
        # Key: (customer_number, customer_part_number, item_number)
        self._akas: Dict[tuple[str, str, str], AkaRecordData] = {}

        self._seed_data()

    def _seed_data(self) -> None:
        # Seed default Inventory Parts
        self._parts["453543"] = InventoryPartData(
            evco_part_number="453543",
            item_number="453543",
            description="Flow Control Base Moulded",
            inventory_class="FG",
        )
        self._parts["9445626"] = InventoryPartData(
            evco_part_number="9445626",
            item_number="9445626",
            description="Flow Control Base Moulded",
            inventory_class="FG",
        )
        self._parts["777888"] = InventoryPartData(
            evco_part_number="777888",
            item_number="777888",
            description="Assembly Bracket Heavy Duty",
            inventory_class="FG",
        )

        # Seed default BOM Candidates
        self._boms["9445626"] = [
            BomCandidateData(
                manufacturing_bom_number="BOM-8798",
                bom_description="Flow Control Base Moulded",
                item_number="9445626",
            ),
            BomCandidateData(
                manufacturing_bom_number="BOM-8812",
                bom_description="Flow Control Base Alternate",
                item_number="9445626",
            ),
        ]
        self._boms["453543"] = [
            BomCandidateData(
                manufacturing_bom_number="BOM-8798",
                bom_description="Flow Control Base Moulded",
                item_number="453543",
            ),
        ]

        # Seed default Initial AKA Record
        initial_aka = AkaRecordData(
            customer_number="CUST-98211",
            customer_part_number="H4652 474 BASE",
            item_number="9445626",
            aka_description="FLOW CONTROL CLACK",
            item_description="Flow Control Base Moulded",
            uom="EA",
            currency="USD",
            manufacturing_bom_number="BOM-8798",
            moq=20,
            selling_multiples_of=10,
        )
        self._akas[("CUST-98211", "H4652 474 BASE", "9445626")] = initial_aka

    def search_part(self, evco_part_number: str) -> InventoryPartData:
        """Dynamically return part details for any searched part number."""
        with self._lock:
            if evco_part_number in self._parts:
                return self._parts[evco_part_number]

            # Dynamic dummy response echoing the searched part number
            return InventoryPartData(
                evco_part_number=evco_part_number,
                item_number=evco_part_number,
                description="Flow Control Base Moulded",
                inventory_class="FG",
            )

    def get_bom_candidates(self, evco_part_number: str) -> List[BomCandidateData]:
        """Dynamically return BOM candidates for any searched part number."""
        with self._lock:
            if evco_part_number in self._boms:
                return self._boms[evco_part_number]

            # Dynamic dummy response echoing the searched part number
            return [
                BomCandidateData(
                    manufacturing_bom_number="BOM-8798",
                    bom_description="Flow Control Base Moulded",
                    item_number=evco_part_number,
                )
            ]

    def get_aka(self, customer_number: str, customer_part_number: str, item_number: str) -> List[AkaRecordData]:
        """Dynamically return AKA record for any searched business key combination."""
        with self._lock:
            key = (customer_number, customer_part_number, item_number)
            if key in self._akas:
                return [self._akas[key]]

            # Dynamic dummy response echoing the requested business key
            dynamic_aka = AkaRecordData(
                customer_number=customer_number,
                customer_part_number=customer_part_number,
                item_number=item_number,
                aka_description="FLOW CONTROL CLACK",
                item_description="Flow Control Base Moulded",
                uom="EA",
                currency="USD",
                manufacturing_bom_number="BOM-8798",
                moq=20,
                selling_multiples_of=10,
            )
            return [dynamic_aka]

    def create_aka(self, req: CreateAkaRequest) -> Optional[CreateAkaResponseData]:
        """Store newly created AKA record for live state persistence."""
        with self._lock:
            key = (req.customer_number, req.aka_item_number, req.item_number)
            if key in self._akas:
                return None  # Record already exists (409)

            new_aka = AkaRecordData(
                customer_number=req.customer_number,
                customer_part_number=req.aka_item_number,
                item_number=req.item_number,
                aka_description=req.aka_description,
                item_description=req.item_description,
                uom=req.uom,
                currency=req.currency,
                manufacturing_bom_number=req.manufacturing_bom_number,
                moq=req.moq,
                selling_multiples_of=req.selling_multiples_of,
            )
            self._akas[key] = new_aka

            return CreateAkaResponseData(
                status="CREATED",
                customer_number=req.customer_number,
                aka_item_number=req.aka_item_number,
                item_number=req.item_number,
                manufacturing_bom_number=req.manufacturing_bom_number,
            )

    def update_aka(self, req: UpdateAkaRequest) -> UpdateAkaResponseData:
        """Dynamically update any AKA record, generating initial state if not previously created."""
        with self._lock:
            key = (req.customer_number, req.customer_part_number, req.item_number)
            existing = self._akas.get(key)

            if not existing:
                # Dynamic initial state for unseeded record
                existing = AkaRecordData(
                    customer_number=req.customer_number,
                    customer_part_number=req.customer_part_number,
                    item_number=req.item_number,
                    aka_description="FLOW CONTROL CLACK",
                    item_description="Flow Control Base Moulded",
                    uom="EA",
                    currency="USD",
                    manufacturing_bom_number="BOM-8798",
                    moq=20,
                    selling_multiples_of=10,
                )

            # Before state snapshot
            before_state = AkaStateBeforeAfter(
                aka_description=existing.aka_description,
                item_description=existing.item_description,
                currency=existing.currency,
                manufacturing_bom_number=existing.manufacturing_bom_number,
                moq=existing.moq,
                selling_multiples_of=existing.selling_multiples_of,
            )

            # Apply updates
            updated_aka = AkaRecordData(
                customer_number=existing.customer_number,
                customer_part_number=existing.customer_part_number,
                item_number=existing.item_number,
                aka_description=req.aka_description if req.aka_description is not None else existing.aka_description,
                item_description=req.item_description if req.item_description is not None else existing.item_description,
                uom=existing.uom,
                currency=req.currency if req.currency is not None else existing.currency,
                manufacturing_bom_number=req.manufacturing_bom_number if req.manufacturing_bom_number is not None else existing.manufacturing_bom_number,
                moq=req.moq if req.moq is not None else existing.moq,
                selling_multiples_of=req.selling_multiples_of if req.selling_multiples_of is not None else existing.selling_multiples_of,
            )
            self._akas[key] = updated_aka

            # After state snapshot
            after_state = AkaStateBeforeAfter(
                aka_description=updated_aka.aka_description,
                item_description=updated_aka.item_description,
                currency=updated_aka.currency,
                manufacturing_bom_number=updated_aka.manufacturing_bom_number,
                moq=updated_aka.moq,
                selling_multiples_of=updated_aka.selling_multiples_of,
            )

            return UpdateAkaResponseData(
                status="UPDATED",
                customer_number=req.customer_number,
                customer_part_number=req.customer_part_number,
                item_number=req.item_number,
                before=before_state,
                after=after_state,
            )


# Singleton instance for in-memory data persistence across HTTP requests
inventory_store = InventoryMockStore()
