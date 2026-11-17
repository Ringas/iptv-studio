import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {playlistRefreshDecision} from './playback.mjs';
for(const scheduled of [.3,.1+.2,.3-Number.EPSILON,.3+Number.EPSILON])assert.equal(playlistRefreshDecision(scheduled,.3,.2),'finish');
assert.equal(playlistRefreshDecision(129.999999,130,120),'reload');
assert.equal(playlistRefreshDecision(130,130,120),'finish');
assert.equal(playlistRefreshDecision(129,130,130),'finish');
assert.throws(()=>playlistRefreshDecision(NaN,130,120));
const dir=await mkdtemp(join(tmpdir(),'iptv-refresh-deadline-'));
try{
 const r=spawnSync(process.env.FFMPEG_BIN||'ffmpeg',['-v','error','-f','lavfi','-i','color=c=blue:s=1280x720:r=12','-t','10','-c:v','libx264','-f','mpegts','pipe:1']);assert.equal(r.status,0,String(r.stderr));
 await writeFile(join(dir,'clip.ts'),r.stdout);
 await writeFile(join(dir,'clock.mjs'),`let t=100,calls=0,mode='normal';export const now=()=>t;export function reset(m){t=100;calls=0;mode=m;}export const advance=s=>{t+=s;};export const count=()=>calls;export async function sleep(ms){calls++;t+=mode==='late'&&calls===1?35:Math.max(.000001,ms/1000-(mode==='early'?.0004:0));}`);
 await writeFile(join(dir,'media-http.mjs'),`import {readFile} from 'node:fs/promises';import {advance} from './clock.mjs';export async function mediaFetch(url){advance(.005);const n=Number(new URL(url).pathname.slice(1));const response=globalThis.testCase==='limited'&&n===4?new Response('',{status:429,headers:{'Retry-After':'8'}}):new Response(await readFile(new URL('./clip.ts',import.meta.url)),{headers:{'Content-Type':'video/mp2t'}});Object.defineProperty(response,'url',{value:url});return response;}`);
 let source=await readFile(new URL('./playback.mjs',import.meta.url),'utf8');source=source.replace("import {setTimeout as sleep} from 'node:timers/promises';","import {sleep} from './clock.mjs';").replace('const now=()=>performance.now()/1000;',"import {now} from './clock.mjs';");await writeFile(join(dir,'playback.mjs'),source);
 const clock=await import(pathToFileURL(join(dir,'clock.mjs'))),api=await import(pathToFileURL(join(dir,'playback.mjs')));
 process.env.IPTV_PROBE_MANAGED='1';process.env.IPTV_PROBE_RECHECK='0';process.env.IPTV_PROBE_MIN_HEIGHT='720';
 const records=[];
 for(const kind of ['normal','early','late','longer','frozen','limited','key_delay']){
  clock.reset(kind);globalThis.testCase=kind;const requests=[];
  const manifest=async()=>{
   requests.push(clock.now());clock.advance(.08);
   const seq=kind==='frozen'?0:kind==='key_delay'?requests.length-1:Math.floor((clock.now()-100.08)/10);
   return {url:'http://fixture.test/live',target:kind==='longer'&&requests.length>1?20:kind==='key_delay'&&requests.length===2?9:10,variants:[],segments:[0,1,2,3].map(i=>{
    const n=seq+i,s={url:'http://fixture.test/'+n,duration:kind==='key_delay'?9:10,encryption:'NONE'};
    Object.defineProperty(s,'seq',{get(){if(kind==='key_delay'&&clock.now()>=129)clock.advance(.4);return n;}});return s;
   })};
  };
  const result=await api.playbackProbe('http://fixture.test/live',false,{},manifest,{started:clock.now(),pipeline:true});
  const deadline=130.08;
  assert(requests.slice(1).every(t=>t<deadline),JSON.stringify({kind,requests,result}));
  if(['normal','early','longer'].includes(kind)){
   assert.equal(result.quality,'流畅',JSON.stringify(result));assert(result.seconds>=30,result.seconds);
   assert(result.advanced&&result.evidence.decode.frames>=3&&result.evidence.decode.pts_advanced);
   assert.equal(requests.length,kind==='longer'?2:3,JSON.stringify({kind,requests}));
  }
  if(kind==='late'){assert.equal(requests.length,1);assert.notEqual(result.quality,'流畅');}
  if(kind==='frozen'){assert(result.seconds>=30);assert.notEqual(result.quality,'流畅');}
  if(kind==='key_delay'){assert.equal(requests.length,3);assert(result.seconds>=30);assert.equal(result.quality,'流畅');assert(result.buffer_estimate.trace.some(e=>e.phase==='refresh_skipped_before_request'));}
  if(kind==='limited'){assert.equal(result.httpStatus,429);assert.equal(result.quality,'未能验证');}
  if(kind==='early')assert(clock.count()>2,'early wake must be re-waited rather than used as a refresh start');
  records.push({kind,seconds:result.seconds,requests:requests.map(t=>t-100.08),timer_calls:clock.count(),quality:result.quality});
 }
 clock.reset('early');await api.waitUntil(100.0002);assert(clock.now()>=100.0002);assert(clock.count()<4);
 console.log('PASS: CGTN10s target waits whole30s, no deadline refresh; early/late wakes, floating boundary, target change, frozen and429 retain standards');
 console.log(JSON.stringify(records));
}finally{await rm(dir,{recursive:true,force:true});}
