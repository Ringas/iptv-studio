import {eitSample} from './eit-input.mjs';
import {playbackProbe,rateLimitError} from './playback.mjs';
import {dimensions} from './resolution.mjs';
import {mediaFetch} from './media-http.mjs';
import {setTimeout as sleep} from 'node:timers/promises';
import {channel} from 'node:diagnostics_channel';
// One process handles one route. Keep hostnames only, including redirect responses.
const observedHosts=new Set();
channel('undici:request:headers').subscribe(({request})=>{
  if(observedHosts.size<32)observedHosts.add(new URL(request.origin).hostname);
});
const headers = {'User-Agent':'Mozilla/5.0 IPTV-Studio/1.0', 'Accept-Encoding':'identity'};
const playlistHeaders={...headers};
function problem(status, message) { throw Object.assign(new Error(message),{status}); }
function clean(s) {
  return s.replace(/https?:\/\/\S+/g,'[地址]').replace(/[A-Za-z0-9%_=-]{28,}/g,'[标识]').replace(/\s+/g,' ').trim().slice(0,300);
}
async function httpFailure(res, status, stage) {
  if(res.status===607){
    await res.body?.cancel();
    throw Object.assign(new Error(`${stage} ${new URL(res.url).hostname} HTTP 607`),{status:'未能验证',httpStatus:607});
  }
  const reader=res.body?.getReader();
  let body='';
  if(reader) {
    try {
      const {value}=await reader.read();
      if(value) body=clean(new TextDecoder().decode(value.subarray(0,2048)));
    } catch(e) {if(res.status!==429)throw e;} finally {await reader.cancel().catch(()=>{});}
  }
  if(res.status===429)throw rateLimitError(res,stage,body);
  const u=new URL(res.url);
  const clues=[...res.headers].filter(([k])=>/error|reason|reject|deny|authenticat/i.test(k)).map(([k,v])=>k+'='+clean(v));
  throw Object.assign(new Error(stage+' '+u.hostname+' HTTP '+res.status+'，'+(body||'响应正文为空')+(clues.length?'，'+clues.join('，'):'')),{status,httpStatus:res.status});
}
function parseManifest(lines,base){
  if (lines[0]!=='#EXTM3U') problem('非 HLS','响应缺少 #EXTM3U');
  const variants=[], segments=[];
  let variant=null, segment=false, gap=false, range='', prev=null;
  let duration=0,sequence=null,target=6,map=null,encryption='NONE';
  for (const s of lines.slice(1)) {
    if (s.startsWith('#EXT-X-STREAM-INF:')) {
      const size=s.match(/[:,]RESOLUTION=(\d+)x(\d+)/i);
      variant={rate:Number(s.match(/(?:[:,])BANDWIDTH=(\d+)/)?.[1]||0),
               resolution:size?{width:+size[1],height:+size[2],source:'所选 HLS 档位声明'}:null};
    }
    else if (s.startsWith('#EXTINF:')) {segment=true;duration=parseFloat(s.slice(8));}
    else if (s.startsWith('#EXT-X-TARGETDURATION:')) target=Number(s.split(':')[1])||6;
    else if (s.startsWith('#EXT-X-MEDIA-SEQUENCE:')) sequence=Number(s.split(':')[1]);
    else if (s.startsWith('#EXT-X-KEY:')) encryption=s.match(/METHOD=([^,]+)/)?.[1]||'unknown';
    else if (s.startsWith('#EXT-X-MAP:')) {
      const uri=s.match(/URI="([^"]+)"/)?.[1];
      map=uri?{url:new URL(uri,base).href,range:s.match(/BYTERANGE="([^"]+)"/)?.[1]}:null;
    }
    else if (s.startsWith('#EXT-X-BYTERANGE:')) range=s.split(':')[1];
    else if (s==='#EXT-X-GAP') gap=true;
    else if (s && !s.startsWith('#')) {
      const absolute = new URL(s,base).href;
      if (variant!==null) variants.push({url:absolute,...variant});
      else if (segment) {
        let span=null;
        if (range) {
          const [len,offset]=range.split('@').map(Number);
          const start=offset ?? (prev?.url===absolute && prev.span ? prev.span.end+1 : NaN);
          if (!Number.isSafeInteger(start)||!Number.isSafeInteger(len)||len<=0) problem('特殊格式','无效字节范围');
          span={start,end:start+len-1};
        }
        prev={url:absolute,span,gap,duration,map,encryption,seq:sequence===null?null:sequence+segments.length}; segments.push(prev);
      }
      variant=null; segment=false; gap=false; range='';
    }
  }
  return {variants,segments,target,url:base,ended:lines.includes('#EXT-X-ENDLIST')};
}
async function playlist(url, note, depth=0, playback=false, reloadEntry=false) {
  if (depth>5) problem('特殊格式','嵌套清单超过 5 层');
  const ctrl = new AbortController();
  const timer = setTimeout(()=>ctrl.abort(),12000);
  try {
    const res = await mediaFetch(url,{headers:playlistHeaders,signal:ctrl.signal,cache:reloadEntry?'reload':'default'});
    if (!res.ok) return await httpFailure(res,'清单失败',depth?'子清单':'入口清单');
    const chunks = [];
    let size=0;
    for await (const chunk of res.body) {
      chunks.push(chunk); size+=chunk.length;
      if(size>1024*1024) problem('特殊格式','清单超过 1 MiB');
      if(size>=8 && !Buffer.concat(chunks,Math.min(size,80)).toString().trimStart().startsWith('#EXTM3U')) {
        const type=res.headers.get('content-type')||'未知';
        let extra='';
        if(type.includes('json') && size<=8192) {
          let parsed;
          try { parsed=JSON.parse(Buffer.concat(chunks).toString()); } catch {}
          const msg=parsed?.message ?? parsed?.msg ?? parsed?.error;
          if(typeof msg==='string') extra='，'+msg.replace(/https?:\/\/\S+/g,'[地址]').slice(0,120);
          if(parsed?.code!=null && ['string','number'].includes(typeof parsed.code)) extra+='，code='+String(parsed.code).slice(0,24);
        }
        if(type.includes('json') && !extra) extra='，'+clean(Buffer.concat(chunks).toString());
        problem('非 HLS','清单返回 '+type+extra);
      }
    }
    const lines = Buffer.concat(chunks).toString().trim().split(/\r?\n/).map(s=>s.trim());
    const parsed=parseManifest(lines,res.url);
    for(const item of [...parsed.variants,...parsed.segments]){
      if(observedHosts.size>=32)break;
      observedHosts.add(new URL(item.url).hostname);
    }
    if(playback)return parsed;
    const {variants,segments}=parsed;
    if (variants.length) {
      variants.sort((a,b)=>b.rate-a.rate);
      note.push('主清单选最高标称带宽 '+variants[0].rate+' bps');
      
      for (const [index, variant] of variants.entries()) {
        try {
          const result=await playlist(variant.url,note,depth+1);
          result.resolution??=variant.resolution;
          if(index) note.push('改测可读取的档位 '+variant.rate+' bps');
          return result;
        } catch(e) {
          if (index===variants.length-1 || !/HTTP (401|403|404)\b/.test(e.message)) throw e;
          note.push(variant.rate+' bps '+e.message);
        }
      }
    }
    if (segments.length<2) problem('无合规分片','完整媒体分片 '+segments.length+' 个，无法跳过首片再取末片');
    const last=segments.at(-1);
    if (last.gap) problem('无合规分片','末片标记 EXT-X-GAP');
    if(last.span) note.push('按字节范围读取');
    return {...last,count:segments.length};
  } finally { clearTimeout(timer); ctrl.abort(); }
}
async function measure(seg,row) {
  const ctrl=new AbortController();
  const start=performance.now(), end=start+8000;
  const timer=setTimeout(()=>ctrl.abort(),8000);
  const ticker=process.argv.includes('--progress') && setInterval(()=>{
    const elapsed=performance.now()-start;
    console.log(JSON.stringify({sample:true,speed:row.bytes/elapsed,seconds:Math.min(8,Math.floor(elapsed/1000))}));
  },1000);
  const h={...headers,...(seg.span?{Range:'bytes='+seg.span.start+'-'+seg.span.end}:{})};
  const sample=[];let sampled=0;
  try {
    while(performance.now()<end) {
      row.requests++;
      const res=await mediaFetch(seg.url,{signal:ctrl.signal,headers:h});
      if(!res.ok) return await httpFailure(res,'分片失败','末片');
      if(seg.span && (res.status!==206 || !res.headers.get('content-range')?.startsWith('bytes '+seg.span.start+'-'+seg.span.end+'/')))
        problem('分片失败','服务器未按请求返回指定字节范围');
      const type=res.headers.get('content-type')||'';
      if (/^text\/|json|mpegurl|xml/i.test(type)) problem('分片失败','末片返回 '+type);
      let received=0;
      for await (const bytes of res.body) {
        if(performance.now()>=end) break;
        if (!received && /^(?:<html|<!doctype|<\?xml|#EXTM3U|\{\s*"|\[\s*\{)/i.test(Buffer.from(bytes.subarray(0,64)).toString().trimStart())) problem('分片失败','末片响应为文本文档');
        if(row.requests===1 && sampled<1048576){
          const chunk=Buffer.from(bytes.subarray(0,1048576-sampled));
          sample.push(chunk);sampled+=chunk.length;
        }
        received+=bytes.length; row.bytes+=bytes.length;
      }
      if(performance.now()>=end) break;
      if(!received) problem('分片失败','末片响应为空');
      row.full++;
    }
  } catch(e) { if(!ctrl.signal.aborted) throw e; }
  finally { clearInterval(ticker); clearTimeout(timer); ctrl.abort(); row.seconds=Math.min(8000,performance.now()-start)/1000; }
  if(!row.bytes) problem('分片超时','8 秒内没有收到媒体数据');
  row.seconds=8;
  row.speed=row.bytes/8000;
  row.resolution=dimensions(Buffer.concat(sample))||seg.resolution||null;
  row.status=row.bytes>800000?'保留':'未过门槛';
}
function setPlaybackHeader(target,name,value){
  const names={'user-agent':'User-Agent',referrer:'Referer',referer:'Referer',origin:'Origin'};
  const key=names[name.toLowerCase()];
  if(key&&value&&!/[\r\n]/.test(value))target[key]=value;
}
for(const option of JSON.parse(process.env.IPTV_PROBE_OPTIONS||'[]')){
  const vlc=String(option).match(/^#EXTVLCOPT:http-([\w-]+)=(.*)$/i);
  if(vlc)for(const target of [headers,playlistHeaders])setPlaybackHeader(target,vlc[1],vlc[2]);
  const kodi=String(option).match(/^#KODIPROP:inputstream\.adaptive\.(stream|common|manifest)_headers=(.*)$/i);
  if(kodi){
    const type=kodi[1].toLowerCase();
    for(const [name,value] of new URLSearchParams(kodi[2])){
      if(type!=='manifest')setPlaybackHeader(headers,name,value);
      if(type!=='stream')setPlaybackHeader(playlistHeaders,name,value);
    }
  }
}
const timing={started:performance.now()/1000};
async function testEntry(reloadEntry=false){
 const entry=process.argv[2];
 const row={at:new Date().toISOString(),bytes:0,requests:0,full:0,note:[]};
 try {
  if(process.argv.includes('--eit')){
    row.packets=await eitSample(process.argv[2],playlistHeaders,headers,parseManifest);
  }else if(process.argv.includes('--continuous')||process.argv.includes('--quick')){
    Object.assign(row,await playbackProbe(entry,process.argv.includes('--quick'),headers,
      url=>playlist(url,row.note,0,true,reloadEntry&&url===entry),timing));
  }else{
    const seg=await playlist(entry,row.note,0,false,reloadEntry);
    row.segments=seg.count;
    await measure(seg,row);
  }
 } catch(e) {
  row.status=e.status || (row.requests?'分片失败':'清单失败');
  row.httpStatus=e.httpStatus;
  row.retryAfter=e.retryAfter;
  row.retryable=[408,425,500,502,503,504,607].includes(e.httpStatus)||e.name==='AbortError'||/timeout|ECONN|UND_ERR|fetch failed/i.test(e.cause?.code||e.message);
  row.note.push(e.name==='AbortError'?'清单超时':(e.cause?.code||clean(e.message)));
 }
 return row;
}
let row=await testEntry(process.env.IPTV_PROBE_RECHECK==='1');
const reconnects=[];
while((row.httpStatus===607||row.retryable) && process.env.IPTV_PROBE_MANAGED!=='1' && !process.argv.includes('--eit') && reconnects.length<1){
  reconnects.push(row.note.at(-1)||row.status);
  if(process.argv.includes('--progress'))console.log(JSON.stringify({sample:true,phase:`首次未连通，等待 ${row.httpStatus===403?8:6} 秒后重新打开订阅入口（1/1）`}));
  await sleep(row.httpStatus===403?8000:6000);
  // Revalidate the original subscription URL, including cached dispatch redirects.
  // Its query may be signed: keep the URL intact.
  row=await testEntry(true);
}
if(reconnects.length){
  row.intermittent=true;
  row.entry_retries=reconnects.length;
  row.note.unshift(`从原始订阅入口重连 ${reconnects.length} 次；此前响应：${[...new Set(reconnects)].join('、')}`);
  if(row.httpStatus===607)row.note.push('本次取得的播放地址均返回 607，未取得可验证的媒体数据');
}
if(row.method==='quick'||row.method==='continuous'){
  if(timing.startup!=null)row.startup=timing.startup;
  row.entry_retries=reconnects.length;
  if(row.advanced)row.opening=reconnects.length?'重连后可播':'直连可播';
}
if(observedHosts.size){
  row.observed_hosts=[...observedHosts];
  row.observed_at=Math.floor(Date.now()/1000);
}
console.log(JSON.stringify(row));
