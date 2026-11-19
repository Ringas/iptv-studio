const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync('ui.js','utf8'),ctx=vm.createContext({Map,JSON,state:{channels:[]},$:()=>({value:''})});
vm.runInContext(src.slice(src.indexOf('const displayOrders='),src.indexOf('function filtered(')),ctx);
vm.runInContext(src.slice(src.indexOf('function routeRows('),src.indexOf('const healthNames=')),ctx);
vm.runInContext(src.slice(src.indexOf('function defaultPlayRoute('),src.indexOf('let playerVersion=')),ctx);
const a={url:'a',enabled:true},b={url:'b',enabled:true},c={name:'频道',label:'频道',lines:[a,b]};
const urls=rows=>Array.from(rows,x=>x.r.url);
assert.deepEqual(urls(ctx.displayRoutes(c)),['a','b']);
c.lines=[b,a];a.enabled=false;
assert.deepEqual(urls(ctx.displayRoutes(c)),['a','b'],'启停后界面自动重排');
assert.equal(ctx.defaultPlayRoute(c),b,'延后界面排序改变首选播放');
assert.deepEqual(c.lines,[b,a],'展示函数修改了标准顺序');
assert.deepEqual(Array.from(ctx.routeRows([c]),x=>[x.r.url,x.i]),[['a',1],['b',0]],'可视顺序与真实线路编号混淆');
for(const scope of ['channels','sources','hidden','groups']){
 const one={id:1},two={id:2},three={id:3};
 assert.deepEqual(Array.from(ctx.displayOrder([one,two],scope,x=>x.id),x=>x.id),[1,2]);
 assert.deepEqual(Array.from(ctx.displayOrder([two,one,three],scope,x=>x.id),x=>x.id),[1,2,3]);
 assert.deepEqual(Array.from(ctx.displayOrder([three,two],scope,x=>x.id),x=>x.id),[2,3],'移除项目未消失或新项目顺序不稳定');
}
assert.deepEqual(urls(ctx.displayRoutes(c,'detail')),['b','a']);
c.lines=[a,b];assert.deepEqual(urls(ctx.displayRoutes(c,'detail')),['b','a'],'详情打开期间自动重排');
ctx.resetDisplayOrder();assert.deepEqual(urls(ctx.displayRoutes(c)),['a','b'],'重进页面未按最新排序');
assert.deepEqual(urls(ctx.displayRoutes(c,'detail')),['a','b']);
console.log('通过：全局界面排序快照、新增追加、删除移除、重进重置、详情与列表保持顺序，首选播放和标准数据顺序不变。');
