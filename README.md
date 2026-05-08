# Golf Club Automation API

FastAPI backend for automating Club Caddie GMS tee-sheet lookup and tee-time booking through the Windows desktop application.

The API controls the installed Club Caddie app using Windows UI automation and OCR. It can:

- Query available/booked tee times for a date.
- Book a tee time for Front or Back.
- Select 9 or 18 holes.
- Fill 1 to 4 player rows.
- Run in `dry_run` mode to fill the form without pressing Reserve.

## Requirements

- Windows machine with an interactive desktop session.
- Club Caddie GMS installed.
- Python 3.12 recommended.
- Tesseract OCR installed separately.
- The desktop must stay logged in and unlocked while automation is running.

Default application paths used by the project:

```text
Club Caddie:
C:\Program Files\Club Caddie GMS, Inc\Club Caddie GMS\POSApp.exe

Tesseract:
C:\Program Files\Tesseract-OCR\tesseract.exe
```

`pip install -r requirements.txt` installs the Python libraries only. It does not install the Tesseract Windows application.

## Setup

Create and activate the Python environment:

```powershell
conda create --name gca python=3.12 -y
conda activate gca
pip install -r requirements.txt
```

Install Tesseract OCR for Windows. If installed in the default location, no extra config is needed.

Optional: create a `.env` file in the project root to override defaults:

```env
GCA_CLUB_CADDIE_EXECUTABLE=C:\Program Files\Club Caddie GMS, Inc\Club Caddie GMS\POSApp.exe
GCA_TESSERACT_EXECUTABLE=C:\Program Files\Tesseract-OCR\tesseract.exe
GCA_CLUB_CODE=CC18
GCA_USERNAME=ashwin
GCA_PASSWORD=0000
GCA_WINDOW_WAIT_SECONDS=60
```

## Windows Date And Time Region Settings

For the most reliable automation, use the same Windows regional format on local machines and EC2.

Recommended Windows setup:

1. Open **Settings**.
2. Go to **Time & language**.
3. Open **Language & region**.
4. Set **Regional format** to **English (India)**.
5. Open **Administrative language settings** or **Additional date, time & regional settings**.
6. In **Region > Formats > Additional settings**, use these values:

```text
Short date: dd-MM-yyyy
Long date: dd MMMM yyyy
Short time: h:mm tt
Long time: h:mm:ss tt
AM symbol: AM
PM symbol: PM
```

This matches the EC2-style date format used during testing, for example `06-10-2026` and `06 October 2026`.

After changing region settings, restart Club Caddie and restart the FastAPI server.

API payloads do not use the Windows display date format. Always send:

```text
date: YYYY-MM-DD
time: h:mm AM/PM
```

Example:

```json
{
  "date": "2026-10-06",
  "time": "6:52 AM"
}
```

The automation can read common Windows display formats such as `10/6/2026`, `06-10-2026`, `06-Oct-2026`, and 24-hour visible times like `17:40`, but the API request time must still include `AM` or `PM`.

## Run The API

Start the backend from the project root:

```powershell
conda activate gca
uvicorn app.main:app --reload
```

Open Swagger UI:

```text
http://localhost:8000/docs
```

Health check:

```text
GET http://localhost:8000/health
```

## Query Tee Sheet

Endpoint:

```text
POST /tee-sheet/query
```

Example payload:

```json
{
  "date": "2026-10-06"
}
```

Example response shape:

```json
{
  "date": "2026-10-06",
  "club_code": "CC18",
  "front": [
    {
      "time": "7:00 AM",
      "status": "available",
      "side": "front",
      "raw_text": "7:00 AM | Add",
      "player_or_note": null,
      "source": "screenshot",
      "confidence": 1.0
    }
  ],
  "back": [],
  "extracted_at": "2026-05-07T00:00:00Z",
  "warnings": []
}
```

Slot statuses can be:

- `available`
- `booked`
- `blocked`
- `unknown`

## Book Tee Time

Endpoint:

```text
POST /tee-sheet/book
```

Request rules:

- `side` must be `front` or `back`.
- `time` must include `AM` or `PM`.
- `holes` must be `9` or `18`.
- `players` must contain 1 to 4 players.
- `email` is optional.
- `dry_run: true` fills the booking modal but does not press Reserve.
- `dry_run: false` submits the real booking.

Example 4-player payload:

```json
{
  "date": "2026-10-05",
  "side": "front",
  "time": "8:20 AM",
  "holes": 18,
  "players": [
    {
      "first_name": "Ethan",
      "last_name": "Brooks",
      "email": "ethan.brooks@example.com",
      "mobile_number": "5550101001"
    },
    {
      "first_name": "Maya",
      "last_name": "Patel",
      "email": "maya.patel@example.com",
      "mobile_number": "5550101002"
    },
    {
      "first_name": "Lucas",
      "last_name": "Reed",
      "email": "lucas.reed@example.com",
      "mobile_number": "5550101003"
    },
    {
      "first_name": "Nora",
      "last_name": "Kim",
      "email": "nora.kim@example.com",
      "mobile_number": "5550101004"
    }
  ],
  "dry_run": false
}
```

Successful dry-run response:

```json
{
  "date": "2026-10-06",
  "side": "front",
  "time": "6:36 AM",
  "holes": 18,
  "player_count": 4,
  "status": "unknown",
  "message": "Booking form filled successfully; dry_run skipped Reserve.",
  "warnings": [
    "dry_run was enabled, so Reserve was not clicked."
  ]
}
```

Successful real booking response:

```json
{
  "date": "2026-10-05",
  "side": "front",
  "time": "8:20 AM",
  "holes": 18,
  "player_count": 4,
  "status": "reserved",
  "message": "Reservation submitted successfully.",
  "warnings": []
}
```

## Recommended Booking Workflow

1. Call `/tee-sheet/query` for the date.
2. Pick an available slot from `front` or `back`.
3. Call `/tee-sheet/book` with `dry_run: true`.
4. Confirm the modal is filled correctly.
5. Call `/tee-sheet/book` again with `dry_run: false` only when ready to submit.

## Important Runtime Notes

- Keep the Windows desktop session active and unlocked.
- Do not run multiple booking/query requests at the same time. The backend has an automation lock, but the Club Caddie UI itself is still single-user.
- Fixed display scaling is recommended, ideally 100%.
- OCR is used as a fallback for tee-sheet grid extraction and Add-button detection.
- If Club Caddie shows update dialogs, the automation attempts to close them.
- If the app is already on Tee Sheet, Register, Home, or Login, the automation attempts to recover and navigate to the needed screen.

## AWS Windows EC2 Notes

This can run on Windows EC2, but it needs a real interactive desktop session.

Recommended:

- Use Windows Server EC2.
- Install Club Caddie and verify it works manually.
- Install Python dependencies and Tesseract.
- Keep a user logged in.
- Start FastAPI from that user desktop session.
- Avoid locked/disconnected/headless execution for the automation process.

The most common production issue is not Python. It is Windows GUI automation losing access to the desktop session.

## Troubleshooting

If OCR fails:

- Confirm this file exists:

```text
C:\Program Files\Tesseract-OCR\tesseract.exe
```

- Or set `GCA_TESSERACT_EXECUTABLE` in `.env`.

If Club Caddie cannot launch:

- Confirm this file exists:

```text
C:\Program Files\Club Caddie GMS, Inc\Club Caddie GMS\POSApp.exe
```

- Or set `GCA_CLUB_CADDIE_EXECUTABLE` in `.env`.

If booking submits the wrong side/time/holes:

- Test first with `dry_run: true`.
- Confirm `time` includes `AM` or `PM`.
- Confirm `side` is exactly `front` or `back`.
- Confirm `holes` is `9` or `18`.

If API returns `503 Service Unavailable`:

- Read the `detail` field in the response.
- Check that Club Caddie is open and not blocked by a popup.
- Make sure the desktop is not locked.
- Retry with `dry_run: true` before real booking.