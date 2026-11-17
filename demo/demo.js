// Read-only synthetic demo: never contact a subscription or media server.
const demoReady=fetch('./demo-state.json').then(r=>r.json());
window.demoAPI=async(path)=>{
 const s=await demoReady,kind=path.split('?')[0];
 if(kind==='state')return structuredClone(s);
 if(kind==='status')return {job:{running:false,message:'模拟演示 · 无后台任务',done:0,total:0,revision:0},logs:[]};
 if(kind==='guide'||kind==='guide-options'||kind==='logs')return [];
 throw Error('静态演示仅供浏览：可搜索、筛选、切换视图和查看频道详情。播放、检测与保存操作请在自托管服务中使用。');
};
