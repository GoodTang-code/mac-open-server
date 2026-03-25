import cgi
import contextlib
import http.server
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlparse
from html import escape


def _read_non_negative_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"Invalid {name}={raw!r}; falling back to {default}")
        sys.stdout.flush()
        return default
    return max(0, value)


PORT = 8001
# Save uploads into a dedicated "uploaded" folder under the current working directory
UPLOAD_DIR = os.path.join(os.getcwd(), "uploaded")
os.makedirs(UPLOAD_DIR, exist_ok=True)
UPLOAD_READ_TIMEOUT_SECONDS = 120
UPLOAD_STREAM_CHUNK_SIZE = 1024 * 1024
MAX_FILES_PER_REQUEST = _read_non_negative_int_env("UPLOAD_MAX_FILES_PER_REQUEST", 100)
MAX_REQUEST_BYTES = _read_non_negative_int_env("UPLOAD_MAX_REQUEST_BYTES", 0)


class ReusableTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    # Allow restarting quickly without waiting for OS socket timeout.
    allow_reuse_address = True
    # Keep request threads from blocking shutdown.
    daemon_threads = True


class UploadFieldStorage(cgi.FieldStorage):
    """
    Store multipart temp files inside UPLOAD_DIR so we can atomically move
    them into final destination names (instead of copying again).
    """
    def make_file(self):
        if self._binary_file:
            return tempfile.NamedTemporaryFile(mode="wb+", dir=UPLOAD_DIR, delete=False)
        return tempfile.NamedTemporaryFile(
            mode="w+",
            encoding=self.encoding,
            newline="\n",
            dir=UPLOAD_DIR,
            delete=False,
        )


class UploadProgressReader:
    def __init__(self, wrapped, total_bytes: int):
        self._wrapped = wrapped
        self._total_bytes = max(1, total_bytes)
        self._read_bytes = 0
        self._next_report_percent = 5

    def _track(self, data):
        if data:
            self._read_bytes += len(data)
        percent = int((self._read_bytes * 100) / self._total_bytes)
        while percent >= self._next_report_percent and self._next_report_percent <= 100:
            print(
                f"Receiving request body: {self._next_report_percent}% "
                f"({self._read_bytes}/{self._total_bytes} bytes)"
            )
            sys.stdout.flush()
            self._next_report_percent += 5
        return data

    def read(self, size=-1):
        return self._track(self._wrapped.read(size))

    def readline(self, size=-1):
        return self._track(self._wrapped.readline(size))

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


class UploadHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        request_path = urlparse(self.path).path
        if request_path in ('/', '/upload', '/upload/'):
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            page_html = """
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
                      --danger: #b91c1c;
                      --success: #166534;
                      --track: #e2e8f0;
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

                    .limit-note {
                      margin: 0 0 10px 0;
                      font-size: 0.9rem;
                      color: #7c2d12;
                    }

                    form {
                      display: grid;
                      gap: 12px;
                    }

                    .actions {
                      display: grid;
                      grid-template-columns: 1fr auto;
                      gap: 8px;
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

                    button {
                      appearance: none;
                      border: 1px solid var(--border);
                      border-radius: 10px;
                      padding: 12px 14px;
                      background: #fff;
                      color: var(--text);
                      font-weight: 600;
                      font-size: 0.95rem;
                      cursor: pointer;
                      min-height: 44px;
                    }

                    button:disabled,
                    input[type="submit"]:disabled {
                      opacity: 0.55;
                      cursor: not-allowed;
                    }

                    .progress {
                      width: 100%;
                      height: 10px;
                      border-radius: 999px;
                      background: var(--track);
                      overflow: hidden;
                    }

                    .progress > span {
                      display: block;
                      height: 100%;
                      width: 0%;
                      background: linear-gradient(90deg, #0f766e 0%, #14b8a6 100%);
                      transition: width 120ms linear;
                    }

                    .meta {
                      display: flex;
                      justify-content: space-between;
                      align-items: center;
                      gap: 12px;
                      color: var(--muted);
                      font-size: 0.9rem;
                    }

                    .status {
                      margin: 0;
                      min-height: 1.2em;
                      font-size: 0.92rem;
                    }

                    .status.ok {
                      color: var(--success);
                    }

                    .status.err {
                      color: var(--danger);
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
                    <p class="limit-note">Max __MAX_FILES__ files per upload.</p>
                    <form id="upload-form" data-max-files="__MAX_FILES__" enctype="multipart/form-data" method="post" action="/upload">
                      <!-- Explicitly allow picking videos (and images) to keep iOS Safari happy -->
                      <input id="file-input" name="file" type="file" accept="video/mp4,video/quicktime,image/*" multiple />
                      <div class="progress" aria-hidden="true"><span id="progress-fill"></span></div>
                      <div class="meta">
                        <span id="progress-text">Waiting for files...</span>
                        <span id="file-summary">0 file selected</span>
                      </div>
                      <p id="status" class="status" role="status" aria-live="polite"></p>
                      <div class="actions">
                        <input id="upload-btn" type="submit" value="Upload" />
                        <button id="cancel-btn" type="button" disabled>Cancel</button>
                      </div>
                    </form>
                  </main>
                  <script>
                    (function () {
                      var form = document.getElementById("upload-form");
                      var fileInput = document.getElementById("file-input");
                      var uploadBtn = document.getElementById("upload-btn");
                      var cancelBtn = document.getElementById("cancel-btn");
                      var progressFill = document.getElementById("progress-fill");
                      var progressText = document.getElementById("progress-text");
                      var fileSummary = document.getElementById("file-summary");
                      var status = document.getElementById("status");
                      var maxFiles = parseInt(form.getAttribute("data-max-files") || "0", 10) || 0;
                      var activeXhr = null;

                      function setProgress(percent, text) {
                        var safePercent = Math.max(0, Math.min(100, percent || 0));
                        progressFill.style.width = safePercent + "%";
                        progressText.textContent = text;
                      }

                      function setStatus(message, kind) {
                        status.textContent = message || "";
                        status.classList.remove("ok", "err");
                        if (kind === "ok") {
                          status.classList.add("ok");
                        } else if (kind === "err") {
                          status.classList.add("err");
                        }
                      }

                      function updateFileSummary() {
                        var count = fileInput.files ? fileInput.files.length : 0;
                        if (count === 0) {
                          fileSummary.textContent = "0 file selected";
                          return;
                        }
                        var label = count + (count === 1 ? " file selected" : " files selected");
                        if (maxFiles > 0) {
                          label = label + " (max " + maxFiles + ")";
                        }
                        fileSummary.textContent = label;
                      }

                      function setBusy(isBusy) {
                        uploadBtn.disabled = isBusy;
                        fileInput.disabled = isBusy;
                        cancelBtn.disabled = !isBusy;
                      }

                      fileInput.addEventListener("change", function () {
                        var count = fileInput.files ? fileInput.files.length : 0;
                        updateFileSummary();
                        if (maxFiles > 0 && count > maxFiles) {
                          setStatus("Too many files: selected " + count + ", max is " + maxFiles + ".", "err");
                          uploadBtn.disabled = true;
                          setProgress(0, "Reduce number of files before upload");
                          return;
                        }
                        uploadBtn.disabled = false;
                        if (!activeXhr) {
                          setStatus("", "");
                          setProgress(0, "Ready to upload");
                        }
                      });

                      cancelBtn.addEventListener("click", function () {
                        if (activeXhr) {
                          activeXhr.abort();
                        }
                      });

                      form.addEventListener("submit", function (event) {
                        event.preventDefault();
                        if (activeXhr) {
                          return;
                        }

                        var selectedFiles = fileInput.files ? Array.from(fileInput.files) : [];
                        if (selectedFiles.length === 0) {
                          setStatus("Select at least one file first.", "err");
                          return;
                        }
                        if (maxFiles > 0 && selectedFiles.length > maxFiles) {
                          setStatus(
                            "Too many files: selected " + selectedFiles.length + ", max is " + maxFiles + ".",
                            "err"
                          );
                          return;
                        }

                        var formData = new FormData();
                        selectedFiles.forEach(function (file) {
                          formData.append("file", file, file.name);
                          formData.append("file_last_modified_ms", String(file.lastModified || 0));
                        });
                        var xhr = new XMLHttpRequest();
                        activeXhr = xhr;
                        setBusy(true);
                        setStatus("", "");
                        setProgress(0, "Starting upload...");

                        xhr.open("POST", form.action, true);
                        xhr.timeout = 15 * 60 * 1000;
                        xhr.upload.onprogress = function (event) {
                          if (!event.lengthComputable) {
                            progressText.textContent = "Uploading...";
                            return;
                          }
                          var percent = Math.round((event.loaded / event.total) * 100);
                          setProgress(percent, "Uploading " + percent + "%");
                        };
                        xhr.upload.onload = function () {
                          setProgress(100, "Upload sent. Processing on server...");
                        };

                        xhr.onload = function () {
                          var success = xhr.status >= 200 && xhr.status < 300;
                          setBusy(false);
                          activeXhr = null;
                          if (success) {
                            setProgress(100, "Upload complete");
                            setStatus(xhr.responseText || "Uploaded.", "ok");
                            form.reset();
                            updateFileSummary();
                          } else {
                            setStatus("Upload failed (" + xhr.status + "). " + (xhr.responseText || ""), "err");
                          }
                        };

                        xhr.onerror = function () {
                          setBusy(false);
                          activeXhr = null;
                          setStatus("Network error while uploading.", "err");
                        };

                        xhr.ontimeout = function () {
                          setBusy(false);
                          activeXhr = null;
                          setStatus("Upload timed out while waiting for server response.", "err");
                        };

                        xhr.onabort = function () {
                          setBusy(false);
                          activeXhr = null;
                          setProgress(0, "Upload canceled");
                          setStatus("Upload canceled.", "err");
                        };

                        xhr.send(formData);
                      });

                      updateFileSummary();
                      setProgress(0, "Waiting for files...");
                    })();
                  </script>
                </body>
                </html>
            """
            page_html = page_html.replace("__MAX_FILES__", str(MAX_FILES_PER_REQUEST))
            self.wfile.write(page_html.encode("utf-8"))
        else:
            super().do_GET()

    def do_POST(self):
        request_started = time.monotonic()
        content_type = self.headers.get("Content-Type", "")
        media_type, content_type_params = cgi.parse_header(content_type)
        if media_type != "multipart/form-data":
            self.send_error(400, "Invalid upload")
            return

        boundary_token = content_type_params.get("boundary")
        if not boundary_token:
            self.send_error(400, "Invalid upload: missing boundary")
            return

        content_length = self._parse_content_length()
        if content_length is None:
            return

        if content_length <= 0:
            self.send_error(400, "Invalid upload: empty body")
            return

        if MAX_REQUEST_BYTES > 0 and content_length > MAX_REQUEST_BYTES:
            self.send_error(413, f"Request too large (max {MAX_REQUEST_BYTES} bytes)")
            return

        print(
            f"Upload request started from {self.client_address[0]}: "
            f"content_length={content_length} bytes"
        )
        sys.stdout.flush()

        form = self._parse_multipart_form(content_type, content_length)
        if form is None:
            return
        parse_elapsed = time.monotonic() - request_started

        try:
            pending_files = self._extract_file_fields(form)
            file_last_modified_ms = self._extract_last_modified_ms(form)
            if len(pending_files) > MAX_FILES_PER_REQUEST:
                self.send_error(413, f"Too many files in one request (max {MAX_FILES_PER_REQUEST})")
                return

            if not pending_files:
                self.send_error(400, "No files uploaded")
                return

            saved_files = []
            total = len(pending_files)
            save_started = time.monotonic()
            for idx, file_part in enumerate(pending_files, start=1):
                original_name = os.path.basename(file_part.filename or "")
                if not original_name:
                    continue

                print(f"[{idx}/{total}] Saving {original_name}...")
                sys.stdout.flush()
                last_modified_ms = (
                    file_last_modified_ms[idx - 1]
                    if idx - 1 < len(file_last_modified_ms)
                    else None
                )
                try:
                    stored_name, size = self._save_file_part(
                        original_name,
                        file_part.file,
                        client_last_modified_ms=last_modified_ms,
                    )
                except OSError as exc:
                    print(f"[{idx}/{total}] Failed to save {original_name}: {exc}")
                    sys.stdout.flush()
                    self.send_error(500, "Failed to save uploaded file")
                    return

                print(f"[{idx}/{total}] Saved {stored_name} ({size} bytes)")
                sys.stdout.flush()
                saved_files.append(stored_name)
            save_elapsed = time.monotonic() - save_started
        finally:
            with contextlib.suppress(Exception):
                form.close()

        if not saved_files:
            self.send_error(400, "No files uploaded")
            return


        escaped = [escape(name) for name in saved_files]
        message = (
            f"File '{escaped[0]}' uploaded successfully."
            if len(saved_files) == 1
            else "Files uploaded successfully: " + ", ".join(escaped)
        )
        body = message.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        total_elapsed = time.monotonic() - request_started
        print(
            f"Upload finished: files={len(saved_files)}, "
            f"parse={parse_elapsed:.2f}s, save={save_elapsed:.2f}s, total={total_elapsed:.2f}s"
        )
        sys.stdout.flush()

    def _parse_content_length(self):
        try:
            return int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid upload: bad content length")
            return None

    def _parse_multipart_form(self, content_type: str, content_length: int):
        previous_timeout = self.connection.gettimeout()
        self.connection.settimeout(UPLOAD_READ_TIMEOUT_SECONDS)
        try:
            progress_reader = UploadProgressReader(self.rfile, content_length)
            return UploadFieldStorage(
                fp=progress_reader,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                    "CONTENT_LENGTH": str(content_length),
                },
                keep_blank_values=False,
            )
        except socket.timeout:
            self.send_error(408, "Upload timeout while receiving request body")
            return None
        except (ValueError, EOFError):
            self.send_error(400, "Invalid multipart upload payload")
            return None
        except Exception as exc:
            print(f"Upload parse failed: {exc}")
            sys.stdout.flush()
            self.send_error(400, "Invalid multipart upload payload")
            return None
        finally:
            self.connection.settimeout(previous_timeout)

    def _extract_file_fields(self, form: cgi.FieldStorage):
        if "file" not in form:
            return []
        files = form["file"]
        if isinstance(files, list):
            return [field for field in files if getattr(field, "filename", None) and getattr(field, "file", None)]
        if getattr(files, "filename", None) and getattr(files, "file", None):
            return [files]
        return []

    def _extract_last_modified_ms(self, form: cgi.FieldStorage):
        if "file_last_modified_ms" not in form:
            return []
        raw_values = form["file_last_modified_ms"]
        fields = raw_values if isinstance(raw_values, list) else [raw_values]
        values = []
        for field in fields:
            raw = getattr(field, "value", None)
            try:
                value = int(raw)
            except (TypeError, ValueError):
                values.append(None)
                continue
            values.append(value if value > 0 else None)
        return values

    def _reserve_output_path(self, original_name: str):
        base_name = original_name.replace("\x00", "").strip()
        if not base_name:
            base_name = "upload.bin"

        stem, ext = os.path.splitext(base_name)
        suffix = 1
        while True:
            candidate = base_name if suffix == 1 else f"{stem}_{suffix - 1}{ext}"
            out_path = os.path.join(UPLOAD_DIR, candidate)
            try:
                fd = os.open(out_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.close(fd)
                return out_path, candidate
            except FileExistsError:
                pass
            suffix += 1

    def _save_file_part(self, original_name: str, source_file, client_last_modified_ms=None):
        out_path, final_name = self._reserve_output_path(original_name)
        parser_temp_path = self._maybe_parser_temp_path(source_file)
        try:
            if parser_temp_path:
                with contextlib.suppress(Exception):
                    source_file.flush()
                total_bytes = os.path.getsize(parser_temp_path)
                os.replace(parser_temp_path, out_path)
                self._apply_client_modified_time(out_path, client_last_modified_ms)
                return final_name, total_bytes

            temp_name = f".{final_name}.{uuid.uuid4().hex}.part"
            temp_path = os.path.join(UPLOAD_DIR, temp_name)
            total_bytes = 0
            with open(temp_path, "wb") as out_file:
                while True:
                    chunk = source_file.read(UPLOAD_STREAM_CHUNK_SIZE)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    total_bytes += len(chunk)
            os.replace(temp_path, out_path)
            self._apply_client_modified_time(out_path, client_last_modified_ms)
            return final_name, total_bytes
        except Exception:
            if parser_temp_path:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(parser_temp_path)
            with contextlib.suppress(FileNotFoundError):
                os.remove(out_path)
            raise

    def _maybe_parser_temp_path(self, source_file):
        source_name = getattr(source_file, "name", None)
        if not isinstance(source_name, str):
            return None
        abs_source = os.path.abspath(source_name)
        upload_root = os.path.abspath(UPLOAD_DIR)
        if os.path.dirname(abs_source) != upload_root:
            return None
        if not os.path.exists(abs_source):
            return None
        return abs_source

    def _apply_client_modified_time(self, path: str, client_last_modified_ms):
        if client_last_modified_ms is None:
            return
        try:
            modified_ts = float(client_last_modified_ms) / 1000.0
        except (TypeError, ValueError):
            return
        if modified_ts <= 0:
            return
        now = time.time()
        if modified_ts > now + 86400:
            return
        with contextlib.suppress(OSError, OverflowError, ValueError):
            os.utime(path, (modified_ts, modified_ts))


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
