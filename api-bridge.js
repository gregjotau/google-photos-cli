(async()=>{
  if(location.hostname!=='photos.google.com')throw Error('Open Google Photos first');
  if(window.avifBridgeV3)throw Error('Bridge already running');
  const base='http://127.0.0.1:8765',key='__BRIDGE_KEY__';
  // Bound both response headers and body consumption. Abort alone is not a
  // settlement guarantee, so the deadline also releases the worker's slot.
  async function request(url,init,deadline,consume){
    const remaining=deadline-Date.now();
    if(!Number.isFinite(remaining)||remaining<=0)throw Error('Request deadline expired');
    const controller=new AbortController();let timer;
    const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{
      controller.abort();reject(Error('Request deadline exceeded; reconcile writes before retrying'));
    },remaining)});
    try{
      return await Promise.race([(async()=>consume(await fetch(url,{...init,signal:controller.signal})))(),timeout]);
    }finally{clearTimeout(timer)}
  }
  const local=(path,init)=>request(base+path+'?key='+key,init,Date.now()+30000,async r=>{
    if(!r.ok)throw Error('Local broker HTTP '+r.status);
    return r.json();
  });
  const post=value=>local('/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
  const account=window.WIZ_global_data.oPEP7c;
  if(typeof account!=='string'||!account)throw Error('Sign in to the intended Photos account first');
  const authuser=Number((location.pathname.match(/\/u\/(\d+)/)||[])[1]||0);
  const allowedRPC=new Set(['lcxiM','EzkLib','VrseUb','fDcn4b','EWgK9e','Z5xsfc','snAcKc','swbisb','EzwWhf','zy0IHe','mdpdU','XwAOJf','E1Cajb','laUYf','DaSgWe','AQNOFd','Ftfh0','w7TP3c','pLFTfd']);
  const readOnlyRPC=new Set(['lcxiM','EzkLib','VrseUb','fDcn4b','EWgK9e','Z5xsfc','snAcKc','swbisb','EzwWhf','zy0IHe','pLFTfd']);
  function checkTask(task){
    if(!Number.isFinite(task.expires_at)||Date.now()>=task.expires_at*1000)throw Error('Expired task; no further Google request was sent');
    if(window.WIZ_global_data.oPEP7c!==account||task.account&&task.account!==account)throw Error('Account changed');
  }
  function google(task,url,init,consume){
    checkTask(task);
    return request(url,init,task.expires_at*1000,consume);
  }
  async function retryDelay(task,milliseconds){
    checkTask(task);
    if(Date.now()+milliseconds>=task.expires_at*1000)throw Error('Retry would exceed task deadline; no further Google request was sent');
    await new Promise(resolve=>setTimeout(resolve,milliseconds));
    checkTask(task);
  }
  async function rpc(calls,task){
    for(const [id] of calls)if(!allowedRPC.has(id))throw Error('RPC is outside migration scope');
    const g=window.WIZ_global_data;
    const params=new URLSearchParams({rpcids:[...new Set(calls.map(x=>x[0]))].join(','),'source-path':location.pathname,'f.sid':g.FdrFJe,bl:g.cfb2h,rt:'c'});
    const body=new URLSearchParams({'f.req':JSON.stringify([calls.map((x,i)=>[x[0],JSON.stringify(x[1]),null,'q'+i])]),at:g.SNlM0e});
    let r;
    for(let attempt=0;attempt<4;attempt++){
      r=await google(task,g.Im6cmf+'/data/batchexecute?'+params,{method:'POST',body,credentials:'include'},async response=>{
        const retry=!response.ok&&calls.every(([id])=>readOnlyRPC.has(id))&&[429,502,503,504].includes(response.status)&&attempt<3;
        if(retry){
          const retryAfter=Number(response.headers.get('Retry-After'));
          const delay=Number.isFinite(retryAfter)&&retryAfter>0?Math.min(retryAfter,60):2**attempt+Math.random();
          await response.body?.cancel();return {retry:true,delay};
        }
        return {ok:response.ok,status:response.status,text:await response.text()};
      });
      if(!r.retry)break;
      await retryDelay(task,r.delay*1000);
    }
    const text=r.text;if(!r.ok)throw Error('RPC HTTP '+r.status);
    const answers=Array(calls.length).fill(undefined);
    for(const line of text.split('\n')){
      let chunk;try{chunk=JSON.parse(line)}catch{continue}
      if(!Array.isArray(chunk))continue;
      for(const row of chunk)if(row[0]==='wrb.fr'){
        const n=Number(String(row[6]).slice(1));
        if(Number.isInteger(n)&&n>=0&&n<answers.length)answers[n]=row[2]===null?null:JSON.parse(row[2]);
      }
    }
    if(answers.some(x=>x===undefined))throw Error('Incomplete RPC response');
    return answers;
  }
  function mediaURL(url,method){
    const u=new URL(url,location.origin),h=u.hostname;
    if(u.protocol!=='https:'||!(h==='photos.google.com'||h==='photos.fife.usercontent.google.com'||/^lh[0-9]+\.google\.com$/.test(h)||h.endsWith('.googleusercontent.com')))throw Error('Unapproved media host');
    if(method!=='GET'&&!(method==='POST'&&h==='photos.google.com'&&u.pathname==='/_/upload/uploadmedia/interactive'))throw Error('Unapproved media write');
    return u;
  }
  async function handle(task){
    try{
      checkTask(task);
      let value;
      if(task.op==='hello')value={account,authuser,ready:true};
      else if(task.op==='rpc'){
        const batched=Array.isArray(task.rpc),calls=batched?task.rpc:[[task.rpc,task.data]];
        const result=await rpc(calls,task);value=batched?result:result[0];
      }else if(task.op==='fetch'){
        const method=task.method||'GET',url=mediaURL(task.url,method);
        value=await google(task,url,{method,headers:task.headers||{},body:task.body64?Uint8Array.from(atob(task.body64),c=>c.charCodeAt(0)):(task.body||undefined),credentials:'include'},async response=>{
          const bytes=new Uint8Array(await response.arrayBuffer());let binary='';
          for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
          return {status:response.status,headers:Object.fromEntries(response.headers),body64:btoa(binary)};
        });
      }else throw Error('Unknown bridge operation');
      checkTask(task);
      await post({id:task.id,ok:true,value});
    }catch(e){await post({id:task.id,ok:false,error:String(e)})}
  }
  const pending=new Set();window.avifBridgeV3=true;
  console.log('AVIF API bridge connected; authentication stays in this tab.');
  while(window.avifBridgeV3){
    try{
      if(pending.size>=6)await Promise.race(pending);
      const task=await local('/next');if(!task)continue;
      const job=handle(task).catch(e=>console.error('AVIF result delivery failed; reconcile before retrying writes:',String(e)));pending.add(job);job.finally(()=>pending.delete(job));
    }catch(e){console.error('AVIF bridge connection:',String(e));await new Promise(r=>setTimeout(r,3000))}
  }
})();
