import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aemeath_visual_core.db import IdentityStore  # noqa: E402
from aemeath_visual_core.prompt import build_generation_prompt, identity_block  # noqa: E402


def test_store_and_prompt():
    tmp = Path(tempfile.mkdtemp())
    store = IdentityStore(tmp, ROOT / "assets" / "official")
    assert len(store.official_images()) == 13
    ident = store.identity()
    assert ident["fixed"]["hair_color"]
    refs = store.current_refs()
    assert refs and all(p.exists() for p in refs)
    prompt = build_generation_prompt(ident, scene="beach", pose="sitting on a rock")
    assert "beach" in prompt and "twin" not in prompt
    assert "visual" not in identity_block(ident).lower()
    assert identity_block(ident)

    store.add_outfit("test outfit", "test", [str(ROOT / "assets" / "official" / "official_full_01.png")])
    assert store.set_outfit("test outfit")
    assert store.identity()["variable"]["current_outfit"] == "test outfit"
    assert store.set_outfit("官方默认")

    store.record_generation("p", ["r"], "test", str(ROOT / "assets" / "official" / "official_face_01.png"))
    assert store.find_generation("p", ["r"])
    assert store.stats()["generations"] == 1
    store.record_generation("p2", ["r"], "test", str(ROOT / "assets" / "official" / "official_face_02.png"), 0.25)
    assert store.stats()["cost"] >= 0.25
    backup = store.backup(tmp / "backup.zip")
    assert backup.exists() and backup.stat().st_size > 0
    store.restore(backup)
    assert store.stats()["generations"] == 2
