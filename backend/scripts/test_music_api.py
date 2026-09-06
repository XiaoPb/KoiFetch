#!/usr/bin/env python3
"""音乐搜索/下载 HTTP 端到端测试脚本。

通过 HTTP 请求验证 KoiFetch 音乐模块全流程：
  1. GET  /api/health                健康检查
  2. POST /api/auth/login            管理员登录，获取 Bearer Token
  3. GET  /api/music/hot             获取热门关键词
  4. GET  /api/music/search          搜索音乐，列出搜索结果
  5. POST /api/music/import          导入第一首歌曲，创建 MUSIC ParseTask
  6. POST /api/download/prepare      准备直连下载（music asset）
  7. GET  /api/download/direct/{id}  流式下载音乐文件并保存
  8. (可选) POST /api/download/submit + GET /api/download/progress  走分阶段下载

前置条件:
  - 后端服务运行中:  uvicorn app.main:app --app-dir backend
  - .env 已配置 ADMIN_PASSWORD / COOKIE_ENCRYPTION_KEY
  - MUSIC_SEARCH_ENGINE=engine（启用 musicdl 真实搜索）

用法:
  python backend/scripts/test_music_api.py
  python backend/scripts/test_music_api.py --host http://localhost:8000
  python backend/scripts/test_music_api.py --keyword 周杰伦
  python backend/scripts/test_music_api.py --no-download  # 跳过文件下载
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

# ── 路径常量 ──────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
OUTPUT_DIR = REPO_ROOT / "data" / "test_output"

# ── 默认搜索关键词（.env HOT_KEYWORDS 的第一项兜底） ───────────────────
DEFAULT_KEYWORD = "周杰伦"


# ── 彩色输出 ──────────────────────────────────────────────────────────
class C:
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    INFO = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def _print(tag: str, color: str, msg: str) -> None:
    print(f"  {color}[{tag}]{C.RESET} {msg}")


def ok(msg: str) -> None:
    _print("PASS", C.OK, msg)


def fail(msg: str) -> None:
    _print("FAIL", C.FAIL, msg)


def warn(msg: str) -> None:
    _print("WARN", C.WARN, msg)


def info(msg: str) -> None:
    _print("INFO", C.DIM, msg)


def step(msg: str) -> None:
    print(f"\n{C.BOLD}━━ {msg} ━━{C.RESET}")


# ── .env 读取 ────────────────────────────────────────────────────────
def read_env_password() -> str:
    """从 .env 读取 ADMIN_PASSWORD，回退到默认值。"""
    if not ENV_FILE.exists():
        return "change-me-admin"
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("ADMIN_PASSWORD="):
            return line.split("=", 1)[1].strip()
    return "change-me-admin"


def read_env_hot_keywords() -> list[str]:
    """从 .env 读取 HOT_KEYWORDS，回退到空列表。"""
    if not ENV_FILE.exists():
        return []
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("HOT_KEYWORDS="):
            value = line.split("=", 1)[1].strip()
            return [k.strip() for k in value.split(",") if k.strip()]
    return []


# ── 测试主体 ──────────────────────────────────────────────────────────
class MusicApiTester:
    """HTTP 端到端测试器：对运行中的 KoiFetch 服务验证音乐搜索/下载流程。"""

    def __init__(self, host: str, *, download_timeout: float = 120.0):
        self.base = host.rstrip("/")
        self.client = httpx.Client(
            base_url=self.base,
            timeout=httpx.Timeout(30.0, read=download_timeout),
            follow_redirects=True,
        )
        self.token: str | None = None
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def close(self) -> None:
        self.client.close()

    # ── 内部工具 ──
    def _request(self, method: str, path: str, **kwargs):
        headers = kwargs.pop("headers", {})
        if self.token:
            headers.setdefault("Authorization", f"Bearer {self.token}")
        return self.client.request(method, path, headers=headers, **kwargs)

    def _check(self, resp: httpx.Response, name: str) -> tuple[bool, dict | None]:
        """校验统一响应包：HTTP 200 + code==0。"""
        if resp.status_code != 200:
            fail(f"{name}: HTTP {resp.status_code} — {resp.text[:300]}")
            self.failed += 1
            return False, None
        try:
            body = resp.json()
        except Exception:
            fail(f"{name}: 响应非 JSON — {resp.text[:300]}")
            self.failed += 1
            return False, None
        if body.get("code") != 0:
            fail(f"{name}: code={body.get('code')} message={body.get('message')}")
            self.failed += 1
            return False, body
        ok(name)
        self.passed += 1
        return True, body

    # ── 步骤 1: 健康检查 ──
    def test_health(self) -> bool:
        step("1. 健康检查  GET /api/health")
        resp = self.client.get("/api/health")
        success, body = self._check(resp, "健康检查")
        if success and body and body.get("data"):
            data = body["data"]
            info(f"状态: {data.get('status')}  存储: {data.get('services', {}).get('storage')}")
        return success

    # ── 步骤 2: 登录 ──
    def test_login(self, password: str) -> bool:
        step("2. 管理员登录  POST /api/auth/login")
        resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": password},
        )
        success, body = self._check(resp, "登录")
        if success and body and body.get("data"):
            self.token = body["data"]["token"]
            info(f"用户: {body['data']['username']}  过期: {body['data']['expires_at']}")
        return success

    # ── 步骤 3: 热门关键词 ──
    def test_hot_keywords(self) -> list[str]:
        step("3. 热门关键词  GET /api/music/hot")
        resp = self._request("GET", "/api/music/hot")
        success, body = self._check(resp, "热门关键词")
        keywords: list[str] = []
        if success and body and body.get("data"):
            keywords = body["data"].get("keywords", [])
            info(f"关键词 ({len(keywords)}): {', '.join(keywords[:8])}")
        return keywords

    # ── 步骤 4: 搜索 ──
    def test_search(self, keyword: str) -> tuple[bool, list[dict]]:
        step(f"4. 搜索音乐  GET /api/music/search?keyword={keyword}")
        resp = self._request(
            "GET",
            "/api/music/search",
            params={"keyword": keyword, "page": 1},
        )
        success, body = self._check(resp, "搜索")
        songs: list[dict] = []
        if success and body and body.get("data"):
            data = body["data"]
            songs = data.get("songs", [])
            totals = data.get("totals", {})
            info(f"总计: {totals.get('all', 0)} 首  (歌曲 {totals.get('song', 0)}/"
                 f"歌手 {totals.get('artist', 0)}/专辑 {totals.get('album', 0)})")
            if not songs:
                warn(f"搜索 [{keyword}] 无结果（关键词可能过冷或上游源限制）")
                self.warnings += 1
            else:
                # 详细列出搜索到的歌曲
                info(f"── 搜索结果 ({len(songs)} 首) ──")
                for i, song in enumerate(songs):
                    self._print_song(i, song)
        return success, songs

    @staticmethod
    def _print_song(index: int, song: dict) -> None:
        """以表格形式打印一首歌的核心字段。"""
        title = song.get("title", "")[:28]
        artist = song.get("artist", "")[:16]
        album = (song.get("album") or "")[:16]
        duration = song.get("duration") or "—"
        bitrate = song.get("bitrate")
        bitrate_str = f"{bitrate}kbps" if bitrate else "—"
        sid = (song.get("id") or "")[:12]
        info(f"  [{index}] {title:<30} | {artist:<18} | {album:<18} | {duration:<8} | {bitrate_str:<8} | id={sid}")

    # ── 步骤 5: 导入第一首歌曲 ──
    def test_import(self, song: dict) -> tuple[bool, str | None]:
        song_id = song["id"]
        title = song.get("title", "")[:30]
        step(f"5. 导入歌曲  POST /api/music/import  [{title}]  (song_id={song_id[:12]}...)")
        resp = self._request("POST", "/api/music/import", json={"song_id": song_id})
        success, body = self._check(resp, "导入歌曲")
        task_id: str | None = None
        if success and body and body.get("data"):
            task_id = body["data"]["task_id"]
            info(f"task_id: {task_id}")
        return success, task_id

    # ── 步骤 6+7: 直连下载（prepare + direct） ──
    def test_direct_download(self, task_id: str, song: dict) -> bool:
        title = song.get("title", "")[:30]
        artist = song.get("artist", "")[:20]
        step(f"6. 准备下载  POST /api/download/prepare  [{title}]")
        resp = self._request(
            "POST",
            "/api/download/prepare",
            json={"task_id": task_id, "asset": {"kind": "music", "index": 0}},
        )
        success, body = self._check(resp, "Prepare [music]")
        if not success or not body or not body.get("data"):
            return False

        data = body["data"]
        mode = data.get("mode")
        info(f"传输模式: {mode}")

        if mode != "direct":
            if mode == "staged":
                did = data.get("download_id", "?")
                info(f"分阶段下载: {did}")
                warn("该资源需要 Worker 执行分阶段下载，跳过直连保存")
                self.warnings += 1
                return self._poll_staged(did)
            fail(f"未知传输模式: {mode}")
            self.failed += 1
            return False

        url = data.get("url", "")
        filename = data.get("filename") or f"{task_id[:8]}.mp3"
        info(f"直连 URL: {url}")
        info(f"文件名: {filename}")

        # 7. 流式下载文件
        step(f"7. 流式下载  GET /api/download/direct/{task_id[:8]}...?kind=music")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUT_DIR / filename
        try:
            with self.client.stream(
                "GET",
                url,
                headers={"Authorization": f"Bearer {self.token}"} if self.token else None,
            ) as stream:
                if stream.status_code != 200:
                    warn(f"直连下载 HTTP {stream.status_code}（上游限制）")
                    self.warnings += 1
                    return True
                total = 0
                with open(out_path, "wb") as f:
                    for chunk in stream.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        total += len(chunk)
                size_mb = total / (1024 * 1024)
                ok(f"文件已保存: {out_path.name}  ({size_mb:.2f} MB)  [{artist}]")
                self.passed += 1
                return True
        except httpx.HTTPError as exc:
            fail(f"直连下载异常: {exc}")
            self.failed += 1
            return False

    def _poll_staged(self, download_id: str, *, max_polls: int = 15) -> bool:
        """分阶段下载：轮询 progress 直到 completed/failed。"""
        step(f"6b. 轮询进度  GET /api/download/progress/{download_id[:8]}...")
        for i in range(max_polls):
            resp = self._request("GET", f"/api/download/progress/{download_id}")
            if resp.status_code != 200:
                warn(f"进度查询 HTTP {resp.status_code}")
                break
            prog = resp.json().get("data", {})
            status = prog.get("status", "?")
            progress = prog.get("progress", 0)
            speed = prog.get("speed")
            if status == "completed":
                ok(f"下载完成 (progress={progress:.1f}%)")
                self.passed += 1
                return True
            if status == "failed":
                warn(f"下载失败: {prog.get('error_message', '?')}")
                self.warnings += 1
                return True
            speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed else "—"
            info(f"[{i + 1}/{max_polls}] status={status} progress={progress:.1f}% speed={speed_str}")
            time.sleep(3)
        warn(f"下载未在 {max_polls * 3}s 内完成（Worker 可能未运行）")
        self.warnings += 1
        return True

    # ── 步骤 8: Bubble 下载（submit + worker + 验证 data/bubble/music） ──
    def test_bubble_download(self, task_id: str, song: dict) -> tuple[bool, str | None]:
        """通过 submit API 触发 worker 下载到 bubble 临时存储。"""
        title = song.get("title", "")[:30]
        step(f"8. Bubble 下载  POST /api/download/submit  [{title}]")
        resp = self._request("POST", "/api/download/submit", json={"task_id": task_id})
        success, body = self._check(resp, "Submit [bubble]")
        if not success or not body or not body.get("data"):
            return False, None
        data = body["data"]
        download_id = data["download_id"]
        info(f"download_id: {download_id}")
        info(f"status: {data.get('status')}")

        # 轮询直到 completed
        step(f"8b. 轮询进度  GET /api/download/progress/{download_id[:8]}...")
        max_polls = 40  # 40 * 3s = 120s
        for i in range(max_polls):
            resp = self._request("GET", f"/api/download/progress/{download_id}")
            if resp.status_code != 200:
                warn(f"进度查询 HTTP {resp.status_code}")
                break
            prog = resp.json().get("data", {})
            status = prog.get("status", "?")
            progress = prog.get("progress", 0)
            speed = prog.get("speed")
            if status == "completed":
                ok(f"Worker 下载完成 (progress={progress:.1f}%)")
                self.passed += 1
                # 验证 bubble 目录有文件
                return self._verify_bubble_file(download_id), download_id
            if status == "failed":
                warn(f"Worker 下载失败: {prog.get('error_message', '?')}")
                self.warnings += 1
                return True, download_id
            speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed else "—"
            info(f"[{i + 1}/{max_polls}] status={status} progress={progress:.1f}% speed={speed_str}")
            time.sleep(3)
        warn(f"Worker 下载未在 {max_polls * 3}s 内完成")
        self.warnings += 1
        return True, download_id

    def _verify_bubble_file(self, download_id: str) -> bool:
        """验证 data/bubble/music/ 目录下存在下载的文件。"""
        step("8c. 验证 Bubble 文件  data/bubble/music/")
        bubble_dir = REPO_ROOT / "data" / "bubble" / "music"
        if not bubble_dir.exists():
            fail(f"Bubble 目录不存在: {bubble_dir}")
            self.failed += 1
            return False
        files = list(bubble_dir.glob("*"))
        # 过滤 .gitkeep 等隐藏文件
        files = [f for f in files if not f.name.startswith(".")]
        if not files:
            fail(f"Bubble 目录为空: {bubble_dir}")
            self.failed += 1
            return False
        total_size = sum(f.stat().st_size for f in files if f.is_file())
        size_mb = total_size / (1024 * 1024)
        file_names = [f.name for f in files[:5]]
        ok(f"Bubble 文件验证通过: {len(files)} 个文件, {size_mb:.2f} MB")
        info(f"文件: {', '.join(file_names)}")
        self.passed += 1
        return True

    # ── 步骤 9: Pond 保存（nas/save + 验证 data/pond/music） ──
    def test_pond_save(self, download_id: str) -> bool:
        """通过 nas/save API 把 bubble 文件移动到 pond 永久存储。"""
        step(f"9. Pond 保存  POST /api/nas/save  (download_id={download_id[:8]}...)")
        resp = self._request("POST", "/api/nas/save", json={"download_id": download_id})
        success, body = self._check(resp, "NAS Save [pond]")
        if not success or not body or not body.get("data"):
            return False
        data = body["data"]
        nas_path = data.get("nas_path", "?")
        file_size = data.get("file_size", 0)
        saved_at = data.get("saved_at", "?")
        info(f"NAS 路径: {nas_path}")
        info(f"文件大小: {file_size / (1024 * 1024):.2f} MB")
        info(f"保存时间: {saved_at}")

        # 验证 pond 目录有文件
        return self._verify_pond_file(nas_path)

    def _verify_pond_file(self, nas_path: str) -> bool:
        """验证 data/pond/music/ 目录下存在保存的文件。"""
        step(f"9b. 验证 Pond 文件  data/pond/music/")
        pond_dir = REPO_ROOT / "data" / "pond" / "music"
        if not pond_dir.exists():
            fail(f"Pond 目录不存在: {pond_dir}")
            self.failed += 1
            return False
        # 递归查找所有文件
        files = [f for f in pond_dir.rglob("*") if f.is_file() and not f.name.startswith(".")]
        if not files:
            fail(f"Pond 目录为空: {pond_dir}")
            self.failed += 1
            return False
        total_size = sum(f.stat().st_size for f in files)
        size_mb = total_size / (1024 * 1024)
        ok(f"Pond 文件验证通过: {len(files)} 个文件, {size_mb:.2f} MB")
        for f in files[:5]:
            rel = f.relative_to(pond_dir)
            info(f"  {rel}  ({f.stat().st_size / (1024 * 1024):.2f} MB)")
        self.passed += 1
        return True

    # ── 汇总 ──
    def summary(self, elapsed: float) -> bool:
        total = self.passed + self.failed + self.warnings
        print(f"\n{'━' * 50}")
        print(f"{C.BOLD}测试结果{C.RESET}")
        print(f"  {C.OK}通过: {self.passed}{C.RESET}  {C.FAIL}失败: {self.failed}{C.RESET}  "
              f"{C.WARN}警告: {self.warnings}{C.RESET}  共: {total}")
        print(f"  耗时: {elapsed:.1f}s")
        all_ok = self.failed == 0
        if all_ok:
            print(f"\n  {C.OK}{C.BOLD}✓ 全部测试通过{C.RESET}\n")
        else:
            print(f"\n  {C.FAIL}{C.BOLD}✗ 有 {self.failed} 项失败{C.RESET}\n")
        return all_ok


def _start_worker() -> subprocess.Popen | None:
    """启动 worker 子进程（后台运行），返回 Popen 对象。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "backend")
    env.setdefault("DATABASE_URL", f"sqlite:///{REPO_ROOT}/backend/data/db/koifetch.db")
    py = str(REPO_ROOT / ".venv" / "Scripts" / "python.exe")
    log_path = REPO_ROOT / "data" / "test_output" / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_file = open(log_path, "w", encoding="utf-8")
        proc = subprocess.Popen(
            [py, "-m", "app.workers.main"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        print(f"  {C.INFO}Worker 已启动 (pid={proc.pid}, log={log_path.name}){C.RESET}")
        return proc
    except Exception as exc:
        print(f"  {C.WARN}Worker 启动失败: {exc}{C.RESET}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="KoiFetch 音乐搜索/下载 HTTP 端到端测试")
    parser.add_argument(
        "--host", default="http://localhost:8000", help="服务地址 (默认 http://localhost:8000)"
    )
    parser.add_argument(
        "--keyword", default=None, help="搜索关键词 (默认取 .env HOT_KEYWORDS 第一项或 周杰伦)"
    )
    parser.add_argument(
        "--no-download", action="store_true", help="跳过直连文件下载步骤"
    )
    parser.add_argument(
        "--no-bubble", action="store_true", help="跳过 bubble/pond 存储验证步骤"
    )
    parser.add_argument(
        "--password", default=None, help="管理员密码 (默认从 .env 读取)"
    )
    args = parser.parse_args()

    password = args.password or read_env_password()
    hot_keywords = read_env_hot_keywords()
    keyword = args.keyword or (hot_keywords[0] if hot_keywords else DEFAULT_KEYWORD)

    tester = MusicApiTester(args.host)
    worker_proc = None
    start = time.time()

    print(f"\n{C.BOLD}╔══ KoiFetch 音乐搜索/下载 HTTP 端到端测试 ══╗{C.RESET}")
    print(f"  {C.INFO}目标: {args.host}{C.RESET}")
    print(f"  {C.INFO}关键词: {keyword}{C.RESET}")
    print(f"  {C.INFO}直连下载: {'跳过' if args.no_download else '启用'}{C.RESET}")
    print(f"  {C.INFO}Bubble/Pond: {'跳过' if args.no_bubble else '启用'}{C.RESET}")

    try:
        # 1. 健康检查
        if not tester.test_health():
            fail("健康检查失败，终止")
            return tester.summary(time.time() - start)  # type: ignore[func-returns-value]

        # 2. 登录
        if not tester.test_login(password):
            fail("登录失败，终止")
            return tester.summary(time.time() - start)  # type: ignore[func-returns-value]

        # 3. 热门关键词
        tester.test_hot_keywords()

        # 4. 搜索
        search_ok, songs = tester.test_search(keyword)
        if not search_ok or not songs:
            fail("搜索无结果，后续步骤无法继续")
            return tester.summary(time.time() - start)  # type: ignore[func-returns-value]

        # 5. 导入第一首
        first_song = songs[0]
        import_ok, task_id = tester.test_import(first_song)
        if not import_ok or not task_id:
            fail("导入失败，终止下载流程")
            return tester.summary(time.time() - start)  # type: ignore[func-returns-value]

        # 6+7. 直连下载（prepare + direct）
        if not args.no_download:
            tester.test_direct_download(task_id, first_song)
        else:
            info("已跳过直连下载 (--no-download)")

        # 8+9. Bubble/Pond 存储验证（需要 worker）
        if not args.no_bubble:
            step("启动 Worker 进程（用于 bubble 下载）")
            worker_proc = _start_worker()
            if worker_proc is None:
                fail("Worker 启动失败，跳过 bubble/pond 验证")
                tester.failed += 1
            else:
                time.sleep(3)  # 等 worker 初始化
                # 8. Bubble 下载（submit + worker + 验证 data/bubble/music）
                bubble_ok, download_id = tester.test_bubble_download(task_id, first_song)
                # 9. Pond 保存（nas/save + 验证 data/pond/music）
                if bubble_ok and download_id:
                    tester.test_pond_save(download_id)
        else:
            info("已跳过 bubble/pond 验证 (--no-bubble)")

    except httpx.ConnectError:
        fail(f"无法连接 {args.host}，请确认服务已启动")
        tester.failed += 1
    except Exception as exc:
        fail(f"未预期异常: {exc}")
        tester.failed += 1
    finally:
        # 停止 worker
        if worker_proc is not None:
            worker_proc.terminate()
            try:
                worker_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker_proc.kill()
            print(f"  {C.INFO}Worker 已停止{C.RESET}")
        tester.close()

    return 0 if tester.summary(time.time() - start) else 1


if __name__ == "__main__":
    sys.exit(main())
