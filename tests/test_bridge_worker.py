import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(
    shutil.which("node"), "Node is optional locally; CI runs browser-worker tests"
)
class BrowserWorker(unittest.TestCase):
    def run_worker(self, rpc, *, expired=False, throttle=False):
        program = r"""
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(process.argv[1]);let calls=0,posted;
const window={WIZ_global_data:{oPEP7c:'synthetic',Im6cmf:'https://photos.google.com/_/PhotosUi',SNlM0e:'fake'}};
const task={id:'test',op:'rpc',rpc:input.rpc,data:[],account:'synthetic',expires_at:Date.now()/1000+(input.expired?-60:180)};
let count=0,resolve;const done=new Promise(r=>resolve=r);
const fetch=async(url,init)=>{
 if(url.includes('/next?')){if(count++===0)return {ok:true,json:async()=>task};window.avifBridgeV3=false;return {ok:true,json:async()=>null};}
 if(url.includes('/result?')){posted=JSON.parse(init.body);resolve();return {ok:true,json:async()=>({})};}
 calls++;
 if(input.throttle&&calls===1)return {ok:false,status:429,headers:{get:()=> '0.001'},body:{cancel:async()=>{}},text:async()=>''};
 return {ok:true,status:200,text:async()=>JSON.stringify([['wrb.fr',input.rpc,JSON.stringify(['answer']),null,null,null,'q0']])};
};
vm.runInNewContext(fs.readFileSync('api-bridge.js','utf8'),{window,location:{hostname:'photos.google.com',pathname:'/',origin:'https://photos.google.com'},URL,URLSearchParams,fetch,console:{log(){},error(){}},setTimeout,clearTimeout,AbortController,Date,Uint8Array,atob,btoa});
Promise.race([done,new Promise((_,reject)=>setTimeout(()=>reject(Error('worker timed out')),2000))]).then(()=>{console.log(JSON.stringify({calls,posted}));process.exit(0)}).catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(
            [
                "node",
                "-e",
                program,
                json.dumps(dict(rpc=rpc, expired=expired, throttle=throttle)),
            ],
            cwd=Path(__file__).parents[1],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return json.loads(result.stdout)

    def test_expired_task_never_contacts_google(self):
        result = self.run_worker("mdpdU", expired=True)
        self.assertEqual(result["calls"], 0)
        self.assertFalse(result["posted"]["ok"])

    def test_read_only_throttle_is_retried(self):
        result = self.run_worker("fDcn4b", throttle=True)
        self.assertEqual(result["calls"], 2)
        self.assertTrue(result["posted"]["ok"])

    def test_mutation_is_never_automatically_retried(self):
        result = self.run_worker("mdpdU", throttle=True)
        self.assertEqual(result["calls"], 1)
        self.assertFalse(result["posted"]["ok"])

    def test_disallowed_rpc_never_contacts_google(self):
        result = self.run_worker("outside-scope")
        self.assertEqual(result["calls"], 0)
        self.assertFalse(result["posted"]["ok"])

    def run_deadline_worker(self, scenario):
        program = r"""
const fs=require('node:fs'),vm=require('node:vm');
const mode=process.argv[1],window={WIZ_global_data:{oPEP7c:'synthetic',Im6cmf:'https://photos.google.com/_/PhotosUi',SNlM0e:'fake'}};
let googleCalls=0,aborted=0,nextCalls=0,resultCalls=0,resolve;
const posted=[],done=new Promise(r=>resolve=r),tasks=[];
const task=(id,op,extra={})=>({id,op,account:'synthetic',expires_at:Date.now()/1000+0.08,...extra});
if(mode==='six-hung-fetches'||mode==='hung-bodies'){
 for(let i=0;i<6;i++)tasks.push(task('hung'+i,'fetch',{url:'https://lh3.google.com/synthetic'}));
 tasks.push(task('after','hello',{expires_at:Date.now()/1000+1}));
}else if(mode==='expired-retry')tasks.push(task('test','rpc',{rpc:'fDcn4b',data:[]}));
else if(mode==='expiry-during-retry')tasks.push(task('test','rpc',{rpc:'fDcn4b',data:[]}));
else if(mode==='changed-account-retry')tasks.push(task('test','rpc',{rpc:'fDcn4b',data:[]}));
else if(mode==='wrong-account')tasks.push(task('test','rpc',{rpc:'mdpdU',data:[],account:'foreign'}));
else if(mode==='hung-write')tasks.push(task('test','rpc',{rpc:'mdpdU',data:[]}));
else if(mode==='local-next'||mode==='local-result')tasks.push(task('test','hello',{expires_at:Date.now()/1000+1}));
const response=value=>({ok:true,json:async()=>value});
const forever=()=>new Promise(()=>{});
const fetch=async(url,init)=>{
 const u=String(url);
 if(u.includes('/next?')){
  if(mode==='local-next'&&nextCalls++===0){init.signal.addEventListener('abort',()=>aborted++);return forever();}
  if(tasks.length)return response(tasks.shift());
  window.avifBridgeV3=false;return response(null);
 }
 if(u.includes('/result?')){
  if(mode==='local-result'&&resultCalls++===0){init.signal.addEventListener('abort',()=>aborted++);return forever();}
  const value=JSON.parse(init.body);posted.push(value);
  if(mode==='six-hung-fetches'||mode==='hung-bodies'){if(posted.length===7)resolve();}else resolve();
  return response({});
 }
 googleCalls++;
 init.signal.addEventListener('abort',()=>aborted++);
 if(mode==='expired-retry'||mode==='expiry-during-retry'||mode==='changed-account-retry'){
  if(mode==='changed-account-retry')setTimeout(()=>window.WIZ_global_data.oPEP7c='foreign',5);
  return {ok:false,status:429,headers:{get:()=>mode==='expired-retry'?'60':'0.02'},body:{cancel:async()=>{}},text:async()=>''};
 }
 if(mode==='hung-bodies')return {ok:true,status:200,headers:new Map(),arrayBuffer:forever};
 return forever();
};
// Local transport deadlines stay 30s in production; accelerate only those
// exact timers and connection backoff for this offline hung-transport fixture.
const timer=(f,ms)=>setTimeout(f,ms>29000&&ms<=30000?20:ms===3000?1:mode==='expiry-during-retry'&&ms===20?100:ms);
vm.runInNewContext(fs.readFileSync('api-bridge.js','utf8'),{window,location:{hostname:'photos.google.com',pathname:'/',origin:'https://photos.google.com'},URL,URLSearchParams,fetch,console:{log(){},error(){}},setTimeout:timer,clearTimeout,AbortController,Date,Uint8Array,atob,btoa});
Promise.race([done,new Promise((_,reject)=>setTimeout(()=>reject(Error('worker timed out')),2000))]).then(()=>{console.log(JSON.stringify({googleCalls,aborted,posted}));process.exit(0)}).catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(["node", "-e", program, scenario], cwd=Path(__file__).parents[1],
                                capture_output=True, text=True, timeout=5, check=True)
        return json.loads(result.stdout)

    def test_six_hung_google_fetches_abort_and_release_all_worker_slots(self):
        result = self.run_deadline_worker('six-hung-fetches')
        self.assertEqual(result['googleCalls'], 6)
        self.assertEqual(result['aborted'], 6)
        errors = [r for r in result['posted'] if r['id'].startswith('hung')]
        self.assertEqual(len(errors), 6)
        self.assertTrue(all(not r['ok'] for r in errors))
        self.assertTrue(next(r for r in result['posted'] if r['id'] == 'after')['ok'])

    def test_hung_response_bodies_are_bounded_and_slots_recover(self):
        result = self.run_deadline_worker('hung-bodies')
        self.assertEqual(result['aborted'], 6)
        self.assertTrue(next(r for r in result['posted'] if r['id'] == 'after')['ok'])

    def test_read_only_retry_wait_never_outlives_task_or_changed_account(self):
        for scenario in ('expired-retry', 'expiry-during-retry', 'changed-account-retry'):
            with self.subTest(scenario=scenario):
                result = self.run_deadline_worker(scenario)
                self.assertEqual(result['googleCalls'], 1)
                self.assertFalse(result['posted'][0]['ok'])

    def test_hung_mutating_rpc_aborts_without_retry(self):
        result = self.run_deadline_worker('hung-write')
        self.assertEqual(result['googleCalls'], 1)
        self.assertEqual(result['aborted'], 1)
        self.assertFalse(result['posted'][0]['ok'])

    def test_account_gate_prevents_google_mutation(self):
        result = self.run_deadline_worker('wrong-account')
        self.assertEqual(result['googleCalls'], 0)
        self.assertFalse(result['posted'][0]['ok'])

    def test_hung_local_next_and_result_requests_are_bounded(self):
        for scenario in ('local-next', 'local-result'):
            with self.subTest(scenario=scenario):
                result = self.run_deadline_worker(scenario)
                self.assertEqual(result['googleCalls'], 0)
                self.assertEqual(result['aborted'], 1)
                self.assertTrue(result['posted'])
