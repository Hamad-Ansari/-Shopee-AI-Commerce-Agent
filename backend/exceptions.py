class AppError(Exception):
    """Base class for all domain errors."""


class ProductNotFoundError(AppError):
    pass


class ProposalNotFoundError(AppError):
    pass


class PolicyViolationError(AppError):
    """Spending limit, quantity limit or availability violation."""


class RateLimitError(AppError):
    pass


class ApprovalInvalidError(AppError):
    """The approval no longer matches the proposal (price/qty/seller/shipping changed)."""


class InvalidTransitionError(AppError):
    """Illegal state-machine transition."""


class PermissionDeniedError(AppError):
    """A tool was called without the required permission level."""


class ToolNotFoundError(AppError):
    pass


class MarketplaceError(AppError):
    """The marketplace call failed."""


class MarketplaceTimeoutError(MarketplaceError):
    """The marketplace call timed out — the outcome is UNKNOWN, never blindly retry."""


class IntegrationUnavailableError(AppError):
    """No authorised marketplace/WhatsApp integration is connected."""
