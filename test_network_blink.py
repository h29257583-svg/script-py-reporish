"""Real local HLS integration test: 30-second outage, no YouTube access or secrets."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,os,subprocess,sys,tempfile,threading,time
from pathlib import Path


def worker(info,root):
    os.environ.update(OUT_DIR=str(root),HOURS='0.1',CHANNEL_HANDLE='@syntheticfixture',TARGET_CHANNEL_ID='',USE_COOKIES='false',COOKIE_FILE='')
    source=Path('recorder.py').read_text().split('# MAIN\n')[0]
    namespace={'__name__':'fixture_recorder','__file__':str(Path('recorder.py').resolve())}
    exec(compile(source,'recorder.py','exec'),namespace)
    namespace['FFMPEG_INPUT_ARGS']=namespace['ffmpeg_reconnect_args']()
    original=namespace['make_download_cmd']
    def command(active,path,client):
        args=original(active,path,client)
        return args+['--metadata',str(info)]
    namespace['make_download_cmd']=command
    active=namespace['new_session']({'id':'fixture1234','source':'local_fault_test','detected_client':'web_safari'})
    result=namespace['run_download_attempt'](active,time.time()+float(os.environ.get('FAULT_STOP_SECONDS','100')),'web_safari',namespace['Queue']())
    namespace['close_session'](active,'synthetic fixture complete')
    files=list(Path(root).glob('*.ts'))
    Path(root,'fault-worker-result.json').write_text(json.dumps({'attempt_result':result,'files':[str(p) for p in files],'http_input_options':namespace['FFMPEG_INPUT_ARGS']},indent=2))


def main():
    out=Path('validation/network-blink-streamlink'+('-stop' if os.environ.get('FAULT_STOP_SECONDS') else ''));out.mkdir(parents=True,exist_ok=True)
    media=out/'media';media.mkdir(exist_ok=True)
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i','testsrc2=size=1280x720:rate=12',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','72',
                    '-c:v','libx264','-preset','ultrafast','-b:v','700k','-maxrate','900k','-bufsize','1800k','-g','24',
                    '-c:a','aac','-b:a','64k','-f','hls','-hls_time','2','-hls_list_size','0','-hls_flags','independent_segments',
                    '-hls_segment_filename',str(media/'seg%03d.ts'),str(media/'vod.m3u8')],check=True)
    segments=sorted(media.glob('seg*.ts'));start=time.monotonic();requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            elapsed=time.monotonic()-start;requests.append({'seconds':round(elapsed,2),'path':self.path,'outage':14<=elapsed<44})
            if 14<=elapsed<44:
                self.send_response(503);self.send_header('Retry-After','2');self.end_headers();self.wfile.write(b'temporary fixture outage');return
            if self.path=='/live.m3u8':
                available=min(len(segments),max(3,int(elapsed/2)+1))
                text='#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n'
                text+=''.join('#EXTINF:2.0,\n'+p.name+'\n' for p in segments[:available])
                if elapsed>=72:text+='#EXT-X-ENDLIST\n'
                payload=text.encode();kind='application/vnd.apple.mpegurl'
            else:
                name=self.path.lstrip('/')
                if name not in [p.name for p in segments]:self.send_error(404);return
                payload=(media/name).read_bytes();kind='video/mp2t'
            self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(payload)));self.end_headers()
            try:self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/live.m3u8'
    info={'id':'fixture1234','title':'Synthetic live network test','url':url,'webpage_url':url,'protocol':'m3u8_native',
          'ext':'ts','is_live':True,'format_id':'hls-720','vcodec':'h264','acodec':'aac','width':1280,'height':720,'fps':12,
          'extractor':'generic','extractor_key':'Generic'}
    info_path=out/'fixture.info.json';info_path.write_text(json.dumps(info));capture=out/'capture';capture.mkdir(exist_ok=True)
    try:
        with (out/'downloader.log').open('w') as log:
            process=subprocess.run([sys.executable,__file__,'--worker',str(info_path.resolve()),str(capture.resolve())],stdout=log,stderr=subprocess.STDOUT,timeout=110)
    finally:server.shutdown();server.server_close()
    (out/'requests.json').write_text(json.dumps(requests,indent=2))
    result={'worker_exit':process.returncode,'outage_seconds':30,'outage_requests':sum(r['outage'] for r in requests),'files':[]}
    for path in capture.glob('*.ts'):
        probe=subprocess.run(['ffprobe','-v','error','-count_frames','-show_entries','stream=codec_type,nb_read_frames:format=duration','-of','json',str(path)],capture_output=True,text=True)
        decode=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],capture_output=True)
        data=json.loads(probe.stdout)
        result['files'].append({'file':str(path),'probe':data,'full_decode_ok':decode.returncode==0 and not decode.stderr})
    result['expected_video_frames']=72*12
    frames=sum(int(stream.get('nb_read_frames','0')) for f in result['files'] for stream in f['probe'].get('streams',[]) if stream.get('codec_type')=='video')
    result['captured_video_frames']=frames
    if os.environ.get('FAULT_STOP_SECONDS'):
        target=float(os.environ['FAULT_STOP_SECONDS']);result['expected_min_video_frames']=int((target-4)*12)
        result['pass']=process.returncode==0 and result['outage_requests']>0 and frames>=result['expected_min_video_frames'] and all(f['full_decode_ok'] for f in result['files'])
    else:
        result['pass']=process.returncode==0 and result['outage_requests']>0 and frames==result['expected_video_frames'] and all(f['full_decode_ok'] for f in result['files'])
    (out/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    return 0 if result['pass'] else 1

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':worker(Path(sys.argv[2]),Path(sys.argv[3]))
    else:raise SystemExit(main())
