"""Build a clean ZIP; never include user databases, uploads, secrets, or virtualenvs."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / 'fonts' / 'NotoSansCJK-Regular.ttc'
LICENSE = ROOT / 'fonts' / 'LICENSE-Noto-CJK.txt'


def main():
    if not FONT.is_file() or not LICENSE.is_file():
        raise SystemExit('Noto CJK font and its license are required to build the portable package.')
    files = [ROOT / name for name in ['README.md','PC-DEMO.md','start_demo.py','start-windows.bat','start-macos.command','requirements.txt','requirements.lock.txt']]
    files += [path for path in (ROOT / 'app').rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc']
    output = ROOT / 'releases' / 'medialog-demo.zip'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            name = 'Medialog-demo/' + path.relative_to(ROOT).as_posix()
            if path.suffix == '.bat':
                archive.writestr(name, path.read_text().replace('\n', '\r\n').encode('utf-8'))
            else:
                archive.write(path, name)
        archive.write(FONT,'Medialog-demo/fonts/NotoSansCJK-Regular.ttc')
        archive.write(LICENSE,'Medialog-demo/fonts/LICENSE-Noto-CJK.txt')
    print(f'Packaged: {output} ({output.stat().st_size / 1024 / 1024:.1f} MB)')


if __name__ == '__main__':
    main()
