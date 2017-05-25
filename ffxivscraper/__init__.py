from werkzeug.urls import url_quote_plus
from gevent.pool import Pool
import bs4
import re
import requests
import math

FFXIV_ELEMENTS = ['fire', 'ice', 'wind', 'earth', 'lightning', 'water']

FFXIV_PROPS = ['Defense', 'Parry', 'Magic Defense',
               'Attack Power', 'Skill Speed',
               'Slashing', 'Piercing', 'Blunt',
               'Attack Magic Potency', 'Healing Magic Potency', 'Spell Speed',
               'Morale',
               'Accuracy', 'Critical Hit Rate', 'Determination',
               'Craftsmanship', 'Control']

def debug_print(field, value):
    debug = 0
    if debug:
        if value:
            print field.upper() + " :: " + value
        else:
            print "NO VALUE FOR: " + field


def strip_tags(html, invalid_tags):
    soup = bs4.BeautifulSoup(html, 'html.parser')

    for tag in soup.findAll(True):
        if tag.name in invalid_tags:
            s = ""

            for c in tag.contents:
                if not isinstance(c, bs4.NavigableString):
                    c = strip_tags(unicode(c), invalid_tags)
                s += unicode(c)

            tag.replaceWith(s)

    return soup


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
        self.lodestone_domain = 'na.finalfantasyxiv.com'
        self.lodestone_url = 'http://%s/lodestone' % self.lodestone_domain

    def scrape_topics(self):
        url = self.lodestone_url + '/topics/'
        r = self.make_request(url)

        news = []
        soup = bs4.BeautifulSoup(r.content, "html.parser")
        for tag in soup.select('.topics_list li'):
            entry = {}
            title_tag = tag.select('.topics_list_inner a')[0]
            script = str(tag.select('script')[0])
            entry['timestamp'] = int(re.findall(r"1[0-9]{9},", script)[0].rstrip(','))
            entry['link'] = '//' + self.lodestone_domain + title_tag['href']
            entry['id'] = entry['link'].split('/')[-1]
            entry['title'] = title_tag.string.encode('utf-8').strip()
            body = tag.select('.area_inner_cont')[0]
            for a in body.findAll('a'):
                if a['href'].startswith('/'):
                    a['href'] = '//' + self.lodestone_domain + a['href']
            entry['body'] = body.encode('utf-8').strip()
            entry['lang'] = 'en'
            news.append(entry)
        return news

    def validate_character(self, server_name, character_name):

        # Search for character
        url = self.lodestone_url + '/character/?q=%s&worldname=%s' \
                                   % (url_quote_plus(character_name), server_name)

        r = self.make_request(url=url)

        if not r:
            return None

        soup = bs4.BeautifulSoup(r.content, "html.parser")

        for tag in soup.select('p.entry__name'):
            if tag.string.lower() == character_name.lower():
                return {
                    'lodestone_id': tag.parent.parent['href'].split('/')[3],
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

        soup = bs4.BeautifulSoup(r.content, "html.parser")

        page_name = soup.select('p.frame__chara__name')[0].text.strip()
        page_server = soup.select('p.frame__chara__world')[0].text.strip()

        if page_name != character_name or page_server != server_name:
            print "%s %s" % (page_name, page_server)
            print "Name mismatch"
            return False

        return lodestone_id if soup.select('div.character__selfintroduction')[0].text.strip() == verification_code else False

    def scrape_character(self, lodestone_id):
        character_url = self.lodestone_url + '/character/%s/' % lodestone_id

        r = self.make_request(url=character_url)

        if not r:
            raise DoesNotExist()

        soup = bs4.BeautifulSoup(r.content, "html.parser")
        
        character_link = '/lodestone/character/%s/' % lodestone_id
        if character_link not in soup.select('a.frame__chara__link')[0]['href']:
            raise DoesNotExist()
        debug_print('character link', character_link)

        # Name, Server, Title
        name = soup.select('p.frame__chara__name')[0].text.strip()
        debug_print('name', name)
        server = soup.select('p.frame__chara__world')[0].text.strip()
        debug_print('server', server)

        try:
            title = soup.select('p.frame__chara__title')[0].text.strip()
        except (AttributeError, IndexError):
            title = None
        debug_print('title', title)

        # Race, Tribe, Gender
        demographics = soup.find(text='Race/Clan/Gender').parent.parent
        demographics.select('p.character-block__name')[0].select('br')[0].replace_with(' / ')
        race, clan, gender = demographics.select('p.character-block__name')[0].text.split(' / ')
        debug_print('race', race)
        debug_print('clan', clan)
        gender = 'male' if gender.strip('\n\t')[-1] == u'\u2642' else 'female'
        debug_print('gender', gender)

        # Nameday & Guardian
        nameday_guardian_block = soup.find(text='Nameday').parent.parent
        nameday = nameday_guardian_block.select('p.character-block__birth')[0].text
        debug_print('nameday', nameday)
        guardian = nameday_guardian_block.select('p.character-block__name')[0].text
        debug_print('guardian', guardian)

        # City-state
        citystate = soup.find(text='City-state').parent.parent.select('p.character-block__name')[0].text
        debug_print('citystate', citystate)

        # Grand Company
        try:
            grand_company = soup.find_all(text='Grand Company')[1].parent.parent.select('p.character-block__name')[0].text.split('/')
            debug_print('grand company affiliation', grand_company[0])
            debug_print('grand company rank', grand_company[1])
        except (AttributeError, IndexError):
            grand_company = None
            debug_print('grand company affiliation', grand_company)
            debug_print('grand company rank', grand_company)

        # Free Company
        try:
            free_company = None
            free_company_name_block = soup.select('div.character__freecompany__name')[0].find('h4').find('a')
            free_company_crest_block = soup.select('div.character__freecompany__crest__image')[0]
            free_company = {
                'id': re.findall('(\d+)', free_company_name_block['href'])[0],
                'name': free_company_name_block.text,
                'crest': [x['src'] for x in free_company_crest_block.findChildren('img')]
            }
            debug_print('fc id', free_company['id'])
            debug_print('fc name', free_company['name'])
            # print free_company['crest']
        except (AttributeError, IndexError):
            free_company = None
            debug_print('missing fc id', free_company)
            debug_print('missing fc name', free_company)
            debug_print('missing fc crest', free_company)

        # Classes
        classes = {}
        for class_type in soup.select('ul.character__job'):
            for job in class_type.find_all('li'):
                job_name = job.select('div.character__job__name')[0].text
                job_level = job.select('div.character__job__level')[0].text
                job_exp_meter = job.select('div.character__job__exp')[0].text
                job_exp = 0
                job_exp_next = 0
                debug_print('job name', job_name)
                debug_print('job level', job_level)
                debug_print('job exp meter', job_exp_meter)
                if job_level == '-':
                    job_level = 0
                else:
                    job_level = int(job_level)
                    job_exp, job_exp_next = job_exp_meter.split(' / ')
                debug_print('job exp', job_exp)
                debug_print('job exp next', job_exp_next)

                classes[job_name] = dict(level=job_level, exp=job_exp, exp_next=job_exp_next)

        # Stats
        stats = {}

        param_blocks = soup.select('table.character__param__list')

        for param_block in param_blocks:
            stat_names = param_block.select('span')
            for stat_name_th in stat_names:
                stat_name = stat_name_th.text
                stat_val = stat_name_th.parent.next_sibling.text
                debug_print('stat_name: ', stat_name)
                debug_print('stat_val: ', stat_val)
                stats[stat_name] = stat_val

        for attribute in ('hp', 'mp', 'tp'):
            try:
                stats[attribute] = int(soup.select('p.character__param__text__' + attribute + '--en-us')[0].next_sibling.text)
            except IndexError:
                pass

        for element in FFXIV_ELEMENTS:
            tooltip = 'Decreases %s-aspected damage.' % element
            ele_value = int(soup.find(attrs={"data-tooltip": tooltip}).parent.text)
            stats[element] = ele_value

        mounts = []
        mount_box = soup.select('div.character__mounts')[0]
        for mount in mount_box.select('li'):
            mount_name = mount.select('div.character__item_icon')[0].get("data-tooltip")
            mounts.append(mount_name)

        minions = []
        minion_box = soup.select('div.character__minion')[0]
        for minion in minion_box.select('li'):
            minion_name = minion.select('div.character__item_icon')[0].get("data-tooltip")
            minions.append(minion_name)

        # Equipment
        parsed_equipment = []

        equip_boxes = soup.select('.ic_reflection_box')
        for equip_box in equip_boxes:
            slot_p = equip_box.select('p.db-tooltip__item__category')
            if len(slot_p) :
                parsed_equip = {}
                parsed_equip['slot'] = slot_p[0].text
                parsed_equip['name'] = equip_box.select('h2.db-tooltip__item__name')[0].text
                parsed_equip['img'] = equip_box.select('img.db-tooltip__item__icon__item_image')[0]['src']
                parsed_equipment.append(parsed_equip)
            else:
                parsed_equipment.append({})

        for i, tag in enumerate(soup.select('.item_name_right')):
            item_tags = tag.select('.item_name')

            if item_tags:

                if i == 0:
                    slot_name = tag.select('.category_name')[0].string.strip()
                    slot_name = slot_name.replace('Two-handed ', '')
                    slot_name = slot_name.replace('One-handed ', '')
                    slot_name = slot_name.replace("'s Arm", '')
                    slot_name = slot_name.replace("'s Primary Tool", '')
                    slot_name = slot_name.replace("'s Grimoire", '')

                # strip out all the extra \t and \n it likes to throw in
                parsed_equipment.append(' '.join(item_tags[0].text.split()))
            else:
                parsed_equipment.append(None)

        equipment = parsed_equipment[:len(parsed_equipment)//2]

        data = {
            'name': name,
            'server': server,
            'title': title,

            'race': race,
            'clan': clan,
            'gender': gender,

            'legacy': len(soup.select('.bt_legacy_history')) > 0,

            'avatar_url': soup.select('div.character__detail__image')[0].select('img')[0]['src'],
            'portrait_url': soup.select('div.frame__chara__face')[0].select('img')[0]['src'],

            'nameday': nameday,
            'guardian': guardian,

            'citystate': citystate,

            'grand_company': grand_company,
            'free_company': free_company,

            'classes': classes,
            'stats': stats,

            'achievements': self.scrape_achievements(lodestone_id),

            'minions': minions,
            'mounts': mounts,

            'current_equipment': equipment,
        }

        return data

    def scrape_achievements(self, lodestone_id, page=1):
        url = 'http://na.finalfantasyxiv.com/lodestone/character/%s/achievement/?filter=2&page=%s' \
              % (lodestone_id, page)

        r = self.make_request(url)

        if not r:
            return {}

        soup = bs4.BeautifulSoup(r.content, "html.parser")

        achievements = {}
        ach_block = soup.select('div.ldst__achievement')[0]
        for tag in ach_block.select('li.entry'):
            achievement = {
                'id': int(tag.select('a.entry__achievement')[0]['href'].split('/')[-2]),
                'icon': tag.select('div.entry__achievement__frame')[0].select('img')[0]['src'],
                'name': tag.select('p.entry__activity__txt')[0].text.split('"')[1],
                'date': int(re.findall(r'ldst_strftime\((\d+),', tag.find('script').text)[0])
            }
            achievements[achievement['id']] = achievement

        try:
            pages = int(math.ceil(float(int(soup.select('.parts__total')[0].text.split(' ')[0]) / 20)))
        except (ValueError, IndexError):
            pages = 0

        if pages > page:
            achievements.update(self.scrape_achievements(lodestone_id, page + 1))

        return achievements

    def scrape_free_company(self, lodestone_id):
        url = self.lodestone_url + '/freecompany/%s/' % lodestone_id
        html = self.make_request(url).content

        if 'The page you are searching for has either been removed,' in html:
            raise DoesNotExist()

        soup = bs4.BeautifulSoup(html, "html.parser")

        fc_tag = soup.select('p.freecompany__text__tag')[0].text
        fc_tag = fc_tag[1:-1] if fc_tag else ''

        crest = [x['src'] for x in soup.find('div', attrs={'class': 'entry__freecompany__crest__image'}).findChildren('img')]

        #formed = soup.find(string="Formed")
        #if formed:
        #    m = re.search(r'ldst_strftime\(([0-9]+),', formed)
        #    if m.group(1):
        #        formed = m.group(1)
        #else:
        #    formed = None
        formed = None

        slogan = soup.select('p.freecompany__text__message')[0].text
        slogan = ''.join(x.encode('utf-8').replace('<br/>', '\n') for x in slogan) if slogan else ""

        active = soup.find(text='Active').parent.next_sibling.next_sibling.text.strip()
        recruitment = soup.find(text='Recruitment').parent.next_sibling.next_sibling.text.strip()
        active_members = soup.find(text='Active Members').parent.next_sibling.next_sibling.text.strip()
        rank = soup.find(text='Rank').parent.next_sibling.next_sibling.text.strip()

        # skip this for now
        focus = []
        #for f in soup.select('.focus_icon li img'):
        #    on = not (f.parent.get('class') and 'icon_off' in f.parent.get('class'))
        #    focus.append(dict(on=on,
        #                      name=f.get('title'),
        #                      icon=f.get('src')))

        seeking = []
        #for f in soup.select('.roles_icon li img'):
        #    on = not (f.parent.get('class') and 'icon_off' in f.parent.get('class'))
        #    seeking.append(dict(on=on,
        #                        name=f.get('title'),
        #                        icon=f.get('src')))

        #estate_block = soup.find(text='Estate Profile').parent.parent
        #if estate_block.select('td')[0].text.strip() != 'No Estate or Plot':
        #    estate = dict()
        #    estate['name'] = estate_block.select('.txt_yellow')[0].text
        #    estate['address'] = estate_block.select('p.mb10')[0].text

        #    greeting = estate_block.select('p.mb10')[1].contents
        #    estate['greeting'] = ''.join(x.encode('utf-8').strip().replace('<br/>', '\n') for x in greeting) if greeting else ""
        #else:
        estate = None

        url = self.lodestone_url + '/freecompany/%s/member/' % lodestone_id
        html = self.make_request(url).content

        if 'The page you are searching for has either been removed,' in html:
            raise DoesNotExist()

        soup = bs4.BeautifulSoup(html, "html.parser")

        try:
            name = soup.select('p.entry__freecompany__name')[0].text.strip()
            server = soup.select('p.entry__freecompany__gc')[1].text.strip()
            grand_company = soup.select('p.entry__freecompany__gc')[0].text.strip()
        except IndexError:
            raise DoesNotExist()

        roster = []

        def populate_roster(page=1, soup=None):
            if not soup:
                r = self.make_request(url + '?page=%s' % page)
                soup = bs4.BeautifulSoup(r.content, "html.parser")

            for tag in soup.select('li.entry'):
                if not tag.find('img'):
                    continue

                member = {
                    'name': tag.select('p.entry__name')[0].text,
                    'lodestone_id': tag.select('a.entry__bg')[0]['href'].split('/')[3],
                    'rank': {
                        #'id': int(re.findall('class/(\d+?)\.png', tag.find('img')['src'])[0]),
                        'id': 1,
                        'name': tag.select('ul.entry__freecompany__info')[0].select('span')[0].text.strip(),
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
            'server': server.lower(),
            'grand_company': grand_company,
            'roster': roster,
            'slogan': slogan,
            'tag': fc_tag,
            'formed': formed,
            'crest': crest,
            'active': active,
            'recruitment': recruitment,
            'active_members': active_members,
            'rank': rank,
            'focus': focus,
            'seeking': seeking,
            'estate': estate
        }
