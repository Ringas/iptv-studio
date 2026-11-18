// Deterministic transport faults: only managed timeouts bypass the local retry.
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const folder=await mkdtemp(join(tmpdir(),'iptv-timeout-policy-'));
try{
 await writeFile(join(folder,'playback.mjs'),await readFile(new URL('./playback.mjs',import.meta.url)));
 await writeFile(join(folder,'media-http.mjs'),`export {responseFailure,upstreamFailureError} from ${JSON.stringify(new URL('./media-http.mjs',import.meta.url).href)};export let calls=0;
export function reset(){calls=0;}
export async function mediaFetch(){calls++;
 const kind=process.env.FIXTURE_FAULT;
 if(kind==='body')throw new DOMException('The operation was aborted.','AbortError');
 if(kind==='connect')throw Object.assign(new Error('fetch failed'),{cause:{code:'UND_ERR_CONNECT_TIMEOUT'}});
 if(kind==='short')throw new Error('socket reset');
 throw Object.assign(new Error('HTTP '+kind),{httpStatus:Number(kind)});
}`);
 const {playbackProbe}=await import(pathToFileURL(join(folder,'playback.mjs')));
 const stub=await import(pathToFileURL(join(folder,'media-http.mjs')));
 const manifest=async()=>({url:'http://fixture.test/live',target:2,variants:[],segments:[0,1].map(seq=>({seq,url:'http://fixture.test/'+seq,duration:2,encryption:'NONE'}))});
 for(const [fault,managed,count] of [['body','1',1],['connect','1',1],['504','1',1],['body','0',2],['short','1',2],['429','1',1],['607','1',1]]){
  process.env.FIXTURE_FAULT=fault;process.env.IPTV_PROBE_MANAGED=managed;stub.reset();
  const r=await playbackProbe('http://fixture.test/live',true,{},manifest,{started:performance.now()/1000});
  assert.equal(stub.calls,count,JSON.stringify({fault,managed,r}));
  if(['body','connect','504'].includes(fault)){
   assert.equal(r.quality,'未能验证');assert(r.note.some(n=>n.includes('超时')));
   if(managed==='1')assert.equal(r.retry_deferred,'entry_cooldown');
  }
 }
 console.log('PASS: managed body/connect/HTTP504 timeout deferred once, standalone and short-disconnect local retry retained, 429/policy not retried inline');
}finally{await rm(folder,{recursive:true,force:true});}
