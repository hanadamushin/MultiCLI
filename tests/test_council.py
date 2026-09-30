import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("PERSONAL_COUNCIL_HOME", str(Path(__file__).resolve().parent.parent / "local-data"))
import app


class CouncilTests(unittest.TestCase):
    def test_modes_and_cross_critique_routing(self):
        for mode, count in (("Quick", 3), ("Council", 7), ("Deep Debate", 9)):
            with self.subTest(mode=mode):
                calls = []
                def fake(kind, prompt, run):
                    calls.append((kind, prompt))
                    return f"{kind}-response-{len(calls)}"
                run = {"logs": []}
                with patch.object(app, "cli_probe", return_value={"authenticated": True}), patch.object(app, "invoke", side_effect=fake):
                    app.run_council(run, "Research question", {"name": "test", "context": "sleep", "instructions": "evidence"}, mode)
                self.assertEqual(run["status"], "complete")
                self.assertEqual(len(calls), count)
                if mode != "Quick":
                    revisions = [(k, p) for k, p in calls if "批評を読んで" in p]
                    for kind, prompt in revisions:
                        other = "claude" if kind == "codex" else "codex"
                        self.assertIn(run["critique_" + other], prompt)
                        self.assertIn(run[kind], prompt)
                    self.assertIn(run["codex_revision"], calls[-1][1])
                    self.assertIn(run["claude_revision"], calls[-1][1])

    def test_login_failure_stops_before_inference(self):
        run = {"logs": []}
        with patch.object(app, "cli_probe", return_value={"authenticated": False, "message": "login required"}), patch.object(app, "invoke") as invoke:
            app.run_council(run, "q", {"name": "test"}, "Quick")
            invoke.assert_not_called()
        self.assertEqual(run["status"], "error")
        self.assertIn("login required", run["error"])

    def test_prompts_use_stdin_without_shell(self):
        prompt = '研究 & $(echo secret) "quoted" ' + '長い文' * 20000
        for kind in ("codex", "claude"):
            process = MagicMock(returncode=0)
            process.communicate.return_value = ("answer", "")
            with patch.object(app, "resolve_cli", return_value=["native-cli"]), patch.object(app.subprocess, "Popen", return_value=process) as popen:
                result = app.invoke(kind, prompt, {"logs": [], "research_context": "shared"})
            self.assertEqual(result, "answer")
            self.assertFalse(popen.call_args.kwargs["shell"])
            self.assertNotIn(prompt, popen.call_args.args[0])
            self.assertIn(prompt, process.communicate.call_args.kwargs["input"])
            self.assertIn("shared", process.communicate.call_args.kwargs["input"])

    def test_api_billing_environment_not_inherited(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test", "OPENAI_API_KEY": "test", "CODEX_API_KEY": "test"}):
            self.assertNotIn("ANTHROPIC_API_KEY", app.cli_env())
            self.assertNotIn("OPENAI_API_KEY", app.cli_env())
            self.assertNotIn("CODEX_API_KEY", app.cli_env())

    def test_connection_error_explains_network_issue(self):
        process = MagicMock(returncode=1)
        process.communicate.return_value = ("", "API Error: Connection refused (ECONNREFUSED)")
        with patch.object(app.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "プロキシ・ファイアウォール"):
                app.invoke_process(["cli"], "q", "claude", {"logs": []}, {"timeout_seconds": 60}, ".")

    def test_samples_copied_and_user_edits_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "PROFILE_DIR", Path(directory)):
            profiles = app.read_profiles()
            self.assertEqual(len(profiles), 2)
            target = Path(directory) / "sleep_research.json"
            target.write_text(json.dumps({"name": "My private research"}), encoding="utf-8")
            app.read_profiles()
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["name"], "My private research")


class LocalHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(app, "PROFILE_DIR", root / "profiles"), patch.object(app, "DATA_DIR", root), patch.object(app, "SETTINGS_FILE", root / "settings.json")]
        for item in self.patches: item.start()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.port = self.server.server_port
        self.port_patch = patch.object(app, "PORT", self.port)
        self.port_patch.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.port_patch.stop()
        for item in self.patches: item.stop()
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request(method, path, json.dumps(data) if data is not None else None, headers or {})
        response = connection.getresponse()
        status, body = response.status, response.read()
        connection.close()
        return status, body

    def test_host_origin_and_session_token(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "foreign.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/session", headers={"Origin": "https://foreign.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/settings", {})[0], 403)
        status, body = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        token = json.loads(body)["token"]
        self.assertEqual(self.request("POST", "/api/settings", {"timeout_seconds": 60}, {"X-Council-Token": token})[0], 200)

    def test_profile_write_cannot_escape_directory(self):
        headers = {"X-Council-Token": app.SESSION_TOKEN}
        self.assertEqual(self.request("POST", "/api/profiles", {"id": "../escape", "profile": {"name": "test"}}, headers)[0], 400)
        self.assertEqual(self.request("POST", "/api/profiles", {"id": "my_research", "profile": {"name": "私の研究"}}, headers)[0], 200)
        self.assertTrue((app.PROFILE_DIR / "my_research.json").is_file())
        self.assertEqual(self.request("GET", "/")[0], 200)


if __name__ == "__main__":
    unittest.main()
