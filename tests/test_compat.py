"""Tests for compatibility re-export modules."""

from backup_tool.gc import GCResult
from backup_tool.repository import GCResult as RepoGCResult
from backup_tool.repository import CheckResult, PruneResult, VerifyResult
from backup_tool.verify import CheckResult as VerifyCheckResult
from backup_tool.verify import VerifyResult as VerifyVerifyResult


def test_gc_module_exports_gc_result():
    assert GCResult is RepoGCResult


def test_verify_module_reexports():
    assert VerifyResult is VerifyVerifyResult
    assert CheckResult is VerifyCheckResult


def test_repository_reexports_result_types():
    assert PruneResult.__module__ == "backup_tool.repository"
