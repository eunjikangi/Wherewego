'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const extension = path.join(__dirname, '..', 'extension');
const content = fs.readFileSync(path.join(extension, 'content.js'), 'utf8');
const popup = fs.readFileSync(path.join(extension, 'popup.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
function selectorMatches(el, selector) {
  const match = selector.trim().match(/^([a-z0-9]+)?(?:\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\])?$/i);
  if (!match || (!match[1] && !match[2])) return false;
  return (!match[1] || el.tag === match[1].toLowerCase()) &&
    (!match[2] || Object.hasOwn(el.attrs, match[2]) && (match[3] === undefined || el.attrs[match[2]] === match[3]));
}
class Element {
  constructor(tag, attrs = {}, text = '', children = [], options = {}) {
    this.tag = tag; this.attrs = attrs; this.ownText = text; this.children = children;
    this.style = {display: 'block', visibility: 'visible', overflowY: 'visible', ...options.style};
    this.rect = {left: 0, right: 1000, top: 0, bottom: 200, ...options.rect};
    this.clientHeight = options.clientHeight || 200; this.scrollHeight = options.scrollHeight || 200; this.scrollTop = 500;
    for (const child of children) child.parentElement = this;
    this.content = attrs.content || '';
  }
  get innerText() { return [this.ownText, ...this.children.map(child => child.innerText)].filter(Boolean).join(' '); }
  get alt() { return this.attrs.alt || ''; }
  getAttribute(name) { return this.attrs[name] || null; }
  getClientRects() { return this.style.display === 'none' ? [] : [this.rect]; }
  getBoundingClientRect() { return this.rect; }
  matches(selector) { return selector.split(',').some(part => selectorMatches(this, part)); }
  closest(selector) { for (let el = this; el; el = el.parentElement) if (el.matches(selector)) return el; return null; }
  querySelectorAll(selector) {
    const found = [];
    const visit = el => { for (const child of el.children) { if (selector === '*' || child.matches(selector)) found.push(child); visit(child); } };
    visit(this); return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  contains(el) { for (; el; el = el.parentElement) if (el === this) return true; return false; }
  scrollBy(options) { this.scrollTop += options.top; }
}
function fixture(body, href = 'https://www.instagram.com/person/saved/') {
  const html = new Element('html', {}, '', [body]);
  const sandbox = {URL, Map, Set, Promise, setTimeout: resolve => { resolve(); return 1; },
    location: {href}, document: {body, documentElement: html, querySelectorAll: selector => html.querySelectorAll(selector), querySelector: selector => html.querySelector(selector)},
    getComputedStyle: el => el.style, innerWidth: 1000, innerHeight: 800, scrollY: 0};
  sandbox.window = {scrollBy(options) { sandbox.scrollY += options.top; }};
  vm.runInNewContext(content, sandbox, {filename: 'content.js'});
  return sandbox;
}
function helpers() { return fixture(new Element('body')).WherewegoCollector; }
test('manifest has only click-scoped capture and explicit clipboard permissions', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(extension, 'manifest.json'), 'utf8'));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions.sort(), ['activeTab', 'clipboardWrite', 'scripting', 'storage'].sort());
  assert.equal(manifest.host_permissions, undefined);
  assert.equal(manifest.background, undefined);
  assert.equal(manifest.content_scripts, undefined);
});
test('only saved, opened DM threads and permalink pages are supported', () => {
  const c = helpers();
  assert.equal(c.validatePage('https://instagram.com/alice/saved/all-posts/').source, 'saved');
  assert.equal(c.validatePage('https://www.instagram.com/direct/t/123/').source, 'dm');
  assert.equal(c.validatePage('https://www.instagram.com/reel/ABC_-1/?igsh=x#y').sourceUrl, 'https://www.instagram.com/reel/ABC_-1/');
  for (const url of ['http://instagram.com/p/A/', 'https://instagram.com.evil.test/p/A/', 'https://www.instagram.com/', 'https://www.instagram.com/direct/inbox/', 'https://www.instagram.com/alice/', 'https://www.instagram.com/accounts/login/', 'https://www.instagram.com/challenge/abc/', 'https://user:pass@www.instagram.com/p/A/']) {
    assert.throws(() => c.validatePage(url));
  }
});
test('URL mapping canonicalizes IG posts and admits external links only in DM', () => {
  const c = helpers(), source = 'https://www.instagram.com/direct/t/123/';
  assert.deepEqual(plain(c.normalizeLink('/reel/ABC_-/?igsh=secret#x', source, 'dm')), {url: 'https://www.instagram.com/reel/ABC_-/', external: false});
  assert.equal(c.normalizeLink('javascript:alert(1)', source, 'dm'), null);
  assert.equal(c.normalizeLink('https://user:pass@example.test/a', source, 'dm'), null);
  assert.equal(c.normalizeLink('https://example.test/article', source, 'saved'), null);
  assert.equal(c.normalizeLink('/alice/', source, 'dm'), null);
  const encoded = 'https://l.instagram.com/?u=' + encodeURIComponent('https://example.test/article?x=1#section');
  assert.deepEqual(plain(c.normalizeLink(encoded, source, 'dm')), {url: 'https://example.test/article?x=1', external: true});
});
test('saved screen excludes navigation and unrelated external anchors', () => {
  const post = new Element('a', {href: '/p/One/'}, '제주 카페 추천');
  const sidebar = new Element('nav', {}, '', [new Element('a', {href: '/p/Noise/'}, '사이드바 링크')]);
  const main = new Element('main', {}, '', [new Element('article', {}, '', [post]), sidebar, new Element('a', {href: 'https://example.test/'}, '외부 링크')]);
  const s = fixture(new Element('body', {}, '', [main]));
  const records = s.WherewegoCollector.extract(s.WherewegoCollector.validatePage(s.location.href));
  assert.equal(records.length, 1);
  assert.equal(records[0].url, 'https://www.instagram.com/p/One/');
  assert.match(records[0].text, /제주 카페/);
});
test('DM extraction stays inside the active conversation log', () => {
  const sidebar = new Element('aside', {}, '', [new Element('a', {href: 'https://private-other-thread.test/'}, '다른 대화')]);
  const log = new Element('div', {role: 'log'}, '', [new Element('div', {role: 'listitem'}, '이번 주 여행', [new Element('a', {href: 'https://example.test/travel'}, '여행 링크')])], {rect: {left: 300, right: 980}});
  const s = fixture(new Element('body', {}, '', [new Element('main', {}, '', [sidebar, log])]), 'https://www.instagram.com/direct/t/123/');
  const records = s.WherewegoCollector.extract(s.WherewegoCollector.validatePage(s.location.href));
  assert.equal(records.length, 1);
  assert.equal(records[0].url, 'https://example.test/travel');
  assert.match(records[0].text, /이번 주 여행/);
});
test('DM refuses to scan the whole page when conversation scope is unknown', () => {
  const s = fixture(new Element('body', {}, '', [new Element('main', {}, '', [new Element('a', {href: 'https://example.test/'}, '목록 링크')])]), 'https://www.instagram.com/direct/t/123/');
  assert.throws(() => s.WherewegoCollector.extract(s.WherewegoCollector.validatePage(s.location.href)), /메시지 영역/);
});
test('DM works without main and captures semantic card attributes and literal permalinks', () => {
  const sidebar = new Element('aside', {}, '', [new Element('div', {role: 'link', 'data-url': '/p/OtherThread/'}, '다른 대화')]);
  const card = new Element('div', {role: 'button', 'data-url': encodeURIComponent('https://www.instagram.com/reel/Card/'), 'aria-label': '공유한 릴스'}, '부산 여행', [new Element('img', {alt: '부산 바닷가'})]);
  const literal = new Element('div', {role: 'listitem'}, 'https://www.instagram.com/p/Literal/');
  const log = new Element('div', {role: 'log'}, '', [card, literal], {rect: {left: 0, right: 390}});
  const s = fixture(new Element('body', {}, '', [sidebar, log]), 'https://www.instagram.com/direct/t/123/');
  const records = s.WherewegoCollector.extract(s.WherewegoCollector.validatePage(s.location.href));
  assert.deepEqual(plain(records.map(item => item.url)), ['https://www.instagram.com/reel/Card/', 'https://www.instagram.com/p/Literal/']);
  assert.match(records[0].text, /부산 여행/);
  assert.equal(s.WherewegoCollector.scopeFor('dm'), log);
});
test('DM reports URL-less preview cards without clicking them in current capture', async () => {
  const preview = new Element('div', {role: 'button', 'aria-label': '공유한 게시물'}, '카페', [new Element('img', {alt: '카페'})]);
  preview.click = () => { throw new Error('unrequested click'); };
  const profile = new Element('div', {role: 'button', 'aria-label': '프로필 보기'}, '', [new Element('img')]);
  const log = new Element('div', {role: 'log'}, '', [preview, profile]);
  const s = fixture(new Element('body', {}, '', [log]), 'https://www.instagram.com/direct/t/123/');
  const result = await s.WherewegoCollector.collect('current');
  assert.equal(result.records.length, 0);
  assert.equal(result.unresolved_cards, 1);
  assert.match(result.notice, /DM 공유 게시물 찾기/);
  await assert.rejects(helpers().collect('resolve'), /열린 DM/);
});
test('DM unknown plain wrappers cannot borrow another post description', () => {
  const first = new Element('div', {role: 'link', 'data-href': '/p/First/'}, '첫 번째 카페');
  const second = new Element('div', {role: 'link', 'data-href': '/p/Second/'}, '둘째 여행');
  const row = new Element('div', {role: 'row'}, '여러 메시지', [first, second]);
  const log = new Element('div', {role: 'log'}, '', [row]);
  const s = fixture(new Element('body', {}, '', [log]), 'https://www.instagram.com/direct/t/123/');
  const records = s.WherewegoCollector.extract(s.WherewegoCollector.validatePage(s.location.href));
  assert.equal(records.length, 2);
  assert.doesNotMatch(records[0].text, /둘째 여행/);
  assert.doesNotMatch(records[1].text, /첫 번째 카페/);
});
test('detail extraction uses only own permalink, not suggested post links', () => {
  const main = new Element('main', {}, '', [new Element('article', {}, '성수 카페와 디저트', [new Element('a', {href: '/p/Suggested/'}, '추천')])]);
  const s = fixture(new Element('body', {}, '', [main]), 'https://www.instagram.com/p/Own/?igsh=x');
  const records = s.WherewegoCollector.extract(s.WherewegoCollector.validatePage(s.location.href));
  assert.equal(records.length, 1); assert.equal(records[0].url, 'https://www.instagram.com/p/Own/');
  assert.match(records[0].text, /성수 카페/);
});
test('merge caps records at 500 and enriches duplicates without losing saved provenance', () => {
  const c = helpers();
  const saved = {url: 'https://www.instagram.com/p/A/', text: '', title: '내용 확인이 필요한 게시물', source: 'saved', source_url: 'https://www.instagram.com/alice/saved/'};
  const detailed = {...saved, text: '서울 맛집 추천', title: '맛집', source: 'post', source_url: saved.url};
  const merged = c.mergeRecords([saved], [detailed]);
  assert.equal(merged.length, 1); assert.equal(merged[0].text, '서울 맛집 추천'); assert.equal(merged[0].source, 'saved');
  assert.equal(c.mergeRecords([], Array.from({length: 700}, (_, n) => ({url: 'https://www.instagram.com/p/P' + n + '/', text: ''}))).length, 500);
});
test('scroll collection stops at 20 passes and popup disconnect cancellation stops before reading', async () => {
  const main = new Element('main', {}, '', [new Element('a', {href: '/p/A/'}, '음식')]);
  const s = fixture(new Element('body', {}, '', [main]));
  const result = await s.WherewegoCollector.collect('scroll');
  assert.equal(result.passes, 20); assert.equal(result.records.length, 1);
  await assert.rejects(s.WherewegoCollector.collect('scroll', {cancelled: () => true}), /중단/);
});
test('visible login overlay blocks extraction without reading password values', async () => {
  const input = new Element('input', {name: 'password'});
  Object.defineProperty(input, 'value', {get() { throw new Error('credential read'); }});
  const s = fixture(new Element('body', {}, '', [new Element('main', {}, '', [new Element('a', {href: '/p/A/'}, '링크'), input])]));
  await assert.rejects(s.WherewegoCollector.collect('current'), /로그인 화면/);
});
test('handoff restricts destinations and caps actual percent-encoded fragment', () => {
  const sandbox = {URL, Set}; vm.runInNewContext(popup, sandbox, {filename: 'popup.js'});
  const p = sandbox.WherewegoPopup;
  assert.equal(p.targetOrigin('https://ml-cherry-wherewego.web.app/'), 'https://ml-cherry-wherewego.web.app');
  for (const url of ['https://evil.test', 'javascript:alert(1)', 'http://ml-cherry-wherewego.web.app', 'https://ml-cherry-wherewego.web.app/path', 'https://ml-cherry-wherewego.web.app/?next=evil', 'https://user:pass@ml-cherry-wherewego.web.app/']) assert.throws(() => p.targetOrigin(url));
  const record = {url: 'https://www.instagram.com/p/A/', title: '서울 카페', text: '한글', source: 'saved', source_url: 'https://www.instagram.com/alice/saved/'};
  const url = new URL(p.transferUrl('https://ml-cherry-wherewego.web.app', [record]));
  assert.deepEqual(JSON.parse(decodeURIComponent(url.hash.slice('#wherewego='.length))), {version: 1, records: [record]});
  assert.throws(() => p.transferUrl('https://ml-cherry-wherewego.web.app', [{...record, text: '가'.repeat(170000)}]), /주소로 전송할 수 없/);
  assert.throws(() => p.transferUrl('https://ml-cherry-wherewego.web.app', []));
});
