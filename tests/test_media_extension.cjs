'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const root = path.join(__dirname, '..');
const popupSource = fs.readFileSync(path.join(root, 'extension', 'popup.js'), 'utf8');
const mediaSource = fs.readFileSync(path.join(root, 'extension', 'media.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const sandbox = {URL, Set, Map, Number, Math};
vm.runInNewContext(popupSource, sandbox);
vm.runInNewContext(mediaSource, sandbox);
const p = sandbox.WherewegoPopup, m = sandbox.WherewegoMedia;
const jpeg = 'data:image/jpeg;base64,';
test('media capture requires an actual Instagram post permalink', () => {
  assert.equal(p.postUrl('https://instagram.com/reel/Ab_c-1/?x=y'), 'https://www.instagram.com/reel/Ab_c-1/');
  assert.equal(m.postUrl('https://www.instagram.com/p/Own/'), 'https://www.instagram.com/p/Own/');
  for (const url of ['https://www.instagram.com/direct/t/1/', 'https://www.instagram.com/person/saved/', 'https://evil.test/p/A/', 'https://www.instagram.com.evil.test/p/A/', 'http://www.instagram.com/p/A/', 'https://user:pass@instagram.com/p/A/', 'https://www.instagram.com/p/A/comments/']) {
    assert.equal(p.postUrl(url), null); assert.throws(() => m.postUrl(url));
  }
});
test('crop bounds translate CSS pixels at device scale and reject stale/outside rectangles', () => {
  assert.deepEqual(plain(p.cropBounds({left: 10.25, top: 20, width: 400, height: 300}, {width: 1000, height: 700}, {width: 2000, height: 1400})), {x: 21, y: 40, width: 800, height: 600});
  for (const rect of [{left: -1, top: 0, width: 200, height: 200}, {left: 850, top: 0, width: 200, height: 200}, {left: 0, top: 0, width: 50, height: 200}, {left: 0, top: NaN, width: 200, height: 200}]) assert.throws(() => p.cropBounds(rect, {width: 1000, height: 700}, {width: 1000, height: 700}));
  assert.throws(() => p.cropBounds({left: 0, top: 0, width: 200, height: 200}, {width: 1000, height: 700}, {width: 2000, height: 700}));
});
test('before/after screenshot checks reject page, media, scroll, resize and moving video changes', () => {
  const state = {post_url: 'https://www.instagram.com/p/A/', media_id: 'photo-a', rect: {left: 20, top: 40, width: 300, height: 240}, viewport: {width: 1000, height: 700}, video_time: null};
  assert.equal(p.sameCaptureState(state, state), true);
  for (const patch of [{post_url: 'https://www.instagram.com/p/B/'}, {media_id: 'photo-b'}, {rect: {...state.rect, top: 45}}, {viewport: {...state.viewport, width: 900}}, {video_time: 3}]) assert.equal(p.sameCaptureState(state, {...state, ...patch}), false);
  assert.equal(p.sameCaptureState({...state, video_time: 3}, {...state, video_time: 3.5}), false);
});
test('video sampling is bounded and finite, including short and non-seekable videos', () => {
  assert.deepEqual(plain(m.videoTimes(10)), [0, 3.3, 6.6, 9]);
  for (const duration of [0, -1, Infinity, NaN]) assert.deepEqual(plain(m.videoTimes(duration)), []);
  assert.ok(m.videoTimes(.001).length <= 4); assert.equal(m.MAX_VIDEO_FRAMES, 4);
});
test('media-only enrichment preserves captions and saved/DM provenance with byte and count limits', () => {
  const saved = {url: 'https://www.instagram.com/p/A/', source: 'dm', source_url: 'https://www.instagram.com/direct/t/123/', title: '카페', text: '성수 주소와 메뉴'};
  const frames = Array.from({length: 14}, (_, index) => ({kind: 'image', index: index + 1, data_url: jpeg + Buffer.from('frame' + index).toString('base64')}));
  const enriched = {url: saved.url, source: 'post', source_url: saved.url, text: '', media: [...frames, {kind: 'image', index: 20, data_url: jpeg + Buffer.alloc(p.MAX_FRAME_BYTES + 1).toString('base64')}], thumbnail: jpeg + 'AAAA'};
  const [result] = p.mergeRecords([saved], [enriched]);
  assert.equal(result.source, 'dm'); assert.equal(result.source_url, saved.source_url); assert.equal(result.text, saved.text); assert.equal(result.media.length, 10); assert.equal(result.thumbnail, enriched.thumbnail);
  assert.deepEqual(plain(result.media.map(frame => frame.index)), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  assert.equal(p.mergeRecords([result], [enriched])[0].media.length, 10);
  assert.equal(p.jpegBytes(jpeg + 'YQ=='), 1); assert.throws(() => p.jpegBytes('https://cdn.example/image.jpg'));
});
test('extension update retains click-only permissions and version', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'extension', 'manifest.json'), 'utf8'));
  assert.equal(manifest.version, '0.2.0'); assert.equal(manifest.host_permissions, undefined); assert.equal(manifest.background, undefined);
  assert.deepEqual(manifest.permissions.sort(), ['activeTab', 'clipboardWrite', 'scripting', 'storage'].sort());
});
test('actual Chromium checks primary post cropping, bounded carousel and video restoration', {timeout: 60000}, t => {
  const script = String.raw`
import base64, json, os, shutil, subprocess, sys, tempfile
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print(json.dumps({'skip': 'Playwright is not installed'})); sys.exit(0)
source = json.loads(sys.stdin.read())
with sync_playwright() as runtime:
    executable = os.getenv('CHROMIUM_EXECUTABLE') or shutil.which('chromium') or runtime.chromium.executable_path
    if not os.path.exists(executable):
        print(json.dumps({'skip': 'Chromium is not installed'})); sys.exit(0)
    browser = runtime.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
    page = browser.new_page(viewport={'width': 1000, 'height': 700})
    page.route('https://www.instagram.com/**', lambda route: route.fulfill(body='<main></main>', content_type='text/html'))
    page.goto('https://www.instagram.com/p/Own/')
    page.evaluate(source['content'])
    page.evaluate(source['media'])
    page.evaluate('(code) => new Function("document", code)(undefined)', source['popup'])
    page.evaluate('''() => {
      document.body.style.margin = '0';
      document.body.style.background = 'blue';
      document.querySelector('main').innerHTML = '<article id="primary" style="width:300px;height:240px;position:relative"><img id="photo" width="300" height="240"><button aria-label="Previous" style="position:absolute;left:0;top:90px">‹</button><button aria-label="Next" style="position:absolute;right:0;top:90px">›</button></article><article id="suggested" style="margin-left:400px"><img id="suggestion" width="400" height="350"></article><aside style="position:fixed;left:650px;top:0">PRIVATE CONVERSATION</aside>';
      globalThis.frames = ['red', 'green', 'yellow'].map(color => { const canvas = document.createElement('canvas'); canvas.width = 300; canvas.height = 240; const ctx = canvas.getContext('2d'); ctx.fillStyle = color; ctx.fillRect(0, 0, 300, 240); return canvas.toDataURL(); });
      globalThis.position = 0;
      const render = () => { document.querySelector('#photo').src = frames[position]; document.querySelector('[aria-label="Previous"]').hidden = position === 0; document.querySelector('[aria-label="Next"]').hidden = position === frames.length - 1; };
      document.querySelector('[aria-label="Next"]').onclick = () => setTimeout(() => { position = Math.min(frames.length - 1, position + 1); render(); }, 350);
      document.querySelector('[aria-label="Previous"]').onclick = () => setTimeout(() => { position = Math.max(0, position - 1); render(); }, 350);
      document.querySelector('#suggestion').src = frames[1]; render();
    }''')
    page.wait_for_function('document.querySelector("#photo").complete && document.querySelector("#suggestion").complete')
    bounds = page.evaluate('() => WherewegoMedia.mainMedia().rect')
    assert bounds == {'left': 0, 'top': 0, 'width': 300, 'height': 240}, bounds
    screenshot = 'data:image/png;base64,' + base64.b64encode(page.screenshot()).decode()
    cropped = page.evaluate('''async ({screenshot, bounds}) => {
      const bitmap = await createImageBitmap(await (await fetch(screenshot)).blob());
      const result = await WherewegoPopup.cropBitmap(bitmap, bounds, {width: innerWidth, height: innerHeight}); bitmap.close();
      const image = await createImageBitmap(await (await fetch(result.data_url)).blob()), thumb = await createImageBitmap(await (await fetch(result.thumbnail)).blob());
      const canvas = new OffscreenCanvas(image.width, image.height); canvas.getContext('2d').drawImage(image, 0, 0);
      return {width: image.width, height: image.height, bytes: WherewegoPopup.jpegBytes(result.data_url), thumbnailBytes: WherewegoPopup.jpegBytes(result.thumbnail), thumbnailMax: Math.max(thumb.width, thumb.height), pixel: [...canvas.getContext('2d').getImageData(150, 120, 1, 1).data]};
    }''', {'screenshot': screenshot, 'bounds': bounds})
    assert cropped['width'] == 300 and cropped['height'] == 240 and cropped['bytes'] <= 75 * 1024, cropped
    assert cropped['thumbnailBytes'] <= 24 * 1024 and cropped['thumbnailMax'] <= 320, cropped
    assert cropped['pixel'][0] > 200 and cropped['pixel'][2] < 40, cropped
    carousel = page.evaluate('''async () => {
      const seen = []; const result = await WherewegoMedia.collect({cancelled: () => false, progress: () => {}, capture: async request => { seen.push(request); return {data_url: 'data:image/jpeg;base64,' + btoa(request.media_id.slice(-20)), thumbnail: 'data:image/jpeg;base64,AAAA', fingerprint: request.media_id}; }});
      return {count: result.records[0].media.length, seen: seen.length, position, source: result.records[0].source};
    }''')
    assert carousel == {'count': 3, 'seen': 3, 'position': 0, 'source': 'post'}, carousel
    page.evaluate('document.querySelector("#primary").style.marginTop = "1000px"')
    rejected = page.evaluate('''() => { try { WherewegoMedia.mainMedia(); return false; } catch (_) { return true; } }''')
    assert rejected, 'offscreen primary must not capture a suggested image'
    page.evaluate('''() => { document.querySelector('main').innerHTML = '<article><video id="clip" width="300" height="240"></video></article>'; const canvas = document.createElement('canvas'); canvas.width = 300; canvas.height = 240; canvas.getContext('2d').fillRect(0, 0, 300, 240); const video = document.querySelector('video'); video.srcObject = canvas.captureStream(); video.muted = true; video.play(); }''')
    page.wait_for_function('document.querySelector("video").readyState >= 2')
    video = page.evaluate('''async () => {
      const clip = document.querySelector('video'); const before = {paused: clip.paused, muted: clip.muted};
      const result = await WherewegoMedia.collect({cancelled: () => false, progress: () => {}, capture: async () => ({data_url: 'data:image/jpeg;base64,AAAA', thumbnail: 'data:image/jpeg;base64,AAAA', fingerprint: 'video'})});
      return {count: result.records[0].media.length, kind: result.records[0].media[0].kind, paused: clip.paused, muted: clip.muted, before};
    }''')
    assert video['count'] == 1 and video['kind'] == 'video_frame' and video['paused'] == video['before']['paused'] and video['muted'] == video['before']['muted'], video
    seekable = None
    if shutil.which('ffmpeg'):
        with tempfile.TemporaryDirectory() as temporary:
            filename = os.path.join(temporary, 'fixture.webm')
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc=size=300x240:rate=10', '-t', '2', '-an', '-c:v', 'libvpx', '-pix_fmt', 'yuv420p', filename], check=True, capture_output=True)
            with open(filename, 'rb') as fixture:
                video_data = 'data:video/webm;base64,' + base64.b64encode(fixture.read()).decode()
        page.evaluate('''data => { const clip = document.querySelector('video'); clip.pause(); clip.srcObject = null; clip.src = data; clip.load(); }''', video_data)
        page.wait_for_function('document.querySelector("video").readyState >= 2 && Number.isFinite(document.querySelector("video").duration)')
        seekable = page.evaluate('''async () => {
          const clip = document.querySelector('video'); clip.pause(); clip.muted = false;
          await new Promise(resolve => { clip.addEventListener('seeked', resolve, {once: true}); clip.currentTime = .8; });
          const before = {time: clip.currentTime, paused: clip.paused, muted: clip.muted};
          const seen = [];
          const result = await WherewegoMedia.collect({cancelled: () => false, progress: () => {}, capture: async () => { seen.push(clip.currentTime); return {data_url: 'data:image/jpeg;base64,' + btoa(String(clip.currentTime)), thumbnail: 'data:image/jpeg;base64,AAAA', fingerprint: String(clip.currentTime)}; }});
          const after = {time: clip.currentTime, paused: clip.paused, muted: clip.muted};
          let failed = false;
          try { await WherewegoMedia.collect({cancelled: () => false, progress: () => {}, capture: async () => { throw new Error('fixture capture stopped'); }}); } catch (_) { failed = true; }
          return {count: result.records[0].media.length, times: result.records[0].media.map(frame => frame.time_seconds), seen, before, after, failed, failureAfter: {time: clip.currentTime, paused: clip.paused, muted: clip.muted}};
        }''')
        assert seekable['count'] == 4 and len(seekable['seen']) == 4, seekable
        assert seekable['seen'] == seekable['times'], seekable
        assert seekable['before'] == seekable['after'] == seekable['failureAfter'] and seekable['failed'], seekable
    browser.close()
    print(json.dumps({'crop': cropped, 'carousel': carousel, 'video': video, 'seekable': seekable}))
`;
  const result = spawnSync('python', ['-c', script], {cwd: root, input: JSON.stringify({content: fs.readFileSync(path.join(root, 'extension', 'content.js'), 'utf8'), media: mediaSource, popup: popupSource}), encoding: 'utf8', timeout: 55000, maxBuffer: 1024 * 1024});
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  const output = JSON.parse(result.stdout.trim());
  if (output.skip) { t.skip(output.skip); return; }
  assert.equal(output.carousel.count, 3); assert.equal(output.video.kind, 'video_frame');
});
