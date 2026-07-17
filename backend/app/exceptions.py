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


class DatabaseError(AuriError):
    """Raised for database-layer failures."""


class ConfessionNotFoundError(DatabaseError):
    """Raised when a confession lookup by ID finds no matching row."""


class DuplicateConfessionError(DatabaseError):
    """Raised when a confession violates a uniqueness constraint."""


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


class RateLimitError(ValidationError):
    """Raised when a device exceeds the confession submission rate limit."""
