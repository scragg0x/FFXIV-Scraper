"""FFXIV Lodestone scraper.

Public API: :class:`FFXIvScraper` plus :class:`DoesNotExist`. Behavior is
intended to match the Python 2 release; only Py3 syntax, the Werkzeug 2.1
removal of ``url_quote_plus``, and a handful of bytes/str boundary issues
have changed.
"""

from __future__ import annotations

import contextlib
import math
import re
import urllib.parse
from typing import Any

import bs4
import requests
from gevent.pool import Pool

FFXIV_ELEMENTS: list[str] = ["fire", "ice", "wind", "earth", "lightning", "water"]

FFXIV_PROPS: list[str] = [
    "Defense",
    "Parry",
    "Magic Defense",
    "Attack Power",
    "Skill Speed",
    "Slashing",
    "Piercing",
    "Blunt",
    "Attack Magic Potency",
    "Healing Magic Potency",
    "Spell Speed",
    "Morale",
    "Accuracy",
    "Critical Hit Rate",
    "Determination",
    "Craftsmanship",
    "Control",
]

_DEBUG = False


def debug_print(field: str, value: Any) -> None:
    if not _DEBUG:
        return
    if value:
        print(f"{field.upper()} :: {value}")
    else:
        print(f"NO VALUE FOR: {field}")


def strip_tags(html: str | bytes, invalid_tags: list[str]) -> bs4.BeautifulSoup:
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(True):
        if tag.name in invalid_tags:
            s = ""
            for c in tag.contents:
                if not isinstance(c, bs4.NavigableString):
                    c = strip_tags(str(c), invalid_tags)
                s += str(c)
            tag.replace_with(s)
    return soup


class DoesNotExist(Exception):
    """Raised when a requested character or free company is not on Lodestone."""


class Scraper:
    def __init__(self) -> None:
        self.s = requests.Session()

    def update_headers(self, headers: dict[str, str]) -> None:
        self.s.headers.update(headers)

    def make_request(self, url: str | None = None) -> requests.Response | None:
        if url is None:
            return None
        return self.s.get(url, timeout=15)


class FFXIvScraper(Scraper):
    def __init__(self) -> None:
        super().__init__()
        headers = {
            "Accept-Language": "en-us,en;q=0.5",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_8_4) Chrome/27.0.1453.116 Safari/537.36"
            ),
        }
        self.update_headers(headers)
        self.lodestone_domain = "na.finalfantasyxiv.com"
        self.lodestone_url = f"http://{self.lodestone_domain}/lodestone"

    def scrape_topics(self) -> list[dict[str, Any]]:
        url = self.lodestone_url + "/topics/"
        r = self.make_request(url)
        if r is None:
            return []

        news: list[dict[str, Any]] = []
        soup = bs4.BeautifulSoup(r.content, "html.parser")
        for tag in soup.select(".topics_list li"):
            entry: dict[str, Any] = {}
            title_tag = tag.select(".topics_list_inner a")[0]
            script = str(tag.select("script")[0])
            entry["timestamp"] = int(re.findall(r"1[0-9]{9},", script)[0].rstrip(","))
            entry["link"] = "//" + self.lodestone_domain + title_tag["href"]
            entry["id"] = entry["link"].split("/")[-1]
            entry["title"] = title_tag.string.strip()
            body = tag.select(".area_inner_cont")[0]
            for a in body.find_all("a"):
                if a["href"].startswith("/"):
                    a["href"] = "//" + self.lodestone_domain + a["href"]
            entry["body"] = str(body).strip()
            entry["lang"] = "en"
            news.append(entry)
        return news

    def validate_character(self, server_name: str, character_name: str) -> dict[str, str] | None:
        url = (
            f"{self.lodestone_url}/character/"
            f"?q={urllib.parse.quote_plus(character_name)}&worldname={server_name}"
        )
        r = self.make_request(url=url)
        if not r:
            return None

        soup = bs4.BeautifulSoup(r.content, "html.parser")
        for tag in soup.select(".player_name_area .player_name_gold a"):
            if tag.string and tag.string.lower() == character_name.lower():
                return {
                    "lodestone_id": re.findall(r"(\d+)", tag["href"])[0],
                    "name": str(tag.string),
                }
        return None

    def verify_character(
        self,
        server_name: str,
        character_name: str,
        verification_code: str,
        lodestone_id: str | None = None,
    ) -> str | bool:
        if not lodestone_id:
            char = self.validate_character(server_name, character_name)
            if not char:
                raise DoesNotExist
            lodestone_id = char["lodestone_id"]

        url = f"{self.lodestone_url}/character/{lodestone_id}/"
        r = self.make_request(url=url)
        if not r:
            return False

        soup = bs4.BeautifulSoup(r.content, "html.parser")

        page_name = soup.select(".player_name_txt h2 a")[0].text.strip()
        page_server = soup.select(".player_name_txt h2 span")[0].text.strip()[1:-1]

        if page_name != character_name or page_server != server_name:
            debug_print("name mismatch", f"{page_name} {page_server}")
            return False

        intro = soup.select(".txt_selfintroduction")[0].text.strip()
        return lodestone_id if intro == verification_code else False

    def scrape_character(self, lodestone_id: str) -> dict[str, Any]:
        character_url = f"{self.lodestone_url}/character/{lodestone_id}/"
        r = self.make_request(url=character_url)
        if not r:
            raise DoesNotExist

        soup = bs4.BeautifulSoup(r.content, "html.parser")

        character_link = f"/lodestone/character/{lodestone_id}/"
        if character_link not in soup.select("a.frame__chara__link")[0]["href"]:
            raise DoesNotExist
        debug_print("character link", character_link)

        # Name, Server, Title
        name = soup.select("p.frame__chara__name")[0].text.strip()
        server = soup.select("p.frame__chara__world")[0].text.strip()

        try:
            title = soup.select("p.frame__chara__title")[0].text.strip()
        except (AttributeError, IndexError):
            title = None

        # Race, Tribe, Gender
        demographics = soup.find(string="Race/Clan/Gender").parent.parent
        demographics.select("p.character-block__name")[0].select("br")[0].replace_with(" / ")
        race, clan, gender = demographics.select("p.character-block__name")[0].text.split(" / ")
        gender = "male" if gender.strip("\n\t")[-1] == "♂" else "female"

        # Nameday & Guardian
        nameday_guardian_block = soup.find(string="Nameday").parent.parent
        nameday = nameday_guardian_block.select("p.character-block__birth")[0].text
        guardian = nameday_guardian_block.select("p.character-block__name")[0].text

        # City-state
        citystate = (
            soup.find(string="City-state").parent.parent.select("p.character-block__name")[0].text
        )

        # Grand Company
        try:
            grand_company = (
                soup.find_all(string="Grand Company")[1]
                .parent.parent.select("p.character-block__name")[0]
                .text.split("/")
            )
        except (AttributeError, IndexError):
            grand_company = None

        # Free Company
        free_company: dict[str, Any] | None = None
        try:
            free_company_name_block = (
                soup.select("div.character__freecompany__name")[0].find("h4").find("a")
            )
            free_company_crest_block = soup.select("div.character__freecompany__crest__image")[0]
            free_company = {
                "id": re.findall(r"(\d+)", free_company_name_block["href"])[0],
                "name": free_company_name_block.text,
                "crest": [x["src"] for x in free_company_crest_block.find_all("img")],
            }
        except (AttributeError, IndexError):
            free_company = None

        # Classes
        classes: dict[str, dict[str, Any]] = {}
        for class_type in soup.select("ul.character__job"):
            for job in class_type.find_all("li"):
                job_name = job.select("div.character__job__name")[0].text
                job_level_text = job.select("div.character__job__level")[0].text
                job_exp_meter = job.select("div.character__job__exp")[0].text
                job_exp: int | str = 0
                job_exp_next: int | str = 0
                if job_level_text == "-":
                    job_level: int = 0
                else:
                    job_level = int(job_level_text)
                    job_exp, job_exp_next = job_exp_meter.split(" / ")
                classes[job_name] = {
                    "level": job_level,
                    "exp": job_exp,
                    "exp_next": job_exp_next,
                }

        # Stats
        stats: dict[str, Any] = {}
        param_blocks = soup.select("table.character__param__list")
        for param_block in param_blocks:
            for stat_name_th in param_block.select("span"):
                stat_name = stat_name_th.text
                stat_val = stat_name_th.parent.next_sibling.text
                stats[stat_name] = stat_val

        for attribute in ("hp", "mp", "tp"):
            with contextlib.suppress(IndexError):
                stats[attribute] = int(
                    soup.select(f"p.character__param__text__{attribute}--en-us")[
                        0
                    ].next_sibling.text
                )

        for element in FFXIV_ELEMENTS:
            tooltip = f"Decreases {element}-aspected damage."
            ele_value = int(soup.find(attrs={"data-tooltip": tooltip}).parent.text)
            stats[element] = ele_value

        mounts: list[str] = []
        mount_box = soup.select("div.character__mounts")[0]
        for mount in mount_box.select("li"):
            mount_name = mount.select("div.character__item_icon")[0].get("data-tooltip")
            mounts.append(mount_name)

        minions: list[str] = []
        minion_box = soup.select("div.character__minion")[0]
        for minion in minion_box.select("li"):
            minion_name = minion.select("div.character__item_icon")[0].get("data-tooltip")
            minions.append(minion_name)

        # Equipment
        parsed_equipment: list[Any] = []
        for equip_box in soup.select(".ic_reflection_box"):
            slot_p = equip_box.select("p.db-tooltip__item__category")
            if slot_p:
                parsed_equipment.append(
                    {
                        "slot": slot_p[0].text,
                        "name": equip_box.select("h2.db-tooltip__item__name")[0].text,
                        "img": equip_box.select("img.db-tooltip__item__icon__item_image")[0]["src"],
                    }
                )
            else:
                parsed_equipment.append({})

        for i, tag in enumerate(soup.select(".item_name_right")):
            item_tags = tag.select(".item_name")
            if item_tags:
                if i == 0:
                    slot_name = tag.select(".category_name")[0].string.strip()
                    slot_name = slot_name.replace("Two-handed ", "")
                    slot_name = slot_name.replace("One-handed ", "")
                    slot_name = slot_name.replace("'s Arm", "")
                    slot_name = slot_name.replace("'s Primary Tool", "")
                    slot_name = slot_name.replace("'s Grimoire", "")
                # strip out all the extra \t and \n it likes to throw in
                parsed_equipment.append(" ".join(item_tags[0].text.split()))
            else:
                parsed_equipment.append(None)

        equipment = parsed_equipment[: len(parsed_equipment) // 2]

        return {
            "name": name,
            "server": server,
            "title": title,
            "race": race,
            "clan": clan,
            "gender": gender,
            "legacy": len(soup.select(".bt_legacy_history")) > 0,
            "avatar_url": soup.select("div.character__detail__image")[0].select("img")[0]["src"],
            "portrait_url": soup.select("div.frame__chara__face")[0].select("img")[0]["src"],
            "nameday": nameday,
            "guardian": guardian,
            "citystate": citystate,
            "grand_company": grand_company,
            "free_company": free_company,
            "classes": classes,
            "stats": stats,
            "achievements": self.scrape_achievements(lodestone_id),
            "minions": minions,
            "mounts": mounts,
            "current_equipment": equipment,
        }

    def scrape_achievements(self, lodestone_id: str, page: int = 1) -> dict[int, dict[str, Any]]:
        url = (
            f"http://na.finalfantasyxiv.com/lodestone/character/{lodestone_id}"
            f"/achievement/?filter=2&page={page}"
        )
        r = self.make_request(url)
        if not r:
            return {}

        soup = bs4.BeautifulSoup(r.content, "html.parser")
        achievements: dict[int, dict[str, Any]] = {}
        ach_block = soup.select("div.ldst__achievement")[0]
        for tag in ach_block.select("li.entry"):
            achievement = {
                "id": int(tag.select("a.entry__achievement")[0]["href"].split("/")[-2]),
                "icon": tag.select("div.entry__achievement__frame")[0].select("img")[0]["src"],
                "name": tag.select("p.entry__activity__txt")[0].text.split('"')[1],
                "date": int(re.findall(r"ldst_strftime\((\d+),", tag.find("script").text)[0]),
            }
            achievements[achievement["id"]] = achievement

        try:
            total = int(soup.select(".parts__total")[0].text.split(" ")[0])
            pages = math.ceil(total / 20)
        except (ValueError, IndexError):
            pages = 0

        if pages > page:
            achievements.update(self.scrape_achievements(lodestone_id, page + 1))

        return achievements

    def scrape_free_company(self, lodestone_id: str) -> dict[str, Any]:
        url = f"{self.lodestone_url}/freecompany/{lodestone_id}/"
        r = self.make_request(url)
        if r is None:
            raise DoesNotExist
        html = r.content

        if b"The page you are searching for has either been removed," in html:
            raise DoesNotExist

        soup = bs4.BeautifulSoup(html, "html.parser")

        fc_tag = strip_tags(str(soup.select(".vm")[0].contents[-1]), ["br"]).text
        fc_tag = fc_tag[1:-1] if fc_tag else ""
        formed_text = soup.select(".table_style2 td script")[0].text

        crest = [x["src"] for x in soup.find("div", attrs={"class": "ic_crest_64"}).find_all("img")]

        formed: str | None
        if formed_text:
            m = re.search(r"ldst_strftime\(([0-9]+),", formed_text)
            formed = m.group(1) if m and m.group(1) else None
        else:
            formed = None

        slogan_parts = soup.find(string="Company Slogan").parent.parent.select("td")[0].contents
        slogan = (
            "".join(str(x).strip().replace("<br/>", "\n") for x in slogan_parts)
            if slogan_parts
            else ""
        )

        active = soup.find(string="Active").parent.parent.select("td")[0].text.strip()
        recruitment = soup.find(string="Recruitment").parent.parent.select("td")[0].text.strip()
        active_members = (
            soup.find(string="Active Members").parent.parent.select("td")[0].text.strip()
        )
        rank = soup.find(string="Rank").parent.parent.select("td")[0].text.strip()

        focus: list[dict[str, Any]] = []
        for f in soup.select(".focus_icon li img"):
            on = not (f.parent.get("class") and "icon_off" in f.parent.get("class"))
            focus.append({"on": on, "name": f.get("title"), "icon": f.get("src")})

        seeking: list[dict[str, Any]] = []
        for f in soup.select(".roles_icon li img"):
            on = not (f.parent.get("class") and "icon_off" in f.parent.get("class"))
            seeking.append({"on": on, "name": f.get("title"), "icon": f.get("src")})

        estate_block = soup.find(string="Estate Profile").parent.parent
        estate: dict[str, Any] | None
        if estate_block.select("td")[0].text.strip() != "No Estate or Plot":
            greeting_parts = estate_block.select("p.mb10")[1].contents
            estate = {
                "name": estate_block.select(".txt_yellow")[0].text,
                "address": estate_block.select("p.mb10")[0].text,
                "greeting": (
                    "".join(str(x).strip().replace("<br/>", "\n") for x in greeting_parts)
                    if greeting_parts
                    else ""
                ),
            }
        else:
            estate = None

        member_url = f"{self.lodestone_url}/freecompany/{lodestone_id}/member"
        r = self.make_request(member_url)
        if r is None:
            raise DoesNotExist
        member_html = r.content
        if b"The page you are searching for has either been removed," in member_html:
            raise DoesNotExist

        member_soup = bs4.BeautifulSoup(member_html, "html.parser")

        try:
            name = member_soup.select(".ic_freecompany_box .pt4")[0].text
            server = member_soup.select(".ic_freecompany_box .crest_id span")[-1].text[1:-1]
            grand_company = member_soup.select(".crest_id")[0].contents[0].strip()
            friendship = member_soup.select(".friendship_color")[0].text[1:-1]
        except IndexError as e:
            raise DoesNotExist from e

        roster: list[dict[str, Any]] = []

        def populate_roster(page: int = 1, soup: bs4.BeautifulSoup | None = None) -> None:
            if not soup:
                resp = self.make_request(f"{member_url}?page={page}")
                if resp is None:
                    return
                soup = bs4.BeautifulSoup(resp.content, "html.parser")

            for tag in soup.select(".player_name_area"):
                if not tag.find("img"):
                    continue
                name_anchor = tag.select(".player_name_gold")[0].find("a")
                member = {
                    "name": name_anchor.text,
                    "lodestone_id": re.findall(r"(\d+)", name_anchor["href"])[0],
                    "rank": {
                        "id": int(re.findall(r"class/(\d+?)\.png", tag.find("img")["src"])[0]),
                        "name": tag.select(".fc_member_status")[0].text.strip(),
                    },
                }
                if member["rank"]["id"] == 0:
                    member["leader"] = True
                roster.append(member)

        populate_roster(soup=member_soup)

        try:
            pages = int(member_soup.find(attrs={"rel": "last"})["href"].rsplit("=", 1)[-1])
        except (TypeError, KeyError):
            pages = 1

        if pages > 1:
            pool = Pool(5)
            for page_num in range(2, pages + 1):
                pool.spawn(populate_roster, page_num)
            pool.join()

        return {
            "name": name,
            "server": server.lower(),
            "grand_company": grand_company,
            "friendship": friendship,
            "roster": roster,
            "slogan": slogan,
            "tag": fc_tag,
            "formed": formed,
            "crest": crest,
            "active": active,
            "recruitment": recruitment,
            "active_members": active_members,
            "rank": rank,
            "focus": focus,
            "seeking": seeking,
            "estate": estate,
        }
