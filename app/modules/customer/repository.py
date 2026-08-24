import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, Any, List, Optional

from app.modules.customer.models import CustomerRecord
from app.integrations.iqms import IQMSClient

logger = logging.getLogger(__name__)


class CustomerRepository:
    """Repository handling DELMIAWorks / IQMS customer database lookups."""

    SIMILARITY_THRESHOLD = 0.80

    def __init__(self, iqms_client: Optional[IQMSClient] = None) -> None:
        self.iqms_client = iqms_client or IQMSClient()

    def _parse_iqms_record(
        self,
        item: Dict[str, Any],
        index: int
    ) -> Optional[CustomerRecord]:
        """Parse raw IQMS customer item into CustomerRecord."""

        if not isinstance(item, dict):
            return None

        # ---------------------------------------------------------
        # Extract Customer ID
        # ---------------------------------------------------------
        ar_custo_id = (
            item.get("ArCustoId")
            or item.get("ID")
            or item.get("Id")
            or item.get("AR_CUSTO_ID")
            or item.get("ARCUSTO_ID")
            or (index + 100000)
        )

        try:
            ar_custo_id = int(ar_custo_id)
        except (ValueError, TypeError):
            ar_custo_id = index + 100000

        # ---------------------------------------------------------
        # Extract Customer Name
        # ---------------------------------------------------------
        customer_name = (
            item.get("Company")
            or item.get("CustomerName")
            or item.get("NAME")
            or item.get("CompanyName")
            or item.get("COMPANY")
            or ""
        )

        if not customer_name or not str(customer_name).strip():
            return None

        customer_name = str(customer_name).strip()

        # ---------------------------------------------------------
        # Extract Customer Number
        # ---------------------------------------------------------
        customer_number = (
            item.get("CustNo")
            or item.get("CUSTNO")
            or item.get("CustomerNumber")
            or item.get("CUST_NO")
        )

        if customer_number:
            customer_number = str(customer_number).strip()
        else:
            customer_number = None

        # ---------------------------------------------------------
        # Build Address
        # ---------------------------------------------------------
        address_parts = []

        for key in [
            "Addr1",
            "Addr2",
            "City",
            "State",
            "Zip",
            "Country",
            "Address",
        ]:
            val = item.get(key)

            if val and str(val).strip():
                address_parts.append(str(val).strip())

        address = (
            ", ".join(address_parts)
            if address_parts
            else None
        )

        return CustomerRecord(
            ar_custo_id=ar_custo_id,
            customer_name=customer_name,
            customer_number=customer_number,
            address=address,
        )

    def get_all_records(self) -> List[CustomerRecord]:
        """Fetch customer records strictly from IQMS WebAPI."""

        raw_items = self.iqms_client.get_customers_lite()

        if raw_items:
            records = []

            for idx, item in enumerate(raw_items):
                parsed = self._parse_iqms_record(item, idx)

                if parsed:
                    records.append(parsed)

            if records:
                logger.info(
                    "Loaded %d customer records from IQMS WebAPI.",
                    len(records)
                )

                return records

        logger.warning(
            "No customer records retrieved from IQMS WebAPI."
        )

        return []

    @staticmethod
    def _normalize_customer_name(value: str) -> str:
        """
        Normalize customer name for matching.

        Examples:
            Coca-Cola      -> COCA COLA
            coca cola      -> COCA COLA
            Coca & Cola    -> COCA AND COLA
        """

        normalized = unicodedata.normalize(
            "NFKD",
            value or ""
        )

        # Remove accents
        normalized = "".join(
            char
            for char in normalized
            if not unicodedata.combining(char)
        )

        # Convert to uppercase
        normalized = normalized.upper()

        # Convert & to AND
        normalized = normalized.replace(
            "&",
            " AND "
        )

        # Replace punctuation/hyphens with spaces
        normalized = re.sub(
            r"[^A-Z0-9]+",
            " ",
            normalized
        )

        # Remove extra spaces
        return " ".join(normalized.split())

    @staticmethod
    def _calculate_similarity(
        search_name: str,
        customer_name: str
    ) -> float:
        """Calculate similarity between two normalized names."""

        return SequenceMatcher(
            None,
            search_name,
            customer_name
        ).ratio()

    def find_candidates(
        self,
        customer_name: str,
        customer_number: Optional[str] = None
    ) -> List[CustomerRecord]:
        """
        Find customer candidates using:

        1. Exact customer number
        2. Customer name starts-with
        3. Customer name similarity >= 80%

        Starts-with matches are prioritized over similarity matches.
        """

        records = self.get_all_records()

        if not records:
            return []

        # =========================================================
        # 1. CUSTOMER NUMBER MATCH
        # =========================================================

        if customer_number and customer_number.strip():

            num_clean = customer_number.strip().upper()

            num_matches = [
                record
                for record in records
                if (
                    record.customer_number
                    and record.customer_number.strip().upper()
                    == num_clean
                )
            ]

            if num_matches:
                logger.info(
                    "Customer matched by customer number: %s",
                    customer_number
                )

                return num_matches

        # =========================================================
        # 2. CUSTOMER NAME VALIDATION
        # =========================================================

        if not customer_name or not customer_name.strip():
            return []

        name_clean = self._normalize_customer_name(
            customer_name
        )

        if not name_clean:
            return []

        # =========================================================
        # 3. FIND STARTS-WITH + 80% SIMILARITY MATCHES
        # =========================================================

        candidates = []

        for record in records:

            record_name = self._normalize_customer_name(
                record.customer_name
            )

            if not record_name:
                continue

            # -----------------------------------------------------
            # Starts-with match
            #
            # Example:
            # Search:  ABC
            # Record:  ABC INC
            # Result:  True
            # -----------------------------------------------------

            starts_with = record_name.startswith(
                name_clean
            )

            # -----------------------------------------------------
            # Similarity match
            #
            # Example:
            # Search:  ABC GLOBAL INC
            # Record:  ABC GLOBAL INC.
            # Similarity: very high
            # -----------------------------------------------------

            similarity = self._calculate_similarity(
                name_clean,
                record_name
            )

            similarity_match = (
                similarity >= self.SIMILARITY_THRESHOLD
            )

            # -----------------------------------------------------
            # Accept if either condition is satisfied
            # -----------------------------------------------------

            if starts_with and similarity_match:

                candidates.append(
                    {
                        "record": record,
                        "similarity": similarity,
                        "starts_with": starts_with,
                    }
                )

        # =========================================================
        # 4. SORT RESULTS
        # =========================================================
        #
        # Priority:
        #
        # 1. Starts-with matches
        # 2. Highest similarity
        #
        # Example:
        #
        # Search: ABC
        #
        # ABC INC
        # ABC GLOBAL INC
        #
        # Both are starts-with matches.
        # They will be returned.
        # =========================================================

        candidates.sort(
            key=lambda item: (
                item["starts_with"],
                item["similarity"],
            ),
            reverse=True,
        )

        # =========================================================
        # 5. LOG MATCH INFORMATION
        # =========================================================

        logger.info(
            "Customer search '%s' returned %d candidates.",
            customer_name,
            len(candidates)
        )

        for candidate in candidates:

            logger.debug(
                "Customer candidate: %s | similarity=%.2f%% | starts_with=%s",
                candidate["record"].customer_name,
                candidate["similarity"] * 100,
                candidate["starts_with"],
            )

        # =========================================================
        # 6. RETURN ONLY CustomerRecord OBJECTS
        # =========================================================

        return [
            candidate["record"]
            for candidate in candidates
        ]