#!/usr/bin/env python3
"""MeshDrop: discover nearby peers and transfer files directly over a LAN."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import socketserver
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

CHUNK_SIZE = 64 * 1024
DISCOVERY_INTERVAL = 4
PEER_TIMEOUT = 16
DISCOVERY_PORT = 37020
DEFAULT_TCP_PORT = 50000


def send_json(stream: Any, payload: dict[str, Any]) -> None:
    stream.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
    stream.flush()


def receive_json(stream: Any) -> dict[str, Any]:
    line = stream.readline(64 * 1024)
    if not line or not line.endswith(b"\n"):
        raise ConnectionError("Peer closed the connection before sending a response")
    data = json.loads(line.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid response from peer")
    return data


def guess_lan_ip() -> str:
    """Get the IP selected for outbound traffic without sending a packet."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET address; UDP connect sends no data.
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class MeshDropServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = (sys.platform != "win32")
    daemon_threads = True
    app: "MeshDrop"


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            request = receive_json(self.rfile)
            operation = request.get("op")
            if operation == "list":
                send_json(self.wfile, {"ok": True, "peer": self.server.app.identity(),
                                       "files": self.server.app.shared_files()})
            elif operation == "get":
                self._send_file(str(request.get("name", "")))
            else:
                send_json(self.wfile, {"ok": False, "error": "Unknown request"})
        except (ConnectionError, OSError, ValueError, json.JSONDecodeError) as exc:
            self.server.app.note(f"Connection ended: {exc}")

    def _send_file(self, name: str) -> None:
        app = self.server.app
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            send_json(self.wfile, {"ok": False, "error": "Only shared file names can be requested"})
            return
        path = app.share_dir / name
        try:
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError
            resolved = path.resolve(strict=True)
            if resolved.parent != app.share_dir.resolve():
                raise FileNotFoundError
            size = resolved.stat().st_size
            digest = hashlib.sha256()
            with resolved.open("rb") as source:
                for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
            send_json(self.wfile, {"ok": True, "name": name, "size": size,
                                   "sha256": digest.hexdigest()})
            with resolved.open("rb") as source:
                for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                    self.wfile.write(chunk)
        except (OSError, FileNotFoundError):
            send_json(self.wfile, {"ok": False, "error": "That file is not available"})


class MeshDrop:
    def __init__(self, name: str, share_dir: Path, downloads_dir: Path,
                 tcp_port: int, discovery_port: int, auto_port: bool = True) -> None:
        self.peer_id = uuid.uuid4().hex[:8]
        self.name = name
        self.host = guess_lan_ip()
        self.tcp_port = tcp_port
        self.auto_port = auto_port
        self.discovery_port = discovery_port
        self.share_dir = share_dir.expanduser().resolve()
        self.downloads_dir = downloads_dir.expanduser().resolve()
        self.share_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.peers: dict[str, dict[str, Any]] = {}
        self.peers_lock = threading.Lock()
        self.print_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.server: MeshDropServer | None = None

    def note(self, message: str) -> None:
        with self.print_lock:
            print(f"\n[MeshDrop] {message}", flush=True)

    def identity(self) -> dict[str, Any]:
        return {"id": self.peer_id, "name": self.name, "host": self.host,
                "port": self.tcp_port}

    def shared_files(self) -> list[dict[str, Any]]:
        result = []
        try:
            entries = sorted(self.share_dir.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return result
        for path in entries:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                result.append({"name": path.name, "size": path.stat().st_size})
            except OSError:
                continue
        return result

    def start(self) -> None:
        server = None
        attempted_port = self.tcp_port
        max_attempts = 100 if self.auto_port else 1
        last_error = None
        for offset in range(max_attempts):
            port = attempted_port + offset
            try:
                server = MeshDropServer(("0.0.0.0", port), RequestHandler)
                if port != attempted_port:
                    self.note(f"TCP port {attempted_port} was busy; bound to port {port} instead.")
                self.tcp_port = port
                break
            except OSError as exc:
                last_error = exc
                continue
        if server is None:
            raise OSError(f"Could not bind TCP server to port {attempted_port}: {last_error}")
        self.server = server
        self.server.app = self
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._discovery_listener, daemon=True).start()
        threading.Thread(target=self._announce_loop, daemon=True).start()
        threading.Thread(target=self._expire_loop, daemon=True).start()
        print(f"MeshDrop running as {self.name} ({self.peer_id})")
        print(f"TCP listening on {self.host}:{self.tcp_port} | UDP discovery on port {self.discovery_port}")
        print(f"Sharing: {self.share_dir}")
        print(f"Downloads: {self.downloads_dir}")
        print("Type /help for commands. Keep this process running to share files.\n")

    def close(self) -> None:
        self.stop_event.set()
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def _announce_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        message = json.dumps({"type": "meshdrop-peer", **self.identity()}).encode("utf-8")
        while not self.stop_event.is_set():
            try:
                sock.sendto(message, ("255.255.255.255", self.discovery_port))
            except OSError as exc:
                self.note(f"Peer announcement failed: {exc}")
            try:
                # Also announce on loopback so two instances on one computer always find each other
                sock.sendto(message, ("127.0.0.1", self.discovery_port))
            except OSError:
                pass
            self.stop_event.wait(DISCOVERY_INTERVAL)
        sock.close()

    def _discovery_listener(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.bind(("", self.discovery_port))
            sock.settimeout(1)
            while not self.stop_event.is_set():
                try:
                    raw, address = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                try:
                    payload = json.loads(raw.decode("utf-8"))
                    if payload.get("type") != "meshdrop-peer" or payload.get("id") == self.peer_id:
                        continue
                    peer_id = str(payload["id"])
                    peer_host = str(payload.get("host") or address[0])
                    # If this is another instance running on the same computer, route via loopback
                    if address[0] == "127.0.0.1" and (peer_host == self.host or peer_host == "127.0.0.1"):
                        peer_host = "127.0.0.1"
                    peer = {"id": peer_id, "name": str(payload.get("name", "Peer")),
                            "host": peer_host,
                            "port": int(payload["port"]), "last_seen": time.time(), "manual": False}
                    with self.peers_lock:
                        self.peers[peer_id] = peer
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    continue
        except OSError as exc:
            self.note(f"UDP discovery unavailable: {exc}. Use /connect HOST:PORT to add a peer manually.")
        finally:
            sock.close()

    def _expire_loop(self) -> None:
        while not self.stop_event.wait(3):
            cutoff = time.time() - PEER_TIMEOUT
            with self.peers_lock:
                expired = [key for key, peer in self.peers.items()
                           if not peer.get("manual") and peer.get("last_seen", 0) < cutoff]
                for key in expired:
                    del self.peers[key]

    def peer_list(self) -> list[dict[str, Any]]:
        with self.peers_lock:
            return sorted((dict(peer) for peer in self.peers.values()),
                          key=lambda peer: (peer["name"].lower(), peer["id"]))

    def find_peer(self, selector: str) -> dict[str, Any]:
        peers = self.peer_list()
        exact = [peer for peer in peers if peer["id"] == selector or peer["name"].lower() == selector.lower()]
        matches = exact or [peer for peer in peers if peer["id"].startswith(selector)]
        if not matches:
            raise ValueError(f"No peer matches '{selector}'. Type /peers to see nearby peers.")
        if len(matches) > 1:
            raise ValueError("That peer selector is ambiguous. Use more of its peer ID.")
        return matches[0]

    def request(self, peer: dict[str, Any], payload: dict[str, Any],
                receive_body: bool = False) -> tuple[dict[str, Any], socket.socket | None, Any | None]:
        sock = socket.create_connection((peer["host"], peer["port"]), timeout=8)
        sock.settimeout(None)
        stream = sock.makefile("rwb")
        send_json(stream, payload)
        response = receive_json(stream)
        if not response.get("ok"):
            stream.close()
            sock.close()
            raise ValueError(response.get("error", "Peer rejected the request"))
        if receive_body:
            return response, sock, stream
        stream.close()
        sock.close()
        return response, None, None

    def connect_peer(self, endpoint: str) -> None:
        if ":" not in endpoint:
            raise ValueError("Use /connect HOST:PORT")
        host, port_text = endpoint.rsplit(":", 1)
        peer = {"id": f"manual-{host}-{port_text}", "name": host,
                "host": host, "port": int(port_text), "manual": True,
                "last_seen": time.time()}
        response, _, _ = self.request(peer, {"op": "list"})
        identity = response.get("peer", {})
        peer.update({"id": str(identity.get("id", peer["id"])),
                     "name": str(identity.get("name", host)),
                     "host": host, "port": int(port_text), "manual": True,
                     "last_seen": time.time()})
        with self.peers_lock:
            self.peers[peer["id"]] = peer
        print(f"Connected to {peer['name']} ({peer['id']}) at {host}:{port_text}")

    def list_files(self, selector: str) -> None:
        peer = self.find_peer(selector)
        response, _, _ = self.request(peer, {"op": "list"})
        files = response.get("files", [])
        print(f"Files shared by {response.get('peer', {}).get('name', peer['name'])}:")
        if not files:
            print("  (no files in their shared folder)")
        for item in files:
            print(f"  {item['name']}  ({format_size(int(item['size']))})")

    def get_file(self, selector: str, name: str) -> None:
        peer = self.find_peer(selector)
        response, sock, stream = self.request(peer, {"op": "get", "name": name}, receive_body=True)
        assert sock is not None and stream is not None
        size = int(response["size"])
        expected_hash = str(response["sha256"])
        destination = unique_path(self.downloads_dir / str(response["name"]))
        temporary = destination.with_name(destination.name + ".part")
        received = 0
        digest = hashlib.sha256()
        started = time.monotonic()
        try:
            with temporary.open("wb") as output:
                while received < size:
                    chunk = stream.read(min(CHUNK_SIZE, size - received))
                    if not chunk:
                        raise ConnectionError("Transfer ended early")
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    elapsed = max(time.monotonic() - started, 0.001)
                    percent = 100 if size == 0 else received * 100 // size
                    print(f"\rReceiving {destination.name}: {percent:3d}%  "
                          f"{format_size(received)}/{format_size(size)}  "
                          f"{format_size(int(received / elapsed))}/s", end="", flush=True)
            if digest.hexdigest() != expected_hash:
                raise ValueError("SHA-256 check failed; the downloaded file may be damaged")
            temporary.replace(destination)
            print(f"\nSaved and verified: {destination}")
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            stream.close()
            sock.close()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(1, 10000):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError("Too many files with the same name in the downloads folder")


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def print_peers(app: MeshDrop) -> None:
    peers = app.peer_list()
    if not peers:
        print("No peers seen yet. Devices need to be on the same LAN.")
        print("On one computer, try /connect 127.0.0.1:50000 from another instance.")
        return
    print("Nearby peers:")
    for peer in peers:
        print(f"  {peer['name']:<20} id={peer['id']}  {peer['host']}:{peer['port']}")


def command_loop(app: MeshDrop) -> None:
    while True:
        try:
            command = input("meshdrop> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not command:
            continue
        if command in {"/quit", "/exit"}:
            return
        if command == "/help":
            print("Commands:\n"
                  "  /peers                   list nearby peers\n"
                  "  /connect HOST:PORT       connect manually (useful on one computer)\n"
                  "  /files PEER              list a peer's shared files\n"
                  "  /get PEER FILE           download and verify a file\n"
                  "  /help                    show this help\n"
                  "  /quit                    stop MeshDrop")
            continue
        if command == "/peers":
            print_peers(app)
            continue
        parts = command.split(maxsplit=2)
        try:
            if parts[0] == "/connect" and len(parts) == 2:
                app.connect_peer(parts[1])
            elif parts[0] == "/files" and len(parts) == 2:
                app.list_files(parts[1])
            elif parts[0] == "/get" and len(parts) == 3:
                app.get_file(parts[1], parts[2])
            else:
                print("Unknown or incomplete command. Type /help.")
        except (OSError, ValueError, KeyError, ConnectionError) as exc:
            print(f"Error: {exc}")


def parse_peer_args(pos_args: list[str], name_opt: str | None = None,
                    port_opt: int | None = None) -> tuple[str, int, bool]:
    """Parse peer name, port, and auto_port flag flexibly from command arguments."""
    pos_name = None
    pos_port = None
    explicit_port = port_opt is not None

    if len(pos_args) == 1:
        val = pos_args[0].strip()
        if val.isdigit() and 1 <= int(val) <= 65535:
            pos_port = int(val)
            explicit_port = True
        else:
            pos_name = val
    elif len(pos_args) >= 2:
        val0, val1 = pos_args[0].strip(), pos_args[1].strip()
        if val0.isdigit() and 1 <= int(val0) <= 65535:
            pos_port = int(val0)
            pos_name = val1
            explicit_port = True
        elif val1.isdigit() and 1 <= int(val1) <= 65535:
            pos_name = val0
            pos_port = int(val1)
            explicit_port = True
        else:
            pos_name = " ".join(pos_args)

    name = (name_opt or pos_name or "MeshDrop").strip() or "MeshDrop"
    port = port_opt if port_opt is not None else (pos_port if pos_port is not None else DEFAULT_TCP_PORT)
    auto_port = not explicit_port
    return name, port, auto_port


def main() -> int:
    parser = argparse.ArgumentParser(description="Share files directly with peers on your local network")
    parser.add_argument("args", nargs="*", help="optional peer name or port (e.g. 'Alice', '50000 Alice')")
    parser.add_argument("--name", default=None, help="name shown to other peers")
    parser.add_argument("--share", type=Path, default=None, help="folder to make available")
    parser.add_argument("--downloads", type=Path, default=None, help="where received files are saved")
    parser.add_argument("--port", type=int, default=None, help="TCP file-transfer port")
    parser.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT, help="UDP discovery port")
    args = parser.parse_args()

    name, port, auto_port = parse_peer_args(args.args, args.name, args.port)
    user_folder = "".join(c for c in name if c.isalnum() or c in ("-", "_")).strip() or "MeshDrop"
    share_dir = (args.share if args.share is not None else Path("shared") / user_folder)
    downloads_dir = (args.downloads if args.downloads is not None else Path("downloads") / user_folder)

    app = MeshDrop(name, share_dir, downloads_dir, port, args.discovery_port, auto_port=auto_port)
    try:
        app.start()
        command_loop(app)
    except OSError as exc:
        print(f"Could not start MeshDrop: {exc}", file=sys.stderr)
        return 1
    finally:
        app.close()
    print("MeshDrop stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
