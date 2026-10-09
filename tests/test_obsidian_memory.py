# tests/test_obsidian_memory.py —— Obsidian 接入：库发现 / 排除 V8 / 越界防护 / 触手永久记忆
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 全部在 tmp 库上跑，绝不碰主人的真实笔记库。
import json

import pytest

from body.obsidian import (ObsidianVault, TentacleMemory, VaultError, VaultMemoryStore,
                           discover_vaults, pick_vault)


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "Obsidian Vault"
    root.mkdir()
    (root / "主人的笔记.md").write_text("这是主人的私人笔记", encoding="utf-8")
    return ObsidianVault(str(root))


# ═══ 库发现：V8 与沙箱必须被排除，且说明原因 ═══
def test_vaults_exclude_v8_and_sandbox(tmp_path):
    # 用真实存在的目录（is_dir 是选库的硬条件，假路径会被过滤掉）
    for name in ("GBT小土豆V8的开心人生", "Obsidian Sandbox", "我的库"):
        (tmp_path / name).mkdir()
    cfg = tmp_path / "obsidian.json"
    cfg.write_text(json.dumps({"vaults": {
        "a": {"path": str(tmp_path / "GBT小土豆V8的开心人生"), "ts": 999},
        "b": {"path": str(tmp_path / "Obsidian Sandbox"), "ts": 888, "open": True},
        "c": {"path": str(tmp_path / "我的库"), "ts": 100},
    }}), encoding="utf-8")
    infos = {i.key: i for i in discover_vaults(config=cfg)}
    assert "v8" in infos["a"].excluded.lower() and infos["a"].excluded
    assert "sandbox" in infos["b"].excluded.lower()
    assert infos["c"].excluded == ""                      # 正常库不排除
    # 选库不能选到被排除的（哪怕它最新）
    picked = pick_vault(config=cfg)
    assert picked["root"] == str(tmp_path / "我的库")


def test_pick_vault_explicit_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("V9_VAULT_DIR", str(tmp_path / "指定库"))
    (tmp_path / "指定库").mkdir()
    assert pick_vault()["source"] == "explicit"


# ═══ 边界：越界必须拦住（两种分隔符都要拦）═══
@pytest.mark.parametrize("bad", ["../../越界.md", "..\\..\\越界.md",
                                 "触手/../../../越界.md"])
def test_path_traversal_is_refused(vault, bad):
    with pytest.raises(VaultError, match="越界"):
        vault.path_of(bad)


def test_writes_stay_inside_namespace_and_do_not_touch_host_notes(vault):
    vault.write("触手/GBT-D1.md", "内容")
    written = vault.path_of("触手/GBT-D1.md")
    assert written.is_file()
    assert "GBT小土豆V9-触手记忆" in str(written)          # 落在命名空间里
    assert (vault.root / "主人的笔记.md").read_text(encoding="utf-8") == "这是主人的私人笔记"
    assert vault.list_files("触手") == ["触手/GBT-D1.md"]   # 清单口径与 read 一致


# ═══ 触手永久记忆：建档 / 追加 / 回读 / 主脑检索 ═══
def test_tentacle_memory_roundtrip_and_append(vault):
    tm = TentacleMemory(vault)
    a = tm.remember("GBT-D7", "建档", {"role": "memory", "key_id": "fp123"})
    assert a["created"] is True
    b = tm.remember("GBT-D7", "第1次任务", {"task": "扫描 src", "ok": True})
    assert b["created"] is False                           # 追加而非覆盖
    r = tm.recall("GBT-D7")
    assert r["found"] and r["count"] == 2
    assert "建档" in r["entries"][0] and "第1次任务" in r["entries"][1]


def test_missing_tentacle_has_no_memory_yet(vault):
    r = TentacleMemory(vault).recall("GBT-D99")
    assert r["found"] is False and r["entries"] == []


def test_brain_can_read_and_search_every_tentacle(vault):
    tm = TentacleMemory(vault)
    for i in (1, 2, 3):
        tm.remember(f"GBT-D{i}", "建档", {"role": "scan", "key_id": "same-key"})
    assert tm.read_all()["notes"] == 3
    hits = tm.search_all("same-key")                       # 主脑全库检索
    assert len(hits) == 3
    assert any("GBT-D1.md" in h["note"] for h in hits)


def test_vault_memory_store_is_drop_in_for_memory_store(vault):
    store = VaultMemoryStore(vault)
    assert store.remember("GBT-D5", "偏好", "喜欢先看证据")["ok"] is True
    assert "喜欢先看证据" in store.recall("GBT-D5", key="偏好")
    assert store.recall("GBY-D404") is None                # 没记过就 None，不编


def test_digest_gives_tentacle_its_own_first_page(vault):
    tm = TentacleMemory(vault)
    d0 = tm.agent_digest("GBT-D9")
    assert "第一页" in d0                                  # 还没有记忆时如实说
    tm.remember("GBT-D9", "建档", {"role": "guard"})
    assert "建档" in tm.agent_digest("GBT-D9")
