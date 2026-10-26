"""Native desktop dashboard for a local MeshDrop peer."""

from __future__ import annotations

import argparse
import hashlib
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
import webview
from werkzeug.serving import make_server
from werkzeug.utils import secure_filename

from meshdrop import DEFAULT_TCP_PORT, DISCOVERY_PORT, MeshDrop, unique_path

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DIST_ROOT = WEB_ROOT / "dist"
WINDOW_ICON = WEB_ROOT / "public" / "meshdrop.ico"


def create_app(node: MeshDrop) -> Flask:
    app = Flask(__name__, static_folder=str(DIST_ROOT / "assets"), static_url_path="/assets")
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024 * 1024
    transfers: dict[str, dict] = {}
    transfers_lock = threading.Lock()

    @app.get("/")
    def index():
        return send_from_directory(DIST_ROOT, "index.html")

    @app.get("/<path:asset>")
    def built_asset(asset: str):
        return send_from_directory(DIST_ROOT, asset)

    @app.get("/api/state")
    def state():
        peers = []
        for peer in node.peer_list():
            peers.append({key: value for key, value in peer.items() if key != "last_seen"} | {
                "last_seen_seconds": max(0, int(time.time() - peer.get("last_seen", time.time())))
            })
        with transfers_lock:
            current_transfers = sorted(
                (dict(item) for item in transfers.values()),
                key=lambda item: item["created_at"], reverse=True,
            )
        return jsonify({
            "identity": node.identity(),
            "local_ip": node.host,
            "tcp_port": node.tcp_port,
            "discovery_port": node.discovery_port,
            "share_dir": str(node.share_dir),
            "downloads_dir": str(node.downloads_dir),
            "peers": peers,
            "local_files": node.shared_files(),
            "transfers": current_transfers,
        })

    @app.get("/api/peers/<peer_id>/files")
    def peer_files(peer_id: str):
        try:
            peer = node.find_peer(peer_id)
            response, _, _ = node.request(peer, {"op": "list"})
            identity = response.get("peer", {})
            return jsonify({"peer": identity, "files": response.get("files", [])})
        except (OSError, ValueError, KeyError, ConnectionError) as exc:
            return jsonify({"error": str(exc)}), 502

    @app.post("/api/peers")
    def add_peer():
        payload = request.get_json(silent=True) or {}
        host = str(payload.get("host", "")).strip()
        try:
            port = int(payload.get("port", DEFAULT_TCP_PORT))
        except (TypeError, ValueError):
            return jsonify({"error": "Enter a valid TCP port."}), 400
        if not host or not 1 <= port <= 65535:
            return jsonify({"error": "Enter a host and a TCP port between 1 and 65535."}), 400
        try:
            node.connect_peer(f"{host}:{port}")
            return jsonify({"ok": True, "peers": node.peer_list()}), 200
        except (OSError, ValueError, KeyError, ConnectionError) as exc:
            return jsonify({"error": str(exc)}), 502

    @app.post("/api/transfers")
    def start_transfer():
        payload = request.get_json(silent=True) or {}
        peer_id = str(payload.get("peer_id", ""))
        filename = str(payload.get("filename", ""))
        if not peer_id or not filename or Path(filename).name != filename or "\\" in filename:
            return jsonify({"error": "Choose a peer and a valid file name."}), 400
        try:
            peer = node.find_peer(peer_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

        transfer_id = uuid.uuid4().hex[:10]
        record = {
            "id": transfer_id,
            "direction": "download",
            "peer_id": peer["id"],
            "peer_name": peer["name"],
            "filename": filename,
            "status": "connecting",
            "received": 0,
            "size": 0,
            "progress": 0,
            "created_at": time.time(),
            "saved_path": None,
            "error": None,
        }
        with transfers_lock:
            transfers[transfer_id] = record

        threading.Thread(
            target=download_from_peer,
            args=(node, peer, filename, transfer_id, transfers, transfers_lock),
            daemon=True,
        ).start()
        return jsonify({"transfer": record}), 202

    @app.post("/api/share")
    def add_to_share():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "Choose a file to add to your shared folder."}), 400
        safe_name = secure_filename(upload.filename)
        if not safe_name:
            return jsonify({"error": "That file name cannot be shared."}), 400
        destination = unique_path(node.share_dir / safe_name)
        temporary = destination.with_name(destination.name + ".uploading")
        try:
            upload.save(temporary)
            temporary.replace(destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            return jsonify({"error": f"Could not add file: {exc}"}), 500
        return jsonify({"file": {"name": destination.name, "size": destination.stat().st_size}}), 201

    @app.delete("/api/share/<path:filename>")
    def remove_from_share(filename: str):
        if Path(filename).name != filename or "\\" in filename:
            return jsonify({"error": "Invalid file name."}), 400
        path = node.share_dir / filename
        try:
            if path.is_symlink() or not path.is_file():
                return jsonify({"error": "File not found."}), 404
            path.unlink()
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True})

    @app.get("/api/downloads/<path:filename>")
    def open_download(filename: str):
        if Path(filename).name != filename or "\\" in filename:
            return jsonify({"error": "Invalid file name."}), 400
        return send_from_directory(node.downloads_dir, filename, as_attachment=True)

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"error": "That file is over the 5 GB upload limit."}), 413

    return app


def download_from_peer(node: MeshDrop, peer: dict, filename: str, transfer_id: str,
                       transfers: dict[str, dict], lock: threading.Lock) -> None:
    sock = None
    stream = None

    def update(**values):
        with lock:
            if transfer_id in transfers:
                transfers[transfer_id].update(values)

    try:
        response, sock, stream = node.request(
            peer, {"op": "get", "name": filename}, receive_body=True
        )
        size = int(response["size"])
        expected_hash = str(response["sha256"])
        safe_name = secure_filename(str(response.get("name", filename))) or "meshdrop-file"
        destination = unique_path(node.downloads_dir / safe_name)
        temporary = destination.with_name(destination.name + ".part")
        update(status="downloading", size=size, filename=destination.name)

        received = 0
        digest = hashlib.sha256()
        with temporary.open("wb") as output:
            while received < size:
                chunk = stream.read(min(64 * 1024, size - received))
                if not chunk:
                    raise ConnectionError("The peer ended the transfer early.")
                output.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                update(received=received, progress=round(received * 100 / size) if size else 100)
        if digest.hexdigest() != expected_hash:
            raise ValueError("SHA-256 verification failed; the file may be damaged.")
        temporary.replace(destination)
        update(status="complete", received=size, progress=100, saved_path=str(destination))
    except Exception as exc:
        update(status="failed", error=str(exc))
    finally:
        if stream:
            stream.close()
        if sock:
            sock.close()


def valid_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be a number") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def apply_windows_window_icon(window) -> None:
    """Replace pywebview's inherited Python executable icon on Windows."""
    if sys.platform != "win32" or window.native is None or not WINDOW_ICON.is_file():
        return
    try:
        from System.Drawing import Icon

        icon = Icon(str(WINDOW_ICON))
        window.native.Icon = icon
        window._meshdrop_icon = icon  # Keep the .NET icon alive with its window.
    except Exception as exc:
        print(f"Could not apply the MeshDrop window icon: {exc}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the MeshDrop desktop app")
    parser.add_argument("positional_port", nargs="?", type=valid_port, default=DEFAULT_TCP_PORT,
                        help="peer TCP port (default: %(default)s)")
    parser.add_argument("positional_name", nargs="?", help="your display name on the network")
    parser.add_argument("--name", dest="name_option", default=None, help="name shown to peers")
    parser.add_argument("--share", type=Path, default=Path("shared"), help="folder made available to peers")
    parser.add_argument("--downloads", type=Path, default=ROOT / "downloads",
                        help="base folder for received files; a folder named after you is created inside")
    parser.add_argument("--port", dest="port_option", type=valid_port, default=None,
                        help="peer-to-peer TCP port (overrides the positional port)")
    parser.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT, help="UDP discovery port")
    parser.add_argument("--web-port", type=int, default=8000,
                        help="local port used by the app's embedded webview")
    args = parser.parse_args()

    name = (args.name_option or args.positional_name or "MeshDrop").strip() or "MeshDrop"
    port = args.port_option if args.port_option is not None else args.positional_port
    downloads_base = args.downloads if args.downloads.is_absolute() else ROOT / args.downloads
    user_folder = secure_filename(name) or "MeshDrop"
    downloads_dir = downloads_base / user_folder
    node = MeshDrop(name, args.share, downloads_dir, port, args.discovery_port)
    server = None
    try:
        node.start()
        server = make_server("127.0.0.1", args.web_port, create_app(node), threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}"
        window = webview.create_window(
            "MeshDrop",
            url,
            width=1440,
            height=940,
            min_size=(900, 640),
            background_color="#08090b",
        )
        window.events.before_show += apply_windows_window_icon
        webview.start(debug=False)
    except Exception as exc:
        print(f"Could not start the MeshDrop desktop app: {exc}")
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        node.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
