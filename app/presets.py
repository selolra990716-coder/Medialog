"""One-time defaults; preserve user settings, feeds, and deliberate deletions."""
from . import db
from .design import TemplateSettings

# Publisher-operated RSS endpoints. Availability is checked when collecting.
FEEDS = [
    ('연합뉴스', 'https://www.yna.co.kr/rss/news.xml'),
    ('한겨레', 'https://www.hani.co.kr/rss/'),
    ('경향신문', 'https://www.khan.co.kr/rss/rssdata/total_news.xml'),
    ('동아일보', 'https://www.donga.com/news/rss'),
    ('전자신문', 'https://rss.etnews.com/Section901.xml'),
]


def install_defaults():
    with db.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY)')
        if not conn.execute("SELECT 1 FROM migrations WHERE name='publisher-feeds-v1'").fetchone():
            for name, url in FEEDS:
                conn.execute('INSERT OR IGNORE INTO feeds(name,url) VALUES (?,?)', (name, url))
            conn.execute("INSERT INTO migrations(name) VALUES('publisher-feeds-v1')")
        template = breaking_template()
        conn.execute('INSERT OR IGNORE INTO settings(id,payload) VALUES(1,?)', (template.model_dump_json(),))


def breaking_template():
    template = TemplateSettings()
    template.design.layout = 'breaking'
    template.design.accent = '#e60012'
    template.design.background = '#191b1d'
    template.design.text_color = '#ffffff'
    template.design.notes = '기본 레퍼런스: 어두운 배경, 빨간 속보 배지, 굵은 흰색 중앙 제목, 하단 미디어로그 워드마크. 배경 사진은 뉴스에 맞는 사용 가능한 사진을 직접 선택하세요.'
    template.outline = template.outline[:3]
    return template
