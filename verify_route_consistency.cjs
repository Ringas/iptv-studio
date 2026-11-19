// Exercise the actual UI selection/rendering/player code against backend snapshots.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync('ui.js','utf8'),snapshots=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
(async()=>{
 for(const snapshot of snapshots){
  const elements={playerTitle:{},playerSource:{},playerStatus:{},retryPlayer:{},playerLine:{},playerLineControl:{},playerDialog:{open:false,showModal(){this.open=true}},previewVideo:{pause(){},removeAttribute(){},load(){}}};
  const calls=[],ctx=vm.createContext({clearTimeout(){},setTimeout(){throw Error('unexpected retry')},state:snapshot.state,$:id=>elements[id],esc:x=>String(x??''),sourceNames:()=>'',closePopup(){},metricText:x=>x,speedResult:(name,r)=>`speed:${r.url}`,api:async(path,payload)=>{calls.push([path,payload]);throw Error('fixture media not requested');},releasePlayer:()=>ctx.playerVersion++});
  vm.runInContext(src.slice(src.indexOf('const healthNames='),src.indexOf('function matchingRows')),ctx);
  vm.runInContext(src.slice(src.indexOf('const displayOrders='),src.indexOf('function filtered(')),ctx);
  vm.runInContext(src.slice(src.indexOf('function defaultPlayRoute'),src.indexOf('let playerVersion=')),ctx);
  vm.runInContext(src.slice(src.indexOf('function cardDetection'),src.indexOf('function channelCard')),ctx);
  vm.runInContext('var playerVersion=0,playerRoute=null,playerSession=null,playerEngine=null,playerRetryTimer=null;'+src.slice(src.indexOf('async function openPlayer'),src.indexOf("$('closePlayer').onclick")),ctx);
  for(const c of snapshot.state.channels){
   const enabled=c.lines.filter(r=>r.enabled),chosen=ctx.defaultPlayRoute(c),ex=snapshot.exported.find(x=>x.name===c.name);
   assert.equal(chosen?.url,(enabled[0]||c.lines[0])?.url);
   assert.equal(ex?.lines[0]?.url,enabled[0]?.url);
   const html=ctx.cardDetection(c);
   if(chosen?.enabled&&chosen.assessment==='保留'&&Number.isFinite(chosen.test.speed))assert(html.includes(`speed:${chosen.url}`));else assert(html.includes('暂无可用'));
   if(chosen){
    assert(ctx.playButton(c,chosen).includes(`data-play="${chosen.url}"`));
    await ctx.openPlayer(c.name,chosen.url);
    assert.equal(calls.at(-1)[1].url,chosen.url);
    assert.equal(elements.playerLine.value,chosen.url);
   }
  }
 }
 console.log(`PASS: ${snapshots.length} snapshots: actual card metric, play button, player request, list and export selection.`);
})().catch(e=>{console.error(e);process.exit(1)});
