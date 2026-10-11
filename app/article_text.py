"""Fetch public original articles and extract grounded, attributed news sentences."""
import asyncio
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin
import httpx
from . import db, rss
from .issues import collect_excerpts, impact_excerpt


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack=[];self.blocks=[];self.current=None;self.jsonld=None;self.structured=[]

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        skip=tag in ('script','style','nav','header','footer','aside') or (bool(self.stack) and self.stack[-1][2])
        scope=tag=='article' or bool(re.search(r'article[-_ ]?(body|content|text)|news[-_ ]?(body|content|text)|story[-_ ]?(body|content|text)',attrs.get('class','')+' '+attrs.get('id',''),re.I)) or (bool(self.stack) and self.stack[-1][1])
        if tag=='script' and attrs.get('type')=='application/ld+json':self.jsonld=[]
        if tag=='p' and not skip:self.current=[bool(scope),[]]
        if tag=='br' and self.current:self.current[1].append('\n')
        if tag not in ('br','img','meta','link','input','hr','source','wbr'):self.stack.append((tag,scope,skip))

    def handle_data(self,data):
        if self.jsonld is not None:self.jsonld.append(data)
        if self.current and not (bool(self.stack) and self.stack[-1][2]):self.current[1].append(data)

    def handle_endtag(self,tag):
        if tag=='script' and self.jsonld is not None:
            try:self.find_body(json.loads(''.join(self.jsonld)))
            except (ValueError,TypeError):pass
            self.jsonld=None
        if tag=='p' and self.current:
            text=re.sub(r'\s+',' ',''.join(self.current[1])).strip()
            if len(text)>=30 and not re.search(r'무단\s*(전재|복제)|재배포|Copyright|구독하기|관련기사|로그인',text,re.I):self.blocks.append((self.current[0],text))
            self.current=None
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                del self.stack[index:];break

    def find_body(self,value):
        if isinstance(value,dict):
            if isinstance(value.get('articleBody'),str):self.structured.append(value['articleBody'])
            for child in value.values():self.find_body(child)
        elif isinstance(value,list):
            for child in value:self.find_body(child)

    def text(self):
        if self.structured:return '\n\n'.join(self.structured)[:20000]
        preferred=[text for scoped,text in self.blocks if scoped]
        paragraphs=preferred if sum(map(len,preferred))>=100 else [text for _,text in self.blocks]
        return '\n\n'.join(dict.fromkeys(paragraphs))[:20000]


def extract_article(html):
    parser=ArticleParser();parser.feed(html);return parser.text()


async def fetch_article(url):
    async with httpx.AsyncClient(timeout=12,follow_redirects=False,headers={'User-Agent':'Medialog/0.2 News Reader'}) as client:
        for _ in range(4):
            url=await rss.validate_public_url(url)
            async with client.stream('GET',url) as response:
                if response.is_redirect:
                    url=urljoin(url,response.headers.get('location',''));continue
                response.raise_for_status()
                if 'html' not in response.headers.get('content-type','text/html'):
                    raise ValueError('기사 페이지가 HTML이 아닙니다.')
                content=bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>2*1024*1024:raise ValueError('기사 크기가 제한을 초과했습니다.')
                encoding=response.encoding or 'utf-8'
                text=extract_article(bytes(content).decode(encoding,errors='replace'))
                if len(text)<80:raise ValueError('공개 기사 본문을 추출하지 못했습니다.')
                return text
    raise ValueError('기사 리디렉션이 너무 많습니다.')


async def prepare(ids):
    with db.connect() as conn:
        rows=[dict(row) for row in conn.execute('SELECT id,url,article_text FROM articles') if row['id'] in ids][:3]
    async def one(row):
        if row['article_text']:return True
        try:
            text=await fetch_article(row['url'])
            with db.connect() as conn:conn.execute('UPDATE articles SET article_text=? WHERE id=?',(text,row['id']))
            return True
        except (ValueError,OSError,httpx.HTTPError):return False
    return await asyncio.gather(*(one(row) for row in rows))


def fact_paragraphs(articles):
    grounded=[{**article,'summary':article.get('article_text') or article['summary']} for article in articles]
    excerpts=collect_excerpts(grounded)
    # Results and named performances come before scene-setting or promotional prose.
    result=re.compile(r'승리|이겼|꺾었|꺾고|제압|패배|승을|승리했다|승리를|승부|\d+\s*[-대:]\s*\d+')
    player=re.compile(r'\d+\s*(?:득점|점|리바운드|어시스트)|최다|최고|맹활약|활약|돋보|주역|승리를 이끌|더블더블|트리플더블')
    candidates=sorted(enumerate(excerpts),key=lambda row:(-(3*bool(result.search(row[1][0]))+2*bool(player.search(row[1][0]))),row[0]))
    selected=[]
    for _,(text,article_id) in candidates:
        if text not in [entry[0] for entry in selected] and len('\n\n'.join([entry[0] for entry in selected]+[text]))<=500:
            selected.append((text,article_id))
        if len(selected)==3:break
    return '\n\n'.join(text for text,_ in selected),list(dict.fromkeys(id for _,id in selected)),impact_excerpt(grounded)
