"""瀵煎叆瀹樻柟鍙傝€冨浘鍒拌瑙夎韩浠芥彃浠舵暟鎹洰褰曘€?
鐢ㄦ硶锛?  python tools/import_official.py --data <plugin_data_dir> --images <official_images_dir>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aemeath_visual_core.db import IdentityStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="鎻掍欢 data 鐩綍")
    parser.add_argument("--images", required=True, help="瀹樻柟鍥剧洰褰?)
    args = parser.parse_args()
    store = IdentityStore(Path(args.data), Path(args.images))
    print(f"assets={len(store.official_images())}")
    print(f"db={store.db_path}")
    print(f"identity_version={store.identity().get('version')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

