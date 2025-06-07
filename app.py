
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import requests
from datetime import datetime
import pandas as pd
import plotly.express as px
import plotly.io as pio
from config import Config
from flask_apscheduler import APScheduler
import json
from prometheus_flask_exporter import PrometheusMetrics
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from flask import Response
from flask_httpauth import HTTPBasicAuth
import os



app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)



# # Optional: group by endpoint, method, etc.
# metrics.info('app_info', 'YouTube Tracker Flask App', version='1.0.0')


auth = HTTPBasicAuth()

# Define your username and password (keep it secure)
USERS = {
    "monitor": "yourpassword"
}

@auth.verify_password
def verify(username, password):
    if username in USERS and USERS[username] == password:
        return username
    return None






job_success_counter = Counter('youtube_job_success_total', 'Total successful extractions', ['channel_id'])
job_failure_counter = Counter('youtube_job_failure_total', 'Total failed extractions', ['channel_id'])



# Database Model (updated)
class YouTubeChannel(db.Model):
    __tablename__ = 'youtube_channels'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)  # NEW: Unique row id
    channel_id = db.Column(db.String(255), index=True)                # Channel ID (not PK)
    title = db.Column(db.String(255))
    subscribers = db.Column(db.BigInteger)
    views = db.Column(db.BigInteger)
    videos = db.Column(db.Integer)
    collected_at = db.Column(db.DateTime, default=datetime.utcnow)


    @classmethod
    def delete_channel(cls, channel_id):
        deleted = cls.query.filter_by(channel_id=channel_id).delete()
        db.session.commit()

        check = cls.query.filter_by(channel_id=channel_id).first()
        print("Still exists after delete?", check is not None)
        return deleted > 0

# # Create tables (run this manually once)
# def create_tables():
with app.app_context():
        
        db.create_all()


class SchedulerConfig:
    JOBS = []
    SCHEDULER_API_ENABLED = True
    SCHEDULER_TIMEZONE = 'UTC'  # Explicitly set timezone

app.config.from_object(SchedulerConfig)


scheduler = APScheduler()
scheduler.init_app(app)
if not scheduler.running:
    scheduler.start()
    print("Scheduler started successfully")

def scheduled_extraction(channel_id):
    with app.app_context():
        try:
            print(f"[⏳] Starting extraction for {channel_id}")
            raw_data = YouTubeService.get_channel_stats(channel_id)
            processed_data = DataProcessor.transform_data(raw_data)
            
            # Create new session for each job run
            with db.session.begin():
                channel = YouTubeChannel(
                    channel_id=processed_data['channel_id'],
                    title=processed_data['title'],
                    subscribers=processed_data['subscribers'],
                    views=processed_data['views'],
                    videos=processed_data['videos'],
                    collected_at=datetime.utcnow()
                )
                db.session.add(channel)
            
            print(f"[✓] Successfully extracted data for {channel_id}")
            job_success_counter.labels(channel_id=channel_id).inc()
        except Exception as e:
            db.session.rollback()
            print(f"[✗] Failed to extract {channel_id}: {str(e)}")
            job_failure_counter.labels(channel_id=channel_id).inc()
        finally:
            # Ensure session is removed
            db.session.remove()

@app.route('/schedule', methods=['POST'])
def schedule_channels():
    selected_ids = request.form.getlist('channel_ids')
    for channel_id in selected_ids:
        job_id = f"extract_{channel_id}"
        # Prevent duplicates
        if scheduler.get_job(job_id):
            continue
        scheduler.add_job(
            id=job_id,
            func=scheduled_extraction,
            args=[channel_id],
            trigger='interval',
            minutes=2  # adjust as needed
        )

    print('extraction scheduled successfully')

    # Print all scheduled jobs for debugging
    print("Current scheduled jobs:")
    for job in scheduler.get_jobs():
        print(f" - {job.id} (next run: {job.next_run_time})")

    return redirect(url_for('manage_channels'))


# YouTube API Service
class YouTubeService:
    @staticmethod
    def get_channel_stats(channel_id):
        params = {
            "part": "snippet,statistics",
            "id": channel_id,
            "key": app.config['YOUTUBE_API_KEY']
        }
        response = requests.get(
            app.config['YOUTUBE_API_URL'],
            params=params,
            timeout=10
        )
        response.raise_for_status()
        return response.json()

# Data Processing
class DataProcessor:
    @staticmethod
    def transform_data(raw_data):
        if not raw_data.get('items'):
            raise ValueError("No channel data found in API response")
        channel = raw_data['items'][0]
        return {
            "channel_id": channel["id"],
            "title": channel["snippet"]["title"],
            "subscribers": int(channel["statistics"].get("subscriberCount", 0)),
            "views": int(channel["statistics"].get("viewCount", 0)),
            "videos": int(channel["statistics"].get("videoCount", 0))
        }
    
@app.route("/")
def health():
    return "OK", 200
    

@app.route('/dashboard')
def dashboard():
    all_records = YouTubeChannel.query.order_by(YouTubeChannel.collected_at.asc()).all()

    latest_per_channel = {}
    for record in all_records:
        if (record.channel_id not in latest_per_channel or
            record.collected_at > latest_per_channel[record.channel_id].collected_at):
            latest_per_channel[record.channel_id] = record
    latest_records = list(latest_per_channel.values())

    chart_sub = chart_views = None
    top_growth = []
    engagement_stats = []
    metadata = {}

    if all_records:
        df = pd.DataFrame([{
            'channel_id': c.channel_id,
            'title': c.title,
            'subscribers': c.subscribers,
            'views': c.views,
            'videos': c.videos,
            'collected_at': c.collected_at
        } for c in all_records])

        df = df.sort_values(['title', 'collected_at'])

        # Growth calculations
        df['prev_subs'] = df.groupby('title')['subscribers'].shift(1)
        df['subs_growth'] = df['subscribers'] - df['prev_subs']

        df['prev_views'] = df.groupby('title')['views'].shift(1)
        df['views_growth'] = df['views'] - df['prev_views']

        # Load metadata if available
        try:
            with open("channel_metadata.json") as f:
                metadata = json.load(f)
        except FileNotFoundError:
            pass

        df['country'] = df['title'].map(lambda t: metadata.get(t, {}).get('country', 'Unknown'))
        df['genre'] = df['title'].map(lambda t: metadata.get(t, {}).get('genre', 'Unknown'))

        print('ok here')

        # Average Subscriber Growth by Country
        country_growth = df.groupby(['country', 'collected_at'])['subscribers'].sum().reset_index()
        fig_country_subs = px.line(country_growth, x='collected_at', y='subscribers', color='country',
                                title='Total Subscribers Over Time by Country')
        chart_country_subs = pio.to_html(fig_country_subs, full_html=False)




        # # Engagement metrics (latest records only)
        latest_df = df.loc[df.groupby('title')['collected_at'].idxmax()]

         # Avoid division by zero
        latest_df['views_per_sub'] = latest_df.apply(lambda row: row['views'] / row['subscribers'] if row['subscribers'] else 0, axis=1)
        latest_df['views_per_video'] = latest_df.apply(lambda row: row['views'] / row['videos'] if row['videos'] else 0, axis=1)
        latest_df['subs_per_video'] = latest_df.apply(lambda row: row['subscribers'] / row['videos'] if row['videos'] else 0, axis=1)


        engagement_stats = latest_df[['title', 'views_per_sub', 'views_per_video', 'subs_per_video']].to_dict(orient='records')



        # Step 1: Compute growth per channel
        df = df.sort_values(['title', 'collected_at'])
        df['subs_growth'] = df.groupby('title')['subscribers'].diff().fillna(0)

        # Step 2: Get the most recent entry per channel (not globally)
        latest_per_channel = df.sort_values('collected_at').groupby('title').tail(1)

        # Step 3: Sort by growth
        top_growth = latest_per_channel.sort_values('subs_growth', ascending=False).head(3)
        top_growth = top_growth[['title', 'subs_growth']].to_dict(orient='records')

        # Calculate engagement metrics
        latest_df['views_per_sub'] = latest_df.apply(lambda row: row['views'] / row['subscribers'] if row['subscribers'] else 0, axis=1)
        latest_df['views_per_video'] = latest_df.apply(lambda row: row['views'] / row['videos'] if row['videos'] else 0, axis=1)
        latest_df['subs_per_video'] = latest_df.apply(lambda row: row['subscribers'] / row['videos'] if row['videos'] else 0, axis=1)

        # Clean country names
        latest_df['country'] = latest_df['country'].str.strip().str.title()

        # Group by country
        country_engagement = latest_df.groupby('country')[['views_per_sub', 'views_per_video', 'subs_per_video']].mean().reset_index()

        # Create individual charts for each metric
        def create_engagement_chart(data, metric, title):
            fig = px.bar(
                data,
                x='country',
                y=metric,
                title=title,
                color_discrete_sequence=['#66c2a5']  # Single color since it's one metric
            )
            fig.update_layout(
                xaxis=dict(
                    categoryorder='array', 
                    categoryarray=sorted(data['country'].unique()),
                    title='Country'
                ),
                yaxis_title='Value',
                showlegend=False
            )
            return fig

        # Create separate charts
        fig_views_per_sub = create_engagement_chart(country_engagement, 'views_per_sub', 'Views per Subscriber by Country')
        fig_views_per_video = create_engagement_chart(country_engagement, 'views_per_video', 'Views per Video by Country')
        fig_subs_per_video = create_engagement_chart(country_engagement, 'subs_per_video', 'Subscribers per Video by Country')

        # Convert to HTML
        chart_views_per_sub = pio.to_html(fig_views_per_sub, full_html=False)
        chart_views_per_video = pio.to_html(fig_views_per_video, full_html=False)
        chart_subs_per_video = pio.to_html(fig_subs_per_video, full_html=False)

        # Clean country names consistently
        latest_df['country'] = latest_df['country'].str.strip().str.upper()

            # Add thumbnail URLs from metadata
        latest_df['thumbnail'] = latest_df['title'].map(lambda t: metadata.get(t, {}).get('thumbnail', ''))

        # Filter by country - using consistent uppercase comparison
        uk_channels = latest_df[latest_df['country'] == 'UK'].copy()
        us_channels = latest_df[latest_df['country'] == 'USA'].copy()

        # Debugging output - check if we're getting any data
        print(f"UK channels found: {len(uk_channels)}")
        print(f"US channels found: {len(us_channels)}")

        # Only process if we have data
        uk_table = []
        us_table = []

        if not uk_channels.empty:
            uk_ranked = uk_channels.sort_values(by='views_per_sub', ascending=False)
            uk_ranked['rank'] = range(1, len(uk_ranked) + 1)
            uk_table = uk_ranked[['rank', 'title', 'thumbnail', 'views', 'subscribers', 'views_per_sub']]
            uk_table = uk_table.round({'views_per_sub': 2}).to_dict(orient='records')
        else:
            print("Warning: No UK channels found")

        if not us_channels.empty:
            us_ranked = us_channels.sort_values(by='views_per_sub', ascending=False)
            us_ranked['rank'] = range(1, len(us_ranked) + 1)
            us_table = us_ranked[['rank', 'title', 'thumbnail', 'views', 'subscribers', 'views_per_sub']]
            us_table = us_table.round({'views_per_sub': 2}).to_dict(orient='records')
        else:
            print("Warning: No US channels found")

        # Subscriber Growth Chart
        fig_sub = px.line(
            df,
            x='collected_at',
            y='subscribers',
            color='title',
            title='Subscriber Growth Over Time',
            line_group='title'  # This ensures lines are drawn per channel
        )

        chart_sub = pio.to_html(fig_sub, full_html=False)

        # View Growth Chart
        fig_views = px.line(
            df,
            x='collected_at',
            y='views',
            color='title',
            title='View Growth Over Time',
            line_group='title')


        chart_views = pio.to_html(fig_views, full_html=False)

    return render_template(
        'dashboard.html',
        channels=latest_records,
        chart_sub=chart_sub,
        chart_views=chart_views,
        engagement_stats=engagement_stats,
        top_growth=top_growth,
        chart_country_subs=chart_country_subs,
        chart_views_per_sub=chart_views_per_sub,
        chart_views_per_video=chart_views_per_video,
        chart_subs_per_video=chart_subs_per_video,
        uk_table=uk_table,
        us_table=us_table
        # chart_country_subs=chart_country_subs,
        # chart_engagement=chart_engagement
    )


@app.route('/extract', methods=['GET', 'POST'])
def extract():
    if request.method == 'POST':
        channel_id = request.form.get('channel_id')
        try:
            # Extract
            raw_data = YouTubeService.get_channel_stats(channel_id)
            # Transform
            processed_data = DataProcessor.transform_data(raw_data)
            # Load: Always add a new record!
            channel = YouTubeChannel(
                channel_id=processed_data['channel_id'],
                title=processed_data['title'],
                subscribers=processed_data['subscribers'],
                views=processed_data['views'],
                videos=processed_data['videos'],
                collected_at=datetime.utcnow()  # Ensure new timestamp
            )
            db.session.add(channel)  # Always insert new row
            db.session.commit()
            return redirect(url_for('dashboard'))
        except Exception as e:
            return render_template('extract.html', error=str(e))
    return render_template('extract.html')

@app.route('/channels')
def manage_channels():
    channels = db.session.query(YouTubeChannel.channel_id, YouTubeChannel.title)\
        .distinct(YouTubeChannel.channel_id, YouTubeChannel.title)\
        .all()
    return render_template('manage_channels.html', channels=channels)


@app.route('/delete-channel', methods=['POST'])
def delete_channel():
    channel_id = request.form.get('channel_id')
    print("Deleting:", channel_id)  # Debug log
    if YouTubeChannel.delete_channel(channel_id):
        return redirect(url_for('manage_channels'))
    return "Channel not found", 404




@app.route('/metrics')
@auth.login_required
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


# if __name__ == '__main__':
#     with app.app_context():
#         db.create_all()
#     app.run(host='0.0.0.0',port=int(os.environ.get("PORT", 8080)),debug=True)
