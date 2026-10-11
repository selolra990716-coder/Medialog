import io
import zipfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db, rss
from app.issues import group_issues
from app.main import app
from app.render import render_card

RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><item>
<title>Climate science policy expands worldwide</title>
<link>https://news.example.com/a?utm_source=rss</link>
<description>&lt;p&gt;Researchers released a new report on global climate policy. The report compares the impact across several regions. Public agencies plan to review the findings this week. The complete research is available in the original report.&lt;/p&gt;</description>
<pubDate>Sat, 10 Oct 2026 00:00:00 GMT</pubDate></item></channel></rss>'''
ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom story</title><link rel="self" href="https://news.example.com/feed"/><link href="/story"/><summary>Some factual text.</summary><updated>2026-10-10T01:00:00Z</updated></entry></feed>'''


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('MEDIALOG_DB', str(tmp_path / 'test.sqlite3'))
    monkeypatch.setenv('MEDIALOG_SCHEDULER', '0')
    monkeypatch.setenv('MEDIALOG_PRESETS', '0')
    monkeypatch.setenv('MEDIALOG_ORIGINALS', '0')
    monkeypatch.setattr(rss, 'validate_public_url', AsyncMock(side_effect=lambda url: rss.canonical_url(url)))
    with TestClient(app) as instance:
        yield instance


def test_rss_and_atom_parsing():
    items = rss.parse_feed(RSS, 'https://news.example.com/rss')
    assert len(items) == 1
    assert items[0]['url'] == 'https://news.example.com/a'
    assert '<p>' not in items[0]['summary']
    assert items[0]['published_at'] == '2026-10-10T00:00:00+00:00'
    assert rss.parse_feed(ATOM, 'https://news.example.com/rss')[0]['url'] == 'https://news.example.com/story'


def test_xml_entities_rejected():
    from defusedxml.common import DefusedXmlException
    malicious = b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&x;</title></item></channel></rss>'
    with pytest.raises(DefusedXmlException):
        rss.parse_feed(malicious, 'https://news.example.com/rss')


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///tmp/rss', 'https://user:password@example.com/rss'])
def test_unsafe_urls_rejected(url):
    with pytest.raises(ValueError):
        rss.canonical_url(url)


def test_private_destination_rejected():
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(rss.validate_public_url('http://127.0.0.1/rss'))


def test_grouping():
    articles = [
        {'id':1, 'title':'Climate science policy expands worldwide', 'publisher':'A', 'summary':'facts', 'published_at':rss.utcnow()},
        {'id':2, 'title':'Climate science policy debate worldwide', 'publisher':'B', 'summary':'facts', 'published_at':rss.utcnow()},
        {'id':3, 'title':'Football tournament kicks off', 'publisher':'C', 'summary':'', 'published_at':rss.utcnow()},
    ]
    issues = group_issues(articles)
    assert len(issues) == 2
    assert issues[0]['publisher_count'] == 2
    assert len(issues[0]['articles']) == 2


def test_end_to_end_rss_to_zip(client, monkeypatch):
    assert client.get('/api/health').json()['status'] == 'ok'
    assert client.get('/').status_code == 200
    assert client.get('/static/app.js').status_code == 200
    feed = {'name':'테스트 언론사', 'url':'https://news.example.com/rss'}
    assert client.post('/api/feeds', json=feed).status_code == 201
    assert client.post('/api/feeds', json=feed).status_code == 409
    items = rss.parse_feed(RSS, feed['url'])
    items[0]['published_at'] = rss.utcnow()
    monkeypatch.setattr(rss, 'fetch_feed', AsyncMock(return_value=items))
    first = client.post('/api/collect').json()
    assert first['results'][0]['added'] == 1
    assert client.post('/api/collect').json()['results'][0]['added'] == 0
    issues = client.get('/api/issues').json()
    assert len(issues) == 1
    assert client.get('/api/issues?q=unmatched').json() == []
    project = client.post('/api/projects', json={'article_ids':[issues[0]['articles'][0]['id']]}).json()
    assert len(project['cards']) == 6
    assert project['generation_mode'] == 'extractive'
    assert all(card['body'] for card in project['cards'])
    pid = project['id']
    assert client.get(f'/api/projects/{pid}/export.zip').status_code == 409
    image_response = client.get(f'/api/projects/{pid}/cards/0.png')
    assert image_response.status_code == 200
    assert Image.open(io.BytesIO(image_response.content)).size == (1080,1350)
    project['reviewed'] = True
    assert client.put(f'/api/projects/{pid}', json=project).status_code == 200
    response = client.get(f'/api/projects/{pid}/export.zip')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 9
        assert len([name for name in archive.namelist() if name.endswith('.png')]) == 6
        assert '테스트 언론사' in archive.read('caption.txt').decode()
        for name in archive.namelist():
            if name.endswith('.png'):
                assert Image.open(io.BytesIO(archive.read(name))).size == (1080,1350)
    assert client.delete('/api/feeds/1').status_code == 204
    assert len(client.get('/api/issues').json()) == 1
    assert client.get(f'/api/projects/{pid}').status_code == 200
    assert client.delete(f'/api/projects/{pid}').status_code == 204
    assert client.get(f'/api/projects/{pid}').status_code == 404


def test_short_summary_cannot_be_reviewed(client, monkeypatch):
    client.post('/api/feeds', json={'name':'Short', 'url':'https://news.example.com/rss'})
    monkeypatch.setattr(rss,'fetch_feed',AsyncMock(return_value=[{'title':'제목만 있는 뉴스', 'url':'https://news.example.com/short', 'summary':'', 'published_at':rss.utcnow()}]))
    client.post('/api/collect')
    issue = client.get('/api/issues').json()[0]
    assert issue['needs_review']
    project = client.post('/api/projects',json={'article_ids':[issue['id']]}).json()
    assert project['cards'][1]['body'] == ''
    project['reviewed'] = True
    assert client.put(f"/api/projects/{project['id']}",json=project).status_code == 422
    for card in project['cards']:
        if not card['body']:
            card['body'] = '원문을 확인하여 직접 작성한 문구입니다.'
            card['source_ids'] = [issue['id']]
    assert client.put(f"/api/projects/{project['id']}",json=project).status_code == 200


def test_partial_collection_failure(client, monkeypatch):
    for name in ['A','B']:
        client.post('/api/feeds', json={'name':name,'url':f'https://news.example.com/{name}'})
    async def fake_fetch(url):
        if url.endswith('/A'):
            raise ValueError('Bad XML')
        items = rss.parse_feed(RSS, url)
        items[0]['published_at'] = rss.utcnow()
        return items
    monkeypatch.setattr(rss,'fetch_feed',fake_fetch)
    results = client.post('/api/collect').json()['results']
    assert results[0]['error']
    assert results[1]['added'] == 1
    assert client.get('/api/feeds').json()[0]['last_error']
    assert len(client.get('/api/issues').json()) == 1


def test_invalid_source_and_color(client, monkeypatch):
    client.post('/api/feeds',json={'name':'A','url':'https://news.example.com/rss'})
    items = rss.parse_feed(RSS,'https://news.example.com/rss')
    items[0]['published_at'] = rss.utcnow()
    monkeypatch.setattr(rss,'fetch_feed',AsyncMock(return_value=items))
    client.post('/api/collect')
    p = client.post('/api/projects',json={'article_ids':[1]}).json()
    p['cards'][0]['source_ids'] = [999]
    assert client.put(f"/api/projects/{p['id']}",json=p).status_code == 422
    p['cards'][0]['source_ids'] = [1]
    p['accent'] = 'red'
    assert client.put(f"/api/projects/{p['id']}",json=p).status_code == 422
    assert client.get('/api/issues?days=0').status_code == 400
    assert client.get(f"/api/projects/{p['id']}/cards/6.png").status_code == 404


def test_render_long_text_rejects_overflow():
    project = {'cards':[{'heading':'한글 카드뉴스', 'body':'가\n'*500, 'source_ids':[]}], 'accent':'#c5f66a', 'sources':[], 'reviewed':False}
    with pytest.raises(ValueError, match='문구'):
        render_card(project,0)


def test_managed_proxy_remote_dns(monkeypatch):
    import asyncio
    import socket
    monkeypatch.setattr(rss, 'getproxies', lambda: {'https':'http://proxy.example.com:8080'})
    monkeypatch.setattr(rss, 'proxy_bypass', lambda host: False)
    def no_local_dns(*args, **kwargs):
        raise socket.gaierror('DNS unavailable')
    monkeypatch.setattr(rss.socket, 'getaddrinfo', no_local_dns)
    assert asyncio.run(rss.validate_public_url('https://news.example.com/rss')) == 'https://news.example.com/rss'
    for url in ['https://127.0.0.1/rss','https://server.local/rss','https://2130706433/rss']:
        with pytest.raises(ValueError):
            asyncio.run(rss.validate_public_url(url))
    monkeypatch.setattr(rss, 'getproxies', lambda: {})
    with pytest.raises(ValueError):
        asyncio.run(rss.validate_public_url('https://news.example.com/rss'))


def test_feed_whitespace_name_rejected(client):
    assert client.post('/api/feeds',json={'name':'   ','url':'https://news.example.com/rss'}).status_code == 422


def seeded_article(client, monkeypatch):
    client.post('/api/feeds',json={'name':'A','url':'https://news.example.com/rss'})
    items=rss.parse_feed(RSS,'https://news.example.com/rss')
    items[0]['published_at']=rss.utcnow()
    monkeypatch.setattr(rss,'fetch_feed',AsyncMock(return_value=items))
    client.post('/api/collect')
    return client.get('/api/issues').json()[0]['id']


def image_bytes():
    buffer=io.BytesIO()
    Image.new('RGB',(160,90),'#e14635').save(buffer,format='JPEG')
    return buffer.getvalue()


@pytest.mark.parametrize('count',[3,10])
def test_custom_outline_layout_and_references(client,monkeypatch,count):
    aid=seeded_article(client,monkeypatch)
    outline=[{'role':'cover','heading':'직접 지정한 표지'}]+[{'role':'facts','heading':f'설정한 제목 {i}'} for i in range(count-2)]+[{'role':'sources','heading':'근거'}]
    design={'layout':'editorial','accent':'#123456','notes':'넓은 여백과 밝은 배경','references':[{'url':'https://example.com/reference','note':'타이포그래피 참고'}]}
    response=client.post('/api/projects',json={'article_ids':[aid],'outline':outline,'design':design})
    assert response.status_code==201,response.text
    project=response.json()
    assert len(project['cards'])==count
    assert project['cards'][0]['heading']=='직접 지정한 표지'
    assert project['cards'][-1]['role']=='sources'
    assert project['accent']=='#123456'
    assert project['design']['notes']==design['notes']
    assert client.get('/api/projects').json()[0]['card_count']==count
    png=client.get(f"/api/projects/{project['id']}/cards/0.png")
    assert png.status_code==200
    assert Image.open(io.BytesIO(png.content)).getpixel((0,0))==(246,244,235)
    loaded=client.get(f"/api/projects/{project['id']}").json()
    assert loaded['design']['references'][0]['note']=='타이포그래피 참고'


def test_upload_render_and_export_design(client,monkeypatch):
    aid=seeded_article(client,monkeypatch)
    response=client.post('/api/assets?name=reference.jpg',content=image_bytes(),headers={'Content-Type':'image/jpeg'})
    assert response.status_code==201,response.text
    asset=response.json()
    assert asset['name']=='reference.jpg'
    assert (asset['width'],asset['height'])==(160,90)
    assert client.get(f"/api/assets/{asset['id']}.png").status_code==200
    assert client.get(f"/api/assets/{asset['id']}/info").json()['id']==asset['id']
    project=client.post('/api/projects',json={'article_ids':[aid],'design':{'layout':'image','image_ids':[asset['id']]}}).json()
    assert project['cards'][0]['image_id']==asset['id']
    response=client.get(f"/api/projects/{project['id']}/cards/0.png")
    assert response.status_code==200,response.text
    image=Image.open(io.BytesIO(response.content))
    pixel=image.getpixel((500,350))
    assert pixel[0]>220 and pixel[1]<80 and pixel[2]<80
    project['cards'][1]['layout']='editorial'
    project['reviewed']=True
    assert client.put(f"/api/projects/{project['id']}",json=project).status_code==200
    archive=client.get(f"/api/projects/{project['id']}/export.zip")
    assert archive.status_code==200,archive.text if archive.status_code!=200 else ''
    import json
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zip:
        design=json.loads(zip.read('design.json'))
        assert design['image_ids']==[asset['id']]
        assert len([name for name in zip.namelist() if name.endswith('.png')])==6


def test_invalid_design_and_upload(client,monkeypatch):
    aid=seeded_article(client,monkeypatch)
    assert client.post('/api/assets',content=b'not a real image').status_code==422
    assert client.post('/api/assets',content=b'x'*(8*1024*1024+1)).status_code==413
    assert client.get('/api/assets/not-an-id.png').status_code==404
    assert client.post('/api/projects',json={'article_ids':[aid],'design':{'image_ids':['a'*32]}}).status_code==422
    assert client.post('/api/projects',json={'article_ids':[aid],'design':{'layout':'invalid'}}).status_code==422
    assert client.post('/api/projects',json={'article_ids':[aid],'design':{'references':[{'url':'javascript:alert(1)'}]}}).status_code==422
    assert client.post('/api/projects',json={'article_ids':[aid],'outline':[{'heading':'1','role':'facts'}]}).status_code==422


def test_existing_projects_are_compatible(client,monkeypatch):
    aid=seeded_article(client,monkeypatch)
    project=client.post('/api/projects',json={'article_ids':[aid]}).json()
    project.pop('design')
    for card in project['cards']:
        for field in ['role','image_id','layout']:
            card.pop(field)
    import json
    with db.connect() as conn:
        conn.execute('UPDATE projects SET payload=? WHERE id=?',(json.dumps(project),project['id']))
    loaded=client.get(f"/api/projects/{project['id']}").json()
    assert loaded['design']['layout']=='classic'
    assert loaded['cards'][0]['role']=='cover'
    assert loaded['cards'][-1]['role']=='sources'
    assert client.get(f"/api/projects/{project['id']}/cards/0.png").status_code==200


def test_settings_and_automatic_issue_workspace(client,monkeypatch):
    aid=seeded_article(client,monkeypatch)
    defaults=client.get('/api/settings').json()
    assert len(defaults['outline'])==6
    defaults['outline']=[{'role':'cover','heading':'기본 표지'},{'role':'facts','heading':'기본 사실'},{'role':'sources','heading':'출처'}]
    defaults['design']['layout']='editorial'
    defaults['design']['accent']='#123456'
    assert client.put('/api/settings',json=defaults).status_code==200
    project=client.post('/api/issues/open',json={'article_ids':[aid]}).json()
    assert len(project['cards'])==3
    assert project['cards'][0]['heading']=='기본 표지'
    assert project['design']['layout']=='editorial'
    assert project['accent']=='#123456'
    project['cards'][0]['body']='편집해서 저장한 문구'
    assert client.put(f"/api/projects/{project['id']}",json=project).status_code==200
    defaults['design']['layout']='classic'
    client.put('/api/settings',json=defaults)
    reopened=client.post('/api/issues/open',json={'article_ids':[aid]}).json()
    assert reopened['id']==project['id']
    assert reopened['cards'][0]['body']=='편집해서 저장한 문구'
    assert reopened['design']['layout']=='editorial'
    assert len(client.get('/api/projects').json())==1
    client.delete(f"/api/projects/{project['id']}")
    recreated=client.post('/api/issues/open',json={'article_ids':[aid]}).json()
    assert recreated['design']['layout']=='classic'
    assert client.post('/api/issues/open',json={'article_ids':[999]}).status_code==404


def test_asset_library_transparent_logo_and_delete_protection(client,monkeypatch):
    aid=seeded_article(client,monkeypatch)
    image=Image.new('RGBA',(64,32),(0,0,0,0))
    from PIL import ImageDraw
    ImageDraw.Draw(image).rectangle((16,0,48,31),fill=(255,0,0,255))
    buffer=io.BytesIO();image.save(buffer,format='PNG')
    asset=client.post('/api/assets?name=logo.png',content=buffer.getvalue()).json()
    stored=Image.open(io.BytesIO(client.get(f"/api/assets/{asset['id']}.png").content))
    assert stored.mode=='RGBA'
    assert stored.getpixel((0,0))[3]==0
    assert client.get('/api/assets').json()[0]['id']==asset['id']
    defaults=client.get('/api/settings').json()
    defaults['design']['logo_id']=asset['id']
    assert client.put('/api/settings',json=defaults).status_code==200
    assert client.delete(f"/api/assets/{asset['id']}").status_code==409
    project=client.post('/api/issues/open',json={'article_ids':[aid]}).json()
    rendered=Image.open(io.BytesIO(client.get(f"/api/projects/{project['id']}/cards/0.png").content))
    assert rendered.getpixel((80,80))==(17,24,32)
    assert rendered.getpixel((130,80))==(255,0,0)
    defaults['design']['logo_id']=None
    client.put('/api/settings',json=defaults)
    assert client.delete(f"/api/assets/{asset['id']}").status_code==409
    client.delete(f"/api/projects/{project['id']}")
    assert client.delete(f"/api/assets/{asset['id']}").status_code==204
    assert client.get(f"/api/assets/{asset['id']}.png").status_code==404


def test_summary_combines_sources_without_copyright_noise():
    articles=[{'id':1,'title':'Climate policy debate worldwide','publisher':'A','summary':'Researchers published a comprehensive report on climate policy. All rights reserved. Copyright 2026.','published_at':rss.utcnow()},
              {'id':2,'title':'Climate policy debate expands worldwide','publisher':'B','summary':'The public agency will review the report next week.','published_at':rss.utcnow()}]
    issue=group_issues(articles)[0]
    assert 'Researchers published' in issue['summary']
    assert 'public agency' in issue['summary']
    assert 'Copyright' not in issue['summary']
    assert 'All rights reserved' not in issue['summary']
    assert issue['summary_source_ids']==[1,2]
    assert not issue['needs_review']


def test_unused_asset_delete_and_missing_setting_asset(client):
    asset=client.post('/api/assets',content=image_bytes()).json()
    defaults=client.get('/api/settings').json()
    defaults['design']['logo_id']='f'*32
    assert client.put('/api/settings',json=defaults).status_code==422
    assert client.delete(f"/api/assets/{asset['id']}").status_code==204
    assert client.get('/api/assets').json()==[]


def test_concurrent_issue_open_creates_one_draft(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    aid=seeded_article(client,monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:client.post('/api/issues/open',json={'article_ids':[aid]}),range(2)))
    assert all(response.status_code==200 for response in results)
    assert results[0].json()['id']==results[1].json()['id']
    assert len(client.get('/api/projects').json())==1


def test_reference_palette_applies_to_saved_defaults_and_render(client):
    image = Image.new('RGB', (240, 300), '#151515')
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 30, 160, 45), fill='#e02030')
    draw.rectangle((30, 120, 210, 150), fill='white')
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG')
    asset = client.post('/api/assets?name=reference.jpg', content=buffer.getvalue()).json()
    response = client.post(f"/api/assets/{asset['id']}/style")
    assert response.status_code == 200
    style = response.json()
    assert style['layout'] == 'poster'
    assert style['text_color'] == '#ffffff'
    assert max(bytes.fromhex(style['background'][1:])) < 40
    accent = bytes.fromhex(style['accent'][1:])
    assert accent[0] > accent[1] * 2
    settings = client.get('/api/settings').json()
    settings['design'].update(style, image_ids=[asset['id']])
    assert client.put('/api/settings', json=settings).status_code == 200
    assert client.get('/api/settings').json()['design']['background'] == style['background']
    project = {'accent':style['accent'], 'design':settings['design'], 'reviewed':False,
               'sources':[], 'cards':[{'heading':'오늘의 이슈', 'body':'핵심 내용을 중앙에 표시합니다.', 'source_ids':[]}]}
    rendered = Image.open(io.BytesIO(render_card(project, 0)))
    assert rendered.getpixel((0, 0)) == tuple(bytes.fromhex(style['background'][1:]))
    assert client.post('/api/assets/invalid/style').status_code == 404
    settings['design']['background'] = 'invalid'
    assert client.put('/api/settings', json=settings).status_code == 422


def test_publisher_defaults_are_once_only_and_preserve_user_edits(client):
    from app.presets import install_defaults, FEEDS
    install_defaults()
    assert len(client.get('/api/feeds').json()) == len(FEEDS)
    settings = client.get('/api/settings').json()
    assert settings['design']['layout'] == 'breaking'
    settings['design']['accent'] = '#123456'
    client.put('/api/settings', json=settings)
    feed = client.get('/api/feeds').json()[0]
    client.delete(f"/api/feeds/{feed['id']}")
    install_defaults()
    assert len(client.get('/api/feeds').json()) == len(FEEDS) - 1
    assert client.get('/api/settings').json()['design']['accent'] == '#123456'
    assert client.post('/api/feeds/presets').status_code == 200
    assert len(client.get('/api/feeds').json()) == len(FEEDS)
    assert client.get('/api/settings/breaking-template').json()['design']['layout'] == 'breaking'


def test_breaking_reference_design_renders_photo_and_long_body(client):
    from app.presets import breaking_template
    design = breaking_template().design.model_dump()
    buffer = io.BytesIO()
    Image.new('RGB', (500, 200), '#609030').save(buffer, format='PNG')
    asset = client.post('/api/assets?name=background.png', content=buffer.getvalue()).json()
    design['image_ids'] = [asset['id']]
    project = {'accent':design['accent'], 'design':design, 'reviewed':False, 'sources':[],
               'cards':[{'heading':'오늘의 이슈', 'role':'cover', 'body':'새로운 지역 소식\n핵심 내용을 확인하세요', 'source_ids':[], 'image_id':asset['id']},
                        {'heading':'핵심 사실', 'role':'facts', 'body':'원문에서 확인해야 하는 핵심 내용입니다. ' * 22, 'source_ids':[], 'image_id':None}]}
    rendered = Image.open(io.BytesIO(render_card(project, 0)))
    assert rendered.size == (1080,1350)
    assert rendered.getpixel((0,0)) == (36,54,18)
    assert rendered.getpixel((500,182)) == (230,0,18)
    assert Image.open(io.BytesIO(render_card(project,1))).size == (1080,1350)
    assert client.post('/api/assets/'+asset['id']+'/style').status_code == 200


def test_default_publisher_collection_creates_breaking_draft(client, monkeypatch):
    from app.presets import install_defaults
    install_defaults()
    items = rss.parse_feed(RSS, 'https://news.example.com/rss')
    async def fetch(url):
        if 'khan.co.kr' in url:
            raise ValueError('테스트: 해당 언론사 일시 응답 실패')
        return items
    monkeypatch.setattr(rss, 'fetch_feed', fetch)
    response = client.post('/api/collect')
    assert response.status_code == 200
    results = response.json()['results']
    assert len(results) == 5
    assert len([row for row in results if row.get('error')]) == 1
    issues = client.get('/api/issues').json()
    assert issues
    draft = client.post('/api/issues/open', json={'article_ids':[a['id'] for a in issues[0]['articles']]}).json()
    assert draft['design']['layout'] == 'breaking'
    assert len(draft['cards']) == 3
    assert client.get(f"/api/projects/{draft['id']}/cards/0.png").status_code == 200
    assert client.post('/api/issues/open', json={'article_ids':[a['id'] for a in issues[0]['articles']]}).json()['id'] == draft['id']


def test_cover_title_shortening_and_word_wrap():
    from app.headlines import summarize_title
    from app.render import wrap, bold_font
    from PIL import ImageDraw
    assert summarize_title('‘상식’이 먹히는 삼성…23년 만에 프로농구 개막 3연승 행진') == '삼성, 23년 만에 프로농구 개막 3연승'
    assert summarize_title('[속보] 대구 북구 마트 천장 무너져 수십명 부상(종합)') == '대구 북구 마트 천장 무너져 수십명 부상'
    assert summarize_title('경찰, 사고 원인 조사…붕괴 가능성 배제 못해') == '경찰, 사고 원인 조사 · 붕괴 가능성 배제 못해'
    long = '당국은 새로운 정책에 대해 추가적인 검토와 관련 절차를 거쳐 적용 여부를 결정할 예정이라고 밝혔다'
    assert len(summarize_title(long)) <= 48
    assert summarize_title(long).endswith('…')
    draw = ImageDraw.Draw(Image.new('RGB',(1080,1350)))
    lines = wrap('삼성, 23년 만에 프로농구 개막 3연승',draw,bold_font(112),930)
    assert ' '.join(lines) == '삼성, 23년 만에 프로농구 개막 3연승'
    assert all(draw.textlength(line,font=bold_font(112)) <= 930 for line in lines)


def test_existing_cover_can_be_shortened_without_changing_other_cards(client):
    from app import rss
    from app.issues import draft_project
    import json
    title = '‘상식’이 먹히는 삼성…23년 만에 프로농구 개막 3연승 행진'
    articles = [{'id':1,'publisher':'테스트','title':title,'url':'https://example.com/story','summary':'원문에서 확인한 핵심 내용입니다. 이 내용은 테스트용으로 작성한 문장입니다.'}]
    draft = draft_project(articles)
    assert draft['cards'][0]['body'] == '삼성, 23년 만에 프로농구 개막 3연승'
    draft['cards'][0]['body'] = title
    draft['cards'][1]['body'] = '내가 편집한 본문'
    with db.connect() as conn:
        cursor = conn.execute('INSERT INTO projects(title,payload,created_at,updated_at) VALUES(?,?,?,?)',(title,json.dumps(draft),rss.utcnow(),rss.utcnow()))
        project_id = cursor.lastrowid
    result = client.post(f'/api/projects/{project_id}/summarize-cover')
    assert result.status_code == 200
    project = result.json()
    assert project['cards'][0]['body'] == '삼성, 23년 만에 프로농구 개막 3연승'
    assert project['cards'][1]['body'] == '내가 편집한 본문'
    assert project['sources'][0]['title'] == title
    assert project['caption'] == draft['caption']
    assert not project['reviewed']


def test_byline_cleanup_paragraphs_and_impact_selection():
    from app.headlines import format_card_body
    from app.issues import impact_excerpt
    from app.presets import breaking_template
    body = '(서울=연합뉴스) 오명언 기자 = 삼성은 개막 3연승을 거뒀다. 다음 경기에서는 연승을 이어갈 계획이다.'
    assert format_card_body(body) == '삼성은 개막 3연승을 거뒀다.\n\n다음 경기에서는 연승을 이어갈 계획이다.'
    assert format_card_body('김철수 기자는 향후 계획을 설명했다.') == '김철수 기자는 향후 계획을 설명했다.'
    text, ids = impact_excerpt([{'id':7,'summary':body}])
    assert text == '다음 경기에서는 연승을 이어갈 계획이다.'
    assert ids == [7]
    assert impact_excerpt([{'id':8,'summary':'삼성은 마지막 경기에서 승리를 거뒀다고 구단이 밝혔습니다.'}]) == ('',[])
    assert breaking_template().outline[2].role == 'impact'
    assert breaking_template().outline[2].heading == '영향과 전망'


def test_body_and_impact_actions_preserve_sources(client):
    from app.issues import draft_project
    from app.presets import breaking_template
    import json
    title='삼성, 프로농구 개막 3연승'
    summary='(서울=연합뉴스) 오명언 기자 = 삼성은 새 시즌 개막 3연승을 거뒀다고 밝혔다. 향후 경기에서도 연승을 이어갈 계획이라고 구단은 밝혔다.'
    with db.connect() as conn:
        article_id=conn.execute('INSERT INTO articles(publisher,title,url,summary,published_at,collected_at) VALUES(?,?,?,?,?,?)',('테스트',title,'https://example.com/impact',summary,rss.utcnow(),rss.utcnow())).lastrowid
        article=dict(conn.execute('SELECT * FROM articles WHERE id=?',(article_id,)).fetchone())
        template=breaking_template()
        draft=draft_project([article],[o.model_dump() for o in template.outline],template.design.model_dump())
        draft['cards'][1]['body']=summary
        draft['cards'][2].update(role='background',heading='배경과 맥락',body='이전 본문')
        project_id=conn.execute('INSERT INTO projects(title,payload,created_at,updated_at) VALUES(?,?,?,?)',(title,json.dumps(draft),rss.utcnow(),rss.utcnow())).lastrowid
    formatted=client.post(f'/api/projects/{project_id}/format-body').json()
    assert '기자 =' not in formatted['cards'][1]['body']
    assert '\n\n' in formatted['cards'][1]['body']
    result=client.post(f'/api/projects/{project_id}/impact-card').json()
    assert result['cards'][2]['role']=='impact'
    assert result['cards'][2]['heading']=='영향과 전망'
    assert '계획' in result['cards'][2]['body']
    assert result['sources']==draft['sources']
    assert result['caption']==draft['caption']
    assert client.get(f'/api/projects/{project_id}/cards/1.png').status_code==200
    assert client.get(f'/api/projects/{project_id}/cards/2.png').status_code==200


def test_ai_config_and_three_backgrounds(client,monkeypatch):
    import asyncio
    from app import ai_images
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    assert client.put('/api/ai/settings',json={'enabled':True}).status_code==422
    result=client.put('/api/ai/settings',json={'key':'test-private-key','enabled':False})
    assert result.json()=={'configured':True,'enabled':False,'research_enabled':False}
    assert 'test-private-key' not in client.get('/api/ai/settings').text
    aid=seeded_article(client,monkeypatch)
    from app.presets import breaking_template
    template=breaking_template()
    project=client.post('/api/projects',json={'article_ids':[aid],'design':template.design.model_dump(),'outline':[row.model_dump() for row in template.outline]}).json()
    calls=[]
    async def fake(title,card,key):
        calls.append((title,card['heading']))
        return image_bytes()
    monkeypatch.setattr(ai_images,'generate_image',fake)
    ai_images.jobs[project['id']]={'status':'running','completed':0,'error':None}
    assert client.put(f"/api/projects/{project['id']}",json=project).status_code==409
    asyncio.run(ai_images.run(project['id'],'test-private-key'))
    result=client.get(f"/api/projects/{project['id']}").json()
    assert len(calls)==3
    assert len(set(card['image_id'] for card in result['cards']))==3
    assert all(card['ai_background'] for card in result['cards'])
    assert client.get(f"/api/projects/{project['id']}/ai-backgrounds").json()['completed']==3
    rendered=Image.open(io.BytesIO(client.get(f"/api/projects/{project['id']}/cards/1.png").content))
    # Label lives at the upper right, and the old source footer is absent.
    assert rendered.crop((800,58,1008,105)).getbbox()
    assert len(set(rendered.crop((150,1120,930,1150)).getdata()))==1
    ai_images.jobs.clear()


def test_rss_image_discovery_and_failed_ai_does_not_corrupt_project(client,monkeypatch):
    from app import ai_images
    import asyncio
    data=b'<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item><title>News</title><link>https://example.com/news</link><media:thumbnail url="https://example.com/photo.jpg"/></item></channel></rss>'
    assert rss.parse_feed(data,'https://example.com/rss')[0]['image_url']=='https://example.com/photo.jpg'
    aid=seeded_article(client,monkeypatch)
    project=client.post('/api/projects',json={'article_ids':[aid]}).json()
    async def fail(*args):
        raise ValueError('이미지 생성 권한을 확인하세요.')
    monkeypatch.setattr(ai_images,'generate_image',fail)
    ai_images.jobs[project['id']]={'status':'running','completed':0,'error':None}
    asyncio.run(ai_images.run(project['id'],'fake'))
    assert ai_images.jobs[project['id']]['status']=='error'
    assert client.get(f"/api/projects/{project['id']}").json()['cards']==project['cards']
    ai_images.jobs.clear()


def test_custom_banner_is_kept_on_cover_only(client):
    from app.presets import breaking_template
    template=breaking_template()
    banner=client.post('/api/assets?name=news-banner.png',content=image_bytes()).json()
    template.design.badge_id=banner['id']
    assert client.put('/api/settings',json=template.model_dump()).status_code==200
    assert client.delete(f"/api/assets/{banner['id']}").status_code==409
    draft={'accent':template.design.accent,'design':template.design.model_dump(),'reviewed':False,'sources':[],
           'cards':[{'role':'cover','heading':'표지','body':'짧은 뉴스 제목','source_ids':[]},
                    {'role':'facts','heading':'핵심 내용','body':'첫 번째 문단입니다.\n\n다음 문단에서 자세히 설명합니다.','source_ids':[]}]}
    cover=Image.open(io.BytesIO(render_card(draft,0)))
    content=Image.open(io.BytesIO(render_card(draft,1)))
    assert cover.getpixel((550,205))[0]>200
    assert content.getpixel((550,280))==(25,27,29)


def test_original_article_extracts_winner_and_player_with_evidence(client,monkeypatch):
    from app import article_text
    html='''<html><nav><p>이 문장은 메뉴 항목으로 경기 결과에 포함되면 안 됩니다.</p></nav><article>
    <p>(서울=연합뉴스) 홍길동 기자 = 서울 삼성은 서울 SK를 90-85로 꺾고 개막 3연승을 기록했다고 구단은 밝혔다.</p>
    <p>테스트 선수 김민수는 25점과 8리바운드를 기록하며 승리를 이끌었다고 구단이 밝혔다.</p>
    <p>삼성은 다음 주 원정 경기를 치를 예정이며 연승을 이어가는 것이 과제라고 밝혔다.</p>
    </article><footer><p>이 문장은 저작권 안내이며 기사 본문에 들어가면 안 됩니다.</p></footer></html>'''
    text=article_text.extract_article(html)
    assert '메뉴 항목' not in text and '저작권 안내' not in text
    assert '90-85' in text and '25점' in text
    monkeypatch.setattr(article_text,'fetch_article',AsyncMock(return_value=text))
    monkeypatch.setenv('MEDIALOG_ORIGINALS','1')
    aid=seeded_article(client,monkeypatch)
    from app.presets import breaking_template
    template=breaking_template()
    project=client.post('/api/projects',json={'article_ids':[aid],'design':template.design.model_dump(),'outline':[row.model_dump() for row in template.outline]}).json()
    assert '서울 SK' in project['cards'][1]['body']
    assert '90-85' in project['cards'][1]['body']
    assert '김민수' in project['cards'][1]['body']
    assert '25점' in project['cards'][1]['body']
    assert '기자 =' not in project['cards'][1]['body']
    assert '예정' in project['cards'][2]['body']
    assert project['generation_mode']=='original-extractive'
    result=client.post(f"/api/projects/{project['id']}/analyze-original")
    assert result.status_code==200
    assert result.json()['sources']==project['sources']


def test_original_fetch_failure_preserves_existing_edits(client,monkeypatch):
    from app import article_text
    aid=seeded_article(client,monkeypatch)
    project=client.post('/api/projects',json={'article_ids':[aid]}).json()
    project['cards'][1]['body']='기존에 편집한 문구입니다.'
    client.put(f"/api/projects/{project['id']}",json=project)
    monkeypatch.setattr(article_text,'fetch_article',AsyncMock(side_effect=ValueError('blocked')))
    assert client.post(f"/api/projects/{project['id']}/analyze-original").status_code==422
    assert client.get(f"/api/projects/{project['id']}").json()['cards'][1]['body']=='기존에 편집한 문구입니다.'


def test_cover_uses_two_lines_or_smaller_three_line_text():
    from app.render import fit_cover,bold_font
    from PIL import ImageDraw
    from app.headlines import summarize_title
    draw=ImageDraw.Draw(Image.new('RGB',(1080,1350)))
    face,lines,spacing=fit_cover('삼성, 소노 꺾고 개막 3연승',draw)
    assert len(lines)==2
    assert face.size==112
    assert all(draw.textlength(line,font=face)<=930 for line in lines)
    assert summarize_title('삼성, 소노에 82-81 역전승 개막 3연승')=='삼성, 소노에 개막 3연승'
    face,lines,spacing=fit_cover('지역 교통 개선을 위한 새로운 노선 확대와 주민 의견 수렴 계획 발표',draw)
    assert len(lines)<=3
    assert len(lines)==3
    assert 88<=face.size<=100
    from app.render import centered_title_origin
    for line in lines:
        x,y=centered_title_origin(draw,line,face,320)
        left,top,right,bottom=draw.textbbox((x,y),line,font=face)
        assert abs((left+right)/2-540)<=0.5
        assert top==320


def test_priority_feed_is_limited_filters_minor_items_and_counts_local_clicks(client,monkeypatch):
    from app.issues import group_issues,noteworthy
    assert not noteworthy('[부고] 홍길동씨 별세')
    assert not noteworthy('[프로농구 중간순위] 11일')
    assert noteworthy('삼성, 소노 꺾고 개막 3연승')
    with db.connect() as conn:
        for i in range(16):
            conn.execute('INSERT INTO articles(publisher,title,url,summary,published_at,collected_at) VALUES(?,?,?,?,?,?)',
                         ('테스트',f'독립소식{i}',f'https://example.com/news/{i}','확인된 테스트 기사 내용을 담은 충분한 길이의 문장입니다.',rss.utcnow(),rss.utcnow()))
    assert len(client.get('/api/issues').json())==12
    assert len(client.get('/api/issues?limit=6').json())==6
    assert client.get('/api/issues?limit=0').status_code==400
    article=client.get('/api/issues').json()[0]['articles'][0]
    client.post('/api/issues/open',json={'article_ids':[article['id']]})
    with db.connect() as conn:assert conn.execute('SELECT open_count FROM articles WHERE id=?',(article['id'],)).fetchone()['open_count']==1
    issues=client.get('/api/issues').json()
    assert issues[0]['local_clicks']==1
    assert issues[0]['priority_basis']=='복수 언론사 보도·최신성·프로그램 내 클릭'


def test_background_preserves_both_edges_of_landscape_and_portrait_photos():
    from app.render import full_photo_background
    for size in ((1200, 600), (600, 1200), (1080, 1350)):
        source = Image.new('RGB', size, '#202020')
        from PIL import ImageDraw, ImageOps
        draw = ImageDraw.Draw(source)
        draw.rectangle((0, 0, 49, size[1]-1), fill='#ff0000')
        draw.rectangle((size[0]-50, 0, size[0]-1, size[1]-1), fill='#00ff00')
        result = full_photo_background(source, (1080, 1350))
        fitted = ImageOps.contain(source, (1080, 1350))
        x = (1080-fitted.width)//2
        y = (1350-fitted.height)//2
        assert result.size == (1080, 1350)
        assert result.getpixel((x+5, y+fitted.height//2)) == (255, 0, 0)
        assert result.getpixel((x+fitted.width-6, y+fitted.height//2)) == (0, 255, 0)


def test_editor_typography_persists_and_changes_actual_png(client, monkeypatch):
    aid=seeded_article(client,monkeypatch)
    project=client.post('/api/projects',json={'article_ids':[aid]}).json()
    project['design']['layout']='breaking'
    project['cards'][0].update(body='짧은 제목',font_size=60,font_style='regular',text_align='left',text_top=400,line_height=1.5)
    response=client.put(f"/api/projects/{project['id']}",json=project)
    assert response.status_code==200
    saved=client.get(f"/api/projects/{project['id']}").json()
    assert saved['cards'][0]['font_size']==60
    assert saved['cards'][0]['text_align']=='left'
    png=client.get(f"/api/projects/{project['id']}/cards/0.png")
    assert png.status_code==200
    image=Image.open(io.BytesIO(png.content)).convert('RGB')
    assert any(r>240 and g>240 and b>240 for r,g,b in image.crop((100,400,380,470)).getdata())
    assert not any(r>240 and g>240 and b>240 for r,g,b in image.crop((700,400,980,470)).getdata())
    project['cards'][0]['font_size']=200
    assert client.put(f"/api/projects/{project['id']}",json=project).status_code==422
