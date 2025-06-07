import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # YouTube API
    YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
    YOUTUBE_API_URL = 'https://www.googleapis.com/youtube/v3/channels'
    
    # Database
    # SQLALCHEMY_DATABASE_URI = f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST')}/{os.getenv('POSTGRES_DB')}"
    SQLALCHEMY_DATABASE_URI = 'postgresql://postgres:fame007dav@34.29.233.211/youtube'
    SQLALCHEMY_TRACK_MODIFICATIONS = False