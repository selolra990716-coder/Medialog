import io
import re
import uuid
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from . import db, rss

MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def image_path(asset_id):
    if not re.fullmatch(r'[a-f0-9]{32}', asset_id):
        raise ValueError('이미지를 찾을 수 없습니다.')
    return db.db_path().parent / 'uploads' / f'{asset_id}.png'


def metadata(asset_id):
    image_path(asset_id)
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone()
    if row is None or not image_path(asset_id).is_file():
        raise ValueError('첨부 이미지를 찾을 수 없습니다. 다시 첨부하세요.')
    return dict(row)


def store(content, filename):
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ValueError('이미지는 8MB 이하로 첨부하세요.')
    try:
        with Image.open(io.BytesIO(content)) as original:
            if original.format not in ('PNG', 'JPEG', 'WEBP') or original.width * original.height > 20_000_000:
                raise ValueError('PNG·JPEG·WebP, 최대 2천만 화소 이미지를 사용하세요.')
            original.load()
            normalized = ImageOps.exif_transpose(original).convert('RGB')
            normalized.thumbnail((2400, 2400))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('읽을 수 없는 이미지입니다. PNG·JPEG·WebP를 사용하세요.') from exc
    asset_id = uuid.uuid4().hex
    path = image_path(asset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized.save(path, format='PNG')
    name = Path(filename.replace('\\', '/')).name[:120] or 'reference.png'
    try:
        with db.connect() as conn:
            conn.execute('INSERT INTO assets(id,name,width,height,created_at) VALUES (?,?,?,?,?)',
                         (asset_id, name, normalized.width, normalized.height, rss.utcnow()))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return metadata(asset_id)
