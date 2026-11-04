// One cookie jar per probe process, shared by manifests, redirects and media.
const cookies=new Map();
const matches=(host,domain)=>host===domain||host.endsWith('.'+domain);
function storeCookies(response,url){
 for(const text of response.headers.getSetCookie()){
   const [pair,...attributes]=text.split(';'),equal=pair.indexOf('=');
   if(equal<=0)continue;
   const name=pair.slice(0,equal).trim(),value=pair.slice(equal+1).trim();
   if(!/^[!#$%&'*+.^_`|~0-9a-z-]+$/i.test(name))continue;
   let domain=url.hostname,path=url.pathname.slice(0,url.pathname.lastIndexOf('/'))||'/',hostOnly=true,secure=false,expires=Infinity,maxAge;
   for(const attribute of attributes){
     const index=attribute.indexOf('='),key=(index<0?attribute:attribute.slice(0,index)).trim().toLowerCase(),v=index<0?'':attribute.slice(index+1).trim();
     if(key==='domain'){domain=v.replace(/^\./,'').toLowerCase();hostOnly=false;}
     if(key==='path'&&v.startsWith('/'))path=v;
     if(key==='secure')secure=true;
     if(key==='expires'&&Number.isFinite(Date.parse(v)))expires=Date.parse(v);
     if(key==='max-age'&&/^-?\d+$/.test(v))maxAge=Number(v);
   }
   if(!matches(url.hostname,domain)||(!hostOnly&&!domain.includes('.')))continue;
   if(maxAge!==undefined)expires=Date.now()+maxAge*1000;
   const key=JSON.stringify([name,domain,path]);
   cookies.delete(key);
   if(expires>Date.now())cookies.set(key,{name,value,domain,path,hostOnly,secure,expires});
   while(cookies.size>256)cookies.delete(cookies.keys().next().value);
 }
}
export async function mediaFetch(address,options={}){
 let url=new URL(address);
 for(let redirects=0;redirects<=8;redirects++){
   if(!['http:','https:'].includes(url.protocol))throw Error('媒体地址仅支持 HTTP(S)');
   const headers=new Headers(options.headers);
   const selected=[];
   for(const [key,cookie] of cookies){
     if(cookie.expires<=Date.now()){cookies.delete(key);continue;}
     const host=cookie.hostOnly?url.hostname===cookie.domain:matches(url.hostname,cookie.domain);
     const path=url.pathname===cookie.path||url.pathname.startsWith(cookie.path.endsWith('/')?cookie.path:cookie.path+'/');
     if(host&&path&&(!cookie.secure||url.protocol==='https:'))selected.push(cookie);
   }
   if(selected.length)headers.set('Cookie',selected.sort((a,b)=>b.path.length-a.path.length).map(c=>c.name+'='+c.value).join('; '));
   const response=await fetch(url,{...options,headers,redirect:'manual'});
   storeCookies(response,url);
   if(![301,302,303,307,308].includes(response.status)||!response.headers.has('location'))return response;
   await response.body?.cancel();
   url=new URL(response.headers.get('location'),url);
 }
 throw Error('媒体重定向次数过多');
}
