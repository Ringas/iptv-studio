import {spawn} from 'node:child_process';
import {setTimeout as sleep} from 'node:timers/promises';
import {mediaFetch} from './media-http.mjs';

const now=()=>performance.now()/1000;
const round=n=>Math.round(n*100)/100;
const unverified=message=>Object.assign(new Error(message),{unverified:true});
const key=s=>s.seq===null?JSON.stringify([s.url,s.span]):String(s.seq);

export function rateLimitError(response,stage,detail=''){
  const header=response.headers.get('retry-after')?.trim();
  const seconds=header && /^\d+$/.test(header)?Number(header):(Date.parse(header)-Date.now())/1000;
  const hint=detail.match(/(?:停止探测|等待|冷却)[^\d]{0,8}(\d+)\s*秒/);
  const retryAfter=Math.max(6,Math.ceil(seconds)||0,hint?Number(hint[1])+1:0);
  return Object.assign(new Error(stage+' '+new URL(response.url).hostname+' HTTP 429'+(detail?'，'+detail:'')),
    {status:'上游限流',httpStatus:429,retryAfter});
}

async function readMedia(seg,headers,sampleLimit){
  const h={...headers};
  if(seg.span)h.Range=`bytes=${seg.span.start}-${seg.span.end}`;
  const res=await mediaFetch(seg.url,{headers:h,signal:AbortSignal.timeout(10000)});
  const chunks=[];
  let bytes=0,sampled=0;
  try{
    if(res.status===429)throw rateLimitError(res,'媒体节点');
    if(!res.ok)throw Object.assign(new Error(`媒体节点 ${new URL(res.url).hostname} HTTP ${res.status}`),
      {httpStatus:res.status,stage:'media',retryable:res.status===403});
    if(seg.span && (res.status!==206||!res.headers.get('content-range')?.startsWith(`bytes ${seg.span.start}-${seg.span.end}/`)))
      throw unverified('服务器未返回指定媒体字节范围');
    if(/text\/html|json|mpegurl/i.test(res.headers.get('content-type')||''))throw new Error('媒体地址返回清单或错误页面');
    for await(const b of res.body){
      if(bytes+b.length>64*1024*1024)throw unverified('单片超出检测流量上限');
      if(sampled<sampleLimit){
        const part=Buffer.from(b.subarray(0,sampleLimit-sampled));
        chunks.push(part);sampled+=part.length;
      }
      bytes+=b.length;
    }
    if(!bytes || (seg.span&&bytes!==seg.span.end-seg.span.start+1))throw new Error('媒体分片不完整');
    return {bytes,sample:Buffer.concat(chunks)};
  }finally{if(!res.bodyUsed)await res.body?.cancel();}
}

function decodeFrame(buffer){
  return new Promise((resolve,reject)=>{
    // Only the supplied bytes may be opened. No secondary URLs, devices or media files.
    const ff=spawn(process.env.FFMPEG_BIN||'ffmpeg',['-hide_banner','-loglevel','info','-nostdin','-protocol_whitelist','pipe',
      '-threads','1','-i','pipe:0','-map','0:v:0','-frames:v','1','-an','-vf','showinfo','-f','null','-'],
      {windowsHide:true,stdio:['pipe','ignore','pipe']});
    let output='',size;
    const timeout=setTimeout(()=>ff.kill('SIGKILL'),4500);
    ff.on('error',()=>{clearTimeout(timeout);reject(unverified('无法运行 FFmpeg 首帧解码器'));});
    ff.stdin.on('error',()=>{});
    ff.stderr.on('data',data=>{
      output=(output+data.toString()).slice(-16000);
      const match=output.match(/Parsed_showinfo[^\n]*\bn:\s*\d+[^\n]*\bs:(\d+)x(\d+)/);
      if(match)size={width:+match[1],height:+match[2],source:'FFmpeg 实际解码帧'};
    });
    ff.on('close',code=>{
      clearTimeout(timeout);
      if(code===0&&size)resolve(size);else reject(unverified('在抽样媒体中未解出视频帧'));
    });
    ff.stdin.end(buffer);
  });
}

async function observe(list,quick,headers,manifest,timing,progress){
  if(list.ended)throw unverified('清单已结束，无法验证实时直播');
  if(!list.segments.length)throw unverified('未找到完整媒体分片');
  const initial=new Set(list.segments.map(key)),seen=new Set();
  let first=list.segments.length-1,behind=0;
  while(first>0 && behind<3*list.target){behind+=list.segments[first].duration;first--;}
  if(quick)first=Math.max(0,list.segments.length-2);
  let pending=list.segments.slice(first),last=null,newMedia=false,buffer=0,lastReady=0,stall=0,stalls=0;
  let consecutiveMissing=0,verifiedNew=false;
  let decodeSamples=[],decodeBytes=0;
  let resolution=null,firstReady=0,bytes=0,download=0,retries=0,gaps=0,segments=0,lastNew=now(),mediaStart=now();
  const window=Math.min(55,Math.max(24,3*list.target)),limit=quick?Math.min(24,Math.max(6,2*list.target)):window;
  const notes=[];
  // RFC 8216 section 6.3.4: changed playlists wait a target duration;
  // unchanged playlists wait half. Downloads/decoding already consume that wait.
  let reloadAt=mediaStart+list.target;
  function result(quality){
    return {quality,seconds:round(now()-mediaStart),segments,bytes,speed:download?round(bytes/(download*1000)):null,
      resolution,play_seconds:round(now()-firstReady),stalls,stall_seconds:round(stall),advanced:newMedia,retries,gaps,note:notes};
  }
  while(now()-mediaStart<limit){
    if(!pending.length){
      if(quick&&segments>=2&&verifiedNew)break;
      const delay=Math.max(0,reloadAt-now());
      if(now()-mediaStart+delay>=limit)break;
      if(delay)await sleep(delay*1000);
      const priorKeys=list.segments.map(key).join('\n'),reloadStarted=now();
      list=await manifest(list.url);
      const unchanged=list.segments.map(key).join('\n')===priorKeys;
      reloadAt=reloadStarted+Math.max(.5,list.target*(unchanged ? .5 : 1));
      if(list.ended)throw unverified('直播清单已结束');
      const pos=list.segments.findIndex(s=>key(s)===last);
      pending=(pos>=0?list.segments.slice(pos+1):list.segments).filter(s=>!seen.has(key(s)));
      if(pos<0 && last!==null && list.segments.length && list.segments[0].seq!==null && list.segments[0].seq>Number(last)+1)gaps++;
      if(!pending.length&&now()-lastNew>=Math.max(12,3*list.target))throw new Error('清单持续停更，未等到后续分片');
      continue;
    }
    const seg=pending.shift();
    last=key(seg);if(seen.has(last))continue;
    seen.add(last);
    if(seg.gap){gaps++;continue;}
    if(!Number.isFinite(seg.duration)||seg.duration<=0)throw unverified('清单未提供有效分片时长');
    if(seg.encryption!=='NONE')throw unverified('加密线路，当前探测器不能验证解码');
    const fresh=!initial.has(last),verifyFrame=!resolution||(fresh&&!verifiedNew);
    let init=Buffer.alloc(0);
    if(verifyFrame && seg.map){
      let span=null;
      if(seg.map.range){
        const m=seg.map.range.match(/^(\d+)@(\d+)$/);
        if(!m)throw unverified('初始化分片字节范围不完整');
        span={start:+m[2],end:+m[2]+(+m[1])-1};
      }
      const data=await readMedia({url:seg.map.url,span},headers,2*1024*1024);
      if(data.bytes!==data.sample.length)throw unverified('初始化分片超出解码样本上限');
      init=data.sample;
    }
    const t=now();
    let data;
    try{data=await readMedia(seg,headers,verifyFrame?4*1024*1024:0);}
    catch(e){
      if(e.unverified||[401,403,429,607].includes(e.httpStatus))throw e;
      if(/HTTP (404|410)/.test(e.message)){
        gaps++;
        if(++consecutiveMissing>=2)throw e;
        notes.push('跳过已撤下的分片并继续检测');continue;
      }
      retries++;await sleep(1500);
      data=await readMedia(seg,headers,verifyFrame?4*1024*1024:0);
    }
    consecutiveMissing=0;bytes+=data.bytes;download+=now()-t;
    if(verifyFrame){
      decodeSamples.push(data.sample);decodeBytes+=data.sample.length;
      try{
        resolution=await decodeFrame(Buffer.concat([init,...decodeSamples]));
        decodeSamples=[];decodeBytes=0;
      }catch(error){
        if(!error.unverified||decodeSamples.length>=3||decodeBytes>=8*1024*1024)throw error;
        notes.push('当前分片尚未解出画面，拼接后续分片继续验证');
        continue;
      }
      if(!segments){
        firstReady=now();
        timing.startup??=round(firstReady-timing.started);
      }
      if(fresh)verifiedNew=true;
    }
    const ready=now();
    if(segments){
      const elapsed=ready-lastReady,missing=elapsed-buffer;
      if(missing>0){stall+=missing;if(missing>0.1)stalls++;}
      buffer=Math.max(0,buffer-elapsed);
    }
    buffer+=seg.duration;lastReady=ready;segments++;
    if(!initial.has(last)){newMedia=true;lastNew=ready;}
    progress(`${segments} 片 · ${stalls} 次预计卡顿`);
    if(quick&&segments>=2&&verifiedNew)break;
  }
  if(segments<2)throw unverified('仅取得一个可解码分片，连续性尚未验证');
  if(quick){
    if(!verifiedNew)throw unverified('快检观察期内未取得新的可解码分片，建议连续复测');
    return result(gaps||retries||stalls?'可播但不稳':'初检可播');
  }
  if(!newMedia&&limit<3*list.target)throw unverified('分片周期较长，观察窗口内无法确认直播推进');
  if(!newMedia)throw new Error('观察期内直播清单没有新增可读分片');
  const tail=Math.max(0,now()-lastReady-buffer);
  if(tail>0.1){stalls++;stall+=tail;}
  return result(stalls||gaps||retries>=2?'可播但不稳':'流畅');
}

export async function playbackProbe(url,quick,headers,manifest,timing){
  const started=timing.started,mode=quick?'quick':'continuous',notes=[];
  let phase='正在打开清单',attempts=0;
  const tick=setInterval(()=>console.log(JSON.stringify({sample:true,phase,seconds:Math.floor(now()-started)})),1000);
  async function visit(address,depth){
    if(depth>4)throw unverified('清单嵌套过深');
    const list=await manifest(address);
    if(!list.variants.length){
      if(++attempts>3)throw unverified('已达到档位尝试上限');
      return observe(list,quick,headers,manifest,timing,msg=>{phase=msg;});
    }
    const sorted=list.variants.sort((a,b)=>b.rate-a.rate);
    const variants=sorted.length<=3?sorted:[sorted[0],sorted[Math.floor(sorted.length/2)],sorted.at(-1)];
    let error,degraded;
    for(const v of variants){
      if(attempts>=3)break;
      error=null;
      try{
        const r=await visit(v.url,depth+1);
        r.bitrate??=v.rate;
        if(v!==variants[0])r.fallback=true;
        if(r.quality!=='可播但不稳')return r;
        degraded??=r;
      }catch(e){if([429,607].includes(e.httpStatus))throw e;error=e;}
      notes.push(`档位 ${v.rate} bps · ${error?.message||'连续性不足'}`);
      if(v!==variants.at(-1))await sleep(1500);
    }
    if(degraded)return degraded;
    throw error||unverified('未找到可验证的档位');
  }
  try{
    const r=await visit(url,0);
    return {...r,method:mode,status:'保留',note:[...notes,...r.note]};
  }catch(e){
    const unknown=e.unverified||['非 HLS','特殊格式'].includes(e.status)||[401,403,429,607].includes(e.httpStatus);
    const retryable=e.retryable===true||[408,425,500,502,503,504,607].includes(e.httpStatus)||(!e.httpStatus&&!e.unverified&&!['非 HLS','特殊格式'].includes(e.status));
    return {method:mode,quality:unknown?'未能验证':'暂时失败',status:unknown?'未能验证':'暂时失败',httpStatus:e.httpStatus,stage:e.stage,retryAfter:e.retryAfter,retryable,note:[...notes,e.message],seconds:round(now()-started)};
  }finally{clearInterval(tick);}
}
