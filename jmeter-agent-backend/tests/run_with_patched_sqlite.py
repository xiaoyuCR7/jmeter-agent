"""Run pytest with a verified Linux pysqlite3 binding, including child probes."""
import os
import sys


def is_wal_reset_safe(version):
    version = tuple(version)
    return (version >= (3, 51, 3)
            or version[:2] == (3, 50) and version[2] >= 7
            or version[:2] == (3, 44) and version[2] >= 6)


def main():
    try:
        import pysqlite3
    except ImportError as error:
        raise SystemExit('pysqlite3-binary is required for the full Linux test lane') from error

    if not is_wal_reset_safe(pysqlite3.sqlite_version_info):
        raise SystemExit(
            f'pysqlite3 uses unsafe SQLite {pysqlite3.sqlite_version}; '
            'the full coordination suite will not run'
        )

    sys.modules['sqlite3'] = pysqlite3
    if hasattr(pysqlite3, 'dbapi2'):
        sys.modules['sqlite3.dbapi2'] = pysqlite3.dbapi2
    os.environ['JMETER_AGENT_TEST_PYSQLITE3'] = '1'

    import pytest
    return pytest.main(sys.argv[1:])


if __name__ == '__main__':
    raise SystemExit(main())

