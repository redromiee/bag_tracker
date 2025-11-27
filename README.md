# Bag Tracker

A web application for scanning bin and bag QR codes, recording timestamps and scan types (FWD/RTO) to Google Sheets.

## Features
- QR code scanning using device camera
- Manual entry option
- Records to Google Sheets automatically
- Works on mobile devices
- Simple FWD/RTO selection interface

## Deployment

This app is deployed on Render and accessible via a public URL.

## Setup

### Prerequisites
- Google Service Account credentials (credentials.json)
- Google Sheet named "Bag Tracker Data"

### Local Development
```bash
# Install dependencies
pip install -r backend/requirements.txt

# Run the server
python backend/main.py
```

Visit http://localhost:8000 to use the app.

## Environment Variables

For deployment, you'll need to set up the Google credentials as an environment variable.
