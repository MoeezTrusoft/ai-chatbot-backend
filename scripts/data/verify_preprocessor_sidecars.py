from __future__ import annotations

from bookcraft.components.preprocessor.sidecars import load_sidecars
from bookcraft.infra.config import get_settings


def main() -> int:
    settings = get_settings()
    load_sidecars(settings.preprocessor_sidecar_dir)
    print("preprocessor sidecars verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
