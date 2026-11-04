"""Decode DVB service information (EN 300 468) from bounded TS samples."""
import re
import unicodedata
from collections import defaultdict

from catalog import normalize


def u16(data, pos):
    return int.from_bytes(data[pos:pos+2], 'big')


def crc32(data):
    crc = 0xffffffff
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04c11db7 if crc & 0x80000000 else 0)) & 0xffffffff
    return crc


def sections(packets):
    pending, counters = {}, {}
    for pos in range(0, len(packets)-187, 188):
        p = packets[pos:pos+188]
        pid = u16(p, 1) & 0x1fff
        control = (p[3] >> 4) & 3
        if p[0] != 0x47 or pid not in (0, 17, 18): continue
        if p[1] & 0x80 or p[3] & 0xc0 or (control & 2 and p[4] and p[5] & 0x80):
            pending.pop(pid, None)
            counters.pop(pid, None)
        if not control & 1 or p[1] & 0x80 or p[3] & 0xc0: continue
        cc = p[3] & 15
        prev = counters.get(pid)
        if cc == prev: continue
        if prev is not None and cc != (prev+1) % 16: pending.pop(pid, None)
        counters[pid] = cc
        body = p[5+p[4]:] if control & 2 else p[4:]
        if not body: continue
        chunks = []
        if p[1] & 0x40:
            cut = 1 + body[0]
            if pid in pending: chunks.append(pending.pop(pid) + body[1:cut])
            chunks.append(body[cut:])
        elif pid in pending:
            chunks.append(pending.pop(pid) + body)
        for i, chunk in enumerate(chunks):
            while chunk and chunk[0] != 0xff:
                length = 3 + (u16(chunk, 1) & 0xfff) if len(chunk) >= 3 else 3
                if len(chunk) < length:
                    if i == len(chunks)-1: pending[pid] = chunk
                    break
                data, chunk = chunk[:length], chunk[length:]
                if length >= 12 and data[1] & 0x80 and data[5] & 1 and crc32(data) == 0:
                    yield pid, data[:-4]


def dvb_text(raw,language=b''):
    if not raw: return ''
    tag, data = raw[0], raw
    codec = {17:'utf-16-be', 18:'euc_kr', 19:'gb2312', 20:'big5', 21:'utf-8'}.get(tag)
    if codec: data = raw[1:]
    elif 1 <= tag <= 11:
        codec, data = 'iso8859_'+str(tag+4), raw[1:]
    elif tag == 16 and len(raw)>=3 and raw[1]==0:
        codec, data = 'iso8859_'+str(raw[2]), raw[3:]
    elif tag < 32: return ''
    elif language in (b'chi',b'zho') and any(b>=128 for b in raw):
        # Some Chinese headends omit the DVB character-table selector.
        for candidate in ('utf-8','gb2312'):
            try:raw.decode(candidate)
            except UnicodeDecodeError:continue
            codec=candidate
            break
    if codec:
        try: value = data.decode(codec, errors='replace')
        except LookupError: return ''
    else:
        # DVB default Latin alphabet (ISO 6937). Combining accents precede the letter.
        accents = dict(zip((193,194,195,196,197,198,199,200,202,203,205,206,207),'̧̨̀́̂̃̄̆̇̈̊̋̌'))
        value = ''
        accent = ''
        for byte in data:
            if byte in accents: accent = accents[byte]; continue
            value += chr(byte) + accent
            accent = ''
        value = unicodedata.normalize('NFC',value)
    return re.sub(r'[\x00-\x1f\x7f-\x9f\ufffe\uffff]', '', value).strip()


def descriptors(data):
    i = 0
    while i+2 <= len(data):
        tag, length = data[i:i+2]
        end = i+2+length
        if end>len(data): break
        yield tag, data[i+2:end]
        i=end


def bcd_time(raw):
    parts=[(b>>4)*10+(b&15) for b in raw]
    if len(parts)!=3 or any(b>>4>9 or b&15>9 for b in raw) or max(parts[1:])>=60:
        raise ValueError('invalid BCD')
    return parts[0]*3600+parts[1]*60+parts[2]


def events(table):
    pos=14
    while pos+12<=len(table):
        end=pos+12+(u16(table,pos+10)&0xfff)
        if end>len(table):break
        try:
            clock=bcd_time(table[pos+4:pos+7])
            if clock>=86400:raise ValueError('invalid hour')
            start=(u16(table,pos+2)-40587)*86400+clock
            stop=start+bcd_time(table[pos+7:pos+10])
        except ValueError:
            pos=end
            continue
        for tag, data in descriptors(table[pos+12:end]):
            if tag!=0x4d or len(data)<5:continue
            split=4+data[3]
            if split>=len(data) or split+1+data[split]>len(data):continue
            title=dvb_text(data[4:split],data[:3])
            if title and stop>start:
                yield start,stop,title,dvb_text(data[split+1:split+1+data[split]],data[:3])
                break
        pos=end


def programmes(packets,item):
    pats=defaultdict(set)
    names=defaultdict(set)
    schedules=defaultdict(dict)
    for pid,s in sections(packets):
        tid=s[0]
        key=u16(s,3)
        if pid==0 and tid==0:
            pats[key].update(u16(s,i) for i in range(8,len(s)-3,4) if u16(s,i))
        elif pid==17 and tid==0x42:
            i=11
            while i+5<=len(s):
                end=i+5+(u16(s,i+3)&0xfff)
                if end>len(s):break
                for tag,d in descriptors(s[i+5:end]):
                    if tag!=0x48 or len(d)<3:continue
                    j=2+d[1]
                    if j<len(d) and j+1+d[j]<=len(d):
                        names[(key,u16(s,8),u16(s,i))].add(normalize(dvb_text(d[j+1:j+1+d[j]])))
                i=end
        elif pid==18 and (tid==0x4e or 0x50<=tid<=0x5f) and len(s)>=14:
            service=(u16(s,8),u16(s,10),key)
            for event in events(s):schedules[service][event[0]]=event
    attrs=item['attrs']
    labels={normalize(n) for n in (item['name'],attrs.get('tvg-name'),attrs.get('tvg-id')) if n}
    candidates=[k for k in schedules if not pats or k[2] in pats.get(k[0],())]
    hint=attrs.get('service-id') or attrs.get('tvg-sid') or next((o.partition('=')[2] for o in item.get('options',[]) if o.startswith('#EXTVLCOPT:program=')),None)
    if hint:
        try:sid=int(hint,16 if hint.lower().startswith('0x') else 10)
        except ValueError:return []
        matched=[k for k in candidates if k[2]==sid]
    else:
        matched=[k for k in candidates if labels & names[k]]
        if not matched and len(candidates)==1 and len(pats)==1:
            k=candidates[0]
            if pats[k[0]]=={k[2]}:matched=[k]
    return list(schedules[matched[0]].values()) if len(matched)==1 else []
