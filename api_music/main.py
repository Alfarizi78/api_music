from fastapi import FastAPI, HTTPException, Query, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import yt_dlp
from typing import List, Dict
from pydantic import BaseModel
from functools import lru_cache
import asyncio
from datetime import datetime, timedelta
import logging

# Initialize FastAPI and Limiter
app = FastAPI()
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Update CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Ganti dengan domain Flutter app Anda nanti
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Song(BaseModel):
    title: str
    url: str
    thumbnail: str
    duration: str
    video_id: str  # Menambahkan video_id

class SongResponse(BaseModel):
    artist_url: str
    artist_name: str
    songs: List[Song]

# Tambahkan daftar URL artis
ARTIST_URLS = [
    #Lagu Barat
    "https://music.youtube.com/channel/UCJls2FMEbRYxi28jcuKe2vA",  # Avenged Seven Fold
    "https://music.youtube.com/channel/UC527A_XB_c7XftocVOIVNeA",  # MCR
    "https://music.youtube.com/channel/UCRI-Ds5eY70A4oeHggAFBbg",  # Rex Orange Country
    "https://music.youtube.com/channel/UCZn4r7heNOPY-C43YIywnVA",   # Bruno Mars
    #======================================================================================
    #Lagu Indonesia
    "https://music.youtube.com/channel/UCn0hl0XZ3bFREX2SCBZK3Pw",  # Dewa 19
    "https://music.youtube.com/channel/UCYBtTmBP2QgHgalgsv2v5LA",  # Juicy Luicy
    "https://music.youtube.com/channel/UCUn9Xjvg8fwqpa58-_XO6zw"  # Bernadya
    
]

# Cache untuk menyimpan hasil fetch songs
CACHE_TIMEOUT = timedelta(minutes=30)
song_cache: Dict[str, tuple] = {}

# Optimasi yt-dlp options
YDL_OPTS = {
    'quiet': True,
    'extract_flat': True,
    'force_generic_extractor': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'no_warnings': True,
    'socket_timeout': 30,  # Tambahkan timeout
    'retries': 3          # Tambahkan retries
}

# Optimasi streaming options
STREAM_OPTS = {
    'format': 'bestaudio/best',
    'quiet': True,
    'extract_info': True,
    'no_warnings': True,
    'nocheckcertificate': True,
}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Tambahkan error handling yang lebih detail
@app.get("/get_artist_songs", response_model=List[SongResponse])
@limiter.limit("100/minute")
async def get_artist_songs(request: Request):
    responses = []
    
    try:
        for artist_url in ARTIST_URLS:
            try:
                songs = []
                with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                    print(f"Fetching songs from: {artist_url}")
                    result = ydl.extract_info(artist_url, download=False)
                    
                    if not result:
                        print(f"No results found for {artist_url}")
                        continue

                    # Get artist name from channel info
                    artist_name = result.get('channel', 'Unknown Artist')
                    
                    if 'entries' in result:
                        for entry in result['entries']:
                            if not entry:
                                continue
                                
                            video_id = entry.get('id', '')
                            if not video_id:
                                continue

                            song = Song(
                                title=entry.get('title', ''),
                                url=f"https://music.youtube.com/watch?v={video_id}",
                                thumbnail=entry.get('thumbnail', '') or f"https://i.ytimg.com/vi/{video_id}/default.jpg",
                                duration=str(entry.get('duration', '0')),
                                video_id=video_id
                            )
                            songs.append(song)
                    
                    responses.append(SongResponse(
                        artist_url=artist_url,
                        artist_name=artist_name,
                        songs=songs
                    ))
            except Exception as artist_error:
                print(f"Error fetching artist {artist_url}: {str(artist_error)}")
                continue
    
    except Exception as e:
        print(f"Fatal error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
    if not responses:
        raise HTTPException(status_code=404, detail="No artists or songs found")
    
    return responses

@app.get("/get_stream_url/{video_id}")
@limiter.limit("100/minute")
async def get_stream_url(request: Request, video_id: str):
    logger.info(f"Fetching stream URL for video ID: {video_id}")
    try:
        stream_url = await fetch_stream_url(video_id)
        return {"stream_url": stream_url}
    except Exception as e:
        logger.error(f"Error fetching stream URL: {str(e)}")
        raise

async def fetch_stream_url(video_id: str) -> str:
    cache_key = f"stream_{video_id}"
    if cache_key in song_cache:
        cached_data, timestamp = song_cache[cache_key]
        if datetime.now() - timestamp < CACHE_TIMEOUT:
            return cached_data

    try:
        with yt_dlp.YoutubeDL(STREAM_OPTS) as ydl:
            info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=False)
            formats = info['formats']
            audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
            best_audio = sorted(audio_formats, key=lambda x: x.get('quality', 0), reverse=True)[0]
            stream_url = best_audio['url']
            
            # Cache the result
            song_cache[cache_key] = (stream_url, datetime.now())
            return stream_url
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/search_songs", response_model=List[Song])
@limiter.limit("100/minute")
async def search_songs(request: Request, query: str = Query(..., description="Search query for songs")):
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            search_query = f"ytsearch10:{query}"  # Batasi 10 hasil
            results = ydl.extract_info(search_query, download=False)
            
            if not results or 'entries' not in results:
                return []

            songs = []
            for entry in results['entries']:
                if not entry:
                    continue
                    
                video_id = entry.get('id', '')
                if not video_id:
                    continue

                song = Song(
                    title=entry.get('title', ''),
                    url=f"https://music.youtube.com/watch?v={video_id}",
                    thumbnail=entry.get('thumbnail', '') or f"https://i.ytimg.com/vi/{video_id}/default.jpg",
                    duration=str(entry.get('duration', '0')),
                    video_id=video_id
                )
                songs.append(song)

            return songs
                
    except Exception as e:
        logger.error(f"Error searching songs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)