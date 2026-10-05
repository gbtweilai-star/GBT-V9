# tests/pg/conftest.py —— PG 集成测试夹具：自动起库 / 健康检查 / 每测试清表
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import os, subprocess, time
from pathlib import Path
import pytest

DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://test:test@127.0.0.1:55432/tentacle_test")

ROOT = Path(__file__).resolve().parents[2]


def _pg_up() -> bool:
    try:
        import psycopg2
        c = psycopg2.connect(DSN, connect_timeout=2)
        c.close()
        return True
    except Exception:
        return False


def _compose(*args):
    subprocess.run(["docker", "compose", "-f",
                    str(ROOT / "docker-compose.test.yml"), *args],
                   capture_output=True, text=True, timeout=120)


@pytest.fixture(scope="session", autouse=True)
def postgres_server():
    """会话级：确保 PG 在跑；起不来就整层跳过（不报失败）"""
    if _pg_up():
        yield; return
    _compose("up", "-d", "--wait")
    for _ in range(30):
        if _pg_up(): break
        time.sleep(1)
    if not _pg_up():
        pytest.skip("Postgres 未就绪（docker 不可用或端口占用）")
    yield
    # 不自动 down，方便连续跑；`make pg-down` 手动清


@pytest.fixture
def pg_ledger(postgres_server):
    """每个测试一个干净账本：清空所有表"""
    os.environ["DATABASE_URL"] = DSN
    from audit.ledger_pg import PGLedger
    led = PGLedger(dsn=DSN, minconn=1, maxconn=6)
    with led._tx() as c, c.cursor() as cur:
        cur.execute("""DROP TABLE IF EXISTS ledger, cross_tasks,
                       cross_arbitration, alert_events, alert_state,
                       db_size_samples, scaler_state, scale_audit,
                       action_log, voice_jobs, transcripts, mic_segments,
                       mic_events CASCADE""")
    led._init()                     # 重建 schema
    yield led
    led.close()


@pytest.fixture
def pg_conn(pg_ledger):
    """裸连接，给系统目录查询用"""
    return pg_ledger
