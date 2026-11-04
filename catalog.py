import re
import json
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from bisect import bisect_left
from urllib.parse import urljoin, urlsplit
from collections import defaultdict
from grouping import GROUPS, classify

# Match streaming hostnames on DNS label boundaries only.
PLATFORM_DOMAINS={'miguvideo.com':'咪咕视频','ysp.cctv.cn':'央视频','yangshipin.cn':'央视频',
                  'bilivideo.com':'哔哩哔哩','bilivideo.cn':'哔哩哔哩',
                  'mgtv.com':'芒果TV','cztv.com':'中国蓝TV','douyucdn.cn':'斗鱼直播',
                  'huyalive.com':'虎牙直播','douyincdn.com':'抖音直播'}

def platform_evidence(hosts):
    matches={}
    for host in hosts:
        if not isinstance(host,str):continue
        host=host.lower().rstrip('.')
        if not re.fullmatch(r'[a-z0-9.-]{1,253}',host):continue
        for domain,label in PLATFORM_DOMAINS.items():
            if host==domain or host.endswith('.'+domain):matches[label]=host
    if len(matches)==1:return next(iter(matches.items()))
    if matches:return ('来源未识别','')

ALIASES = {'CQTV新闻':'重庆新闻','CGTN外语纪录':'CGTN纪录'}
PREFIX=re.compile(r'^(?:(?:\[|【|〔)(?:高清|超清|标清|蓝光|HD|FHD|UHD|线路\d+|[^\w\]\】〕]+)(?:\]|】|〕)|(?:央视|卫视|地方|高清|超清|标清)(?:频道)?\s*[|丨:·])\s*',re.I)
ATTR = re.compile(r'([\w-]+)="([^"\r\n]*)"')

def clean_prefix(s):
    text=unicodedata.normalize('NFKC',s).strip()
    # Strip confirmed quality tags before punctuation, preserving other bracketed editions.
    while text:
        tag=PREFIX.match(text)
        if tag: text=text[tag.end():];continue
        if text[0] in '[【〔(' or text[0].isalnum(): break
        text=text[1:].lstrip()
    return text or s

def normalize(s):
    s = re.sub(r'\s+', '', clean_prefix(s)).upper()
    s = re.sub(r'^CCTV[-_](?=\d)', 'CCTV', s)
    s = re.sub(r'^CCTV5PLUS', 'CCTV5+', s)
    numbered = {'1':'综合','2':'财经','3':'综艺','4':'中文国际','5':'体育','5+':'体育赛事','6':'电影','7':'国防军事','8':'电视剧','9':'纪录','10':'科教','11':'戏曲','12':'社会与法','13':'新闻','14':'少儿','15':'音乐','16':'奥林匹克','17':'农业农村'}
    if s.startswith('CCTV') and s[4:] in numbered: s += numbered[s[4:]]
    return ALIASES.get(s,s)

def logo_url(value,base=''):
    if not value or not value.strip():return ''
    try:
        url=urljoin(base,value.strip())
        parsed=urlsplit(url)
        return url if parsed.scheme in ('http','https') and parsed.hostname else ''
    except ValueError:
        return ''

def logo_keys(epg_id,*names):
    keys=['id:'+epg_id] if epg_id else []
    keys.extend('name:'+normalize(n) for n in names if n)
    return keys

def xmltv_logos(root,base):
    index={}
    for node in root.findall('channel'):
        keys=logo_keys(node.get('id'),*(n.text for n in node.findall('display-name')))
        for icon in node.findall('icon'):
            value=logo_url(icon.get('src'),base)
            if value:
                for key in keys:index.setdefault(key,value)
                break
    return index

def playlist_guides(text,base):
    header=next(iter(text.lstrip('\ufeff \t\r\n').splitlines()),'')
    if not re.match(r'^#EXTM3U(?:\s|$)',header,re.I):return []
    links=[]
    for attr in re.finditer(r'''(?:^|\s)(?:x-tvg-url|url-tvg|tvg-url)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+))''',header,re.I):
        value=next(v for v in attr.groups() if v is not None)
        for part in re.split(r'\s*,\s*|\s+',value.strip()):
            url=logo_url(part,base)
            if url and url not in links:links.append(url)
    return links

def parse_playlist(text, base):
    result = []
    group, meta, name, options = '', {}, '', []
    m3u = text.lstrip('\ufeff \r\n').startswith('#EXTM3U')
    defaults={}
    if m3u and re.search(r'^#EXT-X-(STREAM-INF|TARGETDURATION|MEDIA-SEQUENCE):', text, re.M):
        raise ValueError('这里需要频道订阅，收到的是单频道 HLS 清单')
    for s in text.lstrip('\ufeff').splitlines():
        s=s.strip()
        if not s: continue
        if s.startswith('#EXTM3U'):
            defaults={k:v for k,v in ATTR.findall(s) if k.startswith('catchup')}
            continue
        if s.startswith('#EXTINF:'):
            match=re.match(r'^#EXTINF:((?:"[^"]*"|[^",])*)\s*,(.*)$',s)
            if not match: raise ValueError('无效 EXTINF 条目')
            meta={**defaults,**dict(ATTR.findall(match[1]))}
            if 'tvg-logo' in meta:meta['tvg-logo']=logo_url(meta['tvg-logo'],base)
            name=match[2].strip() or meta.get('tvg-name','')
            options=[]
            continue
        if s.startswith(('#EXTVLCOPT:', '#KODIPROP:')):
            options.append(s)
            continue
        if s.startswith('#'): continue
        if m3u:
            urls=[s]
            if not name: continue
        else:
            if ',' not in s: continue
            name, value=s.split(',',1)
            if value.strip()=='#genre#':
                group=name.strip()
                continue
            urls=value.split('#')
            meta={'group-title':group}
        for url in urls:
            url=urljoin(base,url.strip())
            if name.strip() and urlsplit(url).scheme in ('http','https','rtsp','rtmp','udp','rtp'):
                result.append(dict(name=name.strip(),url=url,attrs=meta,options=list(options)))
        name,meta,options='',{},[]
    if not result: raise ValueError('清单为空或没有识别到频道')
    return result

def speed_display(kbps):
    if kbps is None: return '—'
    if kbps>=1024: return f'{kbps/1024:.2f} MB/s'
    return f'{kbps:.2f} KB/s'

def assess(test, settings):
    if test.get('pipeline'):
        if test.get('stable_qualified') and test.get('quality') in ('暂时失败','未能验证'):return '待复测'
        settings={**settings,'criteria':'both','min_speed':100,'min_height':720}
    if test.get('method') in ('quick','continuous'):
        if test.get('quality') in ('暂时失败','未能验证'):
            return '待复测' if test.get('grace') else test['quality']
    elif test.get('seconds')!=8 or test.get('speed') is None:
        return test.get('status','未测')
    criteria=settings.get('criteria','speed')
    if criteria in ('speed','both') and test['speed']<=settings.get('min_speed',100): return '速率未达标'
    if criteria in ('resolution','both'):
        size=test.get('resolution') or {}
        if not size.get('height'): return '分辨率未知'
        if size['height']<settings.get('min_height',720): return '分辨率未达标'
    return '保留'

def quality_order(line,criteria):
    test=line['test']
    status=line['assessment']
    tier=0 if status=='保留' else 1 if status=='未测' else 2
    speed=test.get('speed')
    height=(test.get('resolution') or {}).get('height')
    metrics=(-(speed if speed is not None else -1),-(height or 0))
    if criteria in ('resolution','both'):metrics=metrics[::-1]
    quality={'流畅':0,'可播但不稳':2}.get(test.get('quality'),1)
    history=test.get('recent',[])
    reliability=(sum(history)+1)/(len(history)+2)
    openings=test.get('direct_history',[])
    direct_rate=(sum(openings)+1)/(len(openings)+2)
    preferred_height=-(height or 0) if criteria in ('resolution','both') else 0
    recovered=test.get('opening') in ('重连后可播','复核后可播','复测后可播') or bool(test.get('intermittent'))
    return (tier,quality,preferred_height,-direct_rate,recovered,-reliability,test.get('stalls',0),test.get('startup',999),*metrics,line['priority'],line['url'])

def route_mode(members,url,modes):
    values={modes.get((name,url)) for name in members}
    return 'off' if 'off' in values else 'on' if 'on' in values else 'auto'

def build(sources, tests, overrides, route_modes=None, settings=None, profiles=None, hidden=None, epg_logos=None, restored_routes=(), hidden_groups=None, restored_channel_routes=(), hidden_channels=None, hidden_items=None):
    channels={}
    hints=defaultdict(lambda:defaultdict(set))
    countries=defaultdict(lambda:defaultdict(set))
    route_modes=route_modes or {}
    hidden=hidden or set()
    channel_inventory=defaultdict(dict)
    standalone={(r['name'],r['url']):r for r in hidden_items or [] if r['kind']=='route'}
    criteria=(settings or {}).get('criteria','speed')
    subscription_logos={}
    for src in sources:  # 数字小的订阅优先提供元数据。
        for item in src['items']:
            if '更新时间' in item['attrs'].get('group-title','') and re.fullmatch(r'[\d\s:/.-]+',item['name']):
                continue
            original=normalize(item['name'])
            rule=overrides.get(original,{})
            name=rule.get('target') or original
            url=item['url']
            owners={original,name}
            if any(('route',key,url) in hidden for key in owners):
                for key in owners:
                    row=standalone.get((key,url))
                    if row is not None:
                        row.setdefault('priority',src['priority'])
                        if original not in row.setdefault('members',[]):row['members'].append(original)
                        names=row.setdefault('sources',[])
                        if src['name'] not in names:names.append(src['name'])
                continue
            blocked=[key for key in owners if ('channel',key,'') in hidden and (key,url) not in restored_channel_routes]
            if blocked:
                if hidden_channels is not None:
                    for key in blocked:
                        row=channel_inventory[key].setdefault(url,dict(name=key,label=overrides.get(key,{}).get('label') or key,url=url,sources=[],priority=src['priority']))
                        if original not in row.setdefault('members',[]):row['members'].append(original)
                        if src['name'] not in row['sources']:row['sources'].append(src['name'])
                continue
            channel=channels.setdefault(name,{'name':name,'members':[], 'lines':{},'group':'','logo_candidates':[],'epg':'','guide_ids':[],'guide_names':[]})
            if original not in channel['members']: channel['members'].append(original)
            attrs=item['attrs']
            for field,attr in [('guide_ids','tvg-id'),('guide_names','tvg-name')]:
                if attrs.get(attr) and attrs[attr] not in channel[field]:channel[field].append(attrs[attr])
            if not channel['epg']:channel['epg']=attrs.get('tvg-id','')
            logo=logo_url(attrs.get('tvg-logo'),src.get('url',''))
            if logo:
                if logo not in channel['logo_candidates']: channel['logo_candidates'].append(logo)
                for k in logo_keys(attrs.get('tvg-id'),attrs.get('tvg-name'),original):
                    subscription_logos.setdefault(k,logo)
            source_id=src.get('id',src['name'])
            hints[name][source_id].add(attrs.get('group-title',''))
            if attrs.get('tvg-country'):countries[name][source_id].add(attrs['tvg-country'])
            flag=re.match(r'^\s*([🇦-🇿]{2})',item['name'])
            if flag:countries[name][source_id].add(flag[1])
            if url in channel['lines']:
                channel['lines'][url]['from'].append(src['name'])
                if original not in channel['lines'][url]['members']:
                    channel['lines'][url]['members'].append(original)
            else:
                channel['lines'][url]={**item,'members':[original],'from':[src['name']],'priority':src['priority'],'test':tests.get(url,{})}
    if hidden_channels is not None:
        hidden_channels.update({name:list(rows.values()) for name,rows in channel_inventory.items()})
    for c in channels.values():
        o=overrides.get(c['name'],{})
        keys=[o.get('epg'),c['epg'],c['name'],*c['members']]
        c['auto_group'],c['group_reason']=classify(c['name'],hints[c['name']],profiles or {},keys,countries[c['name']])
        c['group']=c['auto_group']
        c['auto_epg']=c['epg'] or c['name']
        c['bound_epg']=o.get('bound_epg',False)
        for field in ('group','epg','label'):
            if o.get(field): c[field]=o[field]
        c['enabled']=o.get('enabled',True)
        c['label']=c.get('label',c['name'])
        c['epg']=c['epg'] or c['name']
        keys=logo_keys(c['epg'],c['label'],c['name'],*c['members'])
        for line in c['lines'].values():
            keys.extend(logo_keys(line['attrs'].get('tvg-id'),line['attrs'].get('tvg-name')))
        logos=c['logo_candidates']
        for index in (subscription_logos,epg_logos or {}):
            for key in dict.fromkeys(keys):
                candidate=index.get(key)
                if candidate and candidate not in logos:logos.append(candidate)
        logo=o.get('logo') or ''
        manual=logo if re.fullmatch(r'/logos/[0-9a-f]{64}\.png',logo) else logo_url(logo)
        if manual:
            c['auto_logo_candidates']=logos
            c['logo_candidates']=[manual]
        c['logo']=manual or (logos[0] if logos else '')
        for r in c['lines'].values():
            entry=urlsplit(r['url']).hostname
            probe_result=r['test']
            hosts=probe_result.get('observed_hosts',[])
            if not hosts:
                # Older probes recorded the response host at the beginning of their HTTP error.
                hosts=[m[1] for note in probe_result.get('note',[]) if (m:=re.match(
                    r'^(?:从原始订阅入口重连 \d+ 次；此前响应：)?(?:末片|媒体节点|入口清单|子清单) ([a-z0-9.-]+) HTTP \d{3}\b',note,re.I))]
            evidence=platform_evidence(hosts)
            gateway=platform_evidence(r.get('origin_hosts',[]))
            if r.get('origin_checked',0)>probe_result.get('observed_at',0):
                evidence=gateway or evidence
            r['platform'],r['platform_host']=evidence or gateway or platform_evidence([entry]) or ('来源未识别','')
            r['mode']=route_mode(r['members'],r['url'],route_modes)
            r['assessment']=assess(r['test'],settings or {})
            r['speed_display']=speed_display(r['test'].get('speed'))
        c['lines']=sorted(c['lines'].values(), key=lambda r:quality_order(r,criteria))
    visible=[]
    for c in channels.values():
        group=c['group']
        if ('group',group,'') in hidden:
            shown=[]
            for r in c['lines']:
                if (group,c['name'],r['url']) in restored_routes:
                    shown.append(r)
                elif hidden_groups is not None:
                    hidden_groups.setdefault(group,[]).append(dict(name=c['name'],label=c['label'],url=r['url'],sources=r['from'],members=r['members'],priority=r['priority']))
            if not shown:continue
            c['lines']=shown
        visible.append(c)
    inventories=[*(hidden_groups or {}).values(),*(hidden_channels or {}).values()]
    if hidden_items is not None:inventories.append(hidden_items)
    for rows in inventories:
        for r in rows:
            if not r['url']:continue
            test=tests.get(r['url'],{})
            r['test']={k:test[k] for k in ('speed','resolution','at','intermittent','note','quality','method','recent','startup','stalls','stall_seconds','seconds','bitrate','segments','grace','fallback','direct_history','entry_retries','opening','httpStatus','play_seconds') if k in test}
            r['speed_display']=speed_display(test.get('speed'))
            r['assessment']=assess(test,settings or {})
            r['mode']=route_mode(r.get('members',[r['name']]),r['url'],route_modes)
            r['enabled']=route_enabled(r,settings or {})
            r['published']=False
            r['publication_reason']='hidden'
            r.setdefault('priority',1<<31)
        rows.sort(key=lambda r:(r.get('kind',''),r['name'],quality_order(r,criteria) if r['url'] else ()))
    result=sorted(visible, key=lambda c:(GROUPS.index(c['group']) if c['group'] in GROUPS else len(GROUPS),c['group']))
    publication_state(result,settings or {})
    return result

def route_enabled(route, settings):
    return route['mode']=='on' or (route['mode']=='auto' and (not settings.get('strict',True) or route['assessment'] in ('保留','待复测')))

def selected(channels, settings):
    for channel in channels:
        lines=[r for r in channel['lines'] if route_enabled(r,settings)]
        if lines: yield {**channel,'lines':lines[:1] if settings.get('mode','merge')=='best' else lines}

def publication_state(channels,settings):
    # Project the exact export selection once for every management surface.
    published={(c['name'],r['url']) for c in selected(channels,settings) for r in c['lines']}
    for c in channels:
        for r in c['lines']:
            r['enabled']=route_enabled(r,settings)
            r['published']=(c['name'],r['url']) in published
            r['publication_reason']='published' if r['published'] else 'route_disabled' if not r['enabled'] else 'channel_disabled' if not c['enabled'] else 'not_selected'
        c['enabled_routes']=sum(r['enabled'] for r in c['lines'])
        c['enabled']=bool(c['enabled_routes'])
        c['published_routes']=sum(r['published'] for r in c['lines'])
        c['published']=bool(c['published_routes'])
        c['publication_reason']='published' if c['published'] else 'channel_disabled' if not c['enabled'] else 'no_enabled_routes'

def clean(s):
    return str(s).replace('\r',' ').replace('\n',' ').replace('"','').strip()

def export(kind, channels, guide):
    if kind=='json': return json.dumps(channels,ensure_ascii=False)
    rows=[f'#EXTM3U x-tvg-url="{clean(guide)}"'] if kind=='m3u' else []
    previous=None
    for c in channels:
        name=clean(c['label'])
        if kind=='txt':
            if previous!=c['group']:
                rows.append(clean(c['group'])+',#genre#')
                previous=c['group']
            rows.append(name.replace(',','，')+','+'#'.join(r['url'].replace('#','%23') for r in c['lines']))
        else:
            for r in c['lines']:
                attrs={**r['attrs'],'group-title':c['group'],'tvg-id':c['epg'],'tvg-name':name,'tvg-logo':c['logo']}
                if r['test'].get('speed') is not None:
                    attrs['x-speed-kbytes-per-sec']=f"{r['test']['speed']:.2f}"
                    attrs['x-speed-display']=r['speed_display']
                size=r['test'].get('resolution')
                if size: attrs['x-resolution']=f"{size['width']}x{size['height']}"
                pairs=' '.join(f'{k}="{clean(v)}"' for k,v in attrs.items() if v)
                rows.extend([f'#EXTINF:-1 {pairs},{name}',*r['options'],r['url']])
    return '\n'.join(rows)+'\n'

def xmlroot(raw):
    if b'<!ENTITY' in raw.upper(): raise ValueError('不支持 XML 实体声明')
    root=ET.fromstring(raw)
    if root.tag!='tv': raise ValueError('EPG 必须是 XMLTV')
    return root

def xmltv_stamp(value):
    clean=(value or '').strip()
    if not re.fullmatch(r'\d{14}(?:\s*[+-]\d{4})?',clean):return None
    try:return int(datetime.strptime(clean[:14]+(clean[14:].strip() or '+0000'),'%Y%m%d%H%M%S%z').timestamp())
    except (ValueError,OSError):return None

def guide(channels, guides, eit_events=()):
    root=ET.Element('tv',{'generator-info-name':'IPTV Hub'})
    by_name=defaultdict(set)
    by_id=defaultdict(set)
    ids=set()
    eit_ids=defaultdict(set)
    for c in channels:
        ident=c['epg']
        for member in ([ident] if c.get('bound_epg') else c['members']):eit_ids[member].add(ident)
        if ident not in ids:
            node=ET.SubElement(root,'channel',id=ident)
            ET.SubElement(node,'display-name').text=c['label']
            if c['logo']: ET.SubElement(node,'icon',src=c['logo'])
            ids.add(ident)
        if c.get('bound_epg'):continue
        for alias in c.get('guide_ids',[]):by_id[alias].add(ident)
        for n in [c['label'],*c['members'],*c.get('guide_names',[])]:
            by_name[normalize(n)].add(ident)
    seen=set()
    for raw in guides:
        xml=xmlroot(raw)
        mapping={}
        for node in xml.findall('channel'):
            orig=node.get('id')
            if not orig: continue
            targets=set().union(*(by_name.get(normalize(n.text or ''),set()) for n in node.findall('display-name')))
            mapping[orig]={orig} if orig in ids else by_id.get(orig) or targets
        for p in xml.findall('programme'):
            orig=p.get('channel')
            for ident in mapping.get(orig, {orig} if orig in ids else by_id.get(orig,())):
                key=(ident,p.get('start'))
                if not key[1] or key in seen: continue
                seen.add(key)
                clone=ET.Element('programme',{**p.attrib,'channel':ident})
                clone.extend(p)
                root.append(clone)
    intervals=defaultdict(list)
    eit_events=[row for row in eit_events if row['name'] in eit_ids]
    eligible={ident for row in eit_events for ident in eit_ids[row['name']]}
    for entry in root.iterfind('programme') if eligible else ():
        ident=entry.get('channel')
        if ident in eligible:
            a,b=xmltv_stamp(entry.get('start')),xmltv_stamp(entry.get('stop'))
            if a is not None:intervals[ident].append((a,max(a+1,b or a+1)))
    for ident,spans in intervals.items():
        merged=[]
        for a,b in sorted(spans):
            if merged and a<merged[-1][1]:
                merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
            else:merged.append((a,b))
        intervals[ident]=merged
    for row in eit_events:
        for ident in eit_ids.get(row['name'],()):
            spans=intervals[ident]
            a,b=row['start'],row['stop']
            i=bisect_left(spans,(a,))
            if (i and spans[i-1][1]>a) or (i<len(spans) and spans[i][0]<b):continue
            spans.insert(i,(a,b))
            start,stop=(datetime.fromtimestamp(t,timezone.utc).strftime('%Y%m%d%H%M%S +0000') for t in (a,b))
            event=ET.SubElement(root,'programme',channel=ident,start=start,stop=stop)
            ET.SubElement(event,'title').text=row['title']
            if row['description']:ET.SubElement(event,'desc').text=row['description']
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)
