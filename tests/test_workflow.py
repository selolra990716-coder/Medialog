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
