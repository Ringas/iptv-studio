const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('ui.js','utf8');const code=source.slice(source.indexOf('async function status(){'),source.indexOf('async function launch('));
const elements=new Map();const payload={job:{revision:1,running:false},log_id:0,logo_beauty:'light',logo_cache_revision:2,logo_optimization:{running:false}};const calls=[];
const ctx=vm.createContext({$:id=>{if(!elements.has(id))elements.set(id,{open:false});return elements.get(id)},api:async()=>payload,document:{querySelectorAll:()=>[]},revision:1,wasRunning:false,state:{settings:{logo_beauty:'light'},logo_cache_revision:1},picked:new Set(),updateProbeAction(){},syncSourceActions(){},paintProgress(){},reload:async live=>{calls.push(live);ctx.state.logo_cache_revision=payload.logo_cache_revision;ctx.state.settings.logo_beauty=payload.logo_beauty;}});
vm.runInContext(code,ctx);
(async()=>{
 await ctx.status();assert.deepEqual(calls,[true],'changed content version did not refresh UI');
 await ctx.status();assert.equal(calls.length,1,'unchanged version repeatedly rebuilt UI');
 payload.logo_beauty='dark';await ctx.status();assert.equal(calls.length,2,'mode switch was not refreshed');
 console.log('通过：同地址内容版本更新自动刷新前台，版本未变不刷新，模式切换保持同步。');
})().catch(error=>{console.error(error);process.exitCode=1});
