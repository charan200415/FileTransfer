import os
import logging
from pyrogram import Client, filters, enums
import requests
import tempfile
from urllib.parse import urljoin
from config import API_ID, API_HASH, BOT_TOKEN, API_BASE_URL
import time
import math
import asyncio
import re
from collections import defaultdict
from datetime import datetime, timedelta
import urllib3
from asyncio import Lock

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Initialize the Pyrogram Client first
app = Client(
    "file_sharing_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

# Set timeout for requests
TIMEOUT = 30  # seconds

# User-specific progress tracking
user_progress = defaultdict(dict)

# Rate limiting per user
rate_limit = defaultdict(lambda: {'last_update': 0, 'count': 0})

# Add after other global variables
upload_locks = {}

class RateLimiter:
    def __init__(self, interval=1):
        self.interval = interval
        self.last_check = defaultdict(float)
        
    async def can_proceed(self, user_id):
        now = time.time()
        if now - self.last_check[user_id] < self.interval:
            return False
        self.last_check[user_id] = now
        return True

rate_limiter = RateLimiter(interval=2)

async def progress(current, total, message, start_time, action="Uploading", user_id=None):
    """Progress callback with per-user rate limiting"""
    try:
        if not user_id:
            user_id = message.chat.id
            
        now = time.time()
        
        # Check rate limit for this user
        if not await rate_limiter.can_proceed(user_id):
            return
            
        elapsed_time = now - start_time
        if elapsed_time == 0:
            return

        # Store progress for this user
        user_progress[user_id] = {
            'current': current,
            'total': total,
            'speed': current / elapsed_time,
            'progress': (current * 100) / total
        }
        
        progress_data = user_progress[user_id]
        
        # Format progress bar
        bar_length = 20
        filled_length = int(progress_data['progress'] / 100 * bar_length)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        
        # Calculate ETA
        eta_seconds = (total - current) / progress_data['speed'] if progress_data['speed'] > 0 else 0
        
        text = (
            f"{action} File...\n"
            f"[{bar}] {progress_data['progress']:.1f}%\n"
            f"Size: {format_size(current)}/{format_size(total)}\n"
            f"Speed: {format_size(progress_data['speed'])}/s\n"
            f"ETA: {int(eta_seconds)}s"
        )
        
        try:
            await message.edit_text(text)
        except Exception as e:
            logger.debug(f"Progress update failed: {e}")
            
    except Exception as e:
        logger.error(f"Progress error for user {user_id}: {e}")

# Add this class after other imports
class ProgressTracker:
    def __init__(self, message, action="Uploading"):
        self.message = message
        self.action = action
        self.start_time = time.time()
        self.last_update_time = 0
        self.edit_failed = False
        
    async def update(self, current, total):
        now = time.time()
        # Update only every 1 second
        if now - self.last_update_time < 1:
            return
            
        self.last_update_time = now
        elapsed_time = now - self.start_time
        if elapsed_time == 0:
            return
            
        speed = current / elapsed_time
        progress_percent = (current * 100) / total
        
        # Calculate ETA
        remaining_bytes = total - current
        eta_seconds = remaining_bytes / speed if speed > 0 else 0
        
        # Format progress bar
        bar_length = 20
        filled_length = int(progress_percent / 100 * bar_length)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        
        text = (
            f"{self.action} to server...\n"
            f"[{bar}] {progress_percent:.1f}%\n"
            f"Size: {format_size(current)}/{format_size(total)}\n"
            f"Speed: {format_size(speed)}/s\n"
            f"ETA: {int(eta_seconds)}s"
        )
        
        # If upload is complete, update the message
        if current >= total:
            text = "✅ Upload complete! Processing..."
            
        try:
            if not self.edit_failed:
                try:
                    await self.message.edit_text(text)
                except Exception as e:
                    logger.debug(f"Edit failed, switching to new messages: {e}")
                    self.edit_failed = True
                    # Delete old message
                    try:
                        await self.message.delete()
                    except:
                        pass
                    # Send new message
                    self.message = await self.message.reply_text(text)
            else:
                # Delete old message and send new one
                try:
                    await self.message.delete()
                except:
                    pass
                self.message = await self.message.reply_text(text)
                
        except Exception as e:
            logger.debug(f"Progress update failed: {e}")

class ProgressFile:
    def __init__(self, file, size, progress_callback):
        self.file = file
        self.size = size
        self.progress_callback = progress_callback
        self.uploaded = 0
        
    def read(self, chunk_size=-1):
        data = self.file.read(chunk_size)
        if data:
            self.uploaded += len(data)
            asyncio.create_task(
                self.progress_callback(self.uploaded, self.size)
            )
        return data
        
    def seek(self, offset, whence=0):
        return self.file.seek(offset, whence)
        
    def tell(self):
        return self.file.tell()
        
    def close(self):
        return self.file.close()
        
    def fileno(self):
        return self.file.fileno()
        
    def readable(self):
        return True
        
    def seekable(self):
        return True
        
    def writable(self):
        return False

# Modify document handler for concurrent processing
@app.on_message(filters.document)
async def handle_document(client, message):
    user_id = message.from_user.id
    
    # Check if user is already uploading
    if user_id in upload_locks:
        await message.reply_text("⚠️ Please wait for your current upload to finish.")
        return
        
    upload_locks[user_id] = Lock()
    
    try:
        async with upload_locks[user_id]:
            status_msg = await message.reply_text("Starting file processing...")
            
            # Create user-specific temp directory
            user_temp_dir = os.path.join("temp", str(user_id))
            os.makedirs(user_temp_dir, exist_ok=True)
            
            # Get original filename
            original_filename = message.document.file_name
            safe_filename = sanitize_filename(original_filename)
            file_path = os.path.join(user_temp_dir, safe_filename)
            
            try:
                # Download with user-specific progress tracking
                start_time = time.time()
                await message.download(
                    file_name=file_path,
                    progress=progress,
                    progress_args=(status_msg, start_time, "Downloading", user_id)
                )
                
                # Get file size for upload progress
                file_size = os.path.getsize(file_path)
                progress_tracker = ProgressTracker(status_msg, "Uploading")
                
                with open(file_path, 'rb') as f:
                    # Create a wrapper for the file to track upload progress
                    progress_file = ProgressFile(f, file_size, progress_tracker.update)
                    files = {'file': (original_filename, progress_file)}
                    
                    try:
                        response = requests.post(
                            f"{API_BASE_URL}/upload/",
                            files=files,
                            params={"user_id": str(user_id)},
                            timeout=60,
                            verify=False
                        )
                    
                        if response.status_code == 200:
                            result = response.json()
                            file_url = urljoin(API_BASE_URL, result['access_code'])
                            
                            # Send a new message instead of editing
                            await status_msg.delete()
                            await message.reply_text(
                                f"✅ File uploaded successfully!\n\n"
                                f"📄 Filename: {original_filename}\n"
                                f"🔑 Access Code: <code>{result['access_code']}</code>\n"
                                f"🔗 Direct Link: {file_url}\n\n"
                                f"Anyone with this link can download the file.",
                                parse_mode=enums.ParseMode.HTML
                            )
                        else:
                            error_msg = response.json().get('detail', 'Unknown error')
                            await message.reply_text(f"❌ Upload failed: {error_msg}")
                            
                    except Exception as upload_error:
                        logger.error(f"Upload error detail: {upload_error}")
                        await message.reply_text("❌ Upload failed at final stage. Please try again.")
                        
            except Exception as e:
                logger.error(f"File handling error: {e}")
                await message.reply_text("❌ Error processing file. Please try again.")
                
    except Exception as e:
        logger.error(f"Error for user {user_id}: {e}")
        await message.reply_text("❌ Sorry, something went wrong. Please try again.")
    finally:
        # Cleanup user-specific temp files
        try:
            if os.path.exists(user_temp_dir):
                for file in os.listdir(user_temp_dir):
                    os.remove(os.path.join(user_temp_dir, file))
                os.rmdir(user_temp_dir)
        except Exception as e:
            logger.error(f"Cleanup error for user {user_id}: {e}")
            
        # Remove the lock
        if user_id in upload_locks:
            del upload_locks[user_id]

def format_size(size):
    """Format size in bytes to human readable format"""
    units = ['B', 'KB', 'MB', 'GB']
    size = float(size)
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    return f"{size:.2f} {units[unit]}"

@app.on_message(filters.command("start"))
async def start_command(client, message):
    """Handle the /start command"""
    await message.reply_text(
        'Hi! I can help you share files.\n'
        'Just send me any file and I will give you a link to share it.\n\n'
        'Commands:\n'
        '/start - Show this help message\n'
        '/list - List all uploaded files\n'
        '/delete <code> - Delete a file using access code\n'
        '/stats - View your usage statistics\n\n'
        '💡 You can also send me an access code directly to get the file!'
    )

@app.on_message(filters.command("list"))
async def list_command(client, message):
    """Handle the /list command"""
    try:
        # Get user's Telegram ID
        user_id = str(message.from_user.id)
        
        # Get only this user's files
        response = requests.get(f"{API_BASE_URL}/files/{user_id}")
        files = response.json()
        
        if not files['files']:
            await message.reply_text("📂 You haven't uploaded any files yet.")
            return

        async def send_long_message(text, parse_mode=None):
            MAX_LENGTH = 4000
            messages = []
            current_msg = "📂 Your Files:\n\n"
            
            for line in text.split('\n'):
                if len(current_msg + line + '\n') > MAX_LENGTH:
                    messages.append(current_msg)
                    current_msg = "📂 Your Files (continued):\n\n" + line + '\n'
                else:
                    current_msg += line + '\n'
            
            if current_msg:
                messages.append(current_msg)
            
            for i, msg_text in enumerate(messages, 1):
                if len(messages) > 1:
                    msg_text += f"\n📃 Page {i}/{len(messages)}"
                await message.reply_text(msg_text, parse_mode=parse_mode)

        # Prepare combined message with better formatting
        files_msg = ""
        for i, file in enumerate(files['files'], 1):
            file_url = urljoin(API_BASE_URL, file['access_code'])
            files_msg += f"<b>{i}. {file['filename']}</b>\n"
            files_msg += f"   ├─ 🔗 <a href='{file_url}'>Direct Link</a>\n"
            files_msg += f"   └─ 🔑 Code: <code>{file['access_code']}</code>\n\n"

        # Send message with HTML formatting
        await send_long_message(files_msg, parse_mode=enums.ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error listing files: {e}")
        await message.reply_text("❌ Sorry, couldn't fetch your files.")

def sanitize_filename(filename):
    """Remove invalid characters from filename"""
    # Remove invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Remove any leading/trailing spaces and dots
    filename = filename.strip('. ')
    # If filename is empty after sanitization, use a default name
    if not filename:
        filename = 'downloaded_file'
    return filename

@app.on_message(filters.command("delete"))
async def delete_command(client, message):
    """Handle the /delete command"""
    try:
        # Check if access code is provided
        command_parts = message.text.split()
        if len(command_parts) != 2:
            await message.reply_text(
                "❌ Please provide an access code.\n"
                "Usage: /delete <access_code>"
            )
            return
            
        user_id = str(message.from_user.id)
        access_code = command_parts[1]
        
        # Try to delete the file
        response = requests.delete(
            f"{API_BASE_URL}/delete/{access_code}",
            params={"user_id": user_id},  # Add user_id to verify ownership
            timeout=TIMEOUT,
            verify=False
        )
        
        if response.status_code == 200:
            await message.reply_text("✅ File deleted successfully!")
        else:
            error_msg = response.json().get('detail', 'Unknown error')
            await message.reply_text(f"❌ Error: {error_msg}")
            
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        await message.reply_text("❌ Sorry, couldn't delete the file.")

@app.on_message(filters.command(["stats", "stats@your_bot_username"]))  # Add your bot's username
async def stats_command(client, message):
    """Handle the /stats command"""
    print("stats command received")  # Debug print
    
    # Check if it's a private chat
    if message.chat.type != enums.ChatType.PRIVATE:
        await message.reply_text("Please use this command in private chat.")
        return
        
    try:
        user_id = str(message.from_user.id)
        print(f"Processing stats for user_id: {user_id}")  # Debug print
        
        # Get stats from server
        response = requests.get(
            f"{API_BASE_URL}/stats/{user_id}", 
            timeout=TIMEOUT,
            verify=False
        )
        print(f"Server response: {response.text}")  # Debug print
        
        if response.status_code == 200:
            stats = response.json()
            
            # Format the statistics message with better error handling
            uploads = stats.get('uploads', 0)
            downloads = stats.get('downloads', 0)
            bytes_uploaded = stats.get('bytes_uploaded', 0)
            bytes_downloaded = stats.get('bytes_downloaded', 0)
            last_activity = stats.get('last_activity', 'No activity')
            
            stats_msg = (
                "📊 Your File Sharing Statistics\n\n"
                f"📤 Uploads: {uploads}\n"
                f"📥 Downloads: {downloads}\n"
                f"📈 Total Uploaded: {format_size(bytes_uploaded)}\n"
                f"📉 Total Downloaded: {format_size(bytes_downloaded)}\n"
                f"🕒 Last Activity: {last_activity}"
            )
            
            await message.reply_text(stats_msg)
        else:
            logger.error(f"Stats error: {response.text}")  # Add error logging
            await message.reply_text("❌ Couldn't fetch your statistics.")
            
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        await message.reply_text("❌ Sorry, something went wrong while fetching your statistics.")

@app.on_message(filters.text & filters.private & ~filters.via_bot & ~filters.forwarded)
async def handle_text(client, message):
    """Handle text messages as potential access codes"""
    if message.text.startswith('/'):
        return
        
    try:
        user_id = str(message.from_user.id)
        access_code = message.text.strip()
        if len(access_code) != 8:
            return

        status_msg = await message.reply_text("🔍 Fetching file...")
        
        response = requests.get(
            f"{API_BASE_URL}/download/{access_code}", 
            stream=True, 
            timeout=TIMEOUT
        )
        
        if response.status_code == 200:
            # Get and sanitize filename from headers
            content_disposition = response.headers.get('content-disposition', '')
            if 'filename=' in content_disposition:
                filename = content_disposition.split('filename=')[-1].strip('"\'')
                # URL decode the filename
                filename = requests.utils.unquote(filename)
            else:
                filename = f"file_{access_code}"
            
            # Sanitize the filename
            filename = sanitize_filename(filename)
            
            # Save file temporarily
            temp_path = os.path.join("temp", filename)
            os.makedirs("temp", exist_ok=True)
            
            try:
                #Download file with progress
                total_size = int(response.headers.get('content-length', 0))
                current_size = 0
                start_time = time.time()
                
                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            current_size += len(chunk)
                            await progress(
                                current_size,
                                total_size,
                                status_msg,
                                start_time,
                                "Downloading"
                            )
                
                # Send file to user
                await status_msg.edit_text("📤 Sending file to you...")
                await message.reply_document(
                    temp_path,
                    caption=f"📄 File fetched using access code: {access_code}"
                )
                await status_msg.delete()
                
                # Log the download
                file_size = int(response.headers.get('content-length', 0))
                requests.post(
                    f"{API_BASE_URL}/log_download",
                    json={
                        "user_id": user_id,
                        "file_size": file_size,
                        "filename": filename
                    },
                    timeout=TIMEOUT
                )
                
            finally:
                # Clean up
                try:
                    os.remove(temp_path)
                except:
                    pass
                    
        else:
            error_msg = response.json().get('detail', 'Unknown error')
            await status_msg.edit_text(f"❌ Invalid access code or file not found")
            
    except Exception as e:
        logger.error(f"Error fetching file: {e}")
        await message.reply_text("❌ Sorry, something went wrong while fetching the file.")



def main():
    """Start the bot"""
    print("Starting bot...")
    app.run()

if __name__ == '__main__':
    main() 
