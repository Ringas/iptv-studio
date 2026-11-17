const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('ui.js','utf8');
const code=source.slice(source.indexOf("$('playerDialog').addEventListener('close',()=>"),source.indexOf("$('playerDialog').addEventListener('cancel'"));
let close,focused=null,releases=0,buttons=[];
const ctx=vm.createContext({$:()=>({addEventListener:(event,fn)=>close=fn}),releasePlayer:()=>releases++,document:{querySelectorAll:()=>buttons}});
function trigger(connected=true){return {isConnected:connected,disabled:false,dataset:{owner:'fixture',play:'fixture-url'},focus(options){focused=this;assert.equal(options.preventScroll,true)}}}
vm.runInContext(code,ctx);
const original=trigger();ctx.playerReturnFocus=original;close();assert.equal(focused,original);assert.equal(ctx.playerReturnFocus,null);
const stale=trigger(false),replacement=trigger();buttons=[replacement];focused=null;ctx.playerReturnFocus=stale;close();assert.equal(focused,replacement);
buttons=[];focused=null;ctx.playerReturnFocus=trigger(false);close();assert.equal(focused,null);
assert.equal(releases,3);
console.log('PASS: close restores original/re-rendered playback trigger without scrolling; removed trigger safely ignored.');
