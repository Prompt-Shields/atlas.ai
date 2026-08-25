"""Defender InputAdapter — Microsoft Defender data source connector.

This is a STUB implementation with clear extension points.
It returns deterministic sample data for development and testing.

HOW TO IMPLEMENT A REAL DEFENDER CONNECTOR:
1. Install azure-mgmt-security and azure-identity packages
2. Create SecurityCenter client using DefaultAzureCredential
3. In fetch_blobs(), query Defender for Cloud alerts and recommendations
4. Transform each alert/recommendation into BlobData objects
5. Handle pagination and API throttling
6. Implement incremental fetch using alert timestamps

EXTENSION POINTS:
- Override fetch_blobs() to connect to real Defender APIs
- Use Microsoft Graph Security API for unified security alerts
- Implement webhook receiver for real-time alert ingestion
- Add filtering by severity, category, and resource type
- Support multiple Defender products (Endpoint, Identity, Cloud Apps)

REQUIRED CONFIG (add to Settings):
- DEFENDER_SUBSCRIPTION_ID: str
- DEFENDER_RESOURCE_GROUP: str (optional)
- DEFENDER_ALERT_SEVERITY_FILTER: str (optional, e.g. "High,Critical")
"""

from __future__ import annotations

from typing import Any

import structlog

from app.adapters.base import BlobData, InputAdapter, register_adapter

logger = structlog.get_logger()

# Sample data for stub implementation
SAMPLE_DEFENDER_DATA = [
    {
        "id": "defender-alert-001",
        "title": "Suspicious Sign-in Activity Detected",
        "content": (
            "Microsoft Defender for Identity detected anomalous sign-in patterns "
            "for 12 user accounts across the organisation. Alert details: "
            "Impossible travel detected for user john.doe@contoso.com — login from "
            "New York at 14:00 UTC followed by login from Tokyo at 14:15 UTC. "
            "Brute force attempts detected against 5 service accounts with 2,340 "
            "failed attempts in 1 hour. Lateral movement detected: compromised account "
            "admin-svc accessed 8 servers not in normal baseline. Risk score: 87/100. "
            "Recommended: Force password reset for affected accounts, enable conditional "
            "access policies, review MFA enrollment status."
        ),
        "metadata": {
            "source": "microsoft_defender",
            "product": "defender_for_identity",
            "alert_severity": "high",
            "alert_date": "2025-01-15T14:30:00Z",
            "affected_users": 12,
            "mitre_tactics": ["Initial Access", "Lateral Movement"],
        },
    },
    {
        "id": "defender-alert-002",
        "title": "Cloud Infrastructure Misconfigurations",
        "content": (
            "Defender for Cloud security posture assessment found 34 critical "
            "misconfigurations across Azure resources. Key findings: "
            "5 storage accounts with public blob access enabled, "
            "3 SQL servers without Advanced Threat Protection, "
            "8 VMs with unpatched critical vulnerabilities (CVE-2024-21413, "
            "CVE-2024-21410), 2 Key Vaults without soft-delete enabled, "
            "7 NSGs with overly permissive inbound rules (0.0.0.0/0 on SSH/RDP), "
            "9 resources without diagnostic logging enabled. Secure score impact: "
            "Current score 62/100, potential improvement to 84/100 if all findings "
            "are remediated. Priority: Address storage account exposure and VM "
            "patching within 24 hours."
        ),
        "metadata": {
            "source": "microsoft_defender",
            "product": "defender_for_cloud",
            "alert_severity": "critical",
            "alert_date": "2025-01-15T12:00:00Z",
            "finding_count": 34,
            "secure_score": 62,
        },
    },
]


class DefenderAdapter(InputAdapter):
    """Stub connector for Microsoft Defender.

    In production, this would authenticate to Azure and fetch
    security alerts, recommendations, and posture findings from
    various Defender products.
    """

    async def fetch_blobs(self, **kwargs: Any) -> list[BlobData]:
        """Stub: returns sample Defender alert data.

        In production, replace with:
            client = SecurityCenter(credential, subscription_id)
            alerts = client.alerts.list()
            return [BlobData(...) for alert in alerts]
        """
        logger.info(
            "defender_adapter_fetch",
            note="Using stub data — implement real Defender connector for production",
        )

        return [
            BlobData(
                content=item["content"],
                title=item["title"],
                source_id=item["id"],
                metadata=item["metadata"],
            )
            for item in SAMPLE_DEFENDER_DATA
        ]


# Register
register_adapter("defender", DefenderAdapter)
