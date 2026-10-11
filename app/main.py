from typing import Literal
import asyncio
import io
import json
import logging
import os
import zipfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import db, rss, assets, ai_images, article_text, news_research
from .design import Design, Outline, Layout, Role, default_outline, TemplateSettings
from .issues import draft_project, group_issues, noteworthy
from .render import render_card

log = logging.getLogger('medialog')
collection_lock = asyncio.Lock()


async def collect_all():
    if collection_lock.locked():
        return {'busy': True, 'results': []}
    async with collection_lock:
        with db.connect() as conn:
            feeds = [dict(row) for row in conn.execute('SELECT * FROM feeds ORDER BY id')]
        semaphore = asyncio.Semaphore(4)

        async def collect(feed):
            async with semaphore:
                try:
                    items = [item for item in await rss.fetch_feed(feed['url']) if noteworthy(item['title'])][:30]
                    added = 0
                    with db.connect() as conn:
                        for item in items:
                            cursor = conn.execute('''INSERT OR IGNORE INTO articles
                                (feed_id,publisher,title,url,summary,published_at,collected_at) VALUES (?,?,?,?,?,?,?)''',
                                (feed['id'], feed['name'], item['title'], item['url'], item['summary'], item['published_at'], rss.utcnow()))
                            added += cursor.rowcount
                            if item.get('image_url'):
                                conn.execute('UPDATE articles SET image_url=? WHERE url=?', (item['image_url'],item['url']))
                        conn.execute('UPDATE feeds SET last_checked=?,last_error=NULL WHERE id=?', (rss.utcnow(), feed['id']))
                    return {'feed_id': feed['id'], 'name': feed['name'], 'added': added, 'error': None}
                except Exception as exc:
                    # Do not persist proxy/network exception strings that may include credential-bearing URLs.
                    if isinstance(exc, httpx.HTTPStatusError):
                        error = f'RSS 서버 HTTP {exc.response.status_code}: 주소와 접근 권한을 확인하세요.'
                    elif isinstance(exc, httpx.ProxyError):
                        error = 'RSS 네트워크 접근 실패: 환경 설정에서 이 RSS 호스트의 접근을 허용하세요.'
                    elif isinstance(exc, httpx.TimeoutException):
                        error = 'RSS 서버 응답 시간 초과'
                    elif isinstance(exc, ValueError):
                        error = 'RSS 주소 또는 문서가 올바르지 않습니다.'
                    else:
                        error = 'RSS 수집 실패: 네트워크 접근 또는 XML 형식을 확인하세요.'
                    log.warning('Feed %s failed (%s)', feed['id'], type(exc).__name__)
                    with db.connect() as conn:
                        conn.execute('UPDATE feeds SET last_checked=?,last_error=? WHERE id=?', (rss.utcnow(), error, feed['id']))
                    return {'feed_id': feed['id'], 'name': feed['name'], 'added': 0, 'error': error}
        return {'busy': False, 'results': await asyncio.gather(*(collect(feed) for feed in feeds))}


async def schedule():
    interval = max(60, int(os.getenv('MEDIALOG_POLL_SECONDS', '900')))
    while True:
        try:
            await collect_all()
        except Exception:
            log.exception('Scheduled collection failed')
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    if os.getenv('MEDIALOG_PRESETS', '1') == '1':
        from .presets import install_defaults
        install_defaults()
    task = None
    if os.getenv('MEDIALOG_SCHEDULER', '1') == '1':
        task = asyncio.create_task(schedule())
    yield
    pending=list(news_research.tasks|ai_images.tasks)
    for background in pending:background.cancel()
    if pending:await asyncio.gather(*pending,return_exceptions=True)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title='Medialog', lifespan=lifespan)
static = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=static), name='static')


class FeedInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=10, max_length=2000)

    @model_validator(mode='after')
    def nonblank_name(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('언론사 이름을 입력하세요.')
        return self


class DraftInput(BaseModel):
    article_ids: list[int] = Field(min_length=1, max_length=20)
    outline: list[Outline] = Field(default_factory=default_outline, min_length=3, max_length=10)
    design: Design = Field(default_factory=Design)


class Source(BaseModel):
    article_id: int
    publisher: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(max_length=2000)

    @model_validator(mode='after')
    def valid_url(self):
        self.url = rss.canonical_url(self.url)
        return self


class Card(BaseModel):
    font_style: Literal['bold', 'regular'] = 'bold'
    font_size: int | None = Field(default=None, ge=20, le=150)
    text_align: Literal['center', 'left', 'right'] | None = None
    text_top: int | None = Field(default=None, ge=80, le=950)
    line_height: float | None = Field(default=None, ge=1.1, le=2.2)

    heading: str = Field(min_length=1, max_length=100)
    body: str = Field(max_length=500)
    source_ids: list[int] = Field(max_length=20)
    role: Role = 'facts'
    ai_background: bool = False
    layout: Layout | None = None
    image_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')


class ResearchEvidence(BaseModel):
    @model_validator(mode='after')
    def valid_url(self):
        self.url=rss.canonical_url(self.url)
        return self

    card_index: int = Field(ge=0,le=9)
    url: str = Field(max_length=2000)
    quote: str = Field(max_length=2000)


class Project(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    cards: list[Card] = Field(min_length=3, max_length=10)
    design: Design = Field(default_factory=Design)
    sources: list[Source] = Field(min_length=1, max_length=20)
    accent: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    caption: str = Field(max_length=10000)
    reviewed: bool = False
    research_evidence: list[ResearchEvidence] = Field(default_factory=list,max_length=30)
    generation_mode: str = 'extractive'
    warning: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def valid_review(self):
        self.design.accent = self.accent
        valid = {source.article_id for source in self.sources}
        for card in self.cards:
            if card.image_id and card.image_id not in self.design.image_ids:
                raise ValueError('카드 이미지를 먼저 참고 이미지에 첨부하세요.')
            if not set(card.source_ids) <= valid:
                raise ValueError('카드의 출처가 프로젝트에 없습니다.')
        if self.reviewed and any(not card.body.strip() or not card.source_ids for card in self.cards):
            raise ValueError('모든 카드에 문구와 출처를 입력해야 검토를 완료할 수 있습니다.')
        return self


def read_project(project_id):
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '프로젝트를 찾을 수 없습니다.')
    payload = json.loads(row['payload'])
    if 'design' not in payload:
        payload['design'] = Design(accent=payload['accent']).model_dump()
    for index, card in enumerate(payload['cards']):
        card.setdefault('ai_background', False)
        card.setdefault('role', 'cover' if index == 0 else 'sources' if index == len(payload['cards']) - 1 else 'facts')
        card.setdefault('layout', None)
        card.setdefault('image_id', None)
    return {'id': row['id'], **payload, 'updated_at': row['updated_at']}


@app.get('/')
def index():
    return FileResponse(static / 'index.html')


@app.get('/api/health')
def health():
    with db.connect() as conn:
        conn.execute('SELECT 1').fetchone()
    return {'status': 'ok', 'scheduler': os.getenv('MEDIALOG_SCHEDULER', '1') == '1'}


@app.get('/api/feeds')
def feeds():
    with db.connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM feeds ORDER BY id')]


@app.post('/api/feeds/presets')
def restore_feed_presets():
    from .presets import FEEDS
    with db.connect() as conn:
        for name, url in FEEDS:
            conn.execute('INSERT OR IGNORE INTO feeds(name,url) VALUES (?,?)', (name, url))
    return {'count': len(FEEDS)}


@app.get('/api/settings/breaking-template')
def default_breaking_template():
    from .presets import breaking_template
    return breaking_template()


@app.post('/api/feeds', status_code=201)
async def add_feed(payload: FeedInput):
    try:
        url = await rss.validate_public_url(payload.url)
    except (ValueError, OSError):
        raise HTTPException(400, '공개 HTTP(S) RSS 주소를 입력하세요. 내부 주소와 인증 포함 주소는 허용하지 않습니다.')
    with db.connect() as conn:
        if conn.execute('SELECT id FROM feeds WHERE url=?', (url,)).fetchone():
            raise HTTPException(409, '이미 등록한 RSS 주소입니다.')
        cursor = conn.execute('INSERT INTO feeds(name,url) VALUES (?,?)', (payload.name.strip(), url))
        return {'id': cursor.lastrowid, 'name': payload.name.strip(), 'url': url}


@app.delete('/api/feeds/{feed_id}', status_code=204)
def delete_feed(feed_id: int):
    with db.connect() as conn:
        if conn.execute('DELETE FROM feeds WHERE id=?', (feed_id,)).rowcount == 0:
            raise HTTPException(404, 'RSS를 찾을 수 없습니다.')


@app.post('/api/collect')
async def collect():
    return await collect_all()


@app.get('/api/issues')
def issues(q: str = '', days: int = 7, limit: int = 12):
    if limit < 1 or limit > 50:
        raise HTTPException(400,'이슈 개수는 1~50개입니다.')
    if days < 1 or days > 365:
        raise HTTPException(400, '조회 기간은 1~365일입니다.')
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with db.connect() as conn:
        articles = [dict(row) for row in conn.execute('SELECT * FROM articles WHERE published_at>=? ORDER BY published_at DESC LIMIT 1000', (cutoff,))]
    if q:
        articles = [a for a in articles if q.lower() in (a['title'] + ' ' + a['summary'] + ' ' + a['publisher']).lower()]
    return group_issues(articles)[:limit]


async def prepare_article_images(ids):
    with db.connect() as conn:
        rows = [dict(row) for row in conn.execute('SELECT id,title,image_url,image_id FROM articles') if row['id'] in ids]
    for row in rows[:3]:
        if not row['image_url'] or row['image_id']:
            continue
        try:
            content = await rss.fetch_image(row['image_url'])
            asset = await asyncio.to_thread(assets.store, content, row['title'][:100] + '.jpg')
            with db.connect() as conn:
                conn.execute('UPDATE articles SET image_id=? WHERE id=?', (asset['id'],row['id']))
        except (ValueError, OSError, httpx.HTTPError):
            log.warning('Article image unavailable for article %s', row['id'])


def apply_original_text(project, articles):
    body, source_ids, (impact, impact_ids) = article_text.fact_paragraphs(articles)
    for card in project['cards']:
        if card['role'] == 'facts':card.update(body=body,source_ids=source_ids)
        elif card['role'] == 'impact':card.update(body=impact,source_ids=impact_ids)
    project['reviewed']=False
    project['generation_mode']='original-extractive'
    project['warning']='공개 기사 본문에서 경기 결과·주요 활약·전망 관련 문장을 발췌했습니다. 수치와 출처를 검토하세요.'


def store_draft(conn, payload):
    ids = list(dict.fromkeys(payload.article_ids))
    rows = conn.execute(f"SELECT * FROM articles WHERE id IN ({','.join('?' for _ in ids)})", ids).fetchall()
    lookup = {row['id']: dict(row) for row in rows}
    if len(lookup) != len(ids):
        raise HTTPException(404, '선택한 기사를 찾을 수 없습니다. 이슈 목록을 새로 고침하세요.')
    check_assets(payload.design)
    selected_articles = [lookup[i] for i in ids]
    draft = draft_project([lookup[i] for i in ids], [row.model_dump() for row in payload.outline], payload.design.model_dump())
    if any(a.get('article_text') for a in selected_articles):
        apply_original_text(draft, selected_articles)
    validated = Project(**draft).model_dump()
    now = rss.utcnow()
    cursor = conn.execute('INSERT INTO projects(title,payload,created_at,updated_at) VALUES (?,?,?,?)',
                          (validated['title'], json.dumps(validated, ensure_ascii=False), now, now))
    return {'id': cursor.lastrowid, **validated, 'updated_at': now}


@app.post('/api/projects', status_code=201)
async def create_project(payload: DraftInput):
    if os.getenv('MEDIALOG_ORIGINALS','1') == '1' and not ai_images.config()['research_enabled']:
        await article_text.prepare(payload.article_ids)
    with db.connect() as conn:
        result = store_draft(conn, payload)
    if not news_research.start(result['id'],automatic=True):
        ai_images.start(result['id'], automatic=True)
    return result


class IssueSelection(BaseModel):
    article_ids: list[int] = Field(min_length=1, max_length=20)


@app.post('/api/issues/open')
async def open_issue(payload: IssueSelection):
    if os.getenv('MEDIALOG_ORIGINALS','1') == '1' and not ai_images.config()['research_enabled']:
        await article_text.prepare(payload.article_ids)
    ids = list(dict.fromkeys(payload.article_ids))
    placeholders = ','.join('?' for _ in ids)
    created=False
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute(f'SELECT COUNT(*) FROM articles WHERE id IN ({placeholders})', ids).fetchone()[0] != len(ids):
            raise HTTPException(404, '이슈 기사를 찾을 수 없습니다.')
        linked = conn.execute(f'SELECT project_id FROM issue_projects WHERE article_id IN ({placeholders}) ORDER BY project_id DESC LIMIT 1', ids).fetchone()
        project_id = linked['project_id'] if linked else None
        if project_id is None:
            # Reuse previously created drafts with the same sources, including edited demo projects.
            for row in conn.execute('SELECT id,payload FROM projects ORDER BY id DESC'):
                source_ids = {source['article_id'] for source in json.loads(row['payload'])['sources']}
                if source_ids == set(ids):
                    project_id = row['id']
                    break
        if project_id is None:
            settings_row = conn.execute('SELECT payload FROM settings WHERE id=1').fetchone()
            defaults = TemplateSettings.model_validate_json(settings_row['payload']) if settings_row else TemplateSettings()
            project = store_draft(conn, DraftInput(article_ids=ids, outline=defaults.outline, design=defaults.design))
            project_id = project['id']
            created=True
        else:
            project = read_project(project_id)
        conn.execute(f'UPDATE articles SET open_count=open_count+1 WHERE id=?',(ids[0],))
        for article_id in ids:
            conn.execute('INSERT INTO issue_projects(article_id,project_id) VALUES(?,?) ON CONFLICT(article_id) DO UPDATE SET project_id=excluded.project_id',
                         (article_id, project_id))
    if created and not news_research.start(project_id,automatic=True):
        ai_images.start(project_id,automatic=True)
    return project


@app.get('/api/projects')
def projects():
    with db.connect() as conn:
        rows = conn.execute('SELECT id,title,payload,updated_at FROM projects ORDER BY id DESC').fetchall()
    return [{'id': r['id'], 'title': r['title'], 'updated_at': r['updated_at'], 'reviewed': json.loads(r['payload'])['reviewed'], 'card_count': len(json.loads(r['payload'])['cards'])} for r in rows]


@app.get('/api/projects/{project_id}')
def project(project_id: int):
    return read_project(project_id)


def ensure_editable(project_id):
    if news_research.jobs.get(project_id,{}).get('status')=='running':
        raise HTTPException(409,'AI 기사 검색이 진행 중입니다. 완료 후 저장하세요.')


@app.put('/api/projects/{project_id}')
def save_project(project_id: int, payload: Project):
    ensure_editable(project_id)
    if ai_images.jobs.get(project_id,{}).get('status') == 'running':
        raise HTTPException(409,'AI 배경 생성이 진행 중입니다. 완료 후 저장하세요.')
    read_project(project_id)
    check_assets(payload.design)
    with db.connect() as conn:
        conn.execute('UPDATE projects SET title=?,payload=?,updated_at=? WHERE id=?',
                     (payload.title, payload.model_dump_json(), rss.utcnow(), project_id))
    return read_project(project_id)


@app.delete('/api/projects/{project_id}', status_code=204)
def delete_project(project_id: int):
    with db.connect() as conn:
        if conn.execute('DELETE FROM projects WHERE id=?', (project_id,)).rowcount == 0:
            raise HTTPException(404, '프로젝트를 찾을 수 없습니다.')


@app.get('/api/projects/{project_id}/cards/{index}.png')
def card_png(project_id: int, index: int):
    project = read_project(project_id)
    if index < 0 or index >= len(project['cards']):
        raise HTTPException(404, '카드를 찾을 수 없습니다.')
    try:
        content = render_card(project, index)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return Response(content, media_type='image/png', headers={'Cache-Control': 'no-store'})


@app.post('/api/projects/{project_id}/research')
async def research_project(project_id:int):
    read_project(project_id)
    if ai_images.jobs.get(project_id,{}).get('status')=='running':
        raise HTTPException(409,'배경 이미지 생성 완료 후 재검색하세요.')
    if not news_research.start(project_id):
        raise HTTPException(422,'환경설정에서 OpenAI API 키를 등록하세요.')
    return {'status':'running'}


@app.get('/api/projects/{project_id}/research')
def research_status(project_id:int):
    read_project(project_id)
    return news_research.jobs.get(project_id,{'status':'idle','error':None,'source_count':0})


class AIConfigInput(BaseModel):
    key: str = Field(default='',max_length=300)
    enabled: bool = False
    research_enabled: bool = False


@app.get('/api/ai/settings')
def ai_settings():
    configured = ai_images.config()
    return {'configured':bool(configured['key']), 'enabled':configured['enabled'],'research_enabled':configured['research_enabled']}


@app.put('/api/ai/settings')
def save_ai_settings(payload: AIConfigInput):
    try:
        ai_images.save_config(payload.key.strip(),payload.enabled,payload.research_enabled)
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    return ai_settings()


@app.post('/api/projects/{project_id}/ai-backgrounds')
async def generate_backgrounds(project_id: int):
    ensure_editable(project_id)
    read_project(project_id)
    if not ai_images.start(project_id):
        raise HTTPException(422,'환경설정에서 OpenAI API 키를 입력하세요.')
    return {'status':'running'}


@app.get('/api/projects/{project_id}/ai-backgrounds')
def background_status(project_id: int):
    read_project(project_id)
    return ai_images.jobs.get(project_id,{'status':'idle','completed':0,'error':None})


@app.post('/api/projects/{project_id}/analyze-original')
async def analyze_original(project_id: int):
    ensure_editable(project_id)
    if ai_images.jobs.get(project_id,{}).get('status')=='running':
        raise HTTPException(409,'AI 배경 생성 완료 후 기사 분석을 실행하세요.')
    project=read_project(project_id)
    ids=[source['article_id'] for source in project['sources']]
    await article_text.prepare(ids)
    with db.connect() as conn:
        articles=[dict(row) for row in conn.execute('SELECT * FROM articles') if row['id'] in ids]
        if not any(article.get('article_text') for article in articles):
            raise HTTPException(422,'기사 본문에 접근하지 못했습니다. 공개 RSS 정보만 유지했습니다. 언론사 접근 제한 또는 네트워크를 확인하세요.')
        apply_original_text(project,articles)
        conn.execute('UPDATE projects SET payload=?,updated_at=? WHERE id=?',(json.dumps(project,ensure_ascii=False),rss.utcnow(),project_id))
    return read_project(project_id)


@app.post('/api/projects/{project_id}/impact-card')
def replace_impact_card(project_id: int):
    ensure_editable(project_id)
    from .issues import impact_excerpt
    project = read_project(project_id)
    ids = [source['article_id'] for source in project['sources']]
    with db.connect() as conn:
        rows = [dict(row) for row in conn.execute('SELECT * FROM articles') if row['id'] in ids]
        body, source_ids = impact_excerpt([{**row,'summary':row.get('article_text') or row['summary']} for row in rows])
        project['cards'][2].update(role='impact', heading='영향과 전망', body=body, source_ids=source_ids)
        project['reviewed'] = False
        project['warning'] = ('RSS에서 영향·전망 관련 문장을 발췌했습니다. 원문을 검토하세요.' if body else
                              'RSS에 영향·전망 정보가 없습니다. 원문을 확인하고 세 번째 카드에 내용을 작성하세요.')
        conn.execute('UPDATE projects SET payload=?,updated_at=? WHERE id=?',
                     (json.dumps(project,ensure_ascii=False),rss.utcnow(),project_id))
    return read_project(project_id)


@app.post('/api/projects/{project_id}/format-body')
def format_project_body(project_id: int):
    ensure_editable(project_id)
    from .headlines import format_card_body
    project = read_project(project_id)
    for card in project['cards']:
        if card.get('role') not in ('cover', 'sources', 'custom'):
            card['body'] = format_card_body(card['body'])
    project['reviewed'] = False
    with db.connect() as conn:
        conn.execute('UPDATE projects SET payload=?,updated_at=? WHERE id=?',
                     (json.dumps(project, ensure_ascii=False), rss.utcnow(), project_id))
    return read_project(project_id)


@app.post('/api/projects/{project_id}/summarize-cover')
def summarize_cover(project_id: int):
    ensure_editable(project_id)
    from .headlines import summarize_title
    project = read_project(project_id)
    title = project['sources'][0]['title'] if project['sources'] else project['title']
    covers = [card for card in project['cards'] if card.get('role') == 'cover']
    if not covers:
        raise HTTPException(422, '표지 역할의 카드가 없습니다. 카드 구성을 확인하세요.')
    for card in covers:
        card['body'] = summarize_title(title)
    project['reviewed'] = False
    now = rss.utcnow()
    with db.connect() as conn:
        conn.execute('UPDATE projects SET payload=?,updated_at=? WHERE id=?',
                     (json.dumps(project, ensure_ascii=False), now, project_id))
    return read_project(project_id)


@app.get('/api/projects/{project_id}/export.zip')
def export(project_id: int):
    project = read_project(project_id)
    if not project['reviewed']:
        raise HTTPException(409, '문구와 출처를 검토하고 검토 완료를 저장해 주세요.')
    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for index in range(len(project['cards'])):
                archive.writestr(f'card-{index + 1:02d}.png', render_card(project, index))
            archive.writestr('caption.txt', project['caption'])
            archive.writestr('sources.json', json.dumps(project['sources'], ensure_ascii=False, indent=2))
            if project.get('research_evidence'):
                archive.writestr('research-evidence.json',json.dumps(project['research_evidence'],ensure_ascii=False,indent=2))
            archive.writestr('design.json', json.dumps(project.get('design', {}), ensure_ascii=False, indent=2))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return Response(buffer.getvalue(), media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="medialog-{project_id}.zip"'})


def check_assets(design):
    for asset_id in set(design.image_ids + ([design.logo_id] if design.logo_id else []) + ([design.badge_id] if design.badge_id else [])):
        try:
            assets.metadata(asset_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc))


@app.post('/api/assets', status_code=201)
async def upload_asset(request: Request, name: str = 'reference.png'):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > assets.MAX_UPLOAD_BYTES:
            raise HTTPException(413, '이미지는 8MB 이하로 첨부하세요.')
    try:
        return await asyncio.to_thread(assets.store, bytes(data), name)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except PermissionError:
        raise HTTPException(503, '이미지 저장 권한이 없습니다. ZIP을 모두 풀고 쓰기 가능한 일반 폴더에서 실행하세요.')


@app.post('/api/assets/{asset_id}/style')
def analyze_reference(asset_id: str):
    try:
        return assets.reference_style(asset_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.get('/api/assets/{asset_id}/info')
def asset_info(asset_id: str):
    try:
        return assets.metadata(asset_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.get('/api/assets/{asset_id}.png')
def asset_image(asset_id: str):
    try:
        assets.metadata(asset_id)
        return FileResponse(assets.image_path(asset_id), media_type='image/png')
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.get('/api/settings')
def get_settings():
    with db.connect() as conn:
        row = conn.execute('SELECT payload FROM settings WHERE id=1').fetchone()
    return TemplateSettings.model_validate_json(row['payload']) if row else TemplateSettings()


@app.put('/api/settings')
def save_settings(payload: TemplateSettings):
    check_assets(payload.design)
    with db.connect() as conn:
        conn.execute('INSERT INTO settings(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                     (payload.model_dump_json(),))
    return payload


@app.get('/api/assets')
def list_assets():
    with db.connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM assets ORDER BY created_at DESC')]


@app.delete('/api/assets/{asset_id}', status_code=204)
def delete_asset(asset_id: str):
    try:
        assets.metadata(asset_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        documents = [json.loads(row['payload']) for row in conn.execute('SELECT payload FROM projects')]
        documents += [json.loads(row['payload']) for row in conn.execute('SELECT payload FROM settings')]
        for document in documents:
            design = document.get('design', {})
            if asset_id in design.get('image_ids', []) or asset_id == design.get('logo_id') or asset_id == design.get('badge_id') or any(card.get('image_id') == asset_id for card in document.get('cards', [])):
                raise HTTPException(409, '사용 중인 자산입니다. 기본 설정과 제작물에서 해당 이미지·로고를 해제한 뒤 삭제하세요.')
        try:
            assets.image_path(asset_id).unlink()
        except OSError:
            raise HTTPException(503, '이미지 파일을 삭제할 수 없습니다. 폴더 권한을 확인하세요.')
        conn.execute('DELETE FROM assets WHERE id=?', (asset_id,))
