"""Typed failures raised while reading recommendation bundles."""


class BundleValidationError(ValueError):
    """Base class for invalid or incomplete recommendation bundles."""


class MissingBundleFileError(BundleValidationError):
    """A required bundle artifact is missing."""


class MissingColumnError(BundleValidationError):
    """A parquet artifact is missing a required column."""


class InvalidDtypeError(BundleValidationError):
    """An artifact column has an unsupported dtype or value representation."""


class DuplicateIdentifierError(BundleValidationError):
    """An artifact contains duplicate recommendation or evaluation identifiers."""


class InvalidRankError(BundleValidationError):
    """A recommendation rank is non-positive or duplicated within a set."""


class ForeignKeyError(BundleValidationError):
    """A recommendation refers to an evaluation that is absent from the bundle."""


class SchemaVersionError(BundleValidationError):
    """A recommendation has a missing or unsupported schema version."""


class ManifestCountError(BundleValidationError):
    """Manifest row counts do not match the parquet artifacts."""


class EvidenceEndDateError(BundleValidationError):
    """The configured evidence-end date is missing or invalid."""


class InvalidBundleError(BundleValidationError):
    """A bundle artifact cannot be decoded or has invalid metadata."""
