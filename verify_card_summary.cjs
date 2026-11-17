const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const js=fs.readFileSync('ui.js','utf8'),ctx=vm.createContext({});
vm.runInContext(js.slice(js.indexOf('const healthNames='),js.indexOf('function matchingRows')),ctx);
vm.runInContext(js.slice(js.indexOf('function defaultPlayRoute'),js.indexOf('function playButton')),ctx);
Object.assign(ctx,{esc:x=>String(x),metricText:x=>x,speedResult:(name,r)=>`speed:${r.url}`});
vm.runInContext(js.slice(js.indexOf('function cardDetection'),js.indexOf('function channelCard')),ctx);
const line=(assessment,enabled=true,url=assessment,speed=200)=>({assessment,enabled,url,test:{speed}});
const card=lines=>ctx.cardDetection({name:'fixture',lines});
for(const lines of [[],[line('未测')],[line('待复测'),line('未能验证')],[line('速率未达标'),line('未测')],[line('保留',false)],[line('保留',true,'unknown',null)]]){
 assert(card(lines).includes('首选速率'));
 assert(card(lines).includes('暂无可用'));
 assert(!card(lines).includes('speed:'));
}
assert(card([line('保留',false,'off'),line('保留',true,'on')]).includes('speed:on'));
assert(!card([line('保留')]).includes('线路1'));
assert(!card([line('保留'),line('未测')]).includes('条线路'));
console.log('PASS: preferred speed, unavailable/unknown/disabled states, default playback consistency and no redundant labels.');
