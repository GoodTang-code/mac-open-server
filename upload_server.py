import http.server
import os
import socket
import socketserver
import subprocess
import sys
from urllib.parse import urlparse
from html import escape

PORT = 8001
# Save uploads into a dedicated "uploaded" folder under the current working directory
UPLOAD_DIR = os.path.join(os.getcwd(), "uploaded")
os.makedirs(UPLOAD_DIR, exist_ok=True)


class ReusableTCPServer(socketserver.TCPServer):
    # Allow restarting quickly without waiting for OS socket timeout.
    allow_reuse_address = True

class UploadHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        request_path = urlparse(self.path).path
        if request_path in ('/', '/upload', '/upload/'):
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <!doctype html>
                <html lang="en">
                <head>
                  <meta charset="utf-8" />
                  <meta name="viewport" content="width=device-width, initial-scale=1" />
                  <title>Upload files</title>
                  <style>
                    :root {
                      --bg: #f4f7fb;
                      --card: #ffffff;
                      --text: #1e293b;
                      --muted: #64748b;
                      --border: #dbe2ea;
                      --accent: #0f766e;
                      --accent-pressed: #115e59;
                    }

                    * {
                      box-sizing: border-box;
                    }

                    body {
                      margin: 0;
                      min-height: 100vh;
                      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                      color: var(--text);
                      background: linear-gradient(180deg, #eef4fb 0%, var(--bg) 100%);
                      padding: 16px;
                      display: grid;
                      place-items: center;
                    }

                    .card {
                      width: min(680px, 100%);
                      background: var(--card);
                      border: 1px solid var(--border);
                      border-radius: 14px;
                      padding: 18px;
                      box-shadow: 0 8px 22px rgba(15, 23, 42, 0.08);
                    }

                    h2 {
                      margin: 0 0 8px 0;
                      font-size: clamp(1.25rem, 1.05rem + 1.2vw, 1.75rem);
                    }

                    p {
                      margin: 0 0 14px 0;
                      color: var(--muted);
                      font-size: 0.95rem;
                    }

                    form {
                      display: grid;
                      gap: 12px;
                    }

                    input[type="file"] {
                      width: 100%;
                      border: 1px solid var(--border);
                      border-radius: 10px;
                      padding: 10px;
                      background: #fff;
                    }

                    input[type="submit"] {
                      appearance: none;
                      border: 0;
                      border-radius: 10px;
                      padding: 12px 14px;
                      color: #fff;
                      background: var(--accent);
                      font-weight: 600;
                      font-size: 1rem;
                      cursor: pointer;
                      min-height: 44px;
                    }

                    input[type="submit"]:active {
                      background: var(--accent-pressed);
                    }

                    @media (min-width: 640px) {
                      .card {
                        padding: 24px;
                      }
                    }
                  </style>
                </head>
                <body>
                  <main class="card">
                    <h2>Upload file(s)</h2>
                    <p>Choose one or more files, then tap upload.</p>
                    <form enctype="multipart/form-data" method="post">
                      <!-- Explicitly allow picking videos (and images) to keep iOS Safari happy -->
                      <input name="file" type="file" accept="video/mp4,video/quicktime,image/*" multiple />
                      <input type="submit" value="Upload" />
                    </form>
                  </main>
                </body>
                </html>
            """)
        else:
            super().do_GET()

    def do_POST(self):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            self.send_error(400, "Invalid upload")
            return

        boundary_token = None
        if "boundary=" in content_type:
            boundary_token = content_type.split("boundary=", 1)[1].strip()
        if not boundary_token:
            self.send_error(400, "Invalid upload: missing boundary")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        boundary = ("--" + boundary_token).encode()

        parts = body.split(boundary)
        pending_files = []

        for part in parts:
            if not part or part in (b"--", b"--\r\n"):
                continue

            part = part.lstrip(b"\r\n")
            header_blob, _, file_content = part.partition(b"\r\n\r\n")
            if not _:
                continue

            # Remove trailing boundary line endings
            if file_content.endswith(b"\r\n"):
                file_content = file_content[:-2]

            headers = header_blob.decode("latin-1").split("\r\n")
            disposition = next((h for h in headers if h.lower().startswith("content-disposition")), None)
            if not disposition:
                continue

            filename = ""
            field_name = ""
            for token in disposition.split(";"):
                token = token.strip()
                if token.startswith("name="):
                    field_name = token.split("=", 1)[1].strip('"')
                elif token.startswith("filename="):
                    filename = token.split("=", 1)[1].strip('"')

            if field_name != "file" or not filename:
                continue

            safe_name = os.path.basename(filename)
            pending_files.append((safe_name, file_content))

        if not pending_files:
            self.send_error(400, "No files uploaded")
            return

        saved_files = []
        total = len(pending_files)
        for idx, (safe_name, file_content) in enumerate(pending_files, start=1):
            size = len(file_content)
            print(f"[{idx}/{total}] Saving {safe_name} ({size} bytes)...")
            sys.stdout.flush()
            out_path = os.path.join(UPLOAD_DIR, safe_name)
            with open(out_path, "wb") as f:
                f.write(file_content)
            print(f"[{idx}/{total}] Saved {safe_name}")
            sys.stdout.flush()
            saved_files.append(safe_name)


        escaped = [escape(name) for name in saved_files]
        message = (
            f"File '{escaped[0]}' uploaded successfully."
            if len(saved_files) == 1
            else "Files uploaded successfully: " + ", ".join(escaped)
        )

        self.send_response(200)
        self.end_headers()
        self.wfile.write(message.encode())


def get_internal_ip() -> str:
    """
    Best-effort attempt to determine an internal IP for LAN access.
    Uses a UDP socket trick that does not require actual connectivity.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def open_upload_dir():
    """Open the upload directory in the system file browser."""
    try:
        if sys.platform.startswith("darwin"):
            subprocess.run(["open", UPLOAD_DIR], check=False)
        elif os.name == "nt":
            os.startfile(UPLOAD_DIR)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", UPLOAD_DIR], check=False)
    except Exception as exc:  # Log and continue; availability can vary per environment.
        print(f"Could not open upload folder automatically: {exc}")
        sys.stdout.flush()


handler = UploadHandler
try:
    with ReusableTCPServer(("", PORT), handler) as httpd:
        print(f"Serving at http://localhost:{PORT}/upload")
        internal_ip = get_internal_ip()
        if internal_ip:
            print(f"Also reachable at http://{internal_ip}:{PORT}/upload")
        open_upload_dir()
        httpd.serve_forever()
except OSError as exc:
    if exc.errno == 48:
        print(f"Port {PORT} is already in use.")
        print(f"Close the process using port {PORT}, or change PORT in upload_server.py.")
        print(f"Tip (macOS): lsof -nP -iTCP:{PORT} -sTCP:LISTEN")
    else:
        raise
