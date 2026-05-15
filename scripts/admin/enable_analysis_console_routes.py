from __future__ import annotations

from pathlib import Path

MAIN_PATH = Path("src/bookcraft/api/main.py")
IMPORT_LINE = "from bookcraft.api.admin_analysis import router as admin_analysis_router"
CHAT_IMPORT = "from bookcraft.api.chat import router as chat_router"
CHAT_INCLUDE = "    app.include_router(chat_router)"
ADMIN_INCLUDE = "    app.include_router(admin_analysis_router)"


def main() -> int:
    text = MAIN_PATH.read_text()

    text = "\n".join(line for line in text.splitlines() if line.strip() != IMPORT_LINE) + "\n"

    if CHAT_IMPORT not in text:
        raise SystemExit("Could not find chat router import.")

    text = text.replace(CHAT_IMPORT, f"{CHAT_IMPORT}\n{IMPORT_LINE}", 1)

    text = (
        "\n".join(line for line in text.splitlines() if line.strip() != ADMIN_INCLUDE.strip())
        + "\n"
    )

    if CHAT_INCLUDE not in text:
        raise SystemExit("Could not find chat router include.")

    text = text.replace(CHAT_INCLUDE, f"{CHAT_INCLUDE}\n{ADMIN_INCLUDE}", 1)

    MAIN_PATH.write_text(text)
    print(f"Admin analysis routes enabled in {MAIN_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
