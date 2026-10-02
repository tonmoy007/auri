"""Domain-specific exception hierarchy for the Auri backend.

Every exception raised from service and domain code must be one of these
types (or a subclass) — never a bare ``Exception`` — per AGENTS.md §15.2.
"""

from __future__ import annotations


class AuriError(Exception):
    """Base class for all Auri domain exceptions."""


class ProcessingError(AuriError):
    """Raised when a confession-processing pipeline step fails."""


class DeidentificationError(ProcessingError):
    """Raised when PII de-identification cannot be completed safely."""


class CategorizationError(ProcessingError):
    """Raised when LLM categorisation fails to produce a usable label."""


class SummarizationError(ProcessingError):
    """Raised when LLM summarisation fails to produce a usable summary."""


class SentimentError(ProcessingError):
    """Raised when LLM sentiment classification produces no usable label."""


class ThemeClusteringError(ProcessingError):
    """Raised when the model's grouping of summaries into themes is unusable."""


class ThemesEndpointError(ProcessingError):
    """Raised when the configured themes model address must not be used.

    Its message is safe to show: it never contains a key, a credential or the
    address itself.
    """


class CounselingError(ProcessingError):
    """Raised when LLM counseling-response generation fails to produce a usable reply."""


class PriestError(AuriError):
    """Base class for priest-mode (study-library guide) failures."""


class PriestIndexError(PriestError):
    """Raised when the knowledge index is missing, corrupt or inconsistent."""


class PriestEndpointError(PriestError):
    """Raised when the configured chat server address must not be used.

    Its message is safe to show: it never contains a key or the address itself.
    """


class PriestLLMError(PriestError):
    """Raised when no configured chat server returned a usable reply."""


class DatabaseError(AuriError):
    """Raised for database-layer failures."""


class ConfessionNotFoundError(DatabaseError):
    """Raised when a confession lookup by ID finds no matching row."""


class DuplicateConfessionError(DatabaseError):
    """Raised when a confession violates a uniqueness constraint."""


class DepartmentNotFoundError(DatabaseError):
    """Raised when a department lookup by name finds no matching row."""


class DuplicateDepartmentError(DatabaseError):
    """Raised when a department name is already in the directory."""


class DepartmentInUseError(DatabaseError):
    """Raised when deleting a department that still has undelivered confessions."""


class DuplicateUserError(DatabaseError):
    """Raised when creating a staff account whose email is already registered."""


class OidcError(AuriError):
    """A single sign-on attempt failed.

    ``code`` is one of a fixed set the dashboard shows (``failed``, ``no_account``,
    ``cancelled``); the message is for the log and never names a person.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AuthConfigurationError(AuriError):
    """Raised when session signing is attempted without a real secret configured."""


class ServiceError(AuriError):
    """Raised for failures in external-facing services (STT/TTS/voice)."""


class STTError(ServiceError):
    """Raised when speech-to-text transcription fails."""


class TTSError(ServiceError):
    """Raised when text-to-speech synthesis fails."""


class VoiceModulationError(ServiceError):
    """Raised when voice-mask modulation fails."""


class ValidationError(AuriError):
    """Raised when input fails domain validation rules."""


class EmptyConfessionError(ValidationError):
    """Raised when a confession transcript is empty after trimming."""


class PriestUnavailableError(ServiceError):
    """Raised when priest mode cannot serve a request right now.

    ``code`` is one of ``priest_mode_disabled``, ``priest_busy`` or
    ``priest_index_unavailable``; it is what the client sees, never a detail.
    """

    def __init__(self, code: str, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class RateLimitError(ValidationError):
    """Raised when a device exceeds the confession submission rate limit."""


class InvalidSessionTokenError(ValidationError):
    """Raised when a session token is missing, malformed, expired, or revoked."""


class JustificationRequiredError(ValidationError):
    """Raised when a raw-transcript read is attempted without a stated reason."""


class RawAccessNotPermittedError(ValidationError):
    """Raised when a confession is not eligible for raw-transcript access."""


class HrReplyInvalidError(ValidationError):
    """Raised when an HR reply is blank, too long, or contains forbidden characters."""


class ReplyNotPermittedError(ValidationError):
    """Raised when a confession's status does not accept an HR reply."""


class InvalidEmailError(ValidationError):
    """Raised when a staff account email is empty or structurally invalid."""


class WeakPasswordError(ValidationError):
    """Raised when a proposed staff password is below the minimum length."""
