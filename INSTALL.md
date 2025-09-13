# Image Vault - Installation Guide

## Quick Installation

1. **Clone or download the project** to your desired location

2. **Create a virtual environment** (recommended):
   ```bash
   python -m venv .venv
   ```

3. **Activate the virtual environment**:
   - **Linux/macOS**: 
     ```bash
     source .venv/bin/activate
     ```
   - **Windows**: 
     ```bash
     .venv\Scripts\activate
     ```

4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

5. **Run the application**:
   ```bash
   python app.py
   ```

6. **Open your browser** and go to: http://localhost:8001

## First Time Setup

1. Go to **Settings** in the web interface
2. Set your **Vault root folder** (where your images are stored)
3. Click **Scan** to index your images
4. Start organizing with tags!

## Features

- **Image Management**: View, organize, and tag your images
- **Search & Filter**: Find images by filename, tags, or exclude specific tags
- **Bulk Operations**: Add/remove tags or delete multiple images at once
- **Export**: Copy filtered images to new locations
- **File Manager Integration**: Open image folders directly from the web interface
- **Responsive Design**: Works on desktop and mobile

## System Requirements

- Python 3.8+
- Modern web browser
- File system access for image storage

## Optional Dependencies

For development or testing, you can also install:
```bash
pip install pytest httpx  # for testing
```