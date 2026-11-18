import {acquireGateway,reportGatewayLimit} from './gateway-lease.mjs';
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
   acquireGateway(url);
   const response=await fetch(url,{...options,headers,redirect:'manual'});
   storeCookies(response,url);
   reportGatewayLimit(response,url);
   if(![301,302,303,307,308].includes(response.status)||!response.headers.has('location'))return response;
   await response.body?.cancel();
   url=new URL(response.headers.get('location'),url);
 }
 throw Error('媒体重定向次数过多');
}

// Decode only known error fields / explicit HTTP and cooldown phrases, never arbitrary numbers.
export function upstreamFailure(body='',headers={},stage='entry',httpStatus){
 const get=name=>typeof headers.get==='function'?headers.get(name):headers[name];
 const delay=value=>{
  if(value==null||value==='')return 0;
  const n=Number(value);if(Number.isFinite(n)&&n>=0)return Math.ceil(n);
  const at=Date.parse(String(value));return Number.isFinite(at)?Math.max(0,Math.ceil((at-Date.now())/1000)):0;
 };
 const valid=value=>/^(?:[45]\d{2}|607)$/.test(String(value??''))?Number(value):undefined;
 let parsed;try{parsed=JSON.parse(String(body).slice(0,8192));}catch{}
 const object=parsed&&typeof parsed==='object'&&!Array.isArray(parsed)?parsed:{};
 const detail=object.error&&typeof object.error==='object'?object.error:{};
 const original=[object.message,object.msg,object.error,object.desc,object.data,detail.message].find(v=>typeof v==='string')||(typeof parsed==='string'?parsed:parsed&&typeof parsed==='object'?'':String(body));
 const message=original.replace(/https?:\/\/[^\s"<>]+/gi,'[地址]');
 const explicit=valid(object.httpStatus??object.statusCode??object.code??detail.code);
 const codes=Array.from(message.matchAll(/\bHTTP\s*([45]\d{2}|607)\b/gi),m=>Number(m[1]));
 const code=valid(httpStatus)||explicit||(codes.includes(429)?429:codes.includes(607)?607:codes[0]);
 const hints=[...message.matchAll(/(?:暂停|冷却|等待|重试|停止探测)[^\d\n]{0,12}(\d+(?:\.\d+)?)\s*秒/g),...message.matchAll(/(\d+(?:\.\d+)?)\s*秒(?:内)?(?:暂停|停止|后重试|后再试|后恢复)/g)].map(m=>Math.ceil(Number(m[1]))+(code===429?1:0)).filter(Number.isFinite);
 const retryAfter=Math.max(delay(get('retry-after')),delay(object.retryAfter??object.retry_after??detail.retryAfter),0,...hints);
 const policy=/network policy|policy blocked|blocked by policy|网络策略|策略拦截/i.test(message);
 if(!code&&!retryAfter&&!policy)return null;
 const clean=String(message).replace(/https?:\/\/[^\s"<>]+/gi,'[地址]').replace(/\b(token|sign|auth|key|password)\s*[=:]\s*[^&\s"<>]+/gi,'$1=[已隐藏]').replace(/[A-Za-z0-9%_=-]{28,}/g,'[标识]').replace(/\s+/g,' ').trim().slice(0,300);
 return {httpStatus:code,retryAfter:retryAfter||undefined,upstreamCooldown:retryAfter>0,
  retryable:code!==401&&(retryAfter>0||policy||[408,425,429,500,502,503,504,607].includes(code)||(code===403&&stage==='media')),
  stage,note:clean||`上游返回 HTTP ${code}`};
}
export function upstreamFailureError(info){
 return Object.assign(new Error(info.note),info,{unverified:Boolean(info.upstreamCooldown||!info.httpStatus||[401,403,429,607].includes(info.httpStatus)),status:'未能验证'});
}

export async function responseFailure(response,stage='media'){
 const reader=response.body?.getReader(),chunks=[];let bytes=0;
 try{if(reader)while(bytes<8192){const {value,done}=await reader.read();if(done)break;const part=value.subarray(0,8192-bytes);chunks.push(part);bytes+=part.length;}}
 finally{await reader?.cancel().catch(()=>{});}
 const info=upstreamFailure(Buffer.concat(chunks).toString(),response.headers,stage,response.ok?undefined:response.status);
 if(info&&response.status===429)info.retryAfter=Math.max(6,info.retryAfter||0);
 if(info&&info.httpStatus&&!/\bHTTP\s*\d{3}\b/i.test(info.note))info.note=`上游返回 HTTP ${info.httpStatus}：${info.note}`;
 return info;
}
