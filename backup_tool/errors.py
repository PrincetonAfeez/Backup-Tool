"""Custom exceptions for backup_tool."""


class BackupToolError(Exception):
    """Base class for all expected backup tool errors."""


class HashError(BackupToolError):
    """Raised when hashing fails."""


class StoreError(BackupToolError):
    """Raised when object storage fails."""


class ManifestError(BackupToolError):
    """Raised when a manifest is invalid or cannot be read."""


class RestoreError(BackupToolError):
    """Raised when restore cannot proceed safely."""


class IntegrityError(BackupToolError):
    """Raised when stored data fails integrity checks."""


class RepositoryError(BackupToolError):
    """Raised when repository state is invalid."""


class LockError(BackupToolError):
    """Raised when a repository lock cannot be acquired."""
