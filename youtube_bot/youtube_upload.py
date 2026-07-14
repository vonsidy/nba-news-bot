"""Upload a finished Short to YouTube via the Data API v3.

One-time setup (done by YOU, since it needs a Google login):
  1. In Google Cloud Console, create a project, enable "YouTube Data API v3".
  2. Configure an OAuth consent screen (External, add yourself as a test user).
  3. Create an OAuth Client ID of type "Desktop app", download the JSON as
     youtube_bot/client_secret.json.
  4. Run:  python -m youtube_bot.youtube_upload --auth
     A browser opens once; approve. A token is cached to yt_token.json and
     refreshes automatically after that (no browser needed on the server).
"""

import argparse
import os

import youtube_bot.config as config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(config.YT_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(config.YT_TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                config.YT_CLIENT_SECRETS, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(config.YT_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload(video_path: str, title: str, description: str, tags: list[str]) -> str:
    """Upload and return the video id."""
    from googleapiclient.http import MediaFileUpload

    # #Shorts in the description/title helps YouTube classify vertical <60s clips.
    if "#shorts" not in description.lower():
        description = f"{description}\n\n#shorts"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags[:15],
            "categoryId": config.YT_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": config.YT_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = _service().videos().insert(
        part="snippet,status", body=body, media_body=media
    )
    response = request.execute()
    return response["id"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", action="store_true", help="Run one-time OAuth")
    args = parser.parse_args()
    if args.auth:
        _service()
        print("Authorized. Token cached at", config.YT_TOKEN_FILE)
