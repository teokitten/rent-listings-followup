# Rent Listings Follow-Up

A local web app for tracking apartment listings during a rental search, designed for the Czech market but usable for any country with some [limitations](#limitations).

[Try the live demo](https://teokitten.github.io/rent-listings-followup/demo.html) – all data is fictional and resets on refresh.

---

## What you can do

**Add and manage listings**
- Add listings manually by URL – the app fetches the title and photos automatically from sreality.cz and idnes.cz
- Bulk-import listings and agent updates by pasting raw WhatsApp conversation text – the parser strips timestamps, extracts URLs, detects statuses from context, and previews what it will import before committing
- Deduplicate by URL – the app warns you if a listing already exists before adding it again

**Track and update status**
- Assign statuses across two categories:
  - **Active** (listings still in play): **To contact, Try again, Submitted application, Waiting for current tenant, On hold, To review, Leave as backup, Scheduled viewing**
  - **Closed** (listings no longer being pursued): **Rented out, Reserved, Missing requirements, Foreigners not preferred, In-person viewings only, Dropped after viewing, Stale – no answer**
- Change status directly on the card

**Add and edit notes**
- Add timestamped notes to any listing
- Edit or delete individual notes inline
- Notes are stored as log entries with Prague timezone timestamps

**Schedule viewings**
- Set a viewing date and time (15-minute increments) on any listing marked as **Scheduled viewing**
- The **Scheduled visits** shortcut shows a monthly calendar with viewing dates highlighted and a sorted list below, grouped by month, with an optional comment per viewing

**Organize with shortcuts**
- Built-in shortcuts: **Scheduled visits**, **Favorites**, **Backups**, **To review**
- Create custom shortcut folders, rename them (click the pencil icon on hover), delete them (click ✕ on hover)
- Drag any listing card from the main grid onto a folder to add it
- Remove a listing from a folder without affecting the main data

**Filter listings**
- Filter by free text (matches title, URL, notes, status label)
- Filter by status with a two-column dropdown separating **Active** and **Closed**
- Filters persist while you navigate and carry over to PDF export

**Export and restore data**
- Export all data as JSON (**Backup data**)
- Restore from a JSON backup (**Restore backup**) with a confirmation step
- Export the current selection to PDF

**More**
- Favorite any listing with a heart button – favorites appear in the **Favorites** shortcut
- Click any listing title to open it in a new tab
- Click the apartment photo to open a full-screen lightbox
- Prague metro map overlay – zoom and drag to navigate
- All changes are saved immediately – no save button

---

## Prerequisites

- Python 3.8 or later

  <details>
  <summary>How to install Python</summary>

  **Linux:** `sudo apt install python3` (Debian/Ubuntu) or `sudo dnf install python3` (Fedora)

  **macOS:** `brew install python` or download from [python.org](https://www.python.org/downloads/)

  **Windows:** Download the installer from [python.org](https://www.python.org/downloads/) and check "Add Python to PATH" during setup.
  </details>

- pip (included with Python 3.4+)

  <details>
  <summary>How to install pip if missing</summary>

  ```bash
  python3 -m ensurepip --upgrade
  ```
  </details>

---

## Installation

1. Clone the repository:
```bash
   git clone https://github.com/teokitten/rent-listings-followup
   cd rent-listings-followup
```

2. Install Flask:
```bash
   pip install flask --break-system-packages
```

3. Start the app:
```bash
   python3 app.py
```

4. Open `http://localhost:5000` in your browser.

To run the demo without the server:

1. Start a local server:
```bash
   python3 -m http.server 5001
```

2. Open `http://localhost:5001/demo.html`.

---

## Limitations

- **Local only** – the app runs on your machine and is not designed for hosting or multi-user access
- **JSON storage** – all data lives in `data/listings.json`; concurrent edits from multiple tabs are safe (the backend uses a threading lock and unique temp files), but the file is not a database and has no versioning
- **Custom shortcuts** – stored in the browser's `localStorage`, so they are per-browser and do not follow you to another device or browser profile
- **Site support** – automatic title and photo extraction works on sreality.cz and idnes.cz; other listing sites will add the URL without a photo. The bulk parser is optimized for WhatsApp conversation format and has been tested against Czech listing sites only.
- **No mobile layout** – the four-column grid is designed for a desktop browser

---

## Tech

Flask · vanilla JS · single HTML file · JSON flat file storage

---

Built by [Teo Moldovanu](https://github.com/teokitten) · MIT License
