"""lodestoner — FFXIV Lodestone scraper command-line interface.

Usage:
  lodestoner char (<lodestone_id> | <server_name> <first_name> <last_name>)
  lodestoner free_company <lodestone_id>
  lodestoner verify <server_name> <first_name> <last_name> <key>
  lodestoner validate <server_name> <first_name> <last_name>
  lodestoner topics

Options:
  -h --help     Show this screen.
"""

from __future__ import annotations

import json
from typing import Any

from docopt import docopt

from ffxivscraper import DoesNotExist, FFXIvScraper


def main() -> None:
    a = docopt(__doc__)
    s = FFXIvScraper()

    try:
        ret: Any = None

        if a.get("verify"):
            ret = s.verify_character(
                a["<server_name>"],
                f"{a['<first_name>']} {a['<last_name>']}",
                a["<key>"],
            )

        if a.get("validate"):
            ret = s.validate_character(
                a["<server_name>"],
                f"{a['<first_name>']} {a['<last_name>']}",
            )

        if a.get("char"):
            if a["<lodestone_id>"]:
                ret = s.scrape_character(a["<lodestone_id>"])
            else:
                data = s.validate_character(
                    a["<server_name>"],
                    f"{a['<first_name>']} {a['<last_name>']}",
                )
                if data:
                    ret = s.scrape_character(data["lodestone_id"])

        if a.get("free_company"):
            ret = s.scrape_free_company(a["<lodestone_id>"])

        if a.get("topics"):
            ret = s.scrape_topics()

        if ret:
            print(json.dumps(ret, indent=4))

    except DoesNotExist:
        print("Could not find character")


if __name__ == "__main__":
    main()
