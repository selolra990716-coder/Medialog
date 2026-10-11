import asyncio
import io
import json
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from app import db,rss,ai_images,news_research,article_text
from app.main import app
from app.presets import breaking_template

URL='https://news.example.com/game'
FACT='서울 삼성은 서울 SK를 90-85로 꺾고 개막 3연승을 기록했다고 구단은 밝혔다.'
PLAYER='김민수는 25점과 8리바운드를 기록하며 승리를 이끌었다고 구단은 밝혔다.'
OUTLOOK='삼성은 다음 주 원정 경기를 치를 예정이며 연승을 이어가는 것이 과제라고 밝혔다.'
BODY='\n\n'.join([FACT,PLAYER,OUTLOOK])


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('MEDIALOG_DB',str(tmp_path/'research.sqlite3'))
    monkeypatch.setenv('MEDIALOG_SCHEDULER','0')
    monkeypatch.setenv('MEDIALOG_PRESETS','0')
    monkeypatch.setenv('MEDIALOG_ORIGINALS','0')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setattr(rss,'validate_public_url',AsyncMock(side_effect=lambda url:rss.canonical_url(url)))
    news_research.jobs.clear();ai_images.jobs.clear()
    with TestClient(app) as result:yield result
    news_research.jobs.clear();ai_images.jobs.clear()


def create(client):
    with db.connect() as conn:
        aid=conn.execute('INSERT INTO articles(publisher,title,url,summary,published_at,collected_at) VALUES(?,?,?,?,?,?)',('RSS 언론사','삼성, 프로농구 개막 3연승','https://seed.example.com/game',FACT,rss.utcnow(),rss.utcnow())).lastrowid
    template=breaking_template()
    return client.post('/api/projects',json={'article_ids':[aid],'design':template.design.model_dump(),'outline':[row.model_dump() for row in template.outline]}).json()


def mock_responses(monkeypatch,quote=FACT,search_url=URL,completed=True,impact=True):
    requests=[]
    async def handler(request):
        payload=json.loads(request.content);requests.append(payload)
        if 'tools' in payload:
            parsed={'articles':[{'url':URL,'title':'삼성, SK에 90-85 승리','publisher':'확인 언론사'}]}
            output=[{'type':'web_search_call','status':'completed','action':{'sources':[{'url':search_url}]}}]
        else:
            parsed={'facts':[{'url':URL,'quote':quote},{'url':URL,'quote':PLAYER}],
                    'impact':[{'url':URL,'quote':OUTLOOK}] if impact else []}
            output=[]
        output.append({'type':'message','content':[{'type':'output_text','text':json.dumps(parsed,ensure_ascii=False),'annotations':[]}]})
        return httpx.Response(200,json={'status':'completed' if completed else 'incomplete','output':output})
    original=httpx.AsyncClient
    monkeypatch.setattr(news_research.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    monkeypatch.setattr(article_text,'fetch_article',AsyncMock(return_value=BODY))
    return requests


def run_job(project):
    news_research.jobs[project['id']]={'status':'running','error':None,'source_count':0}
    asyncio.run(news_research.run(project['id'],'fake-api-key'))


def test_web_search_and_verified_facts_are_applied_and_exported(client,monkeypatch):
    project=create(client);requests=mock_responses(monkeypatch)
    run_job(project)
    assert news_research.jobs[project['id']]['status']=='done'
    assert requests[0]['tools']==[{'type':'web_search'}]
    assert requests[0]['tool_choice']=='required' and requests[0]['store'] is False
    assert 'tools' not in requests[1]
    assert json.loads(requests[1]['input'])['articles'][0]['body']==BODY
    latest=client.get(f"/api/projects/{project['id']}").json()
    assert latest['cards'][1]['body']==FACT+'\n\n'+PLAYER
    assert latest['cards'][2]['body']==OUTLOOK
    assert latest['cards'][0]==project['cards'][0]
    assert latest['generation_mode']=='web-researched'
    assert len(latest['research_evidence'])==3
    assert latest['sources'][-1]['url']==URL
    assert latest['cards'][1]['source_ids']==[latest['sources'][-1]['article_id']]
    latest['reviewed']=True
    assert client.put(f"/api/projects/{project['id']}",json=latest).status_code==200
    saved=client.get(f"/api/projects/{project['id']}").json()
    assert saved['research_evidence']==latest['research_evidence']
    import zipfile
    exported=client.get(f"/api/projects/{project['id']}/export.zip")
    assert exported.status_code==200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert 'research-evidence.json' in archive.namelist()
        assert len(json.loads(archive.read('research-evidence.json')))==3


@pytest.mark.parametrize('case',['invented','unsearched','unreadable','incomplete'])
def test_unverified_results_leave_existing_text_untouched(client,monkeypatch,case):
    project=create(client)
    requests=mock_responses(monkeypatch,quote='없는 선수는 100점을 득점하며 우승했다고 구단은 밝혔다.' if case=='invented' else FACT,
                           search_url='https://else.example.com/other' if case=='unsearched' else URL,
                           completed=case!='incomplete')
    if case=='unreadable':monkeypatch.setattr(article_text,'fetch_article',AsyncMock(side_effect=ValueError('blocked')))
    run_job(project)
    assert news_research.jobs[project['id']]['status']=='error'
    saved=client.get(f"/api/projects/{project['id']}").json()
    assert saved['cards']==project['cards'] and saved['caption']==project['caption']
    assert saved['sources']==project['sources']


def test_missing_outlook_stays_empty_and_requires_review(client,monkeypatch):
    project=create(client);mock_responses(monkeypatch,impact=False);run_job(project)
    saved=client.get(f"/api/projects/{project['id']}").json()
    assert saved['cards'][2]['body']==''
    assert '영향·전망 근거가 없어' in saved['warning']
    saved['reviewed']=True
    assert client.put(f"/api/projects/{project['id']}",json=saved).status_code==422


def test_key_requirements_api_start_and_concurrent_job_protection(client,monkeypatch):
    project=create(client)
    assert client.post(f"/api/projects/{project['id']}/research").status_code==422
    assert client.put('/api/ai/settings',json={'research_enabled':True}).status_code==422
    assert client.put('/api/ai/settings',json={'key':'fake-api-key','research_enabled':True,'enabled':False}).status_code==200
    assert client.get('/api/ai/settings').json()['research_enabled']
    assert 'fake-api-key' not in client.get('/api/ai/settings').text
    news_research.jobs[project['id']]={'status':'running','error':None,'source_count':0}
    assert client.put(f"/api/projects/{project['id']}",json=project).status_code==409
    # Existing research returns running without starting a duplicate billable job.
    assert client.post(f"/api/projects/{project['id']}/research").json()=={'status':'running'}
    assert client.get(f"/api/projects/{project['id']}/research").json()['status']=='running'
