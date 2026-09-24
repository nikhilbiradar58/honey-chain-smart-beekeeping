import json, os, sqlite3, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen, Request

ROOT=Path(__file__).resolve().parent
TEST_DB=ROOT/'honey_chain_test.db'
if TEST_DB.exists(): TEST_DB.unlink()
os.environ['OPENAI_API_KEY']='test-key'
os.environ['OPENAI_MODEL']='gpt-5.6-luna'
os.environ['HOST']='127.0.0.1'
os.environ['PORT']='5871'

import app
app.DB_PATH=TEST_DB
app.WEATHER_CACHE={'ts':time.time(),'data':[{'cluster':'Test','temperature':30,'humidity':50,'wind':2,'source':'mock'}],'error':None}

class MockOpenAI(BaseHTTPRequestHandler):
    def do_POST(self):
        n=int(self.headers.get('Content-Length','0')); p=json.loads(self.rfile.read(n)); assert p['model']=='gpt-5.6-luna'; out={'output':[{'content':[{'type':'output_text','text':'mock llm response'}]}]}
        b=json.dumps(out).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self,*a): pass
mock=ThreadingHTTPServer(('127.0.0.1',5872),MockOpenAI); threading.Thread(target=mock.serve_forever,daemon=True).start()
os.environ['OPENAI_BASE_URL']='http://127.0.0.1:5872/responses'
app.ensure_db()
server=ThreadingHTTPServer(('127.0.0.1',5871),app.Handler); threading.Thread(target=server.serve_forever,daemon=True).start(); time.sleep(.2)

def get(path):
    with urlopen('http://127.0.0.1:5871'+path,timeout=5) as r: return r.status,r.read()
def post(path,obj):
    req=Request('http://127.0.0.1:5871'+path,data=json.dumps(obj).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urlopen(req,timeout=5) as r:return r.status,r.read()

s,b=get('/api/dashboard'); j=json.loads(b); assert s==200 and j['stats']['hives']==8 and j['stats']['clusters']==5 and j['stats']['purity']>0
s,b=post('/api/telemetry',{'id':'REAL-HIVE-01','cluster':'Chikkaballapur','temp':35.6,'humidity':62,'weight':23.8,'activity':119}); assert s==200; assert json.loads(b)['status']=='healthy'
s,b=get('/api/dashboard'); j=json.loads(b); real=[h for h in j['hives'] if h['id']=='REAL-HIVE-01'][0]; assert real['source']=='sensor'
s,b=post('/api/batches',{'id':'HC-TEST-1','honey_type':'Demo','cluster':'Chikkaballapur','harvest_date':'2026-09-13','purity':99}); assert s==200
s,b=get('/api/verify/HC-TEST-1'); assert s==200 and json.loads(b)['verified']
s,b=get('/api/export'); assert s==200 and b.startswith(b'id,honey_type')
s,b=post('/api/ai',{'prompt':'Summarize current hive status'}); j=json.loads(b); assert s==200 and j['llm'] is True and j['answer']=='mock llm response'
s,b=get('/api/health'); j=json.loads(b); assert s==200 and j['openai_configured'] is True and j['weather_live'] is True

server.shutdown(); mock.shutdown()
try: TEST_DB.unlink()
except FileNotFoundError: pass
print('Honey Chain LIVE self-test: ALL CHECKS PASSED')
