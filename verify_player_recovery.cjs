const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync('ui.js','utf8');
async function fixture({status={},playError}={}){
 const timers=new Map(),calls=[],engines=[];let seq=0;
 const video={currentTime:0,readyState:0,paused:true,pause(){this.paused=true},removeAttribute(){},load(){},play(){this.paused=false;return Promise.resolve()}};
 const elements={playerTitle:{},playerSource:{},playerStatus:{},retryPlayer:{},playerLine:{},playerLineControl:{},playerDialog:{open:false,showModal(){this.open=true}},previewVideo:video};
 class Hls{
  static Events={MANIFEST_PARSED:'parsed',ERROR:'error'};static ErrorTypes={NETWORK_ERROR:'network',MEDIA_ERROR:'media'};static isSupported(){return true}
  constructor(){this.events={};engines.push(this)}on(k,fn){this.events[k]=fn}loadSource(){}attachMedia(){}destroy(){this.destroyed=true}
 }
 const ctx=vm.createContext({state:{channels:[{name:'C',label:'C',lines:[{url:'http://fixture.invalid/live',enabled:true,platform:'fixture',speed_display:'200 KB/s'}]}]},$:id=>elements[id],esc:x=>String(x),sourceNames:()=>'',closePopup(){},loadHls:async()=>Hls,
  setTimeout(fn,ms){timers.set(++seq,{fn,ms});return seq},clearTimeout(id){timers.delete(id)},api:async(path,data)=>{calls.push([path,data]);if(path==='play'){if(playError)throw playError;return {kind:'hls',session:'s'+calls.length,url:'/media'}}if(path.startsWith('play-status'))return status;return {};}});
 vm.runInContext(src.slice(src.indexOf('const displayOrders='),src.indexOf('function filtered(')),ctx);
 vm.runInContext('var playerVersion=0,playerRoute=null,playerSession=null,playerEngine=null,playerRetryTimer=null;'+src.slice(src.indexOf('function releasePlayer'),src.indexOf("$('closePlayer').onclick")),ctx);
 await ctx.openPlayer('C','http://fixture.invalid/live');
 const error=async(data={})=>engines.at(-1).events.error(null,{type:'network',fatal:false,response:{code:502},frag:{},...data});
 const fire=async()=>{const [id,t]=timers.entries().next().value;timers.delete(id);await t.fn();await new Promise(r=>setImmediate(r));};
 return {ctx,timers,calls,engines,elements,video,error,fire};
}
(async()=>{
 let f=await fixture({status:{error:'HTTP 403',httpStatus:403,stage:'media',retryable:true}});
 await f.error();assert(!f.engines[0].destroyed);assert.equal([...f.timers.values()][0].ms,3000);
 f.video.currentTime=2;f.video.readyState=4;f.video.paused=false;await f.fire();assert(!f.engines[0].destroyed);assert.equal(f.calls.filter(x=>x[0]==='play').length,1);
 f=await fixture({status:{error:'CDN HTTP 403 冷却',httpStatus:403,retryAfter:30,retryable:true,stage:'entry'}});
 await f.error();assert(f.engines[0].destroyed);assert.equal([...f.timers.values()][0].ms,30000);
 await f.fire();await f.error({fatal:true});assert.equal([...f.timers.values()][0].ms,30000);
 await f.fire();await f.error({fatal:true});assert.equal(f.timers.size,0);assert.equal(f.calls.filter(x=>x[0]==='play').length,3);assert.equal(f.elements.retryPlayer.hidden,false);
 f=await fixture({status:{error:'HTTP 401',httpStatus:401,retryAfter:30,retryable:false,stage:'entry'}});await f.error({fatal:true});assert.equal(f.timers.size,0);
 f=await fixture({status:{error:'HTTP 403',httpStatus:403,retryable:false,stage:'entry'}});await f.error({fatal:true});assert.equal(f.timers.size,0);
 f=await fixture({status:{error:'HTTP 429',httpStatus:429,retryAfter:120,retryable:true,stage:'entry'}});await f.error();assert.equal(f.timers.size,0);
 f=await fixture({status:{error:'HTTP 503',httpStatus:503,retryable:true,stage:'media'}});await f.error();await f.fire();assert(f.engines[0].destroyed);assert.equal([...f.timers.values()][0].ms,8000);
 const playCount=f.calls.filter(x=>x[0]==='play').length;f.ctx.releasePlayer();assert.equal(f.timers.size,0);assert.equal(f.calls.filter(x=>x[0]==='play').length,playCount);
 const apiCtx=vm.createContext({fetch:async()=>({ok:false,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify({error:'上游暂时冷却',httpStatus:403,retryAfter:30,retryable:true,stage:'entry'})})});
 vm.runInContext(src.slice(src.indexOf('async function api('),src.indexOf('function safely(')),apiCtx);
 try{await apiCtx.api('play',{});assert.fail('must throw')}catch(e){assert.equal(e.message,'上游暂时冷却');assert.equal(e.upstream.retryAfter,30);assert.equal(e.upstream.httpStatus,403)}
 console.log('PASS: nonfatal recovery, progress cancels restart, max two fresh-entry retries, cooldown, auth/long-delay stop, close cancellation and structured API errors.');
})().catch(e=>{console.error(e);process.exit(1)});
