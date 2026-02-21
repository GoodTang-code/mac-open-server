# UploadServer

Simple local web server for uploading files from desktop or mobile browsers to your machine.

## Features
- Lightweight single-file Python server (`upload_server.py`)
- Mobile-friendly upload page at `/upload`
- Supports multiple files in one request
- Accepts file picker types: `video/mp4`, `video/quicktime`, `image/*`
- Saves uploads to the local `uploaded/` folder
- Prints progress logs for each saved file
- Shows both localhost and LAN URL on startup

## Requirements
- Python 3.8+ (standard library only)

No external dependencies are required.

## Quick Start
1. Open a terminal in the project directory.
2. Run:

```bash
python3 upload_server.py
```

3. Open one of the URLs printed in the terminal:
- `http://localhost:8001/upload`
- `http://<your-lan-ip>:8001/upload`

4. Select one or more files and click **Upload**.

Uploaded files are written to:
- `uploaded/`

## Project Structure
- `upload_server.py`: HTTP server + upload form + multipart parsing/saving
- `uploaded/`: destination folder for uploaded files (created automatically)

## Notes and Current Behavior
- The server listens on port `8001`.
- On startup, it attempts to open `uploaded/` in your system file browser.
- Filenames are sanitized with `os.path.basename` before saving.
- If a file with the same name already exists, it is overwritten.
- This is intended for trusted/local-network use only.

## Troubleshooting
- **Port already in use**: change `PORT` in `upload_server.py`.
- **Cannot access from phone**:
  - Ensure phone and computer are on the same network.
  - Use the LAN URL printed in terminal.
  - Allow Python through your firewall if needed.
- **Upload fails with "Invalid upload"**:
  - Use the `/upload` page form directly.
  - Confirm the request is `multipart/form-data`.

## License
See `LICENSE`.
