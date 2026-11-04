"""Classify a channel once across its routes; never change its identity or URL."""
import re
import unicodedata
from collections import Counter, defaultdict

# Earlier matches resolve compound names, e.g. 少儿科教、体育新闻、游戏音乐.
TOPICS = [
    ('少儿动漫', r'少儿|少兒|儿童|兒童|动画|動畫|动漫|卡通|哈哈炫动|KIDS|CHILDREN|ANIMAT|CARTOON'),
    ('游戏电竞', r'电竞|電競|电子竞技|游戏|遊戲|英雄联盟|王者荣耀|QQ飞车|ESPORT'),
    ('体育赛事', r'体育|體育|篮球|足球|网球|排球|羽毛球|乒乓|高尔夫|台球|赛车|奥林匹克|健身|广场舞|SPORT|ESPN|NBA'),
    ('财经商业', r'财经|財經|经济|金融|证券|證券|股市|商业|BLOOMBERG|CNBC|BUSINESS|FINANCE'),
    ('农业乡村', r'农业|农林|农村|乡村|三农|农科|农民|垄上'),
    ('法治社会', r'法治|法制|社会与法|法律|警法'),
    ('军事国防', r'军事|军武|国防|兵器'),
    ('健康养生', r'健康|养生|医学|卫生'),
    ('科教学习', r'教育|教科|科教|科学|科技|中学生|英文精听|自习|公开课|EDUCATION|SCIENCE'),
    ('纪录人文', r'纪录|紀錄|纪实|紀實|地理|发现之旅|人文|DISCOVERY|DOCUMENTARY'),
    ('文化戏曲', r'戏曲|戏台|戏园|梨园|国学|文化精品|曲艺'),
    ('影视剧场', r'影视|影視|电影|電影|影院|剧场|劇場|电视剧|戏剧|戲劇|映画|悬疑|洋片|CINEMA|MOVIE|FILM|DRAMA|HBO'),
    ('音乐演出', r'音乐|音樂|演唱会|交响|歌剧|MTV|MUSIC'),
    ('新闻资讯', r'新闻|新聞|资讯|資訊|NEWS|^CNN'),
    ('综艺娱乐', r'综艺|綜藝|娱乐|娛樂|育乐|育樂|文娱'),
    ('文旅生活', r'生活|文旅|旅游|旅遊|时尚|美食|休闲|女性|家居'),
    ('节目导视', r'电视指南|导视'),
]
PATTERNS=[(group,re.compile(pattern,re.I)) for group,pattern in TOPICS]
REGION=re.compile(r'北京|天津|上海|重庆|河北|山西|内蒙古|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|广西|海南|四川|贵州|云南|西藏|陕西|甘肃|青海|宁夏|新疆')
HMT_NAMES=re.compile(r'^(?:香港|澳门|台湾|凤凰|翡翠台|明珠台|TVB|RTHK|HOYTV|VIUTV|纬来|东森|三立|靖天|无线(?:新闻|财经|星河|电视)|台视|中视|民视|华视|公视|澳广视|港台电视|八大(?:综合|戏剧)|天映经典)')
FOREIGN_NAMES=re.compile(r'^(?:BBC|CNN|CNBC|NBC NEWS|BLOOMBERG|NHK|KBS|MBC|ARIRANG|FRANCE\s*24|DEUTSCHE WELLE|DW NEWS|AL JAZEERA|EURONEWS|ESPN|HBO|DISCOVERY|NATIONAL GEOGRAPHIC)')
HMT_LABEL=re.compile(r'港澳台|港台|香港|澳门|台湾|HONG\s*KONG|MACA[OU]|TAIWAN')
FOREIGN_LABEL=re.compile(r'国外|海外|外国|日韩|欧美|欧洲|北美|东南亚|日本|韩国|美国|英国|法国|德国|俄罗斯|澳大利亚|加拿大|新加坡|泰国|马来西亚|意大利|西班牙|印度|FOREIGN|OVERSEAS|JAPAN|KOREA|UNITED STATES|UNITED KINGDOM')
GEO_CHARS=str.maketrans('臺灣澳門鳳凰緯來東無線視聞華廣國歐韓馬亞','台湾澳门凤凰纬来东无线视闻华广国欧韩马亚')
# Country codes are useful for unbranded channels; CN/HK/MO/TW are handled separately.
FOREIGN_CODES=set(('AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ '
                   'CA CC CD CF CG CH CI CK CL CM CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR '
                   'GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP '
                   'KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MP MQ MR MS MT '
                   'MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU '
                   'RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TZ UA '
                   'UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW UK USA GBR JPN KOR').split())
GROUPS=['央视','卫视','港澳台','国外','地方综合','新闻财经','影视文娱','体育电竞','少儿科教','纪实生活','其他']
# Keep detailed theme detection and existing cached XMLTV evidence, then publish a short menu.
THEME_GROUP={theme:group for group,themes in {
    '新闻财经':('新闻资讯','财经商业'),
    '影视文娱':('影视剧场','综艺娱乐','音乐演出','文化戏曲'),
    '体育电竞':('体育赛事','游戏电竞'),
    '少儿科教':('少儿动漫','科教学习'),
    '纪实生活':('纪录人文','文旅生活','健康养生','农业乡村','法治社会','军事国防'),
    '地方综合':('地方综合','综合频道'),
    '其他':('节目导视','网络直播','待归类'),
}.items() for theme in themes}
PAID_CCTV={'文化精品','风云音乐','第一剧场','风云剧场','怀旧剧场','女性时尚','高尔夫网球','风云足球','电视指南','兵器科技','老故事','老故事频道','中央新影老故事'}
def station_group(name):
    if name.startswith(('CCTV','CGTN','央视','央視','中央电视台','中央電視台')) or re.match(r'^中央[- ]?\d',name):
        return '央视','央视或 CGTN 频道标识优先'
    if '卫视' in name or '衛視' in name:return '卫视','卫视频道标识优先'
    if name in PAID_CCTV:return '央视','央视专业频道名称'
    return None

def folded(text):
    return unicodedata.normalize('NFKC', text or '').upper().strip()

def region_name(name):
    text=folded(name).translate(GEO_CHARS)
    if HMT_NAMES.match(text):return '港澳台'
    if FOREIGN_NAMES.match(text) or FOREIGN_LABEL.match(text):return '国外'
    return None

def region_label(label,country=False):
    text=folded(label).translate(GEO_CHARS)
    compact=re.sub(r'[\W_]+','',text)
    if HMT_LABEL.search(compact) or compact in ('HK','HKG','TW','TWN','MO','MAC','CNHK','CNTW','CNMO'):return '港澳台'
    if compact in ('CN','CHN','CHINA','中国','中国大陆','大陆','内地'):return '内地'
    if FOREIGN_LABEL.search(text) or compact in (FOREIGN_CODES if country else {'US','USA','UK','GB','JP','KR','FR','DE','AU','CA','SG'}):return '国外'
    flag=re.search(r'[🇦-🇿]{2}',text)
    if flag:
        code=''.join(chr(ord(c)-ord('🇦')+ord('A')) for c in flag[0])
        return region_label(code,True)
    return None

def region_vote(values, country=False):
    votes=Counter()
    for labels in values.values():
        locations=set()
        for label in labels:
            parts=re.split(r'[,;/|，；]',label) if country else [label]
            locations.update(region_label(part,country) for part in parts)
        locations.discard(None)
        if len(locations)==1:votes.update(locations)
    best=votes.most_common(2)
    return best[0][0] if best and (len(best)==1 or best[0][1]>best[1][1]) else None

def named_topic(text):
    for group,pattern in PATTERNS:
        m=pattern.search(text)
        if m:return group,m.group()
    return None

def identity(name):
    topic=named_topic(name)
    if topic:return topic[0],f'频道名称含“{topic[1]}”'
    if name.endswith(('综合','綜合','公共','都市')):return '地方综合','地方综合或公共频道名称'
    return None

def group_hint(text):
    text=re.sub(r'[\W_]+','',folded(text))
    if text in ('央视','央视频道','央视付费','央视付费频道'):return '央视',1
    if '卫视' in text or '衛視' in text:return '卫视',1
    topic=named_topic(text)
    if topic:return topic[0],2
    if REGION.search(text) or '地方' in text or '本地' in text:return '地方综合',1
    if text in ('综合','综合频道'):return '综合频道',1
    if text in ('B站','网络直播','哔哩哔哩'):return '网络直播',1
    return None

def classify(name, hints, profiles, epg_keys, countries=None):
    name=folded(name)
    station=station_group(name)
    if station and station[0]=='央视':return station
    if re.match(r'^CETV[- ]?\d',name):return '少儿科教','中国教育电视台频道标识'
    geo=region_name(name)
    if geo:return geo,'频道名称或品牌标明所属地区'
    domestic=REGION.match(name) or re.match(r'^(东方|东南|深圳|大湾区|海峡|厦门|三沙|兵团|康巴|延边|安多)卫视',name)
    if station and domestic:return station
    for key in epg_keys:
        mapped=station_group(folded(key))
        if not station and mapped and mapped[0]=='央视':return '央视',f'频道映射标识 {key}'
        geo=region_name(key)
        if geo and not domestic:return geo,f'频道映射标识 {key}'
        if not station and mapped:station=mapped
    if not domestic:
        geo=region_vote(countries or {},country=True)
        reason='订阅中的频道国家／地区字段'
        if not geo:
            geo=region_vote(hints)
            reason='订阅分组的地区线索'
        if geo in ('港澳台','国外'):return geo,reason
    if station:return station
    known=identity(name)
    if known:return THEME_GROUP[known[0]],known[1]
    for key in epg_keys:
        profile=profiles.get(folded(key))
        if profile:
            group,hits,total=profile
            return THEME_GROUP.get(group,group),f'节目单中 {hits}/{total} 个节目指向同一主题'
    # One vote per subscription and theme, regardless of mirror URL count.
    votes={1:Counter(),2:Counter()}
    for labels in hints.values():
        evidence=set()
        for label in labels:
            hint=group_hint(label)
            if hint:
                group,weight=hint
                evidence.add((THEME_GROUP.get(group,group),weight))
        for group,weight in evidence:votes[weight][group]+=1
    strongest=votes[2] or votes[1]
    if strongest:
        first,score=strongest.most_common(1)[0]
        if sum(v==score for v in strongest.values())==1:return first,'订阅分组的主题线索'
    if REGION.match(name) or name.endswith(('电视台','一套','二套','民生')):return '地方综合','地域或地方台标识'
    return '其他','名称和节目单缺少明确主题，或不同订阅线索冲突'

def programme_profiles(root):
    """Only treat a sustained theme as evidence; count unknown programmes in the denominator."""
    names=defaultdict(list)
    for node in root.findall('channel'):
        key=node.get('id')
        if key:names[key].extend(n.text for n in node.findall('display-name') if n.text)
    schedules=defaultdict(dict)
    for p in root.findall('programme'):
        start,key=p.get('start'),p.get('channel')
        title=folded(p.findtext('title'))
        if key and start and title:
            categories=' '.join(folded(c.text) for c in p.findall('category'))
            topic=named_topic(categories) or named_topic(title)
            schedules[key][start]=(title,topic[0] if topic else None)
    profiles={}
    for key,schedule in schedules.items():
        votes=Counter(group for title,group in schedule.values() if group)
        if not votes:continue
        group,hits=votes.most_common(1)[0]
        if hits<5 or hits/len(schedule)<0.7:continue
        if len({t for t,g in schedule.values() if g==group})<3:continue
        for alias in [key,*names[key]]:profiles.setdefault(folded(alias),(group,hits,len(schedule)))
    return profiles
