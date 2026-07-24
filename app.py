import os
import json
import subprocess
import tempfile
import threading
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
# Allow all origins (lock this down to your Hugging Face Space domain in production)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Helper function to get base yt-dlp options (adds cookies.txt support if available)
def get_base_ydl_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
    }
    # যদি আপনার প্রজেক্ট ফোল্ডারে cookies.txt ফাইল আপলোড করা থাকে, তবে এটি স্বয়ংক্রিয়ভাবে ব্যবহার করবে
    if os.path.exists('cookies.txt'):
        opts['cookiefile'] = 'cookies.txt'
    return opts

# Helper function to normalize URLs (handles Facebook short/share links)
def normalize_url(url):
    # ফেসবুকের শর্ট বা শেয়ার লিংক হ্যান্ডেল করার জন্য
    if 'facebook.com/share/' in url or 'fb.watch/' in url:
        try:
            import urllib.request
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req) as response:
                return response.url
        except Exception:
            pass
    return url

# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'OmniStream AI Backend running', 'version': '1.0.1'}), 200

@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({'status': 'ok'}), 200

# ─────────────────────────────────────────────────────────────────────────────
# YOUTUBE INFO ENDPOINT
# GET /api/info?url=<youtube_url>
# Returns: title, thumbnail, duration, uploader, view_count, and formats list
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/info', methods=['GET'])
def get_info():
    raw_url = request.args.get('url', '').strip()
    if not raw_url:
        return jsonify({'error': 'No URL provided'}), 400
    
    url = normalize_url(raw_url)

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        'skip_download': True,
        'extract_flat': False,
        'format': 'bestvideo+bestaudio/best',
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': str(e)}), 422
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

    if not info:
        return jsonify({'error': 'Could not extract video information'}), 422

    # Build clean formats list
    raw_formats = info.get('formats', [])
    formats = []
    for f in raw_formats:
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        height = f.get('height')
        width = f.get('width')
        ext = f.get('ext', 'mp4')
        fps = f.get('fps')
        filesize = f.get('filesize') or f.get('filesize_approx')
        format_id = f.get('format_id', '')
        format_note = f.get('format_note', '')
        tbr = f.get('tbr')
        abr = f.get('abr')
        vbr = f.get('vbr')
        protocol = f.get('protocol', '')

        # Only include downloadable formats (not dash manifests without extension)
        if protocol in ('m3u8', 'm3u8_native', 'f4m'):
            continue

        # Include video formats with valid height
        if vcodec != 'none' and height:
            formats.append({
                'format_id': format_id,
                'ext': ext,
                'vcodec': vcodec,
                'acodec': acodec,
                'height': height,
                'width': width,
                'fps': round(fps) if fps else None,
                'filesize': filesize,
                'format_note': format_note,
                'tbr': tbr,
                'vbr': vbr,
                'abr': abr,
            })

    # Sort by height descending
    formats.sort(key=lambda x: x.get('height', 0), reverse=True)

    response_data = {
        'id': info.get('id'),
        'title': info.get('title', 'Untitled'),
        'thumbnail': info.get('thumbnail'),
        'duration': info.get('duration'),
        'uploader': info.get('uploader') or info.get('channel'),
        'view_count': info.get('view_count'),
        'like_count': info.get('like_count'),
        'upload_date': info.get('upload_date'),
        'description': (info.get('description') or '')[:300],
        'webpage_url': info.get('webpage_url', url),
        'formats': formats,
    }

    return jsonify(response_data), 200

# ─────────────────────────────────────────────────────────────────────────────
# YOUTUBE VIDEO DOWNLOAD ENDPOINT
# GET /api/download?url=<youtube_url>&format_id=<format_id>
# Streams the video directly to the client
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/download', methods=['GET'])
def download_video():
    raw_url = request.args.get('url', '').strip()
    format_id = request.args.get('format_id', 'bestvideo+bestaudio/best').strip()
    if not raw_url:
        return jsonify({'error': 'No URL provided'}), 400

    url = normalize_url(raw_url)

    # Create a temporary directory for this download
    tmpdir = tempfile.mkdtemp()
    output_template = os.path.join(tmpdir, '%(title)s.%(ext)s')

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        'format': format_id + '+bestaudio/best' if 'bestaudio' not in format_id else format_id,
        'merge_output_format': 'mp4',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }],
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            final_path = ydl.prepare_filename(info)
            
        # Resolve merged output filename
        if not os.path.exists(final_path):
            base = os.path.splitext(final_path)[0]
            for ext in ['mp4', 'mkv', 'webm']:
                candidate = base + '.' + ext
                if os.path.exists(candidate):
                    final_path = candidate
                    break
    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': str(e)}), 422
    except Exception as e:
        return jsonify({'error': f'Download error: {str(e)}'}), 500

    if not os.path.exists(final_path):
        return jsonify({'error': 'Output file not found after download'}), 500

    filename = os.path.basename(final_path)

    def generate():
        try:
            with open(final_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)  # 64KB chunks
                    if not chunk:
                        break
                    yield chunk
        finally:
            # Clean up temp files after streaming
            try:
                os.remove(final_path)
                os.rmdir(tmpdir)
            except Exception:
                pass

    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Content-Type': 'video/mp4',
        'X-Content-Type-Options': 'nosniff',
    }

    return Response(
        stream_with_context(generate()),
        headers=headers,
        direct_passthrough=True
    )

# ─────────────────────────────────────────────────────────────────────────────
# YOUTUBE MP3 EXTRACTION ENDPOINT
# GET /api/mp3?url=<youtube_url>
# Downloads audio and converts to MP3 via ffmpeg, then streams to client
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/mp3', methods=['GET'])
def download_mp3():
    raw_url = request.args.get('url', '').strip()
    if not raw_url:
        return jsonify({'error': 'No URL provided'}), 400

    url = normalize_url(raw_url)
    tmpdir = tempfile.mkdtemp()
    output_template = os.path.join(tmpdir, '%(title)s.%(ext)s')

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            },
            {
                'key': 'FFmpegMetadata',
                'add_metadata': True,
            }
        ],
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            prepared = ydl.prepare_filename(info)
            base = os.path.splitext(prepared)[0]
            final_path = base + '.mp3'
    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': str(e)}), 422
    except Exception as e:
        return jsonify({'error': f'MP3 extraction error: {str(e)}'}), 500

    if not os.path.exists(final_path):
        # Try to find any mp3 in tmpdir
        for fname in os.listdir(tmpdir):
            if fname.endswith('.mp3'):
                final_path = os.path.join(tmpdir, fname)
                break
        else:
            return jsonify({'error': 'MP3 file not found after conversion'}), 500

    filename = os.path.basename(final_path)

    def generate():
        try:
            with open(final_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.remove(final_path)
                os.rmdir(tmpdir)
            except Exception:
                pass

    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Content-Type': 'audio/mpeg',
        'X-Content-Type-Options': 'nosniff',
    }

    return Response(
        stream_with_context(generate()),
        headers=headers,
        direct_passthrough=True
    )

# ─────────────────────────────────────────────────────────────────────────────
# ERROR HANDLERS
# ─────────────────────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({'error': 'Method not allowed'}), 405

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error'}), 500

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
