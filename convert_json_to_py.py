"""
Converts media_library.json into a hardcoded Python list in media_library.py.

Usage:
    python convert_json_to_py.py
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(BASE_DIR, "media_library.json")
PY_PATH = os.path.join(BASE_DIR, "media_library.py")


def escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def main():
    if not os.path.exists(JSON_PATH):
        print("✗ media_library.json not found. Run scrape_yts.py first.")
        return

    with open(JSON_PATH, "r") as f:
        media = json.load(f)

    print(f"📦 Converting {len(media)} movies from JSON to Python...")

    lines = []
    lines.append('"""')
    lines.append("The Treasure Chest — yer hardcoded list of plunderable media.")
    lines.append(f"Auto-generated from media_library.json ({len(media)} entries).")
    lines.append('"""')
    lines.append("")
    lines.append("MEDIA_LIBRARY = [")

    for item in media:
        lines.append("    {")
        lines.append(f'        "id": "{escape(item.get("id", ""))}",')
        lines.append(f'        "title": "{escape(item.get("title", ""))}",')
        lines.append(f'        "year": {item.get("year", 0)},')
        lines.append(f'        "category": "{escape(item.get("category", "movie"))}",')

        genres = item.get("genres", [])
        genres_str = ", ".join(f'"{escape(g)}"' for g in genres)
        lines.append(f'        "genres": [{genres_str}],')

        lines.append(f'        "description": "{escape(item.get("description", ""))}",')

        torrents = item.get("torrents", {})
        lines.append('        "torrents": {')
        for tier, info in torrents.items():
            lines.append(f'            "{tier}": {{"url": "{escape(info["url"])}", "quality": "{escape(info["quality"])}"}},')
        lines.append("        },")

        lines.append(f'        "image_url": "{escape(item.get("image_url", ""))}",')
        lines.append("    },")

    lines.append("]")
    lines.append("")

    with open(PY_PATH, "w") as f:
        f.write("\n".join(lines))

    size_kb = os.path.getsize(PY_PATH) / 1024
    print(f"✅ Wrote {len(media)} movies to media_library.py ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
