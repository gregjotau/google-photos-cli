import concurrent.futures
import http.server
import importlib.util
import json
from pathlib import Path
import threading
import unittest
import urllib.error
import urllib.request


class Broker(unittest.TestCase):
    def test_result_notification_and_authentication_without_google(self):
        spec = importlib.util.spec_from_file_location(
            "broker_test", Path(__file__).parents[1] / "api-bridge.py"
        )
        broker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(broker)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), broker.H)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def request(path, value=None, origin=None, key=None):
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            req = urllib.request.Request(
                base + path + "?key=" + (key or broker.KEY),
                data=json.dumps(value).encode() if value is not None else None,
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)

        try:
            task = request("/enqueue", {"op": "hello"})
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(request, "/result/" + task["id"])
                request("/result", {"id": task["id"], "ok": True, "value": "synthetic"})
                self.assertEqual(pending.result(timeout=5)["value"], "synthetic")
            for kwargs in ({"origin": "https://example.invalid"}, {"key": "wrong"}):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    request("/next", **kwargs)
                self.assertEqual(caught.exception.code, 403)
                caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
