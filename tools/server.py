"""
Lokální server pro vývoj: jako `python3 -m http.server`, ale
  - prohlížeč si každý soubor pokaždé ověří (Cache-Control: no-cache) – po úpravě
    kódu se nikdy nenačte stará verze,
  - umí posílat soubory po částech (HTTP Range) – bez toho prohlížeč nemůže
    v delší nahrávce skočit na jiné místo.

    python3 tools/server.py          # http://localhost:8137
    python3 tools/server.py 8000
"""
import http.server
import os
import re
import sys


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".webp": "image/webp",
        ".js": "text/javascript",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        self._range_left = None
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        m = re.match(r"bytes=(\d*)-(\d*)$", (rng or "").strip())
        if not m or os.path.isdir(path):
            return super().send_head()
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None
        size = os.fstat(f.fileno()).st_size
        first, last = m.groups()
        if first == "":                                  # posledních N bajtů
            start, end = max(size - int(last or 0), 0), size - 1
        else:
            start, end = int(first), min(int(last) if last else size - 1, size - 1)
        if start > end:
            f.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", self.date_time_string(os.path.getmtime(path)))
        self.end_headers()
        f.seek(start)
        self._range_left = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        left = self._range_left
        if left is None:
            return super().copyfile(source, outputfile)
        while left > 0:
            chunk = source.read(min(64 * 1024, left))
            if not chunk:
                break
            outputfile.write(chunk)
            left -= len(chunk)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8137
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
