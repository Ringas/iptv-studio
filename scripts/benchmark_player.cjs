// Isolated Chromium first-frame benchmark; no production data or browser profile.
const fs=require('node:fs');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
class CDP{
 constructor(ws){this.ws=ws;this.seq=0;this.pending=new Map;this.events=[];ws.onmessage=e=>{const v=JSON.parse(e.data);if(v.id){const p=this.pending.get(v.id);this.pending.delete(v.id);v.error?p?.reject(Error(JSON.stringify(v.error))):p?.resolve(v.result);}else this.events.push(v);};}
 static async open(url){const ws=new WebSocket(url);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});return new CDP(ws);}
 send(method,params={}){return new Promise((resolve,reject)=>{const id=++this.seq;this.pending.set(id,{resolve,reject});this.ws.send(JSON.stringify({id,method,params}));});}
 async eval(expression){const r=await this.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
 close(){this.ws.close();}
}
(async()=>{
 const [debug,out]=process.argv.slice(2),info=await(await fetch(debug+'/json/version')).json();
 const browser=await CDP.open(info.webSocketDebuggerUrl),rows=[];
 try{
  for(let round=0;round<16;round++)for(const label of(round%2?['candidate','baseline']:['baseline','candidate'])){
   const origin='http://127.0.0.1:'+(label==='baseline'?18881:18882);
   const {browserContextId}=await browser.send('Target.createBrowserContext');
   const {targetId}=await browser.send('Target.createTarget',{url:'about:blank',browserContextId});
   const targets=await(await fetch(debug+'/json/list')).json();
   const page=await CDP.open(targets.find(t=>t.id===targetId).webSocketDebuggerUrl);
   try{
    await page.send('Network.enable');await page.send('Page.enable');await page.send('Runtime.enable');
    await page.send('Page.navigate',{url:origin});
    let ready=false;
    for(let i=0;i<150;i++){ready=await page.eval('!!document.querySelector("button[data-play]")');if(ready)break;await sleep(100);}
    if(!ready)throw Error('actual UI did not render play button');
    for(const phase of ['first','repeat']){
     page.events=[];
     const result=await page.eval(`new Promise(resolve=>{
      const video=document.getElementById('previewVideo'),start=performance.now();let playing=null,done=false;
      const finish=value=>{if(done)return;done=true;clearTimeout(timer);resolve({...value,playing_ms:playing});};
      const timer=setTimeout(()=>finish({error:'first frame timeout',status:document.getElementById('playerStatus').textContent}),30000);
      video.addEventListener('playing',()=>{playing=performance.now()-start;},{once:true});
      video.requestVideoFrameCallback((now,meta)=>finish({first_frame_ms:performance.now()-start,media_time:meta.mediaTime,width:video.videoWidth,height:video.videoHeight}));
      document.querySelector('button[data-play]').click();
     })`);
     const responses=page.events.filter(v=>v.method==='Network.responseReceived').map(v=>v.params.response);
     const api=responses.find(r=>new URL(r.url).pathname==='/api/play');
     const media=responses.filter(r=>new URL(r.url).pathname==='/api/play-media');
     const completed=page.events.filter(v=>v.method==='Network.loadingFinished');
     const firstMedia=media.find(r=>!r.mimeType.includes('mpegurl'));
     const reqs=page.events.filter(v=>v.method==='Network.requestWillBeSent');
     const clickStart=reqs.find(v=>new URL(v.params.request.url).pathname==='/api/play')?.params.timestamp;
     const mediaEvent=page.events.find(v=>v.method==='Network.responseReceived'&&v.params.response===firstMedia);
     const row={round,label,phase,...result,session_response_ms:api?.timing?.receiveHeadersEnd,first_media_headers_ms:mediaEvent&&clickStart?(mediaEvent.params.timestamp-clickStart)*1000:null,requests:responses.map(r=>({path:new URL(r.url).pathname,status:r.status,mime:r.mimeType,headers_ms:r.timing?.receiveHeadersEnd})),completed_requests:completed.length,first_media_request_ms:(()=>{const event=reqs.find(v=>v.params.request.url===firstMedia?.url);return event&&clickStart?(event.params.timestamp-clickStart)*1000:null;})()};
     rows.push(row);fs.writeFileSync(out,JSON.stringify({browser:info.Browser,rows},null,2));console.log(JSON.stringify({round,label,phase,...result}));
     await page.eval("document.getElementById('closePlayer').click()");await sleep(200);
     if(result.error)throw Error(JSON.stringify(row));
    }
   }finally{page.close();await browser.send('Target.disposeBrowserContext',{browserContextId});}
  }
 }finally{browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
