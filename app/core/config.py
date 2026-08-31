import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


class Settings:
    # API key for IBM. Keep this in the environment.
    IBM_API_KEY: str = os.getenv("IBM_API_KEY", "")

    # Base URL for IBM OpenAI-compatible endpoint. Keep this in the environment.
    IBM_BASE_URL: str = os.getenv("IBM_BASE_URL", "")

    # Model name to use for generation
    MODEL_NAME: str = os.getenv("GEMINI_MODEL_NAME", "gemma-4-31b-it")

    # Path to poppler binaries (required for pdf2image). Keep this in the environment.
    POPPLER_PATH: str = os.getenv("POPPLER_PATH", "")

    # Folder path where PDF files are stored for local reference. Keep this in the environment.
    PDF_FOLDER_PATH: str = os.getenv("PDF_FOLDER_PATH", "")

    # Folder path where Stage 1 extracted markdown files are saved (within project directory).
    EXTRACTED_MARKDOWN_FOLDER_PATH: str = os.getenv(
        "EXTRACTED_MARKDOWN_FOLDER_PATH", str(BASE_DIR / "extracted_quotes")
    )

    # T21: SMTP server used to send notification emails. Account credentials are
    # supplied per-request (not stored here) - only the server address is configured.
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))

    # PostgreSQL quote-processing database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "").strip()

    # IQMS API Configuration
    IQMS_BASE_URL: str = os.getenv("IQMS_BASE_URL", "https://evco.iqtrain.iqms-cloud.net/WebAPI").rstrip("/")
    IQMS_APPLICATION_NAME: str = os.getenv("IQMS_APPLICATION_NAME", "WebAPI")
    IQMS_USERNAME: str = os.getenv("IQMS_USERNAME", "AIAPI")
    IQMS_PASSWORD: str = os.getenv("IQMS_PASSWORD", "Airwaves8#")
    IQMS_AUTH_TOKEN: str = os.getenv("IQMS_AUTH_TOKEN", "")

    # IBM App ID Configuration
    APPID_REGION: str = os.getenv("APPID_REGION", "us-south").strip()
    APPID_REQUIRED_SCOPE: str = os.getenv("APPID_REQUIRED_SCOPE", os.getenv("scopes", "appid_default")).strip()
    PROTECT_DOCS: bool = os.getenv("PROTECT_DOCS", "false").lower() in ("true", "1", "yes")

    # Environmental key mappings
    APPID_CLIENT_ID_RAW: str = os.getenv("APPID_CLIENT_ID", os.getenv("clientId", "")).strip()
    APPID_TENANT_ID_RAW: str = os.getenv("APPID_TENANT_ID", os.getenv("tenantId", "")).strip()
    APPID_OAUTH_SERVER_URL_RAW: str = os.getenv("APPID_OAUTH_SERVER_URL", os.getenv("oAuthServerUrl", "")).strip()

    # JWKS caching configuration
    JWKS_CACHE_LIFESPAN: int = int(os.getenv("JWKS_CACHE_LIFESPAN", "3600"))
    JWKS_TIMEOUT: int = int(os.getenv("JWKS_TIMEOUT", "10"))

    @property
    def APPID_CLIENT_ID(self) -> str:
        return self.APPID_CLIENT_ID_RAW

    @property
    def APPID_TENANT_ID(self) -> str:
        if self.APPID_TENANT_ID_RAW:
            return self.APPID_TENANT_ID_RAW
        if self.APPID_OAUTH_SERVER_URL_RAW:
            parts = self.APPID_OAUTH_SERVER_URL_RAW.rstrip("/").split("/")
            if parts:
                return parts[-1]
        return ""

    @property
    def APPID_OAUTH_SERVER_URL(self) -> str:
        if self.APPID_OAUTH_SERVER_URL_RAW:
            return self.APPID_OAUTH_SERVER_URL_RAW.rstrip("/")
        if self.APPID_TENANT_ID:
            return f"https://{self.APPID_REGION}.appid.cloud.ibm.com/oauth/v4/{self.APPID_TENANT_ID}"
        return ""

    @property
    def APPID_ISSUER(self) -> str:
        return self.APPID_OAUTH_SERVER_URL

    @property
    def APPID_JWKS_URL(self) -> str:
        if self.APPID_ISSUER:
            return f"{self.APPID_ISSUER}/publickeys"
        return ""

    # Configurable extraction prompt field aliases
    EXTRACTION_ALIAS_QUOTE_NUMBER: str = os.getenv(
        "EXTRACTION_ALIAS_QUOTE_NUMBER", "Quote#:, Quote No:, Quotation Number:, Quote Number, Quote ID:"
    )
    EXTRACTION_ALIAS_TYPE: str = os.getenv("EXTRACTION_ALIAS_TYPE", "Type:, Quote Type:")
    EXTRACTION_ALIAS_ISSUE_DATE: str = os.getenv("EXTRACTION_ALIAS_ISSUE_DATE", "Issue Date:, Date Issued:, Date:")
    EXTRACTION_ALIAS_PRICE_EFFECTIVE_DATE: str = os.getenv(
        "EXTRACTION_ALIAS_PRICE_EFFECTIVE_DATE", "Price Effective Date:, Effective Date:"
    )
    EXTRACTION_ALIAS_CUSTOMER_NAME: str = os.getenv("EXTRACTION_ALIAS_CUSTOMER_NAME", "Customer Name, Customer, Sold To")
    EXTRACTION_ALIAS_ADDRESS: str = os.getenv("EXTRACTION_ALIAS_ADDRESS", "Address, Customer Address, Ship To Address")

    EXTRACTION_ALIAS_MOLD: str = os.getenv("EXTRACTION_ALIAS_MOLD", "Mold, Mold#, Mold Number")
    EXTRACTION_ALIAS_EVCO_PN: str = os.getenv(
        "EXTRACTION_ALIAS_EVCO_PN", "EVCO PN, EVCO Part Number, EVCO Part#, EVCO Part #, EVCO #"
    )
    EXTRACTION_ALIAS_EVCO_MFG: str = os.getenv(
        "EXTRACTION_ALIAS_EVCO_MFG", "EVCO MFG (BOM), EVCO MFG #, EVCO MFG, EVCO BOM, BOM #, BOM"
    )
    EXTRACTION_ALIAS_CUSTOMER_PN: str = os.getenv(
        "EXTRACTION_ALIAS_CUSTOMER_PN",
        "Customer PN, Customer Part Number, Cust PN, Customer Part#, JD PN, JD Part #, RA Part #",
    )
    EXTRACTION_ALIAS_PART_DESC: str = os.getenv("EXTRACTION_ALIAS_PART_DESC", "Part Description, Description, Part Desc")
    EXTRACTION_ALIAS_BOX_QTY: str = os.getenv("EXTRACTION_ALIAS_BOX_QTY", "Box Qty, Box Quantity, Pkg Qty, Parts/Box")
    EXTRACTION_ALIAS_MOQ: str = os.getenv(
        "EXTRACTION_ALIAS_MOQ", "MOQ, Minimum Order Quantity, Min Order Qty, MRQ, Monthly MOQ, Release Qty, Release"
    )
    EXTRACTION_ALIAS_PRICING: str = os.getenv("EXTRACTION_ALIAS_PRICING", "Pricing, Price, Unit Price")

    @staticmethod
    def parse_aliases(raw_value: str) -> list[str]:
        if not raw_value:
            return []
        return [item.strip() for item in raw_value.split(",") if item.strip()]

    def format_aliases_prompt(self, raw_value: str) -> str:
        aliases = self.parse_aliases(raw_value)
        if not aliases:
            return ""
        if len(aliases) == 1:
            return f'"{aliases[0]}"'
        formatted_items = ", ".join(f'"{a}"' for a in aliases)
        return f"any of [{formatted_items}]"


settings = Settings()
