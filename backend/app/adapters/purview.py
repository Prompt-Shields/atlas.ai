"""Purview InputAdapter — Microsoft Purview data source connector.

This is a STUB implementation with clear extension points.
It returns deterministic sample data for development and testing.

HOW TO IMPLEMENT A REAL PURVIEW CONNECTOR:
1. Install azure-purview-scanning and azure-identity packages
2. Create a Purview client using DefaultAzureCredential
3. In fetch_blobs(), query the Purview catalog API for data assets
4. Transform each asset's metadata + content into BlobData objects
5. Handle pagination, rate limiting, and authentication renewal
6. Add configuration for Purview account name, collection, filters

EXTENSION POINTS:
- Override fetch_blobs() to connect to real Purview API
- Add authentication via managed identity or service principal
- Implement incremental sync using Purview's change tracking
- Add filtering by data classification labels
- Implement content extraction from different asset types

REQUIRED CONFIG (add to Settings):
- PURVIEW_ACCOUNT_NAME: str
- PURVIEW_COLLECTION_NAME: str (optional)
- PURVIEW_SCAN_FILTER: str (optional, JSON filter)
"""

from __future__ import annotations

from typing import Any

import structlog

from app.adapters.base import BlobData, InputAdapter, register_adapter

logger = structlog.get_logger()

# Sample data for stub implementation
SAMPLE_PURVIEW_DATA = [
    {
        "id": "purview-asset-001",
        "title": "Customer PII Data Classification Report",
        "content": (
            "Data classification scan detected 1,247 instances of PII data across "
            "3 Azure SQL databases. Categories include: email addresses (456), "
            "phone numbers (312), social security numbers (89), credit card numbers (45), "
            "and physical addresses (345). Sensitivity labels: Confidential (534), "
            "Highly Confidential (713). Databases affected: CustomerDB, OrdersDB, "
            "AnalyticsDB. Recommended actions: Apply column-level encryption to SSN "
            "and credit card fields, enable dynamic data masking for non-prod environments, "
            "review access policies for AnalyticsDB."
        ),
        "metadata": {
            "source": "microsoft_purview",
            "scan_type": "data_classification",
            "scan_date": "2025-01-15T10:30:00Z",
            "asset_count": 1247,
            "sensitivity": "high",
        },
    },
    {
        "id": "purview-asset-002",
        "title": "Data Governance Compliance Gap Analysis",
        "content": (
            "Purview governance scan identified 23 data assets without assigned owners, "
            "15 datasets missing sensitivity labels, and 8 data pipelines without lineage "
            "documentation. Critical findings: The HR database contains GDPR-relevant data "
            "without proper data processing agreements documented. The Marketing analytics "
            "pipeline processes customer behavioral data without consent tracking. "
            "Recommendation: Implement mandatory data stewardship program, automate "
            "sensitivity labeling using Purview auto-labeling policies."
        ),
        "metadata": {
            "source": "microsoft_purview",
            "scan_type": "governance_compliance",
            "scan_date": "2025-01-15T11:00:00Z",
            "gap_count": 46,
            "severity": "medium",
        },
    },
]


class PurviewAdapter(InputAdapter):
    """Stub connector for Microsoft Purview.

    In production, this would authenticate to Azure Purview and
    fetch data classification results, governance findings, and
    data lineage information.
    """

    async def fetch_blobs(self, **kwargs: Any) -> list[BlobData]:
        """Stub: returns sample Purview scan data.

        In production, replace with:
            client = PurviewClient(account_name=...)
            assets = await client.discovery.query(...)
            return [BlobData(...) for asset in assets]
        """
        logger.info(
            "purview_adapter_fetch",
            note="Using stub data — implement real Purview connector for production",
        )

        return [
            BlobData(
                content=item["content"],
                title=item["title"],
                source_id=item["id"],
                metadata=item["metadata"],
            )
            for item in SAMPLE_PURVIEW_DATA
        ]


# Register
register_adapter("purview", PurviewAdapter)
