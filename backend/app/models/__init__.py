"""SQLAlchemy models — import all models here so Alembic can discover them."""

from app.models.agent_control import (  # noqa: F401
    AgentControlAction,
    AgentControlState,
    AgentLifecycleOverride,
)
from app.models.agent_discovery import (  # noqa: F401
    AgentCloudProvider,
    AgentDiscoveryStatus,
    DiscoveredAgent,
)
from app.models.ai_asset import AIAsset  # noqa: F401
from app.models.ai_cost_record import (  # noqa: F401
    AICostRecord,
    CostKind,
    CostProvider,
    CostSource,
    CostSubjectKind,
)
from app.models.ai_cost_usage_batch import AICostUsageBatch  # noqa: F401
from app.models.ai_risk import AIRisk  # noqa: F401
from app.models.ai_use_case import AIUseCase  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.billing import BillingOutbox, StripeWebhookEvent, TrialEvent  # noqa: F401
from app.models.blob import BlobRecord  # noqa: F401
from app.models.compliance_assessment import ComplianceAssessment  # noqa: F401
from app.models.correlation import CorrelationActionPlan  # noqa: F401
from app.models.defender_import import (  # noqa: F401
    DefenderImportStatus,
    DiscoveredDefenderApp,
)
from app.models.developer import (  # noqa: F401
    DeveloperAPIKeyScope,
    DeveloperEvent,
    DeveloperEventDefinition,
    DeveloperScope,
    EventSeverity,
)
from app.models.device_directive import DeviceDirective  # noqa: F401
from app.models.directive_ack import DirectiveAck  # noqa: F401
from app.models.directory import (  # noqa: F401
    DirectoryGroup,
    DirectoryGroupMembership,
    DirectoryUser,
)
from app.models.dispatch import DispatchEvent, OutboxMessage  # noqa: F401
from app.models.drift_signal import (  # noqa: F401
    TenantDriftSignal,
    TenantDriftSignalKind,
    TenantDriftSignalSeverity,
)
from app.models.enrolled_device import EnrolledDevice  # noqa: F401
from app.models.extension_heartbeat import (  # noqa: F401
    ExtensionDeviceHeartbeat,
)
from app.models.handbook import (  # noqa: F401
    HandbookAcknowledgement,
    HandbookReminderLog,
    TenantHandbookOverride,
)
from app.models.integration import (  # noqa: F401
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.invite import Invite  # noqa: F401
from app.models.llm_usage import LLMUsageRecord  # noqa: F401
from app.models.managed_device import ManagedDevice  # noqa: F401
from app.models.mcp_discovery import DiscoveredMcpServer, McpDiscoveryStatus  # noqa: F401
from app.models.microsoft_sync_outputs import (  # noqa: F401
    CloudAppDetection,
    IntuneDevice,  # backwards-compat alias for ManagedDevice
    PurviewEvent,
)
from app.models.model_risk_profile import ModelRiskProfile  # noqa: F401
from app.models.policy_enforcement import (  # noqa: F401
    EnforcementMode,
    PolicyCategory,
    PolicyInstance,
    PolicyInstanceStatus,
    PolicySeverity,
    PolicyTemplate,
    PolicyViolation,
    RolloutStrategy,
)
from app.models.prompt_event import (  # noqa: F401
    PromptEvent,
)
from app.models.risk import RiskMitigation  # noqa: F401
from app.models.roi_assumptions import (  # noqa: F401
    DEFAULT_BLENDED_HOURLY_RATE_USD,
    HoursSavedSource,
    RoiAssumptions,
)
from app.models.saas_vendor import (  # noqa: F401
    AssessmentImportSource,
    AssessmentStatus,
    SaaSVendorProfile,
    VendorAssessmentImport,
    VendorAssessmentRequest,
    VendorCategory,
    VendorContractStatus,
    VendorDiscoveryMethod,
)
from app.models.sentinel_forward import (  # noqa: F401
    SentinelDeadLetter,
    SentinelDeadLetterStatus,
    SentinelForwardCursor,
)
from app.models.settings import TenantSetting  # noqa: F401
from app.models.slack_workspace import SlackWorkspace  # noqa: F401
from app.models.spm_entities import (  # noqa: F401
    DataStore,
    EntityReference,
    OrganizationalUnit,
    Person,
    TechnicalCapability,
    TechnologyProduct,
    TechnologyService,
)
from app.models.survey import (  # noqa: F401
    DeliveryChannel,
    DeliveryStatus,
    ResponseStatus,
    SlackOptOut,
    SurveyDelivery,
    SurveyResponse,
    SurveyTemplate,
)
from app.models.tenant import Organisation, Plan, SubscriptionStatus, Tenant  # noqa: F401
from app.models.use_case import (  # noqa: F401
    UseCase,
    UseCaseRiskTier,
    UseCaseSource,
    UseCaseStatus,
)
from app.models.use_case_review import (  # noqa: F401
    ReviewStatus,
    UseCaseReview,
)
from app.models.user import APIKey, Role, User, UserRole  # noqa: F401
