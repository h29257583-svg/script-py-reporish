import json,os,shutil,subprocess,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from probe_health import ProbeHealth,classify_message,live_badge_ids
from media_chunks import unique_chunk_paths
from publish_recordings import Publisher,fingerprints
from rolling_backup import SafetyArchive

class ProbeTests(unittest.TestCase):
 def test_concurrent_probe_is_deduplicated(self):
  health=ProbeHealth();gate=threading.Barrier(5);loader=Mock(side_effect=lambda:(time.sleep(.02) or {'id':'x'},'live'))
  def call():gate.wait();health.call('same',loader)
  workers=[threading.Thread(target=call) for _ in range(5)]
  for t in workers:t.start()
  for t in workers:t.join()
  self.assertEqual(loader.call_count,1);self.assertEqual(health.snapshot()['counts']['cache_hits'],4)
 def test_rate_limit_pauses_different_probe_urls(self):
  clock=Mock(return_value=100);health=ProbeHealth(clock=clock);health.call('bad',lambda:(None,'rate_limited'))
  loader=Mock(return_value=({'id':'live'},'live'));self.assertIsNone(health.call('other',loader));loader.assert_not_called()
  clock.return_value=401;self.assertEqual(health.call('other',loader),{'id':'live'})
 def test_network_failure_is_not_offline_evidence(self):
  health=ProbeHealth();health.call('x',lambda:(None,'network_error'));self.assertIsNone(health.snapshot()['last_successful_probe_utc'])
  self.assertEqual(classify_message('HTTP error 429'),'rate_limited');self.assertEqual(classify_message('Sign in to confirm your age.'),'age_verification')
 def test_live_badge_is_not_borrowed_from_neighbor(self):
  data={'contents':[{'videoRenderer':{'videoId':'oldvideo123','title':{'text':'LIVE highlights'}}},{'videoRenderer':{'videoId':'livevideo12','thumbnailOverlays':[{'thumbnailOverlayTimeStatusRenderer':{'style':'LIVE'}}]}}]}
  self.assertEqual(live_badge_ids('var ytInitialData = '+json.dumps(data)+';'),['livevideo12'])
 def test_upcoming_or_ended_are_not_live(self):
  data={'contents':[{'videoRenderer':{'videoId':'upcoming123','upcomingEventData':{'startTime':'123'},'badges':[{'style':'BADGE_STYLE_TYPE_LIVE_NOW'}]}}]}
  player={'videoDetails':{'videoId':'ended123456'},'microformat':{'playerMicroformatRenderer':{'liveBroadcastDetails':{'isLiveNow':False}}}}
  self.assertEqual(live_badge_ids('var ytInitialData='+json.dumps(data)+';var ytInitialPlayerResponse='+json.dumps(player)+';'),[])

class MediaTests(unittest.TestCase):
 @staticmethod
 def generate(path,seconds=14):
  subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=12','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t',str(seconds),'-c:v','libx264','-preset','ultrafast','-g','12','-c:a','aac','-f','mpegts',str(path)],check=True)
 def test_short_unique_recovery_is_kept_in_order(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);a=root/'x_chunk0002_short_exit_01.ts';b=root/'x_chunk0002_short_exit_02.ts';c=root/'x_chunk0002.ts'
   a.write_bytes(b'first');b.write_bytes(b'first');c.write_bytes(b'later')
   unique,duplicates=unique_chunk_paths([str(c),str(b),str(a)]);self.assertEqual(unique,[str(a),str(c)]);self.assertEqual(duplicates,[str(b)]);self.assertTrue(b.exists())
 def test_safety_copy_plays_after_original_is_deleted(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'_chunks'/'x_chunk0001.ts';source.parent.mkdir();self.generate(source);remote=root/'remote';remote.mkdir()
   def upload(path,metadata):
    target=remote/path.name;shutil.copyfile(path,target);self.assertEqual(fingerprints(target)['sha256'],metadata['sha256'])
    return {'provider':'local-test','url':str(target),'verification':'sha256_readback'}
   archive=SafetyArchive(root,seconds=5,overlap=1,uploader=upload);archive.stage();self.assertEqual(archive.clips,[]);archive.stage(final=True);archive.capture_finished=True
   worker=threading.Thread(target=archive.upload_pending);worker.start();worker.join(20);self.assertFalse(worker.is_alive());source.unlink()
   files=list(remote.glob('*.ts'));self.assertGreaterEqual(len(files),3)
   for path in files:
    result=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],capture_output=True);self.assertEqual(result.returncode,0);self.assertEqual(result.stderr,b'')
   self.assertTrue(all(c['status']=='uploaded' for c in archive.clips))
 def test_failed_safety_upload_keeps_both_local_copies(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'_chunks'/'x_chunk0001.ts';source.parent.mkdir();self.generate(source,6)
   archive=SafetyArchive(root,seconds=5,overlap=1,uploader=Mock(side_effect=ValueError('denied')));archive.stage(final=True)
   worker=threading.Thread(target=archive.upload_pending);worker.start();time.sleep(.3);archive.stop.set();worker.join(5)
   self.assertTrue(source.exists());self.assertTrue(list(archive.work.glob('*.ts')));self.assertTrue(any(c['attempts'] for c in archive.clips))
 def test_identical_media_uses_one_upload(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);a=root/'main.ts';b=root/'_chunks'/'copy.ts';b.parent.mkdir();self.generate(a,2);shutil.copyfile(a,b);pub=Publisher(root,root/'report.json',1800)
   with patch.object(pub,'gofile',return_value={'provider':'fake','url':'https://gofile.io/d/fixture','verification':'fixture'}) as upload:
    self.assertEqual(pub.run(),0);self.assertEqual(upload.call_count,1)
   self.assertEqual(pub.results[1]['duplicate_of'],'main.ts');self.assertTrue(a.exists() and b.exists())
 def test_stale_remote_receipt_is_not_trusted(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);path=root/'file.ts';path.write_bytes(b'test');metadata=fingerprints(path);report=root/'report.json'
   report.write_text(json.dumps({'files':[dict(metadata,uploaded=True,receipts=[{'provider':'google_drive','remote_path':'drive:YouTubeRecordings/old/file'}])]}))
   with patch.dict(os.environ,{'RCLONE_CONFIG':str(root/'private.conf')}):pub=Publisher(root,report,1800)
   fake=subprocess.CompletedProcess([],0,json.dumps({'Size':4,'Hashes':{'md5':'wrong'}}),'')
   with patch('publish_recordings.subprocess.run',return_value=fake):self.assertIsNone(pub.verified_prior(metadata))

if __name__=='__main__':unittest.main()
