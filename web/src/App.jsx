import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { gsap } from "gsap";
import {
  Activity, ArrowDownToLine, ArrowDownUp, ArrowRight, Check, CheckCircle2,
  ChevronRight, CircleHelp, Clock3, Copy, File, FileArchive, FileAudio2,
  FileImage, FileText, FileVideo2, FolderOpen, LayoutDashboard,
  LoaderCircle, Moon, MoreHorizontal, Network, Plus, Radio, RefreshCw, Search,
  Sun, Trash2, Upload, Wifi, X,
} from "lucide-react";

function formatBytes(bytes = 0) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function fileIcon(filename) {
  const ext = filename.split(".").pop()?.toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return FileImage;
  if (["mp4", "mov", "mkv", "webm"].includes(ext)) return FileVideo2;
  if (["mp3", "wav", "flac", "m4a"].includes(ext)) return FileAudio2;
  if (["zip", "rar", "7z", "tar", "gz"].includes(ext)) return FileArchive;
  if (["pdf", "doc", "docx", "txt", "md", "csv"].includes(ext)) return FileText;
  return File;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

const navItems = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "files", label: "Shared files", icon: FolderOpen },
  { id: "peers", label: "Peers", icon: Radio },
  { id: "transfers", label: "Transfers", icon: ArrowDownUp },
];

function FileGlyph({ name }) {
  const Icon = fileIcon(name);
  return <span className="file-glyph"><Icon size={19} strokeWidth={1.8} /></span>;
}

function PeerAvatar({ name, index = 0, large = false }) {
  return <span className={`peer-avatar peer-avatar--${index % 4} ${large ? "peer-avatar--large" : ""}`}>
    <span>{name?.slice(0, 1).toUpperCase() || "?"}</span><i />
  </span>;
}

function EmptyState({ icon: Icon, title, detail, action }) {
  return <div className="empty-state"><span className="empty-state-icon"><Icon size={21} /></span><b>{title}</b><span>{detail}</span>{action}</div>;
}

function App() {
  const [snapshot, setSnapshot] = useState(null);
  const [theme, setTheme] = useState(() => {
    const saved = window.localStorage.getItem("meshdrop-theme");
    return saved || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  });
  const [activeView, setActiveView] = useState("overview");
  const [activePeerId, setActivePeerId] = useState(null);
  const [peerReload, setPeerReload] = useState(0);
  const [remoteFiles, setRemoteFiles] = useState([]);
  const [remotePeer, setRemotePeer] = useState(null);
  const [loadingPeerFiles, setLoadingPeerFiles] = useState(false);
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState(null);
  const [modal, setModal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [peerHost, setPeerHost] = useState("");
  const [peerPort, setPeerPort] = useState("50000");
  const [connecting, setConnecting] = useState(false);
  const uploadRef = useRef(null);
  const searchRef = useRef(null);
  const themeSwitchRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api("/api/state");
      setSnapshot(data);
      setActivePeerId((current) => current && data.peers.some((peer) => peer.id === current)
        ? current : data.peers[0]?.id || null);
    } catch (error) {
      setToast({ kind: "error", message: error.message });
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!activePeerId) {
      setRemoteFiles([]);
      setRemotePeer(null);
      return undefined;
    }
    let cancelled = false;
    setLoadingPeerFiles(true);
    api(`/api/peers/${encodeURIComponent(activePeerId)}/files`)
      .then((data) => {
        if (!cancelled) {
          setRemoteFiles(data.files || []);
          setRemotePeer(data.peer || null);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setRemoteFiles([]);
          setToast({ kind: "error", message: error.message });
        }
      })
      .finally(() => { if (!cancelled) setLoadingPeerFiles(false); });
    return () => { cancelled = true; };
  }, [activePeerId, peerReload]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("meshdrop-theme", theme);
    if (!themeSwitchRef.current || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    gsap.fromTo(themeSwitchRef.current, { scale: 0.94 }, { scale: 1, duration: 0.42, ease: "back.out(2)" });
  }, [theme]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 3800);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    function onShortcut(event) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") setModal(false);
    }
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, []);

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setBusy(true);
    try {
      for (const file of files) {
        const body = new FormData();
        body.append("file", file);
        await api("/api/share", { method: "POST", body });
      }
      await refresh();
      setToast({ kind: "success", message: files.length === 1 ? "Added to your shared files." : `${files.length} files added to your library.` });
    } catch (error) {
      setToast({ kind: "error", message: error.message });
    } finally {
      setBusy(false);
      if (uploadRef.current) uploadRef.current.value = "";
    }
  }

  async function downloadFile(peerId, filename) {
    try {
      await api("/api/transfers", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ peer_id: peerId, filename }),
      });
      setToast({ kind: "success", message: `Transfer queued: ${filename}` });
      refresh();
    } catch (error) {
      setToast({ kind: "error", message: error.message });
    }
  }

  async function removeFile(filename) {
    try {
      await api(`/api/share/${encodeURIComponent(filename)}`, { method: "DELETE" });
      await refresh();
      setToast({ kind: "success", message: "File removed from your shared library." });
    } catch (error) {
      setToast({ kind: "error", message: error.message });
    }
  }

  async function addPeer(event) {
    event.preventDefault();
    setConnecting(true);
    try {
      await api("/api/peers", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: peerHost.trim(), port: Number(peerPort) }),
      });
      await refresh();
      setPeerHost("");
      setModal(false);
      setActiveView("peers");
      setToast({ kind: "success", message: "Peer connected." });
    } catch (error) {
      setToast({ kind: "error", message: error.message });
    } finally {
      setConnecting(false);
    }
  }

  async function copyAddress() {
    try {
      await navigator.clipboard.writeText(`${snapshot.local_ip}:${snapshot.tcp_port}`);
      setToast({ kind: "success", message: "Peer address copied." });
    } catch {
      setToast({ kind: "error", message: "Clipboard access is unavailable in this browser." });
    }
  }

  function toggleTheme() {
    setTheme((current) => current === "dark" ? "light" : "dark");
  }

  if (!snapshot) return <div className="app-loading"><LoaderCircle className="spin" size={25} />Connecting to your local node…</div>;

  const peers = snapshot.peers || [];
  const localFiles = snapshot.local_files || [];
  const transfers = snapshot.transfers || [];
  const selectedPeer = peers.find((peer) => peer.id === activePeerId);
  const filteredLocal = localFiles.filter((file) => file.name.toLowerCase().includes(query.toLowerCase()));
  const filteredRemote = remoteFiles.filter((file) => file.name.toLowerCase().includes(query.toLowerCase()));
  const filteredPeers = peers.filter((peer) => peer.name.toLowerCase().includes(query.toLowerCase())
    || peer.host.toLowerCase().includes(query.toLowerCase()));
  const activeTransfers = transfers.filter((item) => ["connecting", "downloading"].includes(item.status));
  const completeTransfers = transfers.filter((item) => item.status === "complete").length;
  const selectedNav = navItems.find((item) => item.id === activeView) || navItems[0];

  return (
    <div className="power-app" data-theme={theme} onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
      onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
      onDrop={(event) => { event.preventDefault(); setDragging(false); uploadFiles(event.dataTransfer.files); }}>
      <input ref={uploadRef} type="file" multiple hidden onChange={(event) => uploadFiles(event.target.files)} />

      <aside className="sidebar">
        <a className="brand" href="#workspace" aria-label="MeshDrop workspace">
          <span className="brand-mark"><ArrowDownUp size={18} strokeWidth={2.4} /></span>
          <span className="brand-name">mesh<span>drop</span><i>.</i></span>
        </a>

        <div className="sidebar-node">
          <span className="node-avatar">{snapshot.identity.name.slice(0, 1).toUpperCase()}</span>
          <span className="node-identity"><b>{snapshot.identity.name}</b><small><i className="network-pulse" /> Node online</small></span>
          <button className="sidebar-more" title="Copy peer address" onClick={copyAddress}><MoreHorizontal size={18} /></button>
        </div>

        <div className="nav-label">WORKSPACE</div>
        <nav className="side-nav" aria-label="Workspace navigation">
          {navItems.map(({ id, label, icon: Icon }) => (
            <button key={id} className={`nav-item ${activeView === id ? "nav-item--active" : ""}`} onClick={() => { setActiveView(id); setQuery(""); }}>
              <Icon size={19} strokeWidth={1.8} /><span>{label}</span>
              {id === "files" && <b className="nav-count">{localFiles.length}</b>}
              {id === "peers" && <b className="nav-count">{peers.length}</b>}
              {id === "transfers" && activeTransfers.length > 0 && <b className="nav-count nav-count--live">{activeTransfers.length}</b>}
            </button>
          ))}
        </nav>

        <div className="sidebar-spacer" />
        <div className="sidebar-network">
          <div className="sidebar-network-head"><span className="network-indicator"><Wifi size={15} /></span><span><b>Local network</b><small>Discovery active</small></span><span className="network-pulse" /></div>
          <div className="sidebar-network-address"><span>{snapshot.local_ip}:{snapshot.tcp_port}</span><button onClick={copyAddress} title="Copy address"><Copy size={13} /></button></div>
          <div className="sidebar-network-foot"><span>UDP {snapshot.discovery_port}</span><span>DIRECT TCP</span></div>
        </div>
        <button className="sidebar-help" onClick={() => setToast({ kind: "info", message: "Peers on the same LAN appear here automatically. Share your IP and TCP port to connect manually." })}><CircleHelp size={17} /><span>Network help</span><ChevronRight size={15} /></button>
        <div className="sidebar-version">MESHDROP <span>v1.0</span><i /> PRIVATE LAN NODE</div>
      </aside>

      <div className="app-main">
        <header className="app-topbar">
          <div className="breadcrumb"><span>Workspace</span><ChevronRight size={15} /><b>{selectedNav.label}</b></div>
          <div className="topbar-controls">
            <label className="global-search"><Search size={17} /><input ref={searchRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Search ${activeView === "peers" ? "peers" : "files"}…`} /><kbd>Ctrl K</kbd></label>
            <div className="theme-switch-group"><span>{theme === "dark" ? "Dark" : "Light"}</span><button ref={themeSwitchRef} className="theme-switch" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} aria-pressed={theme === "light"} title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}><span className="theme-switch-track"><motion.span className="theme-switch-thumb" animate={{ x: theme === "light" ? 26 : 0 }} transition={{ type: "spring", stiffness: 470, damping: 29 }}><AnimatePresence mode="wait" initial={false}><motion.span key={theme} className="theme-switch-icon" initial={{ opacity: 0, rotate: -75, scale: .55 }} animate={{ opacity: 1, rotate: 0, scale: 1 }} exit={{ opacity: 0, rotate: 75, scale: .55 }} transition={{ duration: .18 }}>{theme === "dark" ? <Moon size={14} /> : <Sun size={14} />}</motion.span></AnimatePresence></motion.span></span></button></div>
            <button className="topbar-icon" title="Refresh workspace" onClick={refresh}><RefreshCw size={18} /></button>
            <span className="topbar-separator" />
            <button className="button button--primary" onClick={() => uploadRef.current?.click()} disabled={busy}><Plus size={18} /> Add files</button>
          </div>
        </header>

        <main id="workspace" className="content-area">
          <div className="page-heading">
            <div><div className="page-overline"><span className="network-pulse" /> LOCAL PEER WORKSPACE</div><h1>{activeView === "overview" ? "Overview" : selectedNav.label}</h1><p>{activeView === "overview" ? "Manage your shared files and nearby devices." : activeView === "files" ? "Files available for direct transfer from this device." : activeView === "peers" ? "Devices discovered on your local network." : "Track incoming files and verify completed transfers."}</p></div>
            <div className="heading-actions"><div className="sync-state"><span className="network-pulse" /> LIVE SYNC</div>{activeView === "peers" && <button className="button button--secondary" onClick={() => setModal(true)}><Plus size={18} /> Add peer</button>}</div>
          </div>

          <AnimatePresence mode="wait">
            {activeView === "overview" && <motion.div className="page-view" key="overview" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -5 }} transition={{ duration: .2 }}>
              <section className="metrics-grid">
                <Metric icon={FolderOpen} label="Shared files" value={localFiles.length} meta={formatBytes(localFiles.reduce((sum, file) => sum + file.size, 0)) + " total"} />
                <Metric icon={Radio} label="Nearby peers" value={peers.length} meta={peers.length ? "Ready to browse" : "Scanning LAN"} accent />
                <Metric icon={ArrowDownToLine} label="Active transfers" value={activeTransfers.length} meta={activeTransfers.length ? "In progress" : "Queue is clear"} />
                <Metric icon={CheckCircle2} label="Completed" value={completeTransfers} meta="Verified with SHA-256" />
              </section>

              <section className="overview-grid">
                <Panel title="Shared files" kicker="AVAILABLE FROM THIS DEVICE" count={localFiles.length} action={<button className="inline-action" onClick={() => setActiveView("files")}>View all <ArrowRight size={15} /></button>}>
                  {filteredLocal.length ? <FileTable files={filteredLocal.slice(0, 6)} local onRemove={removeFile} /> : <EmptyState icon={FolderOpen} title={query ? "No matching files." : "Your shared folder is empty."} detail={query ? "Try another search." : "Add files so nearby peers can find them."} action={!query && <button className="button button--secondary button--small" onClick={() => uploadRef.current?.click()}><Plus size={16} /> Add files</button>} />}
                  <button className="drop-inline" onClick={() => uploadRef.current?.click()}><Upload size={17} /><span><b>Drop files anywhere</b> or click here to add them to your library.</span></button>
                </Panel>

                <Panel title="Nearby peers" kicker={`UDP DISCOVERY · PORT ${snapshot.discovery_port}`} count={peers.length} action={<button className="icon-button" title="Connect to peer by address" onClick={() => setModal(true)}><Plus size={19} /></button>}>
                  {filteredPeers.length ? <PeerList peers={filteredPeers.slice(0, 5)} activePeerId={activePeerId} onSelect={(id) => { setActivePeerId(id); setActiveView("peers"); }} /> : <EmptyState icon={Radio} title={query ? "No peers match." : "No peers found yet."} detail={query ? "Try a different name or address." : "Devices on this LAN appear here."} action={!query && <button className="button button--secondary button--small" onClick={() => setModal(true)}><Plus size={16} /> Add by address</button>} />}
                  <div className="panel-status"><span className="status-online"><i className="network-pulse" /> DISCOVERY RUNNING</span><button onClick={refresh}><RefreshCw size={14} /> Scan again</button></div>
                </Panel>
              </section>

              <TransferPanel transfers={transfers.slice(0, 4)} onViewAll={() => setActiveView("transfers")} />
            </motion.div>}

            {activeView === "files" && <motion.div className="page-view" key="files" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -5 }} transition={{ duration: .2 }}>
              <section className="file-workspace panel">
                <div className="panel-toolbar"><div><span className="toolbar-title">Shared library</span><span className="toolbar-subtitle">{localFiles.length} files · {formatBytes(localFiles.reduce((sum, file) => sum + file.size, 0))}</span></div><button className="button button--primary" onClick={() => uploadRef.current?.click()} disabled={busy}><Upload size={17} /> Upload files</button></div>
                <div className="upload-strip" onClick={() => uploadRef.current?.click()}><Upload size={20} /><span><b>Drag files here</b> or browse from this device</span><span className="upload-cap">5 GB MAX</span></div>
                {filteredLocal.length ? <FileTable files={filteredLocal} local onRemove={removeFile} /> : <EmptyState icon={FolderOpen} title={query ? "No matching files." : "No shared files yet."} detail={query ? "Clear the search to see all files." : "Add a file to make it available to nearby peers."} />}
                <div className="table-foot"><span>Files are sent directly from this device over TCP.</span><button onClick={refresh}><RefreshCw size={14} /> Refresh list</button></div>
              </section>
            </motion.div>}

            {activeView === "peers" && <motion.div className="page-view" key="peers" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -5 }} transition={{ duration: .2 }}>
              <section className="peer-workspace">
                <div className="panel peers-directory">
                  <div className="panel-toolbar"><div><span className="toolbar-title">Discovered devices</span><span className="toolbar-subtitle">{peers.length} on this network</span></div><button className="button button--secondary" onClick={() => setModal(true)}><Plus size={17} /> Add peer</button></div>
                  {filteredPeers.length ? <PeerList peers={filteredPeers} activePeerId={activePeerId} onSelect={setActivePeerId} expanded /> : <EmptyState icon={Radio} title="No peers found." detail="Wait for LAN discovery or add a peer by IP address." action={<button className="button button--secondary button--small" onClick={() => setModal(true)}><Plus size={16} /> Add peer by address</button>} />}
                </div>
                <div className="panel peer-files-panel">
                  <div className="panel-toolbar"><div><span className="toolbar-title">{remotePeer?.name ? `${remotePeer.name}’s files` : "Peer file browser"}</span><span className="toolbar-subtitle">{selectedPeer ? `${selectedPeer.host}:${selectedPeer.port}` : "Select a device to browse"}</span></div>{selectedPeer && <button className="icon-button" onClick={() => setPeerReload((value) => value + 1)} title="Refresh peer files"><RefreshCw size={17} /></button>}</div>
                  {loadingPeerFiles ? <div className="panel-loading"><LoaderCircle className="spin" size={21} /> Connecting to peer…</div>
                    : !selectedPeer ? <EmptyState icon={Network} title="Choose a peer." detail="Select a device to open its shared library." />
                      : filteredRemote.length ? <FileTable files={filteredRemote} onDownload={(name) => downloadFile(selectedPeer.id, name)} />
                        : <EmptyState icon={FolderOpen} title={query ? "No matching files." : "No files shared by this peer."} detail={query ? "Try a different search." : "Their shared folder may be empty."} />}
                </div>
              </section>
            </motion.div>}

            {activeView === "transfers" && <motion.div className="page-view" key="transfers" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -5 }} transition={{ duration: .2 }}>
              <section className="panel transfer-workspace"><div className="panel-toolbar"><div><span className="toolbar-title">Transfer queue</span><span className="toolbar-subtitle">Incoming files · verified before saving</span></div><span className="queue-state"><Activity size={16} /> {activeTransfers.length} active</span></div><TransferTable transfers={transfers} /></section>
            </motion.div>}
          </AnimatePresence>

          <footer className="app-footer"><span><b>MESHDROP</b> LOCAL PEER NODE <i /> {snapshot.identity.id.toUpperCase()}</span><span>UDP {snapshot.discovery_port} <i /> TCP {snapshot.tcp_port} <i /> {snapshot.local_ip}</span></footer>
        </main>
      </div>

      <AnimatePresence>{dragging && <motion.div className="drop-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}><div><Upload size={34} /><b>Drop to add to shared files</b><span>Your peers can download them as soon as they land.</span></div></motion.div>}</AnimatePresence>
      <AnimatePresence>{toast && <motion.div className={`toast toast--${toast.kind}`} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 10 }}><span>{toast.kind === "success" ? <CheckCircle2 size={18} /> : toast.kind === "error" ? <X size={18} /> : <Network size={18} />}</span><p>{toast.message}</p><button onClick={() => setToast(null)} aria-label="Dismiss"><X size={16} /></button></motion.div>}</AnimatePresence>

      <AnimatePresence>{modal && <motion.div className="modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={(event) => { if (event.target === event.currentTarget) setModal(false); }}>
        <motion.form className="peer-modal" onSubmit={addPeer} initial={{ opacity: 0, y: 12, scale: .97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 8, scale: .98 }}>
          <div className="modal-heading"><span className="modal-icon"><Network size={19} /></span><button type="button" className="modal-close" onClick={() => setModal(false)} aria-label="Close"><X size={18} /></button></div>
          <div className="modal-kicker">MANUAL CONNECTION</div><h2>Add a peer</h2><p>Connect directly using the other device’s IP address and MeshDrop TCP port.</p>
          <label className="form-label">DEVICE IP OR HOSTNAME<input autoFocus value={peerHost} onChange={(event) => setPeerHost(event.target.value)} placeholder="192.168.1.24" required /></label>
          <label className="form-label">TCP PORT<input type="number" min="1" max="65535" value={peerPort} onChange={(event) => setPeerPort(event.target.value)} required /></label>
          <div className="modal-actions"><button type="button" className="button button--secondary" onClick={() => setModal(false)}>Cancel</button><button className="button button--primary" disabled={connecting || !peerHost.trim()}>{connecting ? <LoaderCircle className="spin" size={17} /> : <Network size={17} />}{connecting ? "Connecting…" : "Connect peer"}</button></div>
        </motion.form>
      </motion.div>}</AnimatePresence>
    </div>
  );
}

function Metric({ icon: Icon, label, value, meta, accent = false }) {
  return <div className={`metric-card ${accent ? "metric-card--accent" : ""}`}><span className="metric-icon"><Icon size={20} /></span><div className="metric-data"><span>{label}</span><b>{value.toString().padStart(2, "0")}</b><small>{meta}</small></div></div>;
}

function Panel({ title, kicker, count, action, children }) {
  return <section className="panel overview-panel"><div className="panel-heading"><div><div className="section-kicker">{kicker}</div><h2>{title}<span className="count-badge">{count}</span></h2></div>{action}</div>{children}</section>;
}

function FileTable({ files, local = false, onRemove, onDownload }) {
  return <div className="file-table-wrap"><table className="file-table"><thead><tr><th>FILE NAME</th><th>TYPE</th><th>SIZE</th><th>{local ? "AVAILABILITY" : "SOURCE"}</th><th className="table-action-head">ACTION</th></tr></thead><tbody>
    {files.map((file) => {
      const ext = file.name.includes(".") ? file.name.split(".").pop().toUpperCase() : "FILE";
      return <tr key={file.name}><td><span className="table-file-name"><FileGlyph name={file.name} /><b title={file.name}>{file.name}</b></span></td><td><span className="type-chip">{ext}</span></td><td className="mono-cell">{formatBytes(file.size)}</td><td>{local ? <span className="availability"><i className="network-pulse" /> Shared</span> : <span className="source-cell"><ArrowDownToLine size={14} /> Ready</span>}</td><td className="table-action-cell">{local ? <button className="table-action table-action--danger" title="Remove from shared library" onClick={() => onRemove?.(file.name)}><Trash2 size={17} /><span>Remove</span></button> : <button className="table-action table-action--download" onClick={() => onDownload?.(file.name)}><ArrowDownToLine size={17} /><span>Download</span></button>}</td></tr>;
    })}
  </tbody></table></div>;
}

function PeerList({ peers, activePeerId, onSelect, expanded = false }) {
  return <div className={`peer-list ${expanded ? "peer-list--expanded" : ""}`}>
    {peers.map((peer, index) => <button key={peer.id} className={`peer-row ${activePeerId === peer.id ? "peer-row--active" : ""}`} onClick={() => onSelect(peer.id)}>
      <PeerAvatar name={peer.name} index={index} large={expanded} />
      <span className="peer-row-main"><b>{peer.name}</b><small>{peer.host}:{peer.port}</small></span>
      <span className="peer-row-state"><i className="network-pulse" />{peer.last_seen_seconds <= 5 ? "Online" : `${peer.last_seen_seconds}s ago`}</span>
      <ChevronRight size={17} className="peer-chevron" />
    </button>)}
  </div>;
}

function TransferPanel({ transfers, onViewAll }) {
  return <section className="panel recent-panel"><div className="panel-heading"><div><div className="section-kicker">RECENT ACTIVITY</div><h2>Transfers</h2></div><button className="inline-action" onClick={onViewAll}>Open queue <ArrowRight size={15} /></button></div><TransferTable transfers={transfers} compact /></section>;
}

function TransferTable({ transfers, compact = false }) {
  if (!transfers.length) return <EmptyState icon={ArrowDownUp} title="No transfers yet." detail="Downloads from peers will appear here." />;
  return <div className={`transfer-table ${compact ? "transfer-table--compact" : ""}`}>
    {transfers.map((transfer) => <motion.div className="transfer-entry" key={transfer.id} layout>
      <span className={`transfer-status-icon transfer-status-icon--${transfer.status}`}>{transfer.status === "complete" ? <CheckCircle2 size={19} /> : transfer.status === "failed" ? <X size={19} /> : <LoaderCircle className="spin" size={19} />}</span>
      <FileGlyph name={transfer.filename} />
      <span className="transfer-entry-info"><b title={transfer.filename}>{transfer.filename}</b><small>{transfer.status === "complete" ? `Received from ${transfer.peer_name} · SHA-256 verified` : transfer.status === "failed" ? transfer.error : `From ${transfer.peer_name} · ${formatBytes(transfer.received)} of ${formatBytes(transfer.size || 0)}`}</small>{["connecting", "downloading"].includes(transfer.status) && <span className="transfer-progress"><i style={{ width: `${transfer.progress}%` }} /></span>}</span>
      <span className={`transfer-entry-meta transfer-entry-meta--${transfer.status}`}>{transfer.status === "complete" ? "COMPLETE" : transfer.status === "failed" ? "FAILED" : `${transfer.progress}%`}</span>
      {!compact && <span className="transfer-time"><Clock3 size={14} /> {new Date(transfer.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
    </motion.div>)}
  </div>;
}

export default App;
