"""Flask dashboard for a local MeshDrop peer."""

from __future__ import annotations

import argparse
import hashlib
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from meshdrop import DEFAULT_TCP_PORT, DISCOVERY_PORT, MeshDrop, unique_path

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DIST_ROOT = WEB_ROOT / "dist"


def create_app(node: MeshDrop) -> Flask:
    app = Flask(__name__, static_folder=str(DIST_ROOT / "assets"), static_url_path="/assets")
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024 * 1024
    transfers: dict[str, dict] = {}
    transfers_lock = threading.Lock()

    @app.get("/")
    def index():
        return send_from_directory(DIST_ROOT, "index.html")

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the MeshDrop web dashboard")
    parser.add_argument("--name", default="MeshDrop", help="name shown to peers")
    parser.add_argument("--share", type=Path, default=Path("shared"), help="folder made available to peers")
    parser.add_argument("--downloads", type=Path, default=Path("downloads"), help="where received files are saved")
    parser.add_argument("--port", type=int, default=DEFAULT_TCP_PORT, help="peer-to-peer TCP port")
    parser.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT, help="UDP discovery port")
    parser.add_argument("--web-port", type=int, default=8000, help="local dashboard port")
    args = parser.parse_args()

    node = MeshDrop(args.name, args.share, args.downloads, args.port, args.discovery_port)
    try:
        node.start()
        print(f"Dashboard: http://127.0.0.1:{args.web_port}")
        create_app(node).run(host="127.0.0.1", port=args.web_port, threaded=True, debug=False,
                             use_reloader=False)
    except OSError as exc:
        print(f"Could not start MeshDrop: {exc}")
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        node.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
