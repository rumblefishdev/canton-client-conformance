"""Fault-injecting proxies between an SDK and the participant.

HttpProxy  JSON Ledger API. Forwards everything; records every POST to /v2/commands/*. When armed it faults the next
           such POST once:
             reject-before  answers with a retriable Canton error (SEQUENCER_BACKPRESSURE) without forwarding,
                            as a participant under load does;
             lose-after     forwards, lets the command commit, then answers with REQUEST_TIMEOUT (category 3,
                            outcome unknown), as the participant's command service does when its wait times out.
           Bodies have the shape of a captured Canton 3.7.3 error. HTTP status: the gRPC-to-HTTP mapping
           (ABORTED 409, DEADLINE_EXCEEDED 504).
TcpProxy   gRPC Ledger API. Forwards bytes; when armed, it drops the next connection at the first server-to-client
           chunk that arrives 300 ms after the client last sent anything, i.e. the response to a request that has
           already been forwarded and committed.
"""
import http.client, json, socket, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def canton_error(code, cause, category, grpc):
    cid = uuid.uuid4().hex
    return {"code": code, "cause": cause, "correlationId": cid, "traceId": cid, "context": {}, "resources": [],
            "errorCategory": category, "grpcCodeValue": grpc, "retryInfo": "1 second", "definiteAnswer": False}


FAULTS = {
    "reject-before": (409, lambda: canton_error(
        "SEQUENCER_BACKPRESSURE", "The sequencer is overloaded: rejected by the fault proxy", 2, 10)),
    "lose-after": (504, lambda: canton_error(
        "REQUEST_TIMEOUT", "Timed out while awaiting for a completion: response replaced by the fault proxy", 3, 4)),
}


class HttpProxy:
    def __init__(self, upstream):
        u = urlsplit(upstream)
        self.host, self.port = u.hostname, u.port
        self.fault, self.attempts, self.lock = None, [], threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def relay(self, method):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
                rec = None
                if method == "POST" and self.path.startswith("/v2/commands/"):
                    cmds = json.loads(body or b"{}")
                    # submit-and-wait-for-transaction wraps the command set in {"commands": ...}; submit-and-wait does not
                    if isinstance(cmds.get("commands"), dict):
                        cmds = cmds["commands"]
                    rec = {"path": self.path, "commandId": cmds.get("commandId")}
                    with proxy.lock:
                        fault, proxy.fault = proxy.fault, None
                        proxy.attempts.append(rec)
                    rec["fault"] = fault
                    if fault == "reject-before":
                        return self.reply(*FAULTS[fault], rec=rec)
                conn = http.client.HTTPConnection(proxy.host, proxy.port, timeout=120)
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")}
                conn.request(method, self.path, body=body or None, headers=headers)
                r = conn.getresponse()
                data = r.read()
                if rec is not None:
                    rec["upstream"] = r.status
                    if rec["fault"] == "lose-after":
                        return self.reply(*FAULTS["lose-after"], rec=rec)
                self.send_response(r.status)
                for k, v in r.getheaders():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def reply(self, status, body, rec):
                rec["replied"] = status
                data = json.dumps(body()).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.relay("GET")

            def do_POST(self):
                self.relay("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def arm(self, fault):
        with self.lock:
            self.fault, self.attempts = fault, []

    def close(self):
        self.server.shutdown()


class TcpProxy:
    QUIET = 0.3

    def __init__(self, upstream_port):
        self.upstream = ("127.0.0.1", upstream_port)
        self.armed, self.connections, self.dropped = False, 0, 0
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.endpoint = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        threading.Thread(target=self.accept, daemon=True).start()

    def arm(self):
        self.armed, self.connections, self.dropped = True, 0, 0

    def accept(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            server = socket.create_connection(self.upstream)
            drop, self.armed = self.armed, False
            self.connections += 1
            state = {"last_client": time.time(), "sent": False}
            threading.Thread(target=self.pump, args=(client, server, state, None), daemon=True).start()
            threading.Thread(target=self.pump, args=(server, client, state, drop), daemon=True).start()

    def pump(self, src, dst, state, drop):
        to_server = drop is None
        try:
            while data := src.recv(65536):
                now = time.time()
                if to_server:
                    state["last_client"], state["sent"] = now, True
                elif drop and state["sent"] and now - state["last_client"] >= self.QUIET:
                    self.dropped += 1
                    break
                dst.sendall(data)
        except OSError:
            pass
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()

    def close(self):
        self.sock.close()
