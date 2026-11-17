const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const js=fs.readFileSync('ui.js','utf8');
const source=js.slice(js.indexOf('function setupSubscriptionShare('),js.indexOf('setupSubscriptionShare();'));
async function check(href,clipboardMode,fallback){
 let copied,notice,removed=0,created=0;
 const button={dataset:{path:'./live.m3u'},focus(){}},elements={copySubscription:button};
 const ctx={URL,location:{href:href.replace(/live\.m3u$/,'')},$:id=>elements[id],safely:fn=>fn,notice:text=>{notice=text;},navigator:clipboardMode==='missing'?{}:{clipboard:{writeText:async text=>{if(clipboardMode==='denied')throw Error();copied=text;}}},document:{body:{append(){}},createElement(){created++;return {style:{},select(){copied=this.value;},setSelectionRange(){},remove(){removed++;}};},execCommand:()=>fallback}};
 vm.createContext(ctx);vm.runInContext(source,ctx);ctx.setupSubscriptionShare();
 assert.equal(button.title,'复制 M3U 订阅地址：'+href);await button.onclick();
 assert.equal(copied,href);assert.equal(created,clipboardMode==='ok'?0:1);assert.equal(removed,created);
 assert.equal(notice,clipboardMode==='ok'||fallback?'订阅地址已复制':'复制未完成，可在设置与订阅中长按 M3U 清单复制链接');
}
(async()=>{
 await check('https://example.invalid/live.m3u','ok',false);
 await check('http://192.0.2.10:18789/live.m3u','missing',true);
 await check('https://example.invalid/demo/live.m3u','denied',true);
 await check('http://example.invalid/live.m3u','missing',false);
 const html=fs.readFileSync('index.html','utf8');
 assert(!html.includes('<a id="subscriptionAddress"'));
 assert(html.includes('<span>M3U 订阅</span>')&&html.includes('aria-label="复制 M3U 订阅地址"'));
 console.log('通过：地址不跳转、展示与复制地址一致，HTTPS/局域网 HTTP、子路径、剪贴板拒绝及失败提示');
})().catch(e=>{console.error(e);process.exitCode=1;});
