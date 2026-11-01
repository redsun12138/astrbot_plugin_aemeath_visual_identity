"""SQLite 视觉身份档案。"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any


DEFAULT_IDENTITY = {
    "name": "爱弥斯",
    "style": "官方二次元插画",
    "fixed": {
        "hair_color": "粉色",
        "hair_style": "长发双马尾",
        "bangs": "分层斜刘海，额前碎发",
        "side_hair": "脸侧长碎发",
        "back_hair": "长直发，双马尾",
        "hair_ornament": "蓝银色羽翼/星形发饰，位于头部两侧",
        "eye_color": "琥珀金/橙色，星形瞳孔",
        "eye_shape": "大眼，眼尾微挑",
        "face_style": "柔和圆润的少女脸",
        "outfit_default": "白蓝色舞台礼服，露肩，金属肩饰",
    },
    "variable": {
        "current_outfit": "官方默认",
    },
    "refs": {
        "face": "official_face_01.png",
        "full": "official_full_01.png",
        "side": "official_side_01.png",
    },
    "confidence": 0.9,
    "source": "official_assets_seed",
    "version": 1,
    "updated_at": 0,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  kind TEXT NOT NULL,
  source TEXT NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0,
  meta TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS identity (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  data TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outfits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  refs TEXT NOT NULL DEFAULT '[]',
  confirmed INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS generations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prompt TEXT NOT NULL,
  refs TEXT NOT NULL DEFAULT '[]',
  backend TEXT NOT NULL,
  path TEXT NOT NULL,
  cost REAL NOT NULL DEFAULT 0,
  meta TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visual_context (
  umo TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS visual_memory (
  umo TEXT PRIMARY KEY,
  activity TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  outfit TEXT NOT NULL DEFAULT '',
  mood TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'context_ai',
  confidence REAL NOT NULL DEFAULT 0,
  observed_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class IdentityStore:
    def __init__(self, data_dir: Path, bundled_assets: Path):
        self.data_dir = data_dir
        self.assets_src = bundled_assets
        self.assets_dir = data_dir / "images"
        self.official_dir = self.assets_dir / "official"
        self.outfits_dir = self.assets_dir / "outfits"
        self.generated_dir = self.assets_dir / "generated"
        self.db_path = data_dir / "identity.db"
        for p in (data_dir, self.official_dir, self.outfits_dir, self.generated_dir):
            p.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.import_official_assets()
        self.seed_identity()

    def import_official_assets(self) -> None:
        if not self.assets_src.is_dir():
            return
        for src in sorted(self.assets_src.glob("*.png")):
            dst = self.official_dir / src.name
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                shutil.copy2(src, dst)
            digest = sha256_file(dst)
            kind = "official"
            self.conn.execute(
                "INSERT OR IGNORE INTO assets(path,sha256,kind,source,confirmed,meta,created_at) VALUES(?,?,?,?,?,?,?)",
                (str(dst), digest, kind, "official_gallery", 1, json.dumps({"name": src.name}, ensure_ascii=False), time.time()),
            )
        self.conn.commit()

    def seed_identity(self) -> None:
        row = self.conn.execute("SELECT id FROM identity WHERE id=1").fetchone()
        if row:
            return
        data = dict(DEFAULT_IDENTITY)
        data["updated_at"] = time.time()
        self.conn.execute(
            "INSERT INTO identity(id,data,version,updated_at) VALUES(1,?,?,?)",
            (json.dumps(data, ensure_ascii=False), data["version"], data["updated_at"]),
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO outfits(name,description,refs,confirmed,created_at) VALUES(?,?,?,?,?)",
            ("官方默认", "白蓝色舞台礼服", json.dumps(["official_full_01.png"], ensure_ascii=False), 1, time.time()),
        )
        self.conn.commit()

    def identity(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT data FROM identity WHERE id=1").fetchone()
        return json.loads(row["data"]) if row else dict(DEFAULT_IDENTITY)

    def save_identity(self, data: dict[str, Any]) -> None:
        data = dict(data)
        data["version"] = int(data.get("version", 1)) + 1
        data["updated_at"] = time.time()
        self.conn.execute(
            "INSERT INTO identity(id,data,version,updated_at) VALUES(1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, version=excluded.version, updated_at=excluded.updated_at",
            (json.dumps(data, ensure_ascii=False), data["version"], data["updated_at"]),
        )
        self.conn.commit()

    def official_images(self) -> list[Path]:
        return sorted(self.official_dir.glob("*.png"))

    def ref_path(self, name_or_path: str) -> Path | None:
        p = Path(name_or_path)
        if p.is_absolute() and p.exists():
            return p
        for root in (self.official_dir, self.outfits_dir):
            candidate = root / p.name
            if candidate.exists():
                return candidate
        candidate = self.official_dir / p.name
        return candidate if candidate.exists() else None

    def current_refs(self, full: bool = False) -> list[Path]:
        ident = self.identity()
        names = ident.get("refs", {})
        ordered = [names.get("face"), names.get("full"), names.get("side")]
        if not full:
            ordered = [names.get("full"), names.get("face")]
        current = ident.get("variable", {}).get("current_outfit", "官方默认")
        if current and current != "官方默认":
            ordered = [names.get("face"), names.get("full")] + self.outfit_refs(current)
        out = []
        for name in ordered:
            if name:
                p = self.ref_path(str(name))
                if p and p not in out:
                    out.append(p)
        return out

    def list_outfits(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT name,description,refs,confirmed FROM outfits ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def set_outfit(self, name: str) -> bool:
        row = self.conn.execute("SELECT name FROM outfits WHERE name=?", (name,)).fetchone()
        if not row:
            return False
        data = self.identity()
        data.setdefault("variable", {})["current_outfit"] = name
        self.save_identity(data)
        return True

    def add_outfit(self, name: str, description: str = "", refs: list[str] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO outfits(name,description,refs,confirmed,created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET description=excluded.description, refs=excluded.refs",
            (name, description, json.dumps(refs or [], ensure_ascii=False), 1, time.time()),
        )
        self.conn.commit()

    def outfit_refs(self, name: str) -> list[Path]:
        row = self.conn.execute("SELECT refs FROM outfits WHERE name=?", (name,)).fetchone()
        if not row:
            return []
        out = []
        for item in json.loads(row["refs"] or "[]"):
            p = self.ref_path(str(item))
            if p:
                out.append(p)
        return out

    def find_generation(self, prompt: str, refs: list[str]) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT path,backend,created_at FROM generations WHERE prompt=? AND refs=? ORDER BY id DESC LIMIT 1",
            (prompt, json.dumps(refs, ensure_ascii=False)),
        ).fetchone()
        if row and Path(row["path"]).exists():
            return dict(row)
        return None

    def save_visual_context(self, umo: str, text: str) -> None:
        self.conn.execute(
            "INSERT INTO visual_context(umo,text,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(umo) DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at",
            (umo, text[:4000], time.time()),
        )
        self.conn.commit()

    def get_visual_context(self, umo: str, max_age_seconds: int = 86400) -> str:
        row = self.conn.execute(
            "SELECT text, updated_at FROM visual_context WHERE umo=?", (umo,)
        ).fetchone()
        if not row or time.time() - float(row["updated_at"]) > max_age_seconds:
            return ""
        return str(row["text"] or "")

    def save_visual_memory(self, umo: str, data: dict[str, Any], ttl_seconds: int = 21600) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO visual_memory(umo,activity,location,outfit,mood,source,confidence,observed_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(umo) DO UPDATE SET "
            "activity=excluded.activity,location=excluded.location,outfit=excluded.outfit,"
            "mood=excluded.mood,source=excluded.source,confidence=excluded.confidence,"
            "observed_at=excluded.observed_at,expires_at=excluded.expires_at",
            (
                umo,
                str(data.get("activity") or ""),
                str(data.get("location") or ""),
                str(data.get("outfit") or ""),
                str(data.get("mood") or ""),
                str(data.get("source") or "context_ai"),
                float(data.get("confidence") or 0),
                now,
                now + ttl_seconds,
            ),
        )
        self.conn.commit()

    def get_visual_memory(self, umo: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT activity,location,outfit,mood,source,confidence,observed_at,expires_at "
            "FROM visual_memory WHERE umo=?", (umo,)
        ).fetchone()
        if not row:
            return {}
        if float(row["expires_at"]) <= time.time():
            return {}
        data = dict(row)
        data["age_seconds"] = max(0, int(time.time() - float(row["observed_at"])))
        return data

    def record_generation(self, prompt: str, refs: list[str], backend: str, path: str, cost: float = 0.0) -> None:
        self.conn.execute(
            "INSERT INTO generations(prompt,refs,backend,path,cost,created_at) VALUES(?,?,?,?,?,?)",
            (prompt, json.dumps(refs, ensure_ascii=False), backend, path, cost, time.time()),
        )
        self.conn.commit()

    def stats(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT COUNT(*) c, COALESCE(SUM(cost),0) cost FROM generations").fetchone()
        assets = self.conn.execute("SELECT COUNT(*) c FROM assets").fetchone()["c"]
        return {"generations": row["c"], "assets": assets, "cost": row["cost"], "db": str(self.db_path)}

    def backup(self, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for f in self.data_dir.rglob("*"):
                if f.is_file() and f != target and "backups" not in f.parts:
                    z.write(f, f.relative_to(self.data_dir).as_posix())
        return target

    def restore(self, source: Path) -> None:
        """用备份覆盖当前档案；只接受本插件 data 目录结构。"""
        if not source.exists():
            raise FileNotFoundError(source)
        tmp = Path(tempfile.mkdtemp(prefix="aemeath_restore_"))
        with zipfile.ZipFile(source, "r") as z:
            names = z.namelist()
            if "identity.db" not in names:
                raise ValueError("备份中没有 identity.db")
            z.extractall(tmp)
        self.conn.close()
        shutil.copy2(tmp / "identity.db", self.db_path)
        for sub in ("official", "outfits", "generated"):
            src = tmp / "images" / sub
            if src.is_dir():
                dst = self.data_dir / "images" / sub
                dst.mkdir(parents=True, exist_ok=True)
                for f in src.glob("*"):
                    if f.is_file():
                        shutil.copy2(f, dst / f.name)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.import_official_assets()
        self.seed_identity()

