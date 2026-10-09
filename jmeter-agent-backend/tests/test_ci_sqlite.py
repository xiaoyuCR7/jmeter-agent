from run_with_patched_sqlite import is_wal_reset_safe


def test_wal_reset_safe_version_ranges_match_runtime_gate():
    assert not is_wal_reset_safe((3, 44, 5))
    assert is_wal_reset_safe((3, 44, 6))
    assert not is_wal_reset_safe((3, 50, 6))
    assert is_wal_reset_safe((3, 50, 7))
    assert not is_wal_reset_safe((3, 51, 2))
    assert is_wal_reset_safe((3, 51, 3))
    assert is_wal_reset_safe((3, 52, 0))

