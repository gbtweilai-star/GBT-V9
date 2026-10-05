# 根 conftest.py —— pg 集成测试默认跳过（--run-pg 且 Docker 起库后启用）
import pytest


def pytest_addoption(parser):
    parser.addoption("--run-pg", action="store_true",
                     help="运行 PostgreSQL 集成测试层")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-pg"):
        return
    skip = pytest.mark.skip(reason="需 --run-pg 且 docker 起库")
    for item in items:
        if "tests/pg/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)
