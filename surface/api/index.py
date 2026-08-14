
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import requests
import re
import time
import zipfile
import io
import os

app = Flask(__name__)
CORS(app)

# Get the Roblox API key from Vercel Environment Variables.
ROBLOX_API_KEY = os.environ.get("ROBLOX_API_KEY", "").strip()

HEADERS = {
    "User-Agent": "Surface/1.0",
    "Accept": "application/json",
}

if ROBLOX_API_KEY:
    HEADERS["x-api-key"] = ROBLOX_API_KEY

TIMEOUT = 30

# NOTE:
# These counters are per serverless instance and are NOT permanent.
stats = {
    "api_requests": 0,
    "searches": 0,
    "downloads": 0
}


def get_cdn_url(hash_str):
    if not hash_str:
        return None

    i = 31

    for t in range(min(38, len(hash_str))):
        i ^= ord(hash_str[t])

    return f"https://t{i % 8}.rbxcdn.com/{hash_str}"


def get_user(username):
    stats["api_requests"] += 1

    response = requests.post(
        "https://users.roblox.com/v1/usernames/users",
        json={
            "usernames": [username],
            "excludeBannedUsers": False
        },
        headers=HEADERS,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    users = response.json().get("data", [])

    return users[0] if users else None


def get_thumbnail(user_id):
    stats["api_requests"] += 1

    response = requests.get(
        "https://thumbnails.roblox.com/v1/users/avatar",
        params={
            "userIds": user_id,
            "size": "420x420",
            "format": "Png",
            "isCircular": "false"
        },
        headers=HEADERS,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    data = response.json().get("data", [])

    if not data:
        return None

    return data[0].get("imageUrl")


def get_avatar_3d(user_id):
    stats["api_requests"] += 1

    response = requests.get(
        "https://thumbnails.roblox.com/v1/users/avatar-3d",
        params={"userId": user_id},
        headers=HEADERS,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    return response.json()


@app.route("/api/user", methods=["GET"])
def api_user():

    username = request.args.get("username", "").strip()

    if not username:
        return jsonify({
            "success": False,
            "error": "Enter a Roblox username."
        }), 400

    if len(username) > 20:
        return jsonify({
            "success": False,
            "error": "Username is too long."
        }), 400

    stats["searches"] += 1

    try:

        user = get_user(username)

        if not user:
            return jsonify({
                "success": False,
                "error": "Roblox user not found."
            }), 404

        thumbnail = get_thumbnail(user["id"])

        return jsonify({
            "success": True,
            "user": {
                "id": user["id"],
                "name": user.get("name"),
                "displayName": user.get("displayName"),
                "thumbnail": thumbnail
            }
        })

    except requests.HTTPError as e:

        status = (
            e.response.status_code
            if e.response is not None
            else 502
        )

        if status == 401:
            return jsonify({
                "success": False,
                "error": "Roblox API key is invalid."
            }), 401

        return jsonify({
            "success": False,
            "error": "Roblox API request failed."
        }), 502

    except requests.RequestException:
        return jsonify({
            "success": False,
            "error": "Unable to connect to Roblox."
        }), 502

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/download", methods=["GET"])
def download_obj():

    username = request.args.get("username", "").strip()

    if not username:
        return jsonify({
            "success": False,
            "error": "Username is required."
        }), 400

    try:

        user = get_user(username)

        if not user:
            return jsonify({
                "success": False,
                "error": "Roblox user not found."
            }), 404

        user_id = user["id"]

        safe_name = re.sub(
            r"[^a-zA-Z0-9_-]",
            "_",
            user.get("name", "avatar")
        )

        avatar = None

        completed = False

        # Roblox may take a few seconds to generate the 3D avatar.
        for _ in range(6):

            avatar = get_avatar_3d(user_id)

            state = avatar.get("state")

            if state == "Completed":
                completed = True
                break

            if state == "Blocked":
                return jsonify({
                    "success": False,
                    "error": "This avatar is blocked."
                }), 403

            time.sleep(1.5)

        if not completed:

            return jsonify({
                "success": False,
                "error": "Roblox is still generating the avatar. Try again."
            }), 503

        metadata_url = avatar.get("imageUrl")

        if not metadata_url:

            return jsonify({
                "success": False,
                "error": "No avatar metadata URL returned."
            }), 503

        stats["api_requests"] += 1

        metadata_response = requests.get(
            metadata_url,
            headers=HEADERS,
            timeout=TIMEOUT
        )

        metadata_response.raise_for_status()

        metadata = metadata_response.json()

        obj_hash = metadata.get("obj")
        mtl_hash = metadata.get("mtl")
        textures = metadata.get("textures") or []

        if not obj_hash:

            return jsonify({
                "success": False,
                "error": "No OBJ data was returned by Roblox."
            }), 503

        obj_url = get_cdn_url(obj_hash)

        if not obj_url:

            return jsonify({
                "success": False,
                "error": "Invalid OBJ URL."
            }), 503

        obj_response = requests.get(
            obj_url,
            headers=HEADERS,
            timeout=TIMEOUT
        )

        obj_response.raise_for_status()

        obj_data = obj_response.content

        mtl_data = b""
        texture_files = []

        if mtl_hash:

            mtl_url = get_cdn_url(mtl_hash)

            mtl_response = requests.get(
                mtl_url,
                headers=HEADERS,
                timeout=TIMEOUT
            )

            if mtl_response.ok:
                mtl_data = mtl_response.content

        if mtl_data and textures:

            mtl_text = mtl_data.decode(
                "utf-8",
                errors="ignore"
            )

            for index, texture_hash in enumerate(textures):

                filename = f"{safe_name}_{index}.png"

                mtl_text = mtl_text.replace(
                    texture_hash,
                    filename
                )

                texture_url = get_cdn_url(texture_hash)

                if texture_url:
                    texture_files.append(
                        (filename, texture_url)
                    )

            mtl_data = mtl_text.encode("utf-8")

        if mtl_data and b"mtllib" not in obj_data[:500]:

            obj_data = (
                f"mtllib {safe_name}.mtl\n"
            ).encode("utf-8") + obj_data

        # Create ZIP in memory.
        output = io.BytesIO()

        with zipfile.ZipFile(
            output,
            "w",
            zipfile.ZIP_DEFLATED
        ) as archive:

            archive.writestr(
                f"{safe_name}.obj",
                obj_data
            )

            if mtl_data:

                archive.writestr(
                    f"{safe_name}.mtl",
                    mtl_data
                )

            for filename, texture_url in texture_files:

                try:

                    texture = requests.get(
                        texture_url,
                        headers=HEADERS,
                        timeout=TIMEOUT
                    )

                    if texture.ok:

                        archive.writestr(
                            filename,
                            texture.content
                        )

                except requests.RequestException:
                    pass

        output.seek(0)

        stats["downloads"] += 1

        return Response(
            output.getvalue(),
            mimetype="application/zip",
            headers={
                "Content-Disposition":
                    f'attachment; filename="{safe_name}_avatar.zip"',
                "Cache-Control": "no-store"
            }
        )

    except requests.HTTPError as e:

        status = (
            e.response.status_code
            if e.response is not None
            else 502
        )

        if status == 401:

            return jsonify({
                "success": False,
                "error": "Roblox API key is invalid."
            }), 401

        return jsonify({
            "success": False,
            "error": "Roblox request failed."
        }), 502

    except requests.RequestException:

        return jsonify({
            "success": False,
            "error": "Unable to connect to Roblox."
        }), 502

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/stats", methods=["GET"])
def api_stats():

    return jsonify({
        "success": True,
        "stats": stats
    })


@app.route("/api/health", methods=["GET"])
def health():

    return jsonify({
        "success": True,
        "surface": "online",
        "api_key_configured": bool(ROBLOX_API_KEY)
    })


# Vercel imports the Flask "app" object.
# Do NOT use app.run() on Vercel.
