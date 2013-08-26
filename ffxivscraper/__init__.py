from werkzeug.urls import url_quote_plus
from multiprocessing import Pool
import bs4
import re
import requests

FFXIV_ELEMENTS = ['fire', 'ice', 'wind', 'earth', 'thunder', 'water']

FFXIV_PROPS = ['Defense', 'Parry', 'Magic Defense', 'Attack Power', 'Skill Speed',
               'Slashing', 'Piercing', 'Blunt', 'Attack Magic Potency', 'Healing Magic Potency',
               'Spell Speed', 'Morale']


class DoesNotExist(Exception):
    pass


class Scraper(object):
    def __init__(self):
        self.s = requests.Session()

    def update_headers(self, headers):
        self.s.headers.update(headers)

    def make_request(self, url=None):
        return self.s.get(url)


class FFXIvScraper(Scraper):
    def __init__(self):
        super(FFXIvScraper, self).__init__()
        headers = {
            'Accept-Language': 'en-us,en;q=0.5',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_8_4) Chrome/27.0.1453.116 Safari/537.36',
            }
        self.update_headers(headers)
        self.lodestone_url = 'http://na.finalfantasyxiv.com/lodestone'

    def validate_character(self, server_name, character_name):

        # Search for character
        url = self.lodestone_url + '/character/?q=%s&worldname=%s' \
              % (url_quote_plus(character_name), server_name)

        r = self.make_request(url=url)

        if not r:
            return None

        soup = bs4.BeautifulSoup(r.content)

        for tag in soup.select('.player_name_area .player_name_gold a'):
            if tag.string.lower() == character_name.lower():
                return {
                    'lodestone_id': re.findall(r'(\d+)', tag['href'])[0],
                    'name': str(tag.string),
                    }

        return None

    def verify_character(self, server_name, character_name, verification_code, lodestone_id=None):
        if not lodestone_id:
            char = self.validate_character(server_name, character_name)
            if not char:
                raise DoesNotExist()
            lodestone_id = char['lodestone_id']

        url = self.lodestone_url + '/character/%s/' % lodestone_id

        r = self.make_request(url=url)

        if not r:
            return False

        soup = bs4.BeautifulSoup(r.content)
        print soup.select('.player_name_brown')[0].text

        if '%s (%s)' % (character_name, server_name) != soup.select('.player_name_brown')[0].text.replace("\n", ""):
            print "Name mismatch"
            return False

        return soup.select('.txt_selfintroduction')[0].text.strip() == verification_code

    def scrape_character(self, lodestone_id):
        character_url = self.lodestone_url + '/character/%s/' % lodestone_id

        r = self.make_request(url=character_url)

        if not r:
            raise DoesNotExist()

        soup = bs4.BeautifulSoup(r.content)

        if lodestone_id not in soup.select('.tab_com_chara_header_profile.tab_left a')[0]['href']:
            raise DoesNotExist()

        # Name & Server
        name, server = soup.select('.player_name_brown')[0].text.rsplit(' ', 1)

        # Race, Tribe, Gender
        race, clan_gender = soup.select('.chara_profile_title')[0].text.split(' / ')
        clan_gender = clan_gender.split()
        clan = ' '.join(clan_gender[0:-1])
        gender = 'male' if clan_gender[-1] == u'\u2642' else 'female'

        # Nameday & Guardian
        nameday_text = soup.find(text='Nameday ').parent.parent.select('td .txt_yellow')[-1].text
        nameday = re.findall('(\d+)', nameday_text)
        nameday = {
            'sun': int(nameday[0]),
            'moon': (int(nameday[1]) * 2) - (0 if 'Umbral' in nameday_text else 1),
            }
        guardian = soup.find(text='Guardian ').parent.parent.select('td .txt_yellow')[-1].text

        # City-state
        citystate = soup.find(text=re.compile('City-state')).parent.select('.txt_yellow')[0].text

        # Grand Company
        try:
            grand_company = soup.find(text=re.compile('Grand Company')).parent.select('.txt_yellow')[0].text.split('/')
            #grand_company = {
            #    'id': FFXIV_GRAND_COMPANIES[grand_company[0]],
            #    'rank': FFXIV_GRAND_COMPANY_RANKS.index(re.sub('(Flame|Storm|Serpent)\s', '', grand_company[1])),
            #}
        except (AttributeError, IndexError):
            grand_company = None

        # Free Company
        try:
            free_company = soup.find_all(text=re.compile('Free Company'))[-1].parent.select('a.txt_yellow')[0]
            free_company = {
                'id': re.findall('(\d+)', free_company['href'])[0],
                'name': free_company.text,
                }
        except (AttributeError, IndexError):
            free_company = None

        # Classes
        classes = {}
        for tag in soup.select('.class_list .ic_class_wh24_box'):
            class_ = tag.text

            if not class_:
                continue

            level = tag.next_sibling.next_sibling.text

            if level == '-':
                level = 0
                exp = 0
            else:
                level = int(level)
                exp = int(tag.next_sibling.next_sibling.next_sibling.next_sibling.text.split(' / ')[0])

            classes[class_] = dict(level=level, exp=exp)

        # Stats
        stats = {}
        for attribute in ('hp', 'mp', 'cp', 'tp', 'str', 'dex', 'vit', 'int', 'mnd', 'pie'):
            try:
                stats[attribute] = int(soup.select('.' + attribute)[0].text)
            except IndexError:
                pass
        for element in FFXIV_ELEMENTS:
            stats[element] = int(soup.select('.%s .val' % element)[0].text)
        for prop in FFXIV_PROPS:
            try:
                stats[prop] = int(soup.find(text=prop).parent.parent.select('.right')[0].text)
            except AttributeError:
                pass

        # Equipment
        current_class = None
        equipment = []

        for i, tag in enumerate(soup.select('.ic_reflection_box')):
            item_tags = tag.select('.item_name')

            if item_tags:
                item_tag = item_tags[0]
                item_name = item_tag.text
                slot_name = item_tag.next_sibling.string.strip()

                if i == 0:
                    slot_name = slot_name.replace('Two--Handed ', '')
                    slot_name = slot_name.replace("'s Arm", '')
                    slot_name = slot_name.replace("'s Primary Tool", '')
                    #current_class = FFXIV_CLASSES[slot_name]

                equipment.append(item_name)
            else:
                equipment.append(None)

        data = {
            'name': name,
            'server': server[1:-1],

            'race': race,
            'clan': clan,
            'gender': gender,

            'legacy': len(soup.select('.bt_legacy_history')) > 0,

            'avatar_url': soup.select('.thumb_cont_black_40.mr10.brd_black img')[0]['src'],
            'portrait_url': soup.select('.bg_chara_264 img')[0]['src'],

            'nameday': nameday,
            'guardian': guardian,

            'citystate': citystate,

            'grand_company': grand_company,
            'free_company': free_company,

            'classes': classes,
            'stats': stats,

            'achievements': self.scrape_achievements(lodestone_id),

            'current_class': current_class,
            'current_equipment': equipment,
            }

        return data

    def scrape_achievements(self, lodestone_id):
        url = self.lodestone_url + '/character/%s/achievement/kind/13/?filter=2' % lodestone_id

        r = self.make_request(url)

        if not r:
            return {}

        soup = bs4.BeautifulSoup(r.content)

        achievements = {}
        for tag in soup.select('.achievement_cnts li'):
            achievement = {
                'id': int(tag.select('.bt_more')[0]['href'].split('/')[-2]),
                'icon': tag.select('.ic_achievement img')[0]['src'],
                'name': tag.select('.achievement_name')[0].text,
                'points': int(tag.select('.achievement_point')[0].text),
                'date': int(re.findall(r'ldst_strftime\((\d+),', tag.find('script').text)[0])
            }
            achievements[achievement.id] = achievement
        return achievements

    def scrape_free_company(self, lodestone_id):
        url = self.lodestone_url + '/freecompany/%s/member' % lodestone_id

        html = self.make_request(url).content

        if 'The page you are searching for has either been removed,' in html:
            raise DoesNotExist()

        soup = bs4.BeautifulSoup(html)

        try:
            name = soup.select('.ic_freecompany_box span')[0].text
            server = soup.select('.ic_freecompany_box span')[1].text[1:-1]
        except IndexError:
            raise DoesNotExist()

        roster = []

        def populate_roster(page=1, soup=None):
            if not soup:
                r = self.make_request(url + '?page=%s' % page)
                soup = bs4.BeautifulSoup(r.content)

            for tag in soup.select('.player_name_area'):
                if not tag.find('img'):
                    continue

                name_anchor = tag.select('.player_name_gold')[0].find('a')

                member = {
                    'name': name_anchor.text,
                    'lodestone_id': re.findall('(\d+)', name_anchor['href'])[0],
                    'rank': {
                        'id': int(re.findall('class/(\d+?)\.png', tag.find('img')['src'])[0]),
                        'name': tag.select('.fc_member_status')[0].text.strip(),
                        },
                    }

                if member['rank']['id'] == 0:
                    member['leader'] = True

                roster.append(member)

        populate_roster(soup=soup)

        try:
            pages = int(soup.find(attrs={'rel': 'last'})['href'].rsplit('=', 1)[-1])
        except TypeError:
            pages = 1

        if pages > 1:
            pool = Pool(5)
            for page in xrange(2, pages + 1):
                pool.spawn(populate_roster, page)
            pool.join()

        return {
            'name': name,
            'server': 'ffxiv/' + server.lower(),
            'roster': roster,
            }