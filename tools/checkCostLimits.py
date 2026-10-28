"""Exercise the packaged nginx zones on loopback, with a disposable upstream."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]

class upstreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
    def log_message(self,*args):
        pass


def checkCostLimits():
    binary = shutil.which('nginx')
    if not binary:
        raise RuntimeError('Install nginx to test its real rate limit behavior.')
    upstream = ThreadingHTTPServer(('127.0.0.1',0),upstreamHandler)
    thread = threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
    with tempfile.TemporaryDirectory(prefix='armangle-nginx-') as temporary:
        root = Path(temporary)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        config = root/'nginx.conf'
        config.write_text(f'''pid {root}/nginx.pid;
error_log {root}/error.log;
events {{ worker_connections 1024; }}
http {{
 access_log off;
 client_body_temp_path {root}/body;
 proxy_temp_path {root}/proxy;
 include {ROOT}/deploy/beanstalk/rateLimits.conf;
 server {{
  listen 127.0.0.1:{port};
  include {ROOT}/deploy/beanstalk/uploads.conf;
  location / {{ proxy_pass http://127.0.0.1:{upstream.server_port}; }}
 }}
}}
''')
        subprocess.run([binary,'-t','-p',str(root),'-c',str(config)],check=True,capture_output=True)
        process = subprocess.Popen([binary,'-p',str(root),'-c',str(config),'-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        def request(path, viewer, method='GET',length=None):
            client = HTTPConnection('127.0.0.1',port,timeout=5)
            client.putrequest(method,path)
            client.putheader('CloudFront-Viewer-Address',viewer)
            if length:
                client.putheader('Content-Length',str(length))
            client.endheaders()
            result=client.getresponse();body=result.read()
            outcome=(result.status,result.getheader('Retry-After'),body)
            client.close();return outcome
        try:
            for _ in range(100):
                try:
                    request('/healthz','198.51.100.1:1234');break
                except OSError:
                    time.sleep(.05)
            else:
                raise RuntimeError('nginx did not start: '+(root/'error.log').read_text())
            report={}
            for index,(path,count) in enumerate([('/api/frame',240),('/api/upload',12),('/api/predict-angle',30),('/api/pitches',80)]):
                with ThreadPoolExecutor(max_workers=30) as pool:
                    # Vary the source port deliberately; the IP bucket must persist.
                    outcomes=list(pool.map(lambda n:request(path,f'198.51.100.{index+2}:{10000+n}'),range(count)))
                blocked=[r for r in outcomes if r[0]==429]
                assert blocked and all(r[1]=='60' and 'error' in json.loads(r[2]) for r in blocked),outcomes
                report[path]={'requests':count,'ok':sum(r[0]==200 for r in outcomes),'limited':len(blocked)}
            # A 60fps viewer may fetch 60 new previews/sec at maximum playback speed.
            start=time.monotonic()
            for index in range(180):
                time.sleep(max(0,start+index/60-time.monotonic()))
                result=request('/api/frame',f'[2001:db8::1]:{20000+index}')
                assert result[0]==200,result
            report['normalPlayback']={'requests':180,'fps':60,'limited':0}
            result=request('/api/upload','198.51.100.99:1234','POST',200*1024*1024+65536+1)
            assert result[0]==413 and '200 MB' in json.loads(result[2])['error'],result
            report['oversizeWithoutSendingBody']=413
            print(json.dumps(report,indent=2))
        finally:
            process.terminate();process.wait(timeout=10)
            if process.stderr:
                process.stderr.close()
            upstream.shutdown();upstream.server_close();thread.join()

if __name__=='__main__':
    checkCostLimits()
