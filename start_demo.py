"""Portable demo launcher: standard library only, dependencies isolated in .venv-pc."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent


def pick_port(requested=None):
    for port in ([requested] if requested is not None else range(8001, 8011)):
        try:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
            return port
        except OSError:
            continue
    raise RuntimeError('No free demo port. Close another demo window or use --port 8020.')


def prepare_python():
    if sys.version_info < (3, 12):
        raise RuntimeError('Python 3.12 or newer is required. Install Python 3.12 from python.org.')
    directory = ROOT / '.venv-pc'
    executable = directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not executable.is_file():
        print('Creating a private Python environment...', flush=True)
        venv.EnvBuilder(with_pip=True).create(directory)
    lock = ROOT / 'requirements.lock.txt'
    fingerprint = hashlib.sha256(lock.read_bytes()).hexdigest()
    marker = directory / '.medialog-lock.sha256'
    if not marker.is_file() or marker.read_text() != fingerprint:
        print('Installing dependencies (Internet is needed on first launch)...', flush=True)
        subprocess.run([str(executable), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(lock)], cwd=ROOT, check=True)
        marker.write_text(fingerprint)
    return executable


def main():
    parser = argparse.ArgumentParser(description='Launch the offline Medialog demo')
    parser.add_argument('--port', type=int, help='Local port (default: first free port from 8001 to 8010)')
    parser.add_argument('--no-browser', action='store_true', help='Do not open the browser automatically')
    args = parser.parse_args()
    if args.port is not None and not 1024 <= args.port <= 65535:
        raise RuntimeError('Choose a port from 1024 to 65535.')
    executable = prepare_python()
    env = os.environ.copy()
    env['MEDIALOG_DB'] = str(ROOT / 'data' / 'demo.sqlite3')
    env['MEDIALOG_SCHEDULER'] = '0'
    bundled_font = ROOT / 'fonts' / 'NotoSansCJK-Regular.ttc'
    if bundled_font.is_file():
        env['MEDIALOG_FONT'] = str(bundled_font)
    subprocess.run([str(executable), '-m', 'app.demo'], cwd=ROOT, env=env, check=True)
    subprocess.run([str(executable), '-c', 'from app.render import font_path; print("Image font:", font_path())'], cwd=ROOT, env=env, check=True)
    port = pick_port(args.port)
    url = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([str(executable), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError('Demo server failed to start. Check the log above.')
            try:
                with opener.open(url + '/api/health', timeout=1) as response:
                    if json.load(response).get('status') == 'ok':
                        break
            except (OSError, ValueError):
                time.sleep(0.2)
        else:
            raise RuntimeError('Demo server startup timed out.')
        print(f'\nMedialog demo is ready: {url}\nKeep this window open. Press Ctrl+C to stop.\n', flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        process.wait()
    except KeyboardInterrupt:
        print('\nStopping the demo...', flush=True)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f'\nCannot start Medialog: {error}', file=sys.stderr)
        sys.exit(1)
