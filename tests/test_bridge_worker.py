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
 if(url.includes('/next?')){if(count++===0)return {json:async()=>task};window.avifBridgeV3=false;return {json:async()=>null};}
 if(url.includes('/result?')){posted=JSON.parse(init.body);resolve();return {};}
 calls++;
 if(input.throttle&&calls===1)return {ok:false,status:429,headers:{get:()=>null},body:{cancel:async()=>{}},text:async()=>''};
 return {ok:true,status:200,text:async()=>JSON.stringify([['wrb.fr',input.rpc,JSON.stringify(['answer']),null,null,null,'q0']])};
};
vm.runInNewContext(fs.readFileSync('api-bridge.js','utf8'),{window,location:{hostname:'photos.google.com',pathname:'/',origin:'https://photos.google.com'},URL,URLSearchParams,fetch,console:{log(){},error(){}},setTimeout:(f)=>{queueMicrotask(f)},Date,Uint8Array,atob,btoa});
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
