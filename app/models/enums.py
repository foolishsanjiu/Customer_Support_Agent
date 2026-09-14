from enum import StrEnum


class CustomerLevel(StrEnum):
    NORMAL = "NORMAL"
    VIP = "VIP"
    SVIP = "SVIP"


class CustomerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REFUND_PENDING = "REFUND_PENDING"
    REFUNDED = "REFUNDED"


class ShipmentStatus(StrEnum):
    PENDING = "PENDING"
    IN_TRANSIT = "IN_TRANSIT"
    DELAYED = "DELAYED"
    DELIVERED = "DELIVERED"
    LOST = "LOST"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    PROCESSING = "PROCESSING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class TicketCategory(StrEnum):
    ORDER = "ORDER"
    SHIPPING = "SHIPPING"
    CANCELLATION = "CANCELLATION"
    REFUND = "REFUND"
    POLICY = "POLICY"
    OTHER = "OTHER"


class SenderType(StrEnum):
    CUSTOMER = "CUSTOMER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class RefundStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class AgentRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RESUME_PENDING = "RESUME_PENDING"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class PrincipalRole(StrEnum):
    CUSTOMER = "CUSTOMER"
    SUPPORT_AGENT = "SUPPORT_AGENT"
    MANAGER = "MANAGER"
    ADMIN = "ADMIN"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PolicyDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    REQUIRE_MANAGER_APPROVAL = "REQUIRE_MANAGER_APPROVAL"


class ToolRiskLevel(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class ToolCallStatus(StrEnum):
    RUNNING = "RUNNING"
    EXECUTED = "EXECUTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
