"""Two real capture processes, overlapping windows, 30-second HLS outage, frame coverage."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,os,subprocess,sys,threading,time
from pathlib import Path


def frame_times(path):
 r=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=best_effort_timestamp_time','-of','json',str(path)],capture_output=True,text=True,check=True)
 return {round(float(f['best_effort_timestamp_time']),3) for f in json.loads(r.stdout)['frames'] if f.get('best_effort_timestamp_time')}


def main():
 root=Path('validation/live-handoff');root.mkdir(parents=True,exist_ok=True);media=root/'media';media.mkdir(exist_ok=True)
 subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i','testsrc2=size=1280x720:rate=12','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','100','-c:v','libx264','-preset','ultrafast','-b:v','700k','-maxrate','900k','-bufsize','1800k','-g','24','-c:a','aac','-b:a','64k','-f','hls','-hls_time','2','-hls_list_size','0','-hls_flags','independent_segments','-hls_segment_filename',str(media/'seg%03d.ts'),str(media/'vod.m3u8')],check=True)
 segments=sorted(media.glob('seg*.ts'));start=time.monotonic();events=[]
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_GET(self):
   elapsed=time.monotonic()-start
   if 14<=elapsed<44:
    events.append(round(elapsed,2));self.send_response(503);self.send_header('Retry-After','2');self.end_headers();return
   if self.path=='/live.m3u8':
    count=min(len(segments),max(3,int(elapsed/2)+1));text='#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n'
    text+=''.join('#EXTINF:2.0,\n'+p.name+'\n' for p in segments[:count])
    if elapsed>=100:text+='#EXT-X-ENDLIST\n'
    body=text.encode();kind='application/vnd.apple.mpegurl'
   else:
    path=media/self.path.lstrip('/')
    if path.parent!=media or not path.is_file():self.send_error(404);return
    body=path.read_bytes();kind='video/mp2t'
   self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers()
   try:self.wfile.write(body)
   except (BrokenPipeError,ConnectionResetError):pass
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 url=f'http://127.0.0.1:{server.server_port}/live.m3u8'
 info={'id':'fixture1234','title':'Synthetic overlap test','url':url,'webpage_url':url,'protocol':'m3u8_native','ext':'ts','is_live':True,'format_id':'hls720','vcodec':'h264','acodec':'aac','width':1280,'height':720,'extractor':'generic','extractor_key':'Generic'}
 p=root/'source.info.json';p.write_text(json.dumps(info));children=[];handles=[]
 try:
  for label,delay in [('windowA',0),('windowB',40)]:
   left=delay-(time.monotonic()-start)
   if left>0:time.sleep(left)
   out=root/label;out.mkdir(exist_ok=True);log=(out/'capture.log').open('w');handles.append(log)
   env=dict(os.environ,FAULT_STOP_SECONDS='60')
   children.append((label,subprocess.Popen([sys.executable,'test_network_blink.py','--worker',str(p.resolve()),str(out.resolve())],env=env,stdout=log,stderr=subprocess.STDOUT)))
  for label,child in children:child.wait(timeout=120)
 finally:
  server.shutdown();server.server_close()
  for f in handles:f.close()
 reference=frame_times(media/'vod.m3u8');captured=set();files=[]
 for label,child in children:
  for path in (root/label).glob('*.ts'):
   frames=frame_times(path);captured|=frames;decode=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],capture_output=True)
   files.append({'window':label,'file':str(path),'frames':len(frames),'full_decode_ok':decode.returncode==0 and not decode.stderr,'worker_exit':child.returncode})
 result={'outage_seconds':30,'overlap_seconds':20,'expected_video_frames':len(reference),'covered_video_frames':len(reference&captured),'missing_frames':len(reference-captured),'files':files,'outage_requests':len(events)}
 result['pass']=bool(events) and len(reference)==1200 and not(reference-captured) and all(f['full_decode_ok'] and f['worker_exit']==0 for f in files)
 (root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));return 0 if result['pass'] else 1

if __name__=='__main__':raise SystemExit(main())
