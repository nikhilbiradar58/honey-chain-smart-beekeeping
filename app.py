import csv, hashlib, io, json, mimetypes, os, sqlite3, threading, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'honey_chain.db'
ENV_PATH = ROOT / '.env'
HOST = os.getenv('HOST', '0.0.0.0')
PORT = int(os.getenv('PORT', '5000'))
WEATHER_CACHE = {'ts': 0.0, 'data': [], 'error': None}
WEATHER_LOCK = threading.Lock()
DB_LOCK = threading.Lock()

CLUSTERS = [
    (1,'Chikkaballapur','Karnataka',13.4355,77.7315),
    (2,'Sundarbans','West Bengal',21.9497,88.8988),
    (3,'Kangra Valley','Himachal Pradesh',32.0998,76.2691),
    (4,'Araku Valley','Andhra Pradesh',18.3273,82.8790),
    (5,'Coorg','Karnataka',12.3375,75.8069),
]

DEMO_HIVES = [
    ('HC-CHIK-01',1,34.8,61.0,24.2,118.0),('HC-CHIK-02',1,36.3,69.0,21.8,96.0),
    ('HC-SUND-01',2,35.4,66.0,29.5,131.0),('HC-SUND-02',2,38.1,77.0,18.7,82.0),
    ('HC-KANG-01',3,31.9,55.0,26.1,126.0),('HC-KANG-02',3,33.2,60.0,22.3,113.0),
    ('HC-ARAK-01',4,34.6,64.0,27.4,122.0),('HC-COORG-01',5,32.7,58.0,30.1,137.0),
]
DEMO_BATCHES = [
    ('HC-2026-0001','Multi-floral','Chikkaballapur','2026-09-06',98.2,'demo'),
    ('HC-2026-0002','Sundarbans Forest','Sundarbans','2026-09-05',97.4,'demo'),
    ('HC-2026-0003','Wild Forest','Kangra Valley','2026-09-03',96.8,'demo'),
    ('HC-2026-0004','Jamun','Araku Valley','2026-09-02',99.1,'demo'),
]

def load_env():
    if not ENV_PATH.exists(): return
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        s=line.strip()
        if not s or s.startswith('#') or '=' not in s: continue
        k,v=s.split('=',1); k=k.strip(); v=v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k]=v
load_env()

def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

def db():
    c=sqlite3.connect(DB_PATH,timeout=10,check_same_thread=False); c.row_factory=sqlite3.Row; return c

def ensure_db():
    with DB_LOCK:
        c=db(); cur=c.cursor()
        cur.executescript('''
        CREATE TABLE IF NOT EXISTS clusters(id INTEGER PRIMARY KEY,name TEXT UNIQUE,state TEXT,lat REAL,lon REAL);
        CREATE TABLE IF NOT EXISTS hives(id TEXT PRIMARY KEY,cluster_id INTEGER,temp REAL,humidity REAL,weight REAL,activity REAL,source TEXT,last_seen TEXT);
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,honey_type TEXT,cluster TEXT,harvest_date TEXT,purity REAL,source TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,message TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS ledger(idx INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT,action TEXT,payload TEXT,prev_hash TEXT,hash TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT,scanned_at TEXT);
        ''')
        cur.executemany('INSERT OR IGNORE INTO clusters VALUES(?,?,?,?,?)', CLUSTERS)
        for r in DEMO_HIVES:
            cur.execute('INSERT OR IGNORE INTO hives VALUES(?,?,?,?,?,?,?,?)', (*r,'demo',now_iso()))
        for r in DEMO_BATCHES:
            cur.execute('INSERT OR IGNORE INTO batches VALUES(?,?,?,?,?,?,?)', (*r,now_iso()))
        if cur.execute('SELECT COUNT(*) n FROM ledger').fetchone()['n']==0:
            prev='0'*64
            for bid,typ,clu,hd,pur,src in DEMO_BATCHES:
                payload={'batch_id':bid,'type':typ,'cluster':clu,'harvest_date':hd,'purity':pur}
                raw=json.dumps({'payload':payload,'action':'BATCH_REGISTER','prev_hash':prev},sort_keys=True)
                h=hashlib.sha256(raw.encode()).hexdigest()
                cur.execute('INSERT INTO ledger(batch_id,action,payload,prev_hash,hash,created_at) VALUES(?,?,?,?,?,?)',(bid,'BATCH_REGISTER',json.dumps(payload),prev,h,now_iso())); prev=h
        if cur.execute('SELECT COUNT(*) n FROM events').fetchone()['n']==0:
            cur.executemany('INSERT INTO events(kind,message,created_at) VALUES(?,?,?)',[
                ('SYSTEM','Honey Chain live backend started',now_iso()),('BATCH','HC-2026-0001 registered on ledger',now_iso()),('ALERT','HC-SUND-02 is above the humidity watch threshold',now_iso())])
        c.commit(); c.close()

def rows(sql,args=()):
    c=db(); r=c.execute(sql,args).fetchall(); c.close(); return [dict(x) for x in r]
def one(sql,args=()):
    c=db(); r=c.execute(sql,args).fetchone(); c.close(); return dict(r) if r else None

def event(kind,msg):
    c=db(); c.execute('INSERT INTO events(kind,message,created_at) VALUES(?,?,?)',(kind,msg,now_iso())); c.commit(); c.close()

def ledger_append(batch_id, action, payload):
    c=db(); prevrow=c.execute('SELECT hash FROM ledger ORDER BY idx DESC LIMIT 1').fetchone(); prev=prevrow['hash'] if prevrow else '0'*64
    raw=json.dumps({'batch_id':batch_id,'action':action,'payload':payload,'prev_hash':prev},sort_keys=True)
    h=hashlib.sha256(raw.encode()).hexdigest()
    c.execute('INSERT INTO ledger(batch_id,action,payload,prev_hash,hash,created_at) VALUES(?,?,?,?,?,?)',(batch_id,action,json.dumps(payload,sort_keys=True),prev,h,now_iso())); c.commit(); c.close(); return h

def hive_status(h):
    t=float(h['temp']); hum=float(h['humidity'])
    if t>=37.5 or hum>=75: return 'alert'
    if t>=36 or hum>=68: return 'watch'
    return 'healthy'

def snapshot():
    hs=rows('SELECT h.*,c.name cluster,c.state FROM hives h JOIN clusters c ON c.id=h.cluster_id ORDER BY h.id')
    bs=rows('SELECT * FROM batches ORDER BY created_at DESC')
    cs=rows('SELECT * FROM clusters ORDER BY id')
    es=rows('SELECT * FROM events ORDER BY id DESC LIMIT 15')
    ls=rows('SELECT * FROM ledger ORDER BY idx DESC LIMIT 15')
    for h in hs: h['status']=hive_status(h)
    attention=sum(h['status']=='alert' for h in hs)
    verified=one("SELECT COUNT(*) n FROM scans WHERE date(scanned_at)=date('now')")['n']
    purity=round(sum(float(b['purity']) for b in bs)/len(bs),1) if bs else 0
    return {'hives':hs,'batches':bs,'clusters':cs,'events':es,'ledger':ls,'stats':{'hives':len(hs),'clusters':len(cs),'verified_today':verified,'purity':purity,'attention':attention}}

def fetch_json(url, timeout=10):
    req=Request(url,headers={'User-Agent':'HoneyChain/1.0'})
    with urlopen(req,timeout=timeout) as r: return json.loads(r.read().decode())

def _fetch_cluster_weather(row):
    cid,name,state,lat,lon=row
    u=f'https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code&timezone=auto'
    j=fetch_json(u, timeout=4); cur=j.get('current',{})
    return {'cluster':name,'state':state,'lat':lat,'lon':lon,'temperature':cur.get('temperature_2m'),'humidity':cur.get('relative_humidity_2m'),'wind':cur.get('wind_speed_10m'),'weather_code':cur.get('weather_code'),'time':cur.get('time'),'source':'Open-Meteo'}

def weather(force=False):
    with WEATHER_LOCK:
        if WEATHER_CACHE['data'] and not force and time.time()-WEATHER_CACHE['ts']<300:
            return WEATHER_CACHE['data'],WEATHER_CACHE['error']
    data=[]; errs=[]
    with ThreadPoolExecutor(max_workers=len(CLUSTERS)) as ex:
        futures={ex.submit(_fetch_cluster_weather,row): row for row in CLUSTERS}
        for fut in as_completed(futures):
            row=futures[fut]
            try: data.append(fut.result())
            except Exception as e: errs.append(f'{row[1]}: {type(e).__name__}: {e}')
    data.sort(key=lambda x: next(i for i,r in enumerate(CLUSTERS) if r[1]==x['cluster']))
    with WEATHER_LOCK:
        WEATHER_CACHE.update({'ts':time.time(),'data':data,'error':'; '.join(errs) if errs else None})
        return WEATHER_CACHE['data'],WEATHER_CACHE['error']

def dashboard_weather():
    with WEATHER_LOCK:
        data=list(WEATHER_CACHE['data']); err=WEATHER_CACHE['error']; stale=(not data) or (time.time()-WEATHER_CACHE['ts']>=300)
    if stale:
        threading.Thread(target=weather, kwargs={'force':True}, daemon=True).start()
    return data,err

def local_ai(prompt):
    s=snapshot(); alerts=[h for h in s['hives'] if h['status']!='healthy']; top=max(s['hives'],key=lambda h:(h['status']=='alert',h['humidity'],h['temp'])) if s['hives'] else None
    if top: return f"Local fallback: {top['id']} currently has {top['temp']:.1f}°C and {top['humidity']:.0f}% humidity ({top['status']}). {len(alerts)} hive(s) need attention. This is rule-based decision support, not a disease diagnosis."
    return 'Local fallback: all recorded hives are within the configured watch thresholds.'

def extract_output_text(j):
    out=[]
    for item in j.get('output',[]):
        for c in item.get('content',[]):
            if c.get('type')=='output_text' and c.get('text'): out.append(c['text'])
    return '\n'.join(out).strip()

def ai_answer(prompt):
    key=os.getenv('OPENAI_API_KEY','').strip()
    if not key: return local_ai(prompt),False
    model=os.getenv('OPENAI_MODEL','gpt-5.6-luna')
    base=os.getenv('OPENAI_BASE_URL','https://api.openai.com/v1/responses')
    s=snapshot();
    with WEATHER_LOCK:
        wx=list(WEATHER_CACHE['data'])
    context=json.dumps({'stats':s['stats'],'hives':s['hives'],'batches':s['batches'][:8],'weather':wx[:5]},ensure_ascii=False)
    payload={'model':model,'input':[
      {'role':'system','content':'You are Honey Chain Copilot. Use the supplied current data to answer practical beekeeping and traceability questions. Be concise, distinguish observed data from inference, and never claim a disease or food-safety certification from sensor data alone.'},
      {'role':'user','content':f'Current Honey Chain data:\n{context}\n\nQuestion:\n{prompt}'}]}
    req=Request(base,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':f'Bearer {key}'},method='POST')
    try:
        with urlopen(req,timeout=40) as r: answer=extract_output_text(json.loads(r.read().decode()))
        if not answer: raise RuntimeError('OpenAI returned no text')
        return answer,True
    except Exception as e:
        return f'LLM request failed; using local fallback. ({type(e).__name__}: {e})\n\n{local_ai(prompt)}',False

class Handler(BaseHTTPRequestHandler):
    def _send(self,status,body,ctype='application/json',headers=None):
        if isinstance(body,str): body=body.encode()
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)
    def _json(self):
        try: return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')) or 0).decode() or '{}')
        except: return {}
    def do_GET(self):
        ensure_db(); p=urllib.parse.urlparse(self.path).path
        if p=='/': return self._file('index.html','text/html; charset=utf-8')
        if p.startswith('/static/'):
            name=urllib.parse.unquote(p[len('/static/'):]).lstrip('/')
            return self._file(name,mimetypes.guess_type(name)[0] or 'application/octet-stream', static=True)
        if p=='/api/dashboard':
            wx,we=dashboard_weather(); s=snapshot(); return self._send(200,json.dumps({**s,'weather':wx,'weather_error':we,'ai_configured':bool(os.getenv('OPENAI_API_KEY','').strip())}))
        if p=='/api/health':
            wx,we=dashboard_weather(); return self._send(200,json.dumps({'ok':True,'openai_configured':bool(os.getenv('OPENAI_API_KEY','').strip()),'model':os.getenv('OPENAI_MODEL','gpt-5.6-luna'),'weather_live':bool(wx),'weather_refreshing':not bool(wx),'weather_error':we,'time':now_iso()}))
        if p=='/api/weather':
            wx,we=weather(force=True); return self._send(200,json.dumps({'weather':wx,'error':we,'live':bool(wx)}))
        if p=='/api/ledger': return self._send(200,json.dumps(rows('SELECT * FROM ledger ORDER BY idx DESC')))
        if p=='/api/events': return self._send(200,json.dumps(rows('SELECT * FROM events ORDER BY id DESC LIMIT 50')))
        if p=='/api/export':
            out=io.StringIO(); w=csv.writer(out); w.writerow(['id','honey_type','cluster','harvest_date','purity','source','created_at']); [w.writerow([r[k] for k in ['id','honey_type','cluster','harvest_date','purity','source','created_at']]) for r in rows('SELECT * FROM batches ORDER BY created_at DESC')]
            return self._send(200,out.getvalue(),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename=honey_chain_batches.csv'})
        if p.startswith('/api/verify/'):
            bid=urllib.parse.unquote(p.split('/api/verify/',1)[1]); b=one('SELECT * FROM batches WHERE id=?',(bid,))
            if not b: return self._send(404,json.dumps({'verified':False,'reason':'No matching batch record'}))
            led=one('SELECT hash,prev_hash FROM ledger WHERE batch_id=? ORDER BY idx LIMIT 1',(bid,)); c=db(); c.execute('INSERT INTO scans(batch_id,scanned_at) VALUES(?,?)',(bid,now_iso())); c.commit(); c.close(); return self._send(200,json.dumps({'verified':True,'batch':b,'ledger':led}))
        if p.startswith('/verify/'):
            bid=urllib.parse.unquote(p.split('/verify/',1)[1]); return self._public_verify(bid)
        self._send(404,json.dumps({'error':'not found'}))
    def do_POST(self):
        ensure_db(); p=urllib.parse.urlparse(self.path).path; d=self._json()
        if p=='/api/telemetry':
            missing=[k for k in ['id','temp','humidity','weight','activity'] if k not in d]
            if missing:return self._send(400,json.dumps({'ok':False,'error':'Missing: '+', '.join(missing)}))
            hid=str(d['id']).strip(); conn=db(); ex=conn.execute('SELECT id,cluster_id FROM hives WHERE id=?',(hid,)).fetchone(); cluster_id=ex['cluster_id'] if ex else 1
            if not ex and d.get('cluster'):
                rr=conn.execute('SELECT id FROM clusters WHERE name=?',(str(d['cluster']).strip(),)).fetchone(); cluster_id=rr['id'] if rr else 1
            vals=(hid,cluster_id,float(d['temp']),float(d['humidity']),float(d['weight']),float(d['activity']),'sensor',now_iso())
            conn.execute('''INSERT INTO hives(id,cluster_id,temp,humidity,weight,activity,source,last_seen) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET cluster_id=excluded.cluster_id,temp=excluded.temp,humidity=excluded.humidity,weight=excluded.weight,activity=excluded.activity,source='sensor',last_seen=excluded.last_seen''',vals); conn.commit(); conn.close(); st=hive_status({'temp':vals[2],'humidity':vals[3]}); event('TELEMETRY',f'Real sensor update received from {hid} ({st.upper()})'); return self._send(200,json.dumps({'ok':True,'hive':hid,'status':st,'received_at':vals[-1]}))
        if p=='/api/simulate':
            h=one('SELECT * FROM hives ORDER BY id LIMIT 1');
            conn=db(); conn.execute("UPDATE hives SET temp=?,humidity=?,weight=?,activity=?,source='simulator',last_seen=? WHERE id=?",(36.8,72.0,round(h['weight']+0.3,1),max(20,h['activity']-15),now_iso(),h['id'])); conn.commit(); conn.close(); event('SIMULATOR',f"Simulator updated {h['id']} (not real sensor data)"); return self._send(200,json.dumps({'ok':True,'mode':'simulator','hive':h['id']}))
        if p=='/api/batches':
            for k in ['id','honey_type','cluster','harvest_date','purity']:
                if not d.get(k): return self._send(400,json.dumps({'ok':False,'error':f'Missing {k}'}))
            try: pur=float(d['purity'])
            except: return self._send(400,json.dumps({'ok':False,'error':'Purity must be numeric'}))
            conn=db(); conn.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?)',(str(d['id']),str(d['honey_type']),str(d['cluster']),str(d['harvest_date']),pur,str(d.get('source','operator')),now_iso())); conn.commit(); conn.close(); h=ledger_append(str(d['id']),'BATCH_REGISTER',d); event('BATCH',f"{d['id']} registered on ledger"); return self._send(200,json.dumps({'ok':True,'hash':h}))
        if p=='/api/ai':
            prompt=str(d.get('prompt','')).strip();
            if not prompt:return self._send(400,json.dumps({'ok':False,'error':'Prompt required'}))
            ans,llm=ai_answer(prompt); return self._send(200,json.dumps({'ok':True,'answer':ans,'llm':llm,'model':os.getenv('OPENAI_MODEL','gpt-5.6-luna')}))
        self._send(404,json.dumps({'error':'not found'}))
    def _file(self,name,ctype,static=False):
        base=(ROOT/'static') if static else ROOT
        path=(base/name).resolve()
        if base not in path.parents: return self._send(403,'forbidden','text/plain')
        try:return self._send(200,path.read_bytes(),ctype)
        except FileNotFoundError:return self._send(404,'not found','text/plain')
    def _public_verify(self,bid):
        html=(ROOT/'verify.html').read_text(encoding='utf-8').replace('{{BATCH_ID}}',urllib.parse.quote(bid))
        return self._send(200,html,'text/html; charset=utf-8')
    def log_message(self,fmt,*args): pass

def main():
    ensure_db(); server=ThreadingHTTPServer((HOST,PORT),Handler); print(f'Honey Chain running on http://127.0.0.1:{PORT}'); print(f'LAN access: http://<your-pc-ip>:{PORT}')
    try:
        import threading, webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(f'http://127.0.0.1:{PORT}')).start()
    except Exception:
        pass
    server.serve_forever()
if __name__=='__main__': main()
