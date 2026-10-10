"""Create an explicitly labeled offline demo, isolated from the production DB."""
import json
import os

from . import db, rss
from .issues import draft_project


def main():
    os.environ.setdefault('MEDIALOG_DB', 'data/demo.sqlite3')
    db.init_db()
    with db.connect() as conn:
        for publisher, url in [('데모 언론사 A', 'https://example.com/demo-a'), ('데모 언론사 B', 'https://example.com/demo-b')]:
            conn.execute('''INSERT OR IGNORE INTO articles
                (publisher,title,url,summary,published_at,collected_at) VALUES (?,?,?,?,?,?)''',
                (publisher, '[데모] 지역 도서관 문화 프로그램 확대', url,
                 '이 문구는 프로그램 체험을 위해 만든 가상 뉴스입니다. 지역 도서관에서 문화 프로그램을 확대한다는 상황을 가정했습니다. 참여 대상과 운영 일정은 카드 편집 화면에서 직접 수정해 볼 수 있습니다. 실제 기사나 사실을 보도하는 자료가 아닙니다.',
                 rss.utcnow(), rss.utcnow()))
            conn.execute('UPDATE articles SET published_at=? WHERE url=?', (rss.utcnow(), url))
        rows = [dict(row) for row in conn.execute("SELECT * FROM articles WHERE url IN ('https://example.com/demo-a','https://example.com/demo-b') ORDER BY id")]
        if not conn.execute("SELECT id FROM projects WHERE title=?", (rows[0]['title'],)).fetchone():
            project = draft_project(rows)
            now = rss.utcnow()
            conn.execute('INSERT INTO projects(title,payload,created_at,updated_at) VALUES (?,?,?,?)',
                         (project['title'], json.dumps(project, ensure_ascii=False), now, now))
    print(f'Offline demo prepared: {db.db_path()} (fictional news, no RSS access required)')


if __name__ == '__main__':
    main()
