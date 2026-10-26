# MeshDrop

MeshDrop is a small peer-to-peer file sharing project for a computer networks course. Devices on the same local network discover one another with UDP, then transfer files directly over TCP. Each running copy is both a client and a server; there is no central file server or account system.

It takes inspiration from BitChat's nearby, serverless communication idea. BitChat uses Bluetooth mesh for messaging; MeshDrop uses ordinary LAN networking so it can be implemented and demonstrated quickly in Python.

## Requirements

- Python 3.10 or newer
- Node.js 20.19+ or 22.12+ (only needed to build the interface)
- Microsoft Edge WebView2 Runtime on Windows (usually already installed)
- Two computers on the same Wi-Fi/LAN, or two terminals on one computer

## Run the desktop app

Install the Python dependency and build the frontend once:

```powershell
python -m pip install -r requirements.txt
cd web
npm install
npm run build
cd ..
```

Start a peer. MeshDrop opens in its own desktop window; the local Flask server runs privately in the background to serve the interface and API.
The native window uses the MeshDrop logo as its Windows title bar and taskbar icon.

```powershell
python app.py 50000 Alice
```

The quick form is `python app.py <port> <name>`: the port is your peer-to-peer TCP port (default `50000`), and the name is what other devices see. Received files are saved in `downloads/<name>/`, for example `downloads/Alice/`. The older `--name Alice --port 50000` form still works too.

Use the sidebar to switch between the overview, shared files, peers, and transfer queue. Add files with the upload button or drag-and-drop. If automatic discovery is blocked, use **Peers → Add peer** with the other node's IP address and TCP port. The dashboard runs on your own computer; the actual file transfer still goes directly between peers over TCP.

On a second computer, run the same install/build steps once and start another peer. Its own MeshDrop window will open:

```powershell
python app.py 50000 Bob
```

For two peers on one computer, give the second process a different TCP port and separate folders:

```powershell
python app.py 50001 Bob --web-port 8001 --share bob_shared
```

Peers on the same LAN should appear automatically within a few seconds. If UDP discovery is blocked by Wi-Fi isolation or a firewall, use **Peers → Add peer** to enter the other device's IP and TCP port. Received files are SHA-256 checked before saving to the named folder under `downloads/`. Use `--downloads` to choose a different base folder.

The original command-line peer remains available with `python meshdrop.py --name Alice`.

## Network design

```text
Alice                                  Bob
UDP broadcast: "I'm Alice"  ------->  UDP listener discovers Alice
TCP server  <----------------------->  TCP client requests file
          file bytes + SHA-256 check
```

- **Peer discovery:** each process broadcasts a small JSON announcement over UDP every four seconds on port `37020`. Announcements include a peer ID, display name, address, and TCP port. A peer is removed from the nearby list after 16 seconds without an announcement.
- **File transfer:** a peer connects to the other peer's TCP port (default `50000`). A newline-delimited JSON request asks for a shared-file listing or one file; file contents follow as raw bytes in chunks.
- **Desktop interface:** Flask serves the local UI and API on loopback, and pywebview displays it in a native desktop window. The local server does not act as a relay or carry peer-to-peer file bytes. The React interface uses Framer Motion and GSAP; Urbanist and DM Mono are bundled locally with the frontend build.
- **Integrity:** the sender includes the file size and SHA-256 digest. The receiver calculates the digest while downloading and only keeps the file if it matches.
- **Scope:** this version works on a shared LAN and transfers directly between two peers. It does not implement Bluetooth, multi-hop relaying, internet rendezvous, encryption, authentication, resume, or NAT traversal. Anyone on the LAN who can reach a peer can download the files it shares, so use it on a trusted network for demos.

## Course concepts demonstrated

- UDP broadcast for service discovery
- TCP connection setup and reliable byte-stream transfer
- Application-layer request/response framing over TCP
- Peer-to-peer topology without a central data server
- Chunked I/O, timeouts, peer expiry, and content-integrity checks

## Project structure

```text
app.py             Flask API host and native pywebview desktop launcher
meshdrop.py        UDP discovery, TCP protocol, transfer, and CLI
web/src/           React interface, custom MeshDrop logo, GSAP, and Framer Motion
web/public/        App logo, browser favicon, and Windows app icon
web/dist/          Built frontend served by Flask
shared/            Files this peer offers (created on first run)
downloads/<name>/  Files received by this peer (created on first run)
```
