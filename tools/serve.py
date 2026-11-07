"""Serve the generated report on loopback. Only files under docs are available."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'docs'

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_head(self):
        relative = unquote(urlsplit(self.path).path).lstrip('/')
        if not relative:
            self.send_response(302)
            self.send_header('Location', '/docs/index.html')
            self.end_headers()
            return None
        candidate = (ROOT / relative).resolve()
        if any(part.startswith('.') for part in Path(relative).parts) or not candidate.is_relative_to(PUBLIC) or not candidate.is_file():
            self.send_error(404)
            return None
        return super().send_head()

    def send_error(self, code, message=None, explain=None):
        if code != 404:
            return super().send_error(code, message, explain)
        body = (PUBLIC / '404.html').read_bytes()
        self.send_response(404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Permissions-Policy', 'microphone=(), camera=()')
        super().end_headers()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=3410)
    args = parser.parse_args()
    if not 3400 <= args.port <= 3499:
        parser.error('Choose a loopback test port from 3400 through 3499')
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        print(f'Local report: http://127.0.0.1:{args.port}/docs/index.html', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

if __name__ == '__main__':
    main()
