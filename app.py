from __future__ import annotations

import json
import argparse
import secrets
import signal
import tempfile
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import shutil
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = ROOT / "profiles"
# User data lives outside the source checkout, so updates do not overwrite research.
DATA_DIR = Path(os.environ.get("PERSONAL_COUNCIL_HOME") or (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PersonalCouncil"
    if os.name == "nt" else Path.home() / ".local" / "share" / "personal-council"))
PROFILE_DIR = DATA_DIR / "profiles"
SESSION_TOKEN = secrets.token_urlsafe(32)
PORT = 8765
SETTINGS_FILE = DATA_DIR / "settings.json"
STATIC_DIR = ROOT / "static"
RUNS: dict[str, dict] = {}
RUNS_LOCK = threading.Lock()
DEFAULT_SETTINGS = {"codex_command": "codex", "claude_command": "claude", "timeout_seconds": 240}


def settings() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        value = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return DEFAULT_SETTINGS.copy()
        return {**DEFAULT_SETTINGS, **value}
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SETTINGS.copy()


def read_profiles() -> list[dict]:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    for sample in SAMPLE_DIR.glob("*.json"):
        target = PROFILE_DIR / sample.name
        if not target.exists():
            shutil.copyfile(sample, target)
    result = []
    for path in sorted(PROFILE_DIR.glob("*.json")):
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(profile, dict) or not isinstance(profile.get("name"), str):
                continue
            if any(not isinstance(profile.get(key, ""), str) for key in ("context", "instructions", "description")):
                continue
            profile["id"] = path.stem
            result.append(profile)
        except (OSError, json.JSONDecodeError):
            continue
    return result


def emit(run: dict, message: str, level: str = "info") -> None:
    item = {"time": time.strftime("%H:%M:%S"), "message": message, "level": level}
    with RUNS_LOCK:
        run["logs"].append(item)
        run["updated"] = time.time()


def resolve_cli(command: str) -> list[str]:
    executable = shutil.which(command) if not Path(command).is_absolute() else command
    if not executable or not Path(executable).is_file():
        raise RuntimeError("CLIが見つかりません。導入後に設定で実行ファイルのパスを指定してください。")
    path = Path(executable)
    if path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        # npm's Windows shim must not be passed to cmd.exe with user input.
        entry = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = shutil.which("node")
        if path.stem == "codex" and entry.is_file() and node:
            return [node, str(entry)]
        raise RuntimeError("シェル起動用ファイルは使用できません。ネイティブのCLI実行ファイルを設定してください。")
    return [str(path)]


def cli_env() -> dict:
    # Subscription login only: do not accidentally inherit API billing credentials.
    excluded = {"OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"}
    return {**{k: v for k, v in os.environ.items() if k.upper() not in excluded}, "NO_COLOR": "1"}


def cli_probe(command: str, kind: str) -> dict:
    try:
        argv = resolve_cli(command)
    except RuntimeError as exc:
        return {"installed": False, "authenticated": False, "message": str(exc)}
    try:
        options = dict(capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=12, shell=False, env=cli_env())
        version = subprocess.run(argv + ["--version"], **options)
        if version.returncode:
            return {"installed": True, "authenticated": False, "message": "CLIのバージョン確認に失敗しました。"}
        auth = subprocess.run(argv + (["login", "status"] if kind == "Codex" else ["auth", "status"]), **options)
        if kind == "Codex":
            raw = (auth.stdout + auth.stderr).lower()
            logged_in = auth.returncode == 0 and "chatgpt" in raw
        else:
            try:
                detail = json.loads(auth.stdout)
                logged_in = bool(detail.get("loggedIn")) and detail.get("authMethod") == "claude.ai"
            except json.JSONDecodeError:
                logged_in = False
        login = "codex login" if kind == "Codex" else "claude auth login"
        message = "契約アカウントでログイン済み" if logged_in else f"契約アカウントのログインが必要です。ターミナルで {login} を実行してください。"
        return {"installed": True, "authenticated": logged_in,
                "message": version.stdout.strip()[:100] + " — " + message}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"installed": True, "authenticated": False, "message": f"CLI確認に失敗しました: {exc}"}


def invoke(kind: str, prompt: str, run: dict) -> str:
    cfg = settings()
    command = cfg["codex_command"] if kind == "codex" else cfg["claude_command"]
    executable = resolve_cli(command)
    # Prompts use stdin for both CLIs; there is no shell or Windows argument limit.
    argv = (executable + ["exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "-"]
            if kind == "codex" else executable + ["-p", "--output-format", "text",
            "--tools", "", "--strict-mcp-config", "--no-session-persistence"])
    stdin = run.get("research_context", "") + "\n\n" + prompt
    emit(run, f"{kind.title()} CLI を起動しています…")
    try:
        with tempfile.TemporaryDirectory(prefix="council-") as isolated:
            return invoke_process(argv, stdin, kind, run, cfg, isolated)
    except OSError as exc:
        raise RuntimeError(f"{kind.title()} CLI を起動できません: {exc}") from exc


def invoke_process(argv, stdin, kind, run, cfg, isolated):
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", shell=False,
                                cwd=isolated, env=cli_env(), start_new_session=(os.name != "nt"))
        try:
            out, err = proc.communicate(input=stdin, timeout=max(20, min(int(cfg.get("timeout_seconds", 240)), 1200)))
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10, shell=False)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            if proc.poll() is None:
                proc.kill()
            out, err = proc.communicate()
            raise RuntimeError(f"{kind.title()} CLI がタイムアウトしました。設定画面で制限時間を延長してください。")
    except OSError as exc:
        raise RuntimeError(f"{kind.title()} CLI を起動できません: {exc}") from exc
    if proc.returncode != 0:
        detail = (err or out or "エラー詳細なし").strip()[-1800:]
        lowered = detail.lower()
        if any(x in lowered for x in ("econnrefused", "connection refused", "enotfound", "etimedout")):
            raise RuntimeError(f"{kind.title()} CLI が通信先に接続できません。ネット接続・プロキシ・ファイアウォールを確認し、CLI単体でも接続を確認してください。詳細: {detail}")
        if any(x in lowered for x in ("login", "authenticate", "unauthorized", "not logged")):
            raise RuntimeError(f"{kind.title()} CLI のログインが必要です。ターミナルで `codex login` または `claude auth login` を起動してログインしてください。詳細: {detail}")
        raise RuntimeError(f"{kind.title()} CLI が終了コード {proc.returncode} で失敗しました: {detail}")
    answer = out.strip()
    if not answer:
        raise RuntimeError(f"{kind.title()} CLI から回答がありませんでした。ログイン状態とCLIのバージョンを確認してください。")
    emit(run, f"{kind.title()} の回答を受信しました。")
    return answer


def run_council(run: dict, question: str, profile: dict, mode: str) -> None:
    try:
        context = f"研究コンテキスト: {profile.get('context','')}\n追加方針: {profile.get('instructions','')}"
        run["research_context"] = context
        base = ("あなたはPersonal Councilの独立した研究アシスタントです。質問に直接答え、事実・推論・不確実性を分けてください。"
                "出典を捏造しないでください。\n" + context + "\n\n質問:\n" + question)
        run["status"] = "running"
        emit(run, f"{profile.get('name','Council')} / {mode} を開始します。")
        cfg = settings()
        for kind, name in (("codex", "Codex"), ("claude", "Claude Code")):
            probe = cli_probe(cfg[kind + "_command"], name)
            if not probe.get("authenticated"):
                raise RuntimeError(name + ": " + probe["message"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(invoke, k, base, run): k for k in ("codex", "claude")}
            initial_errors = []
            for future in as_completed(futures):
                kind = futures[future]
                try:
                    run[kind] = future.result()
                except Exception as exc:
                    initial_errors.append(f"{kind}: {exc}")
            if initial_errors:
                raise RuntimeError("独立回答に失敗しました: " + "; ".join(initial_errors))
        codex, claude = run["codex"], run["claude"]
        if mode == "Quick":
            synth = "次の2つの独立回答を統合してください。共通点、相違点、不確実性を整理し、質問への明確な回答を日本語で提示してください。\n質問:\n" + question + "\n\nCodex:\n" + codex + "\n\nClaude:\n" + claude
            run["final"] = invoke("codex", synth, run)
        else:
            emit(run, "相互批評を並行して依頼します。")
            prompts = {
                "codex": "相手の回答を建設的に批評してください。正しい点、誤り/飛躍、見落とし、不確実性を挙げ、改善案を簡潔に示してください。質問: " + question + "\n回答:\n" + claude,
                "claude": "相手の回答を建設的に批評してください。正しい点、誤り/飛躍、見落とし、不確実性を挙げ、改善案を簡潔に示してください。質問: " + question + "\n回答:\n" + codex,
            }
            results: dict[str, str] = {}
            errors: list[str] = []
            def critic(k: str) -> None:
                try: results[k] = invoke(k, prompts[k], run)
                except Exception as exc: errors.append(f"{k}: {exc}")
            threads = [threading.Thread(target=critic, args=(k,)) for k in ("codex", "claude")]
            for t in threads: t.start()
            for t in threads: t.join()
            run["critique_codex"] = results.get("codex", "批評を取得できませんでした: " + next((e for e in errors if e.startswith("codex:")), "不明なエラー"))
            run["critique_claude"] = results.get("claude", "批評を取得できませんでした: " + next((e for e in errors if e.startswith("claude:")), "不明なエラー"))
            if errors:
                raise RuntimeError("相互批評に失敗しました: " + "; ".join(errors))
            if mode in ("Council", "Deep Debate"):
                revised_prompt = "批評を読んであなたの回答を改訂してください。批評を採用しない場合は理由を示してください。\n質問: " + question + "\n\n元の回答:\n{answer}\n\n相手の批評:\n{critique}"
                run["codex_revision"] = invoke("codex", revised_prompt.format(answer=codex, critique=run["critique_claude"]), run)
                run["claude_revision"] = invoke("claude", revised_prompt.format(answer=claude, critique=run["critique_codex"]), run)
                final_codex, final_claude = run["codex_revision"], run["claude_revision"]
            if mode == "Deep Debate":
                review_prompt = "相手の改訂案に重大な誤りや未解決の相違があるか短く確認してください。\n質問: " + question + "\n\n相手の改訂案:\n" + final_claude
                run["review_codex"] = invoke("codex", review_prompt, run)
                review_prompt2 = "相手の改訂案に重大な誤りや未解決の相違があるか短く確認してください。\n質問: " + question + "\n\n相手の改訂案:\n" + final_codex
                run["review_claude"] = invoke("claude", review_prompt2, run)
            synth = ("2つの回答と批評を統合し、日本語で最終回答してください。合意点、解消しない相違、根拠の強さを分け、誇張や出典の捏造を避けてください。\n質問:\n" + question +
                     "\n\nCodex案:\n" + final_codex + "\n\nClaude案:\n" + final_claude +
                     "\n\nCodexによるClaude案への批評:\n" + run["critique_codex"] + "\n\nClaudeによるCodex案への批評:\n" + run["critique_claude"])
            if mode == "Deep Debate":
                synth += "\n\n最終レビュー (Codex):\n" + run["review_codex"] + "\n\n最終レビュー (Claude):\n" + run["review_claude"]
            run["final"] = invoke("codex", synth, run)
        run["status"] = "complete"
        emit(run, "Councilの処理が完了しました。")
    except Exception as exc:
        run["status"] = "error"
        run["error"] = str(exc)
        emit(run, str(exc), "error")


class Handler(BaseHTTPRequestHandler):
    def local_request(self, mutation=False):
        allowed = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
        if self.headers.get("Host") not in allowed:
            self.send_json({"error": "ローカルアドレスから接続してください。"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in allowed}:
            self.send_json({"error": "別サイトからの接続は許可されていません。"}, 403)
            return False
        if mutation and not secrets.compare_digest(self.headers.get("X-Council-Token", ""), SESSION_TOKEN):
            self.send_json({"error": "画面を再読み込みしてから実行してください。"}, 403)
            return False
        return True

    def log_message(self, fmt, *args):
        pass

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 300000: raise ValueError("リクエストが大きすぎます")
        value = json.loads(self.rfile.read(size) or b"{}")
        if not isinstance(value, dict): raise ValueError("JSON object required")
        return value

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        super().end_headers()

    def do_GET(self):
        if not self.local_request(): return
        path = urlparse(self.path).path
        if path == "/api/session": return self.send_json({"token": SESSION_TOKEN})
        if path == "/api/profiles": return self.send_json(read_profiles())
        if path == "/api/settings":
            cfg = settings()
            return self.send_json({"settings": cfg, "codex": cli_probe(cfg["codex_command"], "Codex"), "claude": cli_probe(cfg["claude_command"], "Claude Code")})
        if path.startswith("/api/runs/"):
            rid = path.rsplit("/", 1)[-1]
            with RUNS_LOCK: run = RUNS.get(rid)
            return self.send_json(run or {"status": "missing"}, 200 if run else 404)
        target = (STATIC_DIR / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
        if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file(): return self.send_error(404)
        data = target.read_bytes(); self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8" if target.suffix == ".html" else "text/css; charset=utf-8" if target.suffix == ".css" else "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        if not self.local_request(mutation=True): return
        path = urlparse(self.path).path
        try: data = self.body()
        except Exception: return self.send_json({"error": "JSONを読み取れませんでした。"}, 400)
        if path == "/api/settings":
            old = settings(); new = {**old}
            for k in ("codex_command", "claude_command"):
                v = str(data.get(k, old[k])).strip()
                if v: new[k] = v
            try: new["timeout_seconds"] = max(20, min(int(data.get("timeout_seconds", old["timeout_seconds"])), 1200))
            except (ValueError, TypeError): return self.send_json({"error": "制限時間は20〜1200秒で指定してください。"}, 400)
            DATA_DIR.mkdir(parents=True, exist_ok=True); SETTINGS_FILE.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
            return self.send_json({"ok": True, "settings": new})
        if path == "/api/profiles":
            profile = data.get("profile", {})
            if not isinstance(profile, dict): return self.send_json({"error": "プロファイルが不正です。"}, 400)
            read_profiles()
            pid = str(data.get("id", "")).strip()
            if not pid or not pid.replace("_", "").replace("-", "").isalnum() or len(pid) > 64: return self.send_json({"error": "プロファイルIDが不正です。"}, 400)
            required = {"name": str(profile.get("name", "")).strip(), "description": str(profile.get("description", "")).strip(), "context": str(profile.get("context", "")).strip(), "instructions": str(profile.get("instructions", "")).strip()}
            if not required["name"]: return self.send_json({"error": "プロファイル名を入力してください。"}, 400)
            (PROFILE_DIR / f"{pid}.json").write_text(json.dumps(required, ensure_ascii=False, indent=2), encoding="utf-8")
            return self.send_json({"ok": True, "id": pid})
        if path == "/api/runs":
            question = str(data.get("question", "")).strip(); mode = str(data.get("mode", "Council")); pid = str(data.get("profile", ""))
            if not question: return self.send_json({"error": "質問を入力してください。"}, 400)
            if len(question) > 30000: return self.send_json({"error": "質問は30,000文字以内にしてください。"}, 400)
            if mode not in ("Quick", "Council", "Deep Debate"): return self.send_json({"error": "実行モードが不正です。"}, 400)
            profile = next((p for p in read_profiles() if p["id"] == pid), None)
            if not profile: return self.send_json({"error": "Councilプロファイルが見つかりません。"}, 400)
            run = {"id": uuid.uuid4().hex[:12], "status": "queued", "question": question, "profile": profile["name"], "mode": mode, "logs": [], "updated": time.time()}
            with RUNS_LOCK:
                if any(r["status"] in ("queued", "running") for r in RUNS.values()):
                    return self.send_json({"error": "Councilが実行中です。完了後に実行してください。"}, 409)
                while len(RUNS) >= 30:
                    RUNS.pop(next(iter(RUNS)))
                RUNS[run["id"]] = run
            threading.Thread(target=run_council, args=(run, question, profile, mode), daemon=True).start()
            return self.send_json({"id": run["id"]}, 202)
        return self.send_json({"error": "不明なAPIです。"}, 404)


def main():
    global PORT
    parser = argparse.ArgumentParser(description="Personal Council local application")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the local UI in your browser")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535: parser.error("port must be 1024..65535")
    PORT = args.port
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    read_profiles()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as exc:
        parser.exit(1, f"起動できませんでした: {exc}\n別のポートで起動してください: python app.py --port 8766 --open\n")
    print(f"Personal Council v0: http://127.0.0.1:{PORT}")
    print(f"User data: {DATA_DIR}")
    if args.open: webbrowser.open(f"http://127.0.0.1:{PORT}")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\n終了しました。")
    finally: server.server_close()


if __name__ == "__main__":
    main()
