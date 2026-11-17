import {spawn} from 'node:child_process';
import {setTimeout as sleep} from 'node:timers/promises';
import {mediaFetch} from './media-http.mjs';

const now=()=>performance.now()/1000;
const round=n=>Math.round(n*100)/100;
const unverified=message=>Object.assign(new Error(message),{unverified:true});
const key=s=>s.seq===null?JSON.stringify([s.url,s.span]):String(s.seq);
const timedOut=e=>[408,504].includes(e.httpStatus)||['TimeoutError','AbortError'].includes(e.name)||
  /ETIMEDOUT|UND_ERR_(?:CONNECT|HEADERS|BODY)_TIMEOUT|timeout|timed out/i.test([e.message,e.cause?.code].join(' '));

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

export function decodeFrames(buffer,continuous=false){
  return new Promise((resolve,reject)=>{
    // Only the supplied bytes may be opened. No secondary URLs, devices or media files.
    const ff=spawn(process.env.FFMPEG_BIN||'ffmpeg',['-hide_banner','-loglevel','info','-nostdin','-protocol_whitelist','pipe',
      '-threads','1','-i','pipe:0','-map','0:v:0','-frames:v',continuous?'6':'1','-an','-vf','showinfo','-f','null','-'],
      {windowsHide:true,stdio:['pipe','ignore','pipe']});
    let pending='',size;
    const frames=[];
    function parseLine(line){
      const match=line.match(/Parsed_showinfo[^\n]*\bn:\s*(\d+)[^\n]*\bpts_time:([^\s]+)[^\n]*\bs:(\d+)x(\d+)/);
      if(!match)return;
      const pts=Number(match[2]);
      size={width:+match[3],height:+match[4],source:'FFmpeg 实际解码帧'};
      if(frames.length<16)frames.push({pts,index:+match[1]});
    }
    const timeout=setTimeout(()=>ff.kill('SIGKILL'),4500);
    ff.on('error',()=>{clearTimeout(timeout);reject(unverified('无法运行 FFmpeg 首帧解码器'));});
    ff.stdin.on('error',()=>{});
    ff.stderr.on('data',data=>{
      pending+=data.toString();
      const lines=pending.split('\n');pending=lines.pop();
      for(const line of lines)parseLine(line);
      if(pending.length>16000)pending=pending.slice(-16000);
    });
    ff.on('close',code=>{
      clearTimeout(timeout);
      parseLine(pending);
      const timestamps=frames.map(frame=>frame.pts);
      const advancing=timestamps.length>=3&&timestamps.every((pts,i)=>Number.isFinite(pts)&&(i===0||pts>timestamps[i-1]));
      if(code===0&&size&&(!continuous||advancing)){
        resolve({resolution:size,frames:frames.length,pts:timestamps,pts_advanced:advancing});
      }else{
        const error=unverified(continuous?'精测未取得至少 3 帧及严格推进的有效时间戳':'在抽样媒体中未解出视频帧');
        error.decodeEvidence={state:'not_verified',frames:frames.length,pts:timestamps.map(p=>Number.isFinite(p)?p:null),pts_advanced:advancing};
        reject(error);
      }
    });
    ff.stdin.end(buffer);
  });
}

// One continuous empty-buffer interval is one estimated stall, including tail
// samples. Integer microseconds avoid inventing events at the 0.1s boundary.
export class PlaybackBuffer {
  constructor(){this.last=null;this.available=0;this.missing=0;this.open=0;this.count=0;}
  micros(value){
    if(!Number.isFinite(value)||value<0)throw new RangeError('无效缓冲时间');
    return Math.round(value*1e6);
  }
  advance(at){
    const t=this.micros(at);
    if(this.last===null)return 0;
    if(t<this.last)throw new RangeError('缓冲时钟不能倒退');
    const elapsed=t-this.last,missing=Math.max(0,elapsed-this.available);
    this.available=Math.max(0,this.available-elapsed);this.last=t;
    if(missing){
      const prior=this.open;this.open+=missing;this.missing+=missing;
      if(prior<=100000&&this.open>100000)this.count++;
    }
    return missing/1e6;
  }
  append(duration,at){
    const amount=this.micros(duration);
    if(!amount)throw new RangeError('无效分片时长');
    const added=this.advance(at),closed=this.open/1e6;
    if(this.last===null)this.last=this.micros(at);
    this.available+=amount;this.open=0;
    return {added_stall_seconds:added,closed_gap_seconds:closed};
  }
  snapshot(){
    return {available_seconds:this.available/1e6,stall_seconds:this.missing/1e6,
      stalls:this.count,open_gap_seconds:this.open/1e6,is_starving:this.open>0};
  }
}

function probePolicy(){
  return {criteria:'both',min_speed:100,min_height:720,...JSON.parse(process.env.IPTV_PROBE_POLICY||'{}')};
}
export function recoveryDecision(r){
  const policy=r.policy||probePolicy();
  const reasons=[];
  if(!r.pipeline)reasons.push('not_pipeline');
  if(!r.managed)reasons.push('not_managed');
  if(r.rechecking)reasons.push('entry_recheck');
  if(r.stalls!==1)reasons.push('stall_count_not_one');
  if(!Number.isFinite(r.stall_seconds)||r.stall_seconds<=0||r.stall_seconds>1)reasons.push('stall_duration_outside_limit');
  if(r.gaps!==0)reasons.push('media_gaps');
  if(r.retries!==0)reasons.push('media_retries');
  if(!r.advanced)reasons.push('live_not_verified');
  if(!r.decoded||!Number.isFinite(r.decoded_frames)||r.decoded_frames<3)reasons.push('decode_not_verified');
  if(policy.criteria!=='speed'&&(!Number.isFinite(r.height)||r.height<policy.min_height))reasons.push('height_below_threshold');
  if(policy.criteria!=='resolution'&&(!Number.isFinite(r.speed)||r.speed<=policy.min_speed))reasons.push('speed_not_above_threshold');
  if(r.buffer_unsettled)reasons.push('buffer_gap_not_closed');
  return {eligible:reasons.length===0,reasons};
}
export const canRecoverInSession=r=>recoveryDecision(r).eligible;

// Compare scheduled instants, not a freshly sampled elapsed+remaining sum.
// Match the buffer clock precision; the actual wait still reaches the deadline.
export function playlistRefreshDecision(reloadAt,deadline,at){
  if(![reloadAt,deadline,at].every(Number.isFinite))throw unverified('无效刷新截止时间');
  return at>=deadline||Math.round(reloadAt*1e6)>=Math.round(deadline*1e6)?'finish':'reload';
}
export async function waitUntil(deadline){
  while(now()<deadline)await sleep(Math.max(1,Math.ceil((deadline-now())*1000)));
}

async function observe(list,quick,headers,manifest,timing,progress){
  const evidence=timing.evidence;
  if(list.ended)throw unverified('清单已结束，无法验证实时直播');
  if(!list.segments.length)throw unverified('未找到完整媒体分片');
  const initial=new Set(list.segments.map(key)),seen=new Set();
  let first=list.segments.length-1,behind=0;
  while(first>0 && behind<3*list.target){behind+=list.segments[first].duration;first--;}
  if(quick||timing.pipeline)first=Math.max(0,list.segments.length-2);
  let pending=list.segments.slice(first),last=null,newMedia=false,stall=0,stalls=0;
  const playbackBuffer=new PlaybackBuffer(),bufferTrace=[];
  let lastDecision=null;
  let consecutiveMissing=0,verifiedNew=false;
  let decodeSamples=[],decodeBytes=0;
  let resolution=null,firstReady=0,bytes=0,download=0,retries=0,gaps=0,segments=0,lastNew=now(),mediaStart=now();
  const window=Math.min(55,Math.max(24,3*list.target)),baseLimit=quick?Math.min(24,Math.max(6,2*list.target)):window;
  let limit=baseLimit,recovery=null;
  function startRecovery(){
    const state=playbackBuffer.snapshot();
    const context={policy:probePolicy(),pipeline:Boolean(timing.pipeline)&&!quick,managed:process.env.IPTV_PROBE_MANAGED==='1',
      rechecking:process.env.IPTV_PROBE_RECHECK==='1',stalls,stall_seconds:stall,gaps,retries,
      advanced:newMedia&&verifiedNew,decoded:evidence.decode?.state==='passed'&&evidence.decode?.pts_advanced,
      decoded_frames:evidence.decode?.frames,height:resolution?.height,speed:download?bytes/(download*1000):0,
      buffer_unsettled:state.is_starving||state.available_seconds<=0};
    lastDecision={...recoveryDecision(context),context};
    if(recovery){lastDecision.eligible=false;lastDecision.reasons.push('recovery_already_attempted');}
    if(!lastDecision.eligible)return false;
    recovery={state:'observing',trigger:lastDecision,started:now(),initialKeys:new Set(list.segments.map(key)),decoded:null,
      initial:{seconds:round(now()-mediaStart),bytes,download,segments,stalls,stall_seconds:round(stall),gaps,retries}};
    limit=now()-mediaStart+window;
    progress('短暂不稳 · 同会话完整复核，不重开入口');
    return true;
  }
  function checkRecovery(){
    if(recovery?.state==='observing'&&(stalls>recovery.initial.stalls||gaps>recovery.initial.gaps||retries>recovery.initial.retries)){
      recovery.state='failed';recovery.reason='继续观察仍有异常，保留原冷却入口复测';limit=baseLimit;
    }
  }
  function recoveryEvidence(){
    return recovery?{state:recovery.state,kind:'same_session',required_seconds:window,seconds:round(now()-recovery.started),
      initial_observation:recovery.initial,trigger:recovery.trigger,decode:recovery.decoded,reason:recovery.reason}:null;
  }
  function syncBuffer(at,duration){
    const change=duration===undefined?{added_stall_seconds:playbackBuffer.advance(at)}:playbackBuffer.append(duration,at);
    const state=playbackBuffer.snapshot();stall=state.stall_seconds;stalls=state.stalls;
    return {...state,...change};
  }
  function traceBuffer(phase,detail={}){
    bufferTrace.push({phase,at:round(now()-mediaStart),...playbackBuffer.snapshot(),...detail});
    if(bufferTrace.length>64)bufferTrace.shift();
  }
  function diagnostics(){
    return {buffer_estimate:{model:'declared_duration_at_probe_ready',threshold_seconds:0.1,
      ...playbackBuffer.snapshot(),trace:bufferTrace.slice()},recovery_check:lastDecision,
      ...(recovery?{recovery:recoveryEvidence()}:{} )};
  }
  timing.observeFailure=error=>{
    syncBuffer(now());traceBuffer('probe_error',{error:error.name,httpStatus:error.httpStatus});
    if(recovery?.state==='observing'){recovery.state='interrupted';recovery.reason=error.message;}
    lastDecision={...lastDecision,eligible:false,reasons:[...(lastDecision?.reasons||[]),'probe_incomplete']};
    return diagnostics();
  };
  const notes=[];
  let handoff=null,decodeCalls=0;
  // RFC 8216 section 6.3.4: changed playlists wait a target duration;
  // unchanged playlists wait half. Downloads/decoding already consume that wait.
  let reloadAt=mediaStart+list.target;
  function result(quality){
    return {quality,seconds:round(now()-mediaStart),segments,bytes,speed:download?round(bytes/(download*1000)):null,
      resolution,play_seconds:firstReady?round(now()-firstReady):0,stalls,stall_seconds:round(stall),advanced:newMedia,retries,gaps,note:notes,...diagnostics(),...(timing.pipeline?{handoff:{...handoff,observation_seconds:round(now()-mediaStart),decode_calls:decodeCalls,session_reused:Boolean(handoff)}}:{})};
  }
  for(;;){
  while(now()<mediaStart+limit){
    checkRecovery();if(now()>=mediaStart+limit)break;
    if(!pending.length){
      if(quick&&segments>=2&&verifiedNew)break;
      const deadline=mediaStart+limit,action=playlistRefreshDecision(reloadAt,deadline,now());
      traceBuffer('refresh_boundary',{action,scheduled_at_seconds:reloadAt-mediaStart,deadline_seconds:limit,
        requested_wait_seconds:Math.max(0,Math.min(reloadAt,deadline)-now())});
      if(action==='finish'){
        // Observe the whole window, but do not start a deadline refresh whose
        // response could not be consumed by the media loop.
        await waitUntil(deadline);
        traceBuffer('window_deadline',{deadline_seconds:limit});
        break;
      }
      await waitUntil(reloadAt);
      // Recheck after waking: timers can wake early or the event loop can run late.
      if(now()>=deadline){traceBuffer('refresh_skipped_after_wake',{deadline_seconds:limit});break;}
      const priorKeys=list.segments.map(key).join('\n'),reloadStarted=now();
      if(reloadStarted>=deadline){traceBuffer('refresh_skipped_before_request',{deadline_seconds:limit});break;}
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
    const recoveryFrame=recovery?.state==='observing'&&!recovery.decoded&&!recovery.initialKeys.has(last);
    const fresh=!initial.has(last),verifyFrame=!resolution||(fresh&&!verifiedNew)||recoveryFrame;
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
      // The service already owns a cooled entry retry. Do not stack another full
      // timeout against the same stale segment before giving that retry a turn.
      if(process.env.IPTV_PROBE_MANAGED==='1'&&timedOut(e)){
        e.retryable=true;e.retryDeferred='entry_cooldown';e.stage??='media';
        notes.push('媒体请求已超时，不立即重复同一分片；交由入口冷却复测');
        throw e;
      }
      retries++;await sleep(1500);
      data=await readMedia(seg,headers,verifyFrame?4*1024*1024:0);
    }
    evidence.media={state:'passed',bytes:(evidence.media?.bytes||0)+data.bytes};
    const mediaReady=now();
    consecutiveMissing=0;bytes+=data.bytes;download+=mediaReady-t;
    if(verifyFrame){
      decodeSamples.push(data.sample);decodeBytes+=data.sample.length;
      try{
        decodeCalls++;
        const decoded=await decodeFrames(Buffer.concat([init,...decodeSamples]),!quick);
        resolution=decoded.resolution;
        evidence.decode={state:'passed',...decoded};
        if(recoveryFrame)recovery.decoded=decoded;
        decodeSamples=[];decodeBytes=0;
      }catch(error){
        if(error.decodeEvidence)evidence.decode=error.decodeEvidence;
        if(!error.unverified||decodeSamples.length>=3||decodeBytes>=8*1024*1024)throw error;
        notes.push('当前分片尚未解出画面，拼接后续分片继续验证');
        continue;
      }
      if((quick||timing.pipeline)&&Number(process.env.IPTV_PROBE_MIN_HEIGHT)>resolution.height){
        return {...result('初检可播'),coarse_low:true};
      }
      if(timing.pipeline&&!handoff){
        handoff={coarse_seconds:round(now()-mediaStart),reused_bytes:bytes,reused_decoded_frames:evidence.decode.frames,initial_sequence:list.segments[0]?.seq};
        progress('精测 · 沿用粗筛媒体、解码和清单进度');
      }
      if(!segments){
        firstReady=now();
        timing.startup??=round(firstReady-timing.started);
      }
      if(fresh)verifiedNew=true;
    }
    const ready=now();
    const bufferChange=syncBuffer(ready,seg.duration);segments++;
    traceBuffer('segment_ready',{segment:seg.seq??segments,duration:seg.duration,
      decode_checked:verifyFrame,media_seconds:round(mediaReady-t),validation_seconds:round(ready-mediaReady),...bufferChange});
    if(!initial.has(last)){newMedia=true;lastNew=ready;}
    checkRecovery();startRecovery();
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
  syncBuffer(now());traceBuffer('window_end');
  checkRecovery();
  if(startRecovery())continue;
  if(recovery?.state==='observing'){
    const cleanBytes=bytes-recovery.initial.bytes,cleanDownload=download-recovery.initial.download;
    const speed=cleanDownload?round(cleanBytes/(cleanDownload*1000)):null;
    if(now()>=recovery.started+window&&recovery.decoded?.pts_advanced&&segments-recovery.initial.segments>=2&&(probePolicy().criteria==='resolution'||speed>probePolicy().min_speed)&&(probePolicy().criteria==='speed'||resolution?.height>=probePolicy().min_height)){
      recovery.state='passed';
      notes.push('初始观察有短暂缓冲不足，已在同一会话完成完整无异常窗口、多帧解码和新增分片复核');
      return {...result('流畅'),bytes:cleanBytes,speed,segments:segments-recovery.initial.segments,
        stalls:stalls-recovery.initial.stalls,stall_seconds:round(stall-recovery.initial.stall_seconds),
        retries:retries-recovery.initial.retries,gaps:gaps-recovery.initial.gaps,advanced:true};
    }
    recovery.state='failed';recovery.reason='完整窗口未取得全部达标证据，保留原冷却入口复测';
  }
  return result(stalls||gaps||retries>=2?'可播但不稳':'流畅');
  }
}

export async function playbackProbe(url,quick,headers,manifest,timing){
  timing.evidence={entry:{state:'not_verified'},manifest:{state:'not_verified'},media:{state:'not_verified'},decode:{state:'not_verified'},live:{state:'not_verified'}};
  const started=timing.started,mode=quick?'quick':'continuous',notes=[];
  let phase='正在打开清单',attempts=0;
  const tick=setInterval(()=>console.log(JSON.stringify({sample:true,phase,seconds:Math.floor(now()-started)})),1000);
  async function visit(address,depth){
    delete timing.observeFailure;
    if(depth>4)throw unverified('清单嵌套过深');
    const list=await manifest(address);
    timing.evidence.entry={state:'passed'};timing.evidence.manifest={state:'passed'};
    if(!list.variants.length){
      if(++attempts>3)throw unverified('已达到档位尝试上限');
      return observe(list,quick,headers,manifest,timing,msg=>{phase=msg;});
    }
    const minHeight=Number(process.env.IPTV_PROBE_MIN_HEIGHT)||0;
    const possible=v=>!v.resolution?.height||v.resolution.height>=minHeight;
    const sorted=list.variants.sort((a,b)=>b.rate-a.rate);
    const variants=(quick||timing.pipeline)&&minHeight?sorted.filter(possible):sorted.length<=3?sorted:[sorted[0],sorted[Math.floor(sorted.length/2)],sorted.at(-1)];
    // When declarations advertise only low variants, decode the best one before rejecting.
    if(!variants.length)variants.push(sorted[0]);
    let error,degraded,low,unresolved=false;
    for(const v of variants){
      if(attempts>=3){unresolved=true;break;}
      error=null;
      try{
        const r=await visit(v.url,depth+1);
        r.bitrate??=v.rate;
        if(v!==variants[0])r.fallback=true;
        if(r.coarse_low){low??=r;unresolved ||= Boolean(r.candidate_possible);continue;}
        if(r.quality!=='可播但不稳')return r;
        degraded??=r;
      }catch(e){if([429,607].includes(e.httpStatus))throw e;error=e;unresolved=true;}
      notes.push(`档位 ${v.rate} bps · ${error?.message||'连续性不足'}`);
      if(v!==variants.at(-1))await sleep(1500);
    }
    if(degraded)return degraded;
    if(low)return {...low,candidate_possible:unresolved};
    throw error||unverified('未找到可验证的档位');
  }
  try{
    const r=await visit(url,0);
    timing.evidence.live={state:r.advanced?'passed':'not_verified'};
    if(timing.pipeline&&r.coarse_low&&r.candidate_possible){
      return {...r,evidence:timing.evidence,method:mode,quality:'未能验证',status:'未能验证',retryable:true,note:[...notes,...r.note,'尚有候选未完成验证，冷却后以连续检测复核']};
    }
    if(r.coarse_low&&!r.candidate_possible){
      r.coarse_rejected=true;
      r.note.push(`实际解码高度低于 ${process.env.IPTV_PROBE_MIN_HEIGHT}，且无其他待验证的达标候选档位；提前结束粗筛`);
    }
    return {...r,evidence:timing.evidence,method:mode,status:'保留',note:[...notes,...r.note]};
  }catch(e){
    if(timedOut(e))notes.push('请求超时，未能验证；不据此认定线路失效');
    const unknown=timedOut(e)||e.unverified||['非 HLS','特殊格式'].includes(e.status)||[401,403,429,607].includes(e.httpStatus);
    const retryable=e.retryable===true||[408,425,500,502,503,504,607].includes(e.httpStatus)||(!e.httpStatus&&!e.unverified&&!['非 HLS','特殊格式'].includes(e.status));
    return {...timing.observeFailure?.(e),evidence:timing.evidence,method:mode,quality:unknown?'未能验证':'暂时失败',status:unknown?'未能验证':'暂时失败',httpStatus:e.httpStatus,stage:e.stage,retryAfter:e.retryAfter,retryable,retry_deferred:e.retryDeferred,note:[...notes,e.message],seconds:round(now()-started)};
  }finally{clearInterval(tick);delete timing.observeFailure;}
}
