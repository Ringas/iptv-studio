// Exercise real media reader + controlled decoder on loopback fixtures.
import {createServer} from 'node:http';
import {mkdtempSync,writeFileSync,chmodSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import assert from 'node:assert/strict';
import {playbackProbe} from './playback.mjs';
const dir=mkdtempSync(join(tmpdir(),'coarse-fixture-'));
const fake=join(dir,'decoder');
writeFileSync(fake,'#!/bin/sh\nh=$(cat)\nfor n in 0 1 2 3 4 5; do printf "[Parsed_showinfo_0] n: %s pts: %s pts_time:%s pos: 0 fmt:yuv420p s:1280x%s i:P\\n" "$n" "$n" "$n" "$h" >&2; done\n');chmodSync(fake,0o755);
const server=createServer((req,res)=>{res.end(req.url.slice(1));});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base=`http://127.0.0.1:${server.address().port}`;
const oldBin=process.env.FFMPEG_BIN,oldMin=process.env.IPTV_PROBE_MIN_HEIGHT;
process.env.FFMPEG_BIN=fake;process.env.IPTV_PROBE_MIN_HEIGHT='720';
try{
 const variant=(height,rate,actual)=>({url:base+'/'+actual,rate,resolution:height?{height}:null});
 for(const pipeline of [false,true])for(const mode of ['low','upgrade','unknown']){
  let seq=0;
  const lists={low:[variant(480,9,480)],upgrade:[variant(1080,9,480),variant(720,8,720)],unknown:[variant(null,9,480),variant(null,8,480),variant(null,7,480),variant(null,6,720)]};
  async function manifest(url){seq++;if(url===base+'/entry')return {variants:lists[mode],segments:[]};return {url,variants:[],target:.1,ended:false,segments:[0,1].map(i=>({url,seq:seq+i,duration:.1,encryption:'NONE',gap:false}))};}
  const r=await playbackProbe(base+'/entry',!pipeline,{},manifest,{started:performance.now()/1000,pipeline});
  if(mode==='low')assert.equal(r.coarse_rejected,true,JSON.stringify(r));
  if(mode==='upgrade'){assert.equal(r.resolution.height,720);assert.ok(!r.coarse_rejected);}
  if(mode==='unknown'){assert.equal(r.candidate_possible,true);assert.ok(!r.coarse_rejected);}
 }
 console.log('PASS: real coarse flow, decoded dimensions, alternative variant, attempt budget safety');
}finally{server.closeAllConnections();await new Promise(r=>server.close(r));rmSync(dir,{recursive:true,force:true});if(oldBin===undefined)delete process.env.FFMPEG_BIN;else process.env.FFMPEG_BIN=oldBin;if(oldMin===undefined)delete process.env.IPTV_PROBE_MIN_HEIGHT;else process.env.IPTV_PROBE_MIN_HEIGHT=oldMin;}
