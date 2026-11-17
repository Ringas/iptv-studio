// Deterministic playlist clocks + actual FFmpeg: no internet or user data.
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
const dir=await mkdtemp(join(tmpdir(),'iptv-recovery-trace-'));
try{
 const ff=process.env.FFMPEG_BIN||'ffmpeg';
 const make=(duration)=>{
  const r=spawnSync(ff,['-v','error','-f','lavfi','-i','color=c=blue:s=1280x720:r=40','-t',String(duration),'-c:v','libx264','-f','mpegts','pipe:1']);
  assert.equal(r.status,0,String(r.stderr));return r.stdout;
 };
 for(const [name,seconds] of [['normal',1],['initial',1.5],['tail',.925],['short',.5]])await writeFile(join(dir,name+'.ts'),make(seconds));
 await writeFile(join(dir,'clock.mjs'),`let t=100;export const now=()=>t;export function reset(){t=100;}export function advance(s){t+=s;}export async function sleep(ms){t+=ms/1000;}`);
 await writeFile(join(dir,'media-http.mjs'),`import {readFile} from 'node:fs/promises';import {advance} from './clock.mjs';
export async function mediaFetch(address){const n=Number(new URL(address).pathname.split('/').at(-1));
 advance(.001+(globalThis.fixtureCase==='closed'&&n===4?.15:0));
 const file=globalThis.fixtureCase==='tail'&&(n===2||n===3)?'initial':globalThis.fixtureCase==='tail'&&(n===24||n===25)?'tail':globalThis.fixtureCase==='tail'&&(n===10||n===11)?'short':'normal';
 if(globalThis.fixtureCase==='limited'&&n===6){const r=new Response('',{status:429,headers:{'Retry-After':'9'}});Object.defineProperty(r,'url',{value:address});return r;}
 return new Response(await readFile(new URL('./'+file+'.ts',import.meta.url)),{headers:{'Content-Type':'video/mp2t'}});
}`);
 let source=await readFile(new URL('./playback.mjs',import.meta.url),'utf8');
 source=source.replace("import {setTimeout as sleep} from 'node:timers/promises';","import {sleep} from './clock.mjs';").replace('const now=()=>performance.now()/1000;',"import {now,advance} from './clock.mjs';");
 const decodeAnchor='const decoded=await decodeFrames(Buffer.concat([init,...decodeSamples]),!quick);';
 assert(source.includes(decodeAnchor));
 source=source.replace(decodeAnchor,decodeAnchor+"if(globalThis.fixtureCase==='validation'&&fresh&&!verifiedNew)advance(.15);");
 await writeFile(join(dir,'playback.mjs'),source);
 const clock=await import(pathToFileURL(join(dir,'clock.mjs'))),{playbackProbe}=await import(pathToFileURL(join(dir,'playback.mjs')));
 const manifest=async()=>{
  const t=clock.now()-100;let seq=Math.floor(t/2)*2;
  if(globalThis.fixtureCase==='tail'&&t>=22)seq=22;
  return {url:'http://fixture.test/live',target:2,variants:[],segments:[0,1,2,3].map(i=>{
   const n=seq+i;return {url:'http://fixture.test/'+n,seq:n,encryption:'NONE',duration:globalThis.fixtureCase==='tail'&&(n===2||n===3)?1.5:globalThis.fixtureCase==='tail'&&(n===24||n===25)?.925:globalThis.fixtureCase==='tail'&&(n===10||n===11)?.5:1};
  })};
 };
 process.env.IPTV_PROBE_MIN_HEIGHT='720';
 async function run(kind,managed='1',recheck='0'){
  clock.reset();globalThis.fixtureCase=kind;process.env.IPTV_PROBE_MANAGED=managed;process.env.IPTV_PROBE_RECHECK=recheck;
  return playbackProbe('http://fixture.test/live',false,{},manifest,{started:clock.now(),pipeline:true});
 }
 const closed=await run('closed');
 assert.equal(closed.quality,'流畅',JSON.stringify(closed));assert.equal(closed.recovery.state,'passed');
 assert(closed.recovery.seconds>=24);assert.equal(closed.recovery.trigger.eligible,true);
 assert(closed.buffer_estimate.trace.some(e=>e.closed_gap_seconds>.1&&e.closed_gap_seconds<.2));
 assert.equal(closed.recovery.decode.pts_advanced,true);assert(closed.recovery.decode.frames>=3);
 const validation=await run('validation');
 assert.equal(validation.recovery.state,'passed');
 assert(validation.buffer_estimate.trace.some(e=>e.decode_checked&&e.validation_seconds===.15&&e.closed_gap_seconds>.1));
 const tail=await run('tail');
 assert.equal(tail.quality,'可播但不稳',JSON.stringify(tail));assert.equal(tail.stalls,1);
 assert(tail.stall_seconds>=.14&&tail.stall_seconds<=.16,JSON.stringify(tail));
 assert.equal(tail.recovery,undefined);assert.deepEqual(tail.recovery_check.reasons,['buffer_gap_not_closed']);
 assert.equal(tail.buffer_estimate.is_starving,true);
 assert(tail.buffer_estimate.trace.some(e=>e.phase==='window_end'&&e.open_gap_seconds>0));
 const retry=await run('closed','1','1');assert(!retry.recovery);assert(retry.recovery_check.reasons.includes('entry_recheck'));
 const unmanaged=await run('closed','0');assert(!unmanaged.recovery);assert(unmanaged.recovery_check.reasons.includes('not_managed'));
 const limited=await run('limited');assert.equal(limited.httpStatus,429);assert.equal(limited.quality,'未能验证');
 assert(limited.buffer_estimate.trace.some(e=>e.phase==='probe_error'));assert(limited.recovery_check.reasons.includes('probe_incomplete'));
 console.log('PASS: closed AnDo-like 0.15s gap triggers full 24s recovery; unresolved 0.15s tail stays unsteady; validation CPU time retained; recheck/unmanaged blocked; 429 keeps error diagnostics and unknown verdict');
}finally{await rm(dir,{recursive:true,force:true});}
