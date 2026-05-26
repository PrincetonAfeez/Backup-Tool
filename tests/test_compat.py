"""Tests for compatibility re-export modules."""

from backup_tool.gc import GCResult, PruneResult
from backup_tool.repository import GCResult as RepoGCResult
from backup_tool.repository import PruneResult as RepoPruneResult
from backup_tool.repository import CheckResult, VerifyResult
from backup_tool.verify import CheckResult as VerifyCheckResult
from backup_tool.verify import VerifyResult as VerifyVerifyResult


def test_gc_module_reexports():
    assert GCResult is RepoGCResult
    assert PruneResult is RepoPruneResult


def test_verify_module_reexports():
    assert VerifyResult is VerifyVerifyResult
    assert CheckResult is VerifyCheckResult
